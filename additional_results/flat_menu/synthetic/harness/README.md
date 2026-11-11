# reliability-eval

Reproducible action-selection reliability evaluation comparing a constrained
lettered-menu agent with stock OpenClaw native tool calling.

## Matrix and corpus

The eight cells cross constrained/OpenClaw with GPT-5.6 Luna, GPT-OSS-20B,
Muse Glimmer 30B, and Muse Spark 1.2. The fixed corpus contains
66 benign prompts: six for each of the 11 main-menu branches and 11 for each of
six wording styles. A full run is 528 sessions; the eight-session pilot uses one
fixed prompt in every cell and is excluded from main results by its separate run
directory.

The Muse conditions use immutable OpenRouter snapshots
`meta/muse-glimmer-30b-20260810` and `meta/muse-spark-1.2-20260805`. Glimmer is
pinned to DeepInfra and Spark to Meta, with fallback disabled. Both are mandatory-
reasoning models and use the same `low` effort control as Luna and GPT-OSS.

Each reliability session is one contained activity episode. The constrained arm
ends after the selected activity produces an event; the hidden ground-truth
scorer never controls how many model calls it receives. It does not make an
extra self-reported completion call.

Run `python build_corpus.py` to reproduce `corpus/prompts.jsonl`. The builder
fails unless every prompt maps to both the implemented constrained menu and a
declared OpenClaw mock-tool capability.

## Framework equivalence and containment

Both frameworks receive the same verbatim user request and interact with the
same static fake data. The constrained system selects a main-menu letter and
every applicable submenu letter, including the final concrete game title.
OpenClaw uses its real Gateway, native tool
calling, native transcript, and stock `message` tool. An external tool plugin
provides contained Discord reads, AI chat, games, writing/storage, music, social
feeds, website status, static web search, and reading activities.

No tool contacts real Discord, social media, search, news, AI chat, games, or a
website. Web/browser/runtime/filesystem/automation/subagent surfaces are denied.
Only the trusted Gateway reaches OpenRouter for inference.

All four models use `low` reasoning in both frameworks. Constrained responses use a
strict JSON schema, keep the required internal `thinking` field, and exclude
provider-native reasoning text. Luna is pinned to OpenAI, GPT-OSS-20B to Parasail,
Glimmer to DeepInfra, and Spark to Meta; provider fallback and unsupported-parameter
routing are disabled in both frameworks. OpenClaw uses
`agents.defaults.thinkingDefault: low`.

Constrained multi-turn context mirrors the reference implementation: after
validation, JSON wrappers are stripped. Menu turns re-enter context as plain-text
thinking plus the selected choice, and free-text activity turns re-enter as their
semantic value only. Raw provider bodies remain in the evidence logs, not in the
model's subsequent context.

## Scoring and evidence

Deterministic scoring uses recorded action arguments and the resulting mock-world
events, not the assistant's claim that it finished. Every corpus prompt maps to an
explicit canonical outcome key (for example `video:Zork` or an exact
`dm:friend_1|message`), and corpus generation fails unless that key maps to an
implemented activity target. A broad text-adventure request accepts only the
three concrete menu leaves (Zork, Hitchhiker's Guide, or Planetfall), never the
parent menu label. The batch classifier receives both these canonical result
keys and the expected full menu path. Every session records the
prompt and expected result, constrained rationale and menu choices, OpenClaw
reasoning when natively exposed, native tool calls/results, actions and targets,
first-action and full-task correctness, wrong/invalid/unnecessary actions,
terminal state, turns, tokens, cost, duration, errors, and retries.

Provider-declared 429/5xx error envelopes and invalid structured responses use the
same bounded retry path as HTTP-level failures; successful calls are never repeated. A provider error
inside any submenu or activity invalidates that session and the resume selector
reruns it. Reaching the fixed turn cap without a provider error is retained as an
incorrect model outcome rather than retried, preventing outcome-based selection.

After verified transfer and immutable hashing, the resumable OpenRouter Luna batch
classifies intended action, reasoning correctness, reasoning/action agreement, and
correct↔incorrect reasoning/action crossovers. Deterministic correctness remains
authoritative. Missing OpenClaw reasoning is recorded as missing.

## Commands

```powershell
python build_corpus.py
python tools/selftest.py
python orchestrator.py --pilot
python orchestrator.py --full --no-classify
python post_experiment.py results\run_<timestamp> --classify --max-wait 86400
```

Interrupted full runs can reuse only clean, receipted sessions:

```powershell
python orchestrator.py --full --resume-from results\run_<timestamp>
```

Outputs include `summary.csv`, `metrics.json`, `metrics.md`, three paper-ready SVG
charts, immutable raw hashes, validation records, complete batch request/response,
batch ID and poll history, and per-session raw evidence with transfer receipts.

See `SETUP.md` for the container and pilot runbook.
