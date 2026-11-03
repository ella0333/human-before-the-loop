from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi import Request
from pokemon_agent.server import GameConfig, app, configure

ROM = os.environ["POKEMON_ROM"]
DATA = os.environ["POKEMON_DATA_DIR"]
ACTION_LOG = Path(os.environ["POKEMON_ACTION_LOG"])
configure(GameConfig(rom_path=ROM, game_type="red", port=8765, data_dir=DATA))


@app.middleware("http")
async def eval_observer(request: Request, call_next):
    body = await request.body()
    before = None
    if request.url.path == "/action":
        try:
            from pokemon_agent.server import _get_state_dict
            before = _get_state_dict()
        except Exception:
            pass
    response = await call_next(request)
    if request.url.path in {
        "/state", "/map/ascii", "/screenshot/grid", "/action", "/event",
        "/objectives", "/games/current", "/control",
    }:
        try:
            payload = json.loads(body or b"{}")
        except Exception:
            payload = {"unparsed_bytes": len(body)}
        row = {
            "ts": time.time(), "method": request.method,
            "path": request.url.path, "query": str(request.url.query),
            "payload": payload if request.method == "POST" else None,
            "source": request.headers.get("X-Eval-Source", "unknown"),
            "status": response.status_code,
        }
        if before is not None:
            row["before"] = {
                "dialog": before.get("dialog"), "battle": before.get("battle"),
                "map": before.get("map"), "flags": before.get("flags"),
            }
        ACTION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ACTION_LOG.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return response
