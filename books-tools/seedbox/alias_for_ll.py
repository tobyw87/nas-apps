#!/usr/bin/env python3
"""Give a finished download a second, cleaner name so LazyLibrarian can match it.

LazyLibrarian compares the download's folder or file name with the wanted book. Series text in the name, like
"Jeff VanderMeer - [The Southern Reach Series 3] - Acceptance.m4b", pushes the match below its threshold and the
book is marked Failed. This makes a HARD LINK named "Jeff VanderMeer - Acceptance.m4b" next to the original
(no data is copied, the original keeps seeding, nothing is renamed or deleted).

Only these are removed: whole segments that are bracketed or look like a series ("Book 3", "Series 3", "Vol. 2",
"#3"), and (...) / [...] tags inside a segment. Names without such text are left alone.

Usage: alias_for_ll.py <save path> <torrent name>      (the arguments Deluge's Execute plugin passes)
"""
import os
import re
import sys

SERIES = re.compile(r"(?i)\b(book|series|vol(?:ume)?\.?|part)\s*#?\d+|#\d+")
TAGS = re.compile(r"\s*[\[(][^\])]*[\])]")


def clean(base):
    s = base.replace(" – ", " - ").replace(" — ", " - ")
    parts = [p.strip() for p in s.split(" - ")]
    keep = []
    for p in parts:
        if not p:
            continue
        if re.fullmatch(r"[\[(].*[\])]", p) or SERIES.search(p):
            continue
        p = TAGS.sub("", p).strip()
        if p:
            keep.append(p)
    return " - ".join(keep) if len(keep) >= 2 else base


def main():
    if len(sys.argv) < 3:
        return
    folder, name = sys.argv[1], sys.argv[2]
    src = os.path.join(folder, name)
    if os.path.isfile(src):
        base, ext = os.path.splitext(name)
    elif os.path.isdir(src):
        base, ext = name, ""
    else:
        print(f"alias: '{name}' not found in {folder}")
        return
    new = clean(base)
    if new == base or "/" in new or new.startswith("."):
        return
    dst = os.path.join(folder, new + ext)
    if os.path.exists(dst):
        return
    if os.path.isfile(src):
        os.link(src, dst)
    else:
        os.mkdir(dst)
        for root, dirs, files in os.walk(src):
            rel = os.path.relpath(root, src)
            for d in dirs:
                os.makedirs(os.path.join(dst, rel, d), exist_ok=True)
            for f in files:
                os.link(os.path.join(root, f), os.path.join(dst, rel, f))
    print(f"alias: '{name}' -> '{new + ext}' (hard link, original untouched)")


if __name__ == "__main__":
    try:
        main()
    except OSError as e:
        print(f"alias: skipped ({e})")
