"""Aggregate blinded semantic completion outcomes plus exact-match diagnostics."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from evalspec import SCORER_VERSION, session_measurable


def _metas(run_dir: Path) -> list[dict]:
    rows = []
    for path in sorted(run_dir.glob("*/*/session_meta.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        raw = path.parent / "raw_calls.jsonl"
        try:
            meta["_raw_calls_clean"] = (not raw.is_file() or not any(
                json.loads(line).get("error") for line in raw.read_text(
                    encoding="utf-8").splitlines() if line.strip()))
        except (OSError, AttributeError, json.JSONDecodeError):
            meta["_raw_calls_clean"] = False
        rows.append(meta)
    return rows


def _judgments(run_dir: Path) -> dict[str, dict]:
    path = run_dir / "postprocess" / "completion_judgments.jsonl"
    if not path.is_file():
        return {}
    return {row["custom_id"]: row for row in (
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()) if row.get("scorer_version") == SCORER_VERSION}


def _row(meta: dict, judgment: dict | None = None) -> dict:
    tokens = meta.get("tokens") or {}
    judged = judgment is not None
    completed = (bool(judgment.get("task_completed")) if judgment else None)
    return {
        "config": meta["config"], "framework": meta["framework"],
        "model": meta["model_key"], "task_id": meta.get("task_id") or meta.get("prompt_id"),
        "category": meta.get("category"),
        "task_completed": completed, "judged": judged,
        "judge_confidence": judgment.get("confidence", "") if judgment else "",
        "judge_reason": judgment.get("reason", "") if judgment else "",
        "exact_answer_correct": bool(meta.get("exact_answer_correct",
                                               meta.get("answer_correct"))),
        "answered": bool(meta.get("model_answer")), "model_answer": meta.get("model_answer", ""),
        "terminal_status": meta.get("terminal_status"),
        "tool_calls": meta.get("tool_call_count", len(meta.get("tool_events") or [])),
        "turns": meta.get("turns", 0), "tokens_in": tokens.get("in", 0),
        "tokens_out": tokens.get("out", 0), "cost_usd": meta.get("cost_usd") or 0,
        "duration_s": meta.get("duration_s") or 0, "error": meta.get("error") or "",
    }


def _stats(rows: list[dict]) -> dict:
    judged = [row for row in rows if row["judged"]]
    denominator = len(rows) or 1
    mean = lambda key: sum(float(row.get(key) or 0) for row in rows) / denominator
    return {
        "sessions": len(rows), "judged_sessions": len(judged),
        "accuracy": (sum(bool(row["task_completed"]) for row in judged) / len(judged)
                     if judged else None),
        "exact_match_rate": (sum(row["exact_answer_correct"] for row in rows) / len(rows)
                             if rows else None),
        "answer_rate": sum(row["answered"] for row in rows) / denominator,
        "mean_tool_calls": mean("tool_calls"), "mean_turns": mean("turns"),
        "mean_tokens": mean("tokens_in") + mean("tokens_out"),
        "total_cost_usd": sum(float(row["cost_usd"]) for row in rows),
        "mean_duration_s": mean("duration_s"),
    }


def aggregate(run_dir: Path) -> dict:
    metas = _metas(run_dir)
    judgments = _judgments(run_dir)
    identify = lambda meta: f"{meta['config']}__{meta.get('task_id') or meta.get('prompt_id')}"
    all_rows = [_row(meta, judgments.get(identify(meta))) for meta in metas]
    measured = [_row(meta, judgments.get(identify(meta))) for meta in metas
                if session_measurable(meta) and meta.get("_raw_calls_clean", True)]
    if all_rows:
        with (run_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(all_rows[0]))
            writer.writeheader()
            writer.writerows(all_rows)
    by_cell_groups, by_category_groups = defaultdict(list), defaultdict(list)
    for row in measured:
        by_cell_groups[f"{row['framework']}__{row['model']}"] .append(row)
        by_category_groups[f"{row['framework']}__{row['model']}__{row['category']}"] .append(row)
    by_cell = {key: _stats(value) for key, value in sorted(by_cell_groups.items())}
    by_category = {key: _stats(value) for key, value in sorted(by_category_groups.items())}
    metrics = {
        "sessions": len(all_rows), "measurable_sessions": len(measured),
        "judged_sessions": sum(row["judged"] for row in measured),
        "failed_sessions": len(all_rows) - len(measured), "overall": _stats(measured),
        "judge_cost_usd": sum(float(row.get("judge_cost_usd") or 0)
                              for row in judgments.values()),
        "by_cell": by_cell, "by_category": by_category,
    }
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    lines = ["# GAIA-prompt task-completion reliability metrics", "", f"Sessions: {len(all_rows)}",
             f"Measurable sessions: {len(measured)}", "",
             f"Semantically judged: {sum(row['judged'] for row in measured)}", "",
             "| Cell | Judge completion | Exact diagnostic | Answered | Tool calls | Mean turns | Session cost |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for key, stats in by_cell.items():
        accuracy = "n/a" if stats["accuracy"] is None else f"{stats['accuracy']:.1%}"
        exact = "n/a" if stats["exact_match_rate"] is None else f"{stats['exact_match_rate']:.1%}"
        lines.append(f"| {key} | {accuracy} | {exact} | {stats['answer_rate']:.1%} | "
                     f"{stats['mean_tool_calls']:.2f} | {stats['mean_turns']:.2f} | "
                     f"${stats['total_cost_usd']:.4f} |")
    lines += ["", "## By task category", "",
              "| Cell and category | Completion | Answered | Tool calls |", "|---|---:|---:|---:|"]
    for key, stats in by_category.items():
        accuracy = "n/a" if stats["accuracy"] is None else f"{stats['accuracy']:.1%}"
        lines.append(f"| {key} | {accuracy} | {stats['answer_rate']:.1%} | "
                     f"{stats['mean_tool_calls']:.2f} |")
    lines += ["", f"Judge cost: ${metrics['judge_cost_usd']:.4f}",
              "Exact matching is a diagnostic only; judge completion is the primary outcome."]
    (run_dir / "metrics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return metrics
