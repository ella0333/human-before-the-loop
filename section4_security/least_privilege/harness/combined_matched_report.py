"""Combine the section 4 constrained and OpenClaw runs with the least-privilege OpenClaw runs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from combined_report import _md_table, _write_csv, load_run, summarize, verify_read_only

REPO = Path(__file__).resolve().parent
SECTION4_RESULTS = REPO.parents[1] / "harness" / "results"


def _latest_section4_run(extension: bool) -> Path | None:
    if not SECTION4_RESULTS.is_dir():
        return None
    runs = sorted(path for path in SECTION4_RESULTS.glob("run_*")
                  if path.is_dir() and path.name.endswith("__gray_swan_extension") == extension
                  and (path / "postprocess").is_dir())
    return runs[-1] if runs else None


def _with_arm(rows: list[dict], source: str) -> list[dict]:
    for row in rows:
        row["arm"] = row["config"].rsplit("__", 1)[0]
        row["source"] = source
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("least_privilege_base_run", type=Path)
    parser.add_argument("least_privilege_extension_run", type=Path)
    parser.add_argument("--original-base-run", type=Path,
                        help="section 4 AgentDojo run; defaults to the newest one in section4_security/harness/results")
    parser.add_argument("--original-extension-run", type=Path,
                        help="section 4 Gray Swan run; defaults to the newest one in section4_security/harness/results")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    original_base = args.original_base_run or _latest_section4_run(extension=False)
    original_extension = args.original_extension_run or _latest_section4_run(extension=True)
    if original_base is None or original_extension is None:
        raise SystemExit("No completed section 4 runs were found in section4_security/harness/results. "
                         "Run python orchestrator.py --full and python orchestrator.py --full --greyswan "
                         "in section4_security/harness first, or pass --original-base-run and "
                         "--original-extension-run.")
    sources = {
        "original_base": original_base.resolve(),
        "original_extension": original_extension.resolve(),
        "least_privilege_base": args.least_privilege_base_run.resolve(),
        "least_privilege_extension": args.least_privilege_extension_run.resolve(),
    }
    output = (args.output or REPO / "results"
              / f"combined_matched_{datetime.now():%Y-%m-%d_%H%M%S}").resolve()
    for source in sources.values():
        if output == source or source in output.parents or output in source.parents:
            raise SystemExit(f"output may not overlap a source run: {output}")
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output directory: {output}")
    integrity = {name: verify_read_only(path) for name, path in sources.items()}
    rows = []
    for name, path in sources.items():
        rows += _with_arm(load_run(path, "extension" if name.endswith("extension") else "base"), name)
    plain = [row for row in rows if row["defense"] == "none"]

    output.mkdir(parents=True)
    _write_csv(output / "combined_sessions.csv", rows)
    tables = {
        "by_arm.csv": summarize(plain, ("arm",)),
        "by_phase_arm.csv": summarize(plain, ("study_phase", "arm")),
        "by_arm_model.csv": summarize(plain, ("arm", "model_key")),
        "by_phase_arm_model.csv": summarize(plain, ("study_phase", "arm", "model_key")),
        "by_arm_canary_type.csv": summarize(plain, ("arm", "canary_type")),
        "by_arm_including_pytector.csv": summarize(rows, ("arm",)),
    }
    for name, table in tables.items():
        _write_csv(output / name, table)
    report = ["# Constrained versus OpenClaw versus least-privilege OpenClaw", ""]
    report += [f"- {name}: `{path}` (read-only integrity PASS, {integrity[name]['files']} raw files)"
               for name, path in sources.items()]
    report += ["",
               "Main tables use the undefended arms only (`defense == none`); the Pytector arms "
               "appear only in the last table. `attempted_injection_rate` is the batch "
               "classifier's willingness verdict, `full_attack_success_rate` is deterministic "
               "completion of the malicious objective, and `benign_full_task_rate` is completion "
               "of the cover task.", "",
               "## By arm", "", _md_table(tables["by_arm.csv"]), "",
               "## By corpus and arm", "", _md_table(tables["by_phase_arm.csv"]), "",
               "## By arm and model", "", _md_table(tables["by_arm_model.csv"]), "",
               "## By corpus, arm, and model", "", _md_table(tables["by_phase_arm_model.csv"]), "",
               "## By arm and canary type", "", _md_table(tables["by_arm_canary_type.csv"]), "",
               "## All arms including Pytector", "",
               _md_table(tables["by_arm_including_pytector.csv"]), ""]
    (output / "combined_report.md").write_text("\n".join(report), encoding="utf-8")
    (output / "source_integrity.json").write_text(json.dumps(
        {name: {"path": str(sources[name]), **integrity[name]} for name in sources}, indent=2),
        encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
