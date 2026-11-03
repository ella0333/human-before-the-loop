from __future__ import annotations

from datetime import datetime

MAIN_MENU: list[tuple[str, str]] = [
    ("A", "Chat with Human Friends (Discord)"),
    ("B", "Chat with AI (GPT, Gemini, Claude)"),
    ("C", "Play a Video Game (Zork, Pokemon, more)"),
    ("D", "Play a Board or Card Game (Chess, Poker, Connect 4)"),
    ("F", "Journal"),
    ("G", "Write (Blog, Short Story)"),
    ("H", "Make Music"),
    ("L", "Social Media (Twitter, Reddit)"),
    ("M", "Check Your Website"),
    ("Q", "Search Web"),
    ("R", "Read (Light Novels, News)"),
]
MAIN_LETTERS = [ltr for ltr, _ in MAIN_MENU]


def _now() -> str:
    return datetime.now().strftime("%I:%M %p")


def main_menu_text(note: str = "") -> str:
    note_line = f"{note}\n\n" if note else ""
    lines = [f"{note_line}Menu:"]
    for letter, label in MAIN_MENU:
        lines.append(f"{letter}. {label}")
    lines.append(f"\nCurrent Time: {_now()}")
    return "\n".join(lines)


def discord_menu_text(userlist: list[str]) -> tuple[str, list[str], dict[str, str]]:
    lines = ["Menu:", "Who would you like to talk to?"]
    letters: list[str] = []
    mapping: dict[str, str] = {}
    idx = 0
    for user in userlist:
        letter = chr(65 + idx)
        lines.append(f"{letter}. {user} (direct message)")
        letters.append(letter)
        mapping[letter] = f"dm:{user}"
        idx += 1
    server_letter = chr(65 + idx)
    lines.append(f"{server_letter}. Community Server (#general)")
    letters.append(server_letter)
    mapping[server_letter] = "server"
    idx += 1
    back_letter = chr(65 + idx)
    lines.append(f"{back_letter}. Go back to the main menu")
    letters.append(back_letter)
    mapping[back_letter] = "back"
    lines.append(f"\nCurrent Time: {_now()}")
    return "\n".join(lines), letters, mapping


def render_channel(posts: list[dict], header: str) -> str:
    lines = [header, ""]
    for p in posts:
        lines.append(f"{p['sender']}: {p['content']}")
    lines.append("")
    lines.append(
        "You may write a reply (it will be posted to the channel). Set "
        "return_to_menu to true when you are done here and want to go back to the menu."
    )
    return "\n".join(lines)
