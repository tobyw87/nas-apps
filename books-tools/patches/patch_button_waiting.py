#!/usr/bin/env python3
"""Patch button.py: a "Waiting to be filed" list for audiobooks that were downloaded but never reached ABS.

Usage: python3 patch_button_waiting.py /path/to/button.py
Makes a backup next to the file, refuses to run twice, changes nothing if the file isn't what it expects,
and restores the backup if the result doesn't compile.
"""
import py_compile
import re
import shutil
import sys
import time

path = sys.argv[1]
src = open(path, encoding="utf-8", newline="").read()
if "waiting_items" in src:
    sys.exit("Already patched - nothing to do.")
CRLF = "\r\n" in src

NEW_CODE = r'''# ------------------------------------------------------------ audiobooks waiting to be filed into ABS
# Downloads in book_downloads that LazyLibrarian never filed into ABS (e.g. it has the wrong author for the book).
# Detection: an audio file counts as filed when ABS holds an audio file of the same type and size.
ABS_PATH = os.environ.get("SEEDBOX_PATH", "/home/mynock42/media/ABS").rstrip("/")
WAIT_MIN_AGE = int(os.environ.get("WAIT_MIN_AGE_MINUTES", "30")) * 60      # leave new downloads to LazyLibrarian first
WAIT_REFRESH = int(os.environ.get("WAIT_REFRESH_SECONDS", "600"))
WAITSKIP = DATA_DIR / "waiting-skip.json"
_wait = {"at": 0.0, "items": [], "files": {}, "err": "", "busy": False}
_wait_lock = threading.Lock()


def _audio_listing(root, with_time):
    """[(path, size, mtime)] of the audio files under a seedbox folder."""
    args = ["lsf", "-R", "--files-only", "--ignore-case", "--format", "pst" if with_time else "ps"]
    for ext in sorted(AUDIO_EXTS):
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


def _guess_author_title(name):
    """A first guess from a download name like 'Author - Title'. You can correct it before filing."""
    base = re.sub(r"\.(" + "|".join(sorted(AUDIO_EXTS)) + r")$", "", name, flags=re.I).replace("_", " ").strip()
    left, sep, right = base.partition(" - ")
    return (left.strip(), right.strip()) if sep else ("", base)


def _wait_skips():
    try:
        return set(json.loads(WAITSKIP.read_text()))
    except (OSError, ValueError):
        return set()


def _scan_waiting():
    downloads = _audio_listing(SEEDBOX_DOWNLOADS, True)
    in_abs = {(p.rsplit(".", 1)[-1].lower(), s) for p, s, _ in _audio_listing(ABS_PATH, False)}
    skips = _wait_skips()
    groups = {}
    for p, size, mt in downloads:
        top = p.split("/", 1)[0]
        if top.endswith((".unpack", ".fail")) or top in skips or size <= 0:
            continue
        g = groups.setdefault(top, {"files": [], "newest": 0})
        g["files"].append((p, size))
        g["newest"] = max(g["newest"], mt)
    now = time.time()
    items, files = [], {}
    for top, g in sorted(groups.items()):
        if now - g["newest"] < WAIT_MIN_AGE:
            continue                                   # too new: LazyLibrarian gets the first go
        if any((p.rsplit(".", 1)[-1].lower(), s) in in_abs for p, s in g["files"]):
            continue                                   # LazyLibrarian has filed it (or part of it)
        author, title = _guess_author_title(top)
        files[top] = g["files"]
        items.append({"name": top, "files": len(g["files"]),
                      "ext": "/".join(sorted({p.rsplit(".", 1)[-1].lower() for p, _ in g["files"]})),
                      "size_mb": round(sum(s for _, s in g["files"]) / 1048576),
                      "age_h": round((now - g["newest"]) / 3600, 1), "author": author, "title": title})
    return items, files


def _refresh_waiting():
    try:
        items, files = _scan_waiting()
        with _wait_lock:
            _wait.update(items=items, files=files, err="")
    except Exception as e:
        with _wait_lock:
            _wait["err"] = short(str(e), 160)
    finally:
        with _wait_lock:
            _wait.update(at=time.time(), busy=False)


def waiting_items():
    """The cached list; refreshes in the background (every WAIT_REFRESH seconds) so the page never waits."""
    with _wait_lock:
        if time.time() - _wait["at"] > WAIT_REFRESH and not _wait["busy"]:
            _wait["busy"] = True
            threading.Thread(target=_refresh_waiting, daemon=True).start()
        return list(_wait["items"])


def adopt_waiting(name, author, title):
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
    manual = f'on the seedbox: ~/scripts/adopt_manual.sh link "{name}" "{author}" "{title}"'
    if not hardlink_ok():
        raise RuntimeError("this rclone can't make hard links; " + manual)
    chosen = [f for f in files if f[0].lower().endswith(".m4b")] or files        # prefer M4B when a download has any
    dest = f"{ABS_PATH}/{author}/{title}"
    for p, size in chosen:
        base = p.rsplit("/", 1)[-1]
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


def skip_waiting(name):
    """'Ignore': stop listing this download (it isn't an audiobook, or you don't want it filed)."""
    skips = _wait_skips() | {name}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    WAITSKIP.write_text(json.dumps(sorted(skips)))
    with _wait_lock:
        _wait["items"] = [i for i in _wait["items"] if i["name"] != name]
    blog(f"  Ignoring '{name}': it won't be listed as waiting any more.")


'''

CSS = '''.wait{margin-top:12px;padding:10px;border-radius:8px;background:var(--box)}
.wi{margin:8px 0 0;display:grid;gap:6px;flex:none}.wn{font-weight:600;word-break:break-word}
.wi input{padding:8px;border-radius:6px;border:1px solid #495057;background:var(--bg);color:var(--fg);font:14px system-ui,sans-serif}
.wi button{padding:8px;font-size:13px}.wi .row{margin-top:0}
</style></head><body><div class="wrap">'''
HTML = '''<div id="wait" class="wait" style="display:none"><b>Waiting to be filed</b> <span class="sub">(downloaded, but not in Audiobookshelf)</span><div id="wl"></div></div>
<pre id="o">__LINES__</pre>'''
JS = '''document.getElementById("l").textContent=st.log||"no log yet";
    const w=st.waiting||[];const wb=document.getElementById("wait");wb.style.display=w.length?"":"none";
    const sig=w.map(x=>x.name).join("|");
    if(sig!==window._wsig){window._wsig=sig;const q=s=>String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/"/g,"&quot;");
      document.getElementById("wl").innerHTML=w.map(x=>`<form method="post" action="adopt__Q__" class="wi"><div class="wn">${q(x.name)}</div><div class="sub">${x.files} file(s) &middot; ${q(x.ext)} &middot; ${x.size_mb} MB &middot; waiting ${x.age_h} h</div><input type="hidden" name="name" value="${q(x.name)}"><input name="author" placeholder="Author" value="${q(x.author)}"><input name="title" placeholder="Title" value="${q(x.title)}"><div class="row"><button class="yes">File it</button><button class="no" formaction="skipwait__Q__">Ignore</button></div></form>`).join("");}'''
POST = '''        if path in ("/adopt", "/skipwait") and key_ok(self.path):
            form = parse_qs(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 65536)).decode("utf-8", "replace"))
            val = lambda k: (form.get(k) or [""])[0].strip()
            try:
                if path == "/adopt":
                    adopt_waiting(val("name"), val("author"), val("title"))
                else:
                    skip_waiting(val("name"))
            except Exception as e:
                blog(f"  Couldn't file '{val('name')}': {short(str(e), 220)}")
            return self._send(303, "", extra={"Location": "./" + ("?k=" + KEY if KEY else "")})
        if path == "/dismiss" and key_ok(self.path):'''

EDITS = [
    ("the page section marker", "# ------------------------------------------------------------ page\n", NEW_CODE + "# ------------------------------------------------------------ page\n"),
    ("the status() return", '"ask": None if busy else state.get("ask")}', '"ask": None if busy else state.get("ask"), "waiting": waiting_items()}'),
    ("the page style end", '</style></head><body><div class="wrap">', CSS),
    ("the log pre element", '<pre id="o">__LINES__</pre>', HTML),
    ("the log label script line", 'document.getElementById("l").textContent=st.log||"no log yet";', JS),
    ("the /dismiss handler", '        if path == "/dismiss" and key_ok(self.path):', POST),
]
for label, old, _ in EDITS:
    n = src.count(old.replace("\n", "\r\n") if CRLF else old)
    if n != 1:
        sys.exit(f"Can't patch: expected exactly one copy of {label}, found {n}. Nothing was changed.")

backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(path, backup)
for label, old, new in EDITS:
    if CRLF:
        old, new = old.replace("\n", "\r\n"), new.replace("\r\n", "\n").replace("\n", "\r\n")
    src = src.replace(old, new, 1)
open(path, "w", encoding="utf-8", newline="").write(src)
try:
    py_compile.compile(path, doraise=True)
except py_compile.PyCompileError as e:
    shutil.copy2(backup, path)
    sys.exit(f"The patched file didn't compile, so I restored the original: {e}")
print(f"Patched {path} (backup: {backup})")
