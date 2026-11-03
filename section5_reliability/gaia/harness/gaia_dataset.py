"""Pre-model capability filter for the public GAIA 2023 validation split."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "benchmark_data" / "gaia"
SOURCE_FILE = DATA_DIR / "2023" / "validation" / "metadata.parquet"
MANIFEST = DATA_DIR / "selected_tasks.jsonl"
HF_REPO = "gaia-benchmark/GAIA"
HF_FILE = "2023/validation/metadata.parquet"
GAIA_REVISION = "682dd723ee1e1697e00360edccf2366dc8418dd9"
PROFILE_VERSION = "gaia-level1-no-files-web-calculator-v3"
MODEL_CALL_CAP = 32

_ALLOWED_TOOL = re.compile(
    r"^(?:\s*\d+\.\s*)?(?:a\s+)?(?:web browser|search engine|calculator|wikipedia|"
    r"no tools required|none)\.?$", re.IGNORECASE)

def eligibility_decision(row: dict) -> tuple[bool, str]:
    if str(row.get("Level") or "") != "1":
        return False, "outside Level 1 reliability scope"
    if str(row.get("file_name") or "").strip() or str(row.get("file_path") or "").strip():
        return False, "requires an attached file"
    metadata = row.get("Annotator Metadata") or {}
    tools = [part.strip() for part in str(metadata.get("Tools") or "").splitlines() if part.strip()]
    if not tools or not all(_ALLOWED_TOOL.fullmatch(part) for part in tools):
        return False, "requires a tool outside reasoning/public-web/calculator"
    if not str(row.get("Question") or "").strip() or not str(row.get("Final answer") or "").strip():
        return False, "missing prompt or public reference answer"
    return True, "Level 1; no attachment; only reasoning/public-web/calculator"

def select_rows(source: Path = SOURCE_FILE) -> list[dict]:
    import pyarrow.parquet as pq
    selected = []
    for row in pq.read_table(source).to_pylist():
        allowed, reason = eligibility_decision(row)
        if not allowed:
            continue
        meta = row.get("Annotator Metadata") or {}
        tools = str(meta.get("Tools") or "")
        category = "calculator" if "calculator" in tools.casefold() else (
            "web" if any(word in tools.casefold() for word in ("web", "search", "wikipedia"))
            else "reasoning")
        task_id = str(row["task_id"])
        selected.append({"id": task_id, "task_id": task_id, "suite": "gaia_prompt_subset",
            "question": str(row["Question"]), "final_answer": str(row["Final answer"]),
            "level": 1, "category": category, "annotated_tools": tools,
            "annotated_steps": int(meta.get("Number of steps") or 0),
            "eligibility_profile": PROFILE_VERSION, "eligibility_reason": reason,
            "source_revision": GAIA_REVISION})
    return sorted(selected, key=lambda task: task["task_id"])

def write_corpus(rows: list[dict], manifest: Path = MANIFEST) -> Path:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                        encoding="utf-8")
    return manifest

def load_tasks(manifest: Path = MANIFEST) -> list[dict]:
    if not manifest.is_file():
        raise FileNotFoundError(f"GAIA corpus not found at {manifest}; run build_corpus.py --download")
    return [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]

def load_task(task_id: str) -> dict:
    for row in load_tasks():
        if row["task_id"] == task_id:
            return row
    raise KeyError(f"unknown GAIA task {task_id}")
