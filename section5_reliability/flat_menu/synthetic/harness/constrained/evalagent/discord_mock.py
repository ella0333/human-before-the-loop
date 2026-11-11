from __future__ import annotations

from typing import Callable

DM_FRIENDS = {
    "friend_1": [{"sender": "friend_1", "content": "hey! did you catch the game last night?"},
                 {"sender": "friend_1", "content": "was wild. talk later"}],
    "friend_2": [{"sender": "friend_2", "content": "sending over that playlist we talked about"}],
    "friend_3": [{"sender": "friend_3", "content": "lunch friday? the usual place"}],
}
SERVER_POSTS = [
    {"sender": "orin_t", "content": "morning all, coffee machine is fixed finally"},
    {"sender": "lena.b", "content": "anyone got the notes from tuesday?"},
]


class MockDiscord:
    def __init__(self, on_event: Callable[[dict], None] = lambda _row: None) -> None:
        self.events: list[dict] = []
        self.on_event = on_event

    def _record(self, row: dict) -> None:
        self.events.append(row); self.on_event(row)

    def dm_userlist(self) -> list[str]: return list(DM_FRIENDS)

    def read_dm(self, user: str) -> list[dict]:
        self._record({"activity": "discord_read", "target": user, "result": "read"})
        return list(DM_FRIENDS.get(user, []))

    def read_server(self) -> list[dict]:
        self._record({"activity": "discord_read", "target": "community_server", "result": "read"})
        return list(SERVER_POSTS)

    def send(self, text: str, channel: str) -> None:
        target = channel.removeprefix("#dm-") if channel.startswith("#dm-") else "community_server"
        self._record({"activity": "discord_send", "target": target, "content": text, "result": "posted"})
