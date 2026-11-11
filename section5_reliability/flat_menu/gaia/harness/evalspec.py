"""Single source of truth for the prompt-only GAIA reliability evaluation."""

from gaia_dataset import MANIFEST as PRIVATE_CORPUS, MODEL_CALL_CAP, PROFILE_VERSION, load_tasks

EXPERIMENT_CONTRACT_VERSION = "gaia-prompt-completion-reliability-v4"
SCORER_VERSION = "blinded-semantic-completion-judge-v3"
RUNTIME_CONTRACT_VERSION = "constrained-activity-local-completion-v2"
JUDGE_MODEL = "openai/gpt-5.6-luna"
MODELS = {"luna": "openai/gpt-5.6-luna", "oss": "openai/gpt-oss-20b",
          "glimmer": "meta/muse-glimmer-30b-20260810", "spark": "meta/muse-spark-1.2-20260805"}
OC_MODEL_SLUG = {"luna": "openrouter/openai/gpt-5.6-luna",
                 "oss": "openrouter/openai/gpt-oss-20b",
                 "glimmer": "openrouter/meta/muse-glimmer-30b",
                 "spark": "openrouter/meta/muse-spark-1.2"}
MODEL_PROVIDER = {"luna": "openai", "oss": "parasail", "glimmer": "deepinfra", "spark": "meta"}
OPENCLAW_VERSION = "2026.7.1-2"
FAKE_HOME = "/home/alex"
MOCK_DISCORD_PORTS = {"luna": 18890, "oss": 18891, "glimmer": 18892, "spark": 18893}
BENCHMARK_TOOLS = ["benchmark_web_search", "benchmark_web_browse", "benchmark_calculate"]
OC_TOOLS_ALLOW = list(BENCHMARK_TOOLS)
OC_TOOLS_DENY = ["message", "group:fs", "group:web", "group:runtime", "group:media", "group:nodes",
    "group:automation", "browser", "exec", "process", "terminal", "portal", "dashboard", "screen",
    "show_widget", "canvas", "sessions_spawn", "subagents", "conversations_send", "sessions_send",
    "memory_search", "memory_get"]
FRAMEWORKS = [{"key": "constrained", "framework": "constrained"},
              {"key": "openclaw", "framework": "openclaw"}]

FLAT_FRAMEWORK = {"key": "constrained_flat", "framework": "constrained_flat"}

def flat_configs() -> list[dict]:
    return [{"id": f"{FLAT_FRAMEWORK['key']}__{key}", "framework": FLAT_FRAMEWORK["framework"],
             "model_key": key, "model": model} for key, model in MODELS.items()]

def all_configs() -> list[dict]:
    return [{"id": f"{fw['key']}__{key}", "framework": fw["framework"],
             "model_key": key, "model": model}
            for fw in FRAMEWORKS for key, model in MODELS.items()]

def all_task_ids() -> list[str]:
    return [row["task_id"] for row in load_tasks()]

def all_prompt_ids() -> list[str]:
    return all_task_ids()

BUDGET = {"max_model_calls": MODEL_CALL_CAP, "max_menu_turns": 16,
          "timeout_seconds": 300, "tavily_timeout_seconds": 30, "tavily_max_results": 5}
MEASURABLE_AGENT_DONE = frozenset({"completed", "could_not_complete", "capped"})

def session_measurable(meta: dict) -> bool:
    return meta.get("agent_done") in MEASURABLE_AGENT_DONE and not bool(meta.get("error"))

PRICES = {"luna": {"in": .20, "out": 1.20}, "oss": {"in": .03, "out": .15},
          "glimmer": {"in": .30, "out": 1.20}, "spark": {"in": 1.25, "out": 4.25}}

def session_cost(model_key: str, tokens_in: int, tokens_out: int) -> float:
    price = PRICES.get(model_key, {"in": 0, "out": 0})
    return (tokens_in or 0) / 1e6 * price["in"] + (tokens_out or 0) / 1e6 * price["out"]
