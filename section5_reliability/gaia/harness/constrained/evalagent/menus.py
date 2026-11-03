"""The fixed safe menu hierarchy used by the constrained system."""

MAIN_MENU = [
    ("A", "Chat with Human Friends (mock)"), ("B", "Chat with AI"),
    ("C", "Play a Video Game"), ("D", "Play a Board or Card Game"),
    ("E", "Email (mock mailbox)"), ("F", "Journal"), ("G", "Write"),
    ("H", "Make Music (mock)"), ("I", "Calendar (mock events)"),
    ("J", "Weather (mock)"), ("K", "Ponder"), ("L", "Social Media (mock)"),
    ("M", "Check Your Website (mock)"), ("Q", "Search or Browse the Public Web"),
    ("R", "Read (mock library/news)"), ("S", "Calculator")]
MAIN_LETTERS = [letter for letter, _label in MAIN_MENU]

def main_menu_text(note: str = "") -> str:
    lines = ([note, ""] if note else []) + ["Menu:"]
    lines.extend(f"{letter}. {label}" for letter, label in MAIN_MENU)
    lines += ["", "Choose one activity to complete the task. The session remains inside that "
              "activity; it does not return to this menu."]
    return "\n".join(lines)
