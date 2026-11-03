from __future__ import annotations

import json
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from evalspec import (
    MOCK_DISCORD_PORTS, MODEL_PROVIDER, OC_MODEL_SLUG, OC_TOOLS_ALLOW, OC_TOOLS_DENY,
)


def command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, timeout=60, check=False)


def command_json(*args: str):
    result = command(*args)
    if result.returncode:
        raise RuntimeError(
            f"{' '.join(args)} failed ({result.returncode}): "
            f"{(result.stderr or result.stdout).strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{' '.join(args)} returned invalid JSON: {result.stdout[-500:]}") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    key_file = Path("/run/injection-eval/openrouter.key")
    require(key_file.is_file() and key_file.stat().st_size > 0,
            "root-only OpenRouter runtime key file is missing or empty")
    require(stat.S_IMODE(key_file.stat().st_mode) == 0o600,
            f"OpenRouter runtime key mode is {oct(stat.S_IMODE(key_file.stat().st_mode))}, expected 0o600")

    valid = command("openclaw", "config", "validate")
    require(valid.returncode == 0, f"config validation failed: {valid.stderr or valid.stdout}")

    profile = command_json("openclaw", "config", "get", "tools.profile", "--json")
    allow = command_json("openclaw", "config", "get", "tools.allow", "--json")
    deny = command_json("openclaw", "config", "get", "tools.deny", "--json")
    workspace_only = command_json(
        "openclaw", "config", "get", "tools.fs.workspaceOnly", "--json")
    plugin_allow = command_json("openclaw", "config", "get", "plugins.allow", "--json")
    model_catalog = command_json(
        "openclaw", "config", "get", "agents.defaults.models", "--json")
    thinking_default = command_json(
        "openclaw", "config", "get", "agents.defaults.thinkingDefault", "--json")
    require(profile == "full", f"unexpected tools.profile: {profile!r}")
    require(allow == OC_TOOLS_ALLOW, f"unexpected tools.allow: {allow!r}")
    require(deny == OC_TOOLS_DENY, f"unexpected tools.deny: {deny!r}")
    require(workspace_only is True, "tools.fs.workspaceOnly is not true")
    require(plugin_allow == ["mock-discord", "mock-activities"], f"unexpected plugins.allow: {plugin_allow!r}")
    expected_catalog = {
        OC_MODEL_SLUG[key]: {"params": {"provider": {
            "only": [MODEL_PROVIDER[key]], "allow_fallbacks": False,
        }}}
        for key in OC_MODEL_SLUG
    }
    require(model_catalog == expected_catalog,
            f"unexpected model catalog (Auto/fallback must be absent): {model_catalog!r}")
    require(thinking_default == "low",
            f"unexpected agents.defaults.thinkingDefault: {thinking_default!r}")

    agents = command_json("openclaw", "agents", "list", "--bindings", "--json")
    by_id = {row.get("id"): row for row in agents if isinstance(row, dict)}
    expected = {
        "main": (OC_MODEL_SLUG["luna"], "mock-discord accountId=luna"),
        "oss": (OC_MODEL_SLUG["oss"], "mock-discord accountId=oss"),
        "glimmer": (OC_MODEL_SLUG["glimmer"], "mock-discord accountId=glimmer"),
        "spark": (OC_MODEL_SLUG["spark"], "mock-discord accountId=spark"),
    }
    require(set(by_id) == set(expected), f"unexpected agent roster: {sorted(by_id)}")
    for agent_id, (model, binding) in expected.items():
        row = by_id[agent_id]
        require(row.get("model") == model,
                f"{agent_id} model is {row.get('model')!r}, expected {model!r}")
        require(row.get("bindingDetails") == [binding],
                f"{agent_id} bindings are {row.get('bindingDetails')!r}, expected {[binding]!r}")

    health = command_json("openclaw", "health", "--json", "--timeout", "5000")
    require(health.get("ok") is True, "OpenClaw health is not ok")
    require(not (health.get("plugins") or {}).get("errors"),
            f"plugin errors: {(health.get('plugins') or {}).get('errors')!r}")
    loaded = set((health.get("plugins") or {}).get("loaded") or [])
    require(loaded == {"memory-core", "mock-discord", "mock-activities", "openrouter"},
            f"unexpected loaded plugin set: {sorted(loaded)}")
    accounts = (((health.get("channels") or {}).get("mock-discord") or {}).get("accounts") or {})
    require(set(accounts) == set(MOCK_DISCORD_PORTS),
            f"unexpected mock-discord accounts: {sorted(accounts)}")
    for account_id in MOCK_DISCORD_PORTS:
        state = accounts[account_id]
        require(state.get("running") is True and state.get("configured") is True,
                f"mock-discord {account_id} is not running/configured: {state!r}")
        require(state.get("restartPending") is False and state.get("reconnectAttempts") == 0,
                f"mock-discord {account_id} is unstable: {state!r}")

    for account_id, port in MOCK_DISCORD_PORTS.items():
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/inbound", timeout=5)
        except urllib.error.HTTPError as exc:
            require(exc.code == 404, f"mock-discord {account_id} returned HTTP {exc.code}")
        except OSError as exc:
            raise RuntimeError(f"mock-discord {account_id} is unreachable: {exc}") from exc
        else:
            raise RuntimeError(f"mock-discord {account_id} accepted invalid GET request")

    print("runtime preflight ok: exact policy, agents, models, bindings, health, and ports")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"runtime preflight FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
