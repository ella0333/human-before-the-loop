# OpenClaw reliability arm

The container pins stock `openclaw@2026.7.1-2`. No OpenClaw source is patched.
The loopback `mock-discord` channel supplies the verbatim user prompt and retains
the stock native `message` tool. The separate `mock-activities` plugin registers
11 real native tools mirroring every constrained main-menu activity.

All external effects are local JSONL mock events. The model cannot access public
web/browser/runtime/filesystem/automation/subagent tools or real service tokens.
Each trial has a unique native OpenClaw session and preserved transcript. Runtime
preflight fails closed unless model routes, bindings, plugin set, thinking level,
channel accounts, and exact tool policy match the experiment specification.
