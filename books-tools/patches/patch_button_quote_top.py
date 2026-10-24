#!/usr/bin/env python3
"""Patch button.py (on top of patch_button_quotes.py): show the book quote above the 'Get new books' button.

The quote line used to sit just above the log. This moves it to the very top of the page and keeps its space reserved
(two lines) even while the pipeline runs, when the quote is hidden, so the button never jumps up and down.

Usage: python3 patch_button_quote_top.py /path/to/button.py
Makes a backup, refuses to run twice, changes nothing if the file isn't what it expects,
restores the backup if the result doesn't compile.
"""
import py_compile
import shutil
import sys
import time

path = sys.argv[1]
src = open(path, encoding="utf-8", newline="").read()
if 'id="qline" style="visibility:hidden"' in src:
    sys.exit("Already patched - nothing to do.")
CRLF = "\r\n" in src
if CRLF:
    src = src.replace("\r\n", "\n")

EDITS = [
    ("the quote line above the log", '<div id="qline" style="display:none"></div>\n<pre id="o">__LINES__</pre>', '<pre id="o">__LINES__</pre>'),
    ("the top of the page", '</style></head><body><div class="wrap">', '</style></head><body><div class="wrap">\n<div id="qline" style="visibility:hidden"></div>'),
    ("the quote style", "#qline{flex:none;margin:0 0 6px;", "#qline{flex:none;min-height:2.8em;margin:0 0 6px;"),
    ("the quote show/hide", "el.style.display=idle?'':'none';", "el.style.visibility=idle?'visible':'hidden';"),
]
for label, old, _ in EDITS:
    n = src.count(old)
    if n != 1:
        sys.exit(f"Can't patch: expected exactly one copy of {label}, found {n}. Nothing was changed.")

backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(path, backup)
for label, old, new in EDITS:
    src = src.replace(old, new, 1)
if CRLF:
    src = src.replace("\n", "\r\n")
open(path, "w", encoding="utf-8", newline="").write(src)
try:
    py_compile.compile(path, doraise=True)
except py_compile.PyCompileError as e:
    shutil.copy2(backup, path)
    sys.exit(f"The patched file didn't compile, so I restored the original: {e}")
print(f"Patched {path} (backup: {backup})")
