from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {".gb", ".gbc", ".gba", ".sav", ".sgm", ".state"}


def main() -> int:
    files = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    bad = [path for path in files if Path(path).suffix.lower() in FORBIDDEN_SUFFIXES]
    if bad:
        print("forbidden licensed/save artifacts tracked: " + repr(bad), file=sys.stderr)
        return 1
    for name in (".env", "openrouter.key"):
        if name in files:
            print(f"secret-bearing file tracked: {name}", file=sys.stderr)
            return 1
    print("repository safety check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
