#!/bin/sh
# One-time switch to the new 5_Books layout. Same volume, so it's instant; nothing is copied or
# deleted. Run with sudo:  sudo /volume1/docker/books-tools/migrate.sh
#   5_Books/Library          -> 5_Books/EPUB Archive
#   5_Books/_Seedbox_Mirror  -> Archive/_Seedbox_Mirror/Books   (outside 5_Books)
#   creates 5_Books/Textbooks and 5_Books/Manga
# then updates the paths books-tools remembers in data/state.db. Safe to run again.
cd "$(dirname "$0")" || exit 1
set -a; . ./.env; set +a
[ -n "$ARCHIVE_DIR" ] && [ -d "$ARCHIVE_DIR" ] || { echo "ARCHIVE_DIR in .env is missing: '$ARCHIVE_DIR'"; exit 1; }
RUNNING=$(docker ps -q --filter label=com.docker.compose.project=books-tools) \
  || { echo "Can't check Docker (run with sudo)."; exit 1; }
[ -z "$RUNNING" ] || { echo "A books-tools run is still going. Wait for it to finish."; exit 1; }

EPUB="$ARCHIVE_DIR/EPUB Archive"
MIRROR=${SEEDBOX_MIRROR_DIR:-$(dirname "$ARCHIVE_DIR")/_Seedbox_Mirror/Books}

# move OLD NEW: only if OLD is there. NEW may exist only as an empty folder (Docker makes those
# when a container starts before the move); anything else stops the script.
move() {
  [ -d "$1" ] || return 0
  if [ -e "$2" ]; then
    rmdir "$2" 2>/dev/null || { echo "STOP: '$2' already exists and isn't empty. Nothing more moved."; exit 1; }
  fi
  mkdir -p "$(dirname "$2")" && mv "$1" "$2" && echo "Moved $1 -> $2"
}
move "$ARCHIVE_DIR/Library" "$EPUB"
move "$ARCHIVE_DIR/_Seedbox_Mirror" "$MIRROR"
[ -d "$EPUB" ] || { echo "STOP: no '$EPUB'"; exit 1; }
for d in "$ARCHIVE_DIR/Textbooks" "$ARCHIVE_DIR/Manga" "$MIRROR"; do
  [ -d "$d" ] || { mkdir -p "$d" && echo "Created $d"; }
  chown --reference="$EPUB" "$d"
done

docker compose run --rm -T books migrate-db || exit 1
echo
echo "$ARCHIVE_DIR now contains:"; ls -la "$ARCHIVE_DIR"
