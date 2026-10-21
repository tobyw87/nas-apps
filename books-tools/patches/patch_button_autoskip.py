#!/usr/bin/env python3
"""Patch button.py (on top of patch_button_search.py): stop looking for an audiobook that can't be found, automatically.

Today a missing audiobook stays Wanted in LazyLibrarian, so every run searches again and you end up marking it Ignored
by hand. With this patch, when the searches for an AUDIOBOOK all come up empty, the pipeline does what 'No, stop
looking' does: it marks that audiobook Skipped in LazyLibrarian and remembers it in data/button-skip.json. The log
still shows the Prowlarr link. The ebook is never touched.

Searching for the book again from 'Add a book' (with AudioBook ticked) lifts the skip again.
Switch it off with AUTO_SKIP_MISSING_AUDIOBOOKS=0 in the button service's environment (default: on).

Usage: python3 patch_button_autoskip.py /path/to/button.py
Makes a backup, refuses to run twice, changes nothing if the file isn't what it expects,
restores the backup if the result doesn't compile.
"""
import py_compile
import shutil
import sys
import time

path = sys.argv[1]
src = open(path, encoding="utf-8", newline="").read()
if "AUTO_SKIP_AUDIOBOOK" in src:
    sys.exit("Already patched - nothing to do.")
if "def search_books" not in src:
    sys.exit("Install patch_button_search.py first (this one lifts the skip when you search again).")
CRLF = "\r\n" in src
if CRLF:
    src = src.replace("\r\n", "\n")

CONST = '''AUTO_SKIP_AUDIOBOOK = os.environ.get("AUTO_SKIP_MISSING_AUDIOBOOKS", "1").strip().lower() not in ("0", "no", "false", "off")


def load_skips():'''

NOT_FOUND_OLD = '''                blog(f"  Couldn't find the {label} for {name(r)}")
                missing.add(bid)
'''
NOT_FOUND_NEW = '''                blog(f"  Couldn't find the {label} for {name(r)}")
                missing.add(bid)
                if fmt == "AudioBook" and AUTO_SKIP_AUDIOBOOK:
                    _sk = load_skips()
                    _sk.add(f"{bid}:{fmt}")
                    save_skips(_sk)
                    try:
                        ll("unqueueBook", id=bid, type=fmt)
                    except BaseException:
                        pass
                    results[(bid, fmt)] += " - not looking for it any more"
                    blog("    Won't keep looking for this audiobook (find it again from 'Add a book' to turn it back on)")
'''

ADD_OLD = '''    for f in fmts:
        res = _ll_api("queueBook", id=bid, type=f)
'''
ADD_NEW = '''    try:
        _sk = load_skips()
        _lift = {f"{bid}:{f}" for f in fmts} & _sk
        if _lift:
            save_skips(_sk - _lift)
    except Exception:
        pass
    for f in fmts:
        res = _ll_api("queueBook", id=bid, type=f)
'''

EDITS = [
    ("the skip-list loader", "def load_skips():", CONST),
    ("the 'couldn't find' message", NOT_FOUND_OLD, NOT_FOUND_NEW),
    ("the Add-a-book queue step", ADD_OLD, ADD_NEW),
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
