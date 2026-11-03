"""Create a sealed local VBA-RR baseline without modifying source files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "local_runtime" / "baseline_red"
RUNTIME = ROOT / "runtime"


def load_config() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in (ROOT.parent.parent / "config.env", ROOT / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    values.update({k: v for k, v in os.environ.items() if v})
    return values


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(paths: list[Path]) -> dict:
    files = []
    for item in paths:
        members = [item] if item.is_file() else sorted(p for p in item.rglob("*") if p.is_file())
        for path in members:
            files.append({"path": str(path.resolve()), "bytes": path.stat().st_size,
                          "sha256": sha256(path)})
    return {"created_at": datetime.now(timezone.utc).isoformat(), "files": files}


def contained(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def main() -> None:
    config = load_config()
    parser = argparse.ArgumentParser()
    parser.add_argument("--vba-dir", type=Path,
                        default=config.get("LOCAL_VBA_DIR") or RUNTIME / "VBA-RR")
    parser.add_argument("--rom", type=Path,
                        default=config.get("LOCAL_ROM_PATH"))
    parser.add_argument("--map-db", type=Path,
                        default=config.get("LOCAL_MAP_DB") or RUNTIME / "map_database.json")
    parser.add_argument("--pokemon-access", type=Path,
                        default=config.get("LOCAL_EMULATOR_ACCESS_DIR")
                        or config.get("LOCAL_POKEMON_ACCESS")
                        or RUNTIME / "emulator-access")
    args = parser.parse_args()
    if not args.rom:
        raise SystemExit("supply --rom, or set LOCAL_ROM_PATH in config.env (python setup.py)")
    title = args.rom.read_bytes()[0x134:0x143].rstrip(b"\x00").decode("ascii", "replace")
    if not title.startswith("POKEMON RED"):
        raise SystemExit(f"--rom reports itself as {title!r}, not POKEMON RED. "
                         "Red specifically is required, not Blue or Yellow.")
    pokemon_access = args.pokemon_access.resolve()
    sources = [args.vba_dir.resolve(), args.rom.resolve(), args.map_db.resolve(), pokemon_access]
    missing = [str(path) for path in sources if not path.exists()]
    if missing:
        raise SystemExit(f"local source missing: {missing}")
    if any(contained(BASELINE, source) or contained(source, BASELINE)
           for source in sources):
        raise SystemExit("source and destination must be disjoint")

    if BASELINE.exists():
        raise SystemExit(f"baseline already exists: {BASELINE}; move it aside explicitly to restage")
    BASELINE.mkdir(parents=True)
    before = manifest(sources)
    shutil.copytree(args.vba_dir, BASELINE / "VBA-RR")
    shutil.copy2(args.rom, BASELINE / "PokemonRed.gb")
    shutil.copy2(args.map_db, BASELINE / "map_database.json")
    shutil.copytree(pokemon_access, BASELINE / "pokemon-access")
    shutil.copy2(ROOT / "constrained" / "pokemon_bridge.lua", BASELINE / "pokemon_bridge.lua")
    after = manifest(sources)
    if before["files"] != after["files"]:
        raise RuntimeError("source files changed while staging; baseline is not trustworthy")
    (BASELINE / "source_manifest.json").write_text(
        json.dumps({"before": before, "after": after}, indent=2), encoding="utf-8")
    print(f"staged sealed baseline: {BASELINE}")
    print(f"ROM sha256: {sha256(BASELINE / 'PokemonRed.gb')}")


if __name__ == "__main__":
    main()
