from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evalspec import (COMPLETION_CONFIRMATIONS, FULL_CAPS, MODEL_DISPLAY,
                      MODEL_PROVIDER, MODELS, OC_MODEL_SLUG, PILOT_CAPS,
                      PILOT_TARGET_DESCRIPTION, STATUS_INTERVAL_SECONDS,
                      session_cost)

SERVER = "http://127.0.0.1:8765"
GATE_CONTROL = Path("/run/pokemon-eval/gate-control.json")
ACTIVE_CELL = Path("/run/pokemon-eval/active-cell.json")
AGENTS = {"glimmer": "main", "spark": "spark"}
MAX_TRANSIENT_FAILURES = 5
NEW_GAME_BOOTSTRAP_ACTIONS = ["wait_600"] + [
    action for _ in range(8) for action in ("press_start", "wait_120")
] + ["press_a", "wait_120"]
TASK = (
    "Play Pokemon Blue autonomously from the current game state. Finish the "
    "opening tutorial by obtaining the Pokedex. Use the available Pokemon tools "
    "to observe and interact with the game. Continue until complete."
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    result = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return result


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temp.replace(path)


def gate_rows(path: Path, generation: str) -> list[dict]:
    return [row for row in read_jsonl(path) if row.get("generation") == generation]


def gate_usage(path: Path, generation: str) -> dict:
    rows = gate_rows(path, generation)
    responses = [row for row in rows if row.get("kind") == "response"]
    successful = [row for row in responses if 200 <= int(row.get("status", 0)) < 300]
    tin = tout = reasoning = 0
    cost = 0.0
    models = []
    for row in successful:
        usage = row.get("usage") or {}
        tin += int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        tout += int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
        reasoning += int((usage.get("completion_tokens_details") or {}).get(
            "reasoning_tokens", usage.get("reasoning_tokens", 0)) or 0)
        cost += float(usage.get("cost", 0) or 0)
        if row.get("model"):
            models.append(str(row["model"]))
    pauses = sum(float(row.get("paused_seconds", 0) or 0) for row in rows
                 if row.get("kind") == "credit_resume")
    return {
        "calls": len(successful), "in": tin, "out": tout,
        "reasoning": reasoning, "cost": cost,
        "models": sorted(set(models)), "paused_seconds": pauses,
        "image_calls": sum(int(row.get("image_parts", 0) or 0) > 0 for row in successful),
        "provider_policies": [row.get("provider_policy") for row in successful],
        "responses": responses,
        "upstream_attempts": sum(row.get("kind") == "upstream_attempt" for row in rows),
        "budget_blocks": sum(row.get("kind") == "budget_block" for row in rows),
    }


def session_entry(session_key: str) -> tuple[dict, Path | None]:
    for store in Path("/root/.openclaw/agents").glob("*/sessions/sessions.json"):
        try:
            entry = json.loads(store.read_text(encoding="utf-8")).get(session_key)
        except Exception:
            continue
        if isinstance(entry, dict):
            path = Path(entry["sessionFile"]) if entry.get("sessionFile") else None
            if path and not path.is_absolute():
                path = store.parent / path
            return entry, path
    return {}, None


def archive_transcript(session_key: str, archive: Path, seen: set[str]) -> int:
    _entry, path = session_entry(session_key)
    if not path or not path.is_file():
        return 0
    added = 0
    archive.parent.mkdir(parents=True, exist_ok=True)
    with archive.open("a", encoding="utf-8") as stream:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            identity = str(row.get("id") or hashlib.sha256(line.encode()).hexdigest())
            if identity in seen:
                continue
            seen.add(identity)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            added += 1
    return added


def transcript_assistant_calls(path: Path) -> int:
    count = 0
    for row in read_jsonl(path):
        message = row.get("message") if row.get("type") == "message" else None
        if isinstance(message, dict) and message.get("role") == "assistant":
            usage = message.get("usage") or {}
            if any(int(usage.get(key, 0) or 0) for key in (
                    "input", "output", "reasoningTokens", "total")):
                count += 1
    return count


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)


def transient_error(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in (
        "408", "409", "425", "429", "500", "502", "503", "504",
        "service_overloaded", "temporarily overloaded", "timed out", "timeout",
        "connection reset", "upstream_provider_shared_pool",
    )) and "eval_call_cap" not in lowered and "eval_routing_mismatch" not in lowered


def append_action_turns(action_log: Path, turns_path: Path, consumed: int,
                        counters: dict[str, int]) -> int:
    rows = read_jsonl(action_log)
    for row in rows[consumed:]:
        if row.get("path") != "/action" or row.get("source") != "openclaw":
            continue
        actions = list((row.get("payload") or {}).get("actions", []))
        before = row.get("before") or {}
        dialog = bool((before.get("dialog") or {}).get("active"))
        battle = bool((before.get("battle") or {}).get("in_battle"))
        meaningful = any(action not in {"wait", "wait_60", "a_until_dialog_end"}
                         and not (action == "press_a" and dialog) for action in actions)
        battle_turn = battle and meaningful
        counters["turns"] += 1
        counters["atomic_inputs"] += len(actions)
        counters["meaningful"] += int(meaningful)
        counters["battle"] += int(battle_turn)
        with turns_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "ts": now(), "turn": counters["turns"], "actions": actions,
                "meaningful": meaningful, "battle_decision": battle_turn,
                "map": (before.get("map") or {}).get("map_name"),
                "dialog_active": dialog, "in_battle": battle,
            }) + "\n")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=sorted(MODELS), required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=("pilot", "full"), default="pilot")
    args = parser.parse_args()
    caps = PILOT_CAPS if args.mode == "pilot" else FULL_CAPS
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    action_log = out / "server_events.jsonl"
    turns_path = out / "turns.jsonl"
    tool_log = out / "tool_events.jsonl"
    gate_ledger = out / "openrouter_gate.jsonl"
    transcript_archive = out / "transcript_events.jsonl"
    invocation_log = out / "agent_invocations.jsonl"
    invocation_dir = out / "agent_invocations"
    invocation_dir.mkdir()
    completion_latch = out / "tutorial_complete.json"
    generation = uuid.uuid4().hex
    expected_model = OC_MODEL_SLUG[args.model].removeprefix("openrouter/")
    expected_policy = {"only": [MODEL_PROVIDER[args.model]], "allow_fallbacks": False}
    data_dir = out / "game_data"
    env = dict(os.environ, POKEMON_ROM=str(args.rom.resolve()),
               POKEMON_DATA_DIR=str(data_dir), POKEMON_ACTION_LOG=str(action_log))
    server_log = (out / "server.log").open("w", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "openclaw_eval.server_wrapper:app",
         "--host", "127.0.0.1", "--port", "8765"], cwd=ROOT, env=env,
        stdout=server_log, stderr=subprocess.STDOUT, text=True)

    agent_id = AGENTS[args.model]
    session_key = f"agent:{agent_id}:pokemon-{args.model}-{uuid.uuid4().hex}"
    active = {
        "generation": generation, "model_key": args.model, "mode": args.mode,
        "tool_log": str(tool_log), "completion_latch": str(completion_latch),
    }
    gate = {**active, "active": True, "ledger": str(gate_ledger),
            "call_cap": caps["model_calls"], "credit_poll_seconds": 60,
            "expected_model": expected_model, "expected_provider_policy": expected_policy}
    atomic_json(ACTIVE_CELL, active)
    atomic_json(GATE_CONTROL, gate)

    terminal, error = "running", None
    started = time.monotonic()
    last_status = 0.0
    invocation = 0
    failures = 0
    consumed_actions = 0
    counters = {"turns": 0, "meaningful": 0, "battle": 0, "atomic_inputs": 0}
    seen_transcript: set[str] = set()
    active_process: subprocess.Popen | None = None
    try:
        requests.get("http://127.0.0.1:18999/health", timeout=10).raise_for_status()
        for _ in range(120):
            try:
                if requests.get(SERVER + "/state", timeout=2).status_code == 200:
                    break
            except requests.RequestException:
                pass
            if server.poll() is not None:
                raise RuntimeError("Nous game server exited during startup")
            time.sleep(1)
        else:
            raise RuntimeError("Nous game server did not become ready")

        conversation = session_key.rsplit(":", 1)[-1]
        requests.post(SERVER + "/games/new", json={"name": conversation},
                      timeout=30).raise_for_status()
        requests.post(SERVER + "/action", json={"actions": NEW_GAME_BOOTSTRAP_ACTIONS},
                      headers={"X-Eval-Source": "bootstrap"}, timeout=90).raise_for_status()
        action_log.write_text("", encoding="utf-8")

        while terminal == "running":
            consumed_actions = append_action_turns(action_log, turns_path,
                                                   consumed_actions, counters)
            usage = gate_usage(gate_ledger, generation)
            elapsed = time.monotonic() - started - usage["paused_seconds"]
            if completion_latch.is_file():
                terminal = "pilot_complete" if args.mode == "pilot" else "success"
                break
            if usage["calls"] >= caps["model_calls"]:
                terminal = "pilot_complete" if args.mode == "pilot" else "model_call_cap"
                break
            if elapsed >= caps["wallclock_seconds"]:
                terminal = "wallclock_cap"
                break
            if args.mode == "full" and counters["turns"] >= caps["turns"]:
                terminal = "turn_cap"
                break

            invocation += 1
            message = TASK if invocation == 1 else "Continue the same Pokemon task until it is complete."
            stdout_path = invocation_dir / f"{invocation:04d}.stdout.log"
            stderr_path = invocation_dir / f"{invocation:04d}.stderr.log"
            with stdout_path.open("w", encoding="utf-8") as stdout, \
                    stderr_path.open("w", encoding="utf-8") as stderr:
                active_process = subprocess.Popen([
                    "openclaw", "agent", "--agent", agent_id,
                    "--session-key", session_key, "--message", message,
                    "--thinking", "low", "--timeout", "600", "--json",
                ], cwd="/home/alex", stdout=stdout, stderr=stderr, text=True)
                invocation_started = time.monotonic()
                forced_stop = None
                cap_seen_at = None
                while active_process.poll() is None:
                    time.sleep(0.5)
                    archive_transcript(session_key, transcript_archive, seen_transcript)
                    consumed_actions = append_action_turns(
                        action_log, turns_path, consumed_actions, counters)
                    usage = gate_usage(gate_ledger, generation)
                    elapsed = time.monotonic() - started - usage["paused_seconds"]
                    if completion_latch.is_file():
                        forced_stop = "pilot_complete" if args.mode == "pilot" else "success"
                    elif usage["calls"] >= caps["model_calls"]:
                        cap_seen_at = cap_seen_at or time.monotonic()
                        transcript_ready = transcript_assistant_calls(transcript_archive) >= usage["calls"]
                        if transcript_ready or time.monotonic() - cap_seen_at >= 10:
                            forced_stop = ("pilot_complete" if args.mode == "pilot"
                                           else "model_call_cap")
                    elif elapsed >= caps["wallclock_seconds"]:
                        forced_stop = "wallclock_cap"
                    elif args.mode == "full" and counters["turns"] >= caps["turns"]:
                        forced_stop = "turn_cap"
                    if forced_stop:
                        stop_process(active_process)
                        break
                    if time.monotonic() - invocation_started >= 660:
                        stop_process(active_process)
                        forced_stop = "invocation_timeout"
                        break
                    if time.monotonic() - last_status >= STATUS_INTERVAL_SECONDS:
                        last_status = time.monotonic()
                        state = requests.get(SERVER + "/state", timeout=15).json()
                        print(f"[{now()}] container/{args.model} "
                              f"map={(state.get('map') or {}).get('map_name')} "
                              f"calls={usage['calls']}/{caps['model_calls']} "
                              f"turns={counters['turns']} inputs={counters['atomic_inputs']} "
                              f"cost=${usage['cost']:.6f} elapsed={elapsed:.0f}s", flush=True)

            rc = active_process.returncode
            active_process = None
            archive_transcript(session_key, transcript_archive, seen_transcript)
            consumed_actions = append_action_turns(action_log, turns_path,
                                                   consumed_actions, counters)
            stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
            stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
            with invocation_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({
                    "ts": now(), "invocation": invocation, "session_key": session_key,
                    "returncode": rc, "forced_stop": forced_stop,
                    "stdout_file": stdout_path.name, "stderr_file": stderr_path.name,
                    "stdout_tail": stdout_text[-2000:], "stderr_tail": stderr_text[-2000:],
                }, ensure_ascii=False) + "\n")
            if forced_stop and forced_stop != "invocation_timeout":
                terminal = forced_stop
                break
            combined = f"{stderr_text}\n{stdout_text[-6000:]}"
            if forced_stop == "invocation_timeout" or transient_error(combined):
                failures += 1
                if failures >= MAX_TRANSIENT_FAILURES:
                    terminal, error = "model_error", combined[-6000:]
                    break
                wait_seconds = min(2 ** (failures - 1), 16)
                print(f"[{now()}] container/{args.model} transient model failure "
                      f"{failures}/{MAX_TRANSIENT_FAILURES}; same session retries in {wait_seconds}s",
                      flush=True)
                time.sleep(wait_seconds)
                continue
            agent_failed = '"stopReason": "error"' in stdout_text or "LLM request failed" in stdout_text
            if rc or agent_failed:
                usage = gate_usage(gate_ledger, generation)
                if usage["calls"] >= caps["model_calls"]:
                    terminal = "pilot_complete" if args.mode == "pilot" else "model_call_cap"
                else:
                    terminal, error = "model_error", combined[-6000:]
                break
            failures = 0

    except BaseException as exc:
        terminal, error = "harness_error", f"{type(exc).__name__}: {exc}"
        (out / "traceback.log").write_text(
            __import__("traceback").format_exc(), encoding="utf-8")
    finally:
        if active_process is not None:
            stop_process(active_process)
        archive_transcript(session_key, transcript_archive, seen_transcript)
        append_action_turns(action_log, turns_path, consumed_actions, counters)
        atomic_json(GATE_CONTROL, {**gate, "active": False})
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
        server_log.close()

    entry, transcript_path = session_entry(session_key)
    if transcript_path and transcript_path.is_file():
        shutil.copy2(transcript_path, out / "transcript_current.jsonl")
    (out / "openclaw_session.json").write_text(json.dumps({
        "session_key": session_key, "entry": entry,
    }, indent=2), encoding="utf-8")
    usage = gate_usage(gate_ledger, generation)
    policies_ok = all(policy == expected_policy for policy in usage["provider_policies"])
    tools = read_jsonl(tool_log)
    observe_calls = sum(row.get("kind") == "observe" for row in tools)
    action_calls = sum(row.get("kind") == "action" for row in tools)
    estimated = usage["cost"] or session_cost(args.model, usage["in"], usage["out"])
    meta = {
        "mode": args.mode, "framework": "openclaw", "model_key": args.model,
        "model": MODELS[args.model], "model_display": MODEL_DISPLAY[args.model],
        "integration": "stock OpenClaw native loop + native tools + unmodified Nous server",
        "terminal": terminal, "success": terminal == "success", "error": error,
        "turns": counters["turns"], "meaningful_gameplay_turns": counters["meaningful"],
        "battle_turns": counters["battle"], "atomic_inputs": counters["atomic_inputs"],
        "model_calls": usage["calls"], "upstream_attempts": usage["upstream_attempts"],
        "native_agent_invocations": invocation,
        "tokens": {"in": usage["in"], "out": usage["out"],
                   "reasoning": usage["reasoning"]},
        "estimated_cost_usd": estimated, "cost_source": "OpenRouter usage through egress gate",
        "wallclock_seconds": round(time.monotonic() - started - usage["paused_seconds"], 3),
        "total_elapsed_seconds": round(time.monotonic() - started, 3),
        "credit_pause_seconds_excluded": round(usage["paused_seconds"], 3),
        "session_key": session_key, "session_resume_verified": bool(entry and invocation >= 1),
        "actual_models": usage["models"], "billing_providers": ["openrouter"] if usage["calls"] else [],
        "provider_policy": {"only": [MODEL_PROVIDER[args.model]], "fallbacks": []},
        "provider_policy_verified": policies_ok,
        "tool_inventory": ["pokemon_observe", "pokemon_action"],
        "observe_tool_calls": observe_calls, "action_tool_calls": action_calls,
        "vision_model_calls": usage["image_calls"], "image_input_mode": "native",
        "completion_rule": f"flags.has_pokedex true {COMPLETION_CONFIRMATIONS} times",
        "completion_latched": completion_latch.is_file(),
        "pilot_contract": PILOT_TARGET_DESCRIPTION if args.mode == "pilot" else None,
        "call_cap_enforcement": "successful upstream responses blocked at egress before cap+1",
        "expected_model": expected_model,
    }
    if usage["models"] and set(usage["models"]) != {expected_model} and not error:
        meta["terminal"], meta["error"] = "model_mismatch", repr(usage["models"])
    (out / "session_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2), flush=True)
    valid_terminals = {"pilot_complete", "success", "model_call_cap", "turn_cap"}
    return 0 if meta["terminal"] in valid_terminals else 1


if __name__ == "__main__":
    raise SystemExit(main())
