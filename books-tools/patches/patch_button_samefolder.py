#!/usr/bin/env python3
"""Patch button.py (on top of patch_button_waiting_v3.py): file a download into the folder ABS already has for that book.

Today 'Waiting to be filed' guesses Author and Title from the download's name, then you confirm. If you already have the
audiobook (say) in ABS/Jana Deleon/Marsh Mystics/, an epub downloaded later is guessed as 'Jana DeLeon' / 'Marsh Mystics'
and may land in a new folder if the spelling or punctuation differs. With this patch, when ABS has exactly one folder for
the same author and the same title words, the guess uses that folder's exact names, so the ebook goes in next to the
audiobook. It also fixes a swapped guess: a download called 'Marsh Mystics - Jana DeLeon.epub' (LazyLibrarian's
Title - Author order) was read as author 'Marsh Mystics' because any two capitalised words look like a name; if the
second half is an author ABS already has and the first isn't, the two are swapped back. You still confirm every filing.

Usage: python3 patch_button_samefolder.py /path/to/button.py
Makes a backup, refuses to run twice, changes nothing if the file isn't what it expects,
restores the backup if the result doesn't compile.
"""
import py_compile
import shutil
import sys
import time

path = sys.argv[1]
src = open(path, encoding="utf-8", newline="").read()
if "reuse its exact folder" in src:
    sys.exit("Already patched - nothing to do.")
CRLF = "\r\n" in src
if CRLF:
    src = src.replace("\r\n", "\n")

OLD = '''        author, title = _guess_author_title(top)
        matches = _abs_matches(abs_dirs, title or top)
        if not author and len(matches) == 1:
            author = matches[0].split(" / ")[0]            # the one book ABS already has: same author folder
'''
NEW = '''        author, title = _guess_author_title(top)
        _known = {a.lower() for a, _ in abs_dirs}
        if title and title.lower() in _known and author.lower() not in _known:
            author, title = title, author                  # 'Title - Author' where the title looks like a name
        matches = _abs_matches(abs_dirs, title or top)
        if len(matches) == 1:
            m_author, m_title = matches[0].split(" / ", 1)
            if not author:
                author = m_author                          # the one book ABS already has: same author folder
            if author.lower() == m_author.lower() and title and _wordset(title) == _wordset(m_title):
                author, title = m_author, m_title          # same book ABS already has: reuse its exact folder
'''
if src.count(OLD) != 1:
    sys.exit(f"Can't patch: expected exactly one copy of the filing guess in _scan_waiting, found {src.count(OLD)}. "
             "Nothing was changed.")
backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(path, backup)
src = src.replace(OLD, NEW, 1)
if CRLF:
    src = src.replace("\n", "\r\n")
open(path, "w", encoding="utf-8", newline="").write(src)
try:
    py_compile.compile(path, doraise=True)
except py_compile.PyCompileError as e:
    shutil.copy2(backup, path)
    sys.exit(f"The patched file didn't compile, so I restored the original: {e}")
print(f"Patched {path} (backup: {backup})")
