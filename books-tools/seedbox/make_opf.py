#!/usr/bin/env python3
"""Write a metadata.opf into each book folder of one author in ABS: clean title, series + number, language (dry run unless --apply).

Audiobookshelf reads an .opf file in a book's folder (the same kind LazyLibrarian writes). This sets:
  title, author, language, and the series name with the book's number (calibre:series / calibre:series_index),
so a series like 'The Boxcar Children' shows every book numbered on the Series page.
The new file is a separate file in the ABS folder: the audio files (shared with the torrent) are never touched.
A folder that already has an .opf is left alone.

Usage: python3 make_opf.py "<Author folder in ABS>" --series "<Series name>" [--lang eng] [--apply]
              [--folder-re REGEX]   regex over the folder name: group 1 = book number, group 2 = title
                                    default: ^BC (\\d+)(?:-\\d+)? - (.+)$        ("BC 004 - Mystery Ranch")
"""
import argparse
import os
import re
import sys
from xml.sax.saxutils import escape

ap = argparse.ArgumentParser()
ap.add_argument("author")
ap.add_argument("--series", required=True)
ap.add_argument("--lang", default="eng")
ap.add_argument("--apply", action="store_true")
ap.add_argument("--folder-re", default=r"^BC (\d+)(?:-\d+)? - (.+)$")
a = ap.parse_args()

root = os.path.join(os.path.expanduser(os.environ.get("ABS_DIR", "~/media/ABS")), a.author)
if not os.path.isdir(root):
    sys.exit(f"Not found: {root}")
rx = re.compile(a.folder_re, re.I)
TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uuid_id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{title}</dc:title>
    <dc:creator opf:role="aut">{author}</dc:creator>
    <dc:language>{lang}</dc:language>
    <meta name="calibre:series" content="{series}"/>
    <meta name="calibre:series_index" content="{index}"/>
  </metadata>
</package>
"""
made = skipped = odd = 0
rows = []
for name in sorted(os.listdir(root)):
    folder = os.path.join(root, name)
    if not os.path.isdir(folder):
        continue
    m = rx.match(name)
    if not m:
        odd += 1
        rows.append(f"no match, left alone: {name}")
        continue
    if any(f.lower().endswith(".opf") for f in os.listdir(folder)):
        skipped += 1
        continue
    index, title = str(int(m.group(1))), m.group(2).strip()
    opf = TEMPLATE.format(title=escape(title, {'"': "&quot;"}), author=escape(a.author), lang=escape(a.lang),
                          series=escape(a.series, {'"': "&quot;"}), index=index)
    rows.append(f"{'writing' if a.apply else 'would write'}  #{index:>3}  {title}")
    if a.apply:
        with open(os.path.join(folder, "metadata.opf"), "w", encoding="utf-8") as f:
            f.write(opf)
    made += 1
for r in rows[:6] + (["..."] if len(rows) > 12 else []) + rows[-6:] if len(rows) > 12 else rows:
    print(r)
print(f"\n{'Wrote' if a.apply else 'Would write'} {made} metadata.opf file(s); {skipped} folder(s) already had an .opf; {odd} didn't match.")
if not a.apply:
    print("Dry run: nothing was changed. Add --apply to do it.")
