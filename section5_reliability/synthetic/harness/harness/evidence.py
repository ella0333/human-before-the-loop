from __future__ import annotations

import json
from pathlib import Path


def _jsonl(path: Path) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try: rows.append(json.loads(line))
            except json.JSONDecodeError: pass
    return rows


def custom_id(meta: dict) -> str: return f"{meta['config']}__{meta['prompt_id']}"


def extract(meta_path: Path) -> tuple[dict, str]:
    meta = json.loads(meta_path.read_text(encoding="utf-8")); session = meta_path.parent
    reasoning = []
    if meta["framework"] == "constrained":
        for call in _jsonl(session / "raw_calls.jsonl"):
            parsed = call.get("parsed") or {}
            if parsed.get("thinking"):
                reasoning.append({"activity": call.get("activity"), "thinking": parsed["thinking"],
                                  "choice": parsed.get("choice")})
    else:
        for row in _jsonl(session / "transcript.jsonl"):
            msg = row.get("message") if row.get("type") == "message" else None
            if not isinstance(msg, dict) or msg.get("role") != "assistant": continue
            if msg.get("reasoning"): reasoning.append({"reasoning": msg["reasoning"]})
            if msg.get("reasoning_content"): reasoning.append({"reasoning": msg["reasoning_content"]})
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") in {"thinking", "reasoning"}:
                    reasoning.append(block)
    row = {"custom_id": custom_id(meta), "config": meta["config"], "framework": meta["framework"],
           "model_key": meta["model_key"], "prompt_id": meta["prompt_id"],
           "expected_main_action": meta["expected_main_action"],
           "expected_submenu_action": meta.get("expected_submenu_action"),
           "expected_menu_paths": meta.get("expected_menu_paths", []),
           "first_action_correct": meta["first_action_correct"],
           "full_task_correct": meta["full_task_correct"], "reasoning_available": bool(reasoning)}
    evidence = {"prompt_id": meta["prompt_id"], "user_prompt": meta["user_prompt"],
                "correct_action": {"main": meta["expected_main_action"],
                                   "submenu": meta.get("expected_submenu_action"),
                                   "menu_paths": meta.get("expected_menu_paths", []),
                                   "canonical_result": meta.get("expected_result_key"),
                                   "acceptable_canonical_results": meta.get(
                                       "acceptable_result_keys", [meta.get("expected_result_key")]),
                                   "result": meta["expected_final_mock_result"]},
                "recorded_reasoning": reasoning if reasoning else None,
                "recorded_menu_path": meta.get("menu_path", []),
                "executed_actions": meta.get("actions", []),
                "deterministic_scores": {k: meta.get(k) for k in
                  ("first_action_correct", "full_task_correct", "wrong_action_count",
                   "invalid_action_count", "unnecessary_action_count")}}
    return row, json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))


def collect(run_dir: Path):
    return [(path, *extract(path)) for path in sorted(run_dir.glob("*/*/session_meta.json"))]
