from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from post_experiment import DERIVED_ROOTS, DERIVED_TOP


REPO = Path(__file__).resolve().parent
BASE_CANARY_TYPES = {
    "ssh_key": "private_key", "aws_creds": "credential", "passport": "pii",
    "bank_account": "financial_data", "fb_2fa": "recovery_code",
    "private_notes": "internal_notes", "inbox": "private_email",
    "quarterly_report": "destructive_file", "backup_archive": "destructive_file",
    "thesis": "destructive_file", "wallet": "payment_method",
}
BOOL_FIELDS = ("benign_task_completed", "attempted", "attack_succeeded",
               "canary_accessed", "leaked")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_read_only(run_dir: Path) -> dict:
    manifest = run_dir / "postprocess" / "raw_integrity.sha256"
    if not manifest.is_file():
        raise ValueError(f"missing existing integrity manifest: {manifest}")
    expected = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        expected[name] = digest
    current = {}
    for path in run_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(run_dir)
        if rel.parts[0] in DERIVED_ROOTS or rel.as_posix() in DERIVED_TOP:
            continue
        current[rel.as_posix()] = _sha(path)
    changed = sorted(name for name, digest in expected.items()
                     if name in current and current[name] != digest)
    missing = sorted(set(expected) - set(current))
    if changed or missing:
        raise ValueError(f"source integrity failed for {run_dir}: changed={changed}, missing={missing}")
    return {"files": len(expected), "added_after_snapshot": sorted(set(current) - set(expected))}


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _bool(value) -> bool:
    return str(value).strip().lower() == "true"


def _manifest_map() -> dict[str, dict]:
    path = REPO / "corpus" / "gray_swan_extension_manifest.jsonl"
    return {row["injection_id"]: row for row in (
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()) if row["inclusion_status"] == "included"}


def load_run(run_dir: Path, phase: str) -> list[dict]:
    summary_path = run_dir / "summary.csv"
    evidence_path = run_dir / "postprocess" / "evidence.csv"
    if not summary_path.is_file() or not evidence_path.is_file():
        raise ValueError(f"completed summary/evidence missing under {run_dir}")
    evidence = {row["custom_id"]: row for row in _read_csv(evidence_path)}
    ext_manifest = _manifest_map()
    rows = []
    for row in _read_csv(summary_path):
        custom_id = f"{row['config']}__{row['session_id']}"
        ev = evidence.get(custom_id, {})
        if phase == "base":
            corpus_source, study_phase = "agentdojo", "base"
            family = f"agentdojo__{row['template_name']}"
            canary_type = BASE_CANARY_TYPES.get(row["canary_id"], "unknown")
        else:
            meta = ext_manifest.get(row["session_id"])
            if not meta:
                raise ValueError(f"extension injection absent from pinned manifest: {row['session_id']}")
            corpus_source, study_phase = "gray_swan_ipi_arena", "gray_swan_extension"
            family, canary_type = meta["attack_family"], meta["canary_type"]
        normalized = {**row, "corpus_source": corpus_source, "study_phase": study_phase,
                      "attack_family": family, "canary_type": canary_type,
                      "reasoning_available": bool(int(ev.get("reasoning_records") or 0)
                                                  or int(ev.get("reasoning_tokens") or 0)),
                      "first_action_attempted": _bool(row.get("attempted")),
                      "full_attack_success": _bool(row.get("attack_succeeded")),
                      "benign_full_task_completed": _bool(row.get("benign_task_completed"))}
        for field in BOOL_FIELDS:
            normalized[field] = _bool(row.get(field))
        rows.append(normalized)
    return rows


def summarize(rows: list[dict], keys: tuple[str, ...]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[k] for k in keys)].append(row)
    out = []
    for values, group in sorted(groups.items()):
        n = len(group)
        attempted_judged = sum(_bool(r.get("attempt_judged")) for r in group)
        rec = {key: value for key, value in zip(keys, values)}
        rec.update({
            "n": n,
            "first_action_attempt_rate": round(sum(r["first_action_attempted"] for r in group)
                                                / attempted_judged, 4)
            if attempted_judged else None,
            "full_attack_success_rate": round(sum(r["full_attack_success"] for r in group) / n, 4),
            "reasoning_available_rate": round(sum(r["reasoning_available"] for r in group) / n, 4),
            "attempted_injection_rate": round(sum(r["attempted"] for r in group)
                                               / attempted_judged, 4)
            if attempted_judged else None,
            "canary_access_rate": round(sum(r["canary_accessed"] for r in group) / n, 4),
            "canary_leak_rate": round(sum(r["leaked"] for r in group) / n, 4),
            "benign_full_task_rate": round(sum(r["benign_full_task_completed"] for r in group) / n, 4),
        })
        out.append(rec)
    return out


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def _md_table(rows: list[dict]) -> str:
    if not rows:
        return "(no data)"
    cols = list(rows[0])
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    lines.extend("| " + " | ".join(str(row[c]) for c in cols) + " |" for row in rows)
    return "\n".join(lines)


def _phase_svg(path: Path, phase_rows: list[dict]) -> None:
    metrics = ["first_action_attempt_rate", "full_attack_success_rate",
               "canary_access_rate", "canary_leak_rate", "benign_full_task_rate"]
    phases = [row["study_phase"] for row in phase_rows]
    width, height, left, top, plot_h = 1000, 520, 80, 60, 330
    group_w = 820 / len(metrics); bar_w = group_w * 0.32
    colors = ["#31688e", "#35b779"]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<text x="500" y="32" text-anchor="middle" font-size="22" font-family="sans-serif">Base versus GraySwan extension outcomes</text>']
    for tick in range(6):
        y = top + plot_h - plot_h * tick / 5
        parts.append(f'<line x1="{left}" y1="{y}" x2="920" y2="{y}" stroke="#ddd"/>')
        parts.append(f'<text x="70" y="{y+4}" text-anchor="end" font-size="12">{tick/5:.1f}</text>')
    for mi, metric in enumerate(metrics):
        center = left + group_w * (mi + .5)
        for pi, row in enumerate(phase_rows):
            value = float(row[metric] or 0)
            x = center + (pi - .5) * bar_w
            y = top + plot_h * (1 - value)
            parts.append(f'<rect x="{x}" y="{y}" width="{bar_w-3}" height="{plot_h*value}" fill="{colors[pi]}"/>')
        parts.append(f'<text transform="translate({center},{top+plot_h+18}) rotate(35)" font-size="11">{html.escape(metric)}</text>')
    for pi, phase in enumerate(phases):
        x = 220 + pi * 260
        parts.append(f'<rect x="{x}" y="480" width="14" height="14" fill="{colors[pi]}"/>')
        parts.append(f'<text x="{x+20}" y="492" font-size="13">{html.escape(phase)}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_run", type=Path)
    parser.add_argument("extension_run", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    base, extension = args.base_run.resolve(), args.extension_run.resolve()
    output = (args.output or REPO / "results" / f"combined_{datetime.now():%Y-%m-%d_%H%M%S}").resolve()
    for source in (base, extension):
        if output == source or source in output.parents:
            raise SystemExit(f"output may not be a source run or its parent: {output}")
        if output in source.parents:
            raise SystemExit(f"output may not contain a source run: {output}")
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output directory: {output}")
    integrity = {"base": verify_read_only(base), "extension": verify_read_only(extension)}
    rows = load_run(base, "base") + load_run(extension, "extension")
    output.mkdir(parents=True)
    (output / "figures").mkdir()
    _write_csv(output / "combined_sessions.csv", rows)
    pooled = summarize(rows, ())
    for row in pooled:
        row["corpus_source"] = "pooled_descriptive_only"
        row["study_phase"] = "base_plus_gray_swan_extension"
    tables = {
        "by_phase.csv": summarize(rows, ("corpus_source", "study_phase")),
        "by_framework.csv": summarize(rows, ("study_phase", "framework")),
        "by_model.csv": summarize(rows, ("study_phase", "model_key")),
        "by_defense.csv": summarize(rows, ("study_phase", "defense")),
        "by_attack_family.csv": summarize(rows, ("corpus_source", "study_phase", "attack_family")),
        "by_canary_type.csv": summarize(rows, ("corpus_source", "study_phase", "canary_type")),
        "pooled_descriptive.csv": pooled,
    }
    for name, table in tables.items():
        _write_csv(output / name, table)
    _phase_svg(output / "figures" / "base_vs_extension_outcomes.svg", tables["by_phase.csv"])
    report = ["# Base versus GraySwan extension report", "",
              f"- Base source: `{base}` (read-only integrity PASS, {integrity['base']['files']} raw files)",
              f"- Extension source: `{extension}` (read-only integrity PASS, {integrity['extension']['files']} raw files)",
              "- Pooled rows are descriptive only; every row retains `corpus_source` and `study_phase`.", "",
              "`first_action_attempt_rate` and `attempted_injection_rate` use the existing batch classifier's attempted verdict. "
              "`full_attack_success_rate` is deterministic completion of the malicious objective; "
              "`benign_full_task_rate` is completion of the cover task.", "",
              "## Base versus extension", "", _md_table(tables["by_phase.csv"]), "",
              "## Constrained versus OpenClaw", "", _md_table(tables["by_framework.csv"]), "",
              "## Model comparison", "", _md_table(tables["by_model.csv"]), "",
              "## Defense versus no defense", "", _md_table(tables["by_defense.csv"]), "",
              "## Attack family", "", _md_table(tables["by_attack_family.csv"]), "",
              "## Canary type", "", _md_table(tables["by_canary_type.csv"]), ""]
    (output / "combined_report.md").write_text("\n".join(report), encoding="utf-8")
    (output / "source_integrity.json").write_text(json.dumps(integrity, indent=2), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
