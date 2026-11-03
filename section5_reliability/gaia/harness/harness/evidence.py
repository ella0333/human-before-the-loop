"""Extract compact immutable GAIA reasoning/tool evidence for analysis."""

import json
from pathlib import Path


def _jsonl(path: Path) -> list[dict]:
    rows = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def custom_id(meta: dict) -> str:
    return f"{meta['config']}__{meta.get('task_id') or meta['prompt_id']}"


def extract(meta_path: Path) -> tuple[dict, str]:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    session = meta_path.parent
    reasoning = []
    if meta["framework"] == "constrained":
        for call in _jsonl(session / "raw_calls.jsonl"):
            parsed = call.get("parsed") or {}
            if parsed.get("thinking"):
                reasoning.append({"activity": call.get("activity"),
                                  "thinking": parsed["thinking"],
                                  "choice": parsed.get("choice")})
    else:
        for item in _jsonl(session / "transcript.jsonl"):
            message = item.get("message") if item.get("type") == "message" else None
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            if message.get("reasoning"):
                reasoning.append({"reasoning": message["reasoning"]})
            if message.get("reasoning_content"):
                reasoning.append({"reasoning": message["reasoning_content"]})
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") in {"thinking", "reasoning"}:
                    reasoning.append(block)
    task_id = meta.get("task_id") or meta["prompt_id"]
    row = {"custom_id": custom_id(meta), "config": meta["config"],
           "framework": meta["framework"], "model_key": meta["model_key"],
           "task_id": task_id, "level": meta.get("level"),
           "answer_correct": bool(meta.get("answer_correct")),
           "reasoning_available": bool(reasoning)}
    evidence = {"task_id": task_id, "question": meta.get("question"),
                "reference_answer": meta.get("reference_answer"),
                "model_answer": meta.get("model_answer"),
                "answer_correct": meta.get("answer_correct"),
                "recorded_reasoning": reasoning or None,
                "tool_events": meta.get("tool_events", [])}
    return row, json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))


def collect(run_dir: Path):
    return [(path, *extract(path)) for path in sorted(run_dir.glob("*/*/session_meta.json"))]
