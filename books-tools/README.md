# books-tools

Organizes `5_Books` into three folders, each its own BookOrbit library:

| Folder | Holds |
|---|---|
| `5_Books/EPUB Archive` | `Author/Title/Title - Author.epub` (MOBI/AZW/LIT/... converted to EPUB) |
| `5_Books/Textbooks` | `Author/Title/Title - Author.pdf/.epub`, Springer downloads in `Springer/` |
| `5_Books/Manga` | `Series/Series v01.cbz` (`books.sh manga`) |

It also pulls ebooks one-way from the Ultra.cc seedbox.

## Where things are

| What | Where (default) |
|---|---|
| This tool | `/volume1/docker/books-tools` |
| Your existing archive (read only) | `ARCHIVE_DIR` in `.env`, e.g. `/volume1/NAS/Archive/5_Books` (Z:\Archive\5_Books) |
| The organized folders | `ARCHIVE_DIR/EPUB Archive`, `Textbooks`, `Manga` |
| Ebooks copied down from the seedbox | `/volume1/NAS/Archive/_Seedbox_Mirror/Books` (outside 5_Books) |
| Reports and pull logs | `LOGS_DIR`, e.g. `/volume1/NAS/Logs/books` |
| Seedbox login + memory of what's been done | `data/` (keep it; deleting it only means the next run re-reads everything) |

## Rules it follows

- Never deletes anything (except copies of non-textbook PDFs, via `textbooks --apply`
  after you've checked the plan), and never changes your original files. MOBIs are left where they are;
  EPUB Archive just gets the EPUB. Remove the old folders yourself once you're happy.
- Only ever reads from the seedbox (`rclone copy` seedbox → NAS). Nothing goes up.
- Only ebook files come down (EPUB, MOBI, AZW, AZW3, PRC, PDF). PDFs inside audiobook folders
  (booklets) are skipped.
- One copy per book: EPUB beats MOBI/AZW3 (converted) beats PDF. Exact copies are skipped.
  A PDF is only kept when there's no EPUB of that book, and only if it's a textbook.
- Textbooks (PDF or EPUB) go to `Textbooks/Author/Title/` (Springer downloads to
  `Textbooks/Springer/`). A book counts as a textbook by its
  file name (Springer `YYYY_Book_`, ISBN), a folder named Springer/Textbooks, its title
  ("Introduction to", "Handbook of", "3rd Edition"...), an academic/technical publisher
  (Springer, Wiley, Elsevier, CRC, O'Reilly, university presses...) or subject tags.
- PDFs that aren't textbooks are not kept (`skip-pdf` in the report).
- Books with no usable author/title go to `EPUB Archive/_Unsorted` (or `Textbooks/_Unsorted`) so nothing is lost.
- Files that won't convert (usually DRM) are listed as `error` in the report and in `status`.

## Commands

    sudo /volume1/docker/books-tools/books.sh organize            # dry run: writes a report, changes nothing
    sudo /volume1/docker/books-tools/books.sh organize --apply    # file new books
    sudo /volume1/docker/books-tools/books.sh pull                # copy new ebooks down from the seedbox
    sudo /volume1/docker/books-tools/books.sh run                 # pull + organize --apply (scheduled)
    sudo /volume1/docker/books-tools/books.sh status
    sudo /volume1/docker/books-tools/books.sh organize --apply --retry-errors

Long first run in the background:

    cd /volume1/docker/books-tools && sudo docker compose run -d --rm books organize --apply
    sudo docker ps            # find the container name
    sudo docker logs -f <name>

## Archives (.rar / .zip)

Many archives in the old collection are whole-author bundles (one `.rar` with 20+ novels in
`.lit`/`.pdf`/`.rtf`). Extract them first, then organize as usual:

    sudo /volume1/docker/books-tools/books.sh unpack           # -> 5_Books/_Unpacked/
    sudo /volume1/docker/books-tools/books.sh organize         # dry run: check the report
    sudo /volume1/docker/books-tools/books.sh organize --apply

`.lit`, `.pdb`, `.rtf` and `.txt` books are converted to EPUB (the best copy of each book wins:
EPUB > MOBI/LIT/PDB > RTF > PDF > TXT). `.doc` can't be converted and is left alone.

## One-time move to the three-folder layout (Oct 2026)

    sudo /volume1/docker/books-tools/migrate.sh

Renames `5_Books/Library` to `5_Books/EPUB Archive`, moves `5_Books/_Seedbox_Mirror` to
`Archive/_Seedbox_Mirror/Books`, creates `Textbooks` and `Manga`, and updates the paths in
`data/state.db`. Same volume, so it's instant. Other commands refuse to run until it's done.

## Manga

    sudo /volume1/docker/books-tools/books.sh manga        # plan + report, moves nothing
    sudo /volume1/docker/books-tools/cleanup-apply.sh '<plan file it prints>'

Reads `Archive/Comics` and `Archive/6_Manga` (read-only; `MANGA_SOURCES` in `.env` to change,
colon-separated, no spaces) and files every CBZ/CBR/CB7/PDF/EPUB as
`Manga/<Series>/<Series> v01.cbz` (or `c001` for chapters). The series comes from ComicInfo.xml,
calibre's `metadata.opf`, the EPUB's own metadata, or the file name — never the author.
One copy per volume: a comic archive beats PDF/EPUB, then the larger file wins; the others go to
`Archive/_Manga_removed/duplicates`. Zips/rars of page images become `.cbz`/`.cbr`; anything odd
(mixed archives, folders of loose pages) goes to `Manga/_To check`. What's left in the source
folders (covers, `metadata.opf`, empty folders) goes to `Archive/_Manga_removed/_leftovers`, and
the source folders are removed. Delete `_Manga_removed` yourself once you've checked it.


### Series grouping in BookOrbit

    sudo /volume1/docker/books-tools/books.sh manga-tag            # dry run
    sudo /volume1/docker/books-tools/books.sh manga-tag --apply    # then rescan Manga

BookOrbit's "Collapse series" only uses ComicInfo.xml inside each CBZ, not folder names. This
writes Series (the `Manga/<Series>` folder) and Number (`vNN`/`cNNN` from the file name; Title for
unnumbered books) into each CBZ. Pages aren't touched: the XML is appended (the archive's index
at the end is journalled first, so an interrupted run is rolled back on the next start); a CBZ
with an existing ComicInfo.xml keeps its other fields and is rewritten to a temp file, checked,
and renamed. Changed series folders get a fresh mtime (BookOrbit skips unchanged folders).
PDFs/EPUBs/CBRs in Manga are left alone.

### CBR → CBZ

    sudo /volume1/docker/books-tools/books.sh cbr2cbz            # dry run: lists the .cbr files
    sudo /volume1/docker/books-tools/books.sh cbr2cbz --apply    # repack each as .cbz
    sudo /volume1/docker/books-tools/cleanup-apply.sh '<plan file it prints>'   # .cbr originals out

BookOrbit reads a whole CBR into memory several times over, so big ones get it killed at its
memory cap; CBZs it reads through the ZIP index. Each .cbr in `Manga/` is extracted (unar) and
written as a stored .cbz next to it (same images, no recompression), checked (same files and
sizes, CRCs), then renamed into place. The originals are only moved, to
`Archive/_Manga_removed/cbr-originals`, for you to delete. Rescan Manga after cleanup-apply.

## Textbooks already in EPUB Archive

    sudo /volume1/docker/books-tools/books.sh textbooks            # plan, changes nothing
    sudo /volume1/docker/books-tools/books.sh textbooks --apply '<plan file it prints>'

The plan (`textbooks-plan-*.tsv` in the logs folder) lists every textbook it will move into
`Textbooks` (also everything in the old `_Springer Textbooks`) and every non-textbook PDF it
will delete, with the reason. Edit it first: delete a line to leave a file
alone, or swap `remove`/`textbook`. Deleting only removes the copy in EPUB Archive; the original stays
in the archive until cleanup moves it to `_5_Books_removed/non-textbook-pdfs`.

## Cleanup: leave only EPUB Archive, Textbooks and Manga

After `organize --apply` has finished, this empties `5_Books` of everything except the three
folders. It moves files; it never deletes them.

    sudo /volume1/docker/books-tools/books.sh cleanup        # plan + report, moves nothing
    sudo /volume1/docker/books-tools/cleanup-apply.sh '<plan file it prints>'

| What | Goes to |
|---|---|
| Originals already in EPUB Archive or Textbooks (copied, converted, identical, duplicate) | `Archive/_5_Books_removed/duplicates` |
| Springer front/back matter, reference entries | `Archive/_5_Books_removed/springer-fragments` |
| PDFs that aren't textbooks | `Archive/_5_Books_removed/non-textbook-pdfs` |
| Springer chapters (kept, not in 5_Books) | `Archive/Springer Chapters` |
| Books that failed to convert (only copy) | `EPUB Archive/_Unconverted` |
| Filed copy missing, or the kept version failed | `Archive/_5_Books_removed/_review` |
| Everything else (non-ebooks, emptied folders) | `Archive/_5_Books_removed/_leftovers` |

Both are on the same volume, so moving is instant and frees no space until you delete
`_5_Books_removed` yourself. Look in `_review` and `_leftovers` first. Run `--retry-errors`
before cleanup if you want another go at failed conversions; afterwards they're in EPUB Archive.

## Scheduled task

DSM → Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script.
User: root. Schedule: every 6 hours (or daily). Script:

    /volume1/docker/books-tools/books.sh run >> /volume1/NAS/Logs/books/scheduled.log 2>&1

## Update

Paste the new installer in the NAS terminal, then
`cd /volume1/docker/books-tools && sudo docker compose build`. Your `.env` and `data/` are kept.
