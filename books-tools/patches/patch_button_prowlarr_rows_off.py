#!/usr/bin/env python3
"""Patch button.py (on top of patch_button_prowlarr.py): remove the Prowlarr button from each search result.

The one 'Prowlarr' button beside Search stays; the result rows go back to having only an Add button.

Usage: python3 patch_button_prowlarr_rows_off.py /path/to/button.py
Makes a backup, refuses to run twice, changes nothing if the file isn't what it expects,
restores the backup if the result doesn't compile.
"""
import py_compile
import shutil
import sys
import time

path = sys.argv[1]
src = open(path, encoding="utf-8", newline="").read()
if 'data-p="${i}"' not in src:
    sys.exit("Nothing to do: the per-result Prowlarr buttons aren't there.")
CRLF = "\r\n" in src
if CRLF:
    src = src.replace("\r\n", "\n")

ROW_NEW = '<span class="bt"><button type="button" data-p="${i}">Prowlarr</button><button type="button" data-i="${i}">Add</button></span></div>'
ROW_OLD = '<button type="button" data-i="${i}">Add</button></div>'
BIND_NEW = ('document.querySelectorAll("#lsr button[data-i]").forEach(b=>{b.onclick=()=>ladd(b);});'
            'document.querySelectorAll("#lsr button[data-p]").forEach(b=>{b.onclick=()=>{const x=window._lres[+b.dataset.p];lprowl(x.title+" "+x.author);};});')
BIND_OLD = 'document.querySelectorAll("#lsr button").forEach(b=>{b.onclick=()=>ladd(b);});'

EDITS = [("the result row buttons", ROW_NEW, ROW_OLD), ("the result button binding", BIND_NEW, BIND_OLD)]
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
