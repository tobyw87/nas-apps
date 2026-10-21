#!/usr/bin/env python3
"""Patch mac-audio-cast.py: while casting, switch the Mac's output to the Multi-Output Device (speakers + BlackHole,
speakers as the clock) instead of BlackHole alone. BlackHole alone has no clock, which gave sped-up, choppy sound.
The capture still reads BlackHole. Change the device with "castOutput" in the program's config file, if it has one.

Usage: python3 patch_cast_output.py ~/mac-audio-cast/mac-audio-cast.py
Makes a backup, refuses to run twice, changes nothing if the file isn't what it expects,
restores the backup if the result doesn't compile.
"""
import py_compile
import shutil
import sys
import time

path = sys.argv[1]
src = open(path, encoding="utf-8", newline="").read()
if "castOutput" in src:
    sys.exit("Already patched - nothing to do.")

EDITS = [
    ("the restoreDevice default", '    "restoreDevice": "",\n',
     '    "restoreDevice": "",\n    "castOutput": "Multi-Output Device",\n'),
    ("set_output()", 'def set_output(name: str) -> None:\n    run([cfg["switchaudio"], "-s", name, "-t", "output"])\n',
     'def set_output(name: str) -> None:\n    run([cfg["switchaudio"], "-s", name, "-t", "output"])\n\n\n'
     'def cast_output() -> str:\n    """The output used while casting: the Multi-Output Device by default, else BlackHole alone."""\n'
     '    return cfg.get("castOutput") or cfg["blackhole"]\n'),
    ("the restore check", '    if cur == cfg["blackhole"]:',
     '    if cur in (cfg["blackhole"], cast_output()):'),
    ("the start check", '    prev = current_output()\n    if prev == cfg["blackhole"]:\n',
     '    if cast_output() not in outputs():\n'
     '        raise RuntimeError(f"No output called \'{cast_output()}\'. Make it in Audio MIDI Setup (speakers + BlackHole 2ch).")\n'
     '    prev = current_output()\n    if prev == cfg["blackhole"]:\n'),
    ("the output switch", '    set_output(cfg["blackhole"])\n    log(f"Mac output switched to \'{cfg[\'blackhole\']}\' (was \'{prev}\').")\n',
     '    set_output(cast_output())\n    log(f"Mac output switched to \'{cast_output()}\' (was \'{prev}\').")\n'),
]
for label, old, _ in EDITS:
    n = src.count(old)
    if n != 1:
        sys.exit(f"Can't patch: expected exactly one copy of {label}, found {n}. Nothing was changed.")

backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(path, backup)
for label, old, new in EDITS:
    src = src.replace(old, new, 1)
open(path, "w", encoding="utf-8", newline="").write(src)
try:
    py_compile.compile(path, doraise=True)
except py_compile.PyCompileError as e:
    shutil.copy2(backup, path)
    sys.exit(f"The patched file didn't compile, so I restored the original: {e}")
print(f"Patched {path} (backup: {backup})")
