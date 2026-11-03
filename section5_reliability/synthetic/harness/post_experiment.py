from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from evalspec import all_configs, all_prompt_ids, session_measurable
from harness.aggregate import aggregate


def _sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_files(run_dir):
    post = run_dir / "postprocess"
    return [p for p in sorted(run_dir.rglob("*")) if p.is_file() and post not in p.parents
            and p.name not in {"summary.csv", "metrics.json", "metrics.md", "run.log"}]


def snapshot_or_verify(run_dir):
    post = run_dir / "postprocess"; post.mkdir(exist_ok=True)
    manifest_path = post / "raw_hashes.json"
    current = {p.relative_to(run_dir).as_posix(): _sha(p) for p in raw_files(run_dir)}
    if not manifest_path.exists():
        manifest_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        return {"status": "CREATED", "files": len(current), "changed": [], "missing": []}
    prior = json.loads(manifest_path.read_text(encoding="utf-8"))
    prior.pop("run.log", None)
    changed = sorted(k for k in set(prior) & set(current) if prior[k] != current[k])
    missing = sorted(set(prior) - set(current)); added = sorted(set(current) - set(prior))
    return {"status": "FAILED" if changed or missing else "VERIFIED", "files": len(current),
            "changed": changed, "missing": missing, "added": added}


def _receipt_valid(session):
    try:
        receipt = json.loads((session / "artifact_receipt.json").read_text(encoding="utf-8"))
        items = receipt.get("files") or []
        return receipt.get("file_count") == len(items) and all(
            (session / x["path"]).is_file() and (session / x["path"]).stat().st_size == x["bytes"]
            and _sha(session / x["path"]) == x["sha256"] for x in items)
    except Exception: return False


def validate_run(run_dir):
    failures, warnings = [], []
    paths = sorted(run_dir.glob("*/*/session_meta.json"))
    expected_full = len(all_configs()) * len(all_prompt_ids())
    declared = None
    log_path = run_dir / "run.log"
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "sessions   :" in line:
                try: declared = int(line.rsplit(":", 1)[1].strip())
                except ValueError: pass
    expected = declared or expected_full
    if len(paths) != expected: failures.append(f"expected {expected} sessions, found {len(paths)}")
    receipts = 0
    for path in paths:
        session = path.parent
        try: meta = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc: failures.append(f"{path}: invalid metadata: {exc}"); continue
        required_fields = ["prompt_id", "user_prompt", "expected_main_action", "expected_final_mock_result",
                           "actions", "first_action_correct", "full_task_correct", "terminal_status",
                           "turns", "tokens", "duration_s"]
        for field in required_fields:
            if field not in meta: failures.append(f"{path}: missing {field}")
        required_files = ["agent_stdout.log", "mock_events.jsonl", "outbound.jsonl"]
        required_files += ["raw_calls.jsonl"] if meta.get("framework") == "constrained" else ["transcript.jsonl", "openclaw_session.json", "tool_inventory.json"]
        for name in required_files:
            if not (session / name).is_file(): failures.append(f"{session}: missing {name}")
        if meta.get("framework") == "constrained" and (session / "raw_calls.jsonl").is_file():
            try:
                call_errors = [row.get("error") for row in (
                    json.loads(line) for line in (session / "raw_calls.jsonl").read_text(
                        encoding="utf-8").splitlines() if line.strip()) if row.get("error")]
                if call_errors:
                    failures.append(f"{session}: {len(call_errors)} raw provider/parse failure(s)")
            except (OSError, AttributeError, json.JSONDecodeError) as exc:
                failures.append(f"{session}: invalid raw_calls.jsonl: {exc}")
        if not session_measurable(meta):
            failures.append(f"{session}: incomplete/error: {meta.get('error') or meta.get('agent_done')}")
        if _receipt_valid(session): receipts += 1
        else: failures.append(f"{session}: invalid or missing transfer receipt")
        if meta.get("framework") == "openclaw" and not meta.get("reasoning_blocks"):
            warnings.append(f"{meta.get('config')}__{meta.get('prompt_id')}: native reasoning missing (recorded as missing)")
    return {"ok": not failures, "expected_sessions": expected, "found_sessions": len(paths),
            "artifact_receipts": receipts, "failures": failures, "warnings": warnings}


def svg_bars(path, title, labels, values, color="#4f46e5"):
    width, height, left, top, chart_h = 900, 480, 90, 55, 340
    bar_w = max(24, 650 // max(1, len(labels))); gap = 18
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
             '<rect width="100%" height="100%" fill="white"/>',
             f'<text x="{width/2}" y="28" text-anchor="middle" font-family="sans-serif" font-size="20">{title}</text>',
             f'<line x1="{left}" y1="{top+chart_h}" x2="850" y2="{top+chart_h}" stroke="#333"/>']
    for i, (label, value) in enumerate(zip(labels, values)):
        x = left + 25 + i * (bar_w + gap); h = chart_h * max(0, min(1, value)); y = top + chart_h - h
        parts += [f'<rect x="{x}" y="{y}" width="{bar_w}" height="{h}" fill="{color}"/>',
                  f'<text x="{x+bar_w/2}" y="{y-6}" text-anchor="middle" font-family="sans-serif" font-size="12">{value:.1%}</text>',
                  f'<text x="{x+bar_w/2}" y="{top+chart_h+18}" text-anchor="middle" font-family="sans-serif" font-size="10" transform="rotate(25 {x+bar_w/2} {top+chart_h+18})">{label}</text>']
    parts.append("</svg>"); path.write_text("\n".join(parts), encoding="utf-8")


def make_figures(run_dir, metrics):
    figures = run_dir / "figures"; figures.mkdir(exist_ok=True)
    cells = list(metrics["by_cell"])
    svg_bars(figures / "first_action_accuracy.svg", "First-action accuracy", cells,
             [metrics["by_cell"][c]["first_action_accuracy"] for c in cells], "#2563eb")
    svg_bars(figures / "full_task_accuracy.svg", "Full-task accuracy", cells,
             [metrics["by_cell"][c]["full_task_accuracy"] for c in cells], "#059669")
    svg_bars(figures / "reasoning_action_agreement.svg", "Reasoning/action agreement", cells,
             [metrics["by_cell"][c]["reasoning_action_agreement"] for c in cells], "#7c3aed")
    for cell in cells:
        safe = cell.replace("__", "_")
        style_keys = [k for k in metrics["by_prompt_style"] if k.startswith(cell + "__")]
        svg_bars(figures / f"full_task_by_style_{safe}.svg", f"Full-task accuracy by style — {cell}",
                 [k.removeprefix(cell + "__") for k in style_keys],
                 [metrics["by_prompt_style"][k]["full_task_accuracy"] for k in style_keys], "#d97706")
        branch_keys = [k for k in metrics["by_menu_branch"] if k.startswith(cell + "__")]
        svg_bars(figures / f"full_task_by_branch_{safe}.svg", f"Full-task accuracy by branch — {cell}",
                 [k.removeprefix(cell + "__") for k in branch_keys],
                 [metrics["by_menu_branch"][k]["full_task_accuracy"] for k in branch_keys], "#0891b2")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run_dir", type=Path)
    ap.add_argument("--check-only", action="store_true"); ap.add_argument("--classify", action="store_true")
    ap.add_argument("--max-wait", type=float, default=None); args = ap.parse_args(); run_dir = args.run_dir.resolve()
    checks = validate_run(run_dir); integrity = snapshot_or_verify(run_dir)
    post = run_dir / "postprocess"; post.mkdir(exist_ok=True)
    (post / "log_validation.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    print(f"log check: {'PASS' if checks['ok'] else 'FAIL'} ({checks['found_sessions']} sessions, {checks['artifact_receipts']} receipts)")
    print(f"raw hashes: {integrity['status']} ({integrity['files']} files)")
    if not checks["ok"] or integrity["status"] == "FAILED": return 1
    if args.classify:
        from classify_attempts import run
        rc = run(run_dir, args.max_wait)
        if rc: return rc
    metrics = aggregate(run_dir); make_figures(run_dir, metrics)
    return 0


if __name__ == "__main__": raise SystemExit(main())
