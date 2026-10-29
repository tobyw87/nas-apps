# bookmark-bar-sync

Browser extension (Firefox + Chrome) that keeps the bookmarks bar identical to one Linkwarden
collection ("Bookmarks Bar"). Replaces Floccus.

- Linkwarden is the master copy. Edits on the bar (add, delete, rename, URL edit, move between
  folders, reorder) are sent to Linkwarden one at a time; every few minutes the bar is pulled and
  made to match Linkwarden. Sub-collections = folders.
- Folder order is stored as a line at the end of each collection's description:
  `[bookmark-bar-sync order: l12,c5,l9]` (Linkwarden has no position field).
- Changes made while Linkwarden is unreachable are queued and sent later, never undone.
- Safety: if a pull would delete more than 10 bookmarks and more than half of them, it stops and
  waits for "Apply anyway" on the options page.
- Separators aren't synced (they stay on that computer, at the end of their folder).

Build: `./build.sh https://links.tobyw87.com` → `dist/bookmark-bar-sync-firefox.zip` (upload to AMO
as unlisted / "On your own" for signing) and `dist/bookmark-bar-sync-chrome.zip` (unzip, then
chrome://extensions → Developer mode → Load unpacked).
