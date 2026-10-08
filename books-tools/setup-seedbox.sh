#!/bin/sh
# Saves the Ultra.cc SFTP login for the one-way pull and tests it.
cd "$(dirname "$0")" || exit 1
set -a; . ./.env; set +a
printf "Ultra.cc server hostname (from your Ultra.cc dashboard, e.g. xxxx.usbx.me): "; read HOST
printf "Ultra.cc SSH username [mynock42]: "; read SBUSER; SBUSER=${SBUSER:-mynock42}
stty -echo; printf "Ultra.cc SSH password: "; read SBPASS; stty echo; echo
mkdir -p data/home
printf '%s' "$SBPASS" | docker compose run --rm -T books setup-remote "$HOST" "$SBUSER"
