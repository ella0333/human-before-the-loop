# Section 6: Capability

Completing the Pokémon Red tutorial with no prior instructions, as a long-horizon
realized capability benchmark. Two models in three frameworks, six cells, each
capped at 1,000 model calls.

Paper result: both models finished through the constrained framework. Muse Glimmer
30B took 341 gameplay turns and 342 model calls at $0.28, including 79 battle
decisions; Muse Spark 1.2 took 58 turns and 58 calls at $0.15. Under the
tool-calling harness Spark finished in 441 calls at $6.22, while Glimmer never got
past the starting house, exhausting the 1,000-call cap after 7.98 hours, 12.8
million input tokens, and $5.63, without making a single battle decision. The
hybrid agent, which offers the constrained framework's current choices as native
tool calls alongside raw button presses and screenshots, let Spark finish in 178
calls at $0.78, while Glimmer stopped at the cap in Oak's Lab at $0.86.

## Setup

| Cell | Framework | Runtime | Where it runs |
|---|---|---|---|
| `constrained__glimmer` | constrained menu system | isolated VBA-RR + Lua bridge | locally on Windows, not Docker |
| `constrained__spark` | constrained menu system | isolated VBA-RR + Lua bridge | locally on Windows, not Docker |
| `tool_calling__glimmer` | hybrid: constrained choices as native tool calls | isolated VBA-RR + Lua bridge | locally on Windows, not Docker |
| `tool_calling__spark` | hybrid: constrained choices as native tool calls | isolated VBA-RR + Lua bridge | locally on Windows, not Docker |
| `continual_harness__glimmer` | Continual Harness `simplest` scaffold | isolated PyBoy | Docker container |
| `continual_harness__spark` | Continual Harness `simplest` scaffold | isolated PyBoy | Docker container |

**The four local cells run on Windows, and Windows only.** They drive `VBA-RR`, a
Windows emulator, so they cannot go in the container: the images here are Linux
and the emulator has no Linux or macOS build. A Mac cannot run these cells.
Emulator windows will open on your desktop and take keyboard focus. The local
cells run one after another, constrained first and then tool-calling. Only the two
Continual Harness cells run in Docker, alongside the local cells.

The hybrid cells use the constrained cells' runtime, state text, history, boot
boundary and completion check unchanged. The only difference is how the model
decides. Each current choice is offered as an argument of a named tool (`go_to`,
`walk`, `battle_action`, `choose_pokemon`, `menu_select`, `other_action`, and
`enter_name` when a name is requested), a raw `press_buttons` tool is always
offered, and the current emulator frame is attached to every decision. The code is
in `harness/tool_calling/`.

The objective is complete when Pokémon Red's **actual Pokédex event bit**
(`0xD74B & 0x20`) is observed twice in emulator memory. The evaluator stops
immediately on that reading. Model prose claiming success is never accepted.

Fixed contracts:

- 1,000 paid-call cap per cell and a 1,000 harness-step cap, with early exit on
  Pokédex completion. The constrained cells additionally cap at four hours; the
  other cells' seven-day ceiling exists only as emergency protection against a
  run that never ends, so the call cap is the practical limit.
- One successful paid model response is the primary turn. Harness steps, button
  groups, physical button inputs, battle state, memory calls, tokens, timestamps,
  wall time, and cost are all logged separately.
- The container lane deterministically presses `START` and selects `NEW GAME` before
  scoring begins. Those two bootstrap inputs are recorded as unscored setup so all
  frameworks receive the same first scored boundary.
- OpenRouter credit exhaustion pauses the request and keeps the emulator and container
  alive. Raising the key limit resumes from the current game state.
- If a `go_to` target cannot be reached on the pathfinder's grid, the Lua bridge
  logs the error to `bridge_errors.txt` and keeps running instead of stopping. If
  the bridge stops anyway, the tool-calling runner restarts it in the same emulator
  before spending another model call.

The comparison scaffold is deliberately unassisted: `simplest` receives native
screenshots and openly chooses between `press_buttons` and `process_memory`. It has
no walkthrough, pathfinding, skills, subagents, or prompt optimization.

## The Continual Harness scaffold

Used by the two `continual_harness__*` cells. There is nothing to install: it is
included in `harness/vendor/continual-harness/` at the exact commit this
evaluation used, `62bf6f614b66ff76b79954e5a3f04f91c3c6a049`, redistributed under
its upstream MIT licence, which is kept beside it.

It is included rather than cloned because the evaluation depends on four small
changes to it, listed below. A clean upstream clone would not behave the same
way.

`vendor_pins.json` records that commit and the four changes made to the scaffold
for this evaluation. They add OpenRouter base-URL routing plus the credit/call/cost
gate, expose `flags.has_pokedex` from the real event flag instead of inferred
seen/caught counts, register the upstream `max_steps` parameter in the CLI parser so
the documented agent loop can receive the evaluation cap, and perform the
deterministic START / NEW GAME bootstrap described above.

## The ROM is the only thing you supply

Everything else the local cells need is bundled in `harness/runtime/`:

| Bundled | What it is |
|---|---|
| `runtime/VBA-RR/` | the VBA-ReRecording emulator build (`vba.exe`, `lua51.dll`) the bridge drives |
| `runtime/emulator-access/` | Pokémon Access 3.1.0, the Lua accessibility scripts `constrained/pokemon_bridge.lua` loads |
| `runtime/map_database.json` | the tile/map database used for navigation |

Set one variable in the repository's `config.env`:

```
LOCAL_ROM_PATH=<absolute path to your own Pokémon Red (USA/Europe) .gb>
```

Red specifically: the objective reads Red's Pokédex event bit at `0xD74B`. No ROM
is distributed here and none is committed; `.gb`, `.gbc`, `.gba`, `.sav`, `.sgm`,
and `.state` are all refused by `tools/check_repo_safety.py`. The emulator's
`battery/` and `save/` directories ship empty.

`LOCAL_VBA_DIR`, `LOCAL_MAP_DB`, and `LOCAL_EMULATOR_ACCESS_DIR` are optional
overrides if you want your own copies. Leave them unset to use the bundled ones.

Then stage a sealed baseline. This is one-shot by design and refuses to merge with
an existing baseline:

```powershell
python tools/stage_local_runtime.py
```

Every local cell copies that baseline into a unique `local_runtime/attempts/<id>`
with its own ROM copy, emulator copy, save directory, and bridge directory. Source
checksums are verified before and after every session, so a run cannot quietly
mutate the baseline.

## Running it

```powershell
cd harness
python tools/selftest.py
python tools/test_openrouter_gate.py
python tools/check_repo_safety.py
python -m compileall -q evalspec.py orchestrator.py constrained tool_calling openclaw_eval tools pilot.py

python orchestrator.py --pilot          # all six cells; exactly 5 paid calls per cell
python orchestrator.py --full
```

Scoped variants:

```powershell
python orchestrator.py --pilot --local-only
python orchestrator.py --pilot --tool-calling-only
python orchestrator.py --pilot --container-only
python orchestrator.py --pilot --container-only --model glimmer
```

The pilot permits exactly five successful model API calls per cell; the egress gate
blocks call six before it reaches OpenRouter. It rejects missing screenshot input,
session resume, wrong tool/model/provider policy, absent real game actions,
missing usage/cost evidence, or incomplete artifacts. It writes `pilot_summary.json`
and prints an observed-rate projection, a 2x-worst-observed-call high estimate, and
the hard model-spend maximum implied by your key's credit limit.

To rerun only some cells into a new six-cell folder, carrying completed cells
forward unchanged:

```powershell
python orchestrator.py --full --container-only --combine-with results\<previous>
python orchestrator.py --full --tool-calling-only --model glimmer --combine-with results\<previous>
```

A source cell is only carried if its `session_meta.json` marks it successful, and
the source folder is never modified.

One container is used sequentially for the two Continual Harness cells. It is
removed when the run exits, unless artifact recovery failed; then it is left
running so nothing is lost.

## Results layout

`results/capability_raw_results.xlsx` holds everything in three sheets: `Guide`,
`Raw Turns` (all 3,019 turns across the six cells) and `Outcomes` (one row per
cell). For any turn you can see what the agent was shown, what it decided, and
the token, latency and cost data for that call. `tutorial_complete` in `Outcomes`
is the headline outcome, read from Red's Pokédex event bit in emulator memory
rather than from anything the model said, and `model_calls` is the measure the
paper compares. The tool-calling cells add `semantic_actions`,
`raw_button_presses`, `frames_attached` and `bridge_caught_errors`.

The Continual Harness cells store the full state prompt on every turn, which grows
through a run. Where one exceeds Excel's 32,767-character cell limit it continues
in `shown_text_or_prompt_2`; join the columns in order to recover it exactly.

Paper figures are in the repository-level `figures/` directory.
