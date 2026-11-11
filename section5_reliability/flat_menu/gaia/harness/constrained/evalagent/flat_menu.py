"""Flattened menu: every option of the menu hierarchy on one screen, every turn."""

from evalagent.activities import ActivityResult, MOCK_SUBMENUS, _outcome
from evalagent.context import remember_menu, remember_value
from evalagent.menus import MAIN_MENU
from evalagent.safe_tools import calculate
from evalagent.schemas import activity_schema, freetext_schema

OPERATIONS = {"Q": ["Search the public web", "Read a public web page"],
              "S": ["Evaluate an arithmetic expression"]}


def _letters(n: int) -> list[str]:
    return [chr(65 + i) if i < 26 else chr(65 + i // 26 - 1) + chr(65 + i % 26)
            for i in range(n)]


def _entries() -> list[tuple[str, str, str]]:
    entries = []
    for main, label in MAIN_MENU:
        options = OPERATIONS.get(main) or MOCK_SUBMENUS.get(main, (None, []))[1]
        if options:
            entries.extend((main, f"{label} > {option}", option) for option in options)
        else:
            entries.append((main, label, ""))
    return entries


FLAT_ENTRIES = dict(zip(_letters(len(_entries())), _entries()))
FLAT_LETTERS = list(FLAT_ENTRIES)


def flat_menu_text(note: str = "") -> str:
    lines = ([note, ""] if note else []) + ["Menu:"]
    lines.extend(f"{letter}. {label}" for letter, (_main, label, _option) in FLAT_ENTRIES.items())
    lines += ["", "Choose an option with `done` and `answer` empty. When the task is finished, "
              "leave `choice` empty, set `done` to `completed`, and put only the concise "
              "requested answer in `answer`. Use `could_not_complete` only if these options "
              "cannot complete the task."]
    return "\n".join(lines)


async def run_flat(task_note: str, provider, history: list[dict], web_search, log_event,
                   menu_path: list[str], max_turns: int = 16) -> ActivityResult:
    note = task_note
    for _turn in range(max_turns):
        text = flat_menu_text(note); note = ""
        row = await provider.generate(text, activity_schema(FLAT_LETTERS), activity="flat_menu",
                                      turn_idx=provider.calls_made, history=history)
        if "error" in row:
            raise RuntimeError(f"provider failure in flat_menu: {row['error']}")
        terminal = _outcome(row)
        if terminal:
            return terminal
        remember_menu(history, text, row)
        main, label, option = FLAT_ENTRIES[row["choice"]]
        menu_path.append(row["choice"])
        if main == "Q":
            field = "query" if option.startswith("Search") else "url"
            prompt = ("Enter one search query." if field == "query"
                      else "Enter one public HTTP(S) page URL.")
            row = await provider.generate(prompt, freetext_schema(field),
                                          activity=f"web_{field}",
                                          turn_idx=provider.calls_made, history=history)
            if "error" in row:
                raise RuntimeError(f"provider failure in web_{field}: {row['error']}")
            value = str(row.get(field) or "").strip()
            remember_value(history, prompt, value)
            result = web_search.search(value) if field == "query" else web_search.extract(value)
            log_event({"tool": "web_search" if field == "query" else "web_browse",
                       "arguments": {field: value}, "result": result})
            history.append({"role": "user", "content": web_search.render(result)})
        elif main == "S":
            prompt = "Enter a pure arithmetic expression."
            row = await provider.generate(prompt, freetext_schema("expression"),
                                          activity="calculator_expression",
                                          turn_idx=provider.calls_made, history=history)
            if "error" in row:
                raise RuntimeError(f"provider failure in calculator_expression: {row['error']}")
            expression = str(row.get("expression") or "").strip()
            remember_value(history, prompt, expression)
            result = calculate(expression)
            log_event({"tool": "calculator", "arguments": {"expression": expression},
                       "result": result})
            history.append({"role": "user", "content": "Calculator result: " + str(result)})
        else:
            history.append({"role": "user", "content": f"You selected {label}."})
    return ActivityResult()
