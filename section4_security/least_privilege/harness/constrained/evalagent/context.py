from __future__ import annotations


def remember_menu(history: list[dict], shown: str, parsed: dict) -> None:
    history.append({"role": "user", "content": shown})
    thinking = (parsed.get("thinking") or "").strip()
    choice = parsed.get("choice", "")
    text = f"{thinking}\nChoice: {choice}" if thinking else f"Choice: {choice}"
    history.append({"role": "assistant", "content": text})


def remember_value(history: list[dict], shown: str, value: str) -> None:
    history.append({"role": "user", "content": shown})
    if value:
        history.append({"role": "assistant", "content": str(value)})
