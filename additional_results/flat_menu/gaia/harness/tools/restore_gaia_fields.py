"""Rebuild the full GAIA results workbook. See additional_results/flat_menu/README.md."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gaia_vault import corpus_key, unseal

HARNESS = Path(__file__).resolve().parents[1]
CORPUS = HARNESS / "benchmark_data" / "gaia" / "selected_tasks.jsonl"
RESULTS = HARNESS.parents[1] / "results"
WORKBOOK = RESULTS / "flat_menu_raw_results.xlsx"
SEALED = RESULTS / "gaia_raw_turns.sealed"
PLACEHOLDER = re.compile(r"\[GAIA_QUESTION:([0-9a-fA-F-]+)\]")


def load_corpus(path: Path) -> dict[str, dict]:
    if not path.is_file():
        raise SystemExit(
            f"missing {path}\n"
            "Run `python build_corpus.py --download` first. It needs a HuggingFace\n"
            "account with the GAIA dataset terms accepted; see the section README."
        )
    tasks = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            tasks[row["task_id"]] = row
    if len(tasks) != 31:
        raise SystemExit(f"expected the pinned 31-task corpus, found {len(tasks)}")
    return tasks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workbook", type=Path, default=WORKBOOK)
    parser.add_argument("--sealed", type=Path, default=SEALED)
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--out", type=Path,
                        help="default: <workbook stem>.restored.xlsx beside the input")
    args = parser.parse_args(argv)

    import openpyxl

    tasks = load_corpus(args.corpus)
    key = corpus_key(list(tasks.values()))
    target = args.out or args.workbook.with_suffix(".restored.xlsx")
    book = openpyxl.load_workbook(args.workbook)

    if not args.sealed.is_file():
        raise SystemExit(f"missing {args.sealed}")
    payload = unseal(args.sealed.read_bytes(), key)
    header, rows = payload["header"], payload["rows"]
    position = book.sheetnames.index("GAIA Raw Turns")
    del book["GAIA Raw Turns"]
    sheet = book.create_sheet("GAIA Raw Turns", position)
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    sheet.freeze_panes = "F2"
    sheet.auto_filter.ref = (
        f"A1:{openpyxl.utils.get_column_letter(len(header))}{len(rows) + 1}")

    restored_questions = 0
    unknown: set[str] = set()

    def put_back(match: re.Match) -> str:
        nonlocal restored_questions
        task = tasks.get(match.group(1))
        if task is None:
            unknown.add(match.group(1))
            return match.group(0)
        restored_questions += 1
        return task["question"]

    for page in book.worksheets:
        for row in page.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and "[GAIA_QUESTION:" in cell.value:
                    cell.value = PLACEHOLDER.sub(put_back, cell.value)

    outcomes = book["Outcomes"]
    titles = [cell.value for cell in outcomes[1]]
    restored_answers = 0
    if "final_answer" not in titles:
        suite_at = titles.index("suite") + 1
        task_at = titles.index("task_id") + 1
        column = outcomes.max_column + 1
        outcomes.cell(row=1, column=column, value="final_answer")
        for index in range(2, outcomes.max_row + 1):
            if outcomes.cell(row=index, column=suite_at).value != "gaia":
                continue
            task = tasks.get(outcomes.cell(row=index, column=task_at).value)
            if task is not None:
                outcomes.cell(row=index, column=column, value=task["final_answer"])
                restored_answers += 1

    book.save(target)
    print(f"restored {len(rows)} sealed turn rows, {restored_questions} quoted "
          f"questions and {restored_answers} reference answers")
    print(f"wrote {target}")
    if unknown:
        print(f"warning: {len(unknown)} task ids were not in the corpus: "
              f"{sorted(unknown)[:5]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
