#!/usr/bin/env python3
"""Patch button.py (on top of patch_button_search.py): open Prowlarr's full search results from the 'Add a book' box.

Adds two things to the page (tile and full page):
  * a 'Prowlarr' button beside Search: opens Prowlarr in a new tab with whatever you typed, so you can browse
    every release it finds and pick one there (no LazyLibrarian search needed first);
  * a 'Prowlarr' button on each search result: opens Prowlarr with that book's title and author.
The new tab goes to PROWLARR_URL/search?query=... (the same link the log prints). The page asks its own server
(POST /prowlarr, checked with the usual key), which answers with a redirect, so the Prowlarr address stays in one place.

Usage: python3 patch_button_prowlarr.py /path/to/button.py
Makes a backup, refuses to run twice, changes nothing if the file isn't what it expects,
restores the backup if the result doesn't compile.
"""
import py_compile
import shutil
import sys
import time

path = sys.argv[1]
src = open(path, encoding="utf-8", newline="").read()
if "function lprowl" in src:
    sys.exit("Already patched - nothing to do.")
if "async function lsearch(e){" not in src:
    sys.exit("Install patch_button_search.py first (this one adds buttons to its 'Add a book' box).")
CRLF = "\r\n" in src
if CRLF:
    src = src.replace("\r\n", "\n")

CSS_OLD = ".sri .m{font-size:12px;opacity:.75}"
CSS_NEW = ".sri .m{font-size:12px;opacity:.75}.bt{display:flex;gap:6px;flex:none}"

FORM_OLD = '<button type="submit">Search</button></form>'
FORM_NEW = '<button type="submit">Search</button> <button type="button" onclick="lprowl()">Prowlarr</button></form>'

JS_OLD = "async function lsearch(e){"
JS_NEW = '''function lprowl(q){
  q=(q||document.getElementById("lsq").value).trim();
  if(q.length<2){lmsg("Type a title or author first.",true);return;}
  const f=document.createElement("form");f.method="POST";f.action="prowlarr__Q__";f.target="_blank";
  const i=document.createElement("input");i.type="hidden";i.name="q";i.value=q;f.appendChild(i);
  document.body.appendChild(f);f.submit();f.remove();
}
async function lsearch(e){'''

ROW_OLD = '<button type="button" data-i="${i}">Add</button></div>'
ROW_NEW = '<span class="bt"><button type="button" data-p="${i}">Prowlarr</button><button type="button" data-i="${i}">Add</button></span></div>'

BIND_OLD = 'document.querySelectorAll("#lsr button").forEach(b=>{b.onclick=()=>ladd(b);});'
BIND_NEW = ('document.querySelectorAll("#lsr button[data-i]").forEach(b=>{b.onclick=()=>ladd(b);});'
            'document.querySelectorAll("#lsr button[data-p]").forEach(b=>{b.onclick=()=>{const x=window._lres[+b.dataset.p];lprowl(x.title+" "+x.author);};});')

POST_OLD = '        if path == "/dismiss" and key_ok(self.path):'
POST_NEW = r'''        if path == "/prowlarr" and key_ok(self.path):
            form = parse_qs(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 65536)).decode("utf-8", "replace"))
            q = re.sub(r"\s+", " ", (form.get("q") or [""])[0]).strip()[:200]
            self.send_response(303)
            self.send_header("Location", f"{PROWLARR_URL}/search?query={urllib.parse.quote(q)}")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
''' + POST_OLD

for blk in (CSS_NEW, FORM_NEW, JS_NEW, ROW_NEW, BIND_NEW):
    assert "\\" not in blk, "unexpected backslash in page text"

EDITS = [
    ("the result-row style", CSS_OLD, CSS_NEW),
    ("the Search button", FORM_OLD, FORM_NEW),
    ("the lsearch function", JS_OLD, JS_NEW),
    ("the result row buttons", ROW_OLD, ROW_NEW),
    ("the result button binding", BIND_OLD, BIND_NEW),
    ("the /dismiss handler", POST_OLD, POST_NEW),
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
