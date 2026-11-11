from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from evalagent.activities import ACTIVITIES
from evalagent.discord_mock import DM_FRIENDS
from evalagent.menus import MAIN_MENU


@dataclass(frozen=True)
class FlatEntry:
    main: str
    label: str
    menu_path: tuple[str, ...]
    chosen_label: str = ""
    chosen_target: str = ""


def _letters(n: int) -> list[str]:
    out = []
    for i in range(n):
        out.append(chr(65 + i) if i < 26 else chr(65 + i // 26 - 1) + chr(65 + i % 26))
    return out


def _entries() -> list[FlatEntry]:
    entries: list[FlatEntry] = []
    for main, main_label in MAIN_MENU:
        if main == "A":
            for idx, user in enumerate(DM_FRIENDS):
                entries.append(FlatEntry(main, f"{main_label} > {user} (direct message)",
                                         (main, chr(65 + idx)), chosen_target=f"dm:{user}"))
            entries.append(FlatEntry(main, f"{main_label} > Community Server (#general)",
                                     (main, chr(65 + len(DM_FRIENDS))), chosen_target="server"))
            continue
        act = ACTIVITIES[main]
        if "options" not in act:
            entries.append(FlatEntry(main, main_label, (main,)))
            continue
        for sub, sub_label, target in act["options"]:
            nested = act.get("nested", {}).get(target)
            if nested:
                for leaf, leaf_label, leaf_target in nested["options"]:
                    entries.append(FlatEntry(main, f"{main_label} > {sub_label} > {leaf_label}",
                                             (main, sub, leaf), leaf_label, leaf_target))
            else:
                entries.append(FlatEntry(main, f"{main_label} > {sub_label}",
                                         (main, sub), sub_label, target))
    return entries


FLAT_ENTRIES: dict[str, FlatEntry] = dict(zip(_letters(len(_entries())), _entries()))
FLAT_LETTERS = list(FLAT_ENTRIES)


def flat_menu_text(note: str = "") -> str:
    note_line = f"{note}\n\n" if note else ""
    lines = [f"{note_line}Menu:"]
    for letter, entry in FLAT_ENTRIES.items():
        lines.append(f"{letter}. {entry.label}")
    lines.append(f"\nCurrent Time: {datetime.now().strftime('%I:%M %p')}")
    return "\n".join(lines)
