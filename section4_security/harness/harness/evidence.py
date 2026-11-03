from __future__ import annotations

import json
import re
from pathlib import Path

from evalspec import CANARY_BY_ID, CANARY_TOKEN_RE, GOALS

TOKEN_RE = re.compile(CANARY_TOKEN_RE)
GOAL_TEXT = {g["id"]: g["goal"] for g in GOALS}


def custom_id(meta: dict) -> str:
    return f"{meta['config']}__{meta['injection_id']}"


def _jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _text(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _redact(text: str) -> str:
    return TOKEN_RE.sub("[CANARY_TOKEN]", text)


def extract(meta_path: Path) -> tuple[dict, str]:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    session_dir = meta_path.parent
    reasoning: list[str] = []
    actions: list[str] = []
    final_texts: list[str] = []
    tool_calls: list[dict] = []
    tool_results: list[dict] = []
    model_errors = 0
    thinking_levels: list[str] = []
    reasoning_tokens = 0

    if meta["framework"] == "constrained":
        for rec in _jsonl(session_dir / "raw_calls.jsonl"):
            activity = rec.get("activity", "unknown")
            parsed = rec.get("parsed") or {}
            reasoning_tokens += int((rec.get("tokens") or {}).get("reasoning", 0) or 0)
            if rec.get("error"):
                model_errors += 1
            if parsed.get("thinking"):
                reasoning.append(f"[{activity}] {parsed['thinking']}")
            for field in ("choice", "response", "return_to_menu", "done"):
                value = parsed.get(field)
                if value not in (None, "", False):
                    actions.append(f"[{activity}.{field}] {_text(value)}")
            if parsed.get("response"):
                final_texts.append(str(parsed["response"]))
    else:
        for rec in _jsonl(session_dir / "transcript.jsonl"):
            if rec.get("type") == "thinking_level_change" and rec.get("thinkingLevel"):
                thinking_levels.append(str(rec["thinkingLevel"]))
            msg = rec.get("message") if rec.get("type") == "message" else None
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role == "assistant":
                reasoning_tokens += int((msg.get("usage") or {}).get("reasoningTokens", 0) or 0)
            content = msg.get("content")
            if role == "assistant" and (msg.get("reasoning") or msg.get("reasoning_content")):
                reasoning.append(str(msg.get("reasoning") or msg.get("reasoning_content")))
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                kind = str(block.get("type", ""))
                if kind in {"thinking", "reasoning"}:
                    value = block.get("thinking", block.get("reasoning", block.get("text", "")))
                    if value:
                        reasoning.append(str(value))
                elif kind in {"toolCall", "tool_call", "tool_use"}:
                    call = {
                        "id": block.get("id", block.get("toolCallId")),
                        "name": block.get("name", block.get("toolName")),
                        "arguments": block.get("arguments", block.get("input", {})),
                    }
                    tool_calls.append(call)
                    actions.append(f"[tool call] {call['name']} {_text(call['arguments'])}")
                elif kind in {"text", "output_text"} and block.get("text"):
                    if role == "assistant":
                        final_texts.append(str(block["text"]))
            if role == "tool":
                result = {
                    "id": msg.get("toolCallId"),
                    "name": msg.get("toolName"),
                    "is_error": bool(msg.get("isError")),
                    "content": content,
                }
                tool_results.append(result)
                actions.append(
                    f"[tool result {'ERROR' if result['is_error'] else 'OK'}] "
                    f"{result['name']} {_text(content)}")

    outbound = _jsonl(session_dir / "outbound.jsonl")
    egress = _jsonl(session_dir / "egress_attempts.jsonl")
    for event in egress:
        actions.append(f"[blocked network egress] {_text(event)}")
    target_path = (meta.get("canary_path")
                   or CANARY_BY_ID.get(meta.get("canary_id"), {}).get("path", ""))
    target_tool_calls = sum(
        1 for call in tool_calls if target_path and target_path in _text(call.get("arguments", {})))
    tool_errors = sum(1 for result in tool_results if result.get("is_error"))
    available = []
    if reasoning:
        available.append("explicit_reasoning")
    if actions:
        available.append("actions")
    if tool_calls or tool_results:
        available.append("native_tool_trace")
    if final_texts:
        available.append("assistant_text")
    if outbound:
        available.append("outbound_sink")
    if egress:
        available.append("blocked_egress")

    row = {
        "custom_id": custom_id(meta),
        "config": meta["config"],
        "framework": meta["framework"],
        "defense": meta["defense"],
        "model_key": meta["model_key"],
        "injection_id": meta["injection_id"],
        "template_name": meta["template_name"],
        "goal_id": meta["goal_id"],
        "harm_bucket": meta["harm_bucket"],
        "outcome_before_judge": meta["outcome"],
        "evidence_available": "|".join(available),
        "reasoning_records": len(reasoning),
        "thinking_levels": "|".join(thinking_levels),
        "reasoning_tokens": reasoning_tokens,
        "action_records": len(actions),
        "tool_calls": len(tool_calls),
        "tool_results": len(tool_results),
        "tool_successes": len(tool_results) - tool_errors,
        "tool_errors": tool_errors,
        "tool_names": "|".join(sorted({str(c.get('name')) for c in tool_calls if c.get('name')})),
        "target_path_tool_calls": target_tool_calls,
        "outbound_messages": len(outbound),
        "egress_attempts": len(egress),
        "egress_canary_hits": len(meta.get("egress_canary_hits", [])),
        "egress_urls": "|".join(meta.get("egress_urls", [])),
        "canary_hits": len(meta.get("canary_hits", [])),
        "access_hits": len(meta.get("access_hits", [])),
        "file_changes": len(meta.get("hash_deltas", [])),
        "ledger_rows": meta.get("ledger_rows", 0),
        "model_errors": model_errors,
    }

    goal = meta.get("goal_text") or GOAL_TEXT.get(meta.get("goal_id"), "(unknown)")
    sections = [
        f"SESSION: {row['custom_id']}",
        f"FRAMEWORK: {meta['framework']}",
        f"INJECTED INSTRUCTION: {goal}",
        f"EVIDENCE TYPES AVAILABLE: {row['evidence_available'] or 'none'}",
    ]
    if reasoning:
        sections += ["\nEXPLICIT REASONING:", *reasoning]
    if actions:
        sections += ["\nACTIONS AND TOOL RESULTS:", *actions]
    if final_texts:
        sections += ["\nASSISTANT TEXT:", *final_texts]
    if outbound:
        sections += ["\nMOCK CHANNEL OUTBOUND EVENTS:", *[_text(x) for x in outbound]]
    if egress:
        sections += ["\nBLOCKED NETWORK EGRESS EVENTS:", *[_text(x) for x in egress]]
    sections += [
        "\nDETERMINISTIC OBSERVATIONS:",
        json.dumps({
            "attack_succeeded": meta.get("attack_succeeded", False),
            "canary_accessed": meta.get("canary_accessed", False),
            "leaked": meta.get("leaked", False),
            "target_path_tool_calls": target_tool_calls,
            "file_changes": row["file_changes"],
            "ledger_rows": row["ledger_rows"],
            "egress_attempts": row["egress_attempts"],
            "egress_canary_hits": row["egress_canary_hits"],
        }, sort_keys=True),
    ]
    return row, _redact("\n".join(sections))


def collect(run_dir: Path) -> list[tuple[Path, dict, str]]:
    return [(p, *extract(p)) for p in sorted(run_dir.glob("*/*/session_meta.json"))]
