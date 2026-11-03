# Pokémon Red Tutorial Eval

Six-cell evaluation of Muse Glimmer 30B and Muse Spark 1.2 across:

| Lane | Framework | Runtime |
|---|---|---|
| Local | constrained menu system | isolated VBA-RR/Lua attempt |
| Local | hybrid: constrained choices as native tool calls (`tool_calling/`) | isolated VBA-RR/Lua attempt |
| Container | Continual Harness v5.0.0 `simplest` | isolated PyBoy Red attempt |

The full objective is complete when Pokémon Red's actual Pokédex event bit
(`0xD74B & 0x20`) is observed twice. The evaluator stops immediately; prose
from the model is never accepted as success.

## Fixed contracts

- Glimmer: `meta/muse-glimmer-30b-20260810`. Constrained remains pinned to
  DeepInfra; the container lane uses OpenRouter health-aware routing with provider fallback
  and requires full vision/tool parameter support.
- Spark: `meta/muse-spark-1.2-20260805`, OpenRouter pinned to Meta.
- Every container response records the provider actually selected. The model ID
  remains fixed even when Glimmer's provider changes.
- Pilot: exactly five successful paid calls per cell.
- Full: 1000 paid-call/turn cap per cell and 1000 harness-step cap, with early
  exit on Pokédex completion. Constrained retains its four-hour cap. The tool-calling
  cells and the container lane use a seven-day emergency ceiling so the call cap is the practical limit.
- One successful paid model response is the primary turn. Harness steps,
  button groups, physical button inputs, battle state, memory calls, tokens,
  timestamps, wall time, and cost are logged separately.
- The container lane deterministically presses `START` and selects `NEW GAME` before model
  scoring begins. These two bootstrap inputs are recorded as unscored setup;
  both models receive the same first scored boundary.
- OpenRouter credit exhaustion pauses the request and keeps the emulator and container
  alive. Raising the key limit lets the same request resume.

Continual Harness is vendored at commit
`62bf6f614b66ff76b79954e5a3f04f91c3c6a049`. Its documented `simplest`
agent receives native screenshots and openly chooses between
`press_buttons` and `process_memory`. It has no walkthrough, pathfinding,
skills, subagents, or prompt optimization. The only integration patches are
recorded in `vendor/continual-harness/EVAL_PIN.json`.

## Local isolation

The licensed Red ROM remains at:

`<POKEMON_RUNTIME>/pokemon_red.gb`

`python tools/stage_local_runtime.py` creates a sealed, ignored
`local_runtime/baseline_red`. Each cell copies that baseline into a unique
`local_runtime/attempts/<id>`, including a private ROM copy, VBA-RR copy,
save directory, Lua bridge directory, and metadata. Source checksums are
verified before and after every session. The older Blue baseline and saves are
not read or modified.

## Commands

```powershell
cd <REPO_ROOT>
python orchestrator.py --pilot
```

The pilot runs the four sequential local cells (constrained, then tool-calling) and
both sequential container cells; one local and one container cell may run at the
same time. It prints a total six-cell cost projection, per-model totals, empirical high estimate, and the
hard model-spend maximum imposed by the OpenRouter key limit.

After a valid pilot:

```powershell
python orchestrator.py --full
```

Useful scoped variants:

```powershell
python orchestrator.py --pilot --local-only
python orchestrator.py --pilot --tool-calling-only
python orchestrator.py --pilot --container-only
python orchestrator.py --pilot --container-only --model glimmer
```

To rerun only the two container cells while carrying the completed local cells
into one new timestamped six-cell results folder:

```powershell
python orchestrator.py --full --container-only --combine-with results\full_20260821_183332
```

The new folder includes `combined_from.json` provenance, copies the four local
cell artifacts unchanged, and replaces the prior container evidence
with fresh Glimmer and Spark attempts. The source folder is never modified.

To replace one failed remote cell while retaining the other five:

```powershell
python orchestrator.py --full --container-only --model glimmer --combine-with results\full_20260826_141336
```

The new folder copies and validates those five successful cells, excludes the
source Glimmer failure, and writes only the fresh Glimmer artifacts in its
place. The container full attempt stops on Pokédex completion or 1000 successful
model calls; the seven-day wall clock is emergency billing protection only.

The operator script uses one container sequentially for Glimmer then Spark,
verifies every artifact archive, and removes the container when the run exits,
unless artifact recovery failed; then it is left running so nothing is lost.

## Model-free verification

```powershell
python tools/selftest.py
python tools/test_openrouter_gate.py
python tools/check_repo_safety.py
```

CI builds the private GHCR image and boots it with a fake key for a no-model-call
runtime preflight. ROMs, saves, keys, results, and local runtimes are excluded
from Git and the container image.
