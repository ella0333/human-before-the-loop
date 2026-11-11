# Setup and runbook

## Local configuration

Run `python setup.py` from the repository root; it writes `config.env` and checks
each credential. Then build the corpus and run the offline checks:

```powershell
python build_corpus.py
python tools/selftest.py
```

## The container

The orchestrator builds the image from this directory, starts the container,
and removes it when the run exits. Docker only has to be installed and
running. The first build takes several minutes; later runs reuse it.

## Pilot and full run

```powershell
python orchestrator.py --pilot
```

The pilot runs all eight cells on `REL-D03`, validates receipts and hashes, and is
kept in its own timestamped directory. Do not use it in the main results.

After reviewing the pilot:

```powershell
python orchestrator.py --full
```

The orchestrator disconnects once session evidence is verified locally. Luna
batch classification runs afterward. If local polling
ends first, the terminal prints the remote batch ID and exact resume command.
