"""Validate immutable GAIA-prompt evidence and build deterministic outputs."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path

from evalspec import (EXPERIMENT_CONTRACT_VERSION, RUNTIME_CONTRACT_VERSION,
                      SCORER_VERSION, all_configs, all_task_ids, session_measurable)
from harness.aggregate import aggregate


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_files(run_dir: Path):
    post = run_dir / "postprocess"
    return [path for path in sorted(run_dir.rglob("*")) if path.is_file()
            and post not in path.parents
            and path.name not in {"summary.csv", "metrics.json", "metrics.md", "run.log"}
            and "figures" not in path.parts]


def snapshot_or_verify(run_dir: Path):
    post = run_dir / "postprocess"
    post.mkdir(exist_ok=True)
    manifest_path = post / "raw_hashes.json"
    current = {path.relative_to(run_dir).as_posix(): _sha(path) for path in raw_files(run_dir)}
    if not manifest_path.exists():
        manifest_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        return {"status": "CREATED", "files": len(current), "changed": [], "missing": []}
    prior = json.loads(manifest_path.read_text(encoding="utf-8"))
    prior.pop("run.log", None)
    changed = sorted(key for key in set(prior) & set(current) if prior[key] != current[key])
    missing = sorted(set(prior) - set(current))
    added = sorted(set(current) - set(prior))
    return {"status": "FAILED" if changed or missing else "VERIFIED",
            "files": len(current), "changed": changed, "missing": missing, "added": added}


def _receipt_valid(session: Path) -> bool:
    try:
        receipt = json.loads((session / "artifact_receipt.json").read_text(encoding="utf-8"))
        items = receipt.get("files") or []
        return receipt.get("file_count") == len(items) and all(
            (session / item["path"]).is_file()
            and (session / item["path"]).stat().st_size == item["bytes"]
            and _sha(session / item["path"]) == item["sha256"] for item in items)
    except Exception:
        return False


def validate_run(run_dir: Path):
    failures, warnings = [], []
    paths = sorted(run_dir.glob("*/*/session_meta.json"))
    declared = None
    log_path = run_dir / "run.log"
    if log_path.is_file():
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "sessions   :" in line:
                try:
                    declared = int(line.rsplit(":", 1)[1].strip())
                except ValueError:
                    pass
    expected = declared if declared is not None else len(all_configs()) * len(all_task_ids())
    if len(paths) != expected:
        failures.append(f"expected {expected} sessions, found {len(paths)}")
    receipts = 0
    for path in paths:
        session = path.parent
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"{path}: invalid metadata: {exc}")
            continue
        required_fields = ["task_id", "question", "reference_answer", "model_answer",
                           "answer_correct", "exact_answer_correct", "full_task_correct",
                           "scoring_status", "terminal_status",
                           "turns", "tokens", "duration_s", "eligibility_profile"]
        for field in required_fields:
            if field not in meta:
                failures.append(f"{path}: missing {field}")
        contracts = {
            "experiment_contract_version": EXPERIMENT_CONTRACT_VERSION,
            "runtime_contract_version": RUNTIME_CONTRACT_VERSION,
            "scorer_version": SCORER_VERSION,
        }
        for field, expected_value in contracts.items():
            if meta.get(field) != expected_value:
                failures.append(
                    f"{path}: {field}={meta.get(field)!r}, expected {expected_value!r}")
        required_files = ["agent_stdout.log", "tool_events.jsonl"]
        required_files += (["raw_calls.jsonl"] if str(meta.get("framework")).startswith("constrained")
                           else ["transcript.jsonl", "openclaw_session.json",
                                 "tool_inventory.json", "outbound.jsonl"])
        for name in required_files:
            if not (session / name).is_file():
                failures.append(f"{session}: missing {name}")
        if str(meta.get("framework")).startswith("constrained") and (session / "raw_calls.jsonl").is_file():
            try:
                errors = [row.get("error") for row in (
                    json.loads(line) for line in (session / "raw_calls.jsonl").read_text(
                        encoding="utf-8").splitlines() if line.strip()) if row.get("error")]
                if errors:
                    failures.append(f"{session}: {len(errors)} raw provider/parse failure(s)")
            except (OSError, AttributeError, json.JSONDecodeError) as exc:
                failures.append(f"{session}: invalid raw_calls.jsonl: {exc}")
        if not session_measurable(meta):
            failures.append(f"{session}: incomplete/error: {meta.get('error') or meta.get('agent_done')}")
        if _receipt_valid(session):
            receipts += 1
        else:
            failures.append(f"{session}: invalid or missing transfer receipt")
        if meta.get("framework") == "openclaw" and not meta.get("reasoning_blocks"):
            warnings.append(f"{meta.get('config')}__{meta.get('task_id')}: native reasoning unavailable")
    return {"ok": not failures, "expected_sessions": expected, "found_sessions": len(paths),
            "artifact_receipts": receipts, "failures": failures, "warnings": warnings}


def svg_bars(path: Path, title: str, labels: list[str], values: list[float], color: str):
    width, height, left, top, chart_height = 900, 480, 90, 55, 340
    bar_width, gap = max(24, 650 // max(1, len(labels))), 18
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
             '<rect width="100%" height="100%" fill="white"/>',
             f'<text x="{width/2}" y="28" text-anchor="middle" font-family="sans-serif" font-size="20">{html.escape(title)}</text>',
             f'<line x1="{left}" y1="{top+chart_height}" x2="850" y2="{top+chart_height}" stroke="#333"/>']
    for index, (label, value) in enumerate(zip(labels, values)):
        x = left + 25 + index * (bar_width + gap)
        bar_height = chart_height * max(0, min(1, value))
        y = top + chart_height - bar_height
        parts += [f'<rect x="{x}" y="{y}" width="{bar_width}" height="{bar_height}" fill="{color}"/>',
                  f'<text x="{x+bar_width/2}" y="{y-6}" text-anchor="middle" font-family="sans-serif" font-size="12">{value:.1%}</text>',
                  f'<text x="{x+bar_width/2}" y="{top+chart_height+18}" text-anchor="middle" font-family="sans-serif" font-size="10" transform="rotate(25 {x+bar_width/2} {top+chart_height+18})">{html.escape(label)}</text>']
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def make_figures(run_dir: Path, metrics: dict):
    figures = run_dir / "figures"
    figures.mkdir(exist_ok=True)
    cells = list(metrics["by_cell"])
    svg_bars(figures / "answer_accuracy.svg", "GAIA-prompt task completion", cells,
             [metrics["by_cell"][cell]["accuracy"] or 0 for cell in cells], "#059669")
    svg_bars(figures / "answer_rate.svg", "Answer completion rate", cells,
             [metrics["by_cell"][cell]["answer_rate"] for cell in cells], "#2563eb")
    category_keys = list(metrics["by_category"])
    svg_bars(figures / "accuracy_by_category.svg", "Completion by task category", category_keys,
             [metrics["by_category"][key]["accuracy"] or 0 for key in category_keys], "#7c3aed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    checks = validate_run(run_dir)
    integrity = snapshot_or_verify(run_dir)
    post = run_dir / "postprocess"
    post.mkdir(exist_ok=True)
    (post / "log_validation.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    print(f"log check: {'PASS' if checks['ok'] else 'FAIL'} "
          f"({checks['found_sessions']} sessions, {checks['artifact_receipts']} receipts)")
    print(f"raw hashes: {integrity['status']} ({integrity['files']} files)")
    if not checks["ok"] or integrity["status"] == "FAILED":
        return 1
    metrics = aggregate(run_dir)
    if not args.check_only:
        make_figures(run_dir, metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
