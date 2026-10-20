#!/bin/bash
# adopt_manual.sh - find audiobooks in book_downloads that LazyLibrarian never imported into ABS,
# and (when you say so) hard-link them into ABS as Author/Title/. Never deletes, never overwrites.
#
#   adopt_manual.sh                         list audiobooks not in ABS yet, with a ready-to-run command
#   adopt_manual.sh link "<name>" "<Author>" "<Title>"   link that download into ABS/Author/Title/
#
# A download counts as "not imported" when none of its audio files has a second hard link.
# If the folder has both .m4b and other audio (mp3...), only the .m4b files are linked.

DL="${BOOK_DOWNLOADS:-$HOME/book_downloads}"
ABS="${ABS_DIR:-$HOME/media/ABS}"
LOG="${ADOPT_LOG:-$HOME/scripts/adopt_manual.log}"
MIN_AGE="${MIN_AGE_MIN:-30}"          # leave new downloads alone this long, so LazyLibrarian gets first go
AUDIO='.*\.\(m4b\|mp3\|m4a\|flac\|opus\|aac\|ogg\|wma\|wav\)'

say() { echo "$*"; }
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

audio_files() { find "$1" -type f -iregex "$AUDIO" -not -path "*.unpack/*" -print0 2>/dev/null; }

# 0 = every audio file under $1 has only one link (not imported); 1 = something is already linked or no audio
unimported() {
    local n=0 f
    while IFS= read -r -d '' f; do
        n=$((n+1))
        [ "$(stat -c %h "$f")" -gt 1 ] && return 1
    done < <(audio_files "$1")
    [ "$n" -gt 0 ]
}

if [ "$1" = "link" ]; then
    NAME="$2"; AUTHOR="$3"; TITLE="$4"
    [ -n "$NAME" ] && [ -n "$AUTHOR" ] && [ -n "$TITLE" ] || { say 'Usage: adopt_manual.sh link "<download name>" "<Author>" "<Title>"'; exit 1; }
    SRC="$DL/$NAME"
    [ -e "$SRC" ] || { say "Not found: $SRC"; exit 1; }
    case "$AUTHOR$TITLE" in */*|*..*) say "Author and Title must not contain / or .."; exit 1;; esac
    DEST="$ABS/$AUTHOR/$TITLE"
    mkdir -p "$DEST" || exit 1
    # prefer m4b when the download has any
    if find "$SRC" -type f -iname '*.m4b' -not -path "*.unpack/*" | grep -q .; then
        PAT='.*\.m4b'
    else
        PAT="$AUDIO"
    fi
    count=0
    while IFS= read -r -d '' f; do
        bn=$(basename "$f")
        if [ -e "$DEST/$bn" ]; then say "Already there, skipped: $bn"; continue; fi
        if ln "$f" "$DEST/$bn"; then say "Linked: $DEST/$bn"; log "linked '$f' -> '$DEST/$bn'"; count=$((count+1)); else say "FAILED to link $bn"; fi
    done < <(find "$SRC" -type f -iregex "$PAT" -not -path "*.unpack/*" -print0)
    say "Done: $count file(s) linked into $DEST"
    exit 0
fi

found=0
for p in "$DL"/*; do
    [ -e "$p" ] || continue
    case "$p" in *.unpack) continue;; esac
    # only entries untouched for MIN_AGE minutes
    [ -n "$(find "$p" -maxdepth 0 -mmin +"$MIN_AGE" 2>/dev/null)" ] || continue
    unimported "$p" || continue
    found=$((found+1))
    name=$(basename "$p")
    say "NOT IN ABS: $name"
    say "   fix: $0 link \"$name\" \"AUTHOR\" \"TITLE\""
done
[ "$found" -eq 0 ] && say "Nothing waiting: every audiobook in $DL is already linked into ABS."
exit 0
