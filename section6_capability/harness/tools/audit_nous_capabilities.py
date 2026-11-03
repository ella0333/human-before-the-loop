from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "openclaw_eval/plugins/pokemon-tools/index.js"
START = ROOT / "start.sh"
SKILL = ROOT / "vendor/pokemon-agent/skill/SKILL.md"
SERVER = ROOT / "vendor/pokemon-agent/pokemon_agent/server.py"

EXPECTED_ACTIONS = {
    "press_a", "press_b", "press_start", "press_select",
    "walk_up", "walk_down", "walk_left", "walk_right",
    "hold_a_30", "wait_60", "a_until_dialog_end",
}


def main() -> int:
    plugin = PLUGIN.read_text(encoding="utf-8")
    start = START.read_text(encoding="utf-8")
    skill = SKILL.read_text(encoding="utf-8")
    server = SERVER.read_text(encoding="utf-8")

    action_block = re.search(r"const ACTIONS = \[(.*?)\];", plugin, re.S)
    if not action_block:
        raise SystemExit("Nous capability audit failed: ACTIONS declaration missing")
    exposed = set(re.findall(r'"([a-z0-9_]+)"', action_block.group(1)))
    if exposed != EXPECTED_ACTIONS:
        raise SystemExit(
            f"Nous capability audit failed: actions={sorted(exposed)}, "
            f"expected={sorted(EXPECTED_ACTIONS)}")
    missing_from_skill = sorted(action for action in EXPECTED_ACTIONS
                                if not action.startswith("walk_") and action not in skill)
    if "walk_up/down/left/right" not in skill:
        missing_from_skill.extend(sorted(action for action in EXPECTED_ACTIONS
                                         if action.startswith("walk_")))
    if missing_from_skill:
        raise SystemExit(
            f"Nous capability audit failed: pinned skill lacks {missing_from_skill}")
    for parser_token in ('parts[0] == "press"', 'parts[0] == "walk"',
                         'parts[0] == "hold"', 'parts[0] == "wait"',
                         'action_str == "a_until_dialog_end"'):
        if parser_token not in server:
            raise SystemExit(
                f"Nous capability audit failed: server parser lacks {parser_token}")

    required_io = (
        'fetchOk("/state")', 'fetchOk("/map/ascii")',
        'fetchOk("/screenshot/grid?scale=3")', 'fetchOk("/action"',
        'type: "image"', 'mimeType: "image/png"',
    )
    missing_io = [token for token in required_io if token not in plugin]
    if missing_io:
        raise SystemExit(f"Nous capability audit failed: missing gameplay IO {missing_io}")

    guidance = (
        "always @ at E5", "remaining E5 is normal, not a desync",
        "ASCII map for WHERE", "screenshot for WHAT", "Never route through #",
        "short batches of 2-4 game inputs", "nous_navigation_guidance",
    )
    missing_guidance = [token for token in guidance if token not in plugin]
    if missing_guidance:
        raise SystemExit(
            f"Nous capability audit failed: missing navigation semantics {missing_guidance}")

    if "tools.allow '[\"pokemon_observe\",\"pokemon_action\"]'" not in start:
        raise SystemExit("Nous capability audit failed: Pokemon tools are not allowlisted")
    if re.search(r'tools\.deny.*pokemon_(observe|action)', start):
        raise SystemExit("Nous capability audit failed: Pokemon tool is denied")

    print("Nous/OpenClaw gameplay capability audit passed")
    print("covered: state + ASCII + labelled PNG + complete skill action vocabulary")
    print("excluded: dashboard/session/save administration (not gameplay inputs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
