# nas-apps — notes for Claude

Code for self-hosted apps running on my Synology NAS **Treebeard** (DSM 7, Docker via Container
Manager, Tailscale at `treebeard.taileefdd9.ts.net`, SSH user `tobyw87`, ~5.6 GB RAM, slow disks).
I use Windows (PowerShell) and a Mac to SSH in.

## How apps are installed on the NAS (follow this exactly)
- Each app lives in `/volume1/docker/<app>/` with a `Dockerfile` + `docker-compose.yml`, settings in `.env`,
  state in `data/`. Run with `sudo docker compose ...`.
- You can't reach the NAS. Deliver changes as a **paste-in shell block**: base64 tar of the changed
  files extracted into `/volume1/docker/<app>`, then a `sha256sum -c` check, then the exact
  `sudo docker compose build` / `up -d` command. Never overwrite an existing `.env` or `data/`.
- Scripts go in `NAS/Scripts`, logs in `/volume1/NAS/Logs/<app>`, schedules via DSM Task Scheduler (root).
- Never delete files on the NAS without asking me first.
- **Never commit secrets**: no `.env`, `data/`, `*.db`, `*.conf`, credentials, tokens.

## books-tools (this repo: `books-tools/`)
Organizes my ebook archive and pulls ebooks one-way from my Ultra.cc seedbox.
- Archive: `/volume1/NAS/Archive/5_Books` (Windows `Z:\Archive\5_Books`), 18,326 ebook files.
- Layout (my request, Oct 8): `5_Books` holds only three folders, each a BookOrbit library:
  `EPUB Archive` (was `Library`; `Author/Title/Title - Author.epub`, authors "First Last"),
  `Textbooks` (PDF + EPUB, `Author/Title/...`, Springer in `Springer/`) and `Manga`
  (`Series/Series v01.cbz` — series name, never author). The seedbox mirror lives outside 5_Books
  at `Archive/_Seedbox_Mirror/Books`. `migrate.sh` did the one-time move (Library → EPUB Archive,
  mirror out, state.db paths rewritten). `books.sh manga` + `cleanup-apply.sh` move
  `Archive/Comics` + `Archive/6_Manga` into `Manga/` with no duplicates (comic archive over
  PDF/EPUB, then larger file; others to `Archive/_Manga_removed`, which I delete myself).
  MOBI/AZW/AZW3 → converted to EPUB (MOBI not kept in library). PDFs kept only if no EPUB
  and only if a textbook (my request, Oct 8: other PDFs aren't relevant to me). Textbooks (PDF
  or EPUB) go to `5_Books/Textbooks/`; `books.sh textbooks` sorts what's already in EPUB Archive
  (plan, then `--apply`, which deletes copies of non-textbook PDFs).
  Originals are never modified. `books.sh cleanup` + `cleanup-apply.sh` (my request, Oct 8) move
  everything except the three folders out of `5_Books`: originals already in the
  Library and Springer fragments to `Archive/_5_Books_removed` (I delete it myself), Springer
  chapters to `Archive/Springer Chapters`, failed conversions to `EPUB Archive/_Unconverted`.
  Keep the mirror intact: the pull compares against it.
- Springer downloads in `5_Books/!_Books_old/Springer Ebooks`: `_Chapter_`/`_Bookmatter_` files are
  skipped (left in archive); whole `YYYY_Book_Title.pdf` books go to `Textbooks/Springer/`
  (titles truncated by Springer — fix later via BookOrbit metadata lookup).
- Seedbox pull: `rclone copy` (never sync/upload) from `/home/mynock42/media/ABS` (ebooks and
  audiobooks mixed) into `Archive/_Seedbox_Mirror/Books`, ebook extensions only, skipping PDFs inside
  audiobook folders. Login saved in `data/rclone.conf` on the NAS.
- State in `data/state.db` (SQLite) so runs resume; reports to `/volume1/NAS/Logs/books`.
- `docker-compose.yml` must keep `init: true` (Calibre's ebook-meta leaves zombie processes otherwise).
- The archive's 817 `.rar` + 1,994 `.zip` are author bundles (e.g. ~20 Philip K. Dick novels as
  .lit/.pdf/.rtf), not single books. `books.sh unpack` extracts them (via `unar`) into
  `5_Books/_Unpacked/`; organize then treats them like any other originals. Also handled now:
  `.lit`, `.pdb`, `.rtf`, `.txt` (converted to EPUB; rtf/txt under 20 KB ignored). `.doc` isn't
  convertible by Calibre and stays a leftover. The seedbox pull still only takes PULL_EXTS.
- Commands: `sudo ./books.sh organize` (dry run), `organize --apply`, `pull`, `run`, `status`,
  `unpack`, `textbooks [--apply PLAN]`, `manga`, `manga-tag [--apply]`, `cbr2cbz [--apply]`, `cleanup`; one-time `migrate.sh`.
- `cbr2cbz` (Oct 9): BookOrbit loads a whole CBR into memory several times (node-unrar-js), so
  700 MB+ CBRs got it OOM-killed at its 2.5 GB cap. Manga's .cbr files are repacked as stored
  .cbz (checked, then renamed); the .cbr originals go to `Archive/_Manga_removed/cbr-originals`
  via cleanup-apply.sh. CBZs are read through the ZIP index, so size doesn't matter for them.
- `manga-tag` (Oct 9): BookOrbit groups series only from ComicInfo.xml (Series/Number), never
  folders, so this writes them from `Manga/<Series>/` + `vNN` (append, journalled; rewrite if one exists).

## Status (Oct 7, 2026)
- Dry run result: convert 4,679, copy 1,337 (incl. 333 Springer books), skip-part 9,266,
  skip-identical 2,813, skip-duplicate 231, 6 unsorted → ~6,016 books in the library.
- `organize --apply` finished Oct 8 ~3 am CT: 5,996 in Library (4,659 converted, 1,337 copied),
  20 errors (16 Calibre SplitError, 2 timeouts, 2 corrupt MOBIs; none DRM). Conversion now
  retries a SplitError with `--flow-size 0` and allows 2 h per book (`CONVERT_TIMEOUT`).

## Next up
1. Retry done Oct 8: 18 of 20 converted (6,014 in Library; 2 corrupt MOBIs left). Now:
   `unpack` → `organize` (dry run, check) → `organize --apply` → `cleanup` → `cleanup-apply.sh`,
   then schedule `books.sh run` every 6 h in DSM Task Scheduler.
2. ~~Install BookOrbit~~ done Oct 8: `bookorbit/`, https://bookorbit.taileefdd9.ts.net.
   Then run the cleanup above.
3. Facebook photo export (JSON) → merge into photo archive
   `/volume1/NAS/Archive/1_Photo_Archive/Archive`, restoring dates from the JSON, only adding
   missing photos (like the existing `takeout-tools` Google Takeout merge). Never remove photos.
4. Add my other Claude-built apps to this repo (chi-eats, cta-go, finance-dashboard,
   pipeline-status, stock-app, takeout-tools) — code only, no secrets.
