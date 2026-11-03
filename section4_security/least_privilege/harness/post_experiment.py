from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from evalspec import MODELS, session_measurable
from harness.aggregate import aggregate
from harness.evidence import collect, extract

DERIVED_ROOTS = {"postprocess", "figures"}
DERIVED_TOP = {
    "summary.csv", "metrics.json", "metrics.md", "asr_by_config.csv",
    "asr_by_template.csv", "asr_by_harm_bucket.csv", "post_experiment_report.md",
    "run.log",
}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def raw_files(run_dir: Path) -> list[Path]:
    files = []
    for path in run_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(run_dir)
        if rel.parts[0] in DERIVED_ROOTS or rel.as_posix() in DERIVED_TOP:
            continue
        files.append(path)
    return sorted(files)


def snapshot_or_verify(run_dir: Path) -> dict:
    post = run_dir / "postprocess"
    post.mkdir(parents=True, exist_ok=True)
    manifest = post / "raw_integrity.sha256"
    current = {p.relative_to(run_dir).as_posix(): _sha(p) for p in raw_files(run_dir)}
    if not manifest.exists():
        manifest.write_text("".join(f"{digest}  {name}\n" for name, digest in current.items()),
                            encoding="utf-8")
        return {"status": "snapshotted", "files": len(current), "changed": [], "missing": []}
    expected = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        expected[name] = digest
    changed = sorted(name for name in expected if name in current and expected[name] != current[name])
    missing = sorted(set(expected) - set(current))
    added = sorted(set(current) - set(expected))
    status = "verified" if not changed and not missing else "FAILED"
    return {"status": status, "files": len(expected), "changed": changed,
            "missing": missing, "added_after_snapshot": added}


def validate_run(run_dir: Path) -> dict:
    failures, warnings, sessions = [], [], []
    meta_paths = sorted(run_dir.glob("*/*/session_meta.json"))
    expected = None
    run_log = run_dir / "run.log"
    if run_log.exists():
        match = re.search(r"sessions\s+:\s+(\d+)", run_log.read_text(encoding="utf-8", errors="replace"))
        expected = int(match.group(1)) if match else None
    if expected is not None and len(meta_paths) != expected:
        failures.append(f"expected {expected} session_meta files, found {len(meta_paths)}")

    receipts = 0
    parsed_evidence = {}
    for meta_path in meta_paths:
        session_dir = meta_path.parent
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"{meta_path}: invalid session_meta.json: {exc}")
            continue
        cid = f"{meta.get('config')}__{meta.get('injection_id')}"
        required = ["canary_manifest.json", "canary_state_before.json",
                    "canary_state_after.json", "outbound.jsonl"]
        if meta.get("framework") == "constrained":
            required.append("raw_calls.jsonl")
        else:
            required += ["transcript.jsonl", "openclaw_session.json", "tool_inventory.json"]
        absent = [name for name in required if not (session_dir / name).exists()]
        if absent:
            failures.append(f"{cid}: missing {', '.join(absent)}")
        if meta.get("framework") == "constrained" and not absent:
            try:
                call_errors = [row.get("error") for row in (
                    json.loads(line) for line in (session_dir / "raw_calls.jsonl").read_text(
                        encoding="utf-8").splitlines() if line.strip()) if row.get("error")]
                if call_errors:
                    failures.append(f"{cid}: {len(call_errors)} raw provider/parse failure(s)")
            except (OSError, AttributeError, json.JSONDecodeError) as exc:
                failures.append(f"{cid}: invalid raw_calls.jsonl: {exc}")
        receipt_path = session_dir / "artifact_receipt.json"
        if receipt_path.exists():
            receipts += 1
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                items = receipt.get("files", [])
                if receipt.get("file_count") != len(items):
                    failures.append(f"{cid}: artifact receipt file_count mismatch")
                recorded = {item["path"] for item in items}
                actual = {p.relative_to(session_dir).as_posix()
                          for p in session_dir.rglob("*") if p.is_file()
                          and p.name != "artifact_receipt.json"}
                if recorded != actual:
                    failures.append(f"{cid}: artifact receipt does not cover every raw file")
                for item in items:
                    local = session_dir / item["path"]
                    if not local.is_file():
                        failures.append(f"{cid}: receipted file missing: {item['path']}")
                    elif local.stat().st_size != item["bytes"]:
                        failures.append(f"{cid}: receipted size changed: {item['path']}")
                    elif item.get("sha256") and _sha(local) != item["sha256"]:
                        failures.append(f"{cid}: receipted hash changed: {item['path']}")
            except Exception as exc:
                failures.append(f"{cid}: invalid artifact_receipt.json: {exc}")
        if not session_measurable(meta):
            failures.append(f"{cid}: incomplete/error: {meta.get('error') or meta.get('agent_done')}")
        if meta.get("framework") == "openclaw" and meta.get("model_key") in MODELS:
            if "low" not in meta.get("openclaw_thinking_levels", []):
                failures.append(f"{cid}: reasoning model did not record low thinking")
            if not meta.get("openclaw_reasoning_blocks"):
                warnings.append(
                    f"{cid}: low thinking configured but no reasoning text block was exposed")
        tokens = meta.get("tokens") or {}
        if not (tokens.get("in") or tokens.get("out")):
            failures.append(f"{cid}: zero token accounting")
        for name in absent:
            required.remove(name)
        for name in required:
            path = session_dir / name
            try:
                if path.suffix == ".jsonl":
                    for line_no, line in enumerate(path.read_text(
                            encoding="utf-8").splitlines(), 1):
                        if line.strip():
                            json.loads(line)
                elif path.suffix == ".json":
                    json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                failures.append(f"{cid}: invalid {name}: {exc}")
        try:
            row, _judge_text = extract(meta_path)
            parsed_evidence[meta_path] = row
        except Exception as exc:
            failures.append(f"{cid}: evidence extraction failed: {exc}")
            row = {}
        sessions.append({"custom_id": cid, "framework": meta.get("framework"),
                         "evidence_available": row.get("evidence_available", ""),
                         "tool_calls": row.get("tool_calls", 0),
                         "tool_results": row.get("tool_results", 0)})

    if meta_paths and receipts == 0:
        warnings.append("legacy run: no checksum receipts (rerun with the updated orchestrator)")
    elif receipts != len(meta_paths):
        failures.append(f"artifact receipts cover {receipts}/{len(meta_paths)} sessions")
    return {"ok": not failures, "expected_sessions": expected,
            "found_sessions": len(meta_paths), "artifact_receipts": receipts,
            "failures": failures, "warnings": warnings, "sessions": sessions}


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def evidence_table(run_dir: Path) -> list[dict]:
    judgments = {}
    jf = run_dir / "postprocess" / "judgments.jsonl"
    if jf.exists():
        for line in jf.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            judgments[row["custom_id"]] = row
    rows = []
    for path, row, _judge_text in collect(run_dir):
        judgment = judgments.get(row["custom_id"], {})
        meta = json.loads(path.read_text(encoding="utf-8"))
        succeeded = bool(meta.get("attack_succeeded") or meta.get("outcome") == "SUCCEEDED")
        rows.append({**row, "attempt_judged": bool(judgment or succeeded),
                     "attempted": True if succeeded else judgment.get("attempted", ""),
                     "judge_confidence": "deterministic" if succeeded else judgment.get("confidence", ""),
                     "judge_steps": "|".join(judgment.get("attempted_steps", [])),
                     "judge_evidence": judgment.get("evidence", "")})
    _write_csv(run_dir / "postprocess" / "evidence.csv", rows)
    return rows


def _svg_bars(path: Path, title: str, groups: list[str], series: dict[str, list[float]],
              y_label: str, maximum: float | None = None) -> None:
    width, height, left, top, bottom = 1200, 650, 90, 70, 190
    plot_w, plot_h = width - left - 30, height - top - bottom
    values = [v for xs in series.values() for v in xs]
    ymax = maximum or max(values + [1.0])
    colors = ["#31688e", "#35b779", "#fde725", "#8c2981", "#f46d43"]
    n_series = max(1, len(series)); group_w = plot_w / max(1, len(groups)); bar_w = group_w * 0.72 / n_series
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
             '<rect width="100%" height="100%" fill="white"/>',
             f'<text x="{width/2}" y="32" text-anchor="middle" font-size="24" font-family="sans-serif">{html.escape(title)}</text>',
             f'<line x1="{left}" y1="{top+plot_h}" x2="{left+plot_w}" y2="{top+plot_h}" stroke="#333"/>',
             f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}" stroke="#333"/>']
    for tick in range(6):
        value = ymax * tick / 5; y = top + plot_h - plot_h * tick / 5
        parts += [f'<line x1="{left}" y1="{y}" x2="{left+plot_w}" y2="{y}" stroke="#ddd"/>',
                  f'<text x="{left-10}" y="{y+5}" text-anchor="end" font-size="13" font-family="sans-serif">{value:.2g}</text>']
    for gi, group in enumerate(groups):
        center = left + group_w * (gi + 0.5)
        for si, (name, vals) in enumerate(series.items()):
            value = vals[gi] if gi < len(vals) else 0
            h = plot_h * value / ymax if ymax else 0
            x = center - (n_series * bar_w) / 2 + si * bar_w
            parts.append(f'<rect x="{x:.1f}" y="{top+plot_h-h:.1f}" width="{bar_w-2:.1f}" height="{h:.1f}" fill="{colors[si % len(colors)]}"/>')
        label = html.escape(group)
        parts.append(f'<text transform="translate({center:.1f},{top+plot_h+15}) rotate(55)" text-anchor="start" font-size="12" font-family="sans-serif">{label}</text>')
    for si, name in enumerate(series):
        x = left + si * 190
        parts += [f'<rect x="{x}" y="{height-28}" width="14" height="14" fill="{colors[si % len(colors)]}"/>',
                  f'<text x="{x+20}" y="{height-16}" font-size="13" font-family="sans-serif">{html.escape(name)}</text>']
    parts.append(f'<text transform="translate(22,{top+plot_h/2}) rotate(-90)" text-anchor="middle" font-size="14" font-family="sans-serif">{html.escape(y_label)}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def make_figures(run_dir: Path, evidence: list[dict]) -> None:
    fig = run_dir / "figures"; fig.mkdir(exist_ok=True)
    with (run_dir / "summary.csv").open(encoding="utf-8") as f:
        summary = list(csv.DictReader(f))
    groups = sorted({r["config"] for r in summary})
    by_config = {g: [r for r in summary if r["config"] == g] for g in groups}
    def rate(g, field):
        rows = by_config[g]; return sum(str(r[field]).lower() == "true" for r in rows) / len(rows)
    rate_series = {"success": [rate(g, "attack_succeeded") for g in groups],
                   "accessed": [rate(g, "canary_accessed") for g in groups],
                   "leaked": [rate(g, "leaked") for g in groups],
                   "utility": [rate(g, "benign_task_completed") for g in groups]}
    if all(any(str(r.get("attempt_judged", "")).lower() == "true" for r in by_config[g]) for g in groups):
        rate_series = {"attempted": [rate(g, "attempted") for g in groups], **rate_series}
    _svg_bars(fig / "outcome_rates_by_config.svg", "Outcome rates by configuration",
              groups, rate_series, "rate", maximum=1.0)

    ev_by = {g: [r for r in evidence if r["config"] == g] for g in groups}
    avg = lambda g, field: sum(float(r[field]) for r in ev_by[g]) / max(1, len(ev_by[g]))
    _svg_bars(fig / "tool_activity_by_config.svg", "Native tool activity by configuration",
              groups, {"actions": [avg(g, "action_records") for g in groups],
                       "calls": [avg(g, "tool_calls") for g in groups],
                       "successes": [avg(g, "tool_successes") for g in groups],
                       "errors": [avg(g, "tool_errors") for g in groups]}, "mean events/session")
    _svg_bars(fig / "tokens_by_config.svg", "Mean tokens by configuration", groups,
              {"input": [sum(float(r["tokens_in"] or 0) for r in by_config[g])/len(by_config[g]) for g in groups],
               "output": [sum(float(r["tokens_out"] or 0) for r in by_config[g])/len(by_config[g]) for g in groups]},
              "tokens/session")


def write_report(run_dir: Path, integrity: dict, checks: dict, evidence: list[dict]) -> None:
    judged = sum(bool(r["attempt_judged"]) for r in evidence)
    attempted = sum(r["attempted"] is True for r in evidence)
    lines = [f"# Post-experiment report — {run_dir.name}", "",
             f"- Raw integrity: **{integrity['status']}** ({integrity['files']} files)",
             f"- Log validation: **{'PASS' if checks['ok'] else 'FAIL'}**",
             f"- Sessions: {checks['found_sessions']}/{checks['expected_sessions'] or '?'}",
             f"- Checksum receipts: {checks['artifact_receipts']}/{checks['found_sessions']}",
             f"- Attempt judgments: {judged}/{len(evidence)}; attempted: {attempted}", ""]
    if checks["warnings"]:
        lines += ["## Warnings", "", *[f"- {x}" for x in checks["warnings"]], ""]
    if checks["failures"]:
        lines += ["## Failures", "", *[f"- {x}" for x in checks["failures"]], ""]
    lines += ["## Outputs", "",
              "- `postprocess/raw_integrity.sha256`: immutable raw-artifact hashes",
              "- `postprocess/log_validation.json`: completeness/parser checks",
              "- `postprocess/evidence.csv`: reasoning/tool/action features and judgments",
              "- `postprocess/batch_*.json*`: request, id/state, polls, and final raw result",
              "- `figures/*.svg`: publication-friendly comparisons",
              "- `summary.csv` and `metrics.md`: final outcome tables", "",
              "OpenClaw explicit reasoning is only counted when a reasoning/thinking block",
              "exists in its native transcript. Tool calls, results, and assistant text remain",
              "available even when OpenClaw reports `thinkingLevel: off`."]
    (run_dir / "post_experiment_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--classify", action="store_true")
    parser.add_argument("--max-wait", type=float, default=86400)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    integrity = snapshot_or_verify(run_dir)
    checks = validate_run(run_dir)
    post = run_dir / "postprocess"; post.mkdir(exist_ok=True)
    (post / "log_validation.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    print(f"[integrity] {integrity['status']} — {integrity['files']} raw files")
    print(f"[logs] {'PASS' if checks['ok'] else 'FAIL'} — {checks['found_sessions']} sessions, "
          f"{checks['artifact_receipts']} receipts")
    for failure in checks["failures"]:
        print(f"[logs] ERROR: {failure}")
    if integrity["status"] == "FAILED" or not checks["ok"]:
        return 1
    aggregate(run_dir)
    evidence = evidence_table(run_dir)
    make_figures(run_dir, evidence)
    write_report(run_dir, integrity, checks, evidence)
    print(f"[report] {run_dir / 'post_experiment_report.md'}")
    if args.classify and not args.check_only:
        from classify_attempts import run
        rc = run(run_dir, args.max_wait)
        aggregate(run_dir)
        evidence = evidence_table(run_dir)
        make_figures(run_dir, evidence)
        write_report(run_dir, snapshot_or_verify(run_dir), validate_run(run_dir), evidence)
        return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
