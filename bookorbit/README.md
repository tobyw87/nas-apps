# bookorbit

[BookOrbit](https://github.com/bookorbit/bookorbit) for browsing and reading the books-tools
Library in the browser, Send-to-Kindle by Gmail, fixing metadata, and OPDS for iOS reading apps.

It only **reads** the Library. books-tools does everything upstream (seedbox pull, MOBI→EPUB,
duplicates, filing into `Library/Author/Title/`), so this setup has no Book Dock, download
client, seedbox or indexer integration. Audiobooks stay in Audiobookshelf.

## Where things are

| What | Where |
|---|---|
| This app | `/volume1/docker/bookorbit` |
| Settings + secrets | `.env` (made by `setup.sh`, never committed) |
| BookOrbit data (covers, cache) | `data/app` |
| Database (Postgres + pgvector) | `data/postgres` |
| Library (read-only at `/books`) | `/volume1/NAS/Archive/5_Books/Library` |
| Web UI / OPDS | `http://treebeard.taileefdd9.ts.net:3080` (over Tailscale) |

## Read-only Library

The Library is mounted `:ro`, so BookOrbit can't rename, move or delete anything books-tools
manages. Metadata edits are stored in BookOrbit's database, not in the book files. These
features need write access and won't work as set up: writing metadata/covers into the book
files, bulk rename/move, uploads, and deleting books from disk. Leave "write to files" off in
the library settings.

## Commands

    cd /volume1/docker/bookorbit
    sudo docker compose up -d          # start / apply .env changes
    sudo docker compose logs -f app    # watch the app
    sudo docker compose ps
    sudo docker compose down           # stop (data is kept)

## Update

Change `APP_IMAGE` in `.env` to the new version, then `sudo docker compose pull && sudo docker compose up -d`.
Back up `data/` first; the database migrates on start.
