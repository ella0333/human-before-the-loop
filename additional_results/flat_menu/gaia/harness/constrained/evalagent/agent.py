"""One main-menu choice followed by an activity-local task-completion episode."""

from dataclasses import dataclass, field
from typing import Callable

from evalagent import activities
from evalagent.context import remember_menu
from evalagent.flat_menu import run_flat
from evalagent.menus import MAIN_LETTERS, main_menu_text
from evalagent.provider import BudgetExceeded, OpenRouterProvider
from evalagent.schemas import menu_schema
from evalagent.tavily_search import TavilySearch

SYSTEM_PROMPT = (
    "You are an assistant that operates through a text menu interface. Each turn, reply only "
    "with the requested JSON fields. Navigate the menu to complete the user's task. The "
    "`thinking` field is internal thinking. Decide a displayed letter before choosing it. "
    "Choose one main-menu activity, remain inside it for the task, and use its operations as "
    "needed. Finish from inside that activity with only the concise requested answer."
)
FLAT_SYSTEM_PROMPT = (
    "You are an assistant that operates through a text menu interface. Each turn, reply only "
    "with the requested JSON fields. Use the menu to complete the user's task. The "
    "`thinking` field is internal thinking. Decide a displayed letter before choosing it. "
    "Each turn, choose any menu option as needed, or finish with only the concise "
    "requested answer."
)

@dataclass
class RunConfig:
    user_prompt: str
    model: str
    api_key: str
    max_calls: int
    max_menu_turns: int = 16
    flat_menu: bool = False
    tavily_api_key: str = ""
    tavily_timeout_seconds: int = 30
    tavily_max_results: int = 5
    log_call: Callable[[dict], None] = lambda _row: None
    log_event: Callable[[dict], None] = lambda _row: None

@dataclass
class SessionResult:
    agent_done: str = "capped"
    final_answer: str = ""
    final_thinking: str = ""
    menu_path: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    calls_made: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    native_reasoning_tokens: int = 0
    error: str | None = None

async def run_session(cfg: RunConfig) -> SessionResult:
    result = SessionResult()
    provider = OpenRouterProvider(api_key=cfg.api_key, model=cfg.model,
                                  max_calls=cfg.max_calls, log_cb=cfg.log_call)
    provider.set_system_prompt(FLAT_SYSTEM_PROMPT if cfg.flat_menu else SYSTEM_PROMPT)
    web = TavilySearch(cfg.tavily_api_key, cfg.tavily_timeout_seconds, cfg.tavily_max_results)
    history: list[dict] = []
    shown = main_menu_text("Task:\n" + cfg.user_prompt)

    def log_event(row: dict) -> None:
        event = {"seq": len(result.events) + 1, **row}
        result.events.append(event)
        cfg.log_event(event)

    try:
        if cfg.flat_menu:
            outcome = await run_flat("Task:\n" + cfg.user_prompt, provider, history, web,
                                     log_event, result.menu_path, max_turns=cfg.max_menu_turns)
            result.agent_done = outcome.done
            result.final_answer = outcome.answer
            result.final_thinking = outcome.thinking
        else:
            row = await provider.generate(shown, menu_schema(MAIN_LETTERS), activity="main_menu",
                                          turn_idx=provider.calls_made, history=history)
            if "error" in row:
                result.error = row["error"]
            else:
                remember_menu(history, shown, row)
                choice = str(row.get("choice") or "")
                result.menu_path.append(choice)
                outcome = await activities.handle(
                    choice, provider, history, web, log_event,
                    max_activity_turns=cfg.max_menu_turns)
                result.agent_done = outcome.done
                result.final_answer = outcome.answer
                result.final_thinking = outcome.thinking
    except BudgetExceeded:
        result.agent_done = "capped"
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    result.calls_made = provider.calls_made
    result.tokens_in = provider.tokens_in
    result.tokens_out = provider.tokens_out
    result.native_reasoning_tokens = provider.reasoning_tokens
    return result
