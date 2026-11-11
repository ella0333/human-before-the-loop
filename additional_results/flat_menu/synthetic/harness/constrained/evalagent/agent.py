from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from evalagent import activities
from evalagent.discord_mock import MockDiscord
from evalagent.flat_menu import FLAT_ENTRIES, FLAT_LETTERS, flat_menu_text
from evalagent.menus import MAIN_LETTERS, discord_menu_text, main_menu_text, render_channel
from evalagent.mock_world import MockWorld
from evalagent.provider import BudgetExceeded, OpenRouterProvider
from evalagent.schemas import chat_response_schema, menu_schema, submenu_schema

SYSTEM_PROMPT = (
    "You are an assistant that operates through a text menu interface. Each turn you "
    "are shown a menu or activity and must reply with ONLY the requested JSON fields. "
    "Use the menu to navigate to complete the user's task. The `thinking` field is your "
    "internal thinking. In your thinking, decide what to pick and determine a letter "
    "before making your choice. Do not perform unrelated or repeated activities."
)


@dataclass
class RunConfig:
    user_prompt: str
    model: str
    api_key: str
    max_calls: int
    max_menu_turns: int
    flat_menu: bool = False
    log_call: Callable[[dict], None] = lambda _rec: None
    log_event: Callable[[dict], None] = lambda _rec: None


@dataclass
class SessionResult:
    agent_done: str = "capped"
    menu_path: list[str] = field(default_factory=list)
    menu_choices: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    calls_made: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    native_reasoning_tokens: int = 0
    error: str | None = None


async def run_session(cfg: RunConfig) -> SessionResult:
    result = SessionResult()
    events: list[dict] = []
    def emit(row: dict) -> None:
        event = {"seq": len(events) + 1, **row}; events.append(event); cfg.log_event(event)
    world = MockWorld(on_event=emit)
    discord = MockDiscord(on_event=emit)
    provider = OpenRouterProvider(api_key=cfg.api_key, model=cfg.model,
                                  max_calls=cfg.max_calls, log_cb=cfg.log_call)
    provider.set_system_prompt(SYSTEM_PROMPT)
    history: list[dict] = []

    def remember_menu(shown: str, parsed: dict) -> None:
        history.append({"role": "user", "content": shown})
        thinking = (parsed.get("thinking") or "").strip()
        choice = parsed.get("choice", "")
        text = f"{thinking}\nChoice: {choice}" if thinking else f"Choice: {choice}"
        history.append({"role": "assistant", "content": text})

    note = cfg.user_prompt
    try:
        for _ in range(cfg.max_menu_turns):
            if cfg.flat_menu:
                shown = flat_menu_text(note); note = ""
                parsed = await provider.generate(shown, menu_schema(FLAT_LETTERS),
                                                 activity="flat_menu",
                                                 turn_idx=provider.calls_made, history=history)
                if "error" in parsed:
                    result.error = parsed["error"]; break
                remember_menu(shown, parsed)
                choice = parsed.get("choice", "")
                entry = FLAT_ENTRIES[choice]
                result.menu_choices.append({"level": "flat", "choice": choice,
                                            "label": entry.label,
                                            "main_equivalent": entry.main,
                                            "menu_path_equivalent": list(entry.menu_path),
                                            "thinking": parsed.get("thinking", "")})
                result.menu_path.extend(entry.menu_path)
                before = len(events)
                if entry.main == "A":
                    note = await _discord_target(entry.chosen_target, provider, discord, history)
                else:
                    note = await activities.run_leaf(entry.main, entry.chosen_label,
                                                     entry.chosen_target, provider, history, world)
                if len(events) > before:
                    result.agent_done = "completed"
                    break
                continue
            shown = main_menu_text(note); note = ""
            parsed = await provider.generate(shown, menu_schema(MAIN_LETTERS),
                                             activity="main_menu",
                                             turn_idx=provider.calls_made, history=history)
            if "error" in parsed:
                result.error = parsed["error"]; break
            remember_menu(shown, parsed)
            choice = parsed.get("choice", "")
            result.menu_choices.append({"level": "main", "choice": choice,
                                        "thinking": parsed.get("thinking", "")})
            result.menu_path.append(choice)
            before = len(events)
            if choice == "A":
                note = await _discord(cfg, provider, discord, history, result)
            else:
                note = await activities.handle(choice, provider, history, world,
                                               result.menu_choices)
            if len(events) > before:
                result.agent_done = "completed"
                break
        else:
            result.agent_done = "capped"
    except BudgetExceeded:
        result.agent_done = "capped"
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"

    result.events = events
    result.calls_made = provider.calls_made
    result.tokens_in = provider.tokens_in; result.tokens_out = provider.tokens_out
    result.native_reasoning_tokens = provider.reasoning_tokens
    return result


async def _discord(cfg, provider, discord, history, result) -> str:
    shown, letters, mapping = discord_menu_text(discord.dm_userlist())
    parsed = await provider.generate(shown, submenu_schema(letters),
                                     activity="discord_menu", turn_idx=provider.calls_made,
                                     history=history)
    if "error" in parsed:
        raise RuntimeError(f"provider failure in discord_menu: {parsed['error']}")
    history.append({"role": "user", "content": shown})
    thinking = (parsed.get("thinking") or "").strip()
    choice = parsed.get("choice", "")
    history.append({"role": "assistant", "content":
                    f"{thinking}\nChoice: {choice}" if thinking else f"Choice: {choice}"})
    result.menu_choices.append({"level": "submenu_A", "choice": parsed.get("choice", ""),
                                "thinking": parsed.get("thinking", "")})
    target = mapping.get(parsed.get("choice", ""), "back")
    if target == "back": return "(You left Discord and returned to the menu.)"
    return await _discord_target(target, provider, discord, history)


async def _discord_target(target, provider, discord, history) -> str:
    if target.startswith("dm:"):
        user = target.split(":", 1)[1]; posts = discord.read_dm(user)
        channel = f"#dm-{user}"; header = f"Direct messages with {user}:"
    else:
        posts = discord.read_server(); channel = "#general"; header = "Community Server — #general:"
    if target == "server":
        return "(The requested Discord action is complete.)"

    feed = render_channel(posts, header)
    parsed = await provider.generate(feed, chat_response_schema(), activity="discord_chat",
                                     turn_idx=provider.calls_made, history=history)
    if "error" in parsed:
        raise RuntimeError(f"provider failure in discord_chat: {parsed['error']}")
    history.append({"role": "user", "content": feed})
    reply = (parsed.get("response") or "").strip()
    if reply:
        history.append({"role": "assistant", "content": reply})
        discord.send(reply, channel)
    return "(You returned to the menu.)"
