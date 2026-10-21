#!/usr/bin/env python3
"""Read-only report: which Audiobookshelf books list their audio files more than once?

Works on a COPY of the database (the original is never opened for writing). For every book it looks at the audio files
Audiobookshelf has recorded and reports:
  A) the same file recorded twice (same inode)        -> a stale/duplicate record, like Death Troopers had
  B) the same data under two names in one book        -> two hard links of one file (counts as two tracks)
  C) two different files with the same name           -> look at these by hand
Nothing is changed anywhere.

Usage: python3 abs_audio_report.py /path/to/absdatabase.sqlite
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter

src = os.path.expanduser(sys.argv[1]) if len(sys.argv) > 1 else ""
if not os.path.isfile(src):
    sys.exit("Usage: abs_audio_report.py /path/to/absdatabase.sqlite")
tmp = tempfile.mkdtemp()
dst = os.path.join(tmp, "copy.sqlite")
for ext in ("", "-wal", "-shm"):
    if os.path.exists(src + ext):
        shutil.copy2(src + ext, dst + ext)
db = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
if "books" not in tables:
    sys.exit("No 'books' table found. Tables: " + ", ".join(sorted(tables)))
cols = {r[1] for r in db.execute("pragma table_info(books)")}
if "audioFiles" not in cols:
    sys.exit("The books table has no audioFiles column. Columns: " + ", ".join(sorted(cols)))

a, b, c, total, multi = [], [], [], 0, 0
for bid, title, raw in db.execute("select id, title, audioFiles from books"):
    try:
        files = json.loads(raw or "[]")
    except ValueError:
        continue
    if not files:
        continue
    total += 1
    inos = [str(f.get("ino") or "") for f in files]
    names = [str((f.get("metadata") or {}).get("filename") or "") for f in files]
    paths = [str((f.get("metadata") or {}).get("path") or "") for f in files]
    ino_count = Counter(i for i in inos if i)
    path_count = Counter(p for p in paths if p)
    if any(n > 1 for n in path_count.values()) or (
            any(n > 1 for n in ino_count.values()) and len(set(paths)) < len(paths)):
        a.append((title, len(files), len(set(inos))))
    elif any(n > 1 for n in ino_count.values()):
        b.append((title, len(files), len(ino_count)))
    elif any(n > 1 for n in Counter(n for n in names if n).values()):
        c.append((title, len(files), len(set(names))))
    if len(files) > 1:
        multi += 1

print(f"{total} books with audio files; {multi} have more than one audio file.\n")
for label, rows, hint in (
        ("A) the SAME file recorded twice (stale/duplicate record)", a, "remove the book from Audiobookshelf (hard delete OFF) and rescan"),
        ("B) one file under TWO NAMES (counts as two tracks)", b, "remove one of the two names on the seedbox (needs a dry run)"),
        ("C) different files with the same name", c, "check by hand")):
    print(f"{label}: {len(rows)}   -> {hint}")
    for title, nfiles, nuniq in sorted(rows)[:40]:
        print(f"    {title}   (files listed: {nfiles}, distinct: {nuniq})")
    if len(rows) > 40:
        print(f"    ... and {len(rows) - 40} more")
    print()
shutil.rmtree(tmp, ignore_errors=True)
