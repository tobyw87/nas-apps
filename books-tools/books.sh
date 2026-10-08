#!/bin/sh
# books-tools wrapper. Run on the NAS with sudo, e.g.
#   sudo /volume1/docker/books-tools/books.sh organize            (dry run, changes nothing)
#   sudo /volume1/docker/books-tools/books.sh organize --apply
#   sudo /volume1/docker/books-tools/books.sh pull
#   sudo /volume1/docker/books-tools/books.sh run                 (pull + organize --apply; scheduled)
#   sudo /volume1/docker/books-tools/books.sh status
cd "$(dirname "$0")" || exit 1
set -a; . ./.env; set +a
mkdir -p "$LOGS_DIR" data/home
exec docker compose run --rm -T books "$@"
