#!/usr/bin/env python3
"""Patch button.py: say so when a download matches a wanted book's title but not its author.

Usage: python3 patch_button_near_miss.py /path/to/button.py
Makes a backup next to the file, refuses to run twice, and checks the result compiles.
"""
import py_compile
import shutil
import sys
import time

path = sys.argv[1]
src = open(path, encoding="utf-8", newline="").read()      # newline="": keep the file's own line endings
CRLF = "\r\n" in src

if "_hint_near_miss" in src:
    sys.exit("Already patched - nothing to do.")

NEW_FUNC = '''def _hint_near_miss(entries, exts, r, fmt):
    """A download whose title words all match but whose author doesn't (LazyLibrarian sometimes has the wrong
    author): say so, instead of silently ignoring it. Needs 3+ distinctive title words to avoid false alarms."""
    title = re.split(r"[:(\\[]", str(r.get("bookname") or ""))[0]
    tw = [w for w in _words(title) if len(w) > 3 and w != "the"]
    if len(tw) < 3:
        return
    for e in entries:
        if e[2] & exts and all(w in _words(e[0]) for w in tw) and not _matches(e[0], r):
            blog(f"  {name(r)}: found '{e[0]}', which looks like this book, but the author doesn't match "
                 f"(LazyLibrarian has '{r.get('authorname')}'). Fix the author in LazyLibrarian or Hardcover.")
            if fmt != "eBook":
                blog(f"      Or file the audiobook yourself, on the seedbox: ~/scripts/adopt_manual.sh link "
                     f"\\"{e[0]}\\" \\"AUTHOR\\" \\"TITLE\\"")
            return


def seedbox_downloads():'''

import re

DEF_RE = re.compile(r"^def seedbox_downloads\(\):", re.M)
USE_RE = re.compile(
    r"^(?P<ind>[ \t]*)cands = \[e for e in entries if e\[2\] & exts and _matches\(e\[0\], r\)\][ \t]*(?P<eol>\r?\n)",
    re.M)
for label, rx in (("def seedbox_downloads", DEF_RE), ("the 'cands = ...' line", USE_RE)):
    n = len(rx.findall(src))
    if n != 1:
        sys.exit(f"Can't patch: expected exactly one match for {label}, found {n}. Nothing was changed.")

def add_hint(m):
    ind, eol = m.group("ind"), m.group("eol")
    # keep the original line, then report a near miss when nothing matched (the original 'if not cands' still follows)
    return (m.group(0) + f"{ind}if not cands:{eol}{ind}    _hint_near_miss(entries, exts, r, fmt){eol}")

backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(path, backup)
src = USE_RE.sub(add_hint, src, count=1)
new_func = NEW_FUNC.replace("\n", "\r\n") if CRLF else NEW_FUNC
src = DEF_RE.sub(lambda m: new_func, src, count=1)
open(path, "w", encoding="utf-8", newline="").write(src)
try:
    py_compile.compile(path, doraise=True)
except py_compile.PyCompileError as e:
    shutil.copy2(backup, path)
    sys.exit(f"The patched file didn't compile, so I restored the original: {e}")
print(f"Patched {path} (backup: {backup})")
