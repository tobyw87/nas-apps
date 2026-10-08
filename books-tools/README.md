# books-tools

Organizes the ebook archive into `Library/Author/Title/Title - Author.epub`, converts
MOBI/AZW/AZW3 to EPUB, files textbooks under `Library/_Textbooks`, and pulls ebooks one-way from the Ultra.cc seedbox.

## Where things are

| What | Where (default) |
|---|---|
| This tool | `/volume1/docker/books-tools` |
| Your existing archive (read only) | `ARCHIVE_DIR` in `.env`, e.g. `/volume1/NAS/Archive/5_Books` (Z:\Archive\5_Books) |
| The new organized library | `ARCHIVE_DIR/Library` |
| Ebooks copied down from the seedbox | `ARCHIVE_DIR/_Seedbox_Mirror` |
| Reports and pull logs | `LOGS_DIR`, e.g. `/volume1/NAS/Logs/books` |
| Seedbox login + memory of what's been done | `data/` (keep it; deleting it only means the next run re-reads everything) |

## Rules it follows

- Never deletes anything (except Library copies of non-textbook PDFs, via `textbooks --apply`
  after you've checked the plan), and never changes your original files. MOBIs are left where they are;
  the Library just gets the EPUB. Remove the old folders yourself once you're happy.
- Only ever reads from the seedbox (`rclone copy` seedbox → NAS). Nothing goes up.
- Only ebook files come down (EPUB, MOBI, AZW, AZW3, PRC, PDF). PDFs inside audiobook folders
  (booklets) are skipped.
- One copy per book: EPUB beats MOBI/AZW3 (converted) beats PDF. Exact copies are skipped.
  A PDF is only kept when there's no EPUB of that book, and only if it's a textbook.
- Textbooks (PDF or EPUB) go to `Library/_Textbooks/Author/Title/` (Springer downloads to
  `_Textbooks/Springer/`), a separate BookOrbit library. A book counts as a textbook by its
  file name (Springer `YYYY_Book_`, ISBN), a folder named Springer/Textbooks, its title
  ("Introduction to", "Handbook of", "3rd Edition"...), an academic/technical publisher
  (Springer, Wiley, Elsevier, CRC, O'Reilly, university presses...) or subject tags.
- PDFs that aren't textbooks are not put in the Library (`skip-pdf` in the report).
- Books with no usable author/title go to `Library/_Unsorted` so nothing is lost.
- Files that won't convert (usually DRM) are listed as `error` in the report and in `status`.

## Commands

    sudo /volume1/docker/books-tools/books.sh organize            # dry run: writes a report, changes nothing
    sudo /volume1/docker/books-tools/books.sh organize --apply    # build/update the Library
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

## Textbooks already in the Library

    sudo /volume1/docker/books-tools/books.sh textbooks            # plan, changes nothing
    sudo /volume1/docker/books-tools/books.sh textbooks --apply '<plan file it prints>'

The plan (`textbooks-plan-*.tsv` in the logs folder) lists every textbook it will move into
`_Textbooks` (also everything in the old `_Springer Textbooks`) and every non-textbook PDF it
will delete from the Library, with the reason. Edit it first: delete a line to leave a file
alone, or swap `remove`/`textbook`. Deleting only removes the Library copy; the original stays
in the archive until cleanup moves it to `_5_Books_removed/non-textbook-pdfs`.

## Cleanup: leave only the Library and the mirror

After `organize --apply` has finished, this empties the archive folder of everything except
`Library` and `_Seedbox_Mirror`. It moves files; it never deletes them.

    sudo /volume1/docker/books-tools/books.sh cleanup        # plan + report, moves nothing
    sudo /volume1/docker/books-tools/cleanup-apply.sh '<plan file it prints>'

| What | Goes to |
|---|---|
| Originals already in the Library (copied, converted, identical, duplicate) | `Archive/_5_Books_removed/duplicates` |
| Springer front/back matter, reference entries | `Archive/_5_Books_removed/springer-fragments` |
| PDFs that aren't textbooks | `Archive/_5_Books_removed/non-textbook-pdfs` |
| Springer chapters (kept, not in the Library) | `Archive/Springer Chapters` |
| Books that failed to convert (only copy) | `Library/_Unconverted` |
| Library copy missing, or the kept version failed | `Archive/_5_Books_removed/_review` |
| Everything else (non-ebooks, emptied folders) | `Archive/_5_Books_removed/_leftovers` |

Both are on the same volume, so moving is instant and frees no space until you delete
`_5_Books_removed` yourself. Look in `_review` and `_leftovers` first. Run `--retry-errors`
before cleanup if you want another go at failed conversions; afterwards they're in the Library.

## Scheduled task

DSM → Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script.
User: root. Schedule: every 6 hours (or daily). Script:

    /volume1/docker/books-tools/books.sh run >> /volume1/NAS/Logs/books/scheduled.log 2>&1

## Update

Paste the new installer in the NAS terminal, then
`cd /volume1/docker/books-tools && sudo docker compose build`. Your `.env` and `data/` are kept.
