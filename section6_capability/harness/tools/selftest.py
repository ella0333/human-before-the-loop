from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
failures: list[str] = []


def check(name: str, condition: bool) -> None:
    print(f"[{'ok' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


def main() -> int:
    import evalspec
    from credit_gate import credit_error

    check("matrix is exactly 2x2", [cell.id for cell in evalspec.pilot_cells()] == [
        "constrained__glimmer", "constrained__spark",
        "continual_harness__glimmer", "continual_harness__spark"])
    check("constrained infrastructure is pinned", evalspec.MODEL_PROVIDER == {
        "glimmer": "deepinfra", "spark": "meta"})
    check("container Glimmer uses audited availability routing",
          evalspec.CONTAINER_PROVIDER_POLICY == {
              "glimmer": {"allow_fallbacks": True, "require_parameters": True},
              "spark": {"only": ["meta"], "allow_fallbacks": False}})
    check("Continual Harness revision is fixed",
          evalspec.CONTINUAL_HARNESS_COMMIT ==
          "62bf6f614b66ff76b79954e5a3f04f91c3c6a049")
    check("pilot successful-call cap is five", evalspec.PILOT_CAPS == {
        "wallclock_seconds": 600, "turns": 5, "model_calls": 5})
    check("full tutorial cap is 1000", evalspec.FULL_CAPS == {
        "wallclock_seconds": 14400, "turns": 1000, "model_calls": 1000})
    check("Container full completion is call-cap-first", evalspec.CONTAINER_FULL_CAPS == {
        "wallclock_seconds": 604800, "turns": 1000, "model_calls": 1000})
    check("Pokedex completion is RAM-confirmed twice",
          evalspec.POKEDEX_FLAG_ADDRESS == 0xD74B
          and evalspec.POKEDEX_FLAG_MASK == 0x20
          and evalspec.COMPLETION_CONFIRMATIONS == 2)

    harness = ROOT / "vendor/continual-harness"
    pin = json.loads((harness / "EVAL_PIN.json").read_text(encoding="utf-8"))
    prompt = (harness / "agents/prompts/pokeagent-directives/SIMPLEST_RED.md").read_text(
        encoding="utf-8")
    backend = (harness / "utils/agent_infrastructure/vlm_backends.py").read_text(
        encoding="utf-8")
    harness_entry = (harness / "run.py").read_text(encoding="utf-8")
    reader = (harness / "pokemon_red_env/red_memory_reader.py").read_text(
        encoding="utf-8")
    check("vendored release and simplest scaffold are pinned",
          pin["commit"] == evalspec.CONTINUAL_HARNESS_COMMIT
          and pin["scaffold"] == "simplest"
          and pin["prompt_optimization"] is False)
    check("documented harness supplies native vision and open-ended tools",
          "Every step" in prompt and "press_buttons" in prompt
          and "process_memory" in prompt and "OPENROUTER VLM IMAGE QUERY" in backend)
    check("harness gets no walkthrough, pathfinding, skills, or subagents",
          "no walkthrough" in prompt.lower() and "no pathfinding" in prompt.lower()
          and "no skill or subagent system" in prompt.lower())
    check("only evaluator OpenRouter base URL is patched",
          'os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")' in backend)
    check("remote completion uses the actual Red event flag",
          '"pokedex_event":    0xD74B' in reader
          and 'RED_ADDR["pokedex_event"]' in reader and "& 0x20" in reader)

    start = (ROOT / "start.sh").read_text(encoding="utf-8")
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    remote = (ROOT / "continual_eval/run_remote.py").read_text(encoding="utf-8")
    gate = (ROOT / "openclaw_eval/openrouter_gate.py").read_text(encoding="utf-8")
    runner = (ROOT / "pilot.py").read_text(encoding="utf-8")
    check("container image contains Continual Harness and no OpenClaw runtime",
          "COPY . /app" in docker and "vendor/pokemon-agent" in dockerignore
          and "npm install -g openclaw" not in docker and "openclaw onboard" not in start)
    requirements = (ROOT / "requirements-container.txt").read_text(encoding="utf-8")
    preflight = (ROOT / "tools/runtime_preflight.py").read_text(encoding="utf-8")
    check("documented shared state dependencies and screenshot probe are enforced",
          "mgba==0.10.2" in requirements
          and 'importlib.metadata.version("mgba")' in preflight
          and "get_game_state_direct" in preflight
          and 'state_probe.get("screenshot_base64")' in preflight)
    check("mGBA Python binding has its matching system runtime",
          "FROM python:3.11-slim-bookworm" in docker
          and "libmgba0.10" in docker)
    check("remote uses official Red run.py simplest command",
          'HARNESS / "run.py"' in remote and '"--game", "red"' in remote
          and '"--scaffold", "simplest"' in remote
          and '"--enable-prompt-optimization"' not in remote
          and 'parser.add_argument("--max-steps"' in harness_entry)
    red_emulator = (ROOT / "vendor/continual-harness/pokemon_red_env/red_emulator.py").read_text(
        encoding="utf-8")
    check("container scoring starts after deterministic START and NEW GAME bootstrap",
          'POKEMON_RED_BOOTSTRAP_NEW_GAME="1"' in remote
          and "def _bootstrap_new_game" in red_emulator
          and 'self.press_key("start"' in red_emulator
          and 'self.press_key("a"' in red_emulator
          and '"unscored_bootstrap_inputs": ["START", "A"]' in remote)
    check("native screenshots remain model inputs",
          "image_url" in backend and "vision_model_calls" in remote
          and "native Continual Harness screenshots" in remote)
    check("hard gate blocks before call cap plus one",
          'if count >= int(cfg["call_cap"])' in gate
          and '"kind": "budget_block"' in gate
          and '"call_cap": caps["model_calls"]' in remote)
    check("container artifacts record call-first full termination",
          "configured_model_call_cap" in remote
          and "configured_wallclock_emergency_seconds" in remote
          and "primary_full_terminal_condition" in remote)
    check("credit exhaustion pauses the same request",
          "CREDIT PAUSE:" in gate and "CREDIT RESUME:" in gate
          and "await asyncio.sleep" in gate
          and "extend_on_credit_pause=True" in runner)
    check("provider routes are enforced and actual providers are retained",
          '"expected_provider_policy": expected_policy' in remote
          and "OPENROUTER_API_KEY=read_openrouter_key()" in remote
          and "CONTAINER_PROVIDER_POLICY" in remote
          and "actual_providers" in remote
          and "actual_provider" in gate
          and "response_route" in gate)
    check("completion is observed externally and stops early",
          "confirmations >= COMPLETION_CONFIRMATIONS" in remote
          and "atomic_json(completion_latch" in remote and "stop(process)" in remote)
    check("five-call pilot is not given a pilot gameplay objective",
          "PILOT_TARGET_DESCRIPTION" in remote
          and "pilot_contract" in remote
          and "reach the stairs" not in remote.lower())
    check("licensed ROM never enters artifacts or image",
          "copy rom" not in docker.lower() and "copy_evidence" in remote
          and all(ext in dockerignore for ext in ("*.gb", "*.gbc", "*.gba", "*.sav")))

    local = (ROOT / "constrained/run_local.py").read_text(encoding="utf-8")
    stage = (ROOT / "tools/stage_local_runtime.py").read_text(encoding="utf-8")
    local_prompt = (ROOT / "constrained/prompt.py").read_text(encoding="utf-8")
    engine = (ROOT / "constrained/helpers/pokemon_game.py").read_text(encoding="utf-8")
    check("local Red baseline and saves are isolated per attempt",
          'baseline_red' in local and 'shutil.copytree(baseline, attempt)' in local
          and '"POKEMON_EVAL_ROM": str(attempt / "PokemonRed.gb")' in local
          and "POKEMON RED" in stage and "0x134" in stage)
    check("structured thinking is required but JSON is stripped from context",
          '"required": ["thinking", "name"]' in local
          and '"required": ["thinking", "choice"]' in local
          and '"content": json.dumps(result)' not in local)
    check("constrained navigation wording remains neutral",
          "Use displayed compass directions" in local_prompt
          and "Use Nearby choices to automatically walk" in local_prompt
          and "Prefer Nearby" not in local_prompt)
    check("fragile local interaction memory is retained",
          "(already interacted)" in engine and "(you just exited here" in engine)

    summary = (ROOT / "tools/summarize_pilot.py").read_text(encoding="utf-8")
    check("pilot validates calls, actions, vision, model, and provider",
          all(token in summary for token in (
              "button_groups", "vision_model_calls", "actual_models",
              "provider_policy_verified", "provider_evidence_verified",
              "actual_providers", "successful_gate_calls")))
    check("pilot reports per-model totals and hard credit maximum",
          all(token in summary for token in (
              "hard_maximum_model_spend_before_credit_pause_usd",
              "per_model_full_totals", "HARD MAX MODEL SPEND BEFORE CREDIT PAUSE")))
    check("scoped pilots validate only the requested lane",
          "--expected-framework" in summary and "--expected-model" in summary
          and "expected_rows" in summary and "--expected-framework" in runner
          and "--expected-model" in runner)
    check("scoped pilot summaries omit unrelated missing cells",
          "for framework in frameworks" in summary
          and "for model_key in model_keys" in summary
          and "SCOPED FULL-RUN COST ESTIMATE" in summary)

    workflow = (ROOT / ".github/workflows/build-image.yml").read_text(encoding="utf-8")
    check("CI boots image with no model call and runs preflight",
          "Runtime smoke test (no model calls)" in workflow
          and "runtime_preflight.py" in workflow and "smoke-not-a-real-key" in workflow
          and "from agents.PokeAgent import PokeAgent" in
          (ROOT / "tools/runtime_preflight.py").read_text(encoding="utf-8"))
    check("the runner never changes the container's lifecycle",
          "CONTAINER_SSH_HOST" in runner
          and "terminate" not in runner)
    check("one local and one container lane run concurrently; each lane is sequential",
          "ThreadPoolExecutor(max_workers=2)" in runner
          and "for model in models" in runner)
    check("a lane rerun can produce a combined six-cell folder",
          "--combine-with" in runner and "seed_combined_results" in runner
          and "refusing to carry unsuccessful combined-results cell" in runner
          and "fresh_models" in runner and "--model" in runner
          and "combined_from.json" in runner
          and all(token in (ROOT / "orchestrator.py").read_text(encoding="utf-8")
                  for token in ("--combine-with", "--model")))
    check("local lane runs constrained then tool-calling cells sequentially",
          '"constrained": ("constrained.run_local"' in runner
          and '"tool_calling": ("tool_calling.run_local"' in runner
          and "for framework in frameworks" in runner and "TOOL_CALLING_CELLS" in runner)
    from tool_calling.run_local import offered_tools
    tools, lookup = offered_tools({"A": {"action": "walk_to_object", "label": "Mom"},
                                   "B": {"action": "walk_direction", "label": "North"}}, False)
    check("tool-calling cells offer the current choices as tools plus raw buttons",
          [item["function"]["name"] for item in tools] == ["go_to", "walk", "press_buttons"]
          and lookup == {("go_to", "Mom"): "A", ("walk", "North"): "B"})
    import tempfile
    from tool_calling.watchdog import bridge_alive
    with tempfile.TemporaryDirectory() as silent_bridge:
        check("watchdog treats an unanswered screenshot request as a stopped bridge",
              bridge_alive(Path(silent_bridge), timeout=0.2) is False)
    bridge_lua = (ROOT / "constrained" / "pokemon_bridge.lua").read_text(encoding="utf-8")
    check("bridge serves emulator frames and survives a failed command",
          "gui.gdscreenshot" in bridge_lua and "pcall(process_commands)" in bridge_lua
          and "pcall(execute_auto_walk)" in bridge_lua)
    check("artifact failure warns before the container is stopped",
          "self.preserve_logs = True" in runner
          and "ARTIFACT TRANSFER FAILED" in runner)
    check("uncapped key is refused before launch",
          "no finite per-key limit" in runner and "REFUSING TO START" in runner)
    check("credit breaker remains specific",
          credit_error({"error": {"code": 402,
                        "metadata": {"error_type": "payment_required"}}})
          and not credit_error({"error": {"code": 429, "message": "rate limited"}}))
    check("ROM extensions are gitignored",
          "*.gb" in (ROOT / ".gitignore").read_text(encoding="utf-8"))

    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("all offline self-tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
