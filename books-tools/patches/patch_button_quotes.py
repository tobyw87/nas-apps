#!/usr/bin/env python3
"""Patch button.py: show a famous book quote on the first line of the terminal whenever the pipeline is idle.

A new quote is picked each time the pipeline goes from busy to idle (and when the page is opened).
While it runs, or while it is asking you a question, the quote hides and the log shows as before.
To change the quotes afterwards, edit the QUOTES list in this file and run it against a fresh copy,
or edit the line starting 'const QUOTES=' in button.py.

Usage: python3 patch_button_quotes.py /path/to/button.py
Makes a backup, refuses to run twice, changes nothing if the file isn't what it expects,
restores the backup if the result doesn't compile.
"""
import json
import py_compile
import shutil
import sys
import time

path = sys.argv[1]
src = open(path, encoding="utf-8", newline="").read()
if 'id="qline"' in src:
    sys.exit("Already patched - nothing to do.")
CRLF = "\r\n" in src
if CRLF:
    src = src.replace("\r\n", "\n")

# (quote, source). Plain ASCII, no backslashes or double quotes: the page lives inside a Python string.
QUOTES = [
    ("It is a truth universally acknowledged, that a single man in possession of a good fortune, must be in want of a wife.", "Jane Austen, Pride and Prejudice"),
    ("Call me Ishmael.", "Herman Melville, Moby-Dick"),
    ("All happy families are alike; each unhappy family is unhappy in its own way.", "Leo Tolstoy, Anna Karenina"),
    ("It was the best of times, it was the worst of times.", "Charles Dickens, A Tale of Two Cities"),
    ("In a hole in the ground there lived a hobbit.", "J.R.R. Tolkien, The Hobbit"),
    ("Not all those who wander are lost.", "J.R.R. Tolkien, The Fellowship of the Ring"),
    ("Many that live deserve death. And some that die deserve life.", "J.R.R. Tolkien, The Fellowship of the Ring"),
    ("So we beat on, boats against the current, borne back ceaselessly into the past.", "F. Scott Fitzgerald, The Great Gatsby"),
    ("It does not do to dwell on dreams and forget to live.", "J.K. Rowling, Harry Potter and the Philosopher's Stone"),
    ("It is our choices, Harry, that show what we truly are, far more than our abilities.", "J.K. Rowling, Harry Potter and the Chamber of Secrets"),
    ("Words are, in my not-so-humble opinion, our most inexhaustible source of magic.", "J.K. Rowling, Harry Potter and the Deathly Hallows"),
    ("Whatever our souls are made of, his and mine are the same.", "Emily Bronte, Wuthering Heights"),
    ("I am no bird; and no net ensnares me.", "Charlotte Bronte, Jane Eyre"),
    ("I have always imagined that Paradise will be a kind of library.", "Jorge Luis Borges"),
    ("A reader lives a thousand lives before he dies. The man who never reads lives only one.", "George R.R. Martin, A Dance with Dragons"),
    ("Fear is the mind-killer.", "Frank Herbert, Dune"),
    ("Don't Panic.", "Douglas Adams, The Hitchhiker's Guide to the Galaxy"),
    ("Space is big. You just won't believe how vastly, hugely, mind-bogglingly big it is.", "Douglas Adams, The Hitchhiker's Guide to the Galaxy"),
    ("All animals are equal, but some animals are more equal than others.", "George Orwell, Animal Farm"),
    ("It was a bright cold day in April, and the clocks were striking thirteen.", "George Orwell, Nineteen Eighty-Four"),
    ("The past is a foreign country; they do things differently there.", "L.P. Hartley, The Go-Between"),
    ("We are what we pretend to be, so we must be careful about what we pretend to be.", "Kurt Vonnegut, Mother Night"),
    ("So it goes.", "Kurt Vonnegut, Slaughterhouse-Five"),
    ("Ships at a distance have every man's wish on board.", "Zora Neale Hurston, Their Eyes Were Watching God"),
    ("It is only with the heart that one can see rightly; what is essential is invisible to the eye.", "Antoine de Saint-Exupery, The Little Prince"),
    ("We must cultivate our garden.", "Voltaire, Candide"),
    ("I took a deep breath and listened to the old brag of my heart: I am, I am, I am.", "Sylvia Plath, The Bell Jar"),
    ("The world breaks everyone, and afterward, many are strong at the broken places.", "Ernest Hemingway, A Farewell to Arms"),
    ("Until I feared I would lose it, I never loved to read. One does not love breathing.", "Harper Lee, To Kill a Mockingbird"),
    ("You never really understand a person until you consider things from his point of view.", "Harper Lee, To Kill a Mockingbird"),
    ("The truth is rarely pure and never simple.", "Oscar Wilde, The Importance of Being Earnest"),
    ("Whoever fights monsters should see to it that in the process he does not become a monster.", "Friedrich Nietzsche, Beyond Good and Evil"),
]
for q, a in QUOTES:
    text = q + a
    assert text.isascii() and not any(c in text for c in '\\"`$%{}') and "'''" not in text, text

CSS = ("#qline{flex:none;margin:0 0 6px;font:italic 13px/1.4 ui-monospace,Menlo,Consolas,monospace;"
       "opacity:.75;word-break:break-word}\n")
HTML = '<div id="qline" style="display:none"></div>\n<pre id="o">__LINES__</pre>'
JS = ("const QUOTES=" + json.dumps([[q, a] for q, a in QUOTES]) + """;
let qIdle=false,qLast=-1;
function quoteTick(st){
  const el=document.getElementById('qline');
  const idle=!st.running&&!st.ask;
  if(idle&&!qIdle){let i;do{i=Math.floor(Math.random()*QUOTES.length);}while(i===qLast&&QUOTES.length>1);qLast=i;
    el.textContent='# "'+QUOTES[i][0]+'" - '+QUOTES[i][1];}
  qIdle=idle;el.style.display=idle?'':'none';
}
async function poll(){""")
assert "\\" not in JS.replace('\\"', ""), "unexpected backslash"

EDITS = [
    ("the page style end", "</style></head><body>", CSS + "</style></head><body>"),
    ("the log pre element", '<pre id="o">__LINES__</pre>', HTML),
    ("the poll function", "async function poll(){", JS),
    ("the log label script line", 'document.getElementById("l").textContent=st.log||"no log yet";',
     'document.getElementById("l").textContent=st.log||"no log yet";quoteTick(st);'),
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
