from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main() -> int:
    baseline = ROOT / "local_runtime/baseline_red"
    attempt = ROOT / "local_runtime/attempts" / f"preflight-{uuid.uuid4().hex[:8]}"
    shutil.copytree(baseline, attempt)
    (attempt / "saves/_bridge").mkdir(parents=True)
    os.environ.update({
        "POKEMON_EVAL_ROM": str(attempt / "PokemonRed.gb"),
        "POKEMON_EVAL_ROM_DIR": str(attempt),
        "POKEMON_EVAL_VBA": str(attempt / "VBA-RR/vba.exe"),
        "POKEMON_EVAL_LUA": str(attempt / "pokemon_bridge.lua"),
        "POKEMON_EVAL_SAVE_BASE": str(attempt / "saves"),
        "POKEMON_EVAL_LUA_SAVE_BASE": str(attempt / "saves"),
        "POKEMON_EVAL_BRIDGE_DIR": str(attempt / "saves/_bridge"),
        "POKEMON_EVAL_POKEMON_ACCESS": str(attempt / "pokemon-access"),
        "POKEMON_EVAL_MAP_DB": str(attempt / "map_database.json"),
    })
    from constrained.helpers.pokemon_game import PokemonGame, read_state
    game = PokemonGame("pokemon_red", "Pokemon Red")
    try:
        if not await game.launch(fresh_start=True):
            raise RuntimeError("isolated emulator launch returned false")
        deadline = time.time() + 90
        state = {}
        interactive = None
        while time.time() < deadline:
            state = read_state() or {}
            candidate = await game.get_game_state()
            if candidate and (candidate[1] or candidate[2].get("needs_name")):
                interactive = candidate
                break
            await asyncio.sleep(0.25)
        if not interactive:
            raise RuntimeError("Lua bridge never reached the first post-dialogue decision")
        paths = {"attempt": str(attempt), "mode": state.get("mode"),
                 "map": state.get("map_name"), "has_pokedex": state.get("has_pokedex"),
                 "bridge": str(attempt / "saves/_bridge/state.json"),
                 "first_state_text": interactive[0],
                 "first_choices": interactive[1]}
        print(json.dumps(paths, indent=2))
        return 0
    finally:
        await game.quit()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
