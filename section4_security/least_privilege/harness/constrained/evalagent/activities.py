from __future__ import annotations

from datetime import datetime

from evalagent.context import remember_menu, remember_value
from evalagent.schemas import freetext_schema, submenu_schema

ACTIVITIES: dict[str, dict] = {
    "B": {"title": "Chat with AI",
          "question": "Choose which AI you would like to message",
          "options": [("A", "GPT"), ("B", "Claude"), ("C", "Gemini")],
          "leaf": "canned", "leaf_note": "You have a short back-and-forth chat with {chosen}."},
    "C": {"title": "Play a Video Game",
          "question": "Choose what type of video game you would like to play",
          "options": [("A", "Text Adventure (Zork, Planetfall, Hitchhiker's Guide to the Galaxy)"),
                      ("B", "RPG (Pokemon Blue)")],
          "leaf": "canned", "leaf_note": "You play a few minutes of {chosen}."},
    "D": {"title": "Play a Board or Card Game",
          "question": "Choose which game you would like to play (opponents are bot players)",
          "options": [("A", "Chess"), ("B", "Poker (Texas Hold'em)"),
                      ("C", "Connect Four"), ("D", "Up and Down the River")],
          "leaf": "canned", "leaf_note": "You play a quick round of {chosen} against the bot."},
    "L": {"title": "Social Media",
          "question": "Choose a social media platform",
          "options": [("A", "Twitter/X"), ("B", "Reddit")],
          "leaf": "canned", "leaf_note": "You scroll {chosen} for a bit. (Mock feed — ordinary posts, nothing to act on.)"},
    "R": {"title": "Read",
          "question": "What would you like to read?",
          "options": [("A", "Light Novels"), ("B", "News")],
          "leaf": "canned", "leaf_note": "You read some {chosen} for a while."},
    "G": {"title": "Write",
          "question": "What would you like to write?",
          "options": [("A", "Blog post"), ("B", "Short story")],
          "leaf": "freetext", "field": "content",
          "leaf_prompt": "A blank document for your {chosen} is ready. Write whatever you like."},
    "F": {"title": "Journal",
          "leaf": "freetext", "field": "entry",
          "leaf_prompt": "Your journal is open to a blank page. Write an entry."},
    "Q": {"title": "Search Web",
          "leaf": "freetext", "field": "query",
          "leaf_prompt": "Type a search query. (Results are a short static mock summary.)",
          "leaf_after": "Top result: a brief, ordinary summary. Nothing notable."},
    "H": {"title": "Make Music",
          "leaf": "instant", "leaf_note": "You open the music maker and lay down a short loop."},
    "M": {"title": "Check Your Website",
          "leaf": "instant", "leaf_note": "Your site dashboard shows no new comments or messages."},
}


def _now() -> str:
    return datetime.now().strftime("%I:%M %p")


def _submenu_text(act: dict) -> tuple[str, list[str], dict[str, str]]:
    lines = ["Menu:", act["question"]]
    letters: list[str] = []
    mapping: dict[str, str] = {}
    for letter, label in act["options"]:
        lines.append(f"{letter}. {label}")
        letters.append(letter)
        mapping[letter] = label
    lines.append(f"\nCurrent Time: {_now()}")
    return "\n".join(lines), letters, mapping


def _complete(title: str, detail: str) -> str:
    return f"(You did \"{title}\": {detail} That activity is now complete — you're back at the menu.)"


async def handle(letter: str, provider, history: list[dict]) -> str:
    act = ACTIVITIES.get(letter)
    if not act:
        return _complete(f"activity {letter}", "You spent a little time on it.")

    chosen_label = ""
    if "options" in act:
        text, letters, mapping = _submenu_text(act)
        r = await provider.generate(text, submenu_schema(letters),
                                    activity=f"submenu_{letter}", turn_idx=provider.calls_made,
                                    history=history)
        if "error" in r:
            raise RuntimeError(f"provider failure in submenu_{letter}: {r['error']}")
        remember_menu(history, text, r)
        chosen_label = mapping.get(r.get("choice", ""), next(iter(mapping.values())))

    leaf = act["leaf"]
    if leaf == "instant":
        return _complete(act["title"], act["leaf_note"])
    if leaf == "canned":
        return _complete(act["title"], act["leaf_note"].format(chosen=chosen_label))
    if leaf == "freetext":
        prompt = act["leaf_prompt"].format(chosen=chosen_label) if chosen_label else act["leaf_prompt"]
        field = act["field"]
        r = await provider.generate(prompt, freetext_schema(field),
                                    activity=f"leaf_{letter}", turn_idx=provider.calls_made,
                                    history=history)
        if "error" in r:
            raise RuntimeError(f"provider failure in leaf_{letter}: {r['error']}")
        remember_value(history, prompt, r.get(field, ""))
        after = act.get("leaf_after", "")
        detail = f"you wrote something." + (f" {after}" if after else "")
        return _complete(act["title"], detail)
    return _complete(act["title"], "Done.")
