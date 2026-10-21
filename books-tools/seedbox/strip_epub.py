#!/usr/bin/env python3
"""Write a copy of an EPUB without scripts and embedded fonts (the original is never touched).

Usage: python3 strip_epub.py in.epub out.epub
Removes: js/*, *.js, fonts/*, *.ttf/*.otf/*.woff, their manifest items, <script> tags and @font-face rules.
Keeps the mimetype first and stored, as EPUB requires.
"""
import re
import sys
import zipfile

src, dst = sys.argv[1], sys.argv[2]
DROP = re.compile(r"(^|/)(js|fonts)/|\.(js|ttf|otf|woff2?)$", re.I)
zin = zipfile.ZipFile(src)
names = [i for i in zin.infolist() if not i.filename.endswith("/")]
with zipfile.ZipFile(dst, "w") as zout:
    zout.writestr(zipfile.ZipInfo("mimetype"), zin.read("mimetype"), compress_type=zipfile.ZIP_STORED)
    dropped = []
    for i in names:
        n = i.filename
        if n == "mimetype":
            continue
        if DROP.search(n):
            dropped.append(n)
            continue
        data = zin.read(n)
        if n.lower().endswith(".opf"):
            t = data.decode("utf-8")
            t = re.sub(r"<item\b[^>]*\bhref=\"[^\"]*(?:\.js|\.ttf|\.otf|\.woff2?)\"[^>]*/>\s*", "", t, flags=re.I)
            t = re.sub(r"<item\b[^>]*\bhref=\"(?:[^\"]*/)?(?:js|fonts)/[^\"]*\"[^>]*/>\s*", "", t, flags=re.I)
            data = t.encode("utf-8")
        elif n.lower().endswith((".xhtml", ".html", ".htm")):
            t = data.decode("utf-8")
            t = re.sub(r"<script\b[^>]*>.*?</script>\s*", "", t, flags=re.S | re.I)
            t = re.sub(r"<script\b[^>]*/>\s*", "", t, flags=re.I)
            data = t.encode("utf-8")
        elif n.lower().endswith(".css"):
            t = data.decode("utf-8")
            t = re.sub(r"@font-face\s*\{[^}]*\}", "", t, flags=re.I)
            data = t.encode("utf-8")
        zout.writestr(n, data, compress_type=zipfile.ZIP_DEFLATED)
print("removed:", ", ".join(dropped) or "nothing")
