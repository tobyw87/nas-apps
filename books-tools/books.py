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
  manga-tag [--apply]      write Series/Number (from Manga/<Series>/ and vNN) into each CBZ's
                           ComicInfo.xml so BookOrbit can group series
  cbr2cbz [--apply]        repack Manga's .cbr files as .cbz (BookOrbit runs out of memory on big
                           CBRs); originals listed in a plan for cleanup-apply.sh to move out
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
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
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
TEXT_TIMEOUT = int(os.environ.get("TEXT_TIMEOUT", "900"))        # seconds per RTF/TXT book (native try, then fallback)
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


# ---------------------------------------------------------------- text fallback for RTF/TXT

RTF_TOKEN = re.compile(r"\\([a-z]{1,32})(-?\d{1,10})?[ ]?|\\'([0-9a-f]{2})|\\([^a-z])|([{}])|[\r\n]+|(.)", re.I | re.S)
RTF_SKIP = {"fonttbl", "colortbl", "datastore", "themedata", "stylesheet", "info", "pict", "object",
            "xmlnstbl", "listtable", "listoverridetable", "rsidtbl", "generator", "latentstyles",
            "header", "headerl", "headerr", "headerf", "footer", "footerl", "footerr", "footerf",
            "fldinst", "private", "revtbl", "filetbl", "pgptbl", "mmathPr", "defchp", "defpap",
            "operator", "author", "title", "subject", "keywords", "comment", "company", "category"}
RTF_CHARS = {"par": "\n\n", "sect": "\n\n", "line": "\n", "tab": "\t", "emdash": "\u2014", "endash": "\u2013",
             "lquote": "\u2018", "rquote": "\u2019", "ldblquote": "\u201c", "rdblquote": "\u201d",
             "bullet": "\u2022", "emspace": " ", "enspace": " ", "qmspace": " "}


def rtf_to_text(data):
    """Plain text from an RTF that Calibre's reader rejects.

    Layout is lost (that's the price), but the words, paragraphs, accents and quotes survive.
    Tolerant on purpose: broken groups, odd code pages and stray bytes are skipped, not fatal.
    """
    raw = data.decode("latin-1")
    m = re.search(r"\\ansicpg(\d+)", raw)
    codepage = f"cp{m.group(1)}" if m else "cp1252"
    try:
        "x".encode(codepage)
    except LookupError:
        codepage = "cp1252"
    out, stack, skip_chars, pending = [], [], 0, bytearray()
    ignore, ucskip, started = False, 1, False

    def flush():
        if pending:
            out.append(pending.decode(codepage, errors="replace"))
            pending.clear()

    for m in RTF_TOKEN.finditer(raw):
        word, num, hexa, sym, brace, ch = m.groups()
        if hexa is not None:
            if skip_chars:
                skip_chars -= 1
            elif not ignore:
                pending.append(int(hexa, 16))
            continue
        flush()
        if brace == "{":
            stack.append((ignore, ucskip))
            continue
        if brace == "}":
            if stack:
                ignore, ucskip = stack.pop()
            continue
        if word is not None:
            started = True
            if word in RTF_SKIP:
                ignore = True
            elif word == "uc":
                ucskip = int(num or 1)
            elif word == "u" and num is not None and not ignore:
                n = int(num)
                code = n + 65536 if n < 0 else n
                if not 0xF000 <= code <= 0xF8FF:       # symbol-font private-use codes are noise as text
                    out.append(chr(code))
                skip_chars = ucskip
            elif word in RTF_CHARS and not ignore:
                out.append(RTF_CHARS[word])
            elif word == "bin" and num:
                pass
            continue
        if sym is not None:
            if sym == "*":
                ignore = True
            elif sym in "\\{}" and not ignore:
                out.append(sym)
            elif sym == "~" and not ignore:
                out.append("\u00a0")
            elif sym == "_" and not ignore:
                out.append("\u2011")
            continue
        if ch is None:
            continue                          # bare line break in the file: not content in RTF
        if skip_chars:
            skip_chars -= 1
        elif not ignore and started:
            out.append(ch)
    flush()
    text = "".join(out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def decode_text(data):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1", errors="replace")


def text_fallback(c, tmp):
    """RTF/TXT that Calibre's own readers reject: clean UTF-8 text first, then convert that."""
    data = Path(c["path"]).read_bytes()
    text = rtf_to_text(data) if c["ext"] == "rtf" else decode_text(data)
    if len(text) < 2000:
        raise RuntimeError(f"text fallback: only {len(text)} characters of text")
    with tempfile.TemporaryDirectory(dir=str(DATA_DIR)) as td:
        src = Path(td) / "book.txt"
        src.write_text(text, encoding="utf-8")
        # "plain": the default "auto" sniffs for Markdown/Textile and crawls on texts full of
        # "* * *" scene breaks and underscores (20+ minutes on one book on the NAS).
        cmd = ["ebook-convert", str(src), str(tmp), "--input-encoding", "utf-8",
               "--paragraph-type", "block", "--formatting-type", "plain", "--chapter-mark", "none"]
        if c.get("title"):
            cmd += ["--title", str(c["title"])]
        if c.get("author"):
            cmd += ["--authors", str(c["author"])]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=TEXT_TIMEOUT)
        except subprocess.TimeoutExpired:
            if tmp.exists():
                tmp.unlink()
            raise RuntimeError(f"text fallback timed out after {TEXT_TIMEOUT // 60} min")
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        if tmp.exists():
            tmp.unlink()
        err = (r.stderr or r.stdout or "").strip().splitlines()
        raise RuntimeError("text fallback failed: " + (err[-1] if err else f"exit {r.returncode}"))


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
        first_timeout = TEXT_TIMEOUT if c["ext"] in TEXT_EXTS else CONVERT_TIMEOUT
        r = subprocess.run(convert, capture_output=True, text=True, timeout=first_timeout)
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
            if c["ext"] in TEXT_EXTS:
                try:
                    text_fallback(c, tmp)
                except Exception as e:
                    raise RuntimeError(f"conversion failed{hint}: " + (err[-1] if err else f"exit {r.returncode}")
                                       + f" | {str(e)[-200:]}")
            else:
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
# Books that couldn't be converted (corrupt/DRM LIT, RTFs Calibre and the text fallback both reject)
# are kept, but outside every BookOrbit library.
UNCONVERTED_DIR = Path(os.environ.get("UNCONVERTED_DIR") or ARCHIVE_DIR.parent / "Unconverted Books")
SPRINGER_CHAPTER = re.compile(r"_Chapter_")


def cleanup():
    """Plan the archive cleanup: after it, 5_Books holds only EPUB Archive, Textbooks and Manga.

    Nothing is moved here. This writes a plan for cleanup-apply.sh, which moves files on the
    NAS itself (same volume, so instant). Every file goes somewhere; none is deleted.
      placed in the Library, identical or duplicate -> REMOVED_DIR/duplicates
      Springer chapters                             -> CHAPTERS_DIR (kept, outside the Library)
      Springer front/back matter, reference entries -> REMOVED_DIR/springer-fragments
      PDFs that aren't textbooks                    -> REMOVED_DIR/non-textbook-pdfs
      failed to convert                             -> UNCONVERTED_DIR, "Unconverted Books" (the only copy)
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
            plan.append(("unconverted", path, UNCONVERTED_DIR / rel, "failed to convert; only copy"))
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
        f.write(f"# owner_ref={LIBRARY_DIR}\n# fix_owner={UNCONVERTED_DIR}\n")
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


# ---------------------------------------------------------------- cbr2cbz

CBR_TMP = ARCHIVE_DIR / ".cbr2cbz-tmp"


def rar_listing(path):
    """[(name, size)] of the files in a RAR, via lsar; None if it can't be listed."""
    r = subprocess.run(["lsar", "-j", str(path)], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=600)
    if r.returncode != 0:
        return None
    try:
        entries = json.loads(r.stdout).get("lsarContents", [])
    except json.JSONDecodeError:
        return None
    return [(e.get("XADFileName", ""), e.get("XADFileSize", 0)) for e in entries if not e.get("XADIsDirectory")]


def cbr2cbz(apply, min_mb=0):
    """Repack Manga's .cbr (RAR) files as .cbz (ZIP, stored, same images, no recompression).

    BookOrbit reads a whole CBR into memory several times over to get its cover and metadata,
    so a 900 MB CBR needs ~3-4 GB and gets it killed; CBZs are read through the ZIP index.
    Each new .cbz is written to a temp name, checked (same files, same sizes, CRCs) and then
    renamed. The .cbr originals are left where they are and listed in a plan; cleanup-apply.sh
    moves them to MANGA_REMOVED_DIR/cbr-originals (deleted only by you).
    """
    if not MANGA_DIR.is_dir():
        log(f"No Manga folder at {MANGA_DIR}")
        return
    cbrs = sorted(p for p in MANGA_DIR.rglob("*") if p.is_file() and p.suffix.lower() == ".cbr"
                  and p.stat().st_size >= min_mb * 1e6)
    total = sum(p.stat().st_size for p in cbrs)
    log(f"{len(cbrs)} .cbr file(s) in {MANGA_DIR} ({total / 1e9:.1f} GB)"
        + (f", {min_mb} MB and up" if min_mb else ""))
    if not apply:
        for p in sorted(cbrs, key=lambda p: -p.stat().st_size)[:15]:
            log(f"  {p.stat().st_size / 1e6:6.0f} MB  {p.relative_to(MANGA_DIR)}")
        log("Dry run, nothing changed. To convert: books.sh cbr2cbz --apply "
            "(needs free space for one extracted file plus the new .cbz at a time).")
        return

    plan, counts = [], {}
    for n, src in enumerate(cbrs, 1):
        dest = src.with_suffix(".cbz")
        held = MANGA_REMOVED_DIR / "cbr-originals" / src.relative_to(MANGA_DIR)
        size = src.stat().st_size
        rel = src.relative_to(MANGA_DIR)
        if zipfile.is_zipfile(src):
            # Really a ZIP with the wrong extension: renaming it is the whole fix.
            if dest.exists():
                status = "skipped: .cbz of the same name exists"
            else:
                os.rename(src, dest)
                status = "renamed (was already a ZIP)"
        else:
            listing = rar_listing(src)
            if not listing:
                status = "error: can't read the RAR"
            elif dest.exists():
                try:
                    with zipfile.ZipFile(dest) as z:
                        same = sorted(i.file_size for i in z.infolist() if not i.is_dir()) == \
                            sorted(sz for _, sz in listing)
                except zipfile.BadZipFile:
                    same = False
                if same:
                    plan.append((src, held))
                    status = "converted earlier"
                else:
                    status = "skipped: a different .cbz of the same name exists"
            elif shutil.disk_usage(MANGA_DIR).free < 2.2 * size + 1e9:
                log(f"Not enough free space for {rel} ({size / 1e9:.1f} GB); stopping here.")
                break
            else:
                status = repack(src, dest, listing)
                if status == "converted":
                    plan.append((src, held))
        counts[status.split(":")[0]] = counts.get(status.split(":")[0], 0) + 1
        log(f"[{n}/{len(cbrs)}] {status}: {rel}")
    shutil.rmtree(CBR_TMP, ignore_errors=True)

    log("Done - " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    if not plan:
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    plan_file = LOGS_DIR / f"cbr2cbz-plan-{stamp}.tsv"
    with open(plan_file, "w", encoding="utf-8") as f:
        f.write("# books-tools cbr2cbz plan\n")
        f.write(f"# removed_dir={MANGA_REMOVED_DIR}\n")
        for src, held in plan:
            f.write(f"cbr-original\t{src}\t{held}\n")
    log(f"Each .cbr now has a checked .cbz next to it. Before scanning Manga in BookOrbit, move the "
        f"{len(plan)} .cbr original(s) out (nothing is deleted):")
    log(f"  sudo /volume1/docker/books-tools/cleanup-apply.sh '{plan_file}'")


def repack(src, dest, listing):
    """Extract one RAR with unar and write its files to a stored ZIP; returns a status."""
    tmp = CBR_TMP / "x"
    shutil.rmtree(CBR_TMP, ignore_errors=True)
    tmp.mkdir(parents=True)
    part = dest.with_name(dest.name + ".part")
    try:
        r = subprocess.run(["unar", "-q", "-D", "-p", "", "-o", str(tmp), str(src)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            return "error: unar " + (r.stderr or r.stdout or f"exit {r.returncode}").strip()[-200:]
        files = sorted((p for p in tmp.rglob("*") if p.is_file()), key=lambda p: str(p.relative_to(tmp)))
        if sorted(p.stat().st_size for p in files) != sorted(sz for _, sz in listing):
            return f"error: extracted {len(files)} file(s), the RAR lists {len(listing)} (or sizes differ)"
        with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
            for p in files:
                z.write(p, p.relative_to(tmp).as_posix())
        with zipfile.ZipFile(part) as z:
            bad = z.testzip()
            if bad or len(z.infolist()) != len(files):
                return f"error: check of the new .cbz failed ({bad or 'file count'})"
        shutil.copystat(src, part)
        os.rename(part, dest)
        return "converted"
    except subprocess.TimeoutExpired:
        return "error: unar timed out"
    finally:
        part.unlink(missing_ok=True)
        shutil.rmtree(CBR_TMP, ignore_errors=True)


# ---------------------------------------------------------------- manga-tag

TAG_JOURNAL = DATA_DIR / "manga-tag-journal.json"


def comicinfo_xml(old, series, number, title):
    """ComicInfo.xml bytes with Series/Number (and Title for unnumbered books) set, other fields kept."""
    try:
        x = ET.fromstring(old) if old else None
    except ET.ParseError:
        x = None
    if x is None or x.tag != "ComicInfo":
        x = ET.Element("ComicInfo")
    for tag, val in (("Series", series), ("Number", number), ("Title", title)):
        if val is None:
            continue
        el = x.find(tag)
        if el is None:
            el = ET.SubElement(x, tag)
        el.text = val
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(x, encoding="utf-8")


def manga_tag_target(path):
    """(series, number, title) a Manga/<Series>/... CBZ should carry."""
    series = path.relative_to(MANGA_DIR).parts[0]
    _, kind, num = parse_manga_name(path.stem)
    if num:
        f = float(num)
        return series, (str(int(f)) if f.is_integer() else f"{f:g}"), None
    title = re.sub(r"^" + re.escape(series) + r"\s*-\s*", "", path.stem).strip() or path.stem
    return series, None, title


def zip_tail(path):
    """Offset of the ZIP central directory (where an append starts writing)."""
    with zipfile.ZipFile(path) as z:
        return z.start_dir


def manga_tag_undo():
    """Put back the end of a CBZ whose append was cut short (power cut, kill)."""
    if not TAG_JOURNAL.exists():
        return
    j = json.loads(TAG_JOURNAL.read_text())
    path, tail = Path(j["path"]), bytes.fromhex(j["tail"])
    with open(path, "r+b") as f:
        f.truncate(j["start"])
        f.seek(j["start"])
        f.write(tail)
    os.utime(path, ns=(j["mtime_ns"], j["mtime_ns"]))
    log(f"Restored {path} after an interrupted run.")
    TAG_JOURNAL.unlink()


def manga_tag(apply):
    """Write Series/Number into each Manga CBZ's ComicInfo.xml so BookOrbit can group series.

    BookOrbit only learns a book's series from ComicInfo.xml inside the file (folder names are
    ignored), so without it "Collapse series" does nothing. Series = the Manga/<Series> folder,
    Number = the vNN/cNNN in the file name. Pages are never touched: a new ComicInfo.xml is
    appended to the archive (only its index at the end is rewritten; the old end is journalled
    first so an interrupted run is undone on the next start). A CBZ that already has a root
    ComicInfo.xml is rewritten to a temp file, checked, then renamed over the original.
    """
    if not MANGA_DIR.is_dir():
        log(f"No Manga folder at {MANGA_DIR}")
        return
    manga_tag_undo()
    counts, samples, touched = {}, [], set()
    files = sorted(p for p in MANGA_DIR.rglob("*") if p.is_file() and len(p.relative_to(MANGA_DIR).parts) > 1
                   and not p.relative_to(MANGA_DIR).parts[0].startswith("_"))
    for path in files:
        if path.suffix.lower() != ".cbz":
            counts["not a CBZ (left alone)"] = counts.get("not a CBZ (left alone)", 0) + 1
            continue
        series, number, title = manga_tag_target(path)
        try:
            with zipfile.ZipFile(path) as z:
                name = next((n for n in z.namelist() if n.lower() == "comicinfo.xml"), None)
                old = z.read(name) if name else None
        except (zipfile.BadZipFile, OSError) as e:
            status = "error"
            log(f"  error: {path.relative_to(MANGA_DIR)}: {e}")
            counts[status] = counts.get(status, 0) + 1
            continue
        if old:
            try:
                x = ET.fromstring(old)
                cur = ((x.findtext("Series") or "").strip(), (x.findtext("Number") or "").strip())
            except ET.ParseError:
                cur = ("", "")
            if cur[0] == series and (number is None or cur[1] == number):
                counts["already tagged"] = counts.get("already tagged", 0) + 1
                continue
        status = "rewrite" if old else "append"
        if len(samples) < 8:
            samples.append(f"  {status}: {path.relative_to(MANGA_DIR)} -> Series={series!r}"
                           + (f" Number={number}" if number else f" Title={title!r}"))
        if apply:
            new = comicinfo_xml(old, series, number, title)
            try:
                (tag_rewrite if old else tag_append)(path, new)
                touched.add(path.parent)
            except Exception as e:
                status = "error"
                log(f"  error: {path.relative_to(MANGA_DIR)}: {e}")
        counts[status] = counts.get(status, 0) + 1
        done = sum(v for k, v in counts.items() if k in ("append", "rewrite"))
        if apply and done and done % 100 == 0:
            log(f"Tagged {done}...")
    # BookOrbit skips folders whose mtime hasn't changed, and an in-place append doesn't change it.
    for d in touched:
        os.utime(d)
    for line in samples:
        log(line)
    log(("Done - " if apply else "Dry run, nothing changed - ")
        + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    if not apply:
        log("To write the tags: books.sh manga-tag --apply   (then rescan Manga in BookOrbit)")


def tag_append(path, xml):
    st = path.stat()
    start = zip_tail(path)
    with open(path, "rb") as f:
        f.seek(start)
        tail = f.read()
    TAG_JOURNAL.write_text(json.dumps({"path": str(path), "start": start, "tail": tail.hex(),
                                       "mtime_ns": st.st_mtime_ns}))
    try:
        with zipfile.ZipFile(path, "a", allowZip64=True) as z:
            z.writestr(zipfile.ZipInfo("ComicInfo.xml", time.localtime()[:6]), xml)
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "ComicInfo.xml" not in names or not z.read("ComicInfo.xml") == xml:
                raise RuntimeError("ComicInfo.xml didn't read back")
            first = next((n for n in names if n != "ComicInfo.xml" and not n.endswith("/")), None)
            if first:
                with z.open(first) as f:
                    f.read(4096)
    except BaseException:
        manga_tag_undo()
        raise
    TAG_JOURNAL.unlink()


def tag_rewrite(path, xml):
    part = path.with_name(path.name + ".part")
    try:
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(part, "w", allowZip64=True) as dst:
            n = 0
            for info in src.infolist():
                if info.filename.lower() == "comicinfo.xml":
                    continue
                with src.open(info) as fi, dst.open(info, "w", force_zip64=info.file_size > 2**31) as fo:
                    shutil.copyfileobj(fi, fo, 1 << 20)
                n += 1
            dst.writestr(zipfile.ZipInfo("ComicInfo.xml", time.localtime()[:6]), xml)
            if dst.comment != src.comment:
                dst.comment = src.comment
        with zipfile.ZipFile(part) as z:
            if len(z.infolist()) != n + 1 or z.testzip() is not None:
                raise RuntimeError("check of the rewritten CBZ failed")
        shutil.copymode(path, part)
        os.rename(part, path)
    finally:
        part.unlink(missing_ok=True)


# ---------------------------------------------------------------- main

# ---------------------------------------------------------------- regroup the EPUB Archive

NAME_FORM = re.compile(r"^([^,\-]{1,40}, [^,\-]{1,40}?)(?:\s+-\s+.+)?$")   # "Cherryh, C J" (+ " - Knight 1")


AUTHOR_FIRST = re.compile(r"^(?:\(e?book\)\s*)?([A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,3})\s+-\s+(.+)$")   # "Anne McCaffrey - Pern07 Title"


NAME_PARTICLES = {"de", "van", "von", "der", "den", "la", "le", "di", "da", "du", "bin", "al", "ben", "el", "y"}


def name_ish(s):
    """Looks like (part of) a person's name: 1-4 capitalized words/initials, no digits or punctuation.
    A list of such names ("Marie Osmond; Marcia Wilkie", "Weis & Hickman") counts too."""
    parts = [x.strip() for x in re.split(r"\s*(?:;|&|\band\b|\|)\s*", s) if x.strip()]
    if len(parts) > 1:
        return all(name_ish(x) for x in parts)
    words = re.sub(r",?\s*(?:jr|sr|ii|iii|iv|phd|md)\.?$", "", s.strip(), flags=re.I).split()
    return (1 <= len(words) <= 4 and not re.search(r"[\d:;()\[\]]", s)
            and all(w[:1].isupper() or w in NAME_PARTICLES for w in words))


def regroup_unswap(author, title):
    """Old-archive books have author and title swapped in their metadata ("Cherryh, C J" is the
    title and "The Dreamstone" the author). Returns (real author, real title), or None if fine.
    Both halves of "Last, First" must look like a name, so "Wounds, Book 2" is left alone."""
    if not (author and title) or re.search(r"[;()\[\]]|,", author):
        return None
    m = NAME_FORM.match(title.strip())
    if not m:
        return None
    last, _, first = m.group(1).partition(", ")
    if not (name_ish(last) and name_ish(first)):
        return None
    if re.fullmatch(r"(?:[A-Z]\.\s?)+[A-Z][\w'-]+", author.strip()):
        return None                                  # "P. N. Elrod" is a person, so this is a real book
    new_author = fix_author(m.group(1))
    parts = [x.strip() for x in title.strip().split(" - ")]
    if len(parts) >= 3:                              # "Author - Series 01 - Title": the last part is the title
        new_title = fix_title(parts[-1])
    elif (author == author.lower() or author == author.upper()) and len(author.split()) == 2:   # "wendy kliewer": whoever scanned it
        new_title = fix_title(parts[1]) if len(parts) == 2 else None
    else:
        new_title = fix_title(author)
    if not (new_author and new_title) or norm(new_author) == norm(new_title):
        return None
    return new_author, new_title


def regroup_title(title, rel):
    """Better title from the old middle folder when the metadata one is a stub:
    "1" -> "Crystal Singer 3 - Crystal Line" (from "McCaffrey, Anne - Crystal Singer 3 - Crystal Line"),
    "A Boy" -> "A Boy And His God" (from folder "A Boy And His God")."""
    if len(rel.parts) != 3:
        return title
    mid = rel.parts[1]
    m = re.match(r"^[^,\-]{1,40}, [^,\-]{1,40}? - (.+)$", mid)
    if m and (len(title) <= 3 or title.isdigit()):
        return fix_title(m.group(1)) or title
    if (not m and " - " not in mid and "," not in mid and len(mid) > len(title)
            and norm(mid).startswith(norm(title))):
        return mid
    return title


def regroup_conforming(rel):
    """Already Author/Title/<Title - Author>.ext with a "First Last" author folder."""
    return (len(rel.parts) == 3 and fix_author(rel.parts[0]) == rel.parts[0]
            and Path(rel.parts[2]).stem.endswith(" - " + rel.parts[0]))


def regroup(check_only=False):
    """Plan moves so every file in EPUB Archive is Author/Title/Title - Author.epub.
    Author and title come from the book's own metadata, else the folder/file names. Moves nothing:
    writes a cleanup-style plan (carried out by cleanup-apply.sh) and a CSV of the ones it isn't
    sure about, which are left where they are."""
    require_new_layout()
    skip = {UNSORTED, UNCONVERTED, SPRINGER_DIR, *OLD_TEXTBOOK_DIRS}
    authors = {}
    todo = []
    subs = {}                       # top folder -> its subfolders: a real author has many, a swapped title few
    for path, ext in walk_books(LIBRARY_DIR):
        r = path.relative_to(LIBRARY_DIR)
        if len(r.parts) >= 3:
            subs.setdefault(r.parts[0], set()).add(r.parts[1])
    unswap = lambda a, t, top: None if len(subs.get(top, ())) >= 4 else regroup_unswap(a, t)
    for path, ext in walk_books(LIBRARY_DIR):
        rel = path.relative_to(LIBRARY_DIR)
        if rel.parts[0] in skip:
            continue
        ok = regroup_conforming(rel)
        if ok and ext == "epub":      # same names either way round, so ask the book who wrote it
            try:
                ra, rt = epub_meta(path)
            except Exception:
                ra = rt = None
            a = fix_author(ra)
            ok = (not a or norm(a) == norm(rel.parts[0])) and not unswap(ra, rt, rel.parts[0])
        if ok:
            authors.setdefault(norm(rel.parts[0]), rel.parts[0])
        else:
            todo.append((path, ext, rel))
    log(f"{len(todo)} file(s) are not in Author/Title/ layout; reading their metadata...")
    plan, check, taken = [], [], {}
    n = 0
    for path, ext, rel in todo:
        n += 1
        if n % 500 == 0:
            log(f"Read {n} of {len(todo)}...")
        author, title, src = read_meta(path, ext)
        why = f"metadata ({src})"
        guessed = False
        fixed = unswap(author, title, rel.parts[0])
        if fixed and fixed[0] and fixed[1]:
            author, title = fixed
            why = "metadata had author and title swapped"
        if not (author and title):
            fa, ft = filename_meta(path)
            author = author or fix_author(fa)
            title = title or fix_title(ft)
            why = "file name"
            guessed = True
        if not (author and title):
            check.append((path, "", "", "no author/title found"))
            continue
        title = regroup_title(title, rel)
        # author read from the book's own metadata is trusted; anything guessed must be in the old path
        sur = norm(author.split()[-1])
        if (guessed or src != "epub") and sur and sur not in norm(str(rel)):
            check.append((path, author, title, f"author '{author}' not in the old path"))
            continue
        adir = authors.setdefault(norm(author), safe(author, 80))
        tsafe = safe(title, 120)
        k = 1
        while True:
            tdir = tsafe if k == 1 else f"{tsafe} ({k})"
            dest = LIBRARY_DIR / adir / tdir / f"{tsafe} - {adir}.{ext}"
            other = taken.get(str(dest).casefold())
            if other is None and not dest.exists():
                break
            same = sha256(other or dest) == sha256(path)
            if same:
                dest = REMOVED_DIR / "regroup-duplicates" / rel
                break
            k += 1
        if dest == path:
            continue
        if REMOVED_DIR not in dest.parents:
            taken[str(dest).casefold()] = path
        plan.append(("duplicate" if REMOVED_DIR in dest.parents else "move", path, dest, why))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    plan_file = LOGS_DIR / f"regroup-plan-{stamp}.tsv"
    with open(plan_file, "w", encoding="utf-8") as f:
        f.write("# books-tools cleanup plan\n")
        f.write(f"# removed_dir={REMOVED_DIR}\n# prune={LIBRARY_DIR}\n")
        f.write(f"# owner_ref={TEXTBOOKS_DIR}\n# fix_owner={LIBRARY_DIR}\n")
        for action, src, dest, _ in plan:
            f.write(f"{action}\t{src}\t{dest}\n")
    report = LOGS_DIR / f"regroup-{stamp}.csv"
    with open(report, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["action", "from", "to", "why"])
        for action, src, dest, why in plan:
            w.writerow([action, src.relative_to(LIBRARY_DIR), dest, why])
        for src, a, t, why in check:
            w.writerow(["check (left alone)", src.relative_to(LIBRARY_DIR), f"{a} / {t}", why])
    moves = sum(1 for p in plan if p[0] == "move")
    log(f"Plan: {moves} move(s), {len(plan) - moves} identical duplicate(s) to hold, "
        f"{len(check)} left alone to check")
    log(f"Report: {report}")
    log(f"Plan: {plan_file}")
    log("Nothing has been moved. Look at the report, then on the NAS run:")
    log(f"  sudo /volume1/docker/books-tools/cleanup-apply.sh '{plan_file}'")


# ---------------------------------------------------------------- collect converted books

CONVERTED_DIR = Path(os.environ.get("CONVERTED_DIR") or ARCHIVE_DIR.parent / "Converted")


def converted(formats=None):
    """Plan moving every book this tool converted (MOBI/AZW/LIT/RTF... -> EPUB) out of the libraries
    into CONVERTED_DIR, keeping Author/Title/ paths. Moves nothing: writes a cleanup-style plan."""
    require_new_layout()
    db = db_open()
    moved = {}          # old path -> new path, from every earlier plan's log (oldest first)
    for f in sorted(LOGS_DIR.glob("*-moved.tsv"), key=lambda x: x.stat().st_mtime):
        for line in f.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                moved[parts[1]] = parts[2]
    want = {x.strip().lower().lstrip(".") for x in formats.split(",")} if formats else None
    plan, by_ext, missing = [], {}, 0
    for r in db.execute("SELECT path, dest FROM files WHERE status='convert' AND dest!='' ORDER BY dest"):
        ext = r["path"].rsplit(".", 1)[-1].lower()
        if want and ext not in want:
            continue
        cur = r["dest"]
        for _ in range(5):
            if cur in moved:
                cur = moved[cur]
        cur = Path(cur)
        if not cur.exists():
            missing += 1
            continue
        for root, prefix in ((LIBRARY_DIR, ""), (TEXTBOOKS_DIR, "Textbooks")):
            if root in cur.parents:
                plan.append(("converted", cur, CONVERTED_DIR / prefix / cur.relative_to(root)))
                by_ext[ext] = by_ext.get(ext, 0) + 1
                break
        else:
            missing += 1
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    plan_file = LOGS_DIR / f"converted-plan-{stamp}.tsv"
    with open(plan_file, "w", encoding="utf-8") as f:
        f.write("# books-tools cleanup plan\n")
        f.write(f"# removed_dir={REMOVED_DIR}\n# prune={LIBRARY_DIR}\n")
        f.write(f"# owner_ref={TEXTBOOKS_DIR}\n# fix_owner={CONVERTED_DIR}\n")
        for action, src, dest in plan:
            f.write(f"{action}\t{src}\t{dest}\n")
    log(f"{len(plan)} converted book(s) to move to {CONVERTED_DIR} "
        f"({', '.join(f'{k}: {v}' for k, v in sorted(by_ext.items(), key=lambda x: -x[1]))}); "
        f"{missing} not found where expected (moved or deleted since)")
    log(f"Plan: {plan_file}")
    log("Nothing has been moved. To go ahead, on the NAS run:")
    log(f"  sudo /volume1/docker/books-tools/cleanup-apply.sh '{plan_file}'")


# ---------------------------------------------------------------- fix swapped metadata in a Calibre library

CALIBRE_FIX_SCRIPT = r"""
import sys, csv, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[-4])))
import books
from calibre.library import db
lib, apply_, report = sys.argv[-3], sys.argv[-2] == "1", sys.argv[-1]
d = db(lib).new_api
ids = sorted(d.all_book_ids())
per_author = {}
rows = []
for i in ids:
    a = d.field_for("authors", i) or ()
    per_author.setdefault(books.norm(a[0]) if a else "", []).append(i)
JUNK_TITLE = [re.compile(x, re.I) for x in (r"\.(txt|doc|docx|rtf|epub|lit|pdf|mobi|html?)$", r"\s*\[(?:v?[\d.]+|uc|ocr|html|txt|lit)\]",
              r"\s*\(v\d[\d.]*\)", r"\s+e-?txt$", r"\s+v\d+(\.\d+)?$")]
def clean_title(t):
    for _ in range(2):
        for rx in JUNK_TITLE:
            t = rx.sub("", t).strip()
    return t
def fix_one(a0, t, n_books, canon_names):
    t = re.sub(r",(?=\S)", ", ", t)                         # "Williams,Tad - X" -> "Williams, Tad - X"
    many = len(per_author[books.norm(a0)]) >= 4              # a real author, or a junk name shared by many books
    if not many or len(t.split(" - ")) >= 3:                 # then only trust a title carried in the title field
        fixed = books.regroup_unswap(a0, t)
        if fixed and fixed[0] and fixed[1]:
            return fixed[0], clean_title(fixed[1])
    m = books.AUTHOR_FIRST.match(t)                          # "Anne McCaffrey - Pern07 Smallest Dragonboy"
    if m and n_books <= 3 and books.name_ish(m.group(1)):
        name = canon_names.get(books.norm(m.group(1)))
        if not name:                                         # "Anthony Piers": try the other order
            w = m.group(1).split()
            name = canon_names.get(books.norm(" ".join(w[1:] + w[:1])))
        if name:
            parts = [x.strip() for x in m.group(2).split(" - ")]
            if len(parts) >= 2:
                nt = books.fix_title(parts[-1])
            elif books.norm(a0) in books.norm(parts[0]) or re.search(r"\d$", parts[0]):   # the author field is the title
                nt = books.fix_title(a0)
            else:                                            # "Frederik Pohl - Title" with a junk author field
                nt = books.fix_title(parts[0])
            nt = clean_title(nt or "")
            if nt and books.norm(nt) != books.norm(name):
                return name, nt
    return None
canon_names = {}                                             # real authors: 3+ books
for k, v in per_author.items():
    if len(v) >= 3:
        canon_names[k] = d.field_for("authors", v[0])[0]
flagged = {}
for i in ids:
    a = d.field_for("authors", i) or ()
    t = d.field_for("title", i) or ""
    if not a:
        continue
    fixed = fix_one(a[0], t, len(per_author[books.norm(a[0])]), canon_names)
    if fixed:
        flagged[i] = (t, a[0], fixed)
canon = {}                                                   # spelling already used by the other books
for i in ids:
    if i not in flagged:
        for a in d.field_for("authors", i) or ():
            canon.setdefault(books.norm(a), {}).setdefault(a, 0)
            canon[books.norm(a)][a] += 1
def spell(a):
    if books.norm(a) in canon:
        return max(canon[books.norm(a)].items(), key=lambda x: x[1])[0]
    return re.sub(r"\b([A-Z])\b(?!\.)", r"\1.", a)            # "Sheri S Tepper" -> "Sheri S. Tepper"
for i, (t, a0, fixed) in flagged.items():
    rows.append((i, t, a0, fixed[1], spell(fixed[0])))
with open(report, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["id", "old title", "old author", "new title", "new author"])
    w.writerows(rows)
print("%d of %d book(s) have author and title swapped" % (len(rows), len(ids)))
if apply_:
    for n, (i, t, a, nt, na) in enumerate(rows, 1):
        d.set_field("title", {i: nt})
        d.set_field("authors", {i: (na,)})
        if n % 500 == 0:
            print("Fixed %d..." % n, flush=True)
    print("Fixed %d book(s)" % len(rows))
d.close()
"""


def calibre_fix(library, apply):
    """Fix books whose Calibre author and title are swapped (old-archive books). Calibre renames the
    book folders itself. Run with the Calibre container stopped (one process per library)."""
    lib = Path(library)
    if not (lib / "metadata.db").exists():
        sys.exit(f"STOP: {lib} is not a Calibre library (no metadata.db).")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    report = LOGS_DIR / f"calibre-fix-{stamp}.csv"
    script = DATA_DIR / "calibre-fix.py"
    script.write_text(CALIBRE_FIX_SCRIPT, encoding="utf-8")
    r = subprocess.run(["calibre-debug", "-e", str(script), "--", str(Path(__file__).resolve()),
                        str(lib), "1" if apply else "0", str(report)])
    if r.returncode:
        sys.exit(f"calibre-debug failed ({r.returncode})")
    log(f"Report: {report}")
    if not apply:
        log("Nothing changed. To fix them: books.sh calibre-fix --apply '<library>'")


EXTRA_JUNK_AUTHORS = {"anonymous", "anon", "various", "various authors", "none", "n/a", "na", "user", "scanned", "ebook",
                      "e-book", "default", "author", "a", "the", "index", "contents", "preface", "version", "scan"}


def no_real_author(a, t, per):
    """True when a Calibre 'author' is really the title, a junk word, a scanner's name or a stub."""
    n = norm(a)
    if n == norm(t) or a.casefold() in JUNK_AUTHORS or a.casefold().strip(". ") in EXTRA_JUNK_AUTHORS:
        return True
    if len(re.sub(r"\W", "", a)) <= 2 or a.lstrip()[:1] in "_.([<{-":
        return True
    if re.search(r"\d", a) and not re.search(r"\b(?:[IVX]+|\d+(?:st|nd|rd|th))\b", a):   # "DTC 136", "6 X H"
        return True
    if title_is_junk(t) and per.get(n, 0) <= 2:       # title "index"/"do": the 'author' is probably the real title
        return True
    return per.get(n, 0) == 1 and not name_ish(a)


def title_is_junk(t):
    q, _ = lookup_query(t)
    return len(q) < 3 or q.casefold() in LOOKUP_JUNK or q.isdigit() or bool(LOOKUP_JUNK_RE.match(clean_text(t or "")))


# ---------------------------------------------------------------- authors missing from the metadata

def calibre_authors(library):
    """Read-only: how many Calibre books have no real author (author == title, junk, one-book 'authors'),
    and what the original archive paths (state.db) say about each. Writes a CSV."""
    lib = Path(library)
    mdb = lib / "metadata.db"
    if not mdb.exists():
        sys.exit(f"STOP: {lib} is not a Calibre library (no metadata.db).")
    cal = sqlite3.connect(f"file:{mdb}?immutable=1", uri=True)
    rows = cal.execute("""SELECT b.id, b.title, a.name, b.path FROM books b
        JOIN books_authors_link l ON l.book = b.id JOIN authors a ON a.id = l.author ORDER BY b.id""").fetchall()
    seen, books = set(), []
    for i, t, a, path in rows:
        if i not in seen:
            seen.add(i)
            books.append((i, t, a, path))
    per = {}
    for _, _, a, _ in books:
        per[norm(a)] = per.get(norm(a), 0) + 1
    db = db_open()
    by_key = {}
    for r in db.execute("SELECT path, dest FROM files WHERE dest != '' AND status IN ('copy','convert')"):
        try:
            rel = Path(r["dest"]).relative_to(LIBRARY_DIR)
        except ValueError:
            continue
        if len(rel.parts) >= 2:
            by_key.setdefault((norm(rel.parts[0]), norm(rel.parts[1])), []).append(r["path"])
    bogus = [(i, t, a, path) for i, t, a, path in books if no_real_author(a, t, per)]
    matched = 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = LOGS_DIR / f"calibre-authors-{stamp}.csv"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "calibre title", "calibre author", "original path(s) from the archive"])
        for i, t, a, path in bogus:
            src = by_key.get((norm(a), norm(t))) or by_key.get((norm(t), norm(t))) or []
            matched += bool(src)
            w.writerow([i, t, a, " | ".join(src[:3])])
    log(f"{len(books)} books; {len(bogus)} look like they have no real author "
        f"({sum(1 for _, t, a, _ in books if norm(a) == norm(t))} have the title as the author); "
        f"{matched} of those match a record of their original file")
    log(f"{len({norm(a) for _, _, a, _ in books})} author folders in total; "
        f"{sum(1 for v in per.values() if v == 1)} have exactly one book")
    log(f"Report: {out}")


# ---------------------------------------------------------------- look up missing authors online

LOOKUP_UA = "books-tools/1.0 (personal ebook library)"
LOOKUP_JUNK_RE = re.compile(
    r"^(?:(?:v(?:ersion)?\s*\d[\d.]*\s*)?(?:scanned|proofed|proofread|converted|ripped|edited|formatted|typed|spell\s?checked|ocr)\b"
    r"|copyright\b|\(c\)|version\s*\d|e-?version|html\b|<|=+$|\\v$|table of contents|contents$|chapter\s*(?:\d+|one)$|prologue$)", re.I)
LOOKUP_JUNK = {"index", "do", "a", "x", "v1", "contents", "prologue", "chapter", "default", "unknown", "untitled",
               "title", "book", "one", "the", "part one", "copyright", "version 1", "scanned by highroller"}


RATE_LIMITED = set()      # hosts that said 429: left alone for the rest of the run


def http_json(url, retries=3, pause=1.0, quit_on_429=False):
    """GET json politely: back off on 5xx, give up (None) on anything else. quit_on_429: don't retry a rate limit,
    and remember the host so it isn't asked again this run."""
    host = urllib.parse.urlparse(url).netloc
    if host in RATE_LIMITED:
        return None
    for n in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": LOOKUP_UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and quit_on_429:
                RATE_LIMITED.add(host)
                log(f"{host} is rate limiting this address: not using it for the rest of this run")
                return None
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(pause * (3 ** n))
                continue
            return None
        except Exception:
            time.sleep(pause * (2 ** n))
    return None


def lookup_query(title):
    """The part of a messy title worth searching for, plus an author if the title names one."""
    t = clean_text(re.sub(r"\.(txt|doc|rtf|lit|epub|html?)$", "", title or "", flags=re.I))
    t = re.sub(r"\s*[\[(](?:v?\d[\d.]*|uc|ocr|html|txt|lit|ebook|b|rtf)[\])]", "", t, flags=re.I).strip()
    t = re.sub(r"^microsoft word\s*-\s*", "", t, flags=re.I)
    by = re.match(r"^(.*?)\s+by\s+([A-Z][\w.' -]{3,40})$", t)
    if by and re.search(r"\b(?:scanned|proofed|proofread|converted|ripped|edited|formatted|typed|ocr'?d?|spell\s?checked)\s*(?:&\s*\w+\s*)?$", by.group(1), re.I):
        by = None
    author = None
    if by:
        t, author = by.group(1).strip(), by.group(2).strip()
    for _ in range(3):                                          # "(Star Wars - Legends)", "(Ellora's Cave)"
        t = re.sub(r"\s*\([^()]*\)\s*$", "", t).strip()
    parts = [x.strip() for x in t.split(" - ") if x.strip()]
    if len(parts) >= 2:
        t = parts[-1]
    t = re.sub(r"^\d{1,2}\s*[-.:]\s*", "", t)                 # "03 - Title"
    t = re.sub(r"\s+v\d+(\.\d+)?$", "", t).strip(" :.-")
    return t, author


def ol_search(q, fetch=None):
    fetch = fetch or http_json
    d = fetch("https://openlibrary.org/search.json?" + urllib.parse.urlencode(
        {"title": q, "limit": 40, "fields": "title,author_name,edition_count"}))
    out = []
    for x in (d or {}).get("docs", []):
        if x.get("author_name"):
            out.append({"title": x.get("title", ""), "author": x["author_name"][0], "w": int(x.get("edition_count") or 1)})
    return out, d is not None


def gb_search(q, fetch=None):
    fetch = fetch or (lambda u: http_json(u, quit_on_429=True))
    if "www.googleapis.com" in RATE_LIMITED:
        return [], True                      # nothing to ask: not an error
    d = fetch("https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode(
        {"q": f'intitle:"{q}"', "maxResults": 8, "printType": "books"}))
    if d is None and "www.googleapis.com" in RATE_LIMITED:
        return [], True
    out = []
    for x in (d or {}).get("items", []):
        v = x.get("volumeInfo", {})
        if v.get("authors"):
            out.append({"title": v.get("title", ""), "author": v["authors"][0], "w": 1})
    return out, d is not None


SMALL_WORDS = {"of", "the", "a", "an", "and", "in", "on", "at", "to", "for", "from", "by", "with", "or", "but", "vs"}


def smart_title(s):
    words = s.lower().split()
    return " ".join(w if (i and w in SMALL_WORDS) else w[:1].upper() + w[1:] for i, w in enumerate(words))


def pick_title(q, cased):
    """Keep our capitalization unless it is ALL CAPS or all lowercase; then borrow the catalogue's (or tidy ours)."""
    return smart_title(q) if q.isupper() or q.islower() else q


def dot_initials(a):
    """"Jeffrey A Carver" -> "Jeffrey A. Carver"."""
    return re.sub(r"\b([A-Z])\b(?![.'])", r"\1.", a)


def lookup_choose(q, docs, known):
    """-> (author or None, confidence 'high'|'medium'|'low', note). known: norm(author) -> spelling in the library."""
    amp = lambda x: norm(x.replace("&", " and "))
    qn = amp(q)
    exact = [d for d in docs if amp(d["title"]) == qn or amp(d["title"].split(":")[0]) == qn]
    if not exact:
        return None, "low", "no exact title match"
    weight = {}
    for d in exact:
        a = fix_author(d["author"]) or d["author"]
        weight[a] = weight.get(a, 0) + d["w"]
    import difflib
    names = sorted(weight, key=lambda x: -weight[x])           # "Poul Anderson" / "Paul Anderson": one author
    merged = {}
    for a in names:
        home = next((m for m in merged if difflib.SequenceMatcher(None, norm(a), norm(m)).ratio() >= 0.9), None)
        if home is None:
            merged[a] = weight[a]
        else:
            lib = a if norm(a) in known and norm(home) not in known else home
            merged[lib] = merged.pop(home) + weight[a]
    weight = merged
    def score(a):
        n = norm(a)
        w = weight[a]
        if n in known or norm(" ".join(a.split()[-1:] + a.split()[:-1])) in known:
            w *= 3
        return w
    top = max(weight, key=score)
    total = sum(score(a) for a in weight)
    share = score(top) / total if total else 0
    in_lib = norm(top) in known
    alts = ", ".join(f"{a} ({weight[a]})" for a in sorted(weight, key=score, reverse=True)[:3])
    if in_lib and share >= 0.5:
        return top, "high", alts
    if share >= 0.7 and sum(weight.values()) >= 3:
        return top, "medium", alts
    return top, "low", alts


def calibre_lookup(library, apply_csv=None, level="high", limit=None):
    lib = Path(library)
    mdb = lib / "metadata.db"
    if not mdb.exists():
        sys.exit(f"STOP: {lib} is not a Calibre library (no metadata.db).")
    if apply_csv:
        return calibre_lookup_apply(lib, Path(apply_csv), level)
    cal = sqlite3.connect(f"file:{mdb}?immutable=1", uri=True)
    seen, books = set(), []
    for i, t, a in cal.execute("""SELECT b.id, b.title, a.name FROM books b JOIN books_authors_link l ON l.book=b.id
                                  JOIN authors a ON a.id=l.author ORDER BY b.id"""):
        if i not in seen:
            seen.add(i)
            books.append((i, t, a))
    per = {}
    for _, _, a in books:
        per[norm(a)] = per.get(norm(a), 0) + 1
    bogus = [(i, t, a) for i, t, a in books if no_real_author(a, t, per)]
    bogus_names = {norm(a) for _, _, a in bogus}
    known = {k: a for _, _, a in books for k in [norm(a)] if per[k] >= 3 and k not in bogus_names}
    spelling = {norm(a): a for _, _, a in books if norm(a) in known}
    cache_file = DATA_DIR / "lookup-cache.json"
    try:
        cache = json.loads(cache_file.read_text("utf-8"))
    except Exception:
        cache = {}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    out = LOGS_DIR / f"calibre-lookup-{stamp}.csv"
    counts, fails = {}, 0
    todo = bogus[:limit] if limit else bogus
    log(f"{len(books)} books, {len(bogus)} without a usable author; looking up {len(todo)}")
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "current title", "current author", "proposed author", "proposed title", "confidence", "alternatives"])
        for n, (i, t, a) in enumerate(todo, 1):
            a_title = clean_text(a.replace("|", ",")) if a else a       # Calibre shows a comma as "|"
            title_junk = title_is_junk(t)
            # the real title sits in the author field when the title field is junk or a truncated copy of it
            truncated = bool(t and a_title) and len(a_title) > len(t) and norm(a_title).startswith(norm(t)) and not name_ish(a_title)
            q, by = lookup_query(a_title if (title_junk or truncated) else t)
            res_title = q
            # 1. an author written in the title field itself: "Haydon, Elizabeth - Rhapsody 1", "Tepper, Sheri S"
            local = None
            tt = re.sub(r"^microsoft word\s*-\s*", "", clean_text(t or ""), flags=re.I)
            tt = re.sub(r"\.(txt|doc|rtf|lit|epub|html?)$", "", re.sub(r",(?=\S)", ", ", tt), flags=re.I)
            m = NAME_FORM.match(tt)
            a_junk = (not a_title) or bool(re.search(r"\d", lookup_query(a_title)[0])) or \
                a_title.casefold().strip(". ") in (JUNK_AUTHORS | EXTRA_JUNK_AUTHORS | {"unknown", "me", "user"})
            if m and m.group(1).partition(", ")[2].strip().casefold() in ("the", "a", "an"):
                m = None                                              # "Coelura, The" is a title, not a name
            if m and not title_junk:
                last, _, first = m.group(1).partition(", ")
                if name_ish(last) and name_ish(first):
                    parts = [x.strip() for x in tt.split(" - ")]
                    stub = a_junk or (a_title == a_title.lower() and len(a_title.split()) <= 2) or len(a_title) < 3 or \
                           (a_title.isupper() and len(a_title) <= 4)
                    guess = stub and len(parts) >= 2                              # the title is the last part: a guess
                    new_t = lookup_query(parts[-1])[0] if guess else lookup_query(a_title)[0]
                    if stub and len(parts) < 2:
                        local = (None, "low", "author known but no usable title")
                    elif new_t and len(new_t) >= 3:
                        local = (fix_author(m.group(1)), "medium" if (guess or len(parts) >= 3) else "high",
                                 "author written in the title field" + ("; title taken from the title field" if guess else ""))
                        res_title = new_t
            elif not title_junk and not truncated and a_title and len(t.split()) >= 2 and name_ish(t.strip()) and not t.isupper() and norm(t) != norm(a_title) and \
                    not re.match(r"(?:the|a|an)\s", t, re.I) and not a_junk and not name_ish(a_title) and len(a_title) >= 3 and \
                    not re.search(r"[,&;]|\band\b|\|", t) and len(t.split()[-1].strip(".")) >= 3:
                local = (dot_initials(fix_author(t) or t), "medium", "author is in the title field, title in the author field")
                res_title = lookup_query(a_title)[0]
            if local:
                author, conf, note = local
                res_title = pick_title(res_title, None)
            elif len(q) < 3 or q.casefold() in LOOKUP_JUNK or q.isdigit() or LOOKUP_JUNK_RE.match(q):
                author, conf, note = None, "skip", "title too short or generic"
            elif by and fix_author(by):
                author, conf, note = fix_author(by), "high", "author named in the title"
            else:
                if q not in cache:
                    docs, ok = ol_search(q)
                    if ":" in q and not any(norm(d["title"]) == norm(q) for d in docs):
                        d2, ok2 = ol_search(q.split(":")[0].strip())
                        docs, ok = docs + d2, ok or ok2
                    if not ok or not any(norm(d["title"].replace("&", " and ")) == norm(q.replace("&", " and ")) for d in docs):
                        docs2, ok2 = gb_search(q)
                        docs = docs + docs2
                        ok = ok or ok2
                    if not ok:
                        fails += 1
                        if fails >= 15:
                            cache_file.write_text(json.dumps(cache), "utf-8")
                            sys.exit("STOP: the lookup services keep failing (rate limit or no internet). Run again later; "
                                     "answers so far are cached.")
                        continue
                    cache[q] = docs
                    if len(cache) % 25 == 0:
                        cache_file.write_text(json.dumps(cache), "utf-8")
                    time.sleep(0.7)
                author, conf, note = lookup_choose(q, cache[q], known)
                if conf == "low" and note == "no exact title match":
                    seen_docs = "; ".join(f"{d['title'][:30]} / {d['author'][:20]}" for d in cache[q][:3])
                    note = f"no exact title match ({len(cache[q])} results" + (f": {seen_docs})" if seen_docs else ")")
                else:
                    cased = next((d["title"] for d in cache[q] if norm(d["title"].replace("&", " and ")) == norm(q.replace("&", " and "))), None)
                    res_title = pick_title(q, cased)
            res_title = re.sub(r"\s*\([^)]*$", "", res_title).strip()          # "Quag Keep (AD" -> "Quag Keep"
            if conf != "skip" and (title_is_junk(res_title) or res_title.casefold() == "unknown"):
                author, conf, note = None, "low", "no usable title"
            if author and norm(author) == norm(res_title):
                author, conf, note = None, "low", "the proposed author is the title"
            if author:
                author = dot_initials(author)
            if author:
                author = spelling.get(norm(author)) or spelling.get(norm(" ".join(author.split()[-1:] + author.split()[:-1]))) or author
                if "|" in author:
                    author = fix_author(author.replace("|", ",")) or author
            counts[conf] = counts.get(conf, 0) + 1
            w.writerow([i, t, a, author or "", res_title, conf, note])
            if n % 100 == 0:
                log(f"Looked up {n} of {len(todo)}...")
    cache_file.write_text(json.dumps(cache), "utf-8")
    log("Done - " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    log(f"Report: {out}")
    log("Nothing changed. Check it, then apply with: books.sh calibre-lookup <library> --apply <report.csv> [--level medium]")


CALIBRE_LOOKUP_APPLY = r"""
import sys, csv
from calibre.library import db
lib, rows = sys.argv[-2], sys.argv[-1]
d = db(lib).new_api
n = 0
for line in csv.DictReader(open(rows, encoding="utf-8-sig")):
    i = int(line["id"])
    d.set_field("authors", {i: (line["proposed author"],)})
    d.set_field("title", {i: line["proposed title"]})
    n += 1
    if n % 500 == 0:
        print("Fixed %d..." % n, flush=True)
print("Fixed %d book(s)" % n)
d.close()
"""


def calibre_lookup_apply(lib, csv_file, level):
    allowed = {"high": {"high"}, "medium": {"high", "medium"}}[level]
    rows = [r for r in csv.DictReader(open(csv_file, encoding="utf-8-sig"))
            if r["confidence"] in allowed and r["proposed author"] and r["proposed title"]]
    sel = DATA_DIR / "lookup-apply.csv"
    with open(sel, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["id", "proposed author", "proposed title"], extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    script = DATA_DIR / "calibre-lookup-apply.py"
    script.write_text(CALIBRE_LOOKUP_APPLY, encoding="utf-8")
    log(f"Applying {len(rows)} row(s) at confidence {level} or better")
    r = subprocess.run(["calibre-debug", "-e", str(script), "--", str(lib), str(sel)])
    if r.returncode:
        sys.exit(f"calibre-debug failed ({r.returncode})")

CALIBRE_REVIEW_MOVE = r"""
import sys, csv, os, re
from calibre.library import db
lib, folder, rows = sys.argv[-3], sys.argv[-2], sys.argv[-1]
src = db(lib).new_api
ids = [int(r["id"]) for r in csv.DictReader(open(rows, encoding="utf-8-sig"))]
def safe(x):
    return re.sub(r'[\\/:*?"<>|]+', " ", x or "").strip(" .")[:80] or "untitled"
moved = failed = 0
log = open(os.path.join(folder, "_moved.csv"), "a", newline="", encoding="utf-8-sig")
w = csv.writer(log)
for n, i in enumerate(ids, 1):
    try:
        if not src.has_id(i):
            continue
        mi = src.get_metadata(i)
        title, author = mi.title, " & ".join(mi.authors)
        written = []
        for fmt in src.formats(i):
            path = os.path.join(folder, "%s [%s] (id %d).%s" % (safe(title), safe(author), i, fmt.lower()))
            with open(path, "wb") as f:
                src.copy_format_to(i, fmt, f)
            if os.path.getsize(path) != src.format_metadata(i, fmt)["size"]:
                raise RuntimeError("copy of %s is the wrong size" % fmt)
            written.append(path)
        if not written:
            raise RuntimeError("book has no files")
        src.remove_books((i,), permanent=True)
        for path in written:
            w.writerow([i, title, author, os.path.basename(path)])
        log.flush()
        moved += 1
    except Exception as e:
        failed += 1
        print("FAILED id %s: %s" % (i, e), flush=True)
    if n % 100 == 0:
        print("Moved %d of %d..." % (n, len(ids)), flush=True)
print("Moved %d book(s), %d failed" % (moved, failed))
log.close()
src.close()
"""


def calibre_review(library, apply, strict=False):
    """Take every book without a proper author/title out of the Calibre library into a plain folder
    (copied and size-checked first; removed from Calibre only after that)."""
    lib = Path(library)
    folder = Path(os.environ.get("REVIEW_DIR") or "/volume1/NAS/Archive/EPUB To Fix")
    mdb = lib / "metadata.db"
    if not mdb.exists():
        sys.exit(f"STOP: {lib} is not a Calibre library (no metadata.db).")
    if folder.resolve() == lib.resolve() or lib.resolve() in folder.resolve().parents:
        sys.exit("STOP: the folder must be outside the Calibre library.")
    cal = sqlite3.connect(f"file:{mdb}?immutable=1", uri=True)
    seen, books = set(), []
    for i, t, a in cal.execute("""SELECT b.id, b.title, a.name FROM books b JOIN books_authors_link l ON l.book=b.id
                                  JOIN authors a ON a.id=l.author ORDER BY b.id"""):
        if i not in seen:
            seen.add(i)
            books.append((i, t, a))
    per = {}
    for _, _, a in books:
        per[norm(a)] = per.get(norm(a), 0) + 1
    def not_a_person(a):
        a = clean_text((a or "").replace("|", ","))
        return not name_ish(a) or bool(re.search(r"\d", a))
    bad = [(i, t, a) for i, t, a in books
           if no_real_author(a, t, per) or title_is_junk(t) or (strict and not_a_person(a))]
    if strict:
        odd = {}
        for _, _, a in bad:
            if not_a_person(a):
                odd[a] = odd.get(a, 0) + 1
        log("Author names that are not a person's name (most books first): " +
            "; ".join(f"{a} ({n})" for a, n in sorted(odd.items(), key=lambda x: -x[1])[:60]))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    out = LOGS_DIR / f"calibre-review-{stamp}.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "author"])
        w.writerows(bad)
    log(f"{len(books)} books; {len(bad)} have no proper author/title and would move to {folder}")
    log(f"List: {out}")
    if not apply:
        log("Nothing changed. Check the list, back up metadata.db, stop the Calibre container, then add --apply.")
        return
    folder.mkdir(parents=True, exist_ok=True)
    backup = lib / f"metadata.db.before-review-{stamp}"
    shutil.copy2(mdb, backup)
    log(f"Backed up the database to {backup}")
    script = DATA_DIR / "calibre-review-move.py"
    script.write_text(CALIBRE_REVIEW_MOVE, encoding="utf-8")
    r = subprocess.run(["calibre-debug", "-e", str(script), "--", str(lib), str(folder), str(out)])
    if r.returncode:
        sys.exit(f"calibre-debug failed ({r.returncode})")
    log("Done. Now run: calibredb backup_metadata --all, then rescan the library in BookOrbit.")


CALIBRE_SINGLE_APPLY = r"""
import sys, csv
from calibre.library import db
lib, rows = sys.argv[-2], sys.argv[-1]
d = db(lib).new_api
n = 0
for line in csv.DictReader(open(rows, encoding="utf-8-sig")):
    d.set_field("authors", {int(line["id"]): (line["proposed author"],)})
    n += 1
    if n % 500 == 0:
        print("Fixed %d..." % n, flush=True)
print("Fixed %d book(s)" % n)
d.close()
"""


def first_author(name):
    """'Ayn Rand; Leonard Peikoff', 'Weis| Margaret; Hickman| Tracy', 'Weis and Hickman' -> the first person, 'First Last'."""
    a = clean_text(name.replace("|", ","))
    parts = [x.strip() for x in re.split(r"\s*;\s*|\s+&\s+", a) if x.strip()]
    if len(parts) == 1:
        m = re.match(r"^(.+?)\s+and\s+(.+)$", parts[0])
        if m and name_ish(m.group(1)) and name_ish(m.group(2)):
            parts = [m.group(1)]
    return fix_author(parts[0]) if parts else None


def calibre_single_author(library, apply):
    """Every book gets ONE author (the first), so 'Ayn Rand; Leonard Peikoff; ...' folders all become 'Ayn Rand'."""
    lib = Path(library)
    mdb = lib / "metadata.db"
    if not mdb.exists():
        sys.exit(f"STOP: {lib} is not a Calibre library (no metadata.db).")
    cal = sqlite3.connect(f"file:{mdb}?immutable=1", uri=True)
    names = {}
    for i, a in cal.execute("""SELECT l.book, a.name FROM books_authors_link l JOIN authors a ON a.id=l.author
                               ORDER BY l.book, l.id"""):
        names.setdefault(i, []).append(a)
    titles = dict(cal.execute("SELECT id, title FROM books"))
    count = {}
    for al in names.values():
        count[al[0]] = count.get(al[0], 0) + 1
    changes = []
    for i, al in sorted(names.items()):
        cur = al[0]
        if len(al) == 1 and not re.search(r"[;&|,]|\sand\s", cur):
            continue
        new = first_author(cur)
        if not new or new.casefold() in (JUNK_AUTHORS | EXTRA_JUNK_AUTHORS) or (len(al) == 1 and new == cur):
            continue
        changes.append((i, titles.get(i, ""), " ; ".join(al), new))
    # one spelling per person: the one most books already use
    spell = {}
    for al in names.values():
        spell.setdefault(norm(al[0]), {}).setdefault(al[0], 0)
        spell[norm(al[0])][al[0]] += 1
    final = []
    for i, t, cur, new in changes:
        opts = spell.get(norm(new))
        new = max(opts, key=opts.get) if opts else new
        if new != cur:
            final.append((i, t, cur, new))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    out = LOGS_DIR / f"calibre-single-author-{stamp}.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "current author", "proposed author"])
        w.writerows(final)
    log(f"{len(names)} books; {len(final)} get a single first author ({len({c for _, _, c, _ in final})} author names collapse)")
    log(f"List: {out}")
    if not apply:
        log("Nothing changed. Check it, back up metadata.db, stop the Calibre container, then add --apply.")
        return
    script = DATA_DIR / "calibre-single-apply.py"
    script.write_text(CALIBRE_SINGLE_APPLY, encoding="utf-8")
    r = subprocess.run(["calibre-debug", "-e", str(script), "--", str(lib), str(out)])
    if r.returncode:
        sys.exit(f"calibre-debug failed ({r.returncode})")
    log("Done. Now refresh the metadata files (calibredb backup_metadata --all) and rescan in BookOrbit.")


CALIBRE_EXPORT = r"""
import sys, os, re, csv
from calibre.library import db
from calibre.ebooks.metadata.meta import set_metadata
lib, dest = sys.argv[-2], sys.argv[-1]
d = db(lib).new_api
def safe(x, n=100):
    x = re.sub(r'[\\/:*?"<>|]+', " ", x or "")
    x = re.sub(r"\s+", " ", x).strip(" .")
    return x[:n].strip(" .") or "untitled"
done_file = os.path.join(dest, "_exported.csv")
done = set()
if os.path.exists(done_file):
    done = {int(r[0]) for r in csv.reader(open(done_file, encoding="utf-8-sig")) if r}
log = open(done_file, "a", newline="", encoding="utf-8-sig")
w = csv.writer(log)
ids = sorted(d.all_book_ids())
ok = skipped = bad_meta = failed = 0
for n, i in enumerate(ids, 1):
    if i in done:
        skipped += 1
        continue
    try:
        mi = d.get_metadata(i)
        author = safe(mi.authors[0] if mi.authors else "Unknown")
        title = safe(mi.title)
        for fmt in d.formats(i):
            k, name = 1, title
            while True:
                folder = os.path.join(dest, author, name)
                path = os.path.join(folder, "%s.%s" % (name, fmt.lower()))
                if not os.path.exists(path):
                    break
                k += 1
                name = "%s (%d)" % (title, k)
            os.makedirs(folder, exist_ok=True)
            with open(path, "wb") as f:
                d.copy_format_to(i, fmt, f)
            if os.path.getsize(path) != d.format_metadata(i, fmt)["size"]:
                raise RuntimeError("copy is the wrong size")
            try:
                with open(path, "r+b") as f:
                    set_metadata(f, mi, fmt.lower())
            except Exception as e:
                bad_meta += 1
                print("metadata not written for id %d: %s" % (i, e), flush=True)
            w.writerow([i, author, name, fmt])
        log.flush()
        ok += 1
    except Exception as e:
        failed += 1
        print("FAILED id %s: %s" % (i, e), flush=True)
    if n % 250 == 0:
        print("Exported %d of %d..." % (n, len(ids)), flush=True)
print("Exported %d, already done %d, metadata not written %d, failed %d" % (ok, skipped, bad_meta, failed))
log.close()
d.close()
"""


def calibre_export(library):
    """Write the library out as a plain Author/Title/Title.epub tree (correct metadata embedded), leaving Calibre untouched."""
    lib = Path(library)
    dest = Path(os.environ.get("EXPORT_DIR") or "/volume1/NAS/Archive/EPUB Clean")
    if not (lib / "metadata.db").exists():
        sys.exit(f"STOP: {lib} is not a Calibre library (no metadata.db).")
    if dest.resolve() == lib.resolve() or lib.resolve() in dest.resolve().parents:
        sys.exit("STOP: the export folder must be outside the Calibre library.")
    dest.mkdir(parents=True, exist_ok=True)
    script = DATA_DIR / "calibre-export.py"
    script.write_text(CALIBRE_EXPORT, encoding="utf-8")
    log(f"Exporting {lib} to {dest} (safe to stop and run again; it carries on where it left off)")
    r = subprocess.run(["calibre-debug", "-e", str(script), "--", str(lib), str(dest)])
    if r.returncode:
        sys.exit(f"calibre-debug failed ({r.returncode})")


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
    sub.add_parser("regroup")
    cl = sub.add_parser("calibre-lookup")
    cl.add_argument("library")
    cl.add_argument("--apply", metavar="REPORT_CSV")
    cl.add_argument("--level", choices=["high", "medium"], default="high")
    cl.add_argument("--limit", type=int)
    ce = sub.add_parser("calibre-export")
    ce.add_argument("library")
    cs = sub.add_parser("calibre-single-author")
    cs.add_argument("library")
    cs.add_argument("--apply", action="store_true")
    cr = sub.add_parser("calibre-review")
    cr.add_argument("library")
    cr.add_argument("--apply", action="store_true")
    cr.add_argument("--strict", action="store_true", help="also move books whose author is not a person's name")
    ca = sub.add_parser("calibre-authors")
    ca.add_argument("library")
    cf = sub.add_parser("calibre-fix")
    cf.add_argument("library")
    cf.add_argument("--apply", action="store_true")
    cv = sub.add_parser("converted")
    cv.add_argument("--formats", help="only books converted from these formats, e.g. rtf,txt")
    sub.add_parser("migrate-db")
    sub.add_parser("manga")
    mt = sub.add_parser("manga-tag")
    mt.add_argument("--apply", action="store_true", help="write the tags (default: dry run)")
    cb = sub.add_parser("cbr2cbz")
    cb.add_argument("--apply", action="store_true", help="convert (default: dry run)")
    cb.add_argument("--min-mb", type=int, default=0, help="only .cbr files this size and up")
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
    elif a.cmd == "calibre-lookup":
        calibre_lookup(a.library, a.apply, a.level, a.limit)
    elif a.cmd == "calibre-export":
        calibre_export(a.library)
    elif a.cmd == "calibre-single-author":
        calibre_single_author(a.library, a.apply)
    elif a.cmd == "calibre-review":
        calibre_review(a.library, a.apply, a.strict)
    elif a.cmd == "calibre-authors":
        calibre_authors(a.library)
    elif a.cmd == "calibre-fix":
        calibre_fix(a.library, a.apply)
    elif a.cmd == "converted":
        converted(a.formats)
    elif a.cmd == "regroup":
        regroup()
    elif a.cmd == "textbooks":
        textbooks(a.apply)
    elif a.cmd == "migrate-db":
        migrate_db()
    elif a.cmd == "manga":
        manga()
    elif a.cmd == "manga-tag":
        manga_tag(a.apply)
    elif a.cmd == "cbr2cbz":
        cbr2cbz(a.apply, a.min_mb)
    elif a.cmd == "unpack":
        unpack()
    elif a.cmd == "run":
        pull()
        organize(True)


if __name__ == "__main__":
    main()
