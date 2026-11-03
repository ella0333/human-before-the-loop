# Section 5: Reliability

Benign task completion: the constrained framework against OpenClaw native tool
calling, across the same four models. The original synthetic suite has 528
sessions; the GAIA prompt subset adds 248 sessions.

Paper result: 92.4% full-task accuracy for the constrained framework against
86.7% for OpenClaw, and 93.6% against 89.0% on first action. Both frameworks
completed 100% of sessions. Reasoning-action agreement was measurable in 100% of
constrained sessions and 55.3% of OpenClaw sessions; where measurable it was
97.3% against 86.3%.

## Setup

Eight cells: 2 frameworks x 4 models. The corpus is 66 benign prompts: six for each
of the 11 main-menu branches, and 11 in each of six wording styles. Both arms get the
same verbatim user request and the same static fake data.

Each session is one contained activity episode. The constrained arm ends after the
selected activity produces an event. There is no extra "did you finish?" call, and
the hidden ground-truth scorer never controls how many model calls a session gets.

**Scoring is deterministic and does not ask the agent.** Every corpus prompt maps to
an explicit canonical outcome key, `video:Zork`, or an exact `dm:friend_1|message`,
and corpus generation fails unless that key maps to an implemented activity target.
A broad request such as "play a text adventure" accepts only the three concrete
menu leaves (Zork, Hitchhiker's Guide, Planetfall), never the parent menu label.

Reaching the turn cap without a provider error is kept as an **incorrect** outcome
rather than retried. Retrying it would be outcome-based selection.

## Harnesses

`synthetic/harness/` reproduces the original synthetic suite. `gaia/harness/`
evaluates the fixed GAIA prompt subset.

Both are configured by `python setup.py` at the repository root. GAIA needs two
things the other evaluations do not: a `TAVILY_API_KEY` for bounded public-web
retrieval, and a HuggingFace login, because GAIA is a gated dataset. It is also
the only evaluation here that reaches the live web at all; the synthetic suite
runs entirely against mocks.

## Running the synthetic suite

```powershell
cd synthetic/harness
python build_corpus.py          # regenerates corpus/prompts.jsonl
python tools/selftest.py        # offline; spends nothing
python orchestrator.py --pilot  # 8 cells on one fixed prompt (REL-D03)
python orchestrator.py --full --no-classify
python post_experiment.py results\run_<timestamp> --classify --max-wait 86400
```

The pilot is written to its own timestamped directory and is deliberately excluded
from main results. Interrupted full runs reuse only clean, receipted sessions:

```powershell
python orchestrator.py --full --resume-from results\run_<timestamp>
```

The orchestrator disconnects as soon as session evidence is verified locally.
Batch classification runs afterward and does not need the container; if local polling ends first the terminal prints the batch ID and the
exact resume command.

## Results layout

`results/` holds `reliability_raw_results.xlsx` and `gaia_raw_turns.sealed`.
The workbook has four sheets:

| Sheet | What it is |
|---|---|
| `Guide` | what each sheet and the less obvious columns mean |
| `Synthetic Raw Turns` | every turn of all 528 synthetic sessions, both arms |
| `GAIA Raw Turns` | a placeholder; the real 2,369 rows are sealed, see below |
| `Outcomes` | one row per session, all 776, with the scored result |

For the constrained arm a turn row is one model call: what it was shown, the
request, the raw and parsed response, tokens, latency. For the OpenClaw arm the
rows are its own native transcript events, which carry the tool calls, results,
and natively exposed reasoning where the provider returned it. Where OpenClaw
reasoning is absent it is recorded as missing rather than inferred.

A value longer than Excel's 32,767-character cell limit continues in
`<column>_2`, `<column>_3` and so on. Join them in order to recover the original
exactly; nothing is truncated.

### The cost column

`cost_usd` for the synthetic constrained arm was reconstructed by summing each
session's own recorded provider usage. The run measured it, but its aggregation
step wrote zero into the per-session table. The recovered suite total,
$1.3173, reconciles to the $1.32 the original run reported. Nothing is estimated.

### GAIA prompts are not redistributed here

GAIA is gated so that its questions and answers do not end up in a crawlable file
and contaminate future training runs. This evaluation's turn-by-turn prompts quote
those questions, so they are not published in the clear. Two things are held back:

- `GAIA Raw Turns` in the workbook is a short placeholder. The real 2,369 rows sit
  beside it in `gaia_raw_turns.sealed`.
- `Outcomes` has no reference-answer column.

The seal is keyed to the GAIA validation split itself, derived from the 31 tasks
with PBKDF2-HMAC-SHA256 and applied as an HMAC-SHA256 keystream with an
authentication tag. Anyone who has accepted the dataset terms and downloaded the
split can rederive that key and read everything. A crawler or a training run that
never had the dataset gets nothing. It uses the standard library only, so
restoring adds no dependency.

```powershell
cd gaia/harness
python build_corpus.py --download        # needs a HuggingFace login
python tools/restore_gaia_fields.py
```

That writes `reliability_raw_results.restored.xlsx` beside the original with the
sheet filled in, every quoted question back in place, and the reference answers
added to `Outcomes`. Nothing is lost and the published workbook is not modified.

The published `Outcomes` sheet still carries every GAIA result the paper reports:
all 248 sessions, the judge verdict, the model's own answer, tool calls, tokens,
cost and timing. Only the turn-by-turn prompts are sealed.

One limit worth stating plainly: a model that answered a task correctly reveals
that answer in its own output, which sits in `Outcomes`. Sealing the prompts
reduces exposure rather than eliminating it.

Paper figures are in the repository-level `figures/` directory.
