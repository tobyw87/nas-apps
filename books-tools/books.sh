#!/bin/sh
# books-tools wrapper. Run on the NAS with sudo, e.g.
#   sudo /volume1/docker/books-tools/books.sh organize            (dry run, changes nothing)
#   sudo /volume1/docker/books-tools/books.sh organize --apply
#   sudo /volume1/docker/books-tools/books.sh pull
#   sudo /volume1/docker/books-tools/books.sh run                 (pull + organize --apply; scheduled)
#   sudo /volume1/docker/books-tools/books.sh status
#   sudo /volume1/docker/books-tools/books.sh manga               (plan only; folders read-only)
cd "$(dirname "$0")" || exit 1
set -a; . ./.env; set +a
mkdir -p "$LOGS_DIR" data/home
if [ "$1" = manga ]; then
  # The manga source folders (no spaces in these paths) are mounted read-only, only for this.
  VOLS=""; LIST=""
  OLDIFS=$IFS; IFS=:
  for d in ${MANGA_SOURCES:-/volume1/NAS/Archive/Comics:/volume1/NAS/Archive/6_Manga}; do
    [ -d "$d" ] && { VOLS="$VOLS -v $d:$d:ro"; LIST="$LIST${LIST:+:}$d"; }
  done
  IFS=$OLDIFS
  # shellcheck disable=SC2086
  exec docker compose run --rm -T $VOLS -e MANGA_SOURCES="$LIST" books "$@"
fi
if [ "$1" = calibre-review ]; then
  # the plain folder for books without a proper author/title (outside 5_Books, so BookOrbit never sees it)
  REVIEW_DIR="${REVIEW_DIR:-/volume1/NAS/Archive/EPUB To Fix}"
  mkdir -p "$REVIEW_DIR"
  exec docker compose run --rm -T -v "$REVIEW_DIR:$REVIEW_DIR" -e REVIEW_DIR="$REVIEW_DIR" books "$@"
fi
if [ "$1" = calibre-export ]; then
  # the clean Author/Title/Title.epub tree for BookOrbit (outside 5_Books until I swap it in)
  EXPORT_DIR="${EXPORT_DIR:-/volume1/NAS/Archive/EPUB Clean}"
  mkdir -p "$EXPORT_DIR"
  exec docker compose run --rm -T -v "$EXPORT_DIR:$EXPORT_DIR" -e EXPORT_DIR="$EXPORT_DIR" books "$@"
fi
exec docker compose run --rm -T books "$@"
