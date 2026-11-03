# Setup and runbook

OpenClaw is installed only inside the container image, never on your own machine.
A CPU-only host is enough: all four models are called through OpenRouter.

## 1. The container

The orchestrator builds the image from this directory, starts the container,
and removes it when the run exits. Docker only has to be installed and
running. The first build takes several minutes; later runs reuse it.

## 2. Configure credentials

From the repository root:

```powershell
python setup.py
```

That writes `config.env` with `OPENROUTER_API_KEY` and checks it, along with
whether Docker is running. `config.env` is git-ignored. To override one value here only, add a `.env` in this
directory.

Install local orchestration dependencies:

```powershell
pip install paramiko httpx
```

## 3. Offline validation


```powershell
python tools/selftest.py
```

This spends nothing and does not run an agent evaluation. It checks imports, the
corpus size and template provenance, canary isolation, realistic workspace
placement, stable OpenClaw pin, single-Gateway lifecycle, channel port separation,
model fallback prevention, and fail-closed tool policy.

## 4. Pilot

```powershell
python orchestrator.py --pilot
```

The four pilot sessions are:

1. constrained plain × GPT-5.6 Luna
2. constrained plain × GPT-OSS-20B
3. OpenClaw plain × GPT-5.6 Luna
4. OpenClaw plain × GPT-OSS-20B

The constrained menu's `completed` and `could_not_complete` values both mean the
session deliberately stopped. Neither is an execution failure; benign utility and
attack behavior are derived from the recorded actions and artifacts instead.

GPT-OSS-20B is the open-weight OpenAI MoE model (21B total, 3.6B active), not a
dense 20B model. It supports configurable reasoning and observable
chain-of-thought. The study pins `openai/gpt-oss-20b` through OpenRouter and uses
`low` reasoning in stock OpenClaw.

The constrained classifier deliberately does not consume provider-native reasoning
text. All four models use `low` effort while excluding the native trace. All
constrained routes emit the same required internal `thinking` field directly
in strict structured JSON for attempt analysis. Native
reasoning-token counts and the complete provider response envelope remain available
for auditability. A unique logged OpenRouter `session_id` keeps every multi-turn
constrained session on one provider
endpoint. Luna is pinned to OpenAI, GPT-OSS to Parasail, Glimmer to DeepInfra,
and Spark to Meta, with fallback and unsupported-parameter routing disabled in both
frameworks. Up to three attempts are made only
for transient transport/HTTP failures or provider-declared 429/5xx error envelopes,
and every attempt is retained in `raw_calls.jsonl`.

The image creates all four OpenClaw model agents and their channel-account bindings
before the Gateway starts. Sessions do not invoke `models set`, edit OpenClaw
configuration, or restart/reload the Gateway.

OpenClaw uses its documented `agents.defaults.thinkingDefault: low`. Reasoning
is retained in the native transcript but is not made channel-visible, preventing a
thinking trace from becoming a synthetic Discord leak. All model routes fail unless
the transcript proves `low`.

Before the first session, `tools/runtime_preflight.py` parses OpenClaw's JSON and
requires the exact four-model catalog (with `openrouter/auto` absent), model routes,
filesystem/message-only policy, expected stable loaded plugins, healthy channel
accounts, and all four loopback listeners. Each completed OpenClaw session then checks
the actual transcript model id and effective five-tool inventory. A failed or
incomplete pilot session stops cost projection and exits nonzero instead of being
treated as a zero-cost measurement.

They all use `important_instructions__g_ssh`. The pilot proves all four model routes,
both framework paths, Pytector availability in the image, native OpenClaw transcript and
provider-billed usage capture, mock-channel delivery, canary detection, log transfer,
and teardown.
It prints a full-run model-cost range and projected sequential run time. It does not
submit the batch judge.

For the updated image, each session should print a line like:

```text
[logs] verified 9 files, 12345 bytes, sha256=abc123...
```

The final block must say `log check : PASS`, show `8 sessions, 8 verified transfer
receipts`, and report a raw hash snapshot. Anything else exits nonzero and suppresses
the cost projection.

## 5. Full run

```powershell
python orchestrator.py --full
```

This runs all 1,056 sessions, then submits remaining `PENDING` sessions to the
OpenRouter Batch API for attempt classification. To separate those steps:

```powershell
python orchestrator.py --full --no-classify
python post_experiment.py results\run_<timestamp> --classify
```

Watch progress from another shell:

```powershell
Get-Content -Wait results\run_<timestamp>\run.log
```

The batch command is resumable. It stores the exact request, batch id, every poll,
the complete final API response, individual parse failures, and judgments in
`postprocess/`. If the batch is still running when the local wait ends, rerun the
same command; it polls the existing batch instead of paying for a duplicate. The
plain model slug `openai/gpt-5.6-luna` is used; OpenRouter applies batch pricing to
requests submitted through its Batch API.

To validate or graph a pilot without submitting a judge batch:

```powershell
python post_experiment.py results\run_<timestamp> --check-only
```

This produces an immutable hash manifest, a validation report, a session-level
evidence CSV, final aggregate tables, and three SVG comparison charts. It never
modifies a per-session raw artifact.

## Security and failure behavior

- No real Discord token or endpoint is configured.
- Only the synthetic `/home/alex` tree is reset between sessions.
- The canary answer manifest is outside `/home/alex`.
- OpenClaw runs the stable release and its ordinary Gateway/channel/file-tool path;
  no OpenClaw source is patched.
- Native file tools are workspace-only. Native message delivery can resolve only to
  the loopback mock channel. Runtime/exec and every network-capable model tool are
  denied.
- A startup mismatch in the version, plugin, Gateway health, or policy aborts before
  the matrix. Five consecutive session failures stop the run. The container is
  removed when the run exits, except when an artifact transfer cannot be verified:
  then it is left running and the console prints how to remove it once `/run/out`
  has been recovered.

If first boot is unusually slow, the orchestrator waits up to
`GATEWAY_START_TIMEOUT` seconds (default 900) and prints progress every 30 seconds.
On a genuine failure it includes `/run/injection-eval-startup.log`, process state,
Gateway status, and health output so the container can be inspected.
