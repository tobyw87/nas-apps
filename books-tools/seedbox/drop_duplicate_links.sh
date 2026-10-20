#!/bin/bash
# drop_duplicate_links.sh - remove duplicate hard links from one folder when the same files also live in another.
#
#   drop_duplicate_links.sh "<keep folder>" "<duplicate folder>"            show what would be removed (default)
#   drop_duplicate_links.sh "<keep folder>" "<duplicate folder>" --apply    do it
#
# A file in the duplicate folder is removed only if a file with the SAME inode (the very same data) is in the keep
# folder, so no data is ever lost: the keep folder (and the torrent) still have it. Anything else is left alone.
# Empty folders left behind are removed with rmdir (which refuses if anything is inside).

KEEP="$1"; DUP="$2"; APPLY="$3"
[ -d "$KEEP" ] && [ -d "$DUP" ] || { echo 'Usage: drop_duplicate_links.sh "<keep folder>" "<duplicate folder>" [--apply]'; exit 1; }
[ "$(realpath "$KEEP")" != "$(realpath "$DUP")" ] || { echo "Those are the same folder."; exit 1; }
LOG="${DUP_LOG:-$HOME/scripts/drop_duplicate_links.log}"

declare -A keep_inodes
while IFS= read -r -d '' f; do keep_inodes["$(stat -c %i "$f")"]=1; done < <(find "$KEEP" -type f -print0)

remove=0; leave=0
while IFS= read -r -d '' f; do
    ino=$(stat -c %i "$f"); links=$(stat -c %h "$f")
    if [ -n "${keep_inodes[$ino]}" ] && [ "$links" -gt 1 ]; then
        remove=$((remove+1)); echo "REMOVE (same data is also in the keep folder, $links links): $f"
        if [ "$APPLY" = "--apply" ]; then rm -- "$f" && echo "[$(date '+%F %T')] removed link '$f' (kept in '$KEEP')" >> "$LOG"; fi
    else
        leave=$((leave+1)); echo "LEAVE (not a duplicate of anything in the keep folder): $f"
    fi
done < <(find "$DUP" -type f -print0)

if [ "$APPLY" = "--apply" ]; then
    # rmdir only removes empty folders; start deepest first, then the parent
    find "$DUP" -depth -type d -empty -exec rmdir {} \; 2>/dev/null
    [ -d "$(dirname "$DUP")" ] && rmdir "$(dirname "$DUP")" 2>/dev/null
    echo "Applied: $remove link(s) removed, $leave file(s) left."
else
    echo "Dry run: $remove would be removed, $leave left. Add --apply to do it."
fi
