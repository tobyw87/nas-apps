#!/usr/bin/env python3
"""Dry run by default: split one combined audiobook folder into separate Audiobookshelf book folders.

Hard-links (never copies or moves) the numbered tracks, renamed 'Title 01.mp3', 'Title 02.mp3' and so on, of a combined download into one folder per book under
ABS/<Author>/<Title>/, and writes a metadata.opf with the series and number for each. The source stays where it is,
so the torrent keeps seeding. Existing folders are never overwritten.

Usage: python3 split_combined.py SOURCE_FOLDER "Author" "Series" --book "N|Title|FIRST-LAST" [--book ...] [--apply]
  FIRST-LAST are the track numbers in the file names (the last number before .mp3).
Example:
  python3 split_combined.py ~/book_downloads/X/Y "Gertrude Chandler Warner" "The Boxcar Children" \
      --book "1|The Boxcar Children|1-13" --book "2|Surprise Island|14-27" --book "3|The Yellow House Mystery|28-44"
Reads env ABS_DIR (default ~/media/ABS).
"""
import argparse
import os
import re
import sys
from xml.sax.saxutils import escape

ap = argparse.ArgumentParser()
ap.add_argument("source")
ap.add_argument("author")
ap.add_argument("series")
ap.add_argument("--book", action="append", required=True, help='"N|Title|FIRST-LAST"')
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

src = os.path.expanduser(a.source)
abs_dir = os.path.expanduser(os.environ.get("ABS_DIR", "~/media/ABS"))
if not os.path.isdir(src):
    sys.exit(f"Not a folder: {src}")

tracks = {}
for f in sorted(os.listdir(src)):
    m = re.search(r"(\d+)\.mp3$", f, re.I)
    if m:
        tracks[int(m.group(1))] = f

plan, used = [], set()
for spec in a.book:
    try:
        num, title, rng = [p.strip() for p in spec.split("|")]
        lo, hi = [int(x) for x in rng.split("-")]
    except ValueError:
        sys.exit(f"Bad --book value: {spec}")
    nums = list(range(lo, hi + 1))
    missing = [n for n in nums if n not in tracks]
    clash = [n for n in nums if n in used]
    if missing or clash:
        sys.exit(f"Book {num}: missing tracks {missing} / already used {clash}")
    used.update(nums)
    plan.append((num, title, [tracks[n] for n in nums]))

left = sorted(set(tracks) - used)
if left:
    print(f"Note: tracks not used by any book: {left}")

for num, title, files in plan:
    dest = os.path.join(abs_dir, a.author, f"{int(num):03d} - {title}")
    if os.path.exists(dest):
        sys.exit(f"Already exists, not touching it: {dest}")
for num, title, files in plan:
    dest = os.path.join(abs_dir, a.author, f"{int(num):03d} - {title}")
    print(f"{'linking' if a.apply else 'would link'} {len(files)} tracks -> {dest}")
    print(f"    {files[0]}  ->  {title} 01.mp3\n    {files[-1]}  ->  {title} {len(files):02d}.mp3")
    if a.apply:
        os.makedirs(dest)
        for i, f in enumerate(files, 1):
            os.link(os.path.join(src, f), os.path.join(dest, f"{title} {i:02d}.mp3"))
        with open(os.path.join(dest, "metadata.opf"), "w", encoding="utf-8") as fh:
            fh.write(
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uuid_id">\n'
                '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">\n'
                f"    <dc:title>{escape(title)}</dc:title>\n"
                f'    <dc:creator opf:role="aut">{escape(a.author)}</dc:creator>\n'
                "    <dc:language>eng</dc:language>\n"
                f'    <meta name="calibre:series" content="{escape(a.series)}"/>\n'
                f'    <meta name="calibre:series_index" content="{int(num)}"/>\n'
                "  </metadata>\n</package>\n"
            )
if not a.apply:
    print("\nDry run: nothing was changed. Add --apply to do it.")
