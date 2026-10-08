# bookorbit

[BookOrbit](https://github.com/bookorbit/bookorbit) for browsing and reading the books-tools
Library in the browser, Send-to-Kindle by Gmail, fixing metadata, and OPDS for iOS reading apps.

books-tools does everything upstream (seedbox pull, MOBI→EPUB, duplicates, filing into
`Library/Author/Title/`), so this setup has no Book Dock, download client, seedbox or indexer
integration. Audiobooks stay in Audiobookshelf.

## Where things are

| What | Where |
|---|---|
| This app | `/volume1/docker/bookorbit` |
| Settings + secrets | `.env` (made by `setup.sh`, never committed) |
| BookOrbit data (covers, cache) | `data/app` |
| Database (Postgres + pgvector) | `data/postgres` |
| Library (at `/books`, writable) | `/volume1/NAS/Archive/5_Books/Library` |
| Textbooks | `Library/_Textbooks` (books-tools files them there), BookOrbit library "Textbooks" at `/books/_Textbooks`; the main library skips `_Textbooks/**` |
| Comics (at `/comics`) | `/volume1/NAS/Archive/Comics`, a separate BookOrbit library |
| Web UI / OPDS | `https://bookorbit.taileefdd9.ts.net` (own Tailscale name, HTTPS) |
| Tailscale sidecar state | `data/tailscale` (keep it, or the node must log in again) |

## Seedbox stays one-way

BookOrbit can edit, rename and move files in the Library, but it can't reach the seedbox: only
the Library is mounted, it has no seedbox login (that's in `books-tools/data/rclone.conf`),
and no download client is configured. The only path from Ultra.cc is books-tools'
`rclone copy` seedbox → `_Seedbox_Mirror` → Library.

books-tools copes with BookOrbit's changes: it never re-reads the Library as a source, so an
edited, renamed or deleted Library file is not copied again from the archive or mirror.
Leave the `.books-tools` marker file in the Library alone.

## Tailscale

The `tailscale` container joins the tailnet as `bookorbit` and serves BookOrbit over HTTPS
(`tailscale-serve.json`), forwarding to `app:3000` on the compose network. Tailnet-only, no Funnel.
First start needs `TS_AUTHKEY` in `.env` (admin console → Settings → Keys → Generate auth key,
not reusable, not ephemeral), like chi-eats/finance. Without it the sidecar waits for a browser
login, times out after a minute and restarts with a new identity. After the first login the
state in `data/tailscale` is reused and the key is no longer needed. In the Tailscale admin console, turn off key expiry for `bookorbit` and make sure
MagicDNS and HTTPS certificates are on (DNS page). `http://<nas>:3080` still works as a fallback.

## Commands

    cd /volume1/docker/bookorbit
    sudo docker compose up -d          # start / apply .env changes
    sudo docker compose logs -f app    # watch the app
    sudo docker compose ps
    sudo docker compose down           # stop (data is kept)

## Update

Change `APP_IMAGE` in `.env` to the new version, then `sudo docker compose pull && sudo docker compose up -d`.
Back up `data/` first; the database migrates on start.
