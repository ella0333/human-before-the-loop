# GAIA task-completion reliability evaluation

This repository compares a schema-constrained menu agent with stock
OpenClaw on real GAIA prompts that both systems are equipped to complete. It is
a prompt-only reliability study, not an official GAIA leaderboard run and not a
capability-breadth evaluation.

## Frozen task scope

The builder reads the public GAIA 2023 validation metadata at revision
`682dd723ee1e1697e00360edccf2366dc8418dd9`. Before any evaluated model runs, it
retains every row meeting all three rules:

- GAIA Level 1
- no attached file or file path
- annotator tools limited to reasoning/no tool, public web/search/Wikipedia,
  and calculator

That produces 31 tasks: 10 reasoning-only, 16 web, and 5 calculator tasks. No
success result or model output participates in selection.

The full matrix is 2 frameworks × 4 models × 31 tasks = **248 sessions**. This
new suite alone is 280 sessions (53.0%) smaller than the existing 528-session
synthetic suite. The supplied stratified combiner preserves both suites and
reports 776 sessions total, 248 sessions (47.0%) more than the old suite alone.
Per-suite results remain primary; the pooled number is descriptive only.

## Constrained system

The constrained arm preserves the established interaction contract:

1. fixed lettered main menu
2. activity-specific lettered submenu
3. schema-specific leaf input
4. remain inside the selected activity for repeated operations
5. finish from that activity with a concise answer

Web and calculator are ordinary activities in that hierarchy. Web offers Search
or Read Public Page, both mediated by bounded Tavily APIs, and can perform more
than one retrieval without revisiting the main menu. Calculator similarly accepts
repeated pure-arithmetic operations before activity-local completion. Other
historical harmless menu activities remain mocked. The
model cannot request a tool by name and has no route to local files, downloads,
deletion, arbitrary HTTP, shell execution, or real external actions.

OpenClaw receives matching `benchmark_web_search`, `benchmark_web_browse`, and
`benchmark_calculate` tools. Its native filesystem, browser, runtime, messaging,
media, memory, automation, node, and subagent surfaces are denied. Runtime
preflight verifies the exact allowlist before sessions start.

Primary completion is scored after the sessions by a frozen, blinded semantic
judge submitted through OpenRouter's resumable batch endpoint. It sees only the
task, reference answer, observable final answer, and terminal status, never the
framework, model, reasoning, or tool trace. The batch request, accepted batch ID,
poll history, and final response are persisted under `postprocess/`, so rerunning
resumes the same paid batch instead of submitting duplicate work. Equivalent
wording, rounding, and unit conversions are accepted. Identical blinded cases
reuse one verdict so experimental arms cannot receive different judgments for
the same evidence. Official-style GAIA exact matching is retained only as a
diagnostic. A clean completed terminal state and a positive judge verdict are
both required.

The pilot uses two unchanged tasks from the frozen corpus (one calculator and
one web task) in all eight framework/model cells, for 16 sessions total. It is
for cost/error checking, not paper evidence. The orchestrator judges the pilot
immediately and includes the measured judge cost in its full-run projection.

## Run

```powershell
Set-Location section5_reliability/flat_menu/gaia/harness
python -m pip install -r requirements.txt
python build_corpus.py --download
python tools/selftest.py
python orchestrator.py --pilot
```

Then run the full matrix:

```powershell
python orchestrator.py --full
```

`--containers 2` is optional and starts a second container. Each container runs an independent copy of the original sequential evaluator. Complete
framework/model cells are assigned up front: a cell and all of its prompts stay
on one container, which advances to its next assigned cell only after finishing
the current one. There is no shared session queue, task stealing, shared retry
state, or cross-container cancellation.

Before any session starts, the orchestrator spends one Tavily request on
a live search preflight; an expired, exhausted, or rejected key therefore fails
before any model spend. Put temporary credential rotations in the
git-ignored `.env.local`, which overrides `.env`.

Combine the completed legacy and GAIA suites without altering either source:

```powershell
python combine_results.py `
  <synthetic-run-directory> `
  results\run_<new_timestamp>
```

See `SETUP.md` for the container image details.
