from __future__ import annotations

from typing import Callable


class MockWorld:
    def __init__(self, on_event: Callable[[dict], None] = lambda _row: None) -> None:
        self.events: list[dict] = []
        self.on_event = on_event

    def record(self, activity: str, target: str, **fields) -> dict:
        row = {"activity": activity, "target": target, **fields}
        self.events.append(row); self.on_event(row)
        return row
