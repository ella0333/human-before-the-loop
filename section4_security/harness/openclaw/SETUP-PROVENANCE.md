# Setup provenance

| Repository mechanism | Primary source / reason |
|---|---|
| `openclaw@2026.7.1-2` | Latest non-beta GitHub release on 2026-08-19; avoids the previous beta-only `agent exec` design. |
| `openclaw onboard --non-interactive ... --auth-choice apiKey --token-provider openrouter` | OpenRouter's OpenClaw quick-start command and stable CLI flags. |
| `openclaw models set openrouter/<author>/<model>` | OpenRouter and OpenClaw provider documentation. |
| `openclaw agents add ... --model ... --bind ...` | OpenClaw CLI automation and multi-agent routing; all model routes are fixed before Gateway startup. |
| `openclaw gateway run` | Normal documented service lifecycle; exactly one foreground Gateway is container PID 1. |
| External `mock-discord` channel plugin | OpenClaw's documented generic channel SDK; preserves native inbound routing and the native shared `message` tool. |
| `tools.allow`, `tools.deny`, `tools.fs.workspaceOnly` | OpenClaw tool-policy and security documentation. |
| `/home/alex` workspace | Experimental fixture: a realistic synthetic workstation home, not a claim from OpenClaw docs. |
| Pytector warning prepended to inbound post | Experimental defense manipulation, identical in both agent arms. |
| CPU-only container | Inference is remote through OpenRouter, so no GPU is required when OpenClaw delegates inference. |

The earlier repository mixed three incompatible designs: a beta-only `agent exec`
CLI, a long-lived Gateway channel, and a custom mock shell. The rebuilt path uses
only the stable Gateway/channel route. OpenClaw itself is stock; the environment and
channel transport are mocked externally.
