from __future__ import annotations

import importlib.metadata
import json
import os
import stat
import sys
from pathlib import Path

import requests
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evalspec import (CONTINUAL_HARNESS_COMMIT, MODEL_PROVIDER, MODELS,
                      CONTAINER_PROVIDER_POLICY)


def require(value, message):
    if not value:
        raise RuntimeError(message)


def main() -> int:
    key = Path("/run/pokemon-eval/openrouter.key")
    require(key.is_file() and key.stat().st_size, "root-only OpenRouter key missing")
    require(stat.S_IMODE(key.stat().st_mode) == 0o600,
            "OpenRouter key is not mode 0600")
    harness = ROOT / "vendor" / "continual-harness"
    pin = json.loads((harness / "EVAL_PIN.json").read_text(encoding="utf-8"))
    require(pin.get("commit") == CONTINUAL_HARNESS_COMMIT,
            "Continual Harness commit mismatch")
    require(pin.get("scaffold") == "simplest" and not pin.get("prompt_optimization"),
            "remote scaffold policy mismatch")
    prompt = (harness / "agents/prompts/pokeagent-directives/SIMPLEST_RED.md").read_text(
        encoding="utf-8")
    require("press_buttons" in prompt and "process_memory" in prompt,
            "documented simplest Red prompt is missing")
    require("walkthrough" in prompt.lower() and "no pathfinding" in prompt.lower(),
            "simplest capability boundary changed")
    backend = (harness / "utils/agent_infrastructure/vlm_backends.py").read_text(
        encoding="utf-8")
    require("OPENROUTER_BASE_URL" in backend and "image_url" in backend,
            "native screenshot OpenRouter path is absent")
    entry = (harness / "run.py").read_text(encoding="utf-8")
    require('parser.add_argument("--max-steps"' in entry,
            "Continual Harness CLI is missing the evaluation step cap")
    reader = (harness / "pokemon_red_env/red_memory_reader.py").read_text(
        encoding="utf-8")
    require('"pokedex_event":    0xD74B' in reader and "& 0x20" in reader,
            "authoritative tutorial completion flag is absent")
    require(importlib.metadata.version("pyboy") == "2.5.2", "unexpected PyBoy version")
    require(importlib.metadata.version("mgba") == "0.10.2", "unexpected mGBA binding version")
    require(importlib.metadata.version("openai") == "1.90.0", "unexpected OpenAI SDK")
    os.environ["GAME_TYPE"] = "red"
    sys.path.insert(0, str(harness))
    from agents.PokeAgent import PokeAgent
    from pokemon_red_env.red_emulator import RedEmulator
    from server.app import app
    from server.game_tools import get_game_state_direct
    from utils.state_formatter import format_state_for_llm

    class StateProbe:
        def get_comprehensive_state(self, screenshot=None):
            return {
                "game": {"game_state": "overworld"},
                "player": {"location": "Runtime Preflight", "position": {"x": 0, "y": 0}},
                "visual": {"screenshot": screenshot},
                "map": {},
            }

    state_probe = get_game_state_direct(
        StateProbe(),
        lambda state, action_history=None: "Pokemon Red runtime state preflight",
        current_obs=np.zeros((144, 160, 3), dtype=np.uint8),
    )
    require(state_probe.get("success") is True and state_probe.get("screenshot_base64"),
            f"Red state/screenshot pipeline failed: {state_probe.get('error')}")
    from continual_eval.run_remote import read_openrouter_key
    os.environ["OPENROUTER_API_KEY"] = read_openrouter_key()
    os.environ["OPENROUTER_BASE_URL"] = "http://127.0.0.1:18999/api/v1"
    from utils.agent_infrastructure.vlm_backends import OpenRouterBackend
    backend_probe = OpenRouterBackend(MODELS["glimmer"], tools=[],
                                      system_instruction="runtime preflight")
    require(str(backend_probe.client.base_url).startswith(
        "http://127.0.0.1:18999/api/v1"), "OpenRouter backend bypasses egress gate")
    health = requests.get("http://127.0.0.1:18999/health", timeout=10).json()
    require(health.get("ok") is True, "OpenRouter egress gate is not healthy")
    require(MODELS == {"glimmer": "meta/muse-glimmer-30b-20260810",
                       "spark": "meta/muse-spark-1.2-20260805"},
            "model identifiers changed")
    require(MODEL_PROVIDER == {"glimmer": "deepinfra", "spark": "meta"},
            "constrained provider pins changed")
    require(CONTAINER_PROVIDER_POLICY == {
        "glimmer": {"allow_fallbacks": True, "require_parameters": True},
        "spark": {"only": ["meta"], "allow_fallbacks": False},
    }, "container provider routing policy changed")
    print("runtime preflight ok: pinned Continual Harness simplest, Pokemon Red, "
          "native screenshots/tools, audited provider routes, and healthy hard-cap gate")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"runtime preflight FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
