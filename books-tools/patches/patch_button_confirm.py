#!/usr/bin/env python3
"""Patch button.py: replace the browser popup that File it asked for (confirm/alert, which a browser can block, leaving
the button dead) with a two-click confirmation inside the page. First click: the button turns orange and says exactly
what it will file ("Confirm: file as Author / Title - click again"). Second click files it. Ignore never asks.

Usage: python3 patch_button_confirm.py /path/to/button.py
Makes a backup, refuses to run twice, changes nothing if the file isn't what it expects,
restores the backup if the result doesn't compile.
"""
import py_compile
import re
import shutil
import sys
import time

path = sys.argv[1]
src = open(path, encoding="utf-8", newline="").read()
if "dataset.armed" in src:
    sys.exit("Already patched - nothing to do.")
CRLF = "\r\n" in src
if CRLF:
    src = src.replace("\r\n", "\n")

NEW = '''function wconfirm(f,e){
  const b=e.submitter;
  if(b&&b.classList.contains("no"))return true;
  const a=f.author.value.trim(),t=f.title.value.trim(),y=f.querySelector(".yes");
  if(!a||!t){y.textContent="Fill in author and title first";return false;}
  const k=a+"|"+t;
  if(f.dataset.armed===k)return true;
  f.dataset.armed=k;
  y.textContent="Confirm: file as "+a+" / "+t+(f.querySelector(".wm")?" (possible duplicate)":"")+" - click again";
  y.style.background="#e8590c";
  setTimeout(function(){f.dataset.armed="";y.textContent="File it";y.style.background="";},10000);
  return false;
}
'''
assert "\\" not in NEW and "'''" not in NEW
pat = re.compile(r"function wconfirm\(f,e\)\{.*?\n\}\n", re.S)
found = pat.findall(src)
if len(found) != 1:
    sys.exit(f"Can't patch: expected exactly one wconfirm function, found {len(found)}. Nothing was changed.")

backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(path, backup)
src = pat.sub(lambda m: NEW, src, count=1)
if CRLF:
    src = src.replace("\n", "\r\n")
open(path, "w", encoding="utf-8", newline="").write(src)
try:
    py_compile.compile(path, doraise=True)
except py_compile.PyCompileError as e:
    shutil.copy2(backup, path)
    sys.exit(f"The patched file didn't compile, so I restored the original: {e}")
print(f"Patched {path} (backup: {backup})")
