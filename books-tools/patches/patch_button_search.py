#!/usr/bin/env python3
"""Patch button.py (on top of patch_button_waiting.py): an "Add a book" search box on the page (tile and full page).

Type a title or author, tick eBook / AudioBook (both by default), press Search; each match has an Add button.
Add puts the book into LazyLibrarian, marks the ticked formats Wanted and starts LazyLibrarian's search; the normal
pipeline (Deluge hook, LazyLibrarian import, scheduled pull, Waiting list) takes it from there. The Hardcover button
is untouched. Uses the page's existing LL_URL and LL_APIKEY settings.

Usage: python3 patch_button_search.py /path/to/button.py
Makes a backup, refuses to run twice, changes nothing if the file isn't what it expects,
restores the backup if the result doesn't compile.
"""
import py_compile
import shutil
import sys
import time

path = sys.argv[1]
src = open(path, encoding="utf-8", newline="").read()
if "def search_books" in src:
    sys.exit("Already patched - nothing to do.")
if "waiting_items" not in src:
    sys.exit("Install patch_button_waiting.py first (this one builds on it).")
CRLF = "\r\n" in src
if CRLF:
    src = src.replace("\r\n", "\n")

NEW_CODE = r'''# ------------------------------------------------------------ search LazyLibrarian and add a book
LL_KEY = os.environ.get("LL_APIKEY", "").strip()
SEARCH_MAX = 8
LL_FORMATS = ("eBook", "AudioBook")


def _ll_api(cmd, **params):
    """One LazyLibrarian API call: the decoded JSON, or the plain text it answers with (e.g. OK)."""
    if not (LL_URL and LL_KEY):
        raise RuntimeError("this page has no LazyLibrarian address or API key (LL_URL / LL_APIKEY)")
    query = urllib.parse.urlencode({"apikey": LL_KEY, "cmd": cmd, **params})
    with urllib.request.urlopen(f"{LL_URL}/api?{query}", timeout=90) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError:
        return raw.strip()


def search_books(q):
    """Up to SEARCH_MAX matches for a title or author, from LazyLibrarian's own book source."""
    q = re.sub(r"\s+", " ", q or "").strip()
    if len(q) < 2:
        raise ValueError("type at least two characters")
    res = _ll_api("findBook", name=q)
    if isinstance(res, dict):
        res = next((v for v in res.values() if isinstance(v, list)), [])
    if not isinstance(res, list):
        raise RuntimeError(short(str(res), 160) or "LazyLibrarian found nothing")
    out, seen = [], set()
    for r in res:
        if not isinstance(r, dict):
            continue
        bid = str(r.get("bookid") or "").strip()
        if not bid or bid in seen:
            continue
        seen.add(bid)
        out.append({"id": bid, "title": str(r.get("bookname") or "?")[:120],
                    "author": str(r.get("authorname") or "")[:80], "year": str(r.get("bookdate") or "")[:4]})
        if len(out) >= SEARCH_MAX:
            break
    return out


def add_book(bid, title, author, formats):
    """Add a search result to LazyLibrarian, mark the chosen formats Wanted and start its search."""
    bid = re.sub(r"[^A-Za-z0-9_.:\-]", "", bid or "")
    fmts = [f for f in LL_FORMATS if f in (formats or [])]
    if not bid:
        raise ValueError("no book id")
    if not fmts:
        raise ValueError("tick eBook, AudioBook or both")
    res = _ll_api("addBook", id=bid, wait=1)
    if isinstance(res, str) and res.lower().startswith(("error", "invalid", "missing", "failed")):
        raise RuntimeError(short(res, 160))
    for f in fmts:
        res = _ll_api("queueBook", id=bid, type=f)
        if isinstance(res, str) and res.lower().startswith(("invalid", "missing", "error")):
            raise RuntimeError(f"couldn't mark the {f} as wanted: {short(res, 120)}")
    for f in fmts:
        _ll_api("searchBook", id=bid, type=f)                  # runs in the background inside LazyLibrarian
    what = " and ".join("eBook" if f == "eBook" else "audiobook" for f in fmts)
    msg = f"Added '{title or bid}' by {author or '?'}: {what} wanted; LazyLibrarian is searching."
    blog("  " + msg)
    return msg


'''

CSS = '''.srch{margin-top:12px;padding:10px;border-radius:8px;background:var(--box);flex:none}
.srch form{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px}
.srch input[type=text]{flex:1;min-width:160px;padding:8px;border-radius:6px;border:1px solid #495057;background:var(--bg);color:var(--fg);font:14px system-ui,sans-serif}
.srch label{font-size:13px;white-space:nowrap}.srch button{padding:8px 14px;font-size:13px;width:auto}
.sres{margin-top:8px;display:grid;gap:6px}.sri{display:flex;gap:8px;align-items:center;justify-content:space-between;padding:6px 8px;border-radius:6px;background:var(--bg)}
.sri .t{font-weight:600;word-break:break-word}.sri .m{font-size:12px;opacity:.75}
'''

HTML = '''<div id="srch" class="srch"><b>Add a book</b> <span class="sub">(searches LazyLibrarian)</span>
<form onsubmit="return lsearch(event)"><input type="text" id="lsq" placeholder="Title or author" autocomplete="off">
<label><input type="checkbox" id="lse" checked> eBook</label> <label><input type="checkbox" id="lsa" checked> AudioBook</label>
<button type="submit">Search</button></form>
<div class="sub" id="lsm"></div><div class="sres" id="lsr"></div></div>
'''

JS = '''function lmsg(t,bad){const m=document.getElementById("lsm");m.textContent=t;m.style.color=bad?"#fa5252":"";}
async function lsearch(e){
  e.preventDefault();
  const q=document.getElementById("lsq").value.trim();
  if(q.length<2){lmsg("Type at least two characters.",true);return false;}
  lmsg("Searching...");document.getElementById("lsr").innerHTML="";
  try{
    const r=await (await fetch("lsearch__Q__",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"q="+encodeURIComponent(q)})).json();
    if(!r.ok){lmsg(r.error||"Search failed.",true);return false;}
    if(!r.results.length){lmsg("No matches. Try fewer words, or the author's name.");return false;}
    lmsg(r.results.length+" match(es). Tick the formats, then press Add.");
    const esc=s=>String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/"/g,"&quot;");
    window._lres=r.results;
    document.getElementById("lsr").innerHTML=r.results.map((x,i)=>`<div class="sri"><div><div class="t">${esc(x.title)}${x.year?" ("+esc(x.year)+")":""}</div><div class="m">${esc(x.author)}</div></div><button type="button" data-i="${i}">Add</button></div>`).join("");
    document.querySelectorAll("#lsr button").forEach(b=>{b.onclick=()=>ladd(b);});
  }catch(err){lmsg("Search failed: "+err,true);}
  return false;
}
async function ladd(b){
  const x=window._lres[+b.dataset.i];
  const f=[];if(document.getElementById("lse").checked)f.push("eBook");if(document.getElementById("lsa").checked)f.push("AudioBook");
  if(!f.length){lmsg("Tick eBook, AudioBook or both first.",true);return;}
  b.disabled=true;b.textContent="Adding...";
  const p=new URLSearchParams({id:x.id,title:x.title,author:x.author});f.forEach(v=>p.append("fmt",v));
  try{
    const r=await (await fetch("ladd__Q__",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:p.toString()})).json();
    if(r.ok){b.textContent="Added";lmsg(r.msg);}else{b.disabled=false;b.textContent="Add";lmsg(r.error||"Couldn't add it.",true);}
  }catch(err){b.disabled=false;b.textContent="Add";lmsg("Couldn't add it: "+err,true);}
}
async function poll(){'''

POST = '''        if path in ("/lsearch", "/ladd") and key_ok(self.path):
            form = parse_qs(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 65536)).decode("utf-8", "replace"))
            val = lambda k: (form.get(k) or [""])[0].strip()
            try:
                if path == "/lsearch":
                    out = {"ok": True, "results": search_books(val("q"))}
                else:
                    out = {"ok": True, "msg": add_book(val("id"), val("title"), val("author"), form.get("fmt") or [])}
            except Exception as e:
                out = {"ok": False, "error": short(str(e), 220)}
                if path == "/ladd":
                    blog(f"  Couldn't add '{val('title')}': {short(str(e), 200)}")
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/dismiss" and key_ok(self.path):'''

for blk in (HTML, CSS, JS, POST):
    assert "\\" not in blk.replace("\\n", ""), "unexpected backslash"
    assert "\'\'\'" not in blk

EDITS = [
    ("the page section marker", "# ------------------------------------------------------------ page\n",
     NEW_CODE + "# ------------------------------------------------------------ page\n"),
    ("the page style end", '</style></head><body><div class="wrap">', CSS + '</style></head><body><div class="wrap">'),
    ("the waiting box", '<div id="wait" class="wait" style="display:none">', HTML + '<div id="wait" class="wait" style="display:none">'),
    ("the poll function", "async function poll(){", JS),
    ("the /dismiss handler", '        if path == "/dismiss" and key_ok(self.path):', POST),
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
