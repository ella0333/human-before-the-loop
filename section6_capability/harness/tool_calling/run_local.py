"""Run one tool-calling Pokemon Red attempt on the constrained runtime."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from constrained.run_local import (MAX_OUTAGE_SESSION_ATTEMPTS, MAX_SESSION_ATTEMPTS,  # noqa: E402
                                   UPSTREAM_OUTAGE_ROUND_PAUSE_SECONDS,
                                   UPSTREAM_OVERLOAD_COOLDOWN_SECONDS, append, now,
                                   retryable_model_error, verify_sources)
from credit_gate import credit_error, wait_if_exhausted  # noqa: E402
from evalspec import (COMPLETION_CONFIRMATIONS, MODEL_DISPLAY, MODELS, PILOT_CAPS,  # noqa: E402
                      PILOT_TARGET_DESCRIPTION, CONTAINER_FULL_CAPS, CONTAINER_PROVIDER_POLICY,
                      STATUS_INTERVAL_SECONDS, session_cost)
from tool_calling.frames import current_frame, data_uri  # noqa: E402
from tool_calling.prompt import SYSTEM_PROMPT  # noqa: E402
from tool_calling.provider import BudgetExceeded, ToolCallingProvider  # noqa: E402
from tool_calling.watchdog import bridge_alive, restart_bridge  # noqa: E402

FRAMEWORK = "tool_calling"
TOOL_FOR_ACTION = {
    "walk_to_object": "go_to", "walk_direction": "walk",
    "battle_select": "battle_action", "battle_sub_select": "battle_action",
    "battle_sub_cancel": "battle_action", "party_select": "choose_pokemon",
    "party_cancel": "choose_pokemon", "menu_select": "menu_select", "cancel": "menu_select",
}
TOOL_TEXT = {
    "go_to": ("Automatically walk to a listed nearby location, object, or person and "
              "interact with it.", "target"),
    "walk": ("Walk in a compass direction toward another area.", "direction"),
    "battle_action": ("Choose a battle action. Battle text advances automatically.", "option"),
    "choose_pokemon": ("Choose a Pokemon from the party list, or Back.", "option"),
    "menu_select": ("Select an option in the current game menu.", "option"),
    "other_action": ("Take one of the other listed actions.", "option"),
}
TOOL_ORDER = list(TOOL_TEXT)
BUTTONS = ["A", "B", "START", "SELECT", "UP", "DOWN", "LEFT", "RIGHT"]
LUA_BUTTON = {"A": "A", "B": "B", "START": "start", "SELECT": "select",
              "UP": "up", "DOWN": "down", "LEFT": "left", "RIGHT": "right"}
LETTER_LINE = re.compile(r"^[A-Z]\. ", re.MULTILINE)
REASONING = {"type": "string", "description": "Short private analysis of this decision."}
EXTRA_RETRYABLE = ("tool call invalid",)
MAX_BRIDGE_RESTARTS = 25


def tool(name: str, description: str, properties: dict) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object",
                       "properties": {**properties, "reasoning": REASONING},
                       "required": [*properties, "reasoning"],
                       "additionalProperties": False}}}


def offered_tools(choices: dict, needs_name: bool) -> tuple[list[dict], dict[tuple[str, str], str]]:
    groups: dict[str, dict[str, str]] = {}
    for letter, choice in choices.items():
        name = TOOL_FOR_ACTION.get(choice.get("action"), "other_action")
        label = str(choice.get("label", "")).strip() or letter
        group = groups.setdefault(name, {})
        unique, copy = label, 2
        while unique in group:
            unique, copy = f"{label} #{copy}", copy + 1
        group[unique] = letter
    tools, lookup = [], {}
    for name in TOOL_ORDER:
        if name in groups:
            description, argument = TOOL_TEXT[name]
            tools.append(tool(name, description, {argument: {
                "type": "string", "enum": list(groups[name])}}))
            lookup.update({(name, label): letter for label, letter in groups[name].items()})
    if needs_name:
        tools.append(tool("enter_name", "Type the requested name. Letters only, at most seven.",
                          {"name": {"type": "string", "maxLength": 7}}))
    tools.append(tool("press_buttons", "Press raw Game Boy buttons in order.",
                      {"buttons": {"type": "array", "items": {"type": "string", "enum": BUTTONS},
                                   "minItems": 1, "maxItems": 10}}))
    return tools, lookup


def render(state_text: str, tools: list[dict]) -> str:
    lines = [LETTER_LINE.sub("- ", state_text).strip(), "", "Tools you can call now:"]
    for item in tools:
        function = item["function"]
        options = [f"{field}: {', '.join(rule['enum'])}"
                   for field, rule in function["parameters"]["properties"].items()
                   if "enum" in rule]
        lines.append(f"- {function['name']}" + (f" ({'; '.join(options)})" if options else ""))
    return "\n".join(lines)


def tool_turn(shown: str, call: dict, call_id: str, outcome: str) -> list[dict]:
    return [
        {"role": "user", "content": shown},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": call["name"],
                         "arguments": json.dumps(call["arguments"], ensure_ascii=False)}}]},
        {"role": "tool", "tool_call_id": call_id, "content": outcome or "done"},
    ]


def trim(history: list[dict], limit: int = 24) -> list[dict]:
    history = history[-limit:]
    while history and history[0]["role"] != "user":
        history = history[1:]
    return history


async def run(model_key: str, output: Path, mode: str = "pilot") -> dict:
    caps = PILOT_CAPS if mode == "pilot" else CONTAINER_FULL_CAPS
    baseline = ROOT / "local_runtime" / "baseline_red"
    source_manifest = baseline / "source_manifest.json"
    if not source_manifest.is_file():
        raise RuntimeError("run tools/stage_local_runtime.py first")
    verify_sources(source_manifest)

    attempt_id = f"tools-{model_key}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    attempt = ROOT / "local_runtime" / "attempts" / attempt_id
    if attempt.exists():
        raise RuntimeError(f"attempt collision: {attempt}")
    shutil.copytree(baseline, attempt)
    shutil.copy2(ROOT / "constrained" / "pokemon_bridge.lua", attempt / "pokemon_bridge.lua")
    bridge = attempt / "saves" / "_bridge"
    bridge.mkdir(parents=True)
    output.mkdir(parents=True, exist_ok=False)
    screenshots = output / "screenshots"
    screenshots.mkdir()

    os.environ.update({
        "POKEMON_EVAL_ROM": str(attempt / "PokemonRed.gb"),
        "POKEMON_EVAL_ROM_DIR": str(attempt),
        "POKEMON_EVAL_VBA": str(attempt / "VBA-RR" / "vba.exe"),
        "POKEMON_EVAL_LUA": str(attempt / "pokemon_bridge.lua"),
        "POKEMON_EVAL_SAVE_BASE": str(attempt / "saves"),
        "POKEMON_EVAL_LUA_SAVE_BASE": str(attempt / "saves"),
        "POKEMON_EVAL_BRIDGE_DIR": str(bridge),
        "POKEMON_EVAL_POKEMON_ACCESS": str(attempt / "pokemon-access"),
        "POKEMON_EVAL_MAP_DB": str(attempt / "map_database.json"),
    })
    from constrained.helpers import pokemon_game as helpers
    from constrained.helpers.pokemon_game import PokemonGame, read_state, send_commands

    calls_path = output / "model_calls.jsonl"
    events_path = output / "events.jsonl"
    provider = ToolCallingProvider(
        api_key=os.environ["OPENROUTER_API_KEY"], model=MODELS[model_key],
        provider_policy=CONTAINER_PROVIDER_POLICY[model_key], max_calls=caps["model_calls"],
        log_cb=lambda row: append(calls_path, row))
    provider.set_system_prompt(SYSTEM_PROMPT)
    game = PokemonGame("pokemon_red", "Pokemon Red")
    boot_started = time.monotonic()
    if not await game.launch(fresh_start=True):
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
    meaningful_turns = battle_turns = semantic_actions = raw_groups = raw_inputs = 0
    frames_attached = 0
    bridge_restarts = 0
    terminal, error = "running", None
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

            state_result = pending_state or await game.get_game_state()
            pending_state = None
            if state_result is None:
                await asyncio.sleep(0.25)
                continue
            state_text, choices, metadata = state_result
            if not choices and not metadata.get("needs_name"):
                if state_text.strip():
                    history.append({"role": "user", "content": state_text[:500]})
                    history = trim(history)
                continue

            if not bridge_alive(bridge):
                bridge_restarts += 1
                alive, lua_window = await asyncio.to_thread(restart_bridge, helpers, game, bridge)
                append(events_path, {"event": "bridge_restart", "restart": bridge_restarts,
                                     "turn": meaningful_turns + 1, "recovered": alive,
                                     "lua_window": lua_window})
                print(f"[{now()}] tools/{model_key} bridge stopped; restart "
                      f"{bridge_restarts} {'recovered' if alive else 'FAILED'}", flush=True)
                if not alive or bridge_restarts > MAX_BRIDGE_RESTARTS:
                    terminal, error = "bridge_failed", f"Lua bridge did not recover (restart {bridge_restarts})"
                    break
                continue
            tools, lookup = offered_tools(choices, bool(metadata.get("needs_name")))
            shown = render(state_text, tools)
            frame = current_frame(bridge)
            image_uri = data_uri(frame) if frame is not None else None
            turn_no = meaningful_turns + 1
            if frame is not None:
                frames_attached += 1
                if turn_no <= 5 or turn_no % 25 == 0:
                    frame.save(screenshots / f"turn_{turn_no:04d}.png")
            append(events_path, {"event": "decision_requested", "turn": turn_no,
                   "model_call": provider.calls_made + 1, "metadata": metadata,
                   "shown_text": shown, "choices": choices,
                   "tools": [item["function"]["name"] for item in tools],
                   "frame_attached": frame is not None})
            paused_seconds += await asyncio.to_thread(
                wait_if_exhausted, os.environ["OPENROUTER_API_KEY"], f"tools/{model_key}")
            decision_failures = 0
            while True:
                result = await provider.generate(shown, image_uri, tools, "pokemon_red",
                                                 turn_no, history)
                call_error = result.get("error")
                if not call_error:
                    break
                if credit_error(call_error):
                    provider.calls_made = max(0, provider.calls_made - 1)
                    append(events_path, {"event": "credit_pause", "error": call_error})
                    paused_seconds += await asyncio.to_thread(
                        wait_if_exhausted, os.environ["OPENROUTER_API_KEY"],
                        f"tools/{model_key}", True)
                    continue
                if not (retryable_model_error(call_error)
                        or any(marker in str(call_error) for marker in EXTRA_RETRYABLE)):
                    break
                decision_failures += 1
                outage = "upstream_provider_shared_pool" in str(call_error).lower()
                attempt_cap = MAX_OUTAGE_SESSION_ATTEMPTS if outage else MAX_SESSION_ATTEMPTS
                if decision_failures >= attempt_cap or provider.calls_made >= caps["model_calls"]:
                    break
                if outage and decision_failures == MAX_SESSION_ATTEMPTS:
                    wait_s = UPSTREAM_OUTAGE_ROUND_PAUSE_SECONDS
                elif outage:
                    wait_s = UPSTREAM_OVERLOAD_COOLDOWN_SECONDS
                else:
                    wait_s = min(2 ** (decision_failures - 1), 16)
                append(events_path, {"event": "model_retry", "turn": turn_no,
                       "failed_call": provider.calls_made, "attempt": decision_failures,
                       "attempt_cap": attempt_cap, "wait_seconds": wait_s,
                       "error": call_error})
                await asyncio.sleep(wait_s)
            if result.get("error"):
                terminal, error = "model_error", result["error"]
                break

            meaningful_turns += 1
            name, arguments = result["name"], result["arguments"]
            if name == "press_buttons":
                buttons = [button for button in arguments["buttons"] if button in LUA_BUTTON]
                send_commands([f"PRESS:{LUA_BUTTON[button]}" for button in buttons])
                await asyncio.sleep(0.3 * len(buttons) + 0.4)
                raw_groups += 1
                raw_inputs += len(buttons)
                action = {"kind": "raw", "buttons": buttons}
            elif name == "enter_name":
                entered = re.sub(r"[^A-Za-z]", "", str(arguments.get("name", "")))[:7] or "RED"
                await game.send_name(entered)
                semantic_actions += 1
                action = {"kind": "name", "value": entered}
            else:
                argument = TOOL_TEXT[name][1]
                letter = lookup[(name, arguments[argument])]
                detail = await game.send_choice(letter, choices)
                semantic_actions += 1
                if metadata.get("mode") == "battle":
                    battle_turns += 1
                action = {"kind": "battle_decision" if metadata.get("mode") == "battle"
                          else "decision", "tool": name, "argument": arguments[argument],
                          "letter": letter, "result": detail}
            append(events_path, {"event": "meaningful_turn", "turn": meaningful_turns,
                                 "model_call": provider.calls_made, "tool": name,
                                 "reasoning": arguments.get("reasoning"), "action": action})
            if action["kind"] == "raw":
                outcome = "Pressed " + " ".join(action["buttons"])
            elif action["kind"] == "name":
                outcome = f"Entered {action['value']}"
            else:
                outcome = str(action.get("result") or "done")
            history.extend(tool_turn(shown, result, result.get("id") or f"call_{turn_no}",
                                     outcome[:500]))
            history = trim(history)

            if time.monotonic() - last_status >= STATUS_INTERVAL_SECONDS:
                last_status = time.monotonic()
                print(f"[{now()}] tools/{model_key} map={raw.get('map_name')} "
                      f"turns={meaningful_turns} calls={provider.calls_made} "
                      f"semantic={semantic_actions} raw={raw_groups} "
                      f"elapsed={elapsed:.0f}s cost=${provider.cost_usd:.4f}", flush=True)
    except BudgetExceeded:
        terminal = "model_call_cap"
    except Exception as exc:
        terminal, error = "harness_error", f"{type(exc).__name__}: {exc}"
    finally:
        raw_input_log = game.get_input_log()
        (output / "atomic_inputs.jsonl").write_text(raw_input_log, encoding="utf-8")
        if (bridge / "bridge_errors.txt").is_file():
            shutil.copy2(bridge / "bridge_errors.txt", output / "bridge_errors.txt")
        await game.quit()
        verify_sources(source_manifest)

    meta = {
        "attempt_id": attempt_id, "mode": mode, "framework": FRAMEWORK,
        "model_key": model_key, "model": MODELS[model_key],
        "model_display": MODEL_DISPLAY[model_key],
        "interface": "native tool calls; constrained state text, history, and runtime",
        "terminal": terminal, "success": terminal == "success", "error": error,
        "turns": meaningful_turns, "meaningful_gameplay_turns": meaningful_turns,
        "battle_decision_turns": battle_turns, "battle_turns": battle_turns,
        "model_calls": provider.calls_made,
        "semantic_actions": semantic_actions, "button_groups": raw_groups,
        "raw_button_presses": raw_inputs,
        "atomic_inputs": sum(1 for line in raw_input_log.splitlines() if line.strip()),
        "vision_model_calls": provider.image_calls, "frames_attached": frames_attached,
        "bridge_restarts": bridge_restarts,
        "bridge_caught_errors": (sum(1 for line in (output / "bridge_errors.txt").read_text(
            encoding="utf-8").splitlines() if line.strip())
            if (output / "bridge_errors.txt").is_file() else 0),
        "image_input_mode": "VBA-RR emulator frames",
        "wallclock_seconds": round(time.monotonic() - started - paused_seconds, 3),
        "total_elapsed_seconds": round(time.monotonic() - started, 3),
        "credit_pause_seconds_excluded": round(paused_seconds, 3),
        "boot_seconds_excluded": round(started - boot_started, 3),
        "tokens": {"in": provider.tokens_in, "out": provider.tokens_out,
                   "reasoning": provider.reasoning_tokens},
        "estimated_cost_usd": provider.cost_usd or session_cost(
            model_key, provider.tokens_in, provider.tokens_out),
        "provider_policy": CONTAINER_PROVIDER_POLICY[model_key],
        "actual_providers": sorted(provider.providers),
        "bridge_sha256": hashlib.sha256((attempt / "pokemon_bridge.lua").read_bytes()).hexdigest(),
        "attempt_runtime": str(attempt), "source_integrity": "verified_after_run",
        "completion_rule": f"Pokedex event bit (0xD74B & 0x20) set {COMPLETION_CONFIRMATIONS} times",
        "pilot_contract": PILOT_TARGET_DESCRIPTION if mode == "pilot" else None,
    }
    (output / "session_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=sorted(MODELS), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=("pilot", "full"), default="pilot")
    args = parser.parse_args()
    meta = asyncio.run(run(args.model, args.out, args.mode))
    print(json.dumps(meta, indent=2))
    return 0 if meta["terminal"] in {
        "pilot_complete", "success", "wallclock_cap", "model_call_cap"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
