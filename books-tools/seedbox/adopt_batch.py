#!/usr/bin/env python3
"""Hard-link a folder full of audiobooks into ABS/<Author>/<Title>/ in one go (dry run unless --apply).

For a batch like book_downloads/Boxcar Children 1-138/ where every sub-folder is one book. Same rules as adopt_manual.sh:
  * files are hard-linked (nothing is copied, moved or deleted, and the torrent keeps seeding);
  * an existing file is never overwritten;
  * M4B files are preferred when a book has any, otherwise all audio files are linked.
Plus the pipeline's duplicate rule: if ABS already holds an audio file of the same type and size anywhere under the
author's folder, that book is reported as 'already in ABS' and skipped.

Usage: python3 adopt_batch.py "<folder inside book_downloads>" "<Author>" [--apply] [--title-re REGEX]
  --title-re  regex with ONE group that captures the title from a sub-folder name.
              Default (Boxcar Children style): ^.* - BC \\d+ - (.+?) - [^-]+? \\(Ch\\)$
"""
import argparse
import os
import re
import sys

AUDIO = (".m4b", ".mp3", ".m4a", ".flac", ".opus", ".aac", ".ogg", ".wma", ".wav")
ap = argparse.ArgumentParser()
ap.add_argument("folder")
ap.add_argument("author")
ap.add_argument("--apply", action="store_true")
ap.add_argument("--title-re", default=r"^.* - BC \d+ - (.+?) - [^-]+? \(Ch\)$")
a = ap.parse_args()

dl = os.path.expanduser(os.environ.get("BOOK_DOWNLOADS", "~/book_downloads"))
absd = os.path.expanduser(os.environ.get("ABS_DIR", "~/media/ABS"))
src_root = os.path.join(dl, a.folder)
if not os.path.isdir(src_root):
    sys.exit(f"Not found: {src_root}")
if any(c in a.author for c in "/\\") or ".." in a.author:
    sys.exit("The author can't contain / or ..")
rx = re.compile(a.title_re)
author_dir = os.path.join(absd, a.author)

# audio files ABS already has under this author: (extension, size)
have = set()
for d, _, fs in os.walk(author_dir):
    for f in fs:
        if f.lower().endswith(AUDIO):
            have.add((f.rsplit(".", 1)[-1].lower(), os.path.getsize(os.path.join(d, f))))


def audio_files(folder):
    out = []
    for d, dirs, fs in os.walk(folder):
        dirs[:] = [x for x in dirs if not x.endswith(".unpack")]
        out += [os.path.join(d, f) for f in sorted(fs) if f.lower().endswith(AUDIO)]
    m4b = [f for f in out if f.lower().endswith(".m4b")]
    return m4b or out


plan, odd, dup_titles, filed = [], [], {}, []
for name in sorted(os.listdir(src_root)):
    folder = os.path.join(src_root, name)
    if not os.path.isdir(folder):
        continue
    m = rx.match(name)
    if not m:
        odd.append(name)
        continue
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    if "/" in title or ".." in title:
        odd.append(name)
        continue
    files = audio_files(folder)
    if not files:
        odd.append(f"{name}  (no audio files)")
        continue
    if all((f.rsplit(".", 1)[-1].lower(), os.path.getsize(f)) in have for f in files):
        filed.append(f"{title}   <- {name}")
        continue
    if title in dup_titles:
        odd.append(f"{name}  (same title as '{dup_titles[title]}'; skipped)")
        continue
    dup_titles[title] = name
    plan.append((title, files))

linked = skipped = 0
for title, files in plan:
    dest = os.path.join(author_dir, title)
    print(f"{'LINK' if a.apply else 'would link'}  {a.author}/{title}/   ({len(files)} file(s))")
    for f in files:
        target = os.path.join(dest, os.path.basename(f))
        if os.path.exists(target):
            skipped += 1
            print(f"     already there, skipped: {os.path.basename(f)}")
            continue
        if a.apply:
            os.makedirs(dest, exist_ok=True)
            os.link(f, target)
        linked += 1
print()
if filed:
    print(f"Already in ABS (same type and size, skipped): {len(filed)}")
    for x in filed[:40]:
        print("   ", x)
if odd:
    print(f"Needs a look (not linked): {len(odd)}")
    for x in odd:
        print("   ", x)
print(f"\n{'Linked' if a.apply else 'Would link'} {linked} file(s) into {len(plan)} book folder(s); skipped {skipped} existing.")
if not a.apply:
    print("Dry run: nothing was changed. Add --apply to do it.")
