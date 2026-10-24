#!/usr/bin/env python3
"""Patch button.py: rename the 'Get new books' button to 'Fetch Hardcover Books' (button, log line and the two hints).

Usage: python3 patch_button_label.py /path/to/button.py
Makes a backup, refuses to run twice, changes nothing if the file isn't what it expects,
restores the backup if the result doesn't compile.
"""
import py_compile
import shutil
import sys
import time

path = sys.argv[1]
src = open(path, encoding="utf-8", newline="").read()
if '"Fetch Hardcover Books" if st["hardcover"]' in src:
    sys.exit("Already patched - nothing to do.")
EDITS = [
    ("the button label", '"Get new books" if st["hardcover"] else "Run pipeline now"',
     '"Fetch Hardcover Books" if st["hardcover"] else "Run pipeline now"'),
    ("the log description", '{"hardcover": "Get new books", "import": "Import my grabs"}',
     '{"hardcover": "Fetch Hardcover Books", "import": "Import my grabs"}'),
    ("the Yes / again hint", "then press Yes / Get new books again.", "then press Yes / Fetch Hardcover Books again."),
    ("the Still missing hint", "then press Get new books - it imports finished grabs", "then press Fetch Hardcover Books - it imports finished grabs"),
]
for label, old, _ in EDITS:
    n = src.count(old)
    if n != 1:
        sys.exit(f"Can't patch: expected exactly one copy of {label}, found {n}. Nothing was changed.")

backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(path, backup)
for label, old, new in EDITS:
    src = src.replace(old, new, 1)
open(path, "w", encoding="utf-8", newline="").write(src)
try:
    py_compile.compile(path, doraise=True)
except py_compile.PyCompileError as e:
    shutil.copy2(backup, path)
    sys.exit(f"The patched file didn't compile, so I restored the original: {e}")
print(f"Patched {path} (backup: {backup})")
