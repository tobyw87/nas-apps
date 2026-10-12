#!/bin/sh
# Creates .env and config/. Safe to re-run: an existing .env and config/ are never overwritten.
# Run with sudo from /volume1/docker/calibre.
cd "$(dirname "$0")" || exit 1
mkdir -p config
if [ -f .env ]; then
  echo "Keeping existing .env"
else
  BT=/volume1/docker/books-tools/.env
  PUID=$(sed -n 's/^PUID=//p' "$BT" 2>/dev/null); PGID=$(sed -n 's/^PGID=//p' "$BT" 2>/dev/null)
  [ -n "$PUID" ] || PUID=$(id -u tobyw87)
  [ -n "$PGID" ] || PGID=$(id -g tobyw87)
  if [ -z "$PUID" ] || [ -z "$PGID" ]; then echo "Could not work out PUID/PGID; nothing written."; exit 1; fi
  sed -e "s/^PUID=.*/PUID=$PUID/" -e "s/^PGID=.*/PGID=$PGID/" .env.example > .env
  echo "Created .env (PUID=$PUID PGID=$PGID)"
fi
chown -R "$(sed -n 's/^PUID=//p' .env):$(sed -n 's/^PGID=//p' .env)" config
set -a; . ./.env; set +a
if netstat -tln 2>/dev/null | grep -qE ":($CALIBRE_HTTP_PORT|$CALIBRE_CONTENT_PORT) "; then
  echo "WARNING: port $CALIBRE_HTTP_PORT or $CALIBRE_CONTENT_PORT is in use. Change it in .env."
fi
echo "Next: sudo docker compose up -d   then open http://<nas-ip>:$CALIBRE_HTTP_PORT"
