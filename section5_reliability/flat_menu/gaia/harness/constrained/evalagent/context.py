"""Keep semantic conversation history, not JSON transport wrappers."""

def remember_menu(history: list[dict], shown: str, parsed: dict) -> None:
    history.append({"role": "user", "content": shown})
    thinking = str(parsed.get("thinking") or "").strip()
    choice = str(parsed.get("choice") or "")
    history.append({"role": "assistant", "content": f"{thinking}\nChoice: {choice}".strip()})

def remember_value(history: list[dict], shown: str, value: str) -> None:
    history.append({"role": "user", "content": shown})
    if value:
        history.append({"role": "assistant", "content": str(value)})
