#!/usr/bin/env python3
"""Patch button.py: one scrollbar (the log box no longer scrolls on its own), a shorter log inside a Homarr tile,
and an "Open full page" link that only shows inside a tile.

Usage: python3 patch_button_layout.py /path/to/button.py
Makes a backup, refuses to run twice, changes nothing if the file isn't what it expects,
restores the backup if the result doesn't compile.
"""
import py_compile
import shutil
import sys
import time

path = sys.argv[1]
src = open(path, encoding="utf-8", newline="").read()
if 'id="full"' in src:
    sys.exit("Already patched - nothing to do.")
CRLF = "\r\n" in src
if CRLF:
    src = src.replace("\r\n", "\n")

EDITS = [
    ("the log box scroll rule", "max-height:360px;overflow:auto", ""),
    ("the log label line", '<span id="l">__LOG__</span></div>',
     '<span id="l">__LOG__</span> &middot; <a id="full" href="./__Q__" target="_blank" rel="noopener">Open full page &#8599;</a></div>'),
    ("the log text line", r'const t=st.lines.join("\\n")||"Nothing yet.";',
     r'const lines=window.self!==window.top?st.lines.slice(-25):st.lines;const t=lines.join("\\n")||"Nothing yet.";'),
    ("the poll start", "poll();\n</script></body></html>",
     'if(window.self===window.top)document.getElementById("full").style.display="none";\npoll();\n</script></body></html>'),
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
