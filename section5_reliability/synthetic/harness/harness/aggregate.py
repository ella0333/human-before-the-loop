from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from evalspec import session_measurable


def _metas(run_dir: Path) -> list[dict]:
    rows = []
    judgments = {}
    jp = run_dir / "postprocess" / "judgments.jsonl"
    if jp.exists():
        for line in jp.read_text(encoding="utf-8").splitlines():
            if line.strip():
                j = json.loads(line); judgments[j["custom_id"]] = j
    for path in sorted(run_dir.glob("*/*/session_meta.json")):
        m = json.loads(path.read_text(encoding="utf-8")); cid = f"{m['config']}__{m['prompt_id']}"
        if cid in judgments: m = {**m, **judgments[cid]}
        raw_path = path.parent / "raw_calls.jsonl"
        try:
            m["_raw_calls_clean"] = (not raw_path.is_file() or not any(
                json.loads(line).get("error") for line in raw_path.read_text(
                    encoding="utf-8").splitlines() if line.strip()))
        except (OSError, AttributeError, json.JSONDecodeError):
            m["_raw_calls_clean"] = False
        rows.append(m)
    return rows


def _row(m: dict) -> dict:
    t = m.get("tokens") or {}
    return {"config": m["config"], "framework": m["framework"], "model": m["model_key"],
            "prompt_id": m["prompt_id"], "prompt_style": m["prompt_style"],
            "menu_branch": m["expected_main_action"], "expected_submenu": m.get("expected_submenu_action"),
            "first_action_correct": bool(m.get("first_action_correct")),
            "full_task_correct": bool(m.get("full_task_correct")),
            "requested_result_achieved": bool(m.get("requested_result_achieved")),
            "reasoning_action_agreement": m.get("reasoning_action_agreement"),
            "reasoning_correct": m.get("reasoning_correct"),
            "wrong_actions": int(m.get("wrong_action_count", 0)),
            "invalid_actions": int(m.get("invalid_action_count", 0)),
            "unnecessary_actions": int(m.get("unnecessary_action_count", 0)),
            "completed": bool(m.get("completion_rate_value")), "terminal_status": m.get("terminal_status"),
            "turns": m.get("turns", 0), "tokens_in": t.get("in", 0), "tokens_out": t.get("out", 0),
            "cost_usd": m.get("cost_usd") or 0, "duration_s": m.get("duration_s") or 0,
            "error": m.get("error") or ""}


def _stats(rows: list[dict]) -> dict:
    n = len(rows) or 1
    rate = lambda key: sum(bool(r.get(key)) for r in rows) / n
    mean = lambda key: sum(float(r.get(key) or 0) for r in rows) / n
    return {"sessions": len(rows), "first_action_accuracy": rate("first_action_correct"),
            "full_task_accuracy": rate("full_task_correct"), "completion_rate": rate("completed"),
            "reasoning_action_agreement": (sum(r.get("reasoning_action_agreement") is True for r in rows) /
              max(1, sum(r.get("reasoning_action_agreement") is not None for r in rows))),
            "wrong_actions": sum(r["wrong_actions"] for r in rows),
            "invalid_actions": sum(r["invalid_actions"] for r in rows),
            "unnecessary_actions": sum(r["unnecessary_actions"] for r in rows),
            "mean_turns": mean("turns"), "mean_tokens": mean("tokens_in") + mean("tokens_out"),
            "total_cost_usd": sum(float(r["cost_usd"]) for r in rows), "mean_duration_s": mean("duration_s")}


def aggregate(run_dir: Path) -> dict:
    metas = _metas(run_dir)
    all_rows = [_row(m) for m in metas]
    rows = [_row(m) for m in metas
            if session_measurable(m) and m.get("_raw_calls_clean", True)]
    if all_rows:
        with (run_dir / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(all_rows[0])); writer.writeheader(); writer.writerows(all_rows)
    groups = defaultdict(list)
    for r in rows: groups[f"{r['framework']}__{r['model']}"] .append(r)
    by_cell = {k: _stats(v) for k, v in sorted(groups.items())}
    by_style_groups, by_branch_groups = defaultdict(list), defaultdict(list)
    for r in rows:
        by_style_groups[f"{r['framework']}__{r['model']}__{r['prompt_style']}"] .append(r)
        by_branch_groups[f"{r['framework']}__{r['model']}__{r['menu_branch']}"] .append(r)
    metrics = {"sessions": len(all_rows), "completed_sessions": len(rows),
               "failed_sessions": len(all_rows) - len(rows),
               "overall": _stats(rows), "by_cell": by_cell,
               "by_prompt_style": {k: _stats(v) for k, v in sorted(by_style_groups.items())},
               "by_menu_branch": {k: _stats(v) for k, v in sorted(by_branch_groups.items())}}
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    lines = ["# Reliability evaluation metrics", "", f"Sessions: {len(all_rows)}",
             f"Measurable sessions: {len(rows)}", "",
             "| Cell | First action | Full task | Completion | Wrong | Invalid | Unnecessary | Cost |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for key, s in by_cell.items():
        lines.append(f"| {key} | {s['first_action_accuracy']:.1%} | {s['full_task_accuracy']:.1%} | {s['completion_rate']:.1%} | {s['wrong_actions']} | {s['invalid_actions']} | {s['unnecessary_actions']} | ${s['total_cost_usd']:.4f} |")
    lines += ["", "## By prompt style", "",
              "| Cell and style | First action | Full task | Completion |", "|---|---:|---:|---:|"]
    for key, s in metrics["by_prompt_style"].items():
        lines.append(f"| {key} | {s['first_action_accuracy']:.1%} | {s['full_task_accuracy']:.1%} | {s['completion_rate']:.1%} |")
    lines += ["", "## By menu branch", "",
              "| Cell and branch | First action | Full task | Completion |", "|---|---:|---:|---:|"]
    for key, s in metrics["by_menu_branch"].items():
        lines.append(f"| {key} | {s['first_action_accuracy']:.1%} | {s['full_task_accuracy']:.1%} | {s['completion_rate']:.1%} |")
    (run_dir / "metrics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return metrics
