#!/bin/sh
# Carries out a plan written by `books.sh cleanup`. Moves files only; never deletes a file.
#   sudo /volume1/docker/books-tools/cleanup-apply.sh /volume1/NAS/Logs/books/cleanup-plan-....tsv
# Afterwards the archive folder holds only the Library and the seedbox mirror; anything else
# that was left (non-ebooks, emptied folders, @eaDir) goes to <removed_dir>/_leftovers.
PLAN="$1"
[ -f "$PLAN" ] || { echo "Usage: $0 <cleanup-plan-....tsv>"; exit 1; }
head -1 "$PLAN" | grep -q '^# books-tools cleanup plan$' || { echo "Not a cleanup plan: $PLAN"; exit 1; }
RUNNING=$(docker ps --format '{{.Image}}') || { echo "Can't check Docker (run with sudo)."; exit 1; }
if echo "$RUNNING" | grep -q '^books-tools'; then
  echo "A books-tools run is still going. Wait for it to finish, then run this again."; exit 1
fi

hdr() { sed -n "s/^# $1=//p" "$PLAN"; }
ARCHIVE=$(hdr archive_dir); LIBRARY=$(hdr library_dir); MIRROR=$(hdr mirror_dir); REMOVED=$(hdr removed_dir)
for d in "$ARCHIVE" "$LIBRARY" "$MIRROR"; do
  [ -n "$d" ] && [ -d "$d" ] || { echo "Missing folder from plan header: '$d'"; exit 1; }
done
[ -n "$REMOVED" ] || { echo "Plan has no removed_dir"; exit 1; }

LOG="${PLAN%.tsv}-moved.tsv"
TAB=$(printf '\t')
moved=0; skipped=0
grep -v '^#' "$PLAN" > "$PLAN.todo"
while IFS="$TAB" read -r action src dest; do
  [ -n "$dest" ] || continue
  if [ ! -e "$src" ]; then echo "gone${TAB}$src" >> "$LOG"; skipped=$((skipped+1)); continue; fi
  if [ -e "$dest" ]; then echo "exists${TAB}$src${TAB}$dest" >> "$LOG"; skipped=$((skipped+1)); continue; fi
  mkdir -p "$(dirname "$dest")" && mv -n "$src" "$dest" && printf '%s\t%s\t%s\n' "$action" "$src" "$dest" >> "$LOG" \
    && moved=$((moved+1))
  [ $((moved % 1000)) -eq 0 ] && [ "$moved" -gt 0 ] && echo "Moved $moved..."
done < "$PLAN.todo"
rm -f "$PLAN.todo"
echo "Moved $moved file(s); $skipped skipped (see $LOG)."

# Everything else directly under the archive except the Library and the mirror -> _leftovers.
LIBNAME=$(basename "$LIBRARY"); MIRNAME=$(basename "$MIRROR")
for e in "$ARCHIVE"/* "$ARCHIVE"/.[!.]* "$ARCHIVE"/..?*; do
  [ -e "$e" ] || continue
  name=$(basename "$e")
  [ "$name" = "$LIBNAME" ] || [ "$name" = "$MIRNAME" ] && continue
  mkdir -p "$REMOVED/_leftovers"
  if [ -e "$REMOVED/_leftovers/$name" ]; then
    echo "exists${TAB}$e${TAB}$REMOVED/_leftovers/$name" >> "$LOG"
  else
    mv -n "$e" "$REMOVED/_leftovers/" && echo "leftover${TAB}$e${TAB}$REMOVED/_leftovers/$name" >> "$LOG"
  fi
done

echo
echo "$ARCHIVE now contains:"; ls -la "$ARCHIVE"
echo
echo "Held for you to check, then delete yourself: $REMOVED"
du -sh "$REMOVED"/* 2>/dev/null
