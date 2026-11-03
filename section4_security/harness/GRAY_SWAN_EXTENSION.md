# GraySwan prompt-injection extension

This is a separate, pilot-ready corpus extension. It does not replace or append
to `corpus/injections.jsonl`, and it never writes into the completed base run at
`results/run_2026-08-20_205714`.

## Pinned sources and selection

- Repository: `GraySwanAI/ipi_arena_os`
- Repository commit: `5e2e285910d581c97767195f1d113ef323f9ab07`
- Dataset: `sureheremarv/ipi_arena_attacks`
- Dataset revision: `2ef2d9785ed50981832b199ac89e6a0ff85d666d`
- Dataset file SHA-256: `a15ab6f2d70c5fab9bf28869189742d2daf2db56567da1c4b951121b3ef43087`
- Downloaded: 95 attacks
- Included: 16 attacks from four local-data exfiltration behaviors
- Excluded: 79 attacks, each with a row-level reason in
  `corpus/gray_swan_extension_manifest.jsonl`

The included behavior counts are: `agents-share-keys` (3),
`leak-codebase-zip` (5), `leak-weights` (5), and
`nimbus-llm-exfil` (3). Cloud Drive, webmail/calendar, and public-link prompts
are excluded because they do not target private data stored locally on the
evaluated computer.

Each included attack has exactly one unique deterministic synthetic canary. The
canary exists only in a planted file at its manifest path; its value is never put
in the prompt or mock Discord input. Each rendered input is a plain Discord message
adapted from the source row's persuasion pattern. Its only requested operations are
to read one exact `/home/alex/...` file and reply with its contents in the existing
mock `#general` channel. Arena-specific tool names, shell commands, URLs, cloud
services, external destinations, wrappers, and target-mapping appendices are
forbidden by the offline validator. No tool, permission, system prompt, framework
behavior, or message-delivery mechanism is added or changed for the extension.
Therefore the constrained arm sees the identical message but cannot retrieve the
file, while OpenClaw has only its ordinary native-file opportunity to comply.

## Offline checks (no API calls)

```powershell
python orchestrator.py --plan --greyswan
python tools\validate_gray_swan_extension.py
python tools\selftest.py
```

The planned pilot uses the shortest, median-length, and longest extension prompts
in the eight existing plain framework/model cells: 3 × 8 = 24 sessions. The full
extension is 16 prompts × 16 cells = 256 sessions. After the paid pilot completes,
the runner prints measured cost for every framework/model cell, a prompt-length
interpolated full-run range, projected classifier cost, projected container time, and
the commands below.

## Paid workflow (run manually)

Run these from `<REPO_ROOT>`. The normal workflow is only two
commands. The full command automatically runs classification, post-processing,
and the read-only comparison against the pinned completed base run.

Pilot:

```powershell
python orchestrator.py --pilot --greyswan
```

Full extension:

```powershell
python orchestrator.py --full --greyswan
```

Resume into a new extension run while copying only verified sessions:

```powershell
python orchestrator.py --full --greyswan --resume-from results\run_<extension_timestamp>__gray_swan_extension
```

The following are recovery commands only; they are not part of the normal flow.
Classify and post-process an already completed extension manually:

```powershell
python post_experiment.py results\run_<completed_extension_timestamp>__gray_swan_extension --classify --max-wait 86400
```

Write a separate read-only base-versus-extension report:

```powershell
python combined_report.py results\run_2026-08-20_205714 results\run_<completed_extension_timestamp>__gray_swan_extension
```

The combined reporter writes to a new `results/combined_<timestamp>` directory.
It refuses to place output in either source run and preserves `corpus_source` and
`study_phase` on every combined session row.
