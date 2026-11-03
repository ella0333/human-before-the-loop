"""Combine legacy and GAIA-prompt outcomes without erasing suite boundaries."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from evalspec import SCORER_VERSION


def _rows(run_dir: Path, suite: str) -> list[dict]:
    rows = []
    judgments = {}
    judgment_path = run_dir / "postprocess" / "completion_judgments.jsonl"
    if suite == "gaia_prompt_subset":
        if not judgment_path.is_file():
            raise ValueError(f"semantic judgments missing: {judgment_path}")
        judgments = {row["custom_id"]: row for row in (
            json.loads(line) for line in judgment_path.read_text(encoding="utf-8").splitlines()
            if line.strip()) if row.get("scorer_version") == SCORER_VERSION}
    for path in sorted(run_dir.glob("*/*/session_meta.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        cid = f"{meta.get('config')}__{meta.get('task_id') or meta.get('prompt_id')}"
        if suite == "gaia_prompt_subset" and cid not in judgments:
            raise ValueError(f"semantic judgment missing for {cid}")
        task_completed = (bool(judgments[cid]["task_completed"])
                          if suite == "gaia_prompt_subset"
                          else bool(meta.get("full_task_correct")))
        rows.append({
            "suite": suite,
            "source_run": str(run_dir),
            "config": meta.get("config", ""),
            "framework": meta.get("framework", ""),
            "model": meta.get("model_key", ""),
            "task_id": meta.get("task_id") or meta.get("prompt_id", ""),
            "category": meta.get("category") or meta.get("prompt_style", ""),
            "task_completed": task_completed,
            "measurable": meta.get("agent_done") in {"completed", "could_not_complete", "capped"}
                          and not bool(meta.get("error")),
            "error": meta.get("error") or "",
        })
    if not rows:
        raise ValueError(f"no session metadata found under {run_dir}")
    return rows


def _stats(rows: list[dict]) -> dict:
    measurable = [row for row in rows if row["measurable"]]
    return {
        "sessions": len(rows),
        "measurable_sessions": len(measurable),
        "failed_sessions": len(rows) - len(measurable),
        "completion_accuracy": (sum(row["task_completed"] for row in measurable)
                                / len(measurable) if measurable else None),
    }


def combine(legacy_run: Path, gaia_run: Path, output: Path) -> dict:
    rows = (_rows(legacy_run.resolve(), "synthetic_legacy")
            + _rows(gaia_run.resolve(), "gaia_prompt_subset"))
    output.mkdir(parents=True, exist_ok=True)
    with (output / "combined_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[f"{row['suite']}__{row['config']}"].append(row)
    metrics = {
        "note": ("Suites use different task distributions. Per-suite results are primary; "
                 "pooled accuracy is session-weighted and descriptive only."),
        "overall_descriptive": _stats(rows),
        "by_suite": {suite: _stats([row for row in rows if row["suite"] == suite])
                     for suite in ("synthetic_legacy", "gaia_prompt_subset")},
        "by_suite_and_cell": {key: _stats(value) for key, value in sorted(groups.items())},
    }
    (output / "combined_metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8")
    lines = ["# Combined reliability report", "", metrics["note"], "",
             "| Suite | Sessions | Measurable | Completion accuracy |",
             "|---|---:|---:|---:|"]
    for suite, stats in metrics["by_suite"].items():
        accuracy = "n/a" if stats["completion_accuracy"] is None else f"{stats['completion_accuracy']:.1%}"
        lines.append(f"| {suite} | {stats['sessions']} | {stats['measurable_sessions']} | {accuracy} |")
    lines += ["", "## By suite and framework/model", "",
              "| Suite and cell | Sessions | Measurable | Completion accuracy |",
              "|---|---:|---:|---:|"]
    for key, stats in metrics["by_suite_and_cell"].items():
        accuracy = "n/a" if stats["completion_accuracy"] is None else f"{stats['completion_accuracy']:.1%}"
        lines.append(f"| {key} | {stats['sessions']} | {stats['measurable_sessions']} | {accuracy} |")
    pooled = metrics["overall_descriptive"]
    pooled_accuracy = ("n/a" if pooled["completion_accuracy"] is None
                       else f"{pooled['completion_accuracy']:.1%}")
    lines += ["", f"Descriptive pooled accuracy: {pooled_accuracy} across "
              f"{pooled['measurable_sessions']} measurable sessions."]
    (output / "combined_metrics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("legacy_run", type=Path)
    parser.add_argument("gaia_run", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    output = args.out or (args.gaia_run / f"combined_with_{args.legacy_run.name}")
    metrics = combine(args.legacy_run, args.gaia_run, output.resolve())
    print(f"wrote combined report to {output.resolve()}")
    print(json.dumps(metrics["by_suite"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
