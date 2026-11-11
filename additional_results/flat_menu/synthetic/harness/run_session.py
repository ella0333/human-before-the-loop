from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from evalspec import (BUDGET, MOCK_DISCORD_PORTS, MODELS, MODEL_DISPLAY,
                      OC_MODEL_SLUG, OC_TOOLS_ALLOW, load_prompts, session_cost)

REPO = Path(__file__).resolve().parent
OPENROUTER_KEY_FILE = Path("/run/reliability-eval/openrouter.key")
ACTIVITY_EVENTS = Path("/run/mock-activities/events.jsonl")
OUTBOX = Path("/run/mock-discord/outbox.jsonl")
SCORER_VERSION = "context-canonical-outcomes-four-model-v3"


def load_prompt(prompt_id: str) -> dict:
    for row in load_prompts():
        if row["id"] == prompt_id: return row
    raise SystemExit(f"prompt id not found: {prompt_id}")


def parse_config(config_id: str) -> dict:
    framework, model_key = config_id.rsplit("__", 1)
    if framework not in {"constrained", "constrained_flat", "openclaw"} or model_key not in MODELS:
        raise SystemExit(f"unknown config: {config_id}")
    return {"framework": framework, "model_key": model_key}


def openrouter_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key and OPENROUTER_KEY_FILE.exists(): key = OPENROUTER_KEY_FILE.read_text(encoding="utf-8").strip()
    if not key: raise RuntimeError("OPENROUTER_API_KEY is unavailable")
    return key


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists(): return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try: rows.append(json.loads(line))
        except json.JSONDecodeError: pass
    return rows


def normalize_target(value: str) -> str:
    value = str(value or "")
    for target in ("friend_1", "friend_2", "friend_3", "community_server"):
        if target in value: return target
    if "general" in value: return "community_server"
    return value


def canonical_plugin_event(row: dict) -> dict:
    tool, args, result = row.get("tool", ""), row.get("args") or {}, row.get("result") or {}
    mapping = {
        "mock_discord_read": ("A", "discord_read", args.get("target")),
        "mock_ai_chat": ("B", "ai_chat", args.get("assistant")),
        "mock_video_game": ("C", "video_game", args.get("game")),
        "mock_table_game": ("D", "table_game", args.get("game")),
        "mock_journal_write": ("F", "journal", "journal"),
        "mock_document_write": ("G", "write", args.get("kind")),
        "mock_music_make": ("H", "music", "short_loop"),
        "mock_social_browse": ("L", "social", args.get("platform")),
        "mock_website_check": ("M", "website", "dashboard"),
        "mock_web_search": ("Q", "web_search", args.get("query")),
        "mock_read": ("R", "read", args.get("kind")),
    }
    main, activity, target = mapping.get(tool, (None, tool, None))
    content = args.get("entry") if tool == "mock_journal_write" else args.get("content")
    return {"ts": row.get("ts"), "main_choice": main, "activity": activity,
            "target": normalize_target(target), "content": content, "arguments": args,
            "result": result, "source": "openclaw_tool", "tool": tool}


def canonical_outbound(row: dict) -> dict | None:
    if not row.get("id") or not row.get("target"): return None
    return {"ts": row.get("ts"), "main_choice": "A", "activity": "discord_send",
            "target": normalize_target(row.get("target")), "content": row.get("text", ""),
            "arguments": {"target": row.get("target"), "message": row.get("text", "")},
            "result": "posted", "source": "openclaw_message", "tool": "message"}


_SEARCH_FILLER = {
    "a", "an", "the", "do", "does", "did", "about", "information",
    "online", "search", "web", "for", "please",
}


def canonical_search_text(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", str(value or "").lower())
    return " ".join(word for word in words if word not in _SEARCH_FILLER)


def search_topics_match(expected_key: str, actual_key: str | None) -> bool:
    return bool(actual_key and actual_key.startswith("search:") and
                canonical_search_text(expected_key.split(":", 1)[1]) ==
                canonical_search_text(actual_key.split(":", 1)[1]))


def first_matches(prompt: dict, action: dict | None) -> bool:
    if not action or action.get("main_choice") != prompt["expected_main_choice"]: return False
    key, target = prompt["expected_result_key"], action.get("target")
    if key.startswith("dm:"): return target == key.split("|", 1)[0].split(":", 1)[1]
    if key == "read:server": return target == "community_server"
    action_key = action_result_key(action)
    if action_key is not None:
        if key.startswith("search:"):
            return search_topics_match(key, action_key)
        return action_key in set(prompt.get("acceptable_result_keys") or [key])
    kind, value = key.split(":", 1)
    expected_target = {"journal": "journal", "blog": "Blog post", "story": "Short story",
                       "music": "short_loop", "website": "dashboard"}.get(kind, value)
    return target == expected_target


def action_result_key(action: dict) -> str | None:
    activity, target = action.get("activity"), action.get("target")
    content = action.get("content")
    if activity == "discord_send": return f"dm:{target}|{content or ''}"
    if activity == "discord_read" and target == "community_server": return "read:server"
    if activity == "ai_chat": return f"ai:{target}"
    if activity == "video_game": return f"video:{target}"
    if activity == "table_game": return f"table:{target}"
    if activity == "journal": return f"journal:{content or ''}"
    if activity == "write" and target == "Blog post": return f"blog:{content or ''}"
    if activity == "write" and target == "Short story": return f"story:{content or ''}"
    if activity == "music" and target == "short_loop": return "music:loop"
    if activity == "social": return f"social:{target}"
    if activity == "website" and target == "dashboard": return "website:no_updates"
    if activity == "web_search": return f"search:{str(target or '').strip()}"
    if activity == "read": return f"read:{target}"
    return None


def result_matches(prompt: dict, actions: list[dict]) -> bool:
    key = prompt["expected_result_key"]
    if key == "read:server":
        return (any(action_result_key(a) == key for a in actions)
                and not any(a.get("activity") == "discord_send" for a in actions))
    if key.startswith("search:"):
        return any(search_topics_match(key, action_result_key(a)) for a in actions)
    acceptable = set(prompt.get("acceptable_result_keys") or [key])
    return any(action_result_key(a) in acceptable for a in actions)


def action_allowed(prompt: dict, action: dict) -> bool:
    if action.get("main_choice") != prompt["expected_main_choice"]:
        return False
    key = prompt["expected_result_key"]
    if key.startswith("dm:"):
        left, text = key.split("|", 1); target = left.split(":", 1)[1]
        return (action.get("target") == target and
                (action.get("activity") == "discord_read" or
                 action_result_key(action) == key))
    if key == "read:server":
        return action_result_key(action) == key
    if key.startswith("search:"):
        return search_topics_match(key, action_result_key(action))
    return action_result_key(action) in set(prompt.get("acceptable_result_keys") or [key])


def score(prompt: dict, actions: list[dict], invalid_actions: int, agent_done: str, error) -> dict:
    expected_main = prompt["expected_main_choice"]
    correct = [a for a in actions if action_allowed(prompt, a)]
    wrong = [a for a in actions if not action_allowed(prompt, a)]
    achieved = result_matches(prompt, actions)
    if prompt["expected_result_key"].startswith("dm:"):
        allowed = 2 if any(a["activity"] == "discord_read" for a in correct) else 1
    else: allowed = 1
    unnecessary = max(0, len(correct) - allowed)
    terminal_ok = agent_done == "completed" and not error
    return {"first_action_correct": first_matches(prompt, actions[0] if actions else None),
            "full_task_correct": achieved and not wrong and not unnecessary and not invalid_actions and terminal_ok,
            "requested_result_achieved": achieved, "wrong_action_count": len(wrong),
            "unnecessary_action_count": unnecessary, "invalid_action_count": invalid_actions,
            "completion_rate_value": bool(terminal_ok)}


async def run_constrained(cfg, prompt, out) -> dict:
    from evalagent.agent import RunConfig, run_session
    raw = (out / "raw_calls.jsonl").open("w", encoding="utf-8")
    events_file = (out / "mock_events.jsonl").open("w", encoding="utf-8")
    def log_call(rec): raw.write(json.dumps({"ts": time.time(), **rec}) + "\n"); raw.flush()
    def log_event(rec): events_file.write(json.dumps({"ts": time.time(), **rec}) + "\n"); events_file.flush()
    rc = RunConfig(user_prompt=prompt["prompt"], model=MODELS[cfg["model_key"]],
                   api_key=openrouter_api_key(), max_calls=BUDGET["max_model_calls"],
                   max_menu_turns=BUDGET["max_menu_turns"],
                   flat_menu=cfg["framework"] == "constrained_flat",
                   log_call=log_call, log_event=log_event)
    t0 = time.time(); res = await run_session(rc); raw.close(); events_file.close()
    call_rows = read_jsonl(out / "raw_calls.jsonl")
    retries = sum(max(0, len(x.get("request_attempts") or []) - 1) for x in call_rows)
    actions = [{**e, "main_choice": {"discord_read": "A", "discord_send": "A",
                "ai_chat": "B", "video_game": "C", "table_game": "D", "journal": "F",
                "write": "G", "music": "H", "social": "L", "website": "M",
                "web_search": "Q", "read": "R"}.get(e.get("activity")),
                "source": "constrained"} for e in res.events]
    first_choice = next((x.get("main_equivalent", x["choice"]) for x in res.menu_choices
                         if x["level"] in ("main", "flat")), None)
    if first_choice and actions: actions[0]["main_choice"] = first_choice
    (out / "outbound.jsonl").write_text("".join(json.dumps(a, ensure_ascii=False) + "\n" for a in actions if a.get("activity") == "discord_send"), encoding="utf-8")
    full_menu_path = ([str(item.get("choice", "")) for item in res.menu_choices]
                      if not rc.flat_menu else list(res.menu_path))
    return {"agent_done": res.agent_done, "actions": actions, "menu_path": full_menu_path,
            "menu_choices": res.menu_choices, "turns_used": res.calls_made,
            "tokens": {"in": res.tokens_in, "out": res.tokens_out},
            "reasoning_tokens": res.native_reasoning_tokens, "error": res.error,
            "wallclock_s": round(time.time() - t0, 3), "cost_usd": None,
            "invalid_actions": 1 if res.error else 0,
            "first_main_menu_choice_correct": first_choice == prompt["expected_main_choice"],
            "retries": retries}


def _openclaw_session_entry(session_key: str) -> tuple[dict, Path | None]:
    for store_path in Path("/root/.openclaw/agents").glob("*/sessions/sessions.json"):
        try: store = json.loads(store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): continue
        entry = store.get(session_key)
        if isinstance(entry, dict):
            raw = entry.get("sessionFile"); path = Path(raw) if raw else None
            if path and not path.is_absolute(): path = store_path.parent / path
            return entry, path
    return {}, None


def transcript_data(text: str) -> tuple[list[dict], list[str], int, int, int, int]:
    calls, levels, reasoning_blocks, reasoning_tokens, tin, tout = [], [], 0, 0, 0, 0
    for row in [x for x in read_jsonl_text(text)]:
        if row.get("type") == "thinking_level_change" and row.get("thinkingLevel"): levels.append(str(row["thinkingLevel"]))
        msg = row.get("message") if row.get("type") == "message" else None
        if not isinstance(msg, dict) or msg.get("role") != "assistant": continue
        usage = msg.get("usage") or {}; tin += int(usage.get("input", 0) or 0); tout += int(usage.get("output", 0) or 0)
        reasoning_tokens += int(usage.get("reasoningTokens", 0) or 0)
        if msg.get("reasoning") or msg.get("reasoning_content"): reasoning_blocks += 1
        for block in msg.get("content") or []:
            if not isinstance(block, dict): continue
            if block.get("type") in {"thinking", "reasoning"}: reasoning_blocks += 1
            if block.get("type") in {"toolCall", "tool_call"}:
                calls.append({"id": block.get("id"), "name": block.get("name") or block.get("toolName"),
                              "arguments": block.get("arguments") or block.get("input") or {}})
    return calls, levels, reasoning_blocks, reasoning_tokens, tin, tout


def read_jsonl_text(text: str):
    for line in text.splitlines():
        try: yield json.loads(line)
        except json.JSONDecodeError: continue


def assistant_provider_errors(messages: list[dict]) -> list[str]:
    errors = []
    for msg in messages:
        if msg.get("stopReason") != "error" and not msg.get("errorMessage"):
            continue
        errors.append(json.dumps({
            "message": msg.get("errorMessage"),
            "code": msg.get("errorCode"),
            "body": msg.get("errorBody"),
        }, ensure_ascii=False))
    return errors


def run_openclaw(cfg, prompt, out) -> dict:
    for path in (ACTIVITY_EVENTS, OUTBOX): path.parent.mkdir(parents=True, exist_ok=True); path.write_text("", encoding="utf-8")
    t0 = time.time(); conversation = f"reliability-{cfg['model_key']}-{time.time_ns()}"
    payload = json.dumps({"id": f"eval-{time.time_ns()}", "conversationId": conversation,
                          "sender": "user", "text": prompt["prompt"]}).encode()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{MOCK_DISCORD_PORTS[cfg['model_key']]}/inbound",
                                     data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=BUDGET["timeout_seconds"] + 60) as response:
            ingress = json.loads(response.read().decode())
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        (out / "transcript.jsonl").write_text("", encoding="utf-8")
        (out / "openclaw_session.json").write_text("{}", encoding="utf-8")
        (out / "tool_inventory.json").write_text("[]", encoding="utf-8")
        (out / "outbound.jsonl").write_text("", encoding="utf-8")
        (out / "mock_events.jsonl").write_text("", encoding="utf-8")
        return {"agent_done": "error", "actions": [], "menu_path": [], "menu_choices": [],
                "turns_used": 0, "tokens": {"in": 0, "out": 0}, "reasoning_tokens": 0,
                "error": error, "wallclock_s": round(time.time()-t0, 3), "cost_usd": None,
                "invalid_actions": 1, "tool_calls": []}
    session_key = ingress.get("sessionKey", ""); entry, transcript_path = _openclaw_session_entry(session_key)
    transcript = transcript_path.read_text(encoding="utf-8", errors="replace") if transcript_path and transcript_path.exists() else ""
    (out / "transcript.jsonl").write_text(transcript, encoding="utf-8")
    (out / "openclaw_session.json").write_text(json.dumps({"session_key": session_key, "entry": entry}, indent=2), encoding="utf-8")
    outbox_rows, plugin_rows = read_jsonl(OUTBOX), read_jsonl(ACTIVITY_EVENTS)
    (out / "outbound.jsonl").write_text("".join(json.dumps(x) + "\n" for x in outbox_rows), encoding="utf-8")
    (out / "mock_events.jsonl").write_text("".join(json.dumps(x) + "\n" for x in plugin_rows), encoding="utf-8")
    actions = [canonical_plugin_event(x) for x in plugin_rows]
    actions += [a for a in (canonical_outbound(x) for x in outbox_rows) if a]
    actions.sort(key=lambda x: x.get("ts") or 0)
    calls, levels, blocks, rtokens, tin, tout = transcript_data(transcript)
    inventory = (((entry.get("systemPromptReport") or {}).get("tools") or {}).get("entries") or [])
    (out / "tool_inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    names = [x.get("name") for x in inventory if isinstance(x, dict)]
    assistant_messages = [row["message"] for row in read_jsonl_text(transcript)
                          if isinstance(row.get("message"), dict)
                          and row["message"].get("role") == "assistant"]
    actual_models = {msg.get("model") for msg in assistant_messages}
    errors = []
    errors.extend(f"provider error: {detail}" for detail in
                  assistant_provider_errors(assistant_messages))
    if not transcript: errors.append("OpenClaw transcript not found")
    if entry.get("status") != "done" or entry.get("abortedLastRun") is True: errors.append(f"unclean session status={entry.get('status')!r}")
    expected_oc_model = OC_MODEL_SLUG[cfg["model_key"]].removeprefix("openrouter/")
    if actual_models != {expected_oc_model}: errors.append(f"model mismatch {sorted(actual_models)!r}")
    if set(names) != set(OC_TOOLS_ALLOW): errors.append(f"tool inventory mismatch {sorted(names)!r}")
    if "low" not in levels: errors.append(f"thinking level mismatch {levels!r}")
    error = "; ".join(errors) or None
    failed_results = sum(1 for row in read_jsonl_text(transcript)
                         if isinstance(row.get("message"), dict) and row["message"].get("role") == "tool"
                         and row["message"].get("isError") is True)
    cost = entry.get("estimatedCostUsd")
    if cost is None and (tin or tout): cost = session_cost(cfg["model_key"], tin, tout)
    return {"agent_done": "completed" if not error else "error", "actions": actions,
            "menu_path": [], "menu_choices": [], "turns_used": sum(1 for row in read_jsonl_text(transcript)
            if isinstance(row.get("message"), dict) and row["message"].get("role") == "assistant"),
            "tokens": {"in": tin, "out": tout}, "reasoning_tokens": rtokens,
            "reasoning_blocks": blocks, "thinking_levels": levels, "error": error,
            "wallclock_s": round(time.time()-t0, 3), "cost_usd": cost, "retries": 0,
            "invalid_actions": failed_results + (1 if error else 0), "tool_calls": calls,
            "tool_inventory": names, "session_key": session_key}


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--config-id", required=True)
    ap.add_argument("--prompt-id", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--home", required=True); args = ap.parse_args()
    cfg, prompt, out = parse_config(args.config_id), load_prompt(args.prompt_id), Path(args.out).resolve()
    if out.exists():
        if len(out.parts) < 4 or out == Path(out.anchor):
            raise ValueError(f"refusing to reset unsafe output path: {out}")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True); Path(args.home).mkdir(parents=True, exist_ok=True)
    run = asyncio.run(run_constrained(cfg, prompt, out)) if cfg["framework"].startswith("constrained") else run_openclaw(cfg, prompt, out)
    scores = score(prompt, run["actions"], run["invalid_actions"], run["agent_done"], run["error"])
    meta = {"config": args.config_id, "framework": cfg["framework"], "model_key": cfg["model_key"],
            "scorer_version": SCORER_VERSION,
            "model": MODELS[cfg["model_key"]], "prompt_id": prompt["id"], "user_prompt": prompt["prompt"],
            "prompt_style": prompt["style"], "expected_main_action": prompt["expected_main_choice"],
            "expected_main_label": prompt["expected_main_label"],
            "expected_submenu_action": prompt["expected_submenu_choice"],
            "expected_submenu_label": prompt["expected_submenu_label"],
            "expected_final_mock_result": prompt["expected_result"],
            "expected_result_key": prompt["expected_result_key"],
            "acceptable_result_keys": prompt.get("acceptable_result_keys", [prompt["expected_result_key"]]),
            "expected_menu_paths": prompt.get("expected_menu_paths", []), "actions": run["actions"],
            "actual_activity": run["actions"][0]["activity"] if run["actions"] else None,
            "actual_target": run["actions"][0].get("target") if run["actions"] else None,
            "final_mock_world_result": run["actions"], **scores,
            "first_main_menu_choice_correct": run.get("first_main_menu_choice_correct"),
            "reasoning_action_agreement": None, "reasoning_intended_action": None,
            "agent_done": run["agent_done"], "terminal_status": run["agent_done"],
            "turns": run["turns_used"], "turns_used": run["turns_used"],
            "tokens": run["tokens"], "cost_usd": run["cost_usd"], "duration_s": run["wallclock_s"],
            "wallclock_s": run["wallclock_s"], "error": run["error"], "retries": run.get("retries", 0),
            "menu_path": run.get("menu_path", []), "menu_choices": run.get("menu_choices", []),
            "tool_calls": run.get("tool_calls", []), "tool_inventory": run.get("tool_inventory", []),
            "reasoning_blocks": run.get("reasoning_blocks"), "reasoning_tokens": run.get("reasoning_tokens", 0),
            "thinking_levels": run.get("thinking_levels", [])}
    (out / "session_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "agent_stdout.log").write_text("reliability session complete\n", encoding="utf-8")
    print(json.dumps({"config": args.config_id, "prompt": prompt["id"], **scores, "error": run["error"]}))
    return 1 if run["error"] else 0


if __name__ == "__main__": raise SystemExit(main())
