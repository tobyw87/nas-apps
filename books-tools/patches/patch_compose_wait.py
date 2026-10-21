#!/usr/bin/env python3
"""Add WAIT_MIN_AGE_MINUTES=10 and WAIT_REFRESH_SECONDS=120 to the books-button service in docker-compose.yml,
so downloads that LazyLibrarian couldn't file show up under "Waiting to be filed" after 10 minutes (was 30)
and the list re-scans every 2 minutes (was 10).

Usage: python3 patch_compose_wait.py /path/to/docker-compose.yml
Makes a backup, refuses to run twice, changes nothing if it can't find exactly one books-button service.
"""
import os
import re
import shutil
import sys
import time

path = sys.argv[1]
text = open(path, encoding="utf-8", newline="").read()
if "WAIT_MIN_AGE_MINUTES" in text:
    sys.exit("Already patched - nothing to do.")
nl = "\r\n" if "\r\n" in text else "\n"
lines = text.replace("\r\n", "\n").split("\n")

# service blocks: lines indented by exactly the first level under "services:"
start = next((i for i, l in enumerate(lines) if re.match(r"services:\s*$", l)), None)
if start is None:
    sys.exit("Can't patch: no 'services:' line found. Nothing was changed.")
indent = next((len(l) - len(l.lstrip()) for l in lines[start + 1:] if l.strip() and not l.lstrip().startswith("#")), 2)
heads = [i for i in range(start + 1, len(lines))
         if re.match(rf"^ {{{indent}}}[A-Za-z0-9_.-]+:\s*$", lines[i])]
blocks = []
for n, h in enumerate(heads):
    end = heads[n + 1] if n + 1 < len(heads) else len(lines)
    blocks.append((h, end))
hits = [(h, e) for h, e in blocks
        if any(re.search(r"container_name:\s*[\"']?books-button[\"']?\s*$", l) or "/app/button.py" in l for l in lines[h:e])]
if len(hits) != 1:
    sys.exit(f"Can't patch: expected exactly one books-button service, found {len(hits)}. Nothing was changed.")
h, e = hits[0]
name = lines[h].strip().rstrip(":")
env = next((i for i in range(h, e) if re.match(r"^\s*environment:\s*$", lines[i])), None)
if env is None:
    sys.exit(f"Can't patch: service '{name}' has no 'environment:' block in list or map form. Nothing was changed.")
env_indent = len(lines[env]) - len(lines[env].lstrip())
first = next((l for l in lines[env + 1:e] if l.strip() and not l.lstrip().startswith("#")), "")
item_indent = len(first) - len(first.lstrip())
if item_indent <= env_indent:
    sys.exit("Can't patch: the environment block looks empty or unusual. Nothing was changed.")
pad = " " * item_indent
if first.lstrip().startswith("-"):
    new = [f"{pad}- WAIT_MIN_AGE_MINUTES=10", f"{pad}- WAIT_REFRESH_SECONDS=120"]
else:
    new = [f'{pad}WAIT_MIN_AGE_MINUTES: "10"', f'{pad}WAIT_REFRESH_SECONDS: "120"']

backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(path, backup)
lines[env + 1:env + 1] = new
open(path, "w", encoding="utf-8", newline="").write(nl.join(lines))
print(f"Patched {path} (backup: {backup})")
print(f"Next: cd {os.path.dirname(os.path.abspath(path))} && sudo docker compose up -d {name}")
