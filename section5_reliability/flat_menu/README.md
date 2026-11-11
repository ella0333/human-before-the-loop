# Section 5 additional results: flattened menu

The constrained framework from section 5 rerun with its menu hierarchy removed.
Every option the nested menu offers is listed on a single screen, so the agent
reaches any action in one selection. Everything else matches section 5: the same
four models, the same 66 synthetic prompts and 31 GAIA tasks, the same providers,
reasoning effort, schemas, budgets and scorers. Only the constrained arm is run;
the OpenClaw results are the ones in section 5.

## Results

| Suite | Sessions | Result | Section 5 nested menu |
|---|---:|---:|---:|
| Synthetic, full task correct | 264 | 90.5% | 92.4% |
| Synthetic, first action correct | 264 | 92.8% | 93.6% |
| GAIA, task completed | 124 | 61.3% | 58.1% |

| Model | Synthetic full task | GAIA task completed |
|---|---:|---:|
| GPT-5.6 Luna | 97.0% | 71.0% |
| GPT-OSS-20B | 80.3% | 38.7% |
| Muse Glimmer 30B | 89.4% | 58.1% |
| Muse Spark 1.2 | 95.5% | 77.4% |

All 388 sessions completed. On the synthetic suite, reasoning-action agreement
was measurable in all 264 sessions and was 95.8%.

The flat menu takes fewer model calls per session but sends a longer prompt on
each one. On the synthetic suite a session averaged 1.34 calls and 778 input
tokens; on GAIA it averaged 5.04 calls and 37,209 input tokens, because the full
menu returns on every turn.

## Setup

**Synthetic suite.** The 27 final choices of the nested menu are listed on one
screen, lettered A to Z and then AA. Each label is the nested menu's own labels
joined with " > ", for example `Play a Video Game (Zork, Pokemon, more) > Text
Adventure > Zork`, so the flat and nested agents read the same words. Choosing an
option runs the same activity the nested menu reaches, including any free-text
step that follows, such as writing a journal entry or a direct message. Each
flat option records the nested menu path it stands for, so scoring and the
reasoning judge use the same expected paths as section 5.

**GAIA suite.** The 27 options of the menu hierarchy are listed on one screen,
and the same screen returns every turn with the `done` and `answer` fields. There
is no activity to enter and stay inside: on any turn the agent can search the
web, read a page, use the calculator, pick a mock activity, or finish with its
answer.

## Running

Both harnesses are copies of the section 5 harnesses with a `--flat-menu` flag
added. They are configured by `python setup.py` at the repository root, and the
GAIA suite has the same Tavily and HuggingFace requirements as section 5.

```powershell
cd section5_reliability/flat_menu/synthetic/harness
python build_corpus.py
python tools/selftest.py
python orchestrator.py --pilot --flat-menu
python orchestrator.py --full --flat-menu --no-classify
python post_experiment.py results\run_<timestamp> --classify --max-wait 86400
```

```powershell
cd section5_reliability/flat_menu/gaia/harness
python build_corpus.py --download
python tools/selftest.py
python orchestrator.py --pilot --flat-menu
python orchestrator.py --full --flat-menu
```

An interrupted or partly failed run reuses its clean sessions:

```powershell
python orchestrator.py --full --flat-menu --resume-from results\run_<timestamp>
```

## Results layout

`results/` holds `flat_menu_raw_results.xlsx` and `gaia_raw_turns.sealed`.

| Sheet | What it is |
|---|---|
| `Guide` | what each sheet and the less obvious columns mean |
| `Synthetic Raw Turns` | every model call of the 264 synthetic sessions |
| `GAIA Raw Turns` | a placeholder; the 625 rows are sealed, see below |
| `Outcomes` | one row per session, all 388, with the scored result |
| `Tokens Per Turn` | mean tokens per call and per session, by suite and model |
| `Reruns` | every session the harness discarded and ran again, with the cause |

The GAIA turn rows are sealed the same way as in section 5, because they quote
the gated GAIA questions. Anyone with their own copy of the dataset can open them:

```powershell
cd section5_reliability/flat_menu/gaia/harness
python build_corpus.py --download
python tools/restore_gaia_fields.py
```

That writes `flat_menu_raw_results.restored.xlsx` beside the original with the
sheet filled in, every quoted question back in place, and the reference answers
added to `Outcomes`. The section 5 README describes the seal in full.

### Reruns

A session is rerun when it ends with a provider or parse error, and the rerun
replaces the failed attempt's files. The `Reruns` sheet keeps the cause of every
one from the run log. On the synthetic suite, two sessions were rerun after a
provider rate limit. On GAIA, five sessions were rerun 13 times. Seven of those
reruns were empty model output, where Muse Spark spent its whole output budget
on reasoning and returned no answer. Three were an error logged inside an
otherwise completed session, and three were the account's credit limit. The
same two GAIA tasks produced empty Muse Spark output in section 5.
