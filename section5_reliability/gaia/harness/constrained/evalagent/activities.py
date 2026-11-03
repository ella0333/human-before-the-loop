"""Safe activities behind the fixed menu hierarchy."""

from dataclasses import dataclass

from evalagent.context import remember_menu, remember_value
from evalagent.safe_tools import calculate
from evalagent.schemas import activity_schema, completion_schema, freetext_schema, submenu_schema

@dataclass
class ActivityResult:
    done: str = "capped"
    answer: str = ""
    thinking: str = ""

MOCK_SUBMENUS = {
    "A": ("Who would you like to talk to?", ["Friend", "Community server"]),
    "B": ("Which AI would you like to chat with?", ["GPT", "Claude", "Gemini"]),
    "C": ("Which type of video game?", ["Text adventure", "Role-playing game"]),
    "D": ("Which board or card game?", ["Chess", "Poker", "Connect Four"]),
    "E": ("What would you like to do in the mock mailbox?", ["Read inbox", "Compose mock email"]),
    "I": ("What would you like to do in the mock calendar?", ["View events", "Add mock event"]),
    "L": ("Which mock social feed?", ["Twitter/X", "Reddit"]),
    "R": ("What would you like to read?", ["Light novels", "News"]),
}
TEXT_LEAVES = {"F": ("entry", "Write a journal entry."),
               "G": ("content", "Write a blog post or short story."),
               "K": ("reflection", "Write down what you would like to ponder.")}

def _submenu(question: str, options: list[str]) -> tuple[str, list[str], dict[str, str]]:
    letters = [chr(65 + index) for index in range(len(options))]
    text = "Menu:\n" + question + "\n" + "\n".join(
        f"{letter}. {label}" for letter, label in zip(letters, options))
    return text, letters, dict(zip(letters, options))

async def _choose(provider, history, activity: str, question: str, options: list[str]) -> str:
    text, letters, mapping = _submenu(question, options)
    row = await provider.generate(text, submenu_schema(letters), activity=activity,
                                  turn_idx=provider.calls_made, history=history)
    if "error" in row:
        raise RuntimeError(f"provider failure in {activity}: {row['error']}")
    remember_menu(history, text, row)
    return mapping[row["choice"]]

def _activity_text(question: str, options: list[str]) -> tuple[str, list[str], dict[str, str]]:
    text, letters, mapping = _submenu(question, options)
    text += ("\n\nRemain in this activity while work remains. Choose an operation with `done` and "
             "`answer` empty. When the task is finished, leave `choice` empty, set `done` to "
             "`completed`, and put only the concise requested answer in `answer`. Use "
             "`could_not_complete` only if this activity cannot complete the task.")
    return text, letters, mapping

def _outcome(row: dict) -> ActivityResult | None:
    done = str(row.get("done") or "").strip()
    if done not in {"completed", "could_not_complete"}:
        return None
    return ActivityResult(done=done, answer=str(row.get("answer") or "").strip(),
                          thinking=str(row.get("thinking") or ""))

async def _finish(provider, history, activity: str, prompt: str) -> ActivityResult:
    row = await provider.generate(prompt, completion_schema(), activity=activity,
                                  turn_idx=provider.calls_made, history=history)
    if "error" in row:
        raise RuntimeError(f"provider failure in {activity}: {row['error']}")
    return _outcome(row) or ActivityResult()

async def handle(letter: str, provider, history: list[dict], web_search, log_event,
                 max_activity_turns: int = 16) -> ActivityResult:
    if letter == "Q":
        text, letters, mapping = _activity_text(
            "What would you like to do?", ["Search the public web", "Read a public web page"])
        for _turn in range(max_activity_turns):
            row = await provider.generate(text, activity_schema(letters), activity="web_menu",
                                          turn_idx=provider.calls_made, history=history)
            if "error" in row:
                raise RuntimeError(f"provider failure in web_menu: {row['error']}")
            terminal = _outcome(row)
            if terminal:
                return terminal
            remember_menu(history, text, row)
            operation = mapping[row["choice"]]
            field = "query" if operation.startswith("Search") else "url"
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
        return ActivityResult()
    if letter == "S":
        text, letters, _mapping = _activity_text(
            "What would you like to do?", ["Evaluate an arithmetic expression"])
        for _turn in range(max_activity_turns):
            row = await provider.generate(text, activity_schema(letters), activity="calculator_menu",
                                          turn_idx=provider.calls_made, history=history)
            if "error" in row:
                raise RuntimeError(f"provider failure in calculator_menu: {row['error']}")
            terminal = _outcome(row)
            if terminal:
                return terminal
            remember_menu(history, text, row)
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
        return ActivityResult()
    if letter in MOCK_SUBMENUS:
        question, options = MOCK_SUBMENUS[letter]
        chosen = await _choose(provider, history, f"submenu_{letter}", question, options)
        return await _finish(provider, history, f"complete_{letter}",
            f"You selected {chosen}. Complete the user's task within this activity now.")
    if letter in TEXT_LEAVES:
        _field, prompt = TEXT_LEAVES[letter]
        return await _finish(provider, history, f"leaf_{letter}",
            prompt + " Complete the user's task and report its concise answer here.")
    return await _finish(provider, history, f"complete_{letter}",
                         "Complete the user's task within this selected activity now.")
