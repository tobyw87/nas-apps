#!/usr/bin/env python3
"""Patch button.py (on top of patch_button_search.py and patch_button_waiting_v3.py): a 'File now' button.

'Waiting to be filed' leaves a download alone for WAIT_MIN_AGE_MINUTES (10) so LazyLibrarian gets first go. For something
you grabbed yourself (e.g. from Prowlarr) LazyLibrarian won't take it, so the wait is just a delay. This adds a
'File now' button under the 'Add a book' box. Clicking it makes the list include brand-new downloads (anything not
written to in the last minute) for WAIT_NEW_MINUTES (30), then it goes back to normal. You still confirm the author and
title on each one, exactly as before. Only file a download when its torrent has finished.

Usage: python3 patch_button_filenow.py /path/to/button.py
Makes a backup, refuses to run twice, changes nothing if the file isn't what it expects,
restores the backup if the result doesn't compile.
"""
import py_compile
import shutil
import sys
import time

path = sys.argv[1]
src = open(path, encoding="utf-8", newline="").read()
if "_wait_new_until" in src:
    sys.exit("Already patched - nothing to do.")
for need, why in (("function lmsg(t,bad){", "patch_button_search.py"), ("_wait_lock = threading.Lock()", "patch_button_waiting.py")):
    if need not in src:
        sys.exit(f"Install {why} first (this one builds on it).")
CRLF = "\r\n" in src
if CRLF:
    src = src.replace("\r\n", "\n")

VARS_OLD = "_wait_lock = threading.Lock()"
VARS_NEW = '''_wait_lock = threading.Lock()
WAIT_NEW_MINUTES = int(os.environ.get("WAIT_NEW_MINUTES", "30"))     # how long 'File now' shows brand-new downloads
_wait_new_until = [0.0]'''

AGE_OLD = 'if now - g["newest"] < WAIT_MIN_AGE:'
AGE_NEW = 'if now - g["newest"] < (60 if time.time() < _wait_new_until[0] else WAIT_MIN_AGE):'

HTML_OLD = '<div class="sub" id="lsm"></div>'
HTML_NEW = ('<div class="sub">Grabbed something yourself (e.g. from Prowlarr)? '
            '<button type="button" onclick="wnow()">File now</button></div>\n' + HTML_OLD)

JS_OLD = "function lmsg(t,bad){"
JS_NEW = '''async function wnow(){
  lmsg("Looking for brand-new downloads ...");
  try{
    const r=await (await fetch("waitnow__Q__",{method:"POST"})).json();
    lmsg(r.ok?"Showing brand-new downloads under 'Waiting to be filed' for "+r.minutes+" minutes. File one only when its torrent has finished.":"Couldn't do that.",!r.ok);
  }catch(err){lmsg("Couldn't do that: "+err,true);}
}
function lmsg(t,bad){'''

POST_OLD = '        if path == "/dismiss" and key_ok(self.path):'
POST_NEW = '''        if path == "/waitnow" and key_ok(self.path):
            _wait_new_until[0] = time.time() + WAIT_NEW_MINUTES * 60
            with _wait_lock:
                _wait["at"] = 0.0                                      # re-check right away
            blog(f"  Showing brand-new downloads under 'Waiting to be filed' for {WAIT_NEW_MINUTES} minutes.")
            data = json.dumps({"ok": True, "minutes": WAIT_NEW_MINUTES}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
''' + POST_OLD

for blk in (HTML_NEW, JS_NEW):
    assert "\\" not in blk, "unexpected backslash in page text"

EDITS = [
    ("the wait lock", VARS_OLD, VARS_NEW),
    ("the minimum-age check", AGE_OLD, AGE_NEW),
    ("the Add a book status line", HTML_OLD, HTML_NEW),
    ("the page message function", JS_OLD, JS_NEW),
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
