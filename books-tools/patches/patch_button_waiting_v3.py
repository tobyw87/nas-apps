#!/usr/bin/env python3
"""Patch button.py (on top of patch_button_waiting.py and _v2.py): the "Waiting to be filed" list also covers EBOOKS
(epub, mobi, azw3, pdf), and no longer hides downloads LazyLibrarian renamed to "<name>.fail" when their files aren't
in the library. File it puts an ebook in ABS/<Author>/<Title>/<Title> - <Author>.<ext>, like LazyLibrarian does.

Ebooks are only listed for 21 days after they were downloaded (WAIT_EBOOK_DAYS), so old leftovers stay quiet.

Usage: python3 patch_button_waiting_v3.py /path/to/button.py
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
if "WAIT_EBOOK_DAYS" in src:
    sys.exit("Already patched - nothing to do.")
if "_abs_matches" not in src:
    sys.exit("Install patch_button_waiting_v2.py first (this one builds on it).")
CRLF = "\r\n" in src
if CRLF:
    src = src.replace("\r\n", "\n")

LISTING = r'''EBOOK_EXTS = {"epub", "mobi", "azw3", "pdf"}
WAIT_EXTS = set(AUDIO_EXTS) | EBOOK_EXTS
WAIT_EBOOK_DAYS = int(os.environ.get("WAIT_EBOOK_DAYS", "21"))      # ebooks older than this are left alone


def _audio_listing(root, with_time):
    """[(path, size, mtime)] of the audio and ebook files under a seedbox folder."""
    args = ["lsf", "-R", "--files-only", "--ignore-case", "--format", "pst" if with_time else "ps"]
    for ext in sorted(WAIT_EXTS):
        args += ["--include", f"*.{ext}"]
    out = []
    for line in rclone(*args, f"{RCLONE_REMOTE}:{root}", timeout=600).splitlines():
        parts = line.rsplit(";", 2 if with_time else 1)
        if len(parts) < 2:
            continue
        try:
            size = int(parts[1])
        except ValueError:
            continue
        mt = 0
        if with_time and len(parts) == 3:
            try:
                mt = datetime.strptime(parts[2][:19], "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                pass
        out.append((parts[0], size, mt))
    return out


'''

CLEAN = r'''def _clean_title(t):
    t = re.sub(r"\.fail$", "", t, flags=re.I)
    t = re.sub(r"\.(" + "|".join(sorted(WAIT_EXTS)) + r")$", "", t, flags=re.I).replace("_", " ")
    t = re.sub(r"\s*[\[(][^\])]*[\])]", "", t)                                        # [tags] and (tags)
    t = re.sub(r"[,:\-\u2013]?\s*\b[\w'’]+ Series,?\s*Book\s*\d+\b", "", t, flags=re.I)     # "Culture Series, Book 3"
    t = re.sub(r"[,:\-\u2013]?\s*\bBook\s*\d+\b", "", t, flags=re.I)                        # "Book 2"
    t = re.sub(r"\s+A Novel$", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" -\u2013,:")


'''

GUESS = r'''def _guess_author_title(name):
    """A first guess from a download name. Wrong guesses are caught by the confirmation step."""
    base = re.sub(r"\.fail$", "", name, flags=re.I)
    base = re.sub(r"\.(" + "|".join(sorted(WAIT_EXTS)) + r")$", "", base, flags=re.I).replace("_", " ").strip()
    left, sep, right = base.partition(" - ")
    if sep:
        la, ra = _author_form(left), _author_form(right)
        if la:
            return la, _clean_title(right)
        if ra:
            return ra, _clean_title(left)
        if _SERIES_RE.search(left):
            return "", _clean_title(right)                    # "Old Man's War Book 2 - The Ghost Brigades"
    m = re.fullmatch(r"(.+?) by (.+)", base, re.I)             # "Absolution by Jeff VanderMeer"
    if m:
        a = _author_form(m.group(2))
        if a:
            return a, _clean_title(m.group(1))
    return "", _clean_title(base)


'''

SCAN = r'''def _scan_waiting():
    downloads = _audio_listing(SEEDBOX_DOWNLOADS, True)
    in_abs = {(p.rsplit(".", 1)[-1].lower(), s) for p, s, _ in _audio_listing(ABS_PATH, False)}
    try:
        abs_dirs = _abs_folders()
    except Exception:
        abs_dirs = []
    skips = _wait_skips()
    groups = {}
    for p, size, mt in downloads:
        top = p.split("/", 1)[0]
        if top.endswith(".unpack") or top in skips or size <= 0:
            continue
        g = groups.setdefault(top, {"files": [], "newest": 0})
        g["files"].append((p, size))
        g["newest"] = max(g["newest"], mt)
    now = time.time()
    items, files = [], {}
    for top, g in sorted(groups.items()):
        if top.endswith(".fail") and top[:-5] in groups:
            continue                                   # LazyLibrarian's leftover copy of a download that is also listed
        if now - g["newest"] < WAIT_MIN_AGE:
            continue                                   # too new: LazyLibrarian gets the first go
        if any((p.rsplit(".", 1)[-1].lower(), s) in in_abs for p, s in g["files"]):
            continue                                   # LazyLibrarian has filed it (or part of it)
        if all(p.rsplit(".", 1)[-1].lower() in EBOOK_EXTS for p, _ in g["files"]) \
                and now - g["newest"] > WAIT_EBOOK_DAYS * 86400:
            continue                                   # an old ebook leftover
        author, title = _guess_author_title(top)
        matches = _abs_matches(abs_dirs, title or top)
        if not author and len(matches) == 1:
            author = matches[0].split(" / ")[0]            # the one book ABS already has: same author folder
        files[top] = g["files"]
        items.append({"name": top, "files": len(g["files"]),
                      "ext": "/".join(sorted({p.rsplit(".", 1)[-1].lower() for p, _ in g["files"]})),
                      "size_mb": round(sum(s for _, s in g["files"]) / 1048576),
                      "age_h": round((now - g["newest"]) / 3600, 1), "author": author, "title": title,
                      "matches": matches})
    return items, files


'''

ADOPT = r'''def adopt_waiting(name, author, title):
    """File a waiting download into ABS/<author>/<title>/ with hard links (nothing is copied, moved or deleted)."""
    author, title = (re.sub(r"\s+", " ", x or "").strip() for x in (author, title))
    if not (author and title):
        raise ValueError("both an author and a title are needed")
    if ".." in author + title or any(c in (author + title) for c in '/\\\n\r\x00'):
        raise ValueError("the author and title can't contain / or ..")
    with _wait_lock:
        files = list(_wait["files"].get(name) or [])
    if not files:
        raise ValueError("that download isn't in the list any more")
    manual = f'on the seedbox: ~/scripts/adopt_manual.sh link "{name}" "{author}" "{title}" (audiobooks only)'
    if not hardlink_ok():
        raise RuntimeError("this rclone can't make hard links; " + manual)
    chosen = [f for f in files if f[0].lower().endswith(".m4b")] or files        # prefer M4B when a download has any
    dest = f"{ABS_PATH}/{author}/{title}"
    for p, size in chosen:
        base = p.rsplit("/", 1)[-1]
        ext = base.rsplit(".", 1)[-1].lower()
        if ext in EBOOK_EXTS:
            base = f"{title} - {author}.{ext}"                                     # like LazyLibrarian names an ebook
        try:
            rclone("copyto", "--sftp-copy-is-hardlink", "--no-check-dest", "--max-transfer", "1",
                   "--cutoff-mode", "hard", f"{RCLONE_REMOTE}:{SEEDBOX_DOWNLOADS}/{p}",
                   f"{RCLONE_REMOTE}:{dest}/{base}", timeout=120)
        except Exception as e:
            raise RuntimeError(f"the seedbox wouldn't make a hard link ({short(str(e), 80)}); " + manual)
        listing = rclone("lsf", "--files-only", "--format", "ps", f"{RCLONE_REMOTE}:{dest}")
        if f"{base};{size}" not in listing.splitlines():
            raise RuntimeError(f"'{base}' didn't arrive intact in ABS; check {dest} and " + manual)
    blog(f"  Filed '{name}' into Audiobookshelf as {author} / {title} ({len(chosen)} file(s)); the torrent keeps seeding.")
    with _wait_lock:
        _wait["items"] = [i for i in _wait["items"] if i["name"] != name]
        _wait["at"] = 0.0                                                          # re-check soon


'''

REPLACE = [
    ("_audio_listing", r"def _audio_listing\(.*?\n\n\n", LISTING),
    ("_clean_title", r"def _clean_title\(t\):.*?\n\n\n", CLEAN),
    ("_guess_author_title", r"def _guess_author_title\(name\):.*?\n\n\n", GUESS),
    ("_scan_waiting", r"def _scan_waiting\(\):.*?\n\n\n", SCAN),
    ("adopt_waiting", r"def adopt_waiting\(.*?\n\n\n(?=def skip_waiting)", ADOPT),
]
pats = []
for label, rx, new in REPLACE:
    pat = re.compile(rx, re.S)
    n = len(pat.findall(src))
    if n != 1:
        sys.exit(f"Can't patch: expected exactly one {label}(), found {n}. Nothing was changed.")
    pats.append((pat, new))

backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(path, backup)
for pat, new in pats:
    src = pat.sub(lambda m, new=new: new, src, count=1)
if CRLF:
    src = src.replace("\n", "\r\n")
open(path, "w", encoding="utf-8", newline="").write(src)
try:
    py_compile.compile(path, doraise=True)
except py_compile.PyCompileError as e:
    shutil.copy2(backup, path)
    sys.exit(f"The patched file didn't compile, so I restored the original: {e}")
print(f"Patched {path} (backup: {backup})")
