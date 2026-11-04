# Setup and pilot

## 1. Local baseline

To stage the local baseline on a Windows host, run `python setup.py` from the repository root so `LOCAL_ROM_PATH` is set, then run:

```powershell
python tools/stage_local_runtime.py
```

This is intentionally one-shot and refuses to merge with an existing baseline.

## 2. Configuration

Run `python setup.py` from the repository root. It writes `config.env` with the
OpenRouter key and `LOCAL_ROM_PATH`, then verifies each one and checks that
Docker is running. `config.env` is git-ignored.

## 2b. The container for the two Continual Harness cells

The two constrained cells and the two hybrid tool-calling cells run on this
Windows host, one after another. The two Continual Harness cells run in a container. The orchestrator builds the image from this
directory, starts the container, and removes it when the run exits. Docker only
has to be installed and running. The first build takes several minutes; later runs reuse it.

## 3. Offline checks

```powershell
python tools/selftest.py
python -m compileall -q evalspec.py orchestrator.py constrained tool_calling openclaw_eval tools pilot.py
```

## 4. Pilot

The OpenRouter key's configured credit limit is the hard model-spend circuit
breaker. If it is exhausted, the console prints `CREDIT PAUSE` once per minute;
no model requests are sent until the key reports positive `limit_remaining`.
Raise/reset the limit to resume from the current game state. The container remains
billable during a remote pause.

Local-only:

```powershell
python orchestrator.py --pilot --local-only
```

Tool-calling only:

```powershell
python orchestrator.py --pilot --tool-calling-only
```

Container-only:

```powershell
python orchestrator.py --pilot --container-only
```

Container Glimmer-only health pilot:

```powershell
python orchestrator.py --pilot --container-only --model glimmer
```

Both sequential lanes, with no more than one attempt per lane at once:

```powershell
python orchestrator.py --pilot
```

Every lane prints timestamped live status approximately every 20 seconds.
Artifacts are pulled and checksummed immediately after each container attempt.
The container lane presses `START` and selects `NEW GAME` before the first paid model call;
the agent then takes control from the opening introduction.
Container Glimmer lets OpenRouter choose among currently healthy providers and
fall back automatically, while requiring support for every vision/tool request
parameter. Each response records the selected provider. Container Spark remains
pinned to Meta.

The pilot permits exactly five successful model API calls in each cell. Remote
cells use stock OpenClaw's open-ended native agent loop with two typed tools
backed by the official Nous server. The egress gate blocks call six before it
contacts OpenRouter. The pilot rejects missing native screenshot input, session
resume, exact tool/model/provider policy, real game actions, usage/cost evidence,
or complete artifacts. It automatically writes
`pilot_summary.json`, including observed pilot cost and a projection at the
configured 1000-call-per-cell full cap. The console ends with an observed-rate
projection, a 2x-worst-observed-call empirical high estimate, and the actual
hard model-spend maximum before credit pause from the key limit recorded at
launch. Container time is listed separately.

After reviewing that estimate, the full tutorial-completion run is:

```powershell
python orchestrator.py --full
```

To keep the successful local cells and
run only fresh container cells into a new combined results folder:

```powershell
python orchestrator.py --full --container-only --combine-with results\full_20260821_183332
```

To replace one failed remote cell while carrying the other five forward:

```powershell
python orchestrator.py --full --container-only --model glimmer --combine-with results\full_20260826_141336
```

This creates a new timestamped six-cell folder. `--tool-calling-only` works the
same way for the two hybrid cells. It refuses to carry a source
cell unless `session_meta.json` marks it successful, never copies the failed
source Glimmer cell, and leaves the source folder untouched. Container full cells
are governed by Pokédex completion or the 1000-call cap; a seven-day ceiling
exists only as emergency protection against a run that never ends.
