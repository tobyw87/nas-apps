#!/bin/sh
# Creates .env (random secrets) and data folders. Safe to re-run: an existing .env
# and data/ are never overwritten. Run with sudo from /volume1/docker/bookorbit.
cd "$(dirname "$0")" || exit 1
mkdir -p data/app data/postgres

if [ -f .env ]; then
  echo "Keeping existing .env"
else
  BT=/volume1/docker/books-tools/.env
  PUID=$(sed -n 's/^PUID=//p' "$BT" 2>/dev/null); PGID=$(sed -n 's/^PGID=//p' "$BT" 2>/dev/null)
  [ -n "$PUID" ] || PUID=$(id -u tobyw87)
  [ -n "$PGID" ] || PGID=$(id -g tobyw87)
  if [ -z "$PUID" ] || [ -z "$PGID" ]; then echo "Could not work out PUID/PGID; nothing written."; exit 1; fi
  r() { openssl rand -hex "$1"; }
  umask 077
  sed -e "s/^PUID=.*/PUID=$PUID/" -e "s/^PGID=.*/PGID=$PGID/" \
      -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(r 24)/" \
      -e "s/^JWT_SECRET=.*/JWT_SECRET=$(r 32)/" \
      -e "s/^PODCAST_ENCRYPTION_KEY=.*/PODCAST_ENCRYPTION_KEY=$(r 32)/" \
      -e "s/^SETUP_BOOTSTRAP_TOKEN=.*/SETUP_BOOTSTRAP_TOKEN=$(r 16)/" \
      -e "s/^EMAIL_ENCRYPTION_KEY=.*/EMAIL_ENCRYPTION_KEY=$(r 32)/" \
      .env.example > .env
  echo "Created .env (PUID=$PUID PGID=$PGID)"
fi

set -a; . ./.env; set +a
if [ ! -d "$LIBRARY_DIR" ]; then
  echo "WARNING: LIBRARY_DIR $LIBRARY_DIR does not exist"
fi
if netstat -tln 2>/dev/null | grep -q ":$APP_PORT "; then
  if ! docker ps --format '{{.Names}}' | grep -q '^bookorbit-app$'; then
    echo "WARNING: port $APP_PORT is already in use. Change APP_PORT and APP_URL in .env."
  fi
fi
echo "Open:        $APP_URL"
echo "Setup token: $SETUP_BOOTSTRAP_TOKEN"
