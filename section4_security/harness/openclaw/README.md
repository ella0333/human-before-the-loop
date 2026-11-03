# OpenClaw evaluation arm

This image installs the latest stable release available when the experiment was
rebuilt: `openclaw@2026.7.1-2` (`v2026.7.1-2`, published 2026-08-04). No OpenClaw
source is patched. The container uses documented non-interactive onboarding and
multi-agent routing: all four study models receive fixed agents and channel-account
bindings before one foreground Gateway starts. The configured model catalog contains
only those four models (not OpenRouter Auto). Sessions never mutate live config.

## Mock boundary

`plugins/mock-discord` is an external channel plugin, not an agent implementation.
It accepts a trusted loopback inbound event and uses OpenClaw's channel runtime. The
model receives the ordinary OpenClaw agent, system prompt, file tools, and shared
native `message` tool. Outbound messages are appended to a local JSONL sink.

There is no Discord SDK, Discord token, public listener, webhook, or request to a
Discord domain. The mock is deliberately at the transport boundary so model-visible
tool behavior remains OpenClaw-native while external effects remain synthetic.

## Tool policy

Allowed: `group:fs` and `message`.

Denied: web, runtime/exec/process, browser, media, nodes/computer, automation,
subagents/session messaging, memory, and UI/control surfaces. File tools are limited
to `/home/alex` with `tools.fs.workspaceOnly=true`. The only configured channel is
the loopback mock. The Gateway itself retains HTTPS access to OpenRouter.

This deliberately tests capability architecture: OpenClaw can read and alter files
inside a realistic synthetic home; the constrained agent has no filesystem action
in its schema. It does not test arbitrary host shell compromise.

## Isolation and logging

Every event uses a unique channel conversation id. The mock ingress returns the
exact OpenClaw session key after the native channel turn completes. The harness then
copies that session's native transcript and session metadata, including tool
inventory, token counts, provider-billed cost, and model id. The session fails closed
if the transcript model or effective tool inventory differs from the study cell.
`/home/alex` is reset and replanted before the next sequential session.

## Sources

- OpenRouter integration: https://openrouter.ai/docs/cookbook/coding-agents/openclaw-integration
- OpenClaw OpenRouter provider: https://docs.openclaw.ai/providers/openrouter
- Channel plugins: https://docs.openclaw.ai/plugins/sdk-channel-plugins
- Tool policy/security: https://docs.openclaw.ai/gateway/security
- Stable release: https://github.com/openclaw/openclaw/releases/tag/v2026.7.1-2
