"""Configure every evaluation in this repository. See README.md."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.env"


class Setting:
    def __init__(self, key, prompt, detail, needed_by, default="", secret=True):
        self.key = key
        self.prompt = prompt
        self.detail = detail
        self.needed_by = needed_by
        self.default = default
        self.secret = secret


SETTINGS = [
    Setting(
        "OPENROUTER_API_KEY",
        "OpenRouter API key",
        "Every model call in every evaluation goes through it.\n"
        "  https://openrouter.ai -> Keys -> Create. Set a credit limit on the key:\n"
        "  the harnesses treat that limit as the hard stop on model spend.",
        needed_by={"section 4 security", "section 5 synthetic", "section 5 gaia",
                   "section 6 capability"},
    ),
    Setting(
        "TAVILY_API_KEY",
        "Tavily API key",
        "Bounded public-web search and page extraction. Both arms of the GAIA\n"
        "  evaluation use it; no other evaluation touches the live web.\n"
        "  https://tavily.com -> API Keys.",
        needed_by={"section 5 gaia"},
    ),
    Setting(
        "LOCAL_ROM_PATH",
        "Pokemon Red ROM path",
        "Supply your own legally obtained Pokemon Red (USA, Europe) .gb file.\n"
        "  Red specifically, not Blue or Yellow: completion is detected by reading\n"
        "  Red's Pokedex event bit out of emulator memory. This is the only file you\n"
        "  have to provide; the emulator, Lua scripts and map database are included.",
        needed_by={"section 6 capability"}, secret=False,
    ),
]

EVALS = ["section 4 security", "section 5 synthetic", "section 5 gaia",
         "section 6 capability"]


def read_config() -> dict[str, str]:
    values: dict[str, str] = {}
    if CONFIG.exists():
        for line in CONFIG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def write_config(values: dict[str, str]) -> None:
    lines = ["# Written by setup.py. Configures every evaluation in this repository.",
             "# Git-ignored; never commit it.", ""]
    for setting in SETTINGS:
        lines.append(f"# {setting.prompt}")
        lines.append(f"{setting.key}={values.get(setting.key, '')}")
        lines.append("")
    CONFIG.write_text("\n".join(lines), encoding="utf-8")


def ask(setting: Setting, current: str) -> str:
    shown = current or setting.default
    if current and setting.secret:
        shown = current[:6] + "..." + current[-4:] if len(current) > 12 else "set"
    print(f"\n{setting.prompt}")
    for line in setting.detail.splitlines():
        print(f"  {line}")
    if setting.needed_by:
        print(f"  Needed by: {', '.join(sorted(setting.needed_by))}")
    else:
        print("  Needed by: every evaluation that uses the container")
    suffix = f" [{shown}]" if shown else " (leave blank to skip)"
    try:
        answer = input(f"  > {setting.key}{suffix}: ").strip()
    except EOFError:
        return current or setting.default
    if not answer:
        return current or setting.default
    return answer


def check_openrouter(key: str) -> tuple[bool, str]:
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8")).get("data", {})
    except urllib.error.HTTPError as exc:
        return False, f"rejected by OpenRouter (HTTP {exc.code})"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return False, f"could not reach OpenRouter: {exc}"
    limit, usage = data.get("limit"), data.get("usage") or 0
    if limit is None:
        return True, "valid, but this key has NO credit limit; set one before a full run"
    return True, f"valid, ${max(0.0, limit - usage):.2f} of ${limit:.2f} remaining"


def check_tavily(key: str) -> tuple[bool, str]:
    payload = json.dumps({"query": "example", "max_results": 1}).encode("utf-8")
    request = urllib.request.Request(
        "https://api.tavily.com/search", data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return False, f"rejected by Tavily (HTTP {exc.code})"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return False, f"could not reach Tavily: {exc}"
    return True, "valid"


def check_huggingface() -> tuple[bool, str]:
    try:
        from huggingface_hub import HfApi
    except ImportError:
        return False, ("huggingface_hub is not installed; "
                       "pip install -r section5_reliability/gaia/harness/requirements.txt")
    try:
        user = HfApi().whoami()
    except Exception:
        return False, "not logged in; run: huggingface-cli login"
    try:
        HfApi().dataset_info("gaia-benchmark/GAIA")
    except Exception:
        return False, (f"logged in as {user.get('name', '?')}, but no access to "
                       "gaia-benchmark/GAIA. Accept the terms on its dataset page.")
    return True, f"logged in as {user.get('name', '?')}, GAIA access granted"


def check_rom(path: str) -> tuple[bool, str]:
    rom = Path(os.path.expanduser(path))
    if not rom.is_file():
        return False, f"no file at {rom}"
    data = rom.read_bytes()
    title = data[0x134:0x143].rstrip(b"\x00").decode("ascii", "replace")
    if not title.startswith("POKEMON RED"):
        return False, (f"reports itself as {title!r}, not POKEMON RED. Red "
                       "specifically is required, not Blue or Yellow")
    digest = hashlib.sha256(data).hexdigest()
    return True, f"{title}, {len(data) // 1024} KB, sha256 {digest[:16]}…"


def check_docker() -> tuple[bool, str]:
    if not shutil.which("docker"):
        return False, "not installed. Install Docker Desktop and start it"
    info = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                          capture_output=True, text=True)
    if info.returncode != 0:
        return False, "installed but not running. Start Docker Desktop"
    return True, f"running, server {info.stdout.strip()}"


def verify(values: dict[str, str]) -> int:
    blockers: dict[str, list[str]] = {name: [] for name in EVALS}
    print()

    def report(label: str, ok: bool, detail: str, blocks: set[str]) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}: {detail}")
        if not ok:
            for name in blocks:
                blockers[name].append(label)

    if values.get("OPENROUTER_API_KEY"):
        report("OpenRouter key", *check_openrouter(values["OPENROUTER_API_KEY"]),
               blocks=set(EVALS))
    else:
        report("OpenRouter key", False, "not set", set(EVALS))

    report("Docker", *check_docker(), blocks=set(EVALS))

    if values.get("TAVILY_API_KEY"):
        report("Tavily key", *check_tavily(values["TAVILY_API_KEY"]),
               blocks={"section 5 gaia"})
    else:
        report("Tavily key", False, "not set", {"section 5 gaia"})

    report("HuggingFace / GAIA", *check_huggingface(), blocks={"section 5 gaia"})

    if values.get("LOCAL_ROM_PATH"):
        report("Pokemon Red ROM", *check_rom(values["LOCAL_ROM_PATH"]),
               blocks={"section 6 capability"})
    else:
        report("Pokemon Red ROM", False, "not set", {"section 6 capability"})

    print()
    for name in EVALS:
        missing = blockers[name]
        if missing:
            print(f"  {name:22} needs {', '.join(missing)}")
        elif name == "section 6 capability" and sys.platform != "win32":
            print(f"  {name:22} tool-calling cells ready; the two constrained cells "
                  "need Windows")
        else:
            print(f"  {name:22} ready")
    return 1 if any(blockers.values()) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="verify the existing config.env without prompting")
    args = parser.parse_args(argv)

    values = read_config()
    if not args.check:
        if not sys.stdin.isatty():
            print("setup.py needs a terminal to prompt; use --check to verify only.",
                  file=sys.stderr)
            return 2
        for setting in SETTINGS:
            values[setting.key] = ask(setting, values.get(setting.key, ""))
        write_config(values)
        print(f"\nwrote {CONFIG}")
    elif not CONFIG.exists():
        print(f"no {CONFIG.name} yet; run `python setup.py` first.", file=sys.stderr)
        return 2

    return verify(values)


if __name__ == "__main__":
    raise SystemExit(main())
