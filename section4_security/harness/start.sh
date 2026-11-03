#!/bin/bash
set -Eeuo pipefail
mkdir -p /run/sshd /run/mock-discord /root/.ssh /home/alex
exec > >(tee -a /run/injection-eval-startup.log) 2>&1
if [ -n "${PUBLIC_KEY:-}" ]; then
  printf '%s\n' "$PUBLIC_KEY" >> /root/.ssh/authorized_keys
fi
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys 2>/dev/null || true
/usr/sbin/sshd
: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY must be supplied to the container}"
install -d -m 700 /run/injection-eval
(umask 077 && printf '%s' "$OPENROUTER_API_KEY" > /run/injection-eval/openrouter.key)

openclaw onboard --non-interactive --accept-risk --skip-health --mode local \
  --auth-choice apiKey --token-provider openrouter --token "$OPENROUTER_API_KEY" \
  --skip-bootstrap --skip-skills
openclaw plugins inspect mock-discord --runtime --json >/dev/null
openclaw config set channels.mock-discord \
  '{"enabled":true,"defaultAccount":"luna","accounts":{"luna":{"enabled":true,"port":18890,"outboxPath":"/run/mock-discord/outbox.jsonl"},"oss":{"enabled":true,"port":18891,"outboxPath":"/run/mock-discord/outbox.jsonl"},"glimmer":{"enabled":true,"port":18892,"outboxPath":"/run/mock-discord/outbox.jsonl"},"spark":{"enabled":true,"port":18893,"outboxPath":"/run/mock-discord/outbox.jsonl"}}}' --strict-json
openclaw models set openrouter/openai/gpt-5.6-luna
openclaw config set agents.defaults.models \
  '{"openrouter/openai/gpt-5.6-luna":{"params":{"provider":{"only":["openai"],"allow_fallbacks":false}}},"openrouter/openai/gpt-oss-20b":{"params":{"provider":{"only":["parasail"],"allow_fallbacks":false}}},"openrouter/meta/muse-glimmer-30b":{"params":{"provider":{"only":["deepinfra"],"allow_fallbacks":false}}},"openrouter/meta/muse-spark-1.2":{"params":{"provider":{"only":["meta"],"allow_fallbacks":false}}}}' \
  --strict-json --replace
openclaw config set agents.defaults.workspace '"/home/alex"' --strict-json
openclaw config set agents.defaults.skipBootstrap true --strict-json
openclaw config set agents.defaults.thinkingDefault '"low"' --strict-json
if ! openclaw agents list --json | grep -q '"oss"'; then
  openclaw agents add oss --workspace /home/alex \
    --model openrouter/openai/gpt-oss-20b \
    --bind mock-discord:oss --non-interactive --json
fi
if ! openclaw agents list --json | grep -q '"glimmer"'; then
  openclaw agents add glimmer --workspace /home/alex \
    --model openrouter/meta/muse-glimmer-30b \
    --bind mock-discord:glimmer --non-interactive --json
fi
if ! openclaw agents list --json | grep -q '"spark"'; then
  openclaw agents add spark --workspace /home/alex \
    --model openrouter/meta/muse-spark-1.2 \
    --bind mock-discord:spark --non-interactive --json
fi
openclaw agents bind --agent main --bind mock-discord:luna --json
openclaw config set tools.profile '"full"' --strict-json
openclaw config set tools.deny \
  '["group:web","group:runtime","group:media","group:nodes","group:automation","browser","exec","process","terminal","portal","dashboard","screen","show_widget","canvas","sessions_spawn","subagents","conversations_send","sessions_send","memory_search","memory_get"]' --strict-json
openclaw config set tools.allow '["group:fs","message"]' --strict-json
openclaw config set tools.fs.workspaceOnly true --strict-json
openclaw config set plugins.allow '["mock-discord"]' --strict-json
openclaw config validate
openclaw agents list --bindings --json

exec openclaw gateway run
