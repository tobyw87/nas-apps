#!/bin/sh
# First-time setup / update for Linkwarden. Run on the NAS:  sudo sh /volume1/docker/linkwarden/install.sh
# Never overwrites an existing .env or data/.
set -eu
cd "$(dirname "$0")"
mkdir -p data/pgdata data/files
if [ ! -f .env ]; then
  rand() { head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 40; }
  sed -e "s/^NEXTAUTH_SECRET=.*/NEXTAUTH_SECRET=$(rand)/" \
      -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(rand)/" env.example > .env
  chmod 600 .env
  echo "Created .env with new random secrets."
fi
PORT=$(grep '^HOST_PORT=' .env | cut -d= -f2)
if ! docker compose ps --status running 2>/dev/null | grep -q linkwarden; then
  if netstat -tln 2>/dev/null | grep -q "[:.]$PORT "; then
    echo "Port $PORT is already in use on the NAS. Change HOST_PORT in .env and run this again." >&2
    exit 1
  fi
fi
docker compose pull
docker compose up -d
echo "Waiting for Linkwarden to start (first start takes a minute or two)..."
i=0
until curl -fsS -o /dev/null 2>/dev/null "http://127.0.0.1:$PORT/"; do
  i=$((i+1)); [ $i -gt 60 ] && { echo "Not up yet. Check: sudo docker compose logs --tail 50 linkwarden"; exit 1; }
  sleep 5
done
echo "Linkwarden is running at http://127.0.0.1:$PORT (point the tunnel's links.tobyw87.com hostname here)."
