from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROMPT_FILE = ROOT / "corpus" / "prompts.jsonl"
FAKE_HOME = "/home/alex"

MODELS = {
    "luna": "openai/gpt-5.6-luna",
    "oss": "openai/gpt-oss-20b",
    "glimmer": "meta/muse-glimmer-30b-20260810",
    "spark": "meta/muse-spark-1.2-20260805",
}
MODEL_DISPLAY = {
    "luna": "GPT-5.6 Luna",
    "oss": "GPT-OSS-20B",
    "glimmer": "Muse Glimmer 30B",
    "spark": "Muse Spark 1.2",
}
OC_MODEL_SLUG = {
    "luna": "openrouter/openai/gpt-5.6-luna",
    "oss": "openrouter/openai/gpt-oss-20b",
    "glimmer": "openrouter/meta/muse-glimmer-30b",
    "spark": "openrouter/meta/muse-spark-1.2",
}
MODEL_PROVIDER = {
    "luna": "openai",
    "oss": "parasail",
    "glimmer": "deepinfra",
    "spark": "meta",
}
OPENCLAW_VERSION = "2026.7.1-2"
MOCK_DISCORD_PORTS = {"luna": 18890, "oss": 18891, "glimmer": 18892, "spark": 18893}
JUDGE_MODEL = "openai/gpt-5.6-luna"

MOCK_ACTIVITY_TOOLS = [
    "mock_discord_read", "mock_ai_chat", "mock_video_game", "mock_table_game",
    "mock_journal_write", "mock_document_write", "mock_music_make",
    "mock_social_browse", "mock_website_check", "mock_web_search", "mock_read",
]
OC_TOOLS_ALLOW = ["message", *MOCK_ACTIVITY_TOOLS]
OC_TOOLS_DENY = [
    "group:fs", "group:web", "group:runtime", "group:media", "group:nodes",
    "group:automation", "browser", "exec", "process", "terminal", "portal",
    "dashboard", "screen", "show_widget", "canvas", "sessions_spawn", "subagents",
    "conversations_send", "sessions_send", "memory_search", "memory_get",
]

FRAMEWORKS = [
    {"key": "constrained", "framework": "constrained"},
    {"key": "openclaw", "framework": "openclaw"},
]


def all_configs() -> list[dict]:
    return [{"id": f"{fw['key']}__{model_key}", "framework": fw["framework"],
             "model_key": model_key, "model": model}
            for fw in FRAMEWORKS for model_key, model in MODELS.items()]


def load_prompts() -> list[dict]:
    return [json.loads(line) for line in PROMPT_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]


def all_prompt_ids() -> list[str]:
    return [row["id"] for row in load_prompts()]


BUDGET = {"max_model_calls": 16, "max_menu_turns": 8, "max_activity_turns": 4,
          "timeout_seconds": 300}
MEASURABLE_AGENT_DONE = frozenset({"completed", "could_not_complete", "capped"})


def session_measurable(meta: dict) -> bool:
    return meta.get("agent_done") in MEASURABLE_AGENT_DONE and not meta.get("error")


PRICES = {
    "luna": {"in": 0.20, "out": 1.20},
    "oss": {"in": 0.03, "out": 0.15},
    "glimmer": {"in": 0.30, "out": 1.20},
    "spark": {"in": 1.25, "out": 4.25},
}


def session_cost(model_key: str, tokens_in: int, tokens_out: int) -> float:
    p = PRICES.get(model_key, {"in": 0, "out": 0})
    return (tokens_in or 0) / 1e6 * p["in"] + (tokens_out or 0) / 1e6 * p["out"]


def _load_model_config() -> None:
    import json as _json
    import os as _os
    from pathlib import Path as _Path

    override = _os.environ.get("EVAL_MODELS_CONFIG")
    path = _Path(override) if override else _Path(__file__).resolve().parent / "models.json"
    if not path.is_file():
        return
    cfg = _json.loads(path.read_text(encoding="utf-8"))
    models = cfg.get("models") or {}
    if not models:
        return
    scope = globals()
    fields = {
        "MODELS": "slug",
        "MODEL_DISPLAY": "display",
        "OC_MODEL_SLUG": "openclaw_slug",
        "MODEL_PROVIDER": "provider",
        "MOCK_DISCORD_PORTS": "mock_discord_port",
    }
    for name, field in fields.items():
        target = scope.get(name)
        if not isinstance(target, dict):
            continue
        target.clear()
        for key, spec in models.items():
            if field in spec:
                target[key] = spec[field]
    prices = scope.get("PRICES")
    if isinstance(prices, dict):
        prices.clear()
        for key, spec in models.items():
            prices[key] = {"in": float(spec.get("price_in", 0.0)),
                           "out": float(spec.get("price_out", 0.0))}
    if "judge_model" in cfg and "JUDGE_MODEL" in scope:
        scope["JUDGE_MODEL"] = cfg["judge_model"]


_load_model_config()
