"""Run one isolated constrained-system Pokemon Red attempt on Windows."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evalspec import (COMPLETION_CONFIRMATIONS, FULL_CAPS, MODEL_DISPLAY, MODELS, PILOT_CAPS,
                      PILOT_TARGET_DESCRIPTION,
                      STATUS_INTERVAL_SECONDS, session_cost)
from constrained.prompt import SYSTEM_PROMPT
from constrained.provider import OpenRouterProvider
from credit_gate import credit_error, wait_if_exhausted

MAX_SESSION_ATTEMPTS = 5
MAX_OUTAGE_SESSION_ATTEMPTS = 10
UPSTREAM_OVERLOAD_COOLDOWN_SECONDS = 60
UPSTREAM_OUTAGE_ROUND_PAUSE_SECONDS = 300


def retryable_model_error(value: object) -> bool:
    text = str(value).lower()
    return any(marker in text for marker in (
        "jsondecodeerror", "structured response", "provider response invalid",
        "empty assistant content", "error code: 408", "error code: 409",
        "error code: 425", "error code: 429", "error code: 500",
        "error code: 502", "error code: 503", "error code: 504",
        "service_overloaded", "temporarily overloaded", "timed out", "timeout",
        "upstream_provider_shared_pool",
    ))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"ts": now(), **row}, ensure_ascii=False) + "\n")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sources(manifest_path: Path) -> None:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed = []
    for item in data["before"]["files"]:
        path = Path(item["path"])
        if not path.is_file() or path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
            changed.append(str(path))
    if changed:
        raise RuntimeError(f"original local sources changed: {changed}")


async def run(model_key: str, output: Path, mode: str = "pilot") -> dict:
    caps = PILOT_CAPS if mode == "pilot" else FULL_CAPS
    baseline = ROOT / "local_runtime" / "baseline_red"
    source_manifest = baseline / "source_manifest.json"
    if not source_manifest.is_file():
        raise RuntimeError("run tools/stage_local_runtime.py first")
    verify_sources(source_manifest)

    attempt_id = f"{model_key}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    attempt = ROOT / "local_runtime" / "attempts" / attempt_id
    if attempt.exists():
        raise RuntimeError(f"attempt collision: {attempt}")
    shutil.copytree(baseline, attempt)
    (attempt / "saves" / "_bridge").mkdir(parents=True)
    output.mkdir(parents=True, exist_ok=False)

    os.environ.update({
        "POKEMON_EVAL_ROM": str(attempt / "PokemonRed.gb"),
        "POKEMON_EVAL_ROM_DIR": str(attempt),
        "POKEMON_EVAL_VBA": str(attempt / "VBA-RR" / "vba.exe"),
        "POKEMON_EVAL_LUA": str(attempt / "pokemon_bridge.lua"),
        "POKEMON_EVAL_SAVE_BASE": str(attempt / "saves"),
        "POKEMON_EVAL_LUA_SAVE_BASE": str(attempt / "saves"),
        "POKEMON_EVAL_BRIDGE_DIR": str(attempt / "saves" / "_bridge"),
        "POKEMON_EVAL_POKEMON_ACCESS": str(attempt / "pokemon-access"),
        "POKEMON_EVAL_MAP_DB": str(attempt / "map_database.json"),
    })
    from constrained.helpers.pokemon_game import PokemonGame, read_state

    calls_path = output / "model_calls.jsonl"
    events_path = output / "events.jsonl"
    provider = OpenRouterProvider(
        api_key=os.environ["OPENROUTER_API_KEY"], model=MODELS[model_key],
        max_calls=caps["model_calls"],
        log_cb=lambda row: append(calls_path, row), request_timeout=240)
    provider.set_system_prompt(SYSTEM_PROMPT)
    game = PokemonGame("pokemon_red", "Pokemon Red")
    boot_started = time.monotonic()
    launched = await game.launch(fresh_start=True)
    if not launched:
        raise RuntimeError("isolated VBA-RR/Lua launch failed")

    pending_state = None
    boot_deadline = time.monotonic() + 90
    while time.monotonic() < boot_deadline:
        candidate = await game.get_game_state()
        if candidate and (candidate[1] or candidate[2].get("needs_name")):
            pending_state = candidate
            break
        await asyncio.sleep(0.25)
    if pending_state is None:
        raise RuntimeError("title menu did not become interactive")

    started = time.monotonic()
    last_status = 0.0
    meaningful_turns = 0
    battle_turns = 0
    confirmations = 0
    terminal = "running"
    error = None
    history: list[dict] = []
    raw_input_log = ""
    paused_seconds = 0.0
    try:
        while True:
            if mode == "pilot" and meaningful_turns >= caps["turns"]:
                terminal = "pilot_complete"
                break
            elapsed = time.monotonic() - started - paused_seconds
            raw = read_state() or {}
            has_pokedex = bool(raw.get("has_pokedex"))
            confirmations = 0
            if mode == "full" and has_pokedex:
                confirmations = 1 + int(bool((read_state() or {}).get("has_pokedex")))
            append(events_path, {"event": "observation", "mode": raw.get("mode"),
                   "map": raw.get("map_name"), "x": raw.get("player_x"),
                   "y": raw.get("player_y"), "has_pokedex": has_pokedex,
                   "completion_confirmations": confirmations})
            if confirmations >= COMPLETION_CONFIRMATIONS:
                terminal = "success"
                break
            if elapsed >= caps["wallclock_seconds"]:
                terminal = "wallclock_cap"
                break
            if provider.calls_made >= caps["model_calls"]:
                terminal = "model_call_cap"
                break
            if mode != "pilot" and meaningful_turns >= caps["turns"]:
                terminal = "turn_cap"
                break

            state_result = pending_state or await game.get_game_state()
            pending_state = None
            if state_result is None:
                await asyncio.sleep(0.25)
                continue
            state_text, choices, metadata = state_result
            if not choices and not metadata.get("needs_name"):
                if state_text.strip():
                    history.append({"role": "user", "content": state_text[:500]})
                    history = history[-24:]
                continue

            letters = list(choices)
            if metadata.get("needs_name"):
                schema = {"type": "object", "properties": {
                    "thinking": {"type": "string"},
                    "name": {"type": "string", "maxLength": 7}},
                    "required": ["thinking", "name"], "additionalProperties": False}
            else:
                schema = {"type": "object", "properties": {
                    "thinking": {"type": "string"},
                    "choice": {"type": "string", "enum": letters}},
                    "required": ["thinking", "choice"], "additionalProperties": False}
            turn_no = meaningful_turns + 1
            append(events_path, {"event": "decision_requested", "turn": turn_no,
                   "model_call": provider.calls_made + 1, "metadata": metadata,
                   "shown_text": state_text, "choices": choices})
            paused_seconds += await asyncio.to_thread(
                wait_if_exhausted, os.environ["OPENROUTER_API_KEY"], f"local/{model_key}")
            decision_failures = 0
            while True:
                result = await provider.generate(
                    state_text, schema, "pokemon_red", turn_no, history)
                call_error = result.get("error")
                if not call_error:
                    break
                if credit_error(call_error):
                    provider.calls_made = max(0, provider.calls_made - 1)
                    append(events_path, {"event": "credit_pause", "error": call_error})
                    paused_seconds += await asyncio.to_thread(
                        wait_if_exhausted, os.environ["OPENROUTER_API_KEY"],
                        f"local/{model_key}", True)
                    continue
                if not retryable_model_error(call_error):
                    break
                decision_failures += 1
                outage = "upstream_provider_shared_pool" in str(call_error).lower()
                attempt_cap = (MAX_OUTAGE_SESSION_ATTEMPTS if outage
                               else MAX_SESSION_ATTEMPTS)
                if decision_failures >= attempt_cap:
                    break
                if provider.calls_made >= caps["model_calls"]:
                    break
                if outage and decision_failures == MAX_SESSION_ATTEMPTS:
                    wait_s = UPSTREAM_OUTAGE_ROUND_PAUSE_SECONDS
                    reason = "five-minute outage cooldown before final retry block"
                elif outage:
                    wait_s = UPSTREAM_OVERLOAD_COOLDOWN_SECONDS
                    reason = "upstream-overload cooldown"
                else:
                    wait_s = min(2 ** (decision_failures - 1), 16)
                    reason = "bounded retry backoff"
                append(events_path, {"event": "model_retry", "turn": turn_no,
                    "failed_call": provider.calls_made, "attempt": decision_failures,
                    "attempt_cap": attempt_cap, "wait_seconds": wait_s,
                    "reason": reason, "error": call_error})
                print(f"[{now()}] [local/{model_key}] retryable model failure "
                      f"{decision_failures}/{attempt_cap}; retrying same game decision "
                      f"in {wait_s}s", flush=True)
                await asyncio.sleep(wait_s)
            if result.get("error"):
                terminal, error = "model_error", result["error"]
                break
            meaningful_turns += 1
            if metadata.get("needs_name"):
                name = result["name"][:7]
                await game.send_name(name)
                action = {"kind": "name", "value": name}
            else:
                choice = result["choice"]
                detail = await game.send_choice(choice, choices)
                if metadata.get("mode") == "battle":
                    battle_turns += 1
                action = {"kind": "battle_decision" if metadata.get("mode") == "battle" else "decision",
                          "choice": choice, "label": (choices.get(choice) or {}).get("label"),
                          "result": detail}
            append(events_path, {"event": "meaningful_turn", "turn": meaningful_turns,
                                 "model_call": provider.calls_made, "action": action})
            assistant_context = (f"Named: {name}" if metadata.get("needs_name")
                                 else choice)
            history.extend([{"role": "user", "content": state_text},
                            {"role": "assistant", "content": assistant_context}])
            history = history[-24:]

            if time.monotonic() - last_status >= STATUS_INTERVAL_SECONDS:
                last_status = time.monotonic()
                estimate = session_cost(model_key, provider.tokens_in, provider.tokens_out)
                print(f"[{now()}] local/{model_key} map={raw.get('map_name')} "
                      f"turns={meaningful_turns} calls={provider.calls_made} "
                      f"elapsed={elapsed:.0f}s cost~${estimate:.4f}", flush=True)
    except Exception as exc:
        terminal, error = "harness_error", f"{type(exc).__name__}: {exc}"
    finally:
        raw_input_log = game.get_input_log()
        (output / "atomic_inputs.jsonl").write_text(raw_input_log, encoding="utf-8")
        await game.quit()
        verify_sources(source_manifest)

    meta = {
        "attempt_id": attempt_id, "mode": mode, "framework": "constrained", "model_key": model_key,
        "model": MODELS[model_key], "model_display": MODEL_DISPLAY[model_key],
        "terminal": terminal, "success": terminal == "success", "error": error,
        "turns": meaningful_turns, "meaningful_gameplay_turns": meaningful_turns,
        "battle_decision_turns": battle_turns,
        "meaningful_turns": meaningful_turns, "model_calls": provider.calls_made,
        "atomic_inputs": sum(1 for line in raw_input_log.splitlines() if line.strip()),
        "wallclock_seconds": round(time.monotonic() - started - paused_seconds, 3),
        "total_elapsed_seconds": round(time.monotonic() - started, 3),
        "credit_pause_seconds_excluded": round(paused_seconds, 3),
        "boot_seconds_excluded": round(started - boot_started, 3),
        "tokens": {"in": provider.tokens_in, "out": provider.tokens_out,
                   "reasoning": provider.reasoning_tokens},
        "estimated_cost_usd": session_cost(model_key, provider.tokens_in, provider.tokens_out),
        "attempt_runtime": str(attempt), "source_integrity": "verified_after_run",
        "pilot_contract": PILOT_TARGET_DESCRIPTION if mode == "pilot" else None,
    }
    (output / "session_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=sorted(MODELS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("pilot", "full"), default="pilot")
    args = parser.parse_args()
    meta = asyncio.run(run(args.model, args.output, args.mode))
    print(json.dumps(meta, indent=2))
    return 0 if meta["terminal"] in {
        "pilot_complete", "success", "wallclock_cap", "turn_cap", "model_call_cap"
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
