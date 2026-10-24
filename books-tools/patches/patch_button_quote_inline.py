#!/usr/bin/env python3
"""Patch button.py (on top of patch_button_quotes.py, patch_button_quote_top.py and patch_button_filenow.py):
  * the book quote becomes the first line INSIDE the log box (while the pipeline is idle), not a line above the button;
  * the 'File now' button moves up beside 'Get new books' (a smaller button on the same row).

Usage: python3 patch_button_quote_inline.py /path/to/button.py
Makes a backup, refuses to run twice, changes nothing if the file isn't what it expects,
restores the backup if the result doesn't compile.
"""
import py_compile
import shutil
import sys
import time

path = sys.argv[1]
src = open(path, encoding="utf-8", newline="").read()
if "qText" in src:
    sys.exit("Already patched - nothing to do.")
CRLF = "\r\n" in src
if CRLF:
    src = src.replace("\r\n", "\n")

BS2 = "\\\\"          # two backslash characters: how the page's Python source spells a JavaScript \n

EDITS = [
    # 1. the quote line above the button goes away; CSS for the button row arrives
    ("the quote line at the top", '</style></head><body><div class="wrap">\n<div id="qline" style="visibility:hidden"></div>',
     '.toprow{display:flex;gap:8px;align-items:stretch}.toprow form{flex:1;margin:0}'
     '.toprow .fn{width:auto;margin:0;padding:0 16px;font-size:14px;white-space:nowrap}'
     '</style></head><body><div class="wrap">'),
    # 2. File now beside Get new books
    ("the Get new books button", '<form method="post" action="run__Q__"><button id="b" __DISABLED__>__LABEL__</button></form>',
     '<div class="toprow"><form method="post" action="run__Q__"><button id="b" __DISABLED__>__LABEL__</button></form>'
     '<button type="button" id="fn" class="fn" title="Show brand-new downloads (e.g. from Prowlarr) under Waiting to be filed" '
     'onclick="wnow()">File now</button></div>'),
    ("the old File now line", '<div class="sub">Grabbed something yourself (e.g. from Prowlarr)? '
     '<button type="button" onclick="wnow()">File now</button></div>\n', ''),
    ("the File now feedback", 'const r=await (await fetch("waitnow__Q__",{method:"POST"})).json();',
     'const r=await (await fetch("waitnow__Q__",{method:"POST"})).json();'
     'const fb=document.getElementById("fn");if(fb&&r.ok){fb.textContent="Showing";setTimeout(()=>{fb.textContent="File now";},4000);}'),
    # 3. the quote is kept in a variable instead of its own element
    ("the quote variables", "let qIdle=false,qLast=-1;", "let qIdle=false,qLast=-1,qText='';"),
    ("the quote element lookup", "const el=document.getElementById('qline');\n  const idle=", "const idle="),
    ("the quote text", "el.textContent='# \"'+QUOTES[i][0]+'\" - '+QUOTES[i][1];}", "qText='# \"'+QUOTES[i][0]+'\" - '+QUOTES[i][1];}"),
    ("the quote show/hide", "qIdle=idle;el.style.visibility=idle?'visible':'hidden';", "qIdle=idle;if(!idle)qText='';"),
    # 4. the log box starts with the quote while idle
    ("the log text", f'const t=lines.join("{BS2}n")||"Nothing yet.";',
     f'const t=(qText?qText+"{BS2}n{BS2}n":"")+(lines.join("{BS2}n")||"Nothing yet.");'),
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
