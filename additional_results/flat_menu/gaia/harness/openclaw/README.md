# OpenClaw reliability arm

The container pins stock `openclaw@2026.7.1-2`; no OpenClaw source is patched.
The loopback `mock-discord` channel supplies a selected GAIA prompt and captures
the ordinary final reply. The external `benchmark-tools` plugin provides bounded
public-web search/page extraction through Tavily and pure arithmetic.

These tools cannot modify external state. Native web/browser, filesystem,
runtime, message, media, automation, memory, and subagent tools are denied. Each
trial uses a unique OpenClaw session and preserves its transcript. Runtime
preflight fails closed unless model routes, provider pins, bindings, plugin set,
thinking level, channel accounts, and exact tool policy match the experiment
specification.
