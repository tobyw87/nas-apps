#!/bin/sh
# Carries out a plan written by `books.sh cleanup` or `books.sh manga`. Moves files only; never
# deletes a file.
#   sudo /volume1/docker/books-tools/cleanup-apply.sh /volume1/NAS/Logs/books/<plan>.tsv
# Afterwards each folder the plan sweeps is emptied of whatever is left (non-ebooks, covers,
# emptied folders, @eaDir) into the holding folder, except the folders it says to keep:
#   cleanup: 5_Books keeps only EPUB Archive, Textbooks and Manga
#   manga:   Archive/Comics and Archive/6_Manga are emptied and then removed
PLAN="$1"
[ -f "$PLAN" ] || { echo "Usage: $0 <plan file>"; exit 1; }
case "$(head -1 "$PLAN")" in
  "# books-tools cleanup plan"|"# books-tools manga plan") ;;
  *) echo "Not a books-tools cleanup or manga plan: $PLAN"; exit 1 ;;
esac
# Any books-tools container (organize, pull, a scheduled run) must be finished first.
RUNNING=$(docker ps -q --filter label=com.docker.compose.project=books-tools) \
  || { echo "Can't check Docker (run with sudo)."; exit 1; }
if [ -n "$RUNNING" ]; then
  echo "A books-tools run is still going. Wait for it to finish, then run this again."; exit 1
fi

hdr() { sed -n "s/^# $1=//p" "$PLAN"; }
REMOVED=$(hdr removed_dir)
[ -n "$REMOVED" ] || { echo "Plan has no removed_dir"; exit 1; }
hdr sweep | while IFS='|' read -r SWEEP TO RM; do
  [ -d "$SWEEP" ] || { echo "Missing folder from plan: '$SWEEP'"; exit 1; }
done || exit 1

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

# Whatever is left in each swept folder -> holding folder (except the kept folders).
hdr sweep | while IFS='|' read -r SWEEP TO RM; do
  for e in "$SWEEP"/* "$SWEEP"/.[!.]* "$SWEEP"/..?*; do
    [ -e "$e" ] || continue
    name=$(basename "$e")
    hdr keep | grep -qxF "$name" && continue
    mkdir -p "$TO"
    if [ -e "$TO/$name" ]; then
      echo "exists${TAB}$e${TAB}$TO/$name" >> "$LOG"
    else
      mv -n "$e" "$TO/" && echo "leftover${TAB}$e${TAB}$TO/$name" >> "$LOG"
    fi
  done
  if [ "$RM" = rmdir ]; then
    rmdir "$SWEEP" 2>/dev/null && echo "Removed the now-empty $SWEEP" || echo "Left $SWEEP (not empty, see $LOG)"
  else
    echo; echo "$SWEEP now contains:"; ls -la "$SWEEP"
  fi
done

# Folders made here belong to root; give them (and moved files) to the books user like the rest.
REF=$(hdr owner_ref)
if [ -n "$REF" ] && [ -e "$REF" ]; then
  OWNER=$(stat -c %u "$REF")
  hdr fix_owner | while read -r D; do
    [ -d "$D" ] && find "$D" ! -user "$OWNER" -exec chown --reference="$REF" {} +
  done
fi

echo
echo "Held for you to check, then delete yourself: $REMOVED"
du -sh "$REMOVED"/* 2>/dev/null
