#!/bin/sh
# First-time setup / update for the thermostat page. Run on the NAS:
#   sudo sh /volume1/docker/thermostat/install.sh
# Never overwrites an existing .env or data/.
set -eu
cd "$(dirname "$0")"
mkdir -p data
chown 1000:1000 data
if [ ! -f .env ]; then
  cp env.example .env
  chmod 600 .env
  echo "Created .env. Paste your Home Assistant token after HA_TOKEN= and run this again:"
  echo "  sudo vi /volume1/docker/thermostat/.env"
  exit 0
fi
if ! grep -q '^HA_TOKEN=.\+' .env; then
  echo "HA_TOKEN is empty in .env. Add your Home Assistant long-lived token, then run this again." >&2
  exit 1
fi
PORT=$(grep '^HOST_PORT=' .env | cut -d= -f2)
TSPORT=$(grep '^TS_HTTPS_PORT=' .env | cut -d= -f2)
if ! docker compose ps --status running 2>/dev/null | grep -q thermostat; then
  if netstat -tln 2>/dev/null | grep -q "[:.]$PORT "; then
    echo "Port $PORT is already in use on the NAS. Change HOST_PORT in .env and run this again." >&2
    exit 1
  fi
fi
docker compose build
docker compose up -d
i=0
until curl -fsS -o /dev/null "http://127.0.0.1:$PORT/healthz" 2>/dev/null; do
  i=$((i+1)); [ $i -gt 20 ] && { echo "Not answering yet. Check: sudo docker compose logs --tail 50 thermostat"; exit 1; }
  sleep 2
done
curl -fsS "http://127.0.0.1:$PORT/healthz"; echo
TS=/var/packages/Tailscale/target/bin/tailscale
if [ -x "$TS" ]; then
  if $TS serve status 2>/dev/null | grep -q ":$TSPORT"; then
    echo "Tailscale serve already set up on port $TSPORT."
  else
    $TS serve --bg --https="$TSPORT" "http://127.0.0.1:$PORT"
  fi
  echo "Open: https://treebeard.taileefdd9.ts.net:$TSPORT/   (kiosk view: .../kiosk)"
else
  echo "Tailscale CLI not found at $TS; set up 'tailscale serve' by hand." >&2
fi
