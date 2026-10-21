#!/usr/bin/env python3
"""Dry run by default: where one audio file sits in one folder under two names, keep the LazyLibrarian name.

Audiobookshelf counts every name as a track, so a book with 'Author - Title.m4b' and the release's own name
(same inode) plays twice. This keeps the name that matches 'Author - Title' (optionally ' Part N of M'), judged from
the folder layout Author/Title/, and removes the other name(s). The data stays: the kept name, and the torrent's
copy in book_downloads, are the same file. A folder where the keeper isn't exactly one name is skipped and listed.
Only files that really are the same inode as the keeper are ever removed.

Usage: python3 dedupe_names.py ROOT [--apply]
"""
import os
import re
import sys

AUDIO = (".m4b", ".m4a", ".mp3", ".flac", ".ogg", ".opus")
args = [a for a in sys.argv[1:] if not a.startswith("--")]
apply = "--apply" in sys.argv
if len(args) != 1 or not os.path.isdir(os.path.expanduser(args[0])):
    sys.exit("Usage: dedupe_names.py ROOT [--apply]   (ROOT is the Audiobookshelf library, e.g. ~/media/ABS)")
root = os.path.expanduser(args[0])

removed = skipped = 0
for d, _, files in sorted(os.walk(root)):
    groups = {}
    for f in files:
        if f.lower().endswith(AUDIO):
            st = os.stat(os.path.join(d, f))
            groups.setdefault((st.st_dev, st.st_ino), []).append(f)
    title, author = os.path.basename(d), os.path.basename(os.path.dirname(d))
    want = re.compile(re.escape(f"{author} - {title}") + r"(?: Part \d+ of \d+)?", re.I)
    for names in groups.values():
        if len(names) < 2:
            continue
        keep = [n for n in names if want.fullmatch(os.path.splitext(n)[0])]
        if len(keep) != 1:
            skipped += 1
            print(f"SKIP (needs a look) {os.path.relpath(d, root)}: {sorted(names)}")
            continue
        for n in sorted(names):
            if n == keep[0]:
                continue
            a, b = os.path.join(d, keep[0]), os.path.join(d, n)
            if not os.path.samefile(a, b):
                continue
            print(("removing " if apply else "would remove ") + f"{os.path.relpath(b, root)}   (keeping '{keep[0]}')")
            if apply:
                os.remove(b)
            removed += 1
print(f"\n{'Removed' if apply else 'Would remove'} {removed} extra name(s); skipped {skipped} group(s).")
if not apply:
    print("Dry run: nothing was changed. Add --apply to do it.")
