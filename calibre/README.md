# Calibre

Calibre desktop in the browser, to build the EPUB library. Calibre's own layout is
`Author/Title (id)/Title - Author.epub` plus `metadata.db`, which is what BookOrbit then reads.

Install (on the NAS): copy this folder to `/volume1/docker/calibre`, then
`sudo ./setup.sh && sudo docker compose up -d` and open `http://<nas-ip>:8085`. Stop it with
`sudo docker compose down` when you're not using it (~1 GB RAM).

Plan: build a **new** Calibre library at `5_Books/EPUB Archive New` (in the GUI: Calibre library →
choose `/books/EPUB Archive New`), import the books, check it, then swap it for `EPUB Archive`
(old one kept in `Archive/_EPUB_Archive_old` until you delete it). Never run two Calibre processes
against one library at the same time (the GUI plus `calibredb`).
