#!/usr/bin/env python3
"""books-tools: organize the NAS ebook archive into Library/Author/Title and
pull ebooks one-way from the seedbox.

Commands (run through books.sh on the NAS):
  pull                     copy new ebooks from the seedbox into the mirror folder (one-way)
  organize [--apply]       build Library/Author/Title from the archive + mirror
                           (dry run unless --apply; never deletes or changes originals)
  run                      pull, then organize --apply (what the scheduled task runs)
  status                   counts of what has been processed
  unpack                   extract .rar/.zip author bundles into _Unpacked/ (archives only read)
  textbooks [--apply PLAN] sort the Library: textbooks (PDF or EPUB) into _Textbooks/, and
                           delete Library copies of PDFs that aren't textbooks (dry run first)
  manga                    plan moving the manga folders into Manga/<Series>/ (cleanup-apply.sh moves)
  migrate-db               update remembered paths after migrate.sh (one-time layout change)
  cleanup                  plan moving everything except Library and the mirror out of the
                           archive (writes a plan; cleanup-apply.sh does the moving)
  setup-remote HOST USER   save the seedbox SFTP login (password read from stdin)
  rclone ...               run rclone with this tool's config (for troubleshooting)

Safety rules built in:
  * Nothing is ever deleted, except Library copies of non-textbook PDFs by
    `textbooks --apply` (the originals stay in the archive). Originals in the archive and mirror are only read; only cleanup's
    plan (carried out by cleanup-apply.sh) moves originals, and only to holding folders.
  * The seedbox is only ever read from (rclone copy remote -> NAS, and listings).
  * New library files are written to a temp name and renamed when complete.
"""
import argparse
import csv
import fcntl
import hashlib
import json
import os
import posixpath
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

ARCHIVE_DIR = Path(os.environ.get("ARCHIVE_DIR", "/books"))
# 5_Books ends up holding only these three folders, each its own BookOrbit library:
#   EPUB Archive  Author/Title/Title - Author.epub   (the "Library" in this code)
#   Textbooks     Author/Title/Title - Author.pdf|epub, Springer/ for Springer downloads
#   Manga         Series/Series v01.cbz              (books.sh manga)
LIBRARY_DIR = Path(os.environ.get("EPUB_DIR") or ARCHIVE_DIR / "EPUB Archive")
TEXTBOOKS_DIR = Path(os.environ.get("TEXTBOOKS_DIR") or ARCHIVE_DIR / "Textbooks")
MANGA_DIR = Path(os.environ.get("MANGA_DIR") or ARCHIVE_DIR / "Manga")
# What comes down from the seedbox, kept outside 5_Books (the pull compares against it).
MIRROR_DIR = Path(os.environ.get("SEEDBOX_MIRROR_DIR") or ARCHIVE_DIR.parent / "_Seedbox_Mirror" / "Books")
# Before the Oct 2026 layout change (migrate.sh moves them):
OLD_LIBRARY_DIR = ARCHIVE_DIR / "Library"
OLD_MIRROR_DIR = ARCHIVE_DIR / "_Seedbox_Mirror"
KEEP_DIRS = [LIBRARY_DIR, TEXTBOOKS_DIR, MANGA_DIR, MIRROR_DIR]   # never read as sources
LOGS_DIR = Path(os.environ.get("LOGS_DIR", "/data/logs"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
SEEDBOX_PATH = os.environ.get("SEEDBOX_PATH", "/home/mynock42/media/ABS")
RCLONE_REMOTE = os.environ.get("RCLONE_REMOTE", "seedbox")
RCLONE_CONFIG = os.environ.get("RCLONE_CONFIG", str(DATA_DIR / "rclone.conf"))

PULL_EXTS = {"epub", "mobi", "azw", "azw3", "prc", "pdf"}   # what comes down from the seedbox
# Older formats found in the archive (and inside its .rar/.zip author bundles); converted to EPUB.
EBOOK_EXTS = PULL_EXTS | {"lit", "pdb", "rtf", "txt"}
CONVERT_EXTS = {"mobi", "azw", "azw3", "prc", "lit", "pdb", "rtf", "txt"}
AUDIO_EXTS = ["m4b", "mp3", "m4a", "flac", "opus", "aac", "ogg", "wma", "wav"]
# Best copy of a book wins: EPUB, then structured ebook formats, then RTF, PDF, plain text.
PRIORITY = {"epub": 3, "mobi": 2, "azw": 2, "azw3": 2, "prc": 2, "lit": 2, "pdb": 2,
            "rtf": 1.5, "pdf": 1, "txt": 0.5}
TEXT_EXTS = {"rtf", "txt"}
MIN_TEXT_BYTES = 20_000          # smaller .txt/.rtf are readmes and index files, not books
ARCHIVE_EXTS = {"rar", "zip"}
UNPACKED = "_Unpacked"           # archives are extracted to ARCHIVE_DIR/_Unpacked/<archive path>/
LATER_RAR_PART = re.compile(r"\.part0*([2-9]\d*|1\d+)\.rar$", re.I)   # unar reads these via part 1
SKIP_DIRS = {"@eaDir", "#recycle", "#snapshot", ".@__thumb"}
MARKER = ".books-tools"
UNSORTED = "_Unsorted"
# Textbooks (PDF or EPUB) live in TEXTBOOKS_DIR. PDFs that aren't textbooks are not kept.
SPRINGER_DIR = "Springer"                 # TEXTBOOKS_DIR/Springer: Springer downloads
OLD_TEXTBOOK_DIRS = ("_Springer Textbooks", "_Textbooks")   # older places inside the Library
OLD_SPRINGER_DIR = "_Springer Textbooks"
CONVERT_TIMEOUT = int(os.environ.get("CONVERT_TIMEOUT", "7200"))  # seconds per book
# Springer download names: Author2017_Chapter_Title.pdf (a chapter), 2015_Bookmatter_Title_2.pdf
# (index/back pages), 2017_Book_Title.pdf (a whole book). Only whole books go in the Library.
SPRINGER_PART = re.compile(r"_(Chapter|Bookmatter|Frontmatter|ReferenceWorkEntry)_")
SPRINGER_BOOK = re.compile(r"^(\d{4})_Book_(.+)$")
ISBN_NAME = re.compile(r"(?<!\d)97[89][-_ ]?(\d[-_ ]?){9}\d(?!\d)|_Book_")
TEXTBOOK_FOLDER = re.compile(r"springer|text ?books?", re.I)
TEXTBOOK_PUBLISHERS = re.compile(
    r"\b(springer|wiley|elsevier|academic press|crc|taylor ?& ?francis|routledge|university press|"
    r"mit press|pearson|mcgraw|cengage|o'?reilly|packt|apress|manning|no starch|addison|"
    r"prentice|world scientific|de gruyter|birkh|sage pub|morgan kaufmann|artech|ieee|siam|"
    r"american mathematical|wolters|kluwer|lippincott|thieme|jones ?& ?bartlett|mosby|saunders|"
    r"butterworth|newnes|pragmatic|press syndicate of the university)", re.I)
TEXTBOOK_TITLE = re.compile(
    r"\b(introduction to|an introduction|handbook of|principles of|fundamentals of|"
    r"textbook|lecture notes|\d+(st|nd|rd|th) ed(ition|\.))", re.I)
TEXTBOOK_TAGS = re.compile(r"textbook|study aids|mathematics|physics|chemistry|engineering|"
                           r"computer science|programming|statistics", re.I)

JUNK_AUTHORS = {"unknown", "unknown author", "author", "calibre", "administrator", "admin",
                "user", "owner", "various", "anonymous author", "none", "n/a", "na"}
JUNK_TITLES = {"unknown", "untitled", "title", "none", "n/a", "na", "book", "ebook"}

DC = "{http://purl.org/dc/elements/1.1/}"
OPF = "{http://www.idpf.org/2007/opf}"


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- database

def db_open():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "state.db")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY, size INTEGER, mtime REAL, sha TEXT,
            author TEXT, title TEXT, meta_src TEXT,
            status TEXT, dest TEXT, note TEXT, updated REAL);
        CREATE TABLE IF NOT EXISTS library (
            path TEXT PRIMARY KEY, bkey TEXT, fmt TEXT, sha TEXT, source TEXT);
        CREATE INDEX IF NOT EXISTS library_bkey ON library(bkey);
        CREATE INDEX IF NOT EXISTS library_sha ON library(sha);
        CREATE TABLE IF NOT EXISTS authors (akey TEXT PRIMARY KEY, display TEXT);
    """)
    cols = {r["name"] for r in db.execute("PRAGMA table_info(files)")}
    for col in ("publisher", "tags"):   # added later; NULL = not read yet
        if col not in cols:
            db.execute(f"ALTER TABLE files ADD COLUMN {col} TEXT")
    return db


# ---------------------------------------------------------------- names

def norm(s):
    """Comparison key: case/accent/punctuation-insensitive."""
    s = unicodedata.normalize("NFKD", (s or "").casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"^(the|a|an)\s+", "", s.strip())
    return re.sub(r"[\W_]+", "", s)


def clean_text(s):
    s = unicodedata.normalize("NFC", s or "")
    return re.sub(r"\s+", " ", s).strip()


def fix_author(a):
    a = clean_text(a)
    a = re.sub(r"\s*\[[^\]]*\]\s*$", "", a)          # ebook-meta's "[Last, First]" sort form
    a = re.split(r"\s+&\s+|\s*;\s*", a)[0].strip()      # first author only
    if not a or a.casefold() in JUNK_AUTHORS:
        return None
    if a.count(",") == 1:                                # "Last, First" -> "First Last"
        last, first = (x.strip() for x in a.split(","))
        if first and last and not re.fullmatch(r"(jr|sr|ii|iii|iv|phd|md)\.?", first, re.I):
            a = f"{first} {last}"
    a = re.sub(r"\.(?=[A-Z])", ". ", a)                  # "J.R.R. Tolkien" -> "J. R. R. Tolkien"
    return re.sub(r"\s+", " ", a).strip()


def fix_title(t):
    t = clean_text(t)
    if not t or t.casefold() in JUNK_TITLES:
        return None
    return t


def truncate_bytes(s, limit):
    b = s.encode("utf-8")
    if len(b) <= limit:
        return s
    return b[:limit].decode("utf-8", "ignore").rstrip(" .-")


def safe(s, limit):
    """Make a string safe as a folder/file name on the NAS and over SMB (Windows)."""
    s = unicodedata.normalize("NFC", s)
    s = s.replace(":", " -")
    s = re.sub(r'[\\/*?"<>|\x00-\x1f]', "", s)
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ").lstrip(".")
    return truncate_bytes(s, limit) or "Untitled"


# ---------------------------------------------------------------- metadata

def epub_meta(path):
    with zipfile.ZipFile(path) as z:
        container = ET.fromstring(z.read("META-INF/container.xml"))
        rootfile = container.find(".//{*}rootfile").get("full-path")
        opf = ET.fromstring(z.read(rootfile))
    md = opf.find("{*}metadata")
    if md is None:
        return None, None
    titles = [t.text for t in md.iter(DC + "title") if t.text and t.text.strip()]
    roles = {}
    for m in md.findall(".//{*}meta"):
        if m.get("property") == "role" and m.get("refines"):
            roles[m.get("refines").lstrip("#")] = (m.text or "").strip()
    creators = []
    for c in md.iter(DC + "creator"):
        role = c.get(OPF + "role") or roles.get(c.get("id") or "", "")
        if c.text and c.text.strip():
            creators.append((role, c.text.strip()))
    authors = [n for r, n in creators if r in ("", "aut")] or [n for _, n in creators]
    return (authors[0] if authors else None), (titles[0] if titles else None)


def ebook_meta(path):
    try:
        out = subprocess.run(["ebook-meta", str(path)], capture_output=True, text=True,
                             timeout=180).stdout
    except Exception:
        return None, None
    title = author = None
    for line in out.splitlines():
        key, _, val = line.partition(":")
        key = key.strip().lower()
        if key == "title" and title is None:
            title = val.strip()
        elif key == "author(s)" and author is None:
            author = val.strip()
    return author, title


def extra_meta(path, ext):
    """Publisher and subjects/tags, for spotting textbooks."""
    if ext == "epub":
        try:
            with zipfile.ZipFile(path) as z:
                container = ET.fromstring(z.read("META-INF/container.xml"))
                opf = ET.fromstring(z.read(container.find(".//{*}rootfile").get("full-path")))
            md = opf.find("{*}metadata")
            if md is not None:
                pubs = [p.text.strip() for p in md.iter(DC + "publisher") if p.text and p.text.strip()]
                subs = [t.text.strip() for t in md.iter(DC + "subject") if t.text and t.text.strip()]
                return (pubs[0] if pubs else ""), ", ".join(subs)
        except Exception:
            pass
    try:
        out = subprocess.run(["ebook-meta", str(path)], capture_output=True, text=True,
                             timeout=180).stdout
    except Exception:
        return "", ""
    pub = tags = ""
    for line in out.splitlines():
        key, _, val = line.partition(":")
        key = key.strip().lower()
        if key == "publisher" and not pub:
            pub = val.strip()
        elif key == "tags" and not tags:
            tags = val.strip()
    return pub, tags


def textbook_reason(path, ext, title=None, publisher=None, tags=None, source=None):
    """Why this looks like a textbook, or "" if it doesn't. Cheap name checks first;
    publisher/tags are only looked at when given."""
    for p in (path, Path(source) if source else None):
        if p is None:
            continue
        if SPRINGER_BOOK.match(p.stem):
            return "Springer book file name"
        if ISBN_NAME.search(p.name):
            return "ISBN in file name"
        if OLD_SPRINGER_DIR in p.parts or TEXTBOOKS_DIR in p.parents:
            return "already filed as a textbook"
        for d in p.parent.parts:
            if TEXTBOOK_FOLDER.search(d):
                return f"in folder '{d}'"
    if title and TEXTBOOK_TITLE.search(title):
        return f"title: {title}"
    if publisher and TEXTBOOK_PUBLISHERS.search(publisher):
        return f"publisher: {publisher}"
    if tags and TEXTBOOK_TAGS.search(tags) and not re.search(r"fiction|novel", tags, re.I):
        return f"tags: {tags[:80]}"
    return ""


def filename_meta(path):
    """Fallback: "Author - Title.ext" style file names."""
    stem = re.sub(r"[\(\[][^\)\]]*[\)\]]", "", path.stem)       # drop "(retail)", "[epub]"...
    stem = stem.replace("_", " ")
    parts = [p.strip() for p in re.split(r"\s+-\s+", stem) if p.strip()]
    if len(parts) >= 2:
        return parts[0], " - ".join(parts[1:])
    return None, None


def read_meta(path, ext):
    author = title = None
    src = None
    if ext == "epub":
        try:
            author, title = epub_meta(path)
            src = "epub"
        except Exception:
            author = title = None
    if not (fix_author(author) and fix_title(title)):
        a2, t2 = ebook_meta(path)
        if t2 and clean_text(t2) == clean_text(path.stem):
            t2 = None  # ebook-meta just echoed the file name (txt/rtf); parse it below instead
        author = author if fix_author(author) else a2
        title = title if fix_title(title) else t2
        src = src or "ebook-meta"
    author, title = fix_author(author), fix_title(title)
    if not (author and title):
        fa, ft = filename_meta(path)
        if not author and fix_author(fa) and ft:
            author = fix_author(fa)
            src = "filename"
        if not title and fix_title(ft):
            title = fix_title(ft)
            src = "filename"
    return author, title, src


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- scanning

def walk_books(root, exclude=()):
    exclude = {Path(e).resolve() for e in exclude}
    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and not d.startswith(".")
                             and (dp / d).resolve() not in exclude)
        for fn in sorted(filenames):
            if fn.startswith(".") or fn.startswith("._"):
                continue
            ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
            if ext in EBOOK_EXTS:
                if ext in TEXT_EXTS and (dp / fn).stat().st_size < MIN_TEXT_BYTES:
                    continue
                yield dp / fn, ext


def book_key(author, title, sha):
    if author and title:
        return f"{norm(author)}|{norm(title)}"
    return f"unsorted|{sha}"


def index_library(db):
    """Record what is already in the Library (first run, or after the database was reset)."""
    n = 0
    for root in (LIBRARY_DIR, TEXTBOOKS_DIR):
        if not root.exists():
            continue
        for path, ext in walk_books(root):
            if db.execute("SELECT 1 FROM library WHERE path=?", (str(path),)).fetchone():
                continue
            sha = sha256(path)
            first = path.relative_to(root).parts[0]
            if first in (UNSORTED, SPRINGER_DIR, UNCONVERTED):
                author = title = None
            else:
                author, title, _ = read_meta(path, ext)
            db.execute("INSERT OR REPLACE INTO library VALUES (?,?,?,?,?)",
                       (str(path), book_key(author, title, sha), ext, sha, "existing"))
            if author:
                db.execute("INSERT OR IGNORE INTO authors VALUES (?,?)", (norm(author), first))
            n += 1
    db.commit()
    if n:
        log(f"Indexed {n} file(s) already in the Library")


def require_new_layout():
    """Stop until migrate.sh has moved Library -> EPUB Archive and the mirror out of 5_Books."""
    if OLD_LIBRARY_DIR.exists() or OLD_MIRROR_DIR.exists():
        sys.exit(f"STOP: {OLD_LIBRARY_DIR.name} or {OLD_MIRROR_DIR.name} is still in {ARCHIVE_DIR}.\n"
                 f"Run  sudo /volume1/docker/books-tools/migrate.sh  first (moves them, instantly).")


def check_library_folder():
    if LIBRARY_DIR.exists() and any(LIBRARY_DIR.iterdir()) and not (LIBRARY_DIR / MARKER).exists():
        sys.exit(f"STOP: {LIBRARY_DIR} already exists and was not created by this tool.\n"
                 f"Rename that folder or set LIBRARY_DIR in .env to a different folder.")


# ---------------------------------------------------------------- organize

def organize(apply, retry_errors=False):
    require_new_layout()
    check_library_folder()
    db = db_open()
    if apply:
        LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        (LIBRARY_DIR / MARKER).write_text("Created by books-tools. Do not delete this file.\n")
    if LIBRARY_DIR.exists():
        index_library(db)

    sources = [(ARCHIVE_DIR, KEEP_DIRS)]
    if MIRROR_DIR.exists():
        sources.append((MIRROR_DIR, []))

    # 1. find new or changed files
    cands = []
    scanned = 0
    for root, exclude in sources:
        if not root.exists():
            log(f"Skipping missing folder {root}")
            continue
        for path, ext in walk_books(root, exclude):
            scanned += 1
            st = path.stat()
            row = db.execute("SELECT * FROM files WHERE path=?", (str(path),)).fetchone()
            same = row is not None and row["size"] == st.st_size and abs(row["mtime"] - st.st_mtime) < 1
            if same and row["status"] and not (retry_errors and row["status"] == "error"):
                continue
            if same and row["sha"]:
                sha, author, title, src = row["sha"], row["author"], row["title"], row["meta_src"]
            else:
                sha = sha256(path)
                author, title, src = read_meta(path, ext)
                db.execute("""INSERT OR REPLACE INTO files (path,size,mtime,sha,author,title,meta_src,updated)
                              VALUES (?,?,?,?,?,?,?,?)""",
                           (str(path), st.st_size, st.st_mtime, sha, author, title, src, time.time()))
            cands.append(dict(path=path, ext=ext, size=st.st_size, mtime=st.st_mtime, sha=sha,
                              author=author, title=title, src=src,
                              key=book_key(author, title, sha)))
            if len(cands) % 250 == 0:
                db.commit()
                log(f"Read {len(cands)} new file(s)...")
    db.commit()
    log(f"Scanned {scanned} ebook file(s); {len(cands)} new or changed")

    # 2. decide what to do with each one
    lib_shas = {r["sha"] for r in db.execute("SELECT sha FROM library")}
    lib_best = {}
    for r in db.execute("SELECT bkey, fmt, path FROM library"):
        p = PRIORITY.get(r["fmt"], 0)
        if p > lib_best.get(r["bkey"], (0, ""))[0]:
            lib_best[r["bkey"]] = (p, r["path"])
    authors = {r["akey"]: r["display"] for r in db.execute("SELECT akey, display FROM authors")}
    taken = {r["path"].casefold() for r in db.execute("SELECT path FROM library")}

    plan = []
    groups = {}
    def textbook(c):
        """Textbook check, reading publisher/tags once per file (cached in the database)."""
        if "textbook" not in c:
            reason = textbook_reason(c["path"], c["ext"], c["title"])
            if not reason:
                row = db.execute("SELECT publisher, tags FROM files WHERE path=?", (str(c["path"]),)).fetchone()
                if row is None or row["publisher"] is None:
                    pub, tags = extra_meta(c["path"], c["ext"])
                    db.execute("UPDATE files SET publisher=?, tags=? WHERE path=?", (pub, tags, str(c["path"])))
                else:
                    pub, tags = row["publisher"], row["tags"]
                reason = textbook_reason(c["path"], c["ext"], c["title"], pub, tags)
            c["textbook"] = reason
        return c["textbook"]

    for c in cands:
        if c["ext"] == "pdf" and SPRINGER_PART.search(c["path"].name):
            c["action"], c["note"] = "skip-part", "Springer chapter/back pages - left in the archive"
            plan.append(c)
            continue
        if c["ext"] == "pdf" and not textbook(c):
            c["action"], c["note"] = "skip-pdf", "PDF that isn't a textbook - not kept"
            plan.append(c)
            continue
        groups.setdefault(c["key"], []).append(c)

    # pick one spelling per author for the folder name ("Brandon Sanderson", not "brandon sanderson")
    variants = {}
    for c in cands:
        if c["author"]:
            variants.setdefault(norm(c["author"]), []).append(c["author"])
    for akey, names in variants.items():
        if akey not in authors:
            authors[akey] = safe(best_spelling(names), 80)

    seen_sha = {}
    for key in sorted(groups):
        items = sorted(groups[key], key=lambda c: (-PRIORITY[c["ext"]], -c["size"], str(c["path"])))
        best = None
        for c in items:
            if c["sha"] in lib_shas or c["sha"] in seen_sha:
                c["action"] = "skip-identical"
                c["note"] = (f"identical to {seen_sha[c['sha']]}" if c["sha"] in seen_sha
                             else "an identical file is already in the Library")
                plan.append(c)
                continue
            seen_sha[c["sha"]] = str(c["path"])
            if best is None:
                have = lib_best.get(key)
                if have and have[0] >= PRIORITY[c["ext"]]:
                    c["action"], c["note"] = "skip-duplicate", f"already in Library: {have[1]}"
                else:
                    best = c
                    c["action"] = "convert" if c["ext"] in CONVERT_EXTS else "copy"
                    # a textbook if this copy, or any other copy of the same book, looks like one
                    c["textbook"] = next((r for r in (textbook_reason(o["path"], o["ext"], o["title"])
                                                      for o in items) if r), "") or textbook(c)
                    c["dest"] = dest_for(c, authors, taken)
                    c["note"] = f"metadata from {c['src']}" if c["src"] == "filename" else ""
                    if c["textbook"]:
                        c["note"] = (c["note"] + "; " if c["note"] else "") + f"textbook ({c['textbook']})"
                    if have:
                        c["note"] = (c["note"] + "; " if c["note"] else "") + f"better format than {have[1]}"
            else:
                c["action"], c["note"] = "skip-duplicate", f"same book as {best['path']}"
            plan.append(c)

    # 3. carry it out
    counts = {}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for i, c in enumerate(plan, 1):
        if apply and c["action"] in ("copy", "convert"):
            try:
                do_place(c)
            except Exception as e:  # keep going; report it
                c["action"], c["note"] = "error", str(e)[-500:]
            if c["action"] != "error":
                db.execute("INSERT OR REPLACE INTO library VALUES (?,?,?,?,?)",
                           (str(c["dest"]), c["key"], "pdf" if c["ext"] == "pdf" else "epub",
                            c["sha"], str(c["path"])))
            if i % 25 == 0:
                log(f"Placed {i}/{len(plan)}...")
        if apply:
            db.execute("UPDATE files SET status=?, dest=?, note=?, updated=? WHERE path=?",
                       (c["action"], str(c.get("dest") or ""), c.get("note", ""), time.time(), str(c["path"])))
            if i % 25 == 0:
                db.commit()
        counts[c["action"]] = counts.get(c["action"], 0) + 1
    if apply:
        for akey, display in authors.items():
            db.execute("INSERT OR IGNORE INTO authors VALUES (?,?)", (akey, display))
    db.commit()

    # 4. report
    mode = "apply" if apply else "dryrun"
    if plan or not apply:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        report = LOGS_DIR / f"organize-{stamp}-{mode}.csv"
        with open(report, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["action", "author", "title", "format", "source", "destination", "note"])
            order = {"error": 0, "convert": 1, "copy": 2, "skip-duplicate": 3, "skip-identical": 4, "skip-part": 5,
                     "skip-pdf": 6}
            for c in sorted(plan, key=lambda c: (order.get(c["action"], 9), str(c.get("dest") or c["path"]))):
                w.writerow([c["action"], c["author"] or "", c["title"] or "", c["ext"], str(c["path"]),
                            str(c.get("dest") or ""), c.get("note", "")])
        log(f"Report: {report}")
    unsorted = sum(1 for c in plan if c.get("dest") and UNSORTED in Path(c["dest"]).parts)
    textbooks = sum(1 for c in plan if c.get("dest") and TEXTBOOKS_DIR in Path(c["dest"]).parents)
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "nothing new"
    log(f"{'Done' if apply else 'Dry run (nothing changed)'} - {summary}"
        + (f"; {textbooks} textbooks go to {TEXTBOOKS_DIR.name}" if textbooks else "")
        + (f"; {unsorted} go to {UNSORTED} (no usable author/title)" if unsorted else ""))
    return counts


def best_spelling(names):
    """Most common spelling, preferring normal capitalization over all-lower/ALL-UPPER."""
    def score(n):
        mixed = n != n.lower() and n != n.upper()
        return (mixed, names.count(n), n)
    best = max(set(names), key=score)
    if best == best.lower() or best == best.upper():
        best = " ".join(w[:1].upper() + w[1:].lower() if w.isalpha() else w for w in best.split())
    return best


def dest_for(c, authors, taken):
    ext = "pdf" if c["ext"] == "pdf" else "epub"
    base = TEXTBOOKS_DIR if c.get("textbook") else LIBRARY_DIR
    if not (c["author"] and c["title"]):
        folder = base / UNSORTED
        name = safe(c["path"].stem, 150)
        m = SPRINGER_BOOK.match(c["path"].stem)
        if m:
            folder = TEXTBOOKS_DIR / SPRINGER_DIR
            words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", m.group(2).replace("_", " "))
            name = safe(f"{words} ({m.group(1)})", 150)
        cand = folder / f"{name}.{ext}"
        n = 2
        while str(cand).casefold() in taken or cand.exists():
            cand = folder / f"{name} ({n}).{ext}"
            n += 1
        taken.add(str(cand).casefold())
        return cand
    akey = norm(c["author"])
    author = authors.setdefault(akey, safe(c["author"], 80))
    title = safe(c["title"], 120)
    n = 1
    while True:
        tdir = title if n == 1 else f"{title} ({n})"
        cand = base / author / tdir / f"{title} - {author}.{ext}"
        if str(cand).casefold() not in taken and not cand.exists():
            taken.add(str(cand).casefold())
            return cand
        n += 1


def do_place(c):
    dest = Path(c["dest"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".partial-{dest.name}"
    if tmp.exists():
        tmp.unlink()  # our own leftover from an interrupted run
    if c["action"] == "copy":
        shutil.copyfile(c["path"], tmp)
    else:
        # Huge books (complete collections, big manuals) can take well over 30 min on the NAS.
        convert = ["ebook-convert", str(c["path"]), str(tmp)]
        r = subprocess.run(convert, capture_output=True, text=True, timeout=CONVERT_TIMEOUT)
        if r.returncode != 0 and "SplitError" in (r.stderr + r.stdout):
            # Calibre couldn't cut an oversized chapter into smaller files; keep it whole.
            if tmp.exists():
                tmp.unlink()
            r = subprocess.run(convert + ["--flow-size", "0"], capture_output=True, text=True,
                               timeout=CONVERT_TIMEOUT)
        if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            if tmp.exists():
                tmp.unlink()
            err = (r.stderr or r.stdout or "").strip().splitlines()
            hint = " (looks DRM-protected)" if "DRM" in (r.stderr + r.stdout) else ""
            raise RuntimeError(f"conversion failed{hint}: " + (err[-1] if err else f"exit {r.returncode}"))
    os.replace(tmp, dest)


# ---------------------------------------------------------------- seedbox pull

def glob_escape(s):
    return re.sub(r"([\\*?\[\]{}])", r"\\\1", s)


def build_filter(audio_files):
    """rclone filter: every ebook format, but skip PDFs sitting in audiobook folders
    (booklets/companion PDFs), and skip everything else (audio, images, ...)."""
    dirs = sorted({posixpath.dirname(f.rstrip("/")) for f in audio_files if f.strip()})
    lines = []
    for d in dirs:
        lines.append("- /*.pdf" if d == "" else f"- /{glob_escape(d)}/**.pdf")
    lines.append("+ *.{" + ",".join(sorted(PULL_EXTS)) + "}")
    lines.append("- *")
    return "\n".join(lines) + "\n"


def rclone(*args, **kw):
    return subprocess.run(["rclone", "--config", RCLONE_CONFIG, *args], **kw)


def pull():
    require_new_layout()
    if not Path(RCLONE_CONFIG).exists():
        sys.exit("No seedbox login saved yet. Run: sudo ./setup-seedbox.sh")
    remote = f"{RCLONE_REMOTE}:{SEEDBOX_PATH}"
    log(f"Listing audiobook folders on {remote} ...")
    r = rclone("lsf", "-R", "--files-only", "--ignore-case",
               "--include", "*.{" + ",".join(AUDIO_EXTS) + "}", remote,
               capture_output=True, text=True)
    if r.returncode != 0:
        log("Could not list the seedbox, so nothing was pulled:\n" + r.stderr.strip()[-1500:])
        return False
    filt = DATA_DIR / "pull-filter.txt"
    filt.write_text(build_filter(r.stdout.splitlines()))
    MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    rlog = LOGS_DIR / f"pull-{datetime.now():%Y%m}.log"
    log(f"Copying new ebooks from {remote} -> {MIRROR_DIR} (one-way, never deletes)")
    r = rclone("copy", remote, str(MIRROR_DIR), "--filter-from", str(filt), "--ignore-case",
               "--transfers", "4", "--checkers", "8", "--log-file", str(rlog), "--log-level", "INFO",
               "--stats", "0")
    if r.returncode != 0:
        log(f"rclone copy finished with errors (exit {r.returncode}); see {rlog}")
        return False
    log(f"Pull complete; details in {rlog}")
    return True


def setup_remote(host, user):
    password = sys.stdin.read().rstrip("\r\n")
    if not password:
        sys.exit("No password given.")
    obscured = subprocess.run(["rclone", "obscure", "-"], input=password, capture_output=True,
                              text=True, check=True).stdout.strip()
    conf = Path(RCLONE_CONFIG)
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(f"[{RCLONE_REMOTE}]\ntype = sftp\nhost = {host}\nuser = {user}\n"
                    f"pass = {obscured}\nshell_type = unix\n")
    conf.chmod(0o600)
    log(f"Saved. Testing: listing {RCLONE_REMOTE}:{SEEDBOX_PATH}")
    r = rclone("lsd", f"{RCLONE_REMOTE}:{SEEDBOX_PATH}", "--max-depth", "1",
               capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        sys.exit("Login saved but the test failed:\n" + r.stderr.strip()[-1500:])
    n = len(r.stdout.splitlines())
    log(f"Connected - found {n} folder(s) in {SEEDBOX_PATH}.")


def status():
    db = db_open()
    lib = db.execute("SELECT COUNT(*) FROM library").fetchone()[0]
    log(f"Library: {lib} file(s) in {LIBRARY_DIR} and {TEXTBOOKS_DIR}")
    for r in db.execute("SELECT COALESCE(status,'read, not yet placed') s, COUNT(*) n FROM files GROUP BY s"):
        log(f"  {r['s']}: {r['n']}")
    errs = db.execute("SELECT path, note FROM files WHERE status='error' LIMIT 20").fetchall()
    for e in errs:
        log(f"  ERROR {e['path']}: {e['note']}")


# ---------------------------------------------------------------- unpack

def unpack():
    """Extract the .rar/.zip archives in the archive (never the Library or the mirror) into
    ARCHIVE_DIR/_Unpacked/<archive path>/ so organize can file the books inside them.
    Archives are only read. Each one is extracted once (remembered in state.db); archives found
    inside archives are extracted on the next pass."""
    db = db_open()
    db.execute("""CREATE TABLE IF NOT EXISTS archives (
                    path TEXT PRIMARY KEY, size INTEGER, mtime REAL, status TEXT, note TEXT)""")
    skip = {d.resolve() for d in KEEP_DIRS}
    counts = {}
    for _ in range(3):
        done_this_pass = 0
        for dirpath, dirnames, filenames in os.walk(ARCHIVE_DIR):
            dp = Path(dirpath)
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
                                 and (dp / d).resolve() not in skip)
            for fn in sorted(filenames):
                ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
                if ext not in ARCHIVE_EXTS or fn.startswith(".") or LATER_RAR_PART.search(fn):
                    continue
                path = dp / fn
                st = path.stat()
                row = db.execute("SELECT size, mtime FROM archives WHERE path=?", (str(path),)).fetchone()
                if row and row["size"] == st.st_size and abs(row["mtime"] - st.st_mtime) < 1:
                    continue
                rel = path.relative_to(ARCHIVE_DIR)
                out = (path.parent if rel.parts[0] == UNPACKED else ARCHIVE_DIR / UNPACKED / rel.parent) / path.stem
                out.mkdir(parents=True, exist_ok=True)
                try:
                    r = subprocess.run(["unar", "-q", "-s", "-D", "-p", "", "-o", str(out), str(path)],
                                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=1800)
                    status, note = ("ok", "") if r.returncode == 0 else \
                        ("error", (r.stderr or r.stdout or f"exit {r.returncode}").strip()[-300:])
                except subprocess.TimeoutExpired:
                    status, note = "error", "timed out"
                db.execute("INSERT OR REPLACE INTO archives VALUES (?,?,?,?,?)",
                           (str(path), st.st_size, st.st_mtime, status, note))
                counts[status] = counts.get(status, 0) + 1
                done_this_pass += 1
                if done_this_pass % 50 == 0:
                    db.commit()
                    log(f"Unpacked {done_this_pass} archive(s)...")
        db.commit()
        if not done_this_pass:
            break
    for r in db.execute("SELECT path, note FROM archives WHERE status='error' LIMIT 20"):
        log(f"  ERROR {r['path']}: {r['note']}")
    log(f"Unpack done - {', '.join(f'{k}: {v}' for k, v in sorted(counts.items())) or 'nothing new'}; "
        f"files are in {ARCHIVE_DIR / UNPACKED}. Next: organize, then organize --apply.")


# ---------------------------------------------------------------- cleanup

REMOVED_DIR = Path(os.environ.get("REMOVED_DIR") or ARCHIVE_DIR.parent / "_5_Books_removed")
CHAPTERS_DIR = Path(os.environ.get("CHAPTERS_DIR") or ARCHIVE_DIR.parent / "Springer Chapters")
UNCONVERTED = "_Unconverted"
SPRINGER_CHAPTER = re.compile(r"_Chapter_")


def cleanup():
    """Plan the archive cleanup: after it, 5_Books holds only EPUB Archive, Textbooks and Manga.

    Nothing is moved here. This writes a plan for cleanup-apply.sh, which moves files on the
    NAS itself (same volume, so instant). Every file goes somewhere; none is deleted.
      placed in the Library, identical or duplicate -> REMOVED_DIR/duplicates
      Springer chapters                             -> CHAPTERS_DIR (kept, outside the Library)
      Springer front/back matter, reference entries -> REMOVED_DIR/springer-fragments
      PDFs that aren't textbooks                    -> REMOVED_DIR/non-textbook-pdfs
      failed to convert (usually DRM)               -> Library/_Unconverted (the only copy)
      Library copy missing, or the kept copy failed -> REMOVED_DIR/_review
      anything else left (non-ebooks, folders)      -> REMOVED_DIR/_leftovers (by the apply script)
    Manga comes in through `manga`, not here.
    """
    require_new_layout()
    db = db_open()
    rows = {r["path"]: r for r in db.execute("SELECT path, size, mtime, status, dest, note FROM files")}
    plan, unprocessed, unsafe = [], [], []
    for path, ext in walk_books(ARCHIVE_DIR, KEEP_DIRS):
        r = rows.get(str(path))
        st = path.stat()
        if (r is None or not r["status"] or r["size"] != st.st_size
                or abs(r["mtime"] - st.st_mtime) >= 1):
            unprocessed.append(path)
            continue
        if any(ch in str(path) for ch in "\t\n\r"):
            unsafe.append(path)
            continue
        rel = path.relative_to(ARCHIVE_DIR)
        status = r["status"]
        if status in ("copy", "convert"):
            if r["dest"] and Path(r["dest"]).exists():
                plan.append(("duplicate", path, REMOVED_DIR / "duplicates" / rel, f"in Library: {r['dest']}"))
            else:
                plan.append(("review", path, REMOVED_DIR / "_review" / rel, "its Library copy is missing"))
        elif status in ("skip-identical", "skip-duplicate"):
            # "same book as X": if X failed to convert, this may be the only readable copy
            kept = (r["note"] or "")[len("same book as "):] if (r["note"] or "").startswith("same book as ") else ""
            if kept and rows.get(kept) is not None and rows[kept]["status"] == "error":
                plan.append(("review", path, REMOVED_DIR / "_review" / rel, f"the copy chosen instead failed: {kept}"))
            else:
                plan.append(("duplicate", path, REMOVED_DIR / "duplicates" / rel, status))
        elif status == "skip-part":
            if SPRINGER_CHAPTER.search(path.name):
                plan.append(("chapter", path, CHAPTERS_DIR / rel, "Springer chapter, kept"))
            else:
                plan.append(("fragment", path, REMOVED_DIR / "springer-fragments" / rel, "Springer front/back matter"))
        elif status == "skip-pdf":
            plan.append(("pdf", path, REMOVED_DIR / "non-textbook-pdfs" / rel, "PDF that isn't a textbook"))
        elif status == "error":
            plan.append(("unconverted", path, LIBRARY_DIR / UNCONVERTED / rel, "failed to convert; only copy"))
        else:
            unprocessed.append(path)

    planned = {p for _, p, _, _ in plan} | set(unprocessed) | set(unsafe)
    leftovers = {}
    for dirpath, dirnames, filenames in os.walk(ARCHIVE_DIR):
        dp = Path(dirpath)
        dirnames[:] = [d for d in dirnames if (dp / d).resolve() not in {k.resolve() for k in KEEP_DIRS}
                       and d not in SKIP_DIRS]
        for fn in filenames:
            p = dp / fn
            if p not in planned:
                ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else "(none)"
                n, size = leftovers.get(ext, (0, 0))
                leftovers[ext] = (n + 1, size + p.stat().st_size)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    report = LOGS_DIR / f"cleanup-{stamp}.csv"
    with open(report, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["action", "from", "to", "note"])
        for p in unprocessed:
            w.writerow(["NOT PROCESSED", str(p), "", "run organize --apply first"])
        for p in unsafe:
            w.writerow(["SKIPPED", str(p), "", "tab or newline in name; move it by hand"])
        for action, src, dest, note in sorted(plan, key=lambda x: (x[0], str(x[1]))):
            w.writerow([action, str(src), str(dest), note])
        for ext, (n, size) in sorted(leftovers.items(), key=lambda x: -x[1][1]):
            w.writerow(["leftover", f"{n} .{ext} file(s)", str(REMOVED_DIR / "_leftovers"),
                        f"{size / 1e6:.1f} MB, not ebooks or skipped folders"])
    log(f"Report: {report}")

    counts = {}
    for action, src, _, _ in plan:
        n, size = counts.get(action, (0, 0))
        counts[action] = (n + 1, size + src.stat().st_size)
    for action in ("duplicate", "fragment", "pdf", "chapter", "unconverted", "review"):
        n, size = counts.get(action, (0, 0))
        log(f"  {action}: {n} file(s), {size / 1e9:.2f} GB")
    n = sum(v[0] for v in leftovers.values())
    log(f"  leftover (non-ebooks etc.): {n} file(s), {sum(v[1] for v in leftovers.values()) / 1e9:.2f} GB")
    if unprocessed:
        log(f"STOP: {len(unprocessed)} ebook file(s) haven't been processed by organize --apply yet "
            f"(listed in the report). Run organize --apply, then cleanup again. No plan written.")
        return
    if unsafe:
        log(f"Note: {len(unsafe)} file(s) have a tab or newline in the name and are left in place.")

    plan_file = LOGS_DIR / f"cleanup-plan-{stamp}.tsv"
    with open(plan_file, "w", encoding="utf-8") as f:
        f.write("# books-tools cleanup plan\n")
        f.write(f"# removed_dir={REMOVED_DIR}\n")
        # afterwards, everything else in 5_Books except the three library folders -> _leftovers
        f.write(f"# sweep={ARCHIVE_DIR}|{REMOVED_DIR / '_leftovers'}\n")
        for d in KEEP_DIRS:
            if d.parent == ARCHIVE_DIR:
                f.write(f"# keep={d.name}\n")
        f.write(f"# owner_ref={LIBRARY_DIR}\n# fix_owner={LIBRARY_DIR / UNCONVERTED}\n")
        for action, src, dest, _ in plan:
            f.write(f"{action}\t{src}\t{dest}\n")
    log(f"Plan: {plan_file}")
    log("Nothing has been moved. Check the report, then on the NAS run:")
    log(f"  sudo /volume1/docker/books-tools/cleanup-apply.sh '{plan_file}'")


# ---------------------------------------------------------------- textbooks

def textbook_dest(path):
    """Where a Library (EPUB Archive) file goes when it's a textbook: same Author/Title path."""
    rel = path.relative_to(LIBRARY_DIR)
    if rel.parts[0] == OLD_SPRINGER_DIR:
        return TEXTBOOKS_DIR / SPRINGER_DIR / Path(*rel.parts[1:])
    if rel.parts[0] in OLD_TEXTBOOK_DIRS:
        return TEXTBOOKS_DIR / Path(*rel.parts[1:])
    return TEXTBOOKS_DIR / rel


def prune_empty(d, root):
    """Remove now-empty folders from d up to (not including) root."""
    while d != root and root in d.parents:
        try:
            names = [n for n in os.listdir(d) if n != "@eaDir"]
            if names:
                return
            if os.path.isdir(d / "@eaDir"):
                shutil.rmtree(d / "@eaDir")   # Synology thumbnails, only of files already moved
            d.rmdir()
        except OSError:
            return
        d = d.parent


def textbooks(plan_file=None):
    """Sort what's already in the EPUB Archive. Dry run: write a plan to review (and edit).
      textbook (PDF or EPUB)  -> moved to Textbooks/<same Author/Title path>
      PDF, not a textbook     -> deleted (its original stays in the archive;
                                 cleanup then holds it in REMOVED_DIR/non-textbook-pdfs)
    """
    require_new_layout()
    db = db_open()
    if plan_file:
        return textbooks_apply(db, Path(plan_file))
    source_of = {r["path"]: r["source"] for r in db.execute("SELECT path, source FROM library")}
    plan = []
    n = 0
    for path, ext in walk_books(LIBRARY_DIR, [LIBRARY_DIR / UNCONVERTED]):
        rel = path.relative_to(LIBRARY_DIR)
        src = source_of.get(str(path))
        src = src if src and src != "existing" else None
        title = rel.parts[-2] if len(rel.parts) >= 3 else path.stem
        reason = textbook_reason(path, ext, title, source=src)
        if not reason:
            pub, tags = extra_meta(path, ext)
            reason = textbook_reason(path, ext, title, pub, tags)
        if reason:
            plan.append(("textbook", path, reason))
        elif ext == "pdf":
            plan.append(("remove", path, "PDF, not a textbook"))
        n += 1
        if n % 500 == 0:
            log(f"Checked {n} file(s)...")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    out = LOGS_DIR / f"textbooks-plan-{stamp}.tsv"
    with open(out, "w", encoding="utf-8") as f:
        f.write("# books-tools textbooks plan\n")
        f.write("# action<TAB>file<TAB>why. Edit before applying: delete a line to leave that file alone,\n")
        f.write("# or change remove <-> textbook. textbook = move to Textbooks; remove = delete (PDFs only).\n")
        for action, path, reason in sorted(plan, key=lambda x: (x[0], str(x[1]))):
            f.write(f"{action}\t{path}\t{reason}\n")
    tb = [p for a, p, _ in plan if a == "textbook"]
    log(f"Checked {n} file(s): {len(tb)} textbook(s) to move to {TEXTBOOKS_DIR.name} "
        f"({sum(1 for p in tb if p.suffix.lower() == '.epub')} EPUB, "
        f"{sum(1 for p in tb if p.suffix.lower() == '.pdf')} PDF); "
        f"{sum(1 for a, _, _ in plan if a == 'remove')} non-textbook PDF(s) to delete")
    log(f"Plan: {out}")
    log("Nothing changed. Check the plan (edit it if needed), then run:")
    log(f"  sudo /volume1/docker/books-tools/books.sh textbooks --apply '{out}'")


def textbooks_gone(db, action, path):
    """A plan line whose file is no longer there (deleted elsewhere, or a second run)."""
    if action == "remove":
        db.execute("DELETE FROM library WHERE path=?", (str(path),))
        db.execute("""UPDATE files SET status='skip-pdf', dest='',
                      note='PDF that isn''t a textbook - removed from the Library'
                      WHERE dest=? AND status IN ('copy','convert')""", (str(path),))
        return "gone: already deleted (recorded)"
    if action != "textbook":
        return "skipped: file is gone"
    dest = textbook_dest(path)
    if dest.exists():
        return "skipped: already moved"
    # Put it back from its original in the archive (still there until cleanup moves it).
    row = db.execute("SELECT path, status FROM files WHERE dest=? AND status IN ('copy','convert')",
                     (str(path),)).fetchone()
    src = Path(row["path"]) if row else None
    if not src or not src.exists():
        return "skipped: file is gone and its original wasn't found"
    if row["status"] != "copy" or src.suffix.lower() != path.suffix.lower():
        return f"skipped: file is gone; original needs converting: {src}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".partial-{dest.name}"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)
    db.execute("UPDATE OR REPLACE library SET path=? WHERE path=?", (str(dest), str(path)))
    db.execute("UPDATE files SET dest=? WHERE dest=?", (str(dest), str(path)))
    return f"restored to {dest} from {src}"


def textbooks_apply(db, plan_file):
    with open(plan_file, encoding="utf-8") as f:
        lines = f.read().splitlines()
    if not lines or lines[0] != "# books-tools textbooks plan":
        sys.exit(f"Not a textbooks plan: {plan_file}")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    done_log = LOGS_DIR / f"textbooks-{stamp}-done.tsv"
    counts = {}
    with open(done_log, "w", encoding="utf-8") as done:
        for line in lines:
            if not line.strip() or line.startswith("#"):
                continue
            action, _, rest = line.partition("\t")
            path = Path(rest.partition("\t")[0])
            action = action.strip()
            # plans written before migrate.sh name Library/..., which is EPUB Archive/... now
            if OLD_LIBRARY_DIR == path or OLD_LIBRARY_DIR in path.parents:
                path = LIBRARY_DIR / path.relative_to(OLD_LIBRARY_DIR)
            try:
                rel = path.relative_to(LIBRARY_DIR)
            except ValueError:
                rel = None
            if rel is None or not rel.parts or rel.parts[0] == UNCONVERTED:
                result = "skipped: not an EPUB Archive file to sort"
            elif not path.exists():
                result = textbooks_gone(db, action, path)
            elif action == "textbook":
                dest = textbook_dest(path)
                if dest.exists():
                    result = f"skipped: {dest} already exists"
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.rename(path, dest)
                    db.execute("UPDATE OR REPLACE library SET path=? WHERE path=?", (str(dest), str(path)))
                    db.execute("UPDATE files SET dest=? WHERE dest=?", (str(dest), str(path)))
                    prune_empty(path.parent, LIBRARY_DIR)
                    result = f"moved to {dest}"
            elif action == "remove":
                if path.suffix.lower() != ".pdf":
                    result = "skipped: only PDFs are deleted"
                else:
                    path.unlink()
                    db.execute("DELETE FROM library WHERE path=?", (str(path),))
                    db.execute("""UPDATE files SET status='skip-pdf', dest='',
                                  note='PDF that isn''t a textbook - removed from the Library'
                                  WHERE dest=? AND status IN ('copy','convert')""", (str(path),))
                    prune_empty(path.parent, LIBRARY_DIR)
                    result = "deleted"
            else:
                result = f"skipped: unknown action '{action}'"
            key = result.split(":")[0].split(" ")[0]
            counts[key] = counts.get(key, 0) + 1
            done.write(f"{action}\t{path}\t{result}\n")
            if sum(counts.values()) % 250 == 0:
                db.commit()
    db.commit()
    for name in OLD_TEXTBOOK_DIRS:
        if (LIBRARY_DIR / name).is_dir():
            for dp, _, _ in sorted(os.walk(LIBRARY_DIR / name), key=lambda x: -len(x[0])):
                prune_empty(Path(dp), LIBRARY_DIR)
    log("Done - " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    log(f"Log: {done_log}")


# ---------------------------------------------------------------- layout migration

def migrate_db():
    """After migrate.sh moved Library -> EPUB Archive and the mirror out of 5_Books:
    rewrite the paths state.db remembers. Safe to run again."""
    if OLD_LIBRARY_DIR.exists() or not LIBRARY_DIR.exists():
        sys.exit(f"STOP: expected {LIBRARY_DIR} and no {OLD_LIBRARY_DIR}; run migrate.sh, not this.")
    db = db_open()
    for old, new in ((OLD_LIBRARY_DIR, LIBRARY_DIR), (OLD_MIRROR_DIR, MIRROR_DIR)):
        o, n = str(old) + "/", str(new) + "/"
        total = 0
        for table, col in (("files", "path"), ("files", "dest"), ("library", "path"), ("library", "source")):
            total += db.execute(f"UPDATE {table} SET {col} = ? || substr({col}, ?) WHERE substr({col}, 1, ?) = ?",
                                (n, len(o) + 1, len(o), o)).rowcount
        db.execute("UPDATE files SET note = replace(note, ?, ?) WHERE instr(note, ?) > 0", (o, n, o))
        log(f"{old} -> {new}: {total} remembered path(s) updated")
    db.commit()


# ---------------------------------------------------------------- manga

COMIC_EXTS = {"cbz", "cbr", "cb7", "cbt"}
PACKED_EXTS = {"zip": "cbz", "rar": "cbr", "7z": "cb7"}   # image-only archives get the comic extension
MANGA_EXTS = COMIC_EXTS | set(PACKED_EXTS) | {"pdf", "epub"}
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "avif", "bmp"}
MANGA_REMOVED_DIR = Path(os.environ.get("MANGA_REMOVED_DIR") or ARCHIVE_DIR.parent / "_Manga_removed")
CHECK_DIR = "_To check"
VOL_RE = re.compile(r"(?:^|[\s.\-])(?:v|vol\.?|volume|tome)\s*(\d{1,4}(?:\.\d+)?)(?!\d)", re.I)
CH_RE = re.compile(r"(?:^|[\s.\-])(?:c|ch\.?|chap\.?|chapter)\s*(\d{1,4}(?:\.\d+)?)(?!\d)", re.I)
TRAILING_NUM_RE = re.compile(r"^(.*?\D)\s*#?(\d{1,4}(?:\.\d+)?)$")


def manga_sources():
    return [Path(p) for p in os.environ.get("MANGA_SOURCES", "").split(":") if p]


def parse_manga_name(stem):
    """'Berserk v36 (2012) (Digital) (danke-Empire) - Unknown' -> ('Berserk', 'v', '36')."""
    s = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", stem).replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip(" -.")
    for kind, rx in (("v", VOL_RE), ("c", CH_RE)):
        m = rx.search(s)
        if m:
            return s[:m.start()].strip(" -.,#") or None, kind, m.group(1)
    first = s.split(" - ")[0].strip()
    m = TRAILING_NUM_RE.match(first)
    if m:
        return m.group(1).strip(" -.,#") or None, "v", m.group(2)
    return first or None, None, None


def comicinfo(path):
    """Series and number from ComicInfo.xml inside a CBZ."""
    try:
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.lower().rsplit("/", 1)[-1] == "comicinfo.xml"), None)
            if not name:
                return None, None
            x = ET.fromstring(z.read(name))
    except Exception:
        return None, None
    series = (x.findtext("Series") or "").strip() or None
    num = (x.findtext("Number") or "").strip()
    vol = (x.findtext("Volume") or "").strip()
    if not num and vol.isdigit() and int(vol) < 1000:   # western comics put the year in Volume
        num = vol
    return series, (num if re.fullmatch(r"\d{1,4}(?:\.\d+)?", num) else None)


def opf_series(opf_bytes):
    """calibre:series / series_index (calibre's metadata.opf, or an EPUB's own OPF)."""
    try:
        md = ET.fromstring(opf_bytes).find("{*}metadata")
    except Exception:
        return None, None
    if md is None:
        return None, None
    meta = {m.get("name"): m.get("content") for m in md.findall(".//{*}meta") if m.get("name")}
    series = (meta.get("calibre:series") or "").strip() or None
    idx = (meta.get("calibre:series_index") or "").strip()
    if not series:
        for m in md.findall(".//{*}meta"):
            if m.get("property") == "belongs-to-collection" and (m.text or "").strip():
                series = m.text.strip()
                break
    try:
        idx = f"{float(idx):g}" if idx else None
    except ValueError:
        idx = None
    return series, idx


def archive_is_images(path, ext):
    """True if a .zip/.rar/.7z holds only page images (so it's really a CBZ/CBR/CB7)."""
    try:
        if ext == "zip":
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if not n.endswith("/")]
        else:
            r = subprocess.run(["lsar", "-j", str(path)], capture_output=True, text=True, timeout=300)
            names = [e.get("XADFileName", "") for e in json.loads(r.stdout).get("lsarContents", [])
                     if not e.get("XADIsDirectory")]
    except Exception:
        return False
    pages = [n for n in names if n.rsplit(".", 1)[-1].lower() in IMAGE_EXTS]
    others = [n for n in names if n.rsplit(".", 1)[-1].lower() not in IMAGE_EXTS
              and n.rsplit("/", 1)[-1].lower() not in ("comicinfo.xml", "thumbs.db", ".ds_store")
              and n.rsplit(".", 1)[-1].lower() not in ("txt", "nfo", "xml", "url")]
    return bool(pages) and not others


def manga_info(path, ext):
    """(series, kind, number, where the series came from) for one file."""
    series = num = None
    how = ""
    if ext == "cbz" or ext == "zip":
        series, num = comicinfo(path)
        how = "ComicInfo" if series else ""
    if not series:
        opf = path.parent / "metadata.opf"   # calibre library folder: one book per folder
        try:
            if opf.exists() and sum(1 for p in path.parent.iterdir()
                                    if p.suffix.lower().lstrip(".") in MANGA_EXTS) == 1:
                series, idx = opf_series(opf.read_bytes())
                num = num or idx
                how = "calibre metadata" if series else ""
            elif ext == "epub":
                with zipfile.ZipFile(path) as z:
                    root = ET.fromstring(z.read("META-INF/container.xml")).find(".//{*}rootfile").get("full-path")
                    series, idx = opf_series(z.read(root))
                num = num or idx
                how = "EPUB metadata" if series else ""
        except Exception:
            pass
    fs, kind, fnum = parse_manga_name(path.stem)
    if not series:
        series, how = fs, "file name"
    if num is None:
        num = fnum
    else:
        kind = kind if fnum and float(fnum) == float(num) else "v"
    return clean_text(series) if series else None, (kind or "v") if num else None, num, how


def manga_label(kind, num):
    f = float(num)
    if f.is_integer():
        return f"{kind}{int(f):0{3 if kind == 'c' or f >= 100 else 2}d}"
    whole, frac = f"{f:g}".split(".")
    return f"{kind}{int(whole):0{3 if kind == 'c' else 2}d}.{frac}"


def manga():
    """Plan moving everything in the manga source folders (Archive/Comics, Archive/6_Manga) into
    5_Books/Manga/<Series>/<Series> v01.cbz without duplicates. Nothing is moved here; the plan
    is carried out on the NAS by cleanup-apply.sh (moves only, same volume).
      a volume/chapter -> Manga/<Series>/<Series> v01.<ext>   (one copy: comic archive over
                          PDF/EPUB, then the larger file; the rest -> MANGA_REMOVED_DIR/duplicates)
      no number        -> Manga/<Series>/<name>.<ext>        (identical copies held back)
      no series        -> Manga/_Unsorted/<name>
      odd archives, folders of loose page images -> Manga/_To check/
      everything else left in the sources (covers, metadata.opf, empty folders)
                       -> MANGA_REMOVED_DIR/_leftovers/<source>, then the source folder goes
    """
    sources = [d for d in manga_sources() if d.is_dir()]
    if not sources:
        sys.exit("No manga source folders found (MANGA_SOURCES). Run it through books.sh.")
    items = []
    unsure = []          # (path, dest-under-CHECK_DIR, note)
    for root, existing in [(d, False) for d in sources] + [(MANGA_DIR, True)]:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dp = Path(dirpath)
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            exts = [fn.rsplit(".", 1)[-1].lower() if "." in fn else "" for fn in filenames]
            if not existing and dp != root and not any(e in MANGA_EXTS for e in exts) \
                    and sum(e in IMAGE_EXTS for e in exts) >= 3 and not dirnames:
                unsure.append((dp, dp.relative_to(root), "folder of loose page images"))
                continue
            for fn, ext in zip(sorted(filenames), [fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
                                                   for fn in sorted(filenames)]):
                if fn.startswith(".") or ext not in MANGA_EXTS:
                    continue
                path = dp / fn
                if existing and CHECK_DIR in path.relative_to(root).parts[:1]:
                    continue
                out_ext = ext
                if ext in PACKED_EXTS:
                    if existing or not archive_is_images(path, ext):
                        if not existing:
                            unsure.append((path, path.relative_to(root), f".{ext} that isn't just page images"))
                        continue
                    out_ext = PACKED_EXTS[ext]
                series, kind, num, how = manga_info(path, ext)
                if not series and dp != root:
                    # "20th Century Boys/Volume 01.cbz": the folder is the series
                    folder = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", dp.name).strip(" -._")
                    if folder:
                        series, how = clean_text(folder), "folder name"
                items.append(dict(path=path, ext=out_ext, size=path.stat().st_size, series=series,
                                  kind=kind, num=num, how=how, existing=existing, root=root))
        log(f"Read {root}")

    # one spelling per series ("Berserk", not "berserk")
    names = {}
    for it in items:
        if it["series"]:
            names.setdefault(norm(it["series"]) or it["series"].casefold(), []).append(it["series"])
    display = {k: safe(best_spelling(v), 120) for k, v in names.items()}

    # identical files: only files of equal size can be identical, so only those are hashed
    by_size = {}
    for it in items:
        by_size.setdefault(it["size"], []).append(it)
    for same in by_size.values():
        if len(same) > 1:
            for it in same:
                it["sha"] = sha256(it["path"])

    groups = {}
    for it in items:
        skey = norm(it["series"]) or (it["series"] or "").casefold() if it["series"] else None
        if skey and it["num"]:
            key = (skey, it["kind"], float(it["num"]))
        else:
            key = ("one", skey, it.get("sha") or str(it["path"]))   # no number: only identical files merge
        it["skey"] = skey
        groups.setdefault(key, []).append(it)

    plan = []        # (action, src, dest, series, label, note)
    taken = {str(p).casefold() for p in (MANGA_DIR.rglob("*") if MANGA_DIR.exists() else [])}
    for key, group in sorted(groups.items(), key=lambda kv: str(kv[0])):
        # comic archive beats PDF/EPUB, then the larger file; on a tie keep what's already in Manga
        group.sort(key=lambda it: (it["ext"] not in COMIC_EXTS, -it["size"], not it["existing"]))
        best = group[0]
        for it in group[1:]:
            ident = it.get("sha") and it.get("sha") == best.get("sha")
            why = "identical to" if ident else f"other copy ({it['size'] / 1e6:.0f} MB) of"
            rel = it["path"].relative_to(it["root"])
            dest = MANGA_REMOVED_DIR / "duplicates" / (MANGA_DIR.name if it["existing"] else it["root"].name) / rel
            plan.append(("duplicate", it["path"], dest, best["series"] or "", "", f"{why} {best['path']}"))
        if best["existing"]:
            continue
        if not best["skey"]:
            folder, name = MANGA_DIR / UNSORTED, safe(best["path"].stem, 150)
        else:
            folder = MANGA_DIR / display[best["skey"]]
            name = (f"{display[best['skey']]} {manga_label(best['kind'], best['num'])}" if best["num"]
                    else safe(re.sub(r"\s+", " ", re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", best["path"].stem))
                              .strip(" -.") or best["path"].stem, 150))
        dest = folder / f"{name}.{best['ext']}"
        n = 2
        while str(dest).casefold() in taken:
            dest = folder / f"{name} ({n}).{best['ext']}"
            n += 1
        taken.add(str(dest).casefold())
        label = manga_label(best["kind"], best["num"]) if best["num"] else ""
        plan.append(("move", best["path"], dest, display.get(best["skey"], ""), label,
                     f"series from {best['how']}" + (f"; .{best['path'].suffix[1:]} is a page archive"
                                                     if best["ext"] != best["path"].suffix[1:].lower() else "")))
    for path, rel, note in unsure:
        plan.append(("check", path, MANGA_DIR / CHECK_DIR / rel, "", "", note))

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    report = LOGS_DIR / f"manga-{stamp}.csv"
    with open(report, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["action", "series", "volume", "from", "to", "note"])
        for a, src, dest, series, label, note in sorted(plan, key=lambda x: (x[0] != "move", x[3].casefold(), x[4], str(x[1]))):
            w.writerow([a, series, label, str(src), str(dest), note])
    plan_file = LOGS_DIR / f"manga-plan-{stamp}.tsv"
    with open(plan_file, "w", encoding="utf-8") as f:
        f.write("# books-tools manga plan\n")
        f.write(f"# removed_dir={MANGA_REMOVED_DIR}\n")
        for d in sources:
            f.write(f"# sweep={d}|{MANGA_REMOVED_DIR / '_leftovers' / d.name}|rmdir\n")
        f.write(f"# owner_ref={ARCHIVE_DIR}\n# fix_owner={MANGA_DIR}\n")
        for a, src, dest, *_ in plan:
            f.write(f"{a}\t{src}\t{dest}\n")
    counts = {}
    for a, src, *_ in plan:
        n, size = counts.get(a, (0, 0))
        counts[a] = (n + 1, size + (src.stat().st_size if src.is_file() else 0))
    series_n = len({x[3] for x in plan if x[0] == "move" and x[3]})
    log(f"Report: {report}")
    for a, label in (("move", "to Manga"), ("duplicate", "duplicates held back"), ("check", "to Manga/_To check")):
        n, size = counts.get(a, (0, 0))
        log(f"  {label}: {n} ({size / 1e9:.1f} GB)")
    log(f"  {series_n} series; {sum(1 for x in plan if x[0] == 'move' and not x[3])} without a series go to "
        f"Manga/{UNSORTED}")
    log(f"Plan: {plan_file}")
    log("Nothing has been moved. Check the report, then on the NAS run:")
    log(f"  sudo /volume1/docker/books-tools/cleanup-apply.sh '{plan_file}'")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pull")
    o = sub.add_parser("organize")
    o.add_argument("--apply", action="store_true", help="actually build the Library (default: dry run)")
    o.add_argument("--retry-errors", action="store_true", help="try failed conversions again")
    sub.add_parser("run")
    sub.add_parser("status")
    sub.add_parser("cleanup")
    sub.add_parser("migrate-db")
    sub.add_parser("manga")
    t = sub.add_parser("textbooks")
    t.add_argument("--apply", metavar="PLAN", help="carry out a reviewed textbooks plan")
    sub.add_parser("unpack")
    s = sub.add_parser("setup-remote")
    s.add_argument("host")
    s.add_argument("user")
    rc = sub.add_parser("rclone")
    rc.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args()

    if a.cmd == "rclone":
        os.execvp("rclone", ["rclone", "--config", RCLONE_CONFIG, *a.args])
    if a.cmd == "setup-remote":
        return setup_remote(a.host, a.user)
    if a.cmd == "status":
        return status()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(DATA_DIR / "books.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("Another books-tools run is still going; skipping this one.")
        return
    if a.cmd == "pull":
        pull()
    elif a.cmd == "organize":
        organize(a.apply, a.retry_errors)
    elif a.cmd == "cleanup":
        cleanup()
    elif a.cmd == "textbooks":
        textbooks(a.apply)
    elif a.cmd == "migrate-db":
        migrate_db()
    elif a.cmd == "manga":
        manga()
    elif a.cmd == "unpack":
        unpack()
    elif a.cmd == "run":
        pull()
        organize(True)


if __name__ == "__main__":
    main()
