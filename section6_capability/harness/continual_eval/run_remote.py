"""Run one isolated Pokemon Red Continual Harness cell on container."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "vendor" / "continual-harness"
sys.path.insert(0, str(ROOT))

from evalspec import (COMPLETION_CONFIRMATIONS, CONTINUAL_HARNESS_COMMIT,
                      MODEL_DISPLAY, MODELS,
                      PILOT_CAPS, PILOT_TARGET_DESCRIPTION,
                      CONTAINER_FULL_CAPS, CONTAINER_PROVIDER_POLICY,
                      STATUS_INTERVAL_SECONDS, session_cost)

CONTROL = Path("/run/pokemon-eval/gate-control.json")
GATE_URL = "http://127.0.0.1:18999/api/v1"
SEALED_KEY = Path("/run/pokemon-eval/openrouter.key")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_openrouter_key() -> str:
    try:
        value = SEALED_KEY.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"sealed OpenRouter key unavailable: {type(exc).__name__}") from exc
    if not value:
        raise RuntimeError("sealed OpenRouter key is empty")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def append(path: Path, value: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"ts": now(), **value}, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    result = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                result.append(value)
        except json.JSONDecodeError:
            pass
    return result


def usage(path: Path, generation: str) -> dict[str, Any]:
    rows = [row for row in read_jsonl(path) if row.get("generation") == generation]
    responses = [row for row in rows if row.get("kind") == "response"]
    successful = [row for row in responses if 200 <= int(row.get("status", 0)) < 300]
    tokens_in = tokens_out = reasoning = 0
    cost = paused = 0.0
    models: set[str] = set()
    providers: set[str] = set()
    for row in successful:
        item = row.get("usage") or {}
        tokens_in += int(item.get("prompt_tokens", item.get("input_tokens", 0)) or 0)
        tokens_out += int(item.get("completion_tokens", item.get("output_tokens", 0)) or 0)
        reasoning += int((item.get("completion_tokens_details") or {}).get(
            "reasoning_tokens", item.get("reasoning_tokens", 0)) or 0)
        cost += float(item.get("cost", 0) or 0)
        if row.get("model"):
            models.add(str(row["model"]))
        if row.get("actual_provider"):
            providers.add(str(row["actual_provider"]))
    paused = sum(float(row.get("paused_seconds", 0) or 0) for row in rows
                 if row.get("kind") == "credit_resume")
    return {
        "calls": len(successful), "in": tokens_in, "out": tokens_out,
        "reasoning": reasoning, "cost": cost, "models": sorted(models),
        "providers": sorted(providers),
        "paused_seconds": paused,
        "image_calls": sum(int(row.get("image_parts", 0) or 0) > 0 for row in successful),
        "provider_policies": [row.get("provider_policy") for row in successful],
        "upstream_attempts": sum(row.get("kind") == "upstream_attempt" for row in rows),
        "error_responses": sum(int(row.get("status", 0)) >= 400 for row in responses),
        "budget_blocks": sum(row.get("kind") == "budget_block" for row in rows),
    }


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=15)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def copy_evidence(work: Path, out: Path) -> None:
    for name in (".pokeagent_cache", "run_data"):
        source = work / name
        if source.exists():
            shutil.copytree(source, out / name, dirs_exist_ok=True)
    for pattern in ("*.log", "*.json", "*.jsonl"):
        for source in work.glob(pattern):
            if source.is_file():
                shutil.copy2(source, out / source.name)


def state(port: int) -> dict[str, Any] | None:
    try:
        response = requests.get(f"http://127.0.0.1:{port}/state", timeout=10)
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else None
    except (requests.RequestException, ValueError):
        return None


def metrics(work: Path) -> dict[str, int]:
    candidates = sorted((work / ".pokeagent_cache").glob("*/cumulative_metrics.json"))
    if not candidates:
        return {"agent_steps": 0, "button_groups": 0, "atomic_inputs": 0,
                "memory_calls": 0}
    try:
        data = json.loads(candidates[-1].read_text(encoding="utf-8"))
    except Exception:
        return {"agent_steps": 0, "button_groups": 0, "atomic_inputs": 0,
                "memory_calls": 0}
    steps = data.get("steps") or []
    groups = atomic = memory = 0
    for step in steps:
        for call in step.get("tool_calls") or []:
            if call.get("name") == "press_buttons":
                groups += 1
                atomic += len((call.get("args") or {}).get("buttons") or [])
            elif call.get("name") == "process_memory":
                memory += 1
    return {"agent_steps": len(steps), "button_groups": groups,
            "atomic_inputs": atomic, "memory_calls": memory}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=sorted(MODELS), required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=("pilot", "full"), required=True)
    args = parser.parse_args()
    caps = PILOT_CAPS if args.mode == "pilot" else CONTAINER_FULL_CAPS
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    gate_ledger = out / "openrouter_gate.jsonl"
    states = out / "state_snapshots.jsonl"
    turns_path = out / "turns.jsonl"
    completion_latch = out / "tutorial_complete.json"
    generation = uuid.uuid4().hex
    port = 8000
    expected_model = MODELS[args.model]
    expected_policy = CONTAINER_PROVIDER_POLICY[args.model]
    work = Path("/run/pokemon-eval/cells") / generation
    rom_target = work / "PokemonRed-GBC" / "pokered.gbc"
    rom_target.parent.mkdir(parents=True)
    shutil.copy2(args.rom.resolve(), rom_target)

    gate = {
        "active": True, "generation": generation, "model_key": args.model,
        "mode": args.mode, "ledger": str(gate_ledger),
        "completion_latch": str(completion_latch),
        "call_cap": caps["model_calls"], "credit_poll_seconds": 60,
        "expected_model": expected_model,
        "expected_provider_policy": expected_policy,
    }
    atomic_json(CONTROL, gate)
    env = dict(os.environ, OPENROUTER_API_KEY=read_openrouter_key(),
               GAME_TYPE="red", EXCLUDE_BUILTIN_SUBAGENTS="1",
               POKEMON_RED_BOOTSTRAP_NEW_GAME="1",
               OPENROUTER_BASE_URL=GATE_URL,
               PYTHONPATH=str(HARNESS), PYTHONUNBUFFERED="1")
    command = [
        sys.executable, str(HARNESS / "run.py"), "--game", "red",
        "--backend", "openrouter", "--model-name", expected_model,
        "--scaffold", "simplest", "--headless", "--agent-auto",
        "--max-steps", str(caps["turns"]), "--port", str(port),
        "--run-name", f"eval-{args.model}-{generation[:8]}",
    ]
    stdout_path = out / "harness.log"
    stdout = stdout_path.open("w", encoding="utf-8")
    process = subprocess.Popen(command, cwd=work, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1,
                               start_new_session=True)

    def relay() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            stdout.write(line)
            stdout.flush()
            print(line.rstrip(), flush=True)

    relay_thread = threading.Thread(target=relay, daemon=True)
    relay_thread.start()
    started = time.monotonic()
    last_status = 0.0
    cap_seen_at: float | None = None
    confirmations = 0
    logged_turns = 0
    terminal = "running"
    error = None
    try:
        while process.poll() is None:
            current_usage = usage(gate_ledger, generation)
            elapsed = time.monotonic() - started - current_usage["paused_seconds"]
            current_state = state(port)
            if current_usage["calls"] > logged_turns:
                player = (current_state or {}).get("player") or {}
                game = (current_state or {}).get("game") or {}
                for turn in range(logged_turns + 1, current_usage["calls"] + 1):
                    append(turns_path, {"turn": turn, "model_call": turn,
                        "location": player.get("location"),
                        "position": player.get("position"),
                        "in_battle": game.get("is_in_battle"),
                        "game_state": game.get("game_state")})
                logged_turns = current_usage["calls"]
            if current_state:
                progress = ((current_state.get("game") or {}).get("progress_context") or {})
                has_pokedex = bool((progress.get("flags") or {}).get("has_pokedex"))
                confirmations = confirmations + 1 if has_pokedex else 0
                if time.monotonic() - last_status >= STATUS_INTERVAL_SECONDS or has_pokedex:
                    player = current_state.get("player") or {}
                    game = current_state.get("game") or {}
                    append(states, {"location": player.get("location"),
                        "position": player.get("position"),
                        "game_state": game.get("game_state"),
                        "in_battle": game.get("is_in_battle"),
                        "has_pokedex": has_pokedex,
                        "completion_confirmations": confirmations,
                        "model_calls": current_usage["calls"]})
                if confirmations >= COMPLETION_CONFIRMATIONS:
                    atomic_json(completion_latch, {"ts": now(),
                        "rule": f"flags.has_pokedex true {COMPLETION_CONFIRMATIONS} times"})
                    terminal = "success"
                    stop(process)
                    break
            if current_usage["calls"] >= caps["model_calls"]:
                cap_seen_at = cap_seen_at or time.monotonic()
                counts = metrics(work)
                final_step_recorded = counts["agent_steps"] >= current_usage["calls"]
                if final_step_recorded or time.monotonic() - cap_seen_at >= 30:
                    terminal = "pilot_complete" if args.mode == "pilot" else "model_call_cap"
                    stop(process)
                    break
            if elapsed >= caps["wallclock_seconds"]:
                terminal = "wallclock_cap"
                stop(process)
                break
            if time.monotonic() - last_status >= STATUS_INTERVAL_SECONDS:
                last_status = time.monotonic()
                location = ((current_state or {}).get("player") or {}).get("location")
                counts = metrics(work)
                print(f"[{now()}] container/{args.model} location={location} "
                      f"calls={current_usage['calls']}/{caps['model_calls']} "
                      f"steps={counts['agent_steps']} inputs={counts['atomic_inputs']} "
                      f"cost=${current_usage['cost']:.6f} elapsed={elapsed:.0f}s", flush=True)
            time.sleep(1)

        if terminal == "running":
            terminal = "harness_exit" if process.returncode else (
                "pilot_complete" if args.mode == "pilot" else "model_call_cap")
            if process.returncode:
                error = f"Continual Harness exited rc={process.returncode}"
    except BaseException as exc:
        terminal, error = "harness_error", f"{type(exc).__name__}: {exc}"
        stop(process)
        (out / "traceback.log").write_text(
            __import__("traceback").format_exc(), encoding="utf-8")
    finally:
        atomic_json(CONTROL, {**gate, "active": False})
        if process.poll() is None:
            stop(process)
        relay_thread.join(timeout=5)
        stdout.close()
        copy_evidence(work, out)

    current_usage = usage(gate_ledger, generation)
    counts = metrics(work)
    battle_turns = sum(row.get("in_battle") is True for row in read_jsonl(turns_path))
    policies_ok = all(policy == expected_policy
                      for policy in current_usage["provider_policies"])
    estimated = current_usage["cost"] or session_cost(
        args.model, current_usage["in"], current_usage["out"])
    meta = {
        "mode": args.mode, "game": "Pokemon Red",
        "framework": "continual_harness", "scaffold": "simplest",
        "prompt_optimization": False, "model_key": args.model,
        "startup_boundary": "agent begins after evaluator presses START and selects NEW GAME",
        "unscored_bootstrap_inputs": ["START", "A"],
        "model": expected_model, "model_display": MODEL_DISPLAY[args.model],
        "harness_commit": CONTINUAL_HARNESS_COMMIT,
        "terminal": terminal, "success": terminal == "success", "error": error,
        "turns": current_usage["calls"], "model_calls": current_usage["calls"],
        "battle_turns": battle_turns,
        "harness_steps": counts["agent_steps"],
        "button_groups": counts["button_groups"],
        "atomic_inputs": counts["atomic_inputs"],
        "memory_tool_calls": counts["memory_calls"],
        "tokens": {"in": current_usage["in"], "out": current_usage["out"],
                   "reasoning": current_usage["reasoning"]},
        "estimated_cost_usd": estimated,
        "cost_source": "OpenRouter usage through egress gate",
        "wallclock_seconds": round(time.monotonic() - started - current_usage["paused_seconds"], 3),
        "total_elapsed_seconds": round(time.monotonic() - started, 3),
        "credit_pause_seconds_excluded": round(current_usage["paused_seconds"], 3),
        "actual_models": current_usage["models"],
        "provider_policy": expected_policy,
        "provider_policy_verified": policies_ok,
        "actual_providers": current_usage["providers"],
        "provider_evidence_verified": (
            len(current_usage["providers"]) > 0 if current_usage["calls"] else False),
        "vision_model_calls": current_usage["image_calls"],
        "image_input_mode": "native Continual Harness screenshots",
        "upstream_attempts": current_usage["upstream_attempts"],
        "provider_error_responses": current_usage["error_responses"],
        "completion_rule": f"flags.has_pokedex true {COMPLETION_CONFIRMATIONS} times",
        "completion_latched": completion_latch.is_file(),
        "pilot_contract": PILOT_TARGET_DESCRIPTION if args.mode == "pilot" else None,
        "call_cap_enforcement": "successful upstream responses blocked at egress before cap+1",
        "configured_model_call_cap": caps["model_calls"],
        "configured_wallclock_emergency_seconds": caps["wallclock_seconds"],
        "primary_full_terminal_condition": (
            "Pokedex completion or 1000 successful model calls"
            if args.mode == "full" else None),
    }
    if current_usage["models"] and set(current_usage["models"]) != {expected_model}:
        meta["terminal"], meta["error"] = "model_mismatch", repr(current_usage["models"])
    if current_usage["calls"] and not policies_ok:
        meta["terminal"], meta["error"] = "provider_policy_mismatch", "route policy was not enforced"
    (out / "session_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2), flush=True)
    return 0 if meta["terminal"] in {
        "pilot_complete", "success", "model_call_cap", "wallclock_cap"
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
