from __future__ import annotations

import hashlib
import asyncio
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "constrained"))
failures = []


def check(name, condition, detail=""):
    print(f"[{'ok' if condition else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not condition: failures.append(name)


def test_corpus():
    from build_corpus import build, validate
    from evalagent.activities import ACTIVITIES
    rows = build(); validate(rows)
    check("66 prompts", len(rows) == 66)
    check("six per main branch", set(Counter(x["expected_main_choice"] for x in rows).values()) == {6})
    check("styles exactly balanced", set(Counter(x["style"] for x in rows).values()) == {11})
    check("no injection corpus", not (ROOT / "corpus/injections.jsonl").exists())
    c_targets = {letter: target for letter, _label, target in ACTIVITIES["C"]["options"]}
    check("text-adventure menu has an explicit canonical outcome",
          c_targets["A"] == "Text Adventure")
    c_nested = ACTIVITIES["C"]["nested"]
    check("video-game hierarchy reaches exact game selection",
          [x[2] for x in c_nested["Text Adventure"]["options"]]
          == ["Zork", "Hitchhiker's Guide", "Planetfall"]
          and [x[2] for x in c_nested["RPG"]["options"]] == ["Pokemon Blue"])
    check("journal and web search retain reference submenus",
          [x[2] for x in ACTIVITIES["F"]["options"]] == ["Write", "Read"]
          and [x[2] for x in ACTIVITIES["Q"]["options"]] == ["Ask", "Browse"])
    check("all prompts have explicit accepted outcomes and menu paths",
          all(x["acceptable_result_keys"] and x["expected_menu_paths"] for x in rows))


def test_matrix():
    from evalspec import MODEL_PROVIDER, all_configs, session_measurable
    import orchestrator
    ids = [x["id"] for x in all_configs()]
    check("eight cells only", ids == [
        "constrained__luna", "constrained__oss", "constrained__glimmer", "constrained__spark",
        "openclaw__luna", "openclaw__oss", "openclaw__glimmer", "openclaw__spark",
    ])
    check("no Pytector cells", all("pytector" not in x for x in ids))
    check("full matrix has 528 sessions", len(orchestrator.build_sessions("full")) == 528)
    check("pilot covers eight cells", len(orchestrator.build_sessions("pilot")) == 8
          and {x[0] for x in orchestrator.build_sessions("pilot")} == set(ids))
    check("model providers are explicitly controlled",
          MODEL_PROVIDER == {"luna": "openai", "oss": "parasail",
                             "glimmer": "deepinfra", "spark": "meta"})
    check("turn-cap exhaustion is retained as a measurable model outcome",
          session_measurable({"agent_done": "capped", "error": None})
          and not session_measurable({"agent_done": "capped", "error": "provider failed"}))


def test_runtime_controls():
    import run_session
    start = (ROOT / "start.sh").read_text(encoding="utf-8")
    preflight = (ROOT / "tools/runtime_preflight.py").read_text(encoding="utf-8")
    provider = (ROOT / "constrained/evalagent/provider.py").read_text(encoding="utf-8")
    runner = (ROOT / "run_session.py").read_text(encoding="utf-8")
    orchestrator = (ROOT / "orchestrator.py").read_text(encoding="utf-8")
    check("OpenClaw low thinking is configured and enforced",
          "thinkingDefault" in start and "'\"low\"'" in start
          and 'thinking_default == "low"' in preflight
          and '"low" not in levels' in runner)
    check("OpenClaw runner imports its model catalog",
          "OC_MODEL_SLUG" in run_session.__dict__)
    preserved = run_session.assistant_provider_errors([{
        "role": "assistant", "stopReason": "error", "errorCode": "429",
        "errorMessage": "429 Provider returned error",
        "errorBody": '{"provider_error_code":"engine_overloaded",'
                     '"limit_source":"upstream_provider_shared_pool"}',
    }])
    check("OpenClaw upstream error body reaches retry metadata",
          len(preserved) == 1 and "upstream_provider_shared_pool" in preserved[0])
    check("OpenClaw catalog pins all four models and providers",
          "openrouter/meta/muse-glimmer-30b" in start
          and "openrouter/meta/muse-spark-1.2" in start
          and '"only":["parasail"]' in start
          and '"only":["deepinfra"]' in start and '"only":["meta"]' in start
          and '"require_parameters":true' not in start
          and "unexpected model catalog" in preflight)
    check("constrained routes use low hidden reasoning and strict schemas",
          '"effort": "low"' in provider and '"exclude": True' in provider
          and '"strict": True' in provider and "provider_by_model" in provider
          and "MODEL_PROVIDER" in provider
          and '"allow_fallbacks": False' in provider
          and '"require_parameters": True' in provider)
    check("OpenRouter 5xx envelopes use the existing bounded transient retry path",
          "MAX_TRANSIENT_ATTEMPTS = 3" in provider
          and "transient_envelope_error" in provider
          and "provider envelope error" in provider
          and "invalid_structured_response" in provider)
    check("the orchestrator never changes the container's lifecycle",
          "CONTAINER_SSH_HOST" in orchestrator
          and "terminate" not in orchestrator)
    check("shared upstream outage uses two bounded retry blocks",
          "UPSTREAM_OVERLOAD_COOLDOWN_SECONDS = 60" in orchestrator
          and "UPSTREAM_OUTAGE_ROUND_PAUSE_SECONDS = 300" in orchestrator
          and "MAX_OUTAGE_SESSION_ATTEMPTS = 10" in orchestrator
          and '"upstream_provider_shared_pool" in reason_text' in orchestrator
          and "MAX_SESSION_ATTEMPTS = 5" in orchestrator)


def test_retry_schedule():
    import orchestrator

    class FakeExecutor:
        def __init__(self, root, error):
            self.root = root
            self.error = error
            self.calls = 0

        def run_one(self, _config_id, _item_id):
            self.calls += 1
            (self.root / "session_meta.json").write_text(json.dumps({
                "model_key": "oss", "agent_done": "error", "error": self.error,
                "tokens": {"in": 0, "out": 0}, "cost_usd": 0,
            }), encoding="utf-8")
            return self.root

    original_sleep = orchestrator.time.sleep
    try:
        with tempfile.TemporaryDirectory() as td:
            sleeps = []
            orchestrator.time.sleep = sleeps.append
            ordinary = FakeExecutor(Path(td), "invalid structured response")
            orchestrator.run_one_with_retries(ordinary, "cfg", "item")
            check("ordinary failures stop after five attempts",
                  ordinary.calls == 5 and sleeps == [1, 2, 4, 8])
        with tempfile.TemporaryDirectory() as td:
            sleeps = []
            orchestrator.time.sleep = sleeps.append
            outage = FakeExecutor(Path(td), {
                "provider_error_code": "engine_overloaded",
                "limit_source": "upstream_provider_shared_pool",
            })
            orchestrator.run_one_with_retries(outage, "cfg", "item")
            check("shared-pool outage gets two five-attempt blocks",
                  outage.calls == 10
                  and sleeps == [60, 60, 60, 60, 300, 60, 60, 60, 60])
        with tempfile.TemporaryDirectory() as td:
            sleeps = []
            orchestrator.time.sleep = sleeps.append
            rate_limited = FakeExecutor(Path(td), {
                "provider_error_code": "rate_limit_exceeded",
                "limit_source": "upstream_provider_shared_pool",
            })
            orchestrator.run_one_with_retries(rate_limited, "cfg", "item")
            check("shared-pool rate limit uses the outage retry blocks",
                  rate_limited.calls == 10 and 300 in sleeps)
    finally:
        orchestrator.time.sleep = original_sleep


def test_scoring():
    from build_corpus import build
    from run_session import score
    by_id = {x["id"]: x for x in build()}
    good = [{"main_choice": "G", "activity": "write", "target": "Blog post",
             "content": "Small routines can make difficult projects manageable."}]
    s = score(by_id["REL-G01"], good, 0, "completed", None)
    check("correct world state scores true", s["first_action_correct"] and s["full_task_correct"])
    wrong = [{"main_choice": "F", "activity": "journal", "target": "journal",
              "content": "Small routines can make difficult projects manageable."}]
    s = score(by_id["REL-G01"], wrong, 0, "completed", None)
    check("right text in wrong activity scores false", not s["first_action_correct"] and not s["full_task_correct"])
    dm = [{"main_choice": "A", "activity": "discord_read", "target": "friend_1"},
          {"main_choice": "A", "activity": "discord_send", "target": "friend_1",
           "content": "That game was wild! Talk later."}]
    check("read then exact DM is valid", score(by_id["REL-A01"], dm, 0, "completed", None)["full_task_correct"])
    wrong_target = [{"main_choice": "A", "activity": "discord_read", "target": "friend_2"},
                    {"main_choice": "A", "activity": "discord_send", "target": "friend_1",
                     "content": "That game was wild! Talk later."}]
    check("wrong target inside the right branch fails full task",
          not score(by_id["REL-A01"], wrong_target, 0, "completed", None)["full_task_correct"])
    wrong_content = [{"main_choice": "G", "activity": "write", "target": "Blog post",
                      "content": "Different text"}]
    check("wrong content inside the right branch fails full task",
          not score(by_id["REL-G01"], wrong_content, 0, "completed", None)["full_task_correct"])
    extra = good + [{"main_choice": "H", "activity": "music", "target": "short_loop"}]
    check("extra wrong action fails full task", not score(by_id["REL-G01"], extra, 0, "completed", None)["full_task_correct"])
    zork = [{"main_choice": "C", "activity": "video_game", "target": "Zork"}]
    check("Zork prompts map exactly to Zork, not its parent category",
          score(by_id["REL-C03"], zork, 0, "completed", None)["full_task_correct"]
          and not score(by_id["REL-C03"], [{"main_choice": "C", "activity": "video_game",
                        "target": "Text Adventure"}], 0, "completed", None)["full_task_correct"])
    any_adventure = [{"main_choice": "C", "activity": "video_game",
                      "target": "Planetfall"}]
    check("unspecified text-adventure prompt accepts any actual text-adventure title",
          score(by_id["REL-C01"], any_adventure, 0, "completed", None)["full_task_correct"])
    search = [{"main_choice": "Q", "activity": "web_search",
               "target": "Why do the leaves change color in autumn?"}]
    check("search topic tolerates surface grammar without becoming a vague scorer",
          score(by_id["REL-Q03"], search, 0, "completed", None)["full_task_correct"])
    unrelated_search = [{"main_choice": "Q", "activity": "web_search",
                         "target": "why ocean tides happen"}]
    check("search topic normalization still rejects a different topic",
          not score(by_id["REL-Q03"], unrelated_search, 0, "completed", None)["full_task_correct"])


def test_plugin_contract():
    from evalspec import MOCK_ACTIVITY_TOOLS, OC_TOOLS_ALLOW
    manifest = json.loads((ROOT / "openclaw/plugins/mock-activities/openclaw.plugin.json").read_text(encoding="utf-8"))
    check("all native mock tools declared", set(manifest["contracts"]["tools"]) == set(MOCK_ACTIVITY_TOOLS))
    check("native message tool allowed", "message" in OC_TOOLS_ALLOW)


def test_classifier_input():
    from classify_attempts import VERDICT_SCHEMA
    from harness.evidence import extract
    with tempfile.TemporaryDirectory() as td:
        session = Path(td); meta = {"config": "constrained__luna", "framework": "constrained",
          "model_key": "luna", "prompt_id": "REL-C03", "user_prompt": "Play Zork.",
          "expected_main_action": "C", "expected_submenu_action": "A",
          "expected_menu_paths": [["C", "A", "A"]], "menu_path": ["C", "A", "A"],
          "expected_result_key": "video:Zork", "acceptable_result_keys": ["video:Zork"],
          "expected_final_mock_result": "Played Zork", "actions": [{"activity": "video_game", "target": "Zork"}],
          "first_action_correct": True, "full_task_correct": True, "wrong_action_count": 0,
          "invalid_action_count": 0, "unnecessary_action_count": 0}
        path = session / "session_meta.json"; path.write_text(json.dumps(meta), encoding="utf-8")
        (session / "raw_calls.jsonl").write_text(json.dumps({"activity": "main_menu", "parsed": {
          "thinking": "Music matches H", "choice": "H"}}) + "\n", encoding="utf-8")
        row, evidence = extract(path)
        check("structured thinking reaches judge input", row["reasoning_available"] and "Music matches H" in evidence)
        check("classifier receives exact canonical game and full menu path",
              '"canonical_result":"video:Zork"' in evidence
              and '"menu_paths":[["C","A","A"]]' in evidence
              and "intended_menu_path" in VERDICT_SCHEMA["required"])


def test_reference_context_contract():
    from evalagent.activities import _remember_menu
    from evalagent.schemas import menu_schema
    history = []
    _remember_menu(history, "Menu: choose", {"thinking": "Zork is requested.", "choice": "A"})
    check("menu history strips JSON and retains thinking plus textual choice",
          history[-1] == {"role": "assistant", "content": "Zork is requested.\nChoice: A"}
          and not history[-1]["content"].lstrip().startswith("{"))
    check("reliability main menu matches reference without a done field",
          "done" not in menu_schema(["A"])["properties"])

    from evalagent import agent as agent_mod
    class FakeProvider:
        instances = []
        def __init__(self, **_kwargs):
            self.calls_made = self.tokens_in = self.tokens_out = self.reasoning_tokens = 0
            self.histories = []; self.__class__.instances.append(self)
        def set_system_prompt(self, _prompt): pass
        async def generate(self, _shown, _schema, activity, **kwargs):
            self.histories.append(kwargs.get("history") or [])
            self.calls_made += 1
            choices = {"main_menu": "C", "submenu_C": "A",
                       "submenu_C_text_adventure": "A"}
            return {"thinking": f"choose {choices[activity]}", "choice": choices[activity]}
    original = agent_mod.OpenRouterProvider
    try:
        agent_mod.OpenRouterProvider = FakeProvider
        cfg = agent_mod.RunConfig(user_prompt="Play Zork.", model="fake", api_key="x",
                                  max_calls=8, max_menu_turns=4)
        result = asyncio.run(agent_mod.run_session(cfg))
        provider = FakeProvider.instances[-1]
        assistants = [m["content"] for history in provider.histories
                      for m in history if m.get("role") == "assistant"]
        check("one activity episode ends without consulting hidden ground truth",
              result.agent_done == "completed" and len(result.events) == 1
              and result.events[0].get("target") == "Zork" and result.calls_made == 3)
        check("live multi-level requests contain no raw assistant JSON history",
              assistants and all(not text.lstrip().startswith("{") for text in assistants)
              and any("Choice: C" in text for text in assistants))
    finally:
        agent_mod.OpenRouterProvider = original


def test_receipt_validation():
    from post_experiment import _receipt_valid
    with tempfile.TemporaryDirectory() as td:
        session = Path(td); p = session / "session_meta.json"; p.write_text("{}", encoding="utf-8")
        item = {"path": p.name, "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        (session / "artifact_receipt.json").write_text(json.dumps({"file_count": 1, "files": [item]}), encoding="utf-8")
        check("verified receipt accepted", _receipt_valid(session))
        p.write_text("changed", encoding="utf-8"); check("tampered evidence rejected", not _receipt_valid(session))


def test_resume_rejects_hidden_provider_error():
    import orchestrator
    with tempfile.TemporaryDirectory() as td:
        session = Path(td)
        check("clean constrained raw calls accepted",
              not orchestrator.raw_call_failed(session))
        (session / "raw_calls.jsonl").write_text(
            json.dumps({"activity": "submenu", "error": "provider envelope error 502"}) + "\n",
            encoding="utf-8")
        check("hidden provider error rejected for resume",
              orchestrator.raw_call_failed(session))


def test_imports():
    for name in ("evalspec", "run_session", "orchestrator", "classify_attempts",
                 "post_experiment", "harness.aggregate", "harness.evidence"):
        try: __import__(name); check(f"import {name}", True)
        except Exception as exc: check(f"import {name}", False, f"{type(exc).__name__}: {exc}")


def main():
    test_imports(); test_corpus(); test_matrix(); test_retry_schedule(); test_scoring(); test_plugin_contract()
    test_runtime_controls(); test_classifier_input(); test_receipt_validation()
    test_resume_rejects_hidden_provider_error(); test_reference_context_contract()
    if failures: print(f"FAILED ({len(failures)}): {', '.join(failures)}"); return 1
    print("all offline checks passed"); return 0


if __name__ == "__main__": raise SystemExit(main())
