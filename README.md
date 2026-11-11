# Human Before the Loop

Evaluation harnesses and raw results for the paper.

The paper proposes an agent framework where the action space and data access are
fixed at design time rather than at runtime. The LLM navigates a Hierarchical
Extended Finite State Machine using semantic action abstraction, choosing among
simplified options while static Python code performs the execution.

Three evaluations were run to validate the security measures, while measuring an increase in reliability and realized capability; each evaluation is described below.

| Directory | Paper section | What it tests | Headline result |
|---|---|---|---|
| `section4_security/` | 4. Security | Data exfiltration via prompt injection, our constrained agent vs OpenClaw and a least-privilege OpenClaw, 328 distinct attacks per arm across 4 models | 0 successful attacks constrained, 28 of 328 (8.5%) for OpenClaw, 0 for least-privilege OpenClaw; benign tasks completed 100%, 86.6% and 66.2% |
| `section5_reliability/` | 5. Reliability | Synthetic benign-task completion (528 sessions) and a GAIA prompt subset (248 sessions) | 81.4% constrained vs 76.5% OpenClaw pooled; per-suite results are reported separately |
| `section6_capability/` | 6. Capability | Pokémon Red tutorial completion, our constrained agent vs a tool-calling harness and a hybrid agent with our menu choices as tool calls | Glimmer 30B finished in 342 model calls at $0.28; the same model tool-calling never left the first house in 1,000 calls, and as the hybrid agent stopped at the cap in Oak's Lab |

Each evaluation directory contains its harnesses and raw results. Paper figures
are collected in the top-level `figures/` directory.

`section5_reliability/flat_menu/` holds an additional reliability result: the
section 5 constrained framework rerun with its menu hierarchy removed, so every
option sits on one screen.

[hbtl-pokemon-eval](https://github.com/ella0333/hbtl-pokemon-eval) is a standalone
harness for testing LLMs in this framework.

[Eli Felse](https://elifelse.org/dev/open-source) is a fully open-source
implementation of the framework described in the paper.

In sections 4 and 5, both our constrained agent and OpenClaw run inside a Docker container. In section 6, our constrained agent and the hybrid agent run locally on Windows, and the tool-calling Pokémon harness runs in a container.

---

# Setup

## 1. Requirements

- Docker, installed and running. Each evaluation builds its own container image
  and starts and removes the container itself.
- An OpenRouter API key, for every evaluation.
- A Tavily API key and a HuggingFace login, for the GAIA suite only.
- Your own Pokémon Red ROM, for section 6 only. See
  [Additional requirements for Section 6](#additional-requirements-for-section-6).

Install the launcher requirements:

```bash
pip install paramiko httpx openpyxl huggingface_hub
```

## 2. Configuration

```bash
python setup.py
```

This asks for each value in turn, writes `config.env`, and checks every
credential against its live service, along with whether Docker is running. That
one file configures all three evaluations at once. The check reports per
evaluation, so a missing ROM does not stop you running section 4.

To re-check later without being prompted again, run `python setup.py --check`.

To override a value for one evaluation only, put a `.env` in that evaluation's
`harness/` directory.

The GAIA suite needs a HuggingFace login because GAIA is a gated dataset.
Create an account, accept the terms at
<https://huggingface.co/datasets/gaia-benchmark/GAIA> (granted instantly), then
run `huggingface-cli login` once.

---

# Evaluations

All of them start the same way within their respective folders:

```bash
cd section4_security/harness
python tools/selftest.py         # offline check
python orchestrator.py --pilot   # small live run, prints a full cost projection
python orchestrator.py --full    # the complete run
```

The pilot shows what the full run will cost before you commit to it.

Sections 4 and 5 compare against **OpenClaw**, a general tool-calling agent with
real file access, and section 6's tool-calling cells run the PokéAgent Continual
Harness. Both run only inside a Docker container, never on your own machine. The
orchestrator builds the image, starts the container, and removes it when the run
ends. The first build of each image takes several minutes; later runs reuse it.

The harness directories are `section4_security/harness`,
`section4_security/least_privilege/harness`,
`section5_reliability/synthetic/harness`, `section5_reliability/gaia/harness`,
`section5_reliability/flat_menu/synthetic/harness`,
`section5_reliability/flat_menu/gaia/harness`
and `section6_capability/harness`. Each section's own `README.md` covers its
result files, resume behavior, and options. Each harness's `SETUP.md` holds
operator notes.

The GAIA suite needs its corpus built first, which is the step that uses your
HuggingFace login:

```bash
cd section5_reliability/gaia/harness
python build_corpus.py --download
```

It can also spread its runs across two containers with `--containers 2`.

The least-privilege OpenClaw arm of section 4 has its own harness, because its
tool policy is fixed when the container starts. Run it after
`section4_security/harness`, since its combined report reads those runs to put
all three arms side by side.

## Your Results

| Path | What it is |
|---|---|
| `section*/results/` | our raw results, as reported in the paper, committed and tracked |
| `section*/harness/results/` | anything you run, written fresh and git-ignored |

Each run creates its own timestamped directory underneath: `run_<timestamp>/` for
sections 4 and 5, `full_<timestamp>/` or `pilot_<timestamp>/` for section 6.
Repeated runs do not overwrite each other either. Nothing in the harness ever
writes to `section*/results/`, and the root `.gitignore` excludes
`*/harness/results/`, so `git status` after a run stays clean.

## Additional requirements for Section 6

**A Pokémon Red ROM.** Supply your own `.gb` file for Pokémon Red (USA, Europe).
Red specifically, not Blue or Yellow, because completion is detected by reading
Red's Pokédex flag out of emulator memory. `setup.py` puts its path in
`LOCAL_ROM_PATH`.

That is the only file you need to provide. The VBA-RR emulator is included in
`section6_capability/harness/runtime/`, and the staging step below downloads the
Pokémon Access Lua scripts and builds the map database. The third-party licenses
are listed in `section6_capability/README.md`.

**The four local cells run on Windows only.** The constrained and hybrid cells
drive VBA-RR, a Windows emulator with no Linux or macOS build, so they run locally
rather than in the container. Emulator windows will open on your desktop and take
keyboard focus while they run. The two Continual Harness cells run in the
container like everything else.

Build the sealed baseline once. This is a one-shot step and refuses to merge with
an existing baseline:

```bash
cd section6_capability/harness
python tools/stage_local_runtime.py
```

**The PokéAgent Continual Harness**, for the two Continual Harness cells, is already
included in `section6_capability/harness/vendor/continual-harness/` at the exact
commit this evaluation used, `62bf6f614b66ff76b79954e5a3f04f91c3c6a049`, under
its upstream MIT licence. It is included rather than cloned because the
evaluation depends on four small changes to it. `harness/vendor_pins.json`
records that commit and the four changes.

Then run all six cells in one command, the same as sections 4 and 5:

```bash
cd section6_capability/harness
python tools/selftest.py
python orchestrator.py --pilot
python orchestrator.py --full
```

`--full` runs the constrained and hybrid cells one after another on your own
machine and the two Continual Harness cells in the container, each capped at 1,000
model calls, with early exit the moment the Pokédex event bit is read out of
emulator memory. That is the configuration reported in the paper.

There are scoped variants (`--local-only`, `--tool-calling-only`,
`--container-only`, `--model`, `--combine-with`) but none of them are needed for a normal run. They exist to
rerun individual cells and carry the rest forward unchanged, which is only useful
after a cell has already failed. `section6_capability/README.md` covers them.

---

## Dummy Execution 

To prevent our evals from reaching the outside world, we have removed all outgoing access and replaced it with dummy actions. The models, the agent loops, and the tool calls are real. What the agents can reach externally is not. The one exception is the GAIA suite, which searches the live web through Tavily.

# Our Results

The committed results are sortable Excel workbooks. Each begins with a `Guide`
sheet, uses filterable tables throughout, freezes identifying columns, and keeps
long raw fields in numbered continuation columns when Excel's per-cell text limit
requires it. Every workbook has an `Outcomes` sheet with one row per session and
the scored result the paper reports, and raw-turn sheets beside it with the
evidence behind those scores.

- `section4_security/results/security_raw_results.xlsx` contains all 1,640
  security sessions: 1,320 AgentDojo base sessions plus 320 Gray Swan extension
  sessions, across the constrained, OpenClaw and least-privilege OpenClaw arms.
  `study_phase` and `corpus_source` preserve provenance. `Raw Turns` is the 984
  sessions the paper reports, and `Appendix - Pytector` is the repeat pass of the
  first two arms with Pytector added as a warning layer.
- `section5_reliability/results/reliability_raw_results.xlsx` contains all 776
  reliability sessions, 528 synthetic and 248 GAIA, with their constrained model
  calls, OpenClaw transcript events, tool events, and judge verdicts. GAIA is a
  gated dataset, so its turn rows are sealed in `gaia_raw_turns.sealed` with a
  key derived from the dataset itself, and the reference-answer column is
  omitted. Downloading the dataset and running one command restores them;
  `section5_reliability/README.md` explains how.
- `section6_capability/results/capability_raw_results.xlsx` contains all six
  Pokémon cells and their 3,019 turns. Each turn row retains what the model saw,
  what it decided, what happened, and the associated usage/state evidence.

The workbooks replace the earlier loose CSV/JSONL exports; the original source
runs remain reproducible through the harnesses.
