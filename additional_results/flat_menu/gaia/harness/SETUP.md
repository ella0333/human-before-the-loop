# Setup and runbook

## Local preparation

Run `python setup.py` from the repository root. GAIA is a gated dataset: you need
a HuggingFace account, the accepted terms, and `huggingface-cli login` on this
machine. `setup.py` checks all three. No HuggingFace token is ever sent to the
container; the prepared manifest is uploaded with a checksum instead.

```powershell
Set-Location additional_results/flat_menu/gaia/harness
python -m pip install -r requirements.txt
python build_corpus.py --download
python tools/selftest.py
```

The orchestrator uploads the locally prepared, pinned 31-task manifest into the
container and verifies its SHA-256 and row count before any model session starts.

## The container

The orchestrator builds the image from this directory, starts the container,
and removes it when the run exits. Docker only has to be installed and
running. The first build takes several minutes; later runs reuse it.

`--containers 2` starts two containers and spreads the matrix across them.

Startup compares the container's scorer contract with the local harness and stops
before any paid session if they differ.

## Pilot and full run

```powershell
Set-Location additional_results/flat_menu/gaia/harness
python orchestrator.py --pilot
```

The pilot is 8 sessions: the same frozen task once in every framework/model cell.
It is only a smoke test and cost estimate. After artifact verification, it runs the pinned OpenAI-provider semantic judge locally and writes
the requests, responses, and verdicts under `postprocess/`. If judging is
interrupted, resume it without rerunning sessions:

```powershell
python judge_attempts.py results\run_<timestamp>
```

```powershell
python orchestrator.py --full
```

The full run is 248 sessions. Each session is copied and checksummed locally.
Resume creates a new composite run and reuses only clean, checksum-verified cells:

```powershell
python orchestrator.py --full --resume-from results\run_<timestamp>
```

The constrained transport keeps the frozen 3,000-token output budget for its
first three attempts. If a provider returns HTTP 200 but spends that entire
budget on hidden reasoning and supplies no structured content, one final attempt
receives 6,000 tokens. The attempt log records the escalation. This recovery is
triggered by transport evidence, not answer correctness.

When `LEGACY_RUN_DIR` is configured, a successful full or resumed run
automatically judges every GAIA session, generates figures, and writes the
suite-preserving 776-session combined report under the new run directory.
