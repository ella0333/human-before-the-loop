# Setup provenance

The experiment uses documented OpenClaw surfaces: stable non-interactive
onboarding, fixed multi-agent model bindings, one foreground Gateway, an external
channel plugin, external agent tools registered with `api.registerTool`, exact
tool allow/deny policy, and native transcript storage. The OpenClaw implementation
is unchanged; only the external mock environment and plugins are experiment code.

Primary references:

- https://openrouter.ai/docs/cookbook/coding-agents/openclaw-integration
- https://docs.openclaw.ai/providers/openrouter
- https://docs.openclaw.ai/plugins/sdk-channel-plugins
- https://docs.openclaw.ai/plugins/building-plugins
- https://docs.openclaw.ai/tools
