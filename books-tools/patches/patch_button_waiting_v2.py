#!/usr/bin/env python3
"""Patch button.py (on top of patch_button_waiting.py): smarter author/title guesses for the "Waiting to be filed"
list, a "possible match already in ABS" note, and a confirmation popup before filing.

Usage: python3 patch_button_waiting_v2.py /path/to/button.py
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
if "_abs_matches" in src:
    sys.exit("Already patched - nothing to do.")
if "waiting_items" not in src:
    sys.exit("Install patch_button_waiting.py first (this one builds on it).")
CRLF = "\r\n" in src
if CRLF:
    src = src.replace("\r\n", "\n")

NEW_FUNCS = r'''_STOP = {"the", "and", "of", "a", "an", "in", "to", "on", "for"}
_NOT_NAME = {"book", "series", "novel", "audiobook", "volume", "vol", "part", "the", "a", "an", "unabridged",
             "edition", "collection", "saga", "trilogy", "restoration"}
_SERIES_RE = re.compile(r"\bBook\s*\d+\b|\bSeries\b", re.I)


def _author_form(s):
    """'First Last' (also 'Last, First') if s looks like a person's name, else ''."""
    s = s.strip()
    m = re.fullmatch(r"([A-Z][\w'’\-]+),\s*([A-Z][\w.'’\- ]+)", s)
    if m:
        s = f"{m.group(2).strip()} {m.group(1)}"
    if not s or re.search(r"[\d\[\]():,]", s):
        return ""
    parts = s.split()
    if not 2 <= len(parts) <= 4 or any(p.lower().strip(".,") in _NOT_NAME for p in parts):
        return ""
    ok = all(re.fullmatch(r"[A-Z][A-Za-z'’\-]*\.?|&|de|van|von|der|la|le|di|da|bin|ibn", p) for p in parts)
    return s if ok else ""


def _clean_title(t):
    t = re.sub(r"\.(" + "|".join(sorted(AUDIO_EXTS)) + r")$", "", t, flags=re.I).replace("_", " ")
    t = re.sub(r"\s*[\[(][^\])]*[\])]", "", t)                                        # [tags] and (tags)
    t = re.sub(r"[,:\-–]?\s*\b[\w'’]+ Series,?\s*Book\s*\d+\b", "", t, flags=re.I)     # "Culture Series, Book 3"
    t = re.sub(r"[,:\-–]?\s*\bBook\s*\d+\b", "", t, flags=re.I)                        # "Book 2"
    t = re.sub(r"\s+A Novel$", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" -–,:")


def _guess_author_title(name):
    """A first guess from a download name. Wrong guesses are caught by the confirmation popup."""
    base = re.sub(r"\.(" + "|".join(sorted(AUDIO_EXTS)) + r")$", "", name, flags=re.I).replace("_", " ").strip()
    left, sep, right = base.partition(" - ")
    if sep:
        la, ra = _author_form(left), _author_form(right)
        if la:
            return la, _clean_title(right)
        if ra:
            return ra, _clean_title(left)
        if _SERIES_RE.search(left):
            return "", _clean_title(right)                    # "Old Man's War Book 2 - The Ghost Brigades"
    return "", _clean_title(base)


def _wordset(s):
    return {w for w in _words(s) if w not in _STOP and len(w) > 1}


def _abs_folders():
    """[(author, title folder)] for every Author/Title folder in ABS (one cheap directory listing)."""
    out = []
    for line in rclone("lsf", "-R", "--dirs-only", "--max-depth", "2", f"{RCLONE_REMOTE}:{ABS_PATH}",
                       timeout=600).splitlines():
        parts = line.strip("/").split("/")
        if len(parts) == 2:
            out.append((parts[0], parts[1]))
    return out


def _abs_matches(folders, title):
    """ABS folders whose title contains all of this title's words, in any format: a hint, not a decision."""
    tw = _wordset(title)
    return [f"{a} / {t}" for a, t in folders if tw and tw <= _wordset(t)][:3]
'''

# 1. replace the v1 guess function with the new helpers
m = re.search(r"def _guess_author_title\(name\):.*?\n\n\n(?=def _wait_skips)", src, re.S)
if not m:
    sys.exit("Can't patch: couldn't find _guess_author_title. Nothing was changed.")
GUESS_BLOCK = (m.start(), m.end())

EDITS = [
    ("the in_abs line",
     '    in_abs = {(p.rsplit(".", 1)[-1].lower(), s) for p, s, _ in _audio_listing(ABS_PATH, False)}\n',
     '    in_abs = {(p.rsplit(".", 1)[-1].lower(), s) for p, s, _ in _audio_listing(ABS_PATH, False)}\n'
     '    try:\n        abs_dirs = _abs_folders()\n    except Exception:\n        abs_dirs = []\n'),
    ("the guess line", '        author, title = _guess_author_title(top)\n',
     '        author, title = _guess_author_title(top)\n'
     '        matches = _abs_matches(abs_dirs, title or top)\n'
     '        if not author and len(matches) == 1:\n'
     '            author = matches[0].split(" / ")[0]            # the one book ABS already has: same author folder\n'),
    ("the item dict end", '"author": author, "title": title})',
     '"author": author, "title": title,\n                      "matches": matches})'),
    ("the .wi input style", ".wi input{", ".wm{color:#fab005;font-size:12px}\n.wi input{"),
    ("the poll function", "async function poll(){", r'''function wconfirm(f,e){
  const b=e.submitter;
  if(b&&b.classList.contains("no"))return true;
  const a=f.author.value.trim(),t=f.title.value.trim();
  if(!a||!t){alert("Fill in both the author and the title first.");return false;}
  const m=f.querySelector(".wm");
  return confirm("File this audiobook as:\\n\\nAuthor: "+a+"\\nTitle: "+t+(m?"\\n\\n"+m.textContent+"\\n(Filing it may create a duplicate.)":"")+"\\n\\nOK to file?");
}
async function poll(){'''),
]
FORM_RE = re.compile(r'document\.getElementById\("wl"\)\.innerHTML=w\.map\(x=>`.*?`\)\.join\(""\);\}', re.S)
NEW_FORM = r'''document.getElementById("wl").innerHTML=w.map(x=>`<form method="post" action="adopt__Q__" class="wi" onsubmit="return wconfirm(this,event)"><div class="wn">${q(x.name)}</div><div class="sub">${x.files} file(s) &middot; ${q(x.ext)} &middot; ${x.size_mb} MB &middot; waiting ${x.age_h} h</div>${(x.matches&&x.matches.length)?`<div class="wm">Possible match already in ABS: ${x.matches.map(q).join("; ")}</div>`:""}<input type="hidden" name="name" value="${q(x.name)}"><input name="author" placeholder="Author" value="${q(x.author)}"><input name="title" placeholder="Title" value="${q(x.title)}"><div class="row"><button class="yes">File it</button><button class="no" formaction="skipwait__Q__">Ignore</button></div></form>`).join("");}'''

for label, old, _ in EDITS:
    n = src.count(old)
    if n != 1:
        sys.exit(f"Can't patch: expected exactly one copy of {label}, found {n}. Nothing was changed.")
if len(FORM_RE.findall(src)) != 1:
    sys.exit("Can't patch: couldn't find the waiting-list form code. Nothing was changed.")

backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(path, backup)
src = src[:GUESS_BLOCK[0]] + NEW_FUNCS + "\n\n" + src[GUESS_BLOCK[1]:]
for label, old, new in EDITS:
    src = src.replace(old, new, 1)
src = FORM_RE.sub(lambda m: NEW_FORM, src, count=1)
if CRLF:
    src = src.replace("\n", "\r\n")
open(path, "w", encoding="utf-8", newline="").write(src)
try:
    py_compile.compile(path, doraise=True)
except py_compile.PyCompileError as e:
    shutil.copy2(backup, path)
    sys.exit(f"The patched file didn't compile, so I restored the original: {e}")
print(f"Patched {path} (backup: {backup})")
