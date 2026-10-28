#!/bin/sh
# Nightly reset of the public construction demo (cron, e.g. 03:00 Tashkent):
#
#   0 3 * * * /opt/nazar-agent/deploy/reset-demo.sh >> /var/log/nazar-demo-reset.log 2>&1
#
# Everyone who tries the demo can apply and undo actions; by morning the
# company is as seeded again, with three fresh weeks up to yesterday. The
# credentials live in deploy/demo.env on the server (not in git):
#   DEMO_EMAIL=...
#   DEMO_PASSWORD=...
# The old company is dropped, so its proposals in the agent are left behind
# with it: the new company gets its own empty agent database.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
NAZAR_DIR=${NAZAR_DIR:-/opt/faceid-startup-mvp}
NAZAR_PROJECT=${NAZAR_PROJECT:-faceid-startup-mvp}
SECRETS="$HERE/demo.env"
if [ "$(stat -c %a "$SECRETS")" != 600 ]; then
  echo "$SECRETS must be readable by its owner only: chmod 600 $SECRETS" >&2
  exit 1
fi
# Read as data, not run as shell code: a password may contain $, quotes or spaces.
DEMO_EMAIL=$(sed -n 's/^DEMO_EMAIL=//p' "$SECRETS")
DEMO_PASSWORD=$(sed -n 's/^DEMO_PASSWORD=//p' "$SECRETS")
export DEMO_EMAIL DEMO_PASSWORD

cd "$NAZAR_DIR"
# The password is expanded inside the container, so it never shows in the host's process list.
docker compose -p "$NAZAR_PROJECT" -f docker-compose.prod.yml --env-file .env.prod \
  exec -T -e DEMO_EMAIL -e DEMO_PASSWORD backend \
  sh -c 'python scripts/seed_construction_demo.py --reset --email "$DEMO_EMAIL" --password "$DEMO_PASSWORD" --photos-dir /app/models/demo-faces'
echo "$(date -Is) construction demo reset"
