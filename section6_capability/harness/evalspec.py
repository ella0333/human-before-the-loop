from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent

MODELS = {
    "glimmer": "meta/muse-glimmer-30b-20260810",
    "spark": "meta/muse-spark-1.2-20260805",
}
MODEL_DISPLAY = {"glimmer": "Muse Glimmer 30B", "spark": "Muse Spark 1.2"}
MODEL_PROVIDER = {"glimmer": "deepinfra", "spark": "meta"}
CONTAINER_PROVIDER_POLICY = {
    "glimmer": {"allow_fallbacks": True, "require_parameters": True},
    "spark": {"only": ["meta"], "allow_fallbacks": False},
}
OC_MODEL_SLUG = {
    "glimmer": "openrouter/meta/muse-glimmer-30b",
    "spark": "openrouter/meta/muse-spark-1.2",
}

CONTINUAL_HARNESS_COMMIT = "62bf6f614b66ff76b79954e5a3f04f91c3c6a049"

POKEDEX_FLAG_ADDRESS = 0xD74B
POKEDEX_FLAG_MASK = 0x20
COMPLETION_CONFIRMATIONS = 2
PILOT_TARGET_DESCRIPTION = "exactly five paid model calls through the Continual Harness simplest loop"

PILOT_CAPS = {
    "wallclock_seconds": 10 * 60,
    "turns": 5,
    "model_calls": 5,
}
FULL_CAPS = {
    "wallclock_seconds": 4 * 60 * 60,
    "turns": 1000,
    "model_calls": 1000,
}
CONTAINER_FULL_CAPS = {
    "wallclock_seconds": 7 * 24 * 60 * 60,
    "turns": 1000,
    "model_calls": 1000,
}
STATUS_INTERVAL_SECONDS = 20

PRICES = {
    "glimmer": {"in": 0.30, "out": 1.20},
    "spark": {"in": 1.25, "out": 4.25},
}


@dataclass(frozen=True)
class Cell:
    framework: str
    model_key: str

    @property
    def id(self) -> str:
        return f"{self.framework}__{self.model_key}"


def pilot_cells() -> list[Cell]:
    return [
        Cell("constrained", "glimmer"), Cell("constrained", "spark"),
        Cell("continual_harness", "glimmer"), Cell("continual_harness", "spark"),
    ]


def session_cost(model_key: str, tokens_in: int, tokens_out: int) -> float:
    price = PRICES[model_key]
    return tokens_in / 1_000_000 * price["in"] + tokens_out / 1_000_000 * price["out"]


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
        "CONTAINER_PROVIDER_POLICY": "container_provider_policy",
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
