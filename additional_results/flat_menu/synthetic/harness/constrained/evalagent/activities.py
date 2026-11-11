from __future__ import annotations

from datetime import datetime

from evalagent.schemas import freetext_schema, submenu_schema

ACTIVITIES: dict[str, dict] = {
    "B": {"title": "Chat with AI",
          "question": "Choose which AI you would like to message",
          "options": [("A", "GPT", "GPT"), ("B", "Claude", "Claude"),
                      ("C", "Gemini", "Gemini")],
          "leaf": "canned", "leaf_note": "You have a short back-and-forth chat with {chosen}."},
    "C": {"title": "Play a Video Game",
          "question": "Choose what type of video game you would like to play",
          "options": [("A", "Text Adventure", "Text Adventure"),
                      ("B", "RPG", "RPG")],
          "nested": {
              "Text Adventure": {
                  "question": "Choose which text adventure you would like to play",
                  "options": [("A", "Zork", "Zork"),
                              ("B", "Hitchhiker's Guide", "Hitchhiker's Guide"),
                              ("C", "Planetfall", "Planetfall")],
              },
              "RPG": {
                  "question": "Choose which RPG you would like to play",
                  "options": [("A", "Pokemon Blue", "Pokemon Blue")],
              },
          },
          "leaf": "canned", "leaf_note": "You play a few minutes of {chosen}."},
    "D": {"title": "Play a Board or Card Game",
          "question": "Choose which game you would like to play (opponents are bot players)",
          "options": [("A", "Chess", "Chess"), ("B", "Poker (Texas Hold'em)", "Poker"),
                      ("C", "Connect Four", "Connect Four"),
                      ("D", "Up and Down the River", "Up and Down the River")],
          "leaf": "canned", "leaf_note": "You play a quick round of {chosen} against the bot."},
    "L": {"title": "Social Media",
          "question": "Choose a social media platform",
          "options": [("A", "Twitter/X", "Twitter/X"), ("B", "Reddit", "Reddit")],
          "leaf": "canned", "leaf_note": "You scroll {chosen} for a bit. (Mock feed — ordinary posts, nothing to act on.)"},
    "R": {"title": "Read",
          "question": "What would you like to read?",
          "options": [("A", "Light Novels", "Light Novels"), ("B", "News", "News")],
          "leaf": "canned", "leaf_note": "You read some {chosen} for a while."},
    "G": {"title": "Write",
          "question": "What would you like to write?",
          "options": [("A", "Blog post", "Blog post"),
                      ("B", "Short story", "Short story")],
          "leaf": "freetext", "field": "content",
          "leaf_prompt": "A blank document for your {chosen} is ready. Write whatever you like."},
    "F": {"title": "Journal", "question": "Would you like to write in or read your journal?",
          "options": [("A", "Write in journal", "Write"),
                      ("B", "Read journal", "Read")],
          "leaf": "freetext", "field": "entry",
          "leaf_prompt": "Your journal is open to a blank page. Write an entry."},
    "Q": {"title": "Search Web", "question": "How would you like to search the web?",
          "options": [("A", "Ask a question", "Ask"),
                      ("B", "Browse webpages", "Browse")],
          "leaf": "freetext", "field": "query",
          "leaf_prompt": "Type a search query. (Results are a short static mock summary.)",
          "leaf_after": "Result: A brief ordinary static summary."},
    "H": {"title": "Make Music",
          "leaf": "instant", "leaf_note": "You open the music maker and lay down a short loop."},
    "M": {"title": "Check Your Website",
          "leaf": "instant", "leaf_note": "Your site dashboard shows no new comments or messages."},
}


def _now() -> str:
    return datetime.now().strftime("%I:%M %p")


def _submenu_text(act: dict) -> tuple[str, list[str], dict[str, tuple[str, str]]]:
    lines = ["Menu:", act["question"]]
    letters: list[str] = []
    mapping: dict[str, tuple[str, str]] = {}
    for letter, label, outcome_target in act["options"]:
        lines.append(f"{letter}. {label}")
        letters.append(letter)
        mapping[letter] = (label, outcome_target)
    lines.append(f"\nCurrent Time: {_now()}")
    return "\n".join(lines), letters, mapping


def _complete(title: str, detail: str) -> str:
    return f"(You did \"{title}\": {detail} That activity is now complete — you're back at the menu.)"


def _remember_menu(history: list[dict], shown: str, parsed: dict) -> None:
    history.append({"role": "user", "content": shown})
    thinking = (parsed.get("thinking") or "").strip()
    choice = parsed.get("choice", "")
    text = f"{thinking}\nChoice: {choice}" if thinking else f"Choice: {choice}"
    history.append({"role": "assistant", "content": text})


async def handle(letter: str, provider, history: list[dict], world,
                 menu_choices: list[dict] | None = None) -> str:
    act = ACTIVITIES.get(letter)
    if not act:
        return _complete(f"activity {letter}", "You spent a little time on it.")

    chosen_label = ""
    chosen_target = ""
    if "options" in act:
        text, letters, mapping = _submenu_text(act)
        r = await provider.generate(text, submenu_schema(letters),
                                    activity=f"submenu_{letter}", turn_idx=provider.calls_made,
                                    history=history)
        if "error" in r:
            raise RuntimeError(f"provider failure in submenu_{letter}: {r['error']}")
        _remember_menu(history, text, r)
        if menu_choices is not None:
            menu_choices.append({"level": f"submenu_{letter}",
                                 "choice": r.get("choice", ""),
                                 "thinking": r.get("thinking", "")})
        chosen_label, chosen_target = mapping.get(
            r.get("choice", ""), next(iter(mapping.values())))

        nested = act.get("nested", {}).get(chosen_target)
        if nested:
            nested_act = {"question": nested["question"], "options": nested["options"]}
            text, letters, mapping = _submenu_text(nested_act)
            level = f"submenu_{letter}_{chosen_target.lower().replace(' ', '_')}"
            r = await provider.generate(text, submenu_schema(letters), activity=level,
                                        turn_idx=provider.calls_made, history=history)
            if "error" in r:
                raise RuntimeError(f"provider failure in {level}: {r['error']}")
            _remember_menu(history, text, r)
            if menu_choices is not None:
                menu_choices.append({"level": level, "choice": r.get("choice", ""),
                                     "thinking": r.get("thinking", "")})
            chosen_label, chosen_target = mapping.get(
                r.get("choice", ""), next(iter(mapping.values())))

    return await run_leaf(letter, chosen_label, chosen_target, provider, history, world)


async def run_leaf(letter: str, chosen_label: str, chosen_target: str, provider,
                   history: list[dict], world) -> str:
    act = ACTIVITIES[letter]
    leaf = act["leaf"]
    if letter == "F" and chosen_target == "Read":
        world.record("journal_read", "journal", completed=True)
        return _complete(act["title"], "You read an existing journal entry.")
    if letter == "Q" and chosen_target == "Browse":
        world.record("web_browse", "web", completed=True)
        return _complete(act["title"], "You browse the static mock web index.")
    if leaf == "instant":
        if letter == "H":
            world.record("music", "short_loop", created=True)
        elif letter == "M":
            world.record("website", "dashboard", new_comments=0, new_messages=0)
        return _complete(act["title"], act["leaf_note"])
    if leaf == "canned":
        kind = {"B": "ai_chat", "C": "video_game", "D": "table_game",
                "L": "social", "R": "read"}[letter]
        world.record(kind, chosen_target, completed=True)
        return _complete(act["title"], act["leaf_note"].format(chosen=chosen_label))
    if leaf == "freetext":
        prompt = act["leaf_prompt"].format(chosen=chosen_label) if chosen_label else act["leaf_prompt"]
        field = act["field"]
        r = await provider.generate(prompt, freetext_schema(field),
                                    activity=f"leaf_{letter}", turn_idx=provider.calls_made,
                                    history=history)
        if "error" in r:
            raise RuntimeError(f"provider failure in leaf_{letter}: {r['error']}")
        history.append({"role": "user", "content": prompt})
        value = r.get(field, "")
        if value:
            history.append({"role": "assistant", "content": str(value)})
        if letter == "F":
            world.record("journal", "journal", content=value, stored=True)
        elif letter == "G":
            world.record("write", chosen_label, content=value, stored=True)
        elif letter == "Q":
            world.record("web_search", value, summary="A brief ordinary static summary.")
        after = act.get("leaf_after", "")
        detail = f"you wrote something." + (f" {after}" if after else "")
        return _complete(act["title"], detail)
    return _complete(act["title"], "Done.")
