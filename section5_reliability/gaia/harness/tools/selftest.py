"""Offline contract checks for the GAIA reliability harness."""

import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "constrained"))
failures = []

def check(label, condition, detail=""):
    if condition:
        print(f"[ok] {label}" + (f": {detail}" if detail else ""))
    else:
        print(f"[FAIL] {label}" + (f": {detail}" if detail else ""))
        failures.append(label)

def test_imports():
    for name in ("evalspec", "gaia_dataset", "gaia_scorer", "run_session", "orchestrator",
                 "post_experiment", "judge_attempts", "harness.aggregate", "harness.evidence"):
        try:
            __import__(name); check(f"import {name}", True)
        except Exception as exc:
            check(f"import {name}", False, str(exc))

def test_filter_and_matrix():
    import orchestrator
    from evalspec import all_configs
    from gaia_dataset import MANIFEST, eligibility_decision, load_tasks
    base = {"Level": "1", "Question": "What is 2+2?", "Final answer": "4",
            "file_name": "", "file_path": "", "Annotator Metadata": {"Tools": "1. Calculator"}}
    check("eligible scope admitted", eligibility_decision(base)[0])
    check("files structurally excluded", not eligibility_decision({**base, "file_name": "x.pdf"})[0])
    check("higher levels structurally excluded", not eligibility_decision({**base, "Level": "2"})[0])
    bad = {**base, "Annotator Metadata": {"Tools": "1. Python"}}
    check("unavailable tools structurally excluded", not eligibility_decision(bad)[0])
    check("eight mirrored framework/model cells", len(all_configs()) == 8)
    if MANIFEST.is_file():
        tasks = load_tasks()
        check("fixed corpus contains 31 tasks", len(tasks) == 31, f"selected={len(tasks)}")
        check("fixed category allocation", {k: sum(t["category"] == k for t in tasks)
              for k in ("reasoning", "web", "calculator")} ==
              {"reasoning": 10, "web": 16, "calculator": 5})
        full = orchestrator.build_sessions("full")
        check("full matrix contains 248 sessions", len(full) == 248)
        ids = {t["task_id"] for t in tasks}
        groups = {cfg: {task for c, task in full if c == cfg} for cfg, _ in full}
        check("every cell receives identical tasks", len(groups) == 8 and all(v == ids for v in groups.values()))
        lanes = orchestrator.partition_config_lanes(full, 2, full)
        owners = {}
        for lane_index, lane in enumerate(lanes):
            for _position, config_id, task_id in lane:
                owners.setdefault(config_id, set()).add(lane_index)
        check("two containers receive whole unsplit cells",
              len(lanes) == 2 and len(owners) == 8
              and all(len(lane_ids) == 1 for lane_ids in owners.values()))
        check("each container cell retains every task",
              all(sum(row[1] == config_id for lane in lanes for row in lane) == 31
                  for config_id in owners))
        single_lane = orchestrator.partition_config_lanes(full, 1, full)
        check("one container preserves the original session order exactly",
              len(single_lane) == 1
              and [(row[1], row[2]) for row in single_lane[0]] == full)
        pilot = orchestrator.build_sessions("pilot")
        pilot_groups = {cfg: {task for c, task in pilot if c == cfg} for cfg, _ in pilot}
        check("pilot has fixed calculator and web tasks in every cell",
              len(pilot) == 16 and len(pilot_groups) == 8 and
              all(v == set(orchestrator.PILOT_TASK_IDS) for v in pilot_groups.values()))
    else:
        with tempfile.TemporaryDirectory() as td:
            fixture = Path(td) / "tasks.jsonl"
            fixture.write_text("\n".join(json.dumps({"task_id": task_id})
                               for task_id in orchestrator.PILOT_TASK_IDS) + "\n", encoding="utf-8")
            old = orchestrator.PRIVATE_CORPUS
            try:
                orchestrator.PRIVATE_CORPUS = fixture
                check("CI fixture creates sixteen full sessions", len(orchestrator.build_sessions("full")) == 16)
                check("CI fixture creates sixteen pilot sessions", len(orchestrator.build_sessions("pilot")) == 16)
            finally:
                orchestrator.PRIVATE_CORPUS = old

def test_scorer():
    from gaia_scorer import question_scorer, score_task
    check("official-style string normalization", question_scorer("Sea gull!", "seagull"))
    check("official-style numeric normalization", question_scorer("$1,024", "1024"))
    check("extra answer prose rejected", not question_scorer("The answer is Paris.", "Paris"))
    check("exact diagnostic remains available", score_task({"final_answer": "42"}, [], "42")["full_task_correct"])

def test_semantic_judge():
    from evalspec import SCORER_VERSION
    from judge_attempts import (JUDGE_SYSTEM, apply_batch_results,
                                blinded_input, build_batch_requests,
                                judge_model, request_body)
    meta = {"config": "constrained__luna", "model_key": "luna", "framework": "constrained",
            "question": "Report the value in thousand hours.", "reference_answer": "17",
            "model_answer": "17,000 hours", "terminal_status": "completed",
            "tool_events": [{"secret": "trace"}], "final_thinking": "hidden"}
    visible = blinded_input(meta)
    check("judge input is identity-blinded", set(visible) == {
        "user_task", "reference_answer", "candidate_answer", "terminal_status"})
    encoded = json.dumps(request_body(meta))
    check("judge receives no framework/model/trace identity",
          "constrained__luna" not in encoded and "tool_events" not in encoded
          and "final_thinking" not in encoded)
    check("frozen rubric explicitly accepts unit equivalence",
          "accept either 17 or 17000" in JUDGE_SYSTEM)

    from harness.aggregate import aggregate
    with tempfile.TemporaryDirectory() as td:
        run = Path(td); session = run / "constrained__luna" / "task"
        session.mkdir(parents=True)
        (session / "session_meta.json").write_text(json.dumps({
            **meta, "task_id": "task", "model": "fixture", "category": "calculator",
            "agent_done": "completed", "error": None, "answer_correct": False,
            "exact_answer_correct": False, "full_task_correct": None,
            "tokens": {"in": 1, "out": 1}, "cost_usd": .01, "duration_s": 1,
        }), encoding="utf-8")
        post = run / "postprocess"; post.mkdir()
        (post / "completion_judgments.jsonl").write_text(json.dumps({
            "custom_id": "constrained__luna__task", "task_completed": True,
            "confidence": "high", "reason": "Equivalent units", "judge_cost_usd": .001,
            "scorer_version": SCORER_VERSION,
        }) + "\n", encoding="utf-8")
        metrics = aggregate(run)
        check("semantic outcome is primary over failed exact diagnostic",
              metrics["overall"]["accuracy"] == 1
              and metrics["overall"]["exact_match_rate"] == 0)

    with tempfile.TemporaryDirectory() as td:
        run = Path(td)
        for config, framework in (("constrained__luna", "constrained"),
                                  ("openclaw__luna", "openclaw")):
            session = run / config / "same-task"
            session.mkdir(parents=True)
            (session / "session_meta.json").write_text(json.dumps({
                **meta, "config": config, "framework": framework,
                "task_id": "same-task", "model": "fixture",
                "category": "calculator", "agent_done": "completed",
                "error": None,
            }), encoding="utf-8")
        requests, groups, judgments = build_batch_requests(run)
        check("batch judge deduplicates identical blinded cases",
              len(requests) == 1 and len(next(iter(groups.values()))) == 2
              and "model" not in requests[0]["body"])
        request_cid = requests[0]["custom_id"]
        verdict = {
            "task_completed": True, "reference_equivalent": True,
            "instruction_satisfied": True, "confidence": "high",
            "reason": "Equivalent units",
        }
        response = {
            "id": "batch-fixture", "status": "completed", "results": [{
                "id": "request-fixture", "custom_id": request_cid,
                "response": {"body": {
                    "id": "generation-fixture", "provider": "openai",
                    "model": judge_model(),
                    "choices": [{"message": {"content": json.dumps(verdict)}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                              "cost": .001},
                }},
            }],
        }
        check("batch judge applies one paid verdict to both identical cases",
              apply_batch_results(run, response, groups, judgments) == 2)
        rows = [json.loads(line) for line in
                (run / "postprocess" / "completion_judgments.jsonl").read_text(
                    encoding="utf-8").splitlines() if line.strip()]
        check("batch judge charges only the representative request",
              len(rows) == 2 and sum(row["judge_cost_usd"] for row in rows) == .001
              and sum("reused_from_custom_id" in row for row in rows) == 1)

def test_constrained():
    from evalagent.menus import MAIN_LETTERS
    from evalagent.provider import (LENGTH_RECOVERY_MAX_TOKENS, NORMAL_MAX_TOKENS,
                                    MAX_TRANSIENT_ATTEMPTS, _parse_structured,
                                    attempt_max_tokens)
    from evalagent.safe_tools import calculate
    from evalagent.schemas import activity_schema, menu_schema, submenu_schema
    schema = menu_schema(MAIN_LETTERS)
    check("main menu is closed and lettered", schema["additionalProperties"] is False
          and schema["properties"]["choice"]["enum"] == MAIN_LETTERS
          and schema["required"] == ["thinking", "choice"]
          and "done" not in schema["properties"])
    check("web and calculator are ordinary menu activities", MAIN_LETTERS[-3:] == ["Q", "R", "S"])
    check("submenu schema is distinct and closed", submenu_schema(["A"])["required"] == ["thinking", "choice"])
    activity_wire = activity_schema(["A", "B"])
    check("completion is activity-local", activity_wire["properties"]["choice"]["enum"]
          == ["", "A", "B"] and "done" in activity_wire["properties"])
    fixture = {"thinking": "choose", "choice": "Q"}
    parsed, repairs = _parse_structured("prose\n" + json.dumps(fixture), schema)
    check("provider framing repair preserves schema semantics", parsed == fixture and bool(repairs))
    check("calculator performs arithmetic", calculate("(12+3)*4").get("result") == "60")
    check("calculator rejects code", "error" in calculate("__import__('os').system('id')"))
    check("length-only recovery preserves normal budget then escalates once",
          [attempt_max_tokens(i, True) for i in range(1, MAX_TRANSIENT_ATTEMPTS + 1)]
          == [NORMAL_MAX_TOKENS] * (MAX_TRANSIENT_ATTEMPTS - 1)
          + [LENGTH_RECOVERY_MAX_TOKENS]
          and attempt_max_tokens(MAX_TRANSIENT_ATTEMPTS, False) == NORMAL_MAX_TOKENS)

    from evalagent import agent as module
    class FakeProvider:
        activities = []
        histories = []
        def __init__(self, **_kwargs):
            self.calls_made = self.tokens_in = self.tokens_out = self.reasoning_tokens = 0
        def set_system_prompt(self, _prompt): pass
        async def generate(self, _shown, _schema, **_kwargs):
            rows = [{"thinking": "search", "choice": "Q"},
                    {"thinking": "choose search", "choice": "A", "done": "", "answer": ""},
                    {"thinking": "query", "query": "answer source"},
                    {"thinking": "search again", "choice": "A", "done": "", "answer": ""},
                    {"thinking": "query", "query": "second source"},
                    {"thinking": "done", "choice": "", "done": "completed", "answer": "42"}]
            self.activities.append(_kwargs["activity"])
            self.histories.append(len(_kwargs.get("history") or []))
            row = rows[self.calls_made]; self.calls_made += 1; return row
    class FakeWeb:
        def __init__(self, *_args): pass
        def search(self, query): return {"query": query, "results": [{"title": "x", "url": "https://x", "content": "42"}]}
        def extract(self, url): return {"url": url, "results": []}
        @staticmethod
        def render(result): return json.dumps(result)
    old_provider, old_web = module.OpenRouterProvider, module.TavilySearch
    try:
        FakeProvider.activities = []; FakeProvider.histories = []
        module.OpenRouterProvider, module.TavilySearch = FakeProvider, FakeWeb
        result = asyncio.run(module.run_session(module.RunConfig("question", "fixture", "x", 8)))
        check("activity-local multi-turn loop completes", result.agent_done == "completed"
              and result.final_answer == "42" and result.menu_path == ["Q"]
              and len(result.events) == 2
              and all(event["tool"] == "web_search" for event in result.events))
        check("activity loop never returns to main menu",
              FakeProvider.activities == ["main_menu", "web_menu", "web_query",
                                           "web_menu", "web_query", "web_menu"])
        check("activity retains multi-turn context",
              FakeProvider.histories == sorted(FakeProvider.histories)
              and FakeProvider.histories[-1] > FakeProvider.histories[1])
    finally:
        module.OpenRouterProvider, module.TavilySearch = old_provider, old_web

def test_openclaw():
    from evalspec import OC_TOOLS_ALLOW, OC_TOOLS_DENY
    manifest = json.loads((ROOT / "openclaw/plugins/benchmark-tools/openclaw.plugin.json").read_text())
    source = (ROOT / "openclaw/plugins/benchmark-tools/index.js").read_text(encoding="utf-8")
    start = (ROOT / "start.sh").read_text(encoding="utf-8")
    expected = ["benchmark_web_search", "benchmark_web_browse", "benchmark_calculate"]
    check("OpenClaw exposes matching benchmark capabilities", manifest["contracts"]["tools"] == OC_TOOLS_ALLOW == expected)
    check("startup allowlist is exact", json.dumps(expected, separators=(",", ":")) in start)
    check("dangerous OpenClaw groups remain denied", all(x in OC_TOOLS_DENY for x in
          ("group:fs", "group:runtime", "browser", "exec", "message")))
    check("plugin has no local task/file reader", "readFile" not in source and "params.path" not in source)
    check("web observations are bounded", "slice(0, 12000)" in source and "slice(0, 3000)" in source)

def test_teardown_scope():
    import orchestrator

    source = (ROOT / "orchestrator.py").read_text(encoding="utf-8")
    check("the orchestrator never changes the container's lifecycle",
          "CONTAINER_SSH_HOST" in source
          and "terminate" not in source)
    check("a second lane reads its own container address",
          'f"CONTAINER_SSH_HOST{suffix}"' in source and 'lane: int = 1' in source)
    check("parallel lanes use independent original-style loops",
          "def run_container_lane" in source and "partition_config_lanes" in source
          and "session_iter" not in source and "task stealing" not in source)
    check("container scorer contract is checked before sessions",
          "self._verify_image_contract()" in source
          and "remote != expected" in source
          and "EXPERIMENT_CONTRACT_VERSION" in source
          and "RUNTIME_CONTRACT_VERSION" in source)
    check("resume rejects every stale execution contract",
          'meta.get("experiment_contract_version") != EXPERIMENT_CONTRACT_VERSION' in source
          and 'meta.get("runtime_contract_version") != RUNTIME_CONTRACT_VERSION' in source
          and 'meta.get("scorer_version") != SCORER_VERSION' in source)
    check("successful full run auto-judges, plots, and combines",
          "_run_completion_judge(run_dir)" in source
          and "make_figures(run_dir, metrics)" in source
          and "_write_combined_report(run_dir, env)" in source)

def main():
    test_imports(); test_filter_and_matrix(); test_scorer(); test_semantic_judge()
    test_constrained(); test_openclaw(); test_teardown_scope()
    if failures:
        print(f"FAILED ({len(failures)}): {', '.join(failures)}"); return 1
    print("all offline checks passed"); return 0

if __name__ == "__main__":
    raise SystemExit(main())
