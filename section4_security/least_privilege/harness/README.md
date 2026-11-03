# Least-privilege OpenClaw

This harness runs the third arm of the section 4 security evaluation. It is the
same OpenClaw `2026.7.1-2` agent as `section4_security/harness`, with its file
tools removed. The agent keeps OpenClaw's native `message` tool and gains eleven
mock activity tools, one for each activity in the constrained framework's menu, so
it can reach exactly what the constrained agent can reach and nothing more.

Everything else is identical to the main section 4 harness: the AgentDojo and
Gray Swan corpora, the synthetic `/home/alex` workspace and its canaries, the
deterministic success detection, the attempt classifier, and the four models. The
full run is 328 sessions, 264 from AgentDojo and 64 from Gray Swan.

## Running it

Run the main section 4 harness first, because the combined report places all three
arms side by side and reads those runs. Then:

```powershell
cd section4_security/least_privilege/harness
python tools/selftest.py
python orchestrator.py --pilot
python orchestrator.py --full
```

`--pilot` runs four sessions, one per model, and prints a cost projection.
`--full` runs the AgentDojo corpus, then the Gray Swan corpus, classifies both, and
writes `results/combined_matched_<timestamp>/` with the three-arm tables. The
combined report uses the newest completed runs in `section4_security/harness/results/`.
To choose specific runs instead:

```powershell
python combined_matched_report.py results\run_<agentdojo> results\run_<grayswan>__gray_swan_extension --original-base-run ..\..\harness\results\run_<agentdojo> --original-extension-run ..\..\harness\results\run_<grayswan>__gray_swan_extension
```

A single corpus can still be run on its own with `--greyswan` or `--corpus`, and an
interrupted run resumes with `--resume-from`, exactly as in the main harness. Its
[README](../../harness/README.md) and [SETUP.md](../../harness/SETUP.md) cover the
shared details, and [openclaw/README.md](openclaw/README.md) lists the tool policy
for this arm.

Each OpenClaw session is rejected unless its effective tool inventory is exactly
`message` plus the eleven mock activity tools. Every call to a mock activity tool is
saved in the session's `mock_events.jsonl`.
