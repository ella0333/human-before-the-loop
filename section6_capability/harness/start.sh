#!/bin/bash
set -Eeuo pipefail
mkdir -p /run/sshd /run/pokemon-eval /run/pokemon-eval/cells /root/.ssh
exec > >(tee -a /run/pokemon-eval/startup.log) 2>&1
if [ -n "${PUBLIC_KEY:-}" ]; then
  printf '%s\n' "$PUBLIC_KEY" >> /root/.ssh/authorized_keys
fi
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys 2>/dev/null || true
/usr/sbin/sshd
: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY must be supplied to the container}"
(umask 077 && printf '%s' "$OPENROUTER_API_KEY" > /run/pokemon-eval/openrouter.key)

python -m openclaw_eval.openrouter_gate >> /run/pokemon-eval/gate.log 2>&1 &
echo $! > /run/pokemon-eval/gate.pid
for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:18999/health >/dev/null; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:18999/health >/dev/null
touch /run/pokemon-eval/ready
exec tail -f /dev/null
