# linkwarden

Linkwarden bookmark manager on Treebeard, used as the master copy for the bookmarks bar on all three
computers (see `../bookmark-bar-sync`).

- Lives in `/volume1/docker/linkwarden` (Postgres + Linkwarden, upstream images; state in `data/`).
- Listens only on `127.0.0.1:8110`; reached at **links.tobyw87.com** through the Cloudflare Tunnel,
  behind Cloudflare Access (email login for the web page, a service token for the extension).
- Page archiving is off (`DISABLE_PRESERVATION=true`) and Meilisearch is left out, to keep CPU/RAM/disk use low.

Install / update: `sudo sh /volume1/docker/linkwarden/install.sh` (never overwrites `.env` or `data/`).
Logs: `cd /volume1/docker/linkwarden && sudo docker compose logs --tail 50 linkwarden`.
