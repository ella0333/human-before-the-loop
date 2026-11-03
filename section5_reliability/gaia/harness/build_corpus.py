"""Build the fixed public GAIA prompt subset without using model outcomes."""

import argparse
from collections import Counter

from gaia_dataset import (DATA_DIR, GAIA_REVISION, HF_FILE, HF_REPO, SOURCE_FILE,
                          select_rows, write_corpus)

def download() -> None:
    from huggingface_hub import hf_hub_download
    hf_hub_download(HF_REPO, HF_FILE, repo_type="dataset", revision=GAIA_REVISION,
                    local_dir=DATA_DIR)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    if args.download:
        download()
    if not SOURCE_FILE.is_file():
        raise FileNotFoundError(f"missing {SOURCE_FILE}; rerun with --download")
    rows = select_rows()
    if len(rows) != 31:
        raise RuntimeError(f"pinned GAIA filter expected 31 tasks, selected {len(rows)}")
    output = write_corpus(rows)
    counts = Counter(row["category"] for row in rows)
    print(f"prepared {len(rows)} GAIA prompt tasks at {output}")
    print("selection by category: " + ", ".join(f"{key}={counts[key]}" for key in sorted(counts)))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
