"""Run and deterministically score one prompt-only GAIA session."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import time
import urllib.request
from pathlib import Path

from evalspec import (BUDGET, EXPERIMENT_CONTRACT_VERSION, MOCK_DISCORD_PORTS,
                      MODELS, OC_MODEL_SLUG, OC_TOOLS_ALLOW,
                      PROFILE_VERSION, RUNTIME_CONTRACT_VERSION, SCORER_VERSION,
                      session_cost)
from gaia_dataset import load_task
from gaia_scorer import score_task

REPO = Path(__file__).resolve().parent
OPENROUTER_KEY_FILE = Path("/run/reliability-eval/openrouter.key")
ACTIVITY_EVENTS = Path("/run/benchmark-tools/events.jsonl")
OUTBOX = Path("/run/mock-discord/outbox.jsonl")


def _key(name: str, path: Path) -> str:
    value = os.environ.get(name, "").strip()
    if not value and path.exists():
        value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"{name} is unavailable")
    return value


def parse_config(config_id: str) -> dict:
    framework, model_key = config_id.rsplit("__", 1)
    if framework not in {"constrained", "openclaw"} or model_key not in MODELS:
        raise SystemExit(f"unknown config: {config_id}")
    return {"framework": framework, "model_key": model_key}


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def read_jsonl_text(text: str):
    for line in text.splitlines():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


async def run_constrained(cfg: dict, task: dict, out: Path) -> dict:
    from evalagent.agent import RunConfig, run_session

    raw = (out / "raw_calls.jsonl").open("w", encoding="utf-8")
    events_file = (out / "tool_events.jsonl").open("w", encoding="utf-8")

    def log_call(row):
        raw.write(json.dumps({"ts": time.time(), **row}, ensure_ascii=False) + "\n")
        raw.flush()

    def log_event(row):
        events_file.write(json.dumps({"ts": time.time(), **row}, ensure_ascii=False) + "\n")
        events_file.flush()

    rc = RunConfig(
        user_prompt=task["question"], model=MODELS[cfg["model_key"]],
        api_key=_key("OPENROUTER_API_KEY", OPENROUTER_KEY_FILE),
        max_calls=BUDGET["max_model_calls"],
        max_menu_turns=BUDGET["max_menu_turns"],
        tavily_api_key=_key("TAVILY_API_KEY", Path("/run/reliability-eval/tavily.key")),
        tavily_timeout_seconds=BUDGET["tavily_timeout_seconds"],
        tavily_max_results=BUDGET["tavily_max_results"],
        log_call=log_call, log_event=log_event,
    )
    started = time.time()
    result = await run_session(rc)
    raw.close()
    events_file.close()
    call_rows = read_jsonl(out / "raw_calls.jsonl")
    retries = sum(max(0, len(row.get("request_attempts") or []) - 1) for row in call_rows)
    recovery_escalations = sum(
        1 for row in call_rows for attempt in (row.get("request_attempts") or [])
        if int(attempt.get("max_tokens", 0) or 0) > 3000)
    return {
        "agent_done": result.agent_done, "model_answer": result.final_answer,
        "final_thinking": result.final_thinking, "tool_events": result.events,
        "turns_used": result.calls_made,
        "tokens": {"in": result.tokens_in, "out": result.tokens_out},
        "reasoning_tokens": result.native_reasoning_tokens, "error": result.error,
        "wallclock_s": round(time.time() - started, 3),
        "cost_usd": session_cost(cfg["model_key"], result.tokens_in, result.tokens_out),
        "invalid_actions": 1 if result.error else 0, "retries": retries,
        "recovery_escalations": recovery_escalations,
    }


def _openclaw_session_entry(session_key: str) -> tuple[dict, Path | None]:
    for store_path in Path("/root/.openclaw/agents").glob("*/sessions/sessions.json"):
        try:
            store = json.loads(store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entry = store.get(session_key)
        if isinstance(entry, dict):
            raw = entry.get("sessionFile")
            path = Path(raw) if raw else None
            if path and not path.is_absolute():
                path = store_path.parent / path
            return entry, path
    return {}, None


def transcript_data(text: str) -> tuple[list[dict], list[str], int, int, int, int]:
    calls, levels, reasoning_blocks, reasoning_tokens, tokens_in, tokens_out = [], [], 0, 0, 0, 0
    for row in read_jsonl_text(text):
        if row.get("type") == "thinking_level_change" and row.get("thinkingLevel"):
            levels.append(str(row["thinkingLevel"]))
        message = row.get("message") if row.get("type") == "message" else None
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        usage = message.get("usage") or {}
        tokens_in += int(usage.get("input", 0) or 0)
        tokens_out += int(usage.get("output", 0) or 0)
        reasoning_tokens += int(usage.get("reasoningTokens", 0) or 0)
        if message.get("reasoning") or message.get("reasoning_content"):
            reasoning_blocks += 1
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"thinking", "reasoning"}:
                reasoning_blocks += 1
            if block.get("type") in {"toolCall", "tool_call"}:
                calls.append({"id": block.get("id"),
                              "name": block.get("name") or block.get("toolName"),
                              "arguments": block.get("arguments") or block.get("input") or {}})
    return calls, levels, reasoning_blocks, reasoning_tokens, tokens_in, tokens_out


def assistant_provider_errors(messages: list[dict]) -> list[str]:
    errors = []
    for message in messages:
        if message.get("stopReason") == "error" or message.get("errorMessage"):
            errors.append(json.dumps({"message": message.get("errorMessage"),
                                      "code": message.get("errorCode"),
                                      "body": message.get("errorBody")}, ensure_ascii=False))
    return errors


def run_openclaw(cfg: dict, task: dict, out: Path) -> dict:
    for path in (ACTIVITY_EVENTS, OUTBOX):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    started = time.time()
    conversation = f"gaia-{cfg['model_key']}-{time.time_ns()}"
    prompt = ("Complete this task using reasoning and, when useful, the provided public-web or "
              "calculator tools. Your final reply must contain only the concise requested answer.\n\n"
              + task["question"])
    payload = json.dumps({"id": f"eval-{time.time_ns()}", "conversationId": conversation,
                          "sender": "user", "text": prompt}).encode()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{MOCK_DISCORD_PORTS[cfg['model_key']]}/inbound",
            data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=BUDGET["timeout_seconds"] + 60) as response:
            ingress = json.loads(response.read().decode())
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        for name, value in (("transcript.jsonl", ""), ("openclaw_session.json", "{}"),
                            ("tool_inventory.json", "[]"), ("outbound.jsonl", ""),
                            ("tool_events.jsonl", "")):
            (out / name).write_text(value, encoding="utf-8")
        return {"agent_done": "error", "model_answer": "", "tool_events": [],
                "turns_used": 0, "tokens": {"in": 0, "out": 0},
                "reasoning_tokens": 0, "error": error,
                "wallclock_s": round(time.time() - started, 3), "cost_usd": None,
                "invalid_actions": 1, "tool_calls": []}

    session_key = ingress.get("sessionKey", "")
    entry, transcript_path = _openclaw_session_entry(session_key)
    transcript = transcript_path.read_text(encoding="utf-8", errors="replace") if transcript_path and transcript_path.exists() else ""
    (out / "transcript.jsonl").write_text(transcript, encoding="utf-8")
    (out / "openclaw_session.json").write_text(
        json.dumps({"session_key": session_key, "entry": entry}, indent=2), encoding="utf-8")
    outbox_rows, tool_rows = read_jsonl(OUTBOX), read_jsonl(ACTIVITY_EVENTS)
    (out / "outbound.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in outbox_rows), encoding="utf-8")
    (out / "tool_events.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in tool_rows), encoding="utf-8")
    model_answer = str(outbox_rows[-1].get("text") or "").strip() if outbox_rows else ""
    calls, levels, blocks, reasoning_tokens, tokens_in, tokens_out = transcript_data(transcript)
    inventory = (((entry.get("systemPromptReport") or {}).get("tools") or {}).get("entries") or [])
    (out / "tool_inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    names = [item.get("name") for item in inventory if isinstance(item, dict)]
    assistant_messages = [row["message"] for row in read_jsonl_text(transcript)
                          if isinstance(row.get("message"), dict)
                          and row["message"].get("role") == "assistant"]
    actual_models = {message.get("model") for message in assistant_messages}
    errors = [f"provider error: {detail}" for detail in assistant_provider_errors(assistant_messages)]
    if not transcript:
        errors.append("OpenClaw transcript not found")
    if entry.get("status") != "done" or entry.get("abortedLastRun") is True:
        errors.append(f"unclean session status={entry.get('status')!r}")
    expected_model = OC_MODEL_SLUG[cfg["model_key"]].removeprefix("openrouter/")
    if actual_models != {expected_model}:
        errors.append(f"model mismatch {sorted(actual_models)!r}")
    if set(names) != set(OC_TOOLS_ALLOW):
        errors.append(f"tool inventory mismatch {sorted(names)!r}")
    if "low" not in levels:
        errors.append(f"thinking level mismatch {levels!r}")
    error = "; ".join(errors) or None
    failed_results = sum(1 for row in read_jsonl_text(transcript)
                         if isinstance(row.get("message"), dict)
                         and row["message"].get("role") == "tool"
                         and row["message"].get("isError") is True)
    cost = entry.get("estimatedCostUsd")
    if cost is None and (tokens_in or tokens_out):
        cost = session_cost(cfg["model_key"], tokens_in, tokens_out)
    return {"agent_done": "completed" if not error else "error", "model_answer": model_answer,
            "tool_events": tool_rows, "turns_used": len(assistant_messages),
            "tokens": {"in": tokens_in, "out": tokens_out},
            "reasoning_tokens": reasoning_tokens, "reasoning_blocks": blocks,
            "thinking_levels": levels, "error": error,
            "wallclock_s": round(time.time() - started, 3), "cost_usd": cost,
            "retries": 0, "invalid_actions": failed_results + (1 if error else 0),
            "tool_calls": calls, "tool_inventory": names, "session_key": session_key}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-id", required=True)
    parser.add_argument("--task-id", "--prompt-id", dest="task_id", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--home", required=True)
    args = parser.parse_args()
    cfg, task, out = parse_config(args.config_id), load_task(args.task_id), Path(args.out).resolve()
    if out.exists():
        if len(out.parts) < 4 or out == Path(out.anchor):
            raise ValueError(f"refusing to reset unsafe output path: {out}")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    Path(args.home).mkdir(parents=True, exist_ok=True)
    run = asyncio.run(run_constrained(cfg, task, out)) if cfg["framework"] == "constrained" else run_openclaw(cfg, task, out)
    score = score_task(task, run.get("tool_events", []), run["model_answer"])
    meta = {
        "config": args.config_id, "framework": cfg["framework"],
        "model_key": cfg["model_key"], "model": MODELS[cfg["model_key"]],
        "experiment_contract_version": EXPERIMENT_CONTRACT_VERSION,
        "runtime_contract_version": RUNTIME_CONTRACT_VERSION,
        "scorer_version": SCORER_VERSION, "eligibility_profile": PROFILE_VERSION,
        "task_id": task["task_id"], "prompt_id": task["task_id"],
        "suite": task["suite"], "category": task["category"], "level": task["level"],
        "annotated_tools": task["annotated_tools"], "question": task["question"],
        "user_prompt": task["question"], "reference_answer": score["reference_answer"],
        "model_answer": run["model_answer"], "answer_correct": score["answer_correct"],
        "exact_answer_correct": score["answer_correct"],
        "actions_correct": score["actions_correct"],
        "full_task_correct": None,
        "deterministic_full_task_correct": score["full_task_correct"],
        "completion_rate_value": None,
        "scoring_status": "awaiting_semantic_judge",
        "expected_actions": [], "actual_actions": [], "expected_action_count": 0,
        "tool_events": run.get("tool_events", []), "tool_call_count": len(run.get("tool_events", [])),
        "agent_done": run["agent_done"], "terminal_status": run["agent_done"],
        "turns": run["turns_used"], "turns_used": run["turns_used"],
        "tokens": run["tokens"], "cost_usd": run["cost_usd"],
        "duration_s": run["wallclock_s"], "wallclock_s": run["wallclock_s"],
        "error": run["error"], "retries": run.get("retries", 0),
        "recovery_escalations": run.get("recovery_escalations", 0),
        "invalid_action_count": run.get("invalid_actions", 0),
        "tool_calls": run.get("tool_calls", []), "tool_inventory": run.get("tool_inventory", []),
        "final_thinking": run.get("final_thinking"),
        "reasoning_blocks": run.get("reasoning_blocks"),
        "reasoning_tokens": run.get("reasoning_tokens", 0),
        "thinking_levels": run.get("thinking_levels", []),
    }
    (out / "session_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "agent_stdout.log").write_text("GAIA prompt session complete\n", encoding="utf-8")
    print(json.dumps({"config": args.config_id, "task_id": task["task_id"],
                      "exact_answer_correct": score["answer_correct"],
                      "scoring_status": "awaiting_semantic_judge", "error": run["error"]}))
    return 1 if run["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
