/* Bookmark Bar Sync — keeps the browser's bookmarks bar in sync with one Linkwarden collection.
 *
 * Linkwarden is the master copy. Changes made on the bar (add, delete, rename, edit URL, move between
 * folders, reorder) are sent to Linkwarden one at a time as they happen. Every few minutes the
 * extension pulls the collection and makes the bar match it, so changes from the other computers
 * (or from the Linkwarden web page) show up here.
 *
 * Linkwarden has no "position" field for links, so the order of each folder is saved as one line at
 * the end of that collection's description: [bookmark-bar-sync order: l12,c5,l9]
 */
'use strict';

const B = globalThis.browser ?? globalThis.chrome;
const ACTION = B.action ?? B.browserAction;
const IS_FIREFOX = B.runtime.getURL('').startsWith('moz-extension:');

const DEFAULTS = {
  url: '',
  token: '',
  cfId: '',
  cfSecret: '',
  collectionName: 'Bookmarks Bar',
  intervalMin: 2,
};
const ORDER_RE = /\s*\[bookmark-bar-sync order:[^\]]*\]\s*/g;
const DESC_MAX = 2048;
// Pulls that would delete more than this many bookmarks, and more than half of them, wait for a click.
const SAFETY_MIN_DELETES = 10;

// ---------------------------------------------------------------- small helpers

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const normTitle = (t) => (t || '').trim();
function normUrl(u) {
  u = (u || '').trim();
  try { return new URL(u).href; } catch { return u; }
}
const isFolder = (n) => (n.type ? n.type === 'folder' : !n.url);
const isSeparator = (n) => n.type === 'separator';
const SYNCABLE_URL = /^(https?|ftp|file|javascript):/i;
function syncable(n) {
  if (isSeparator(n)) return false;
  if (isFolder(n)) return true;
  return SYNCABLE_URL.test(n.url || '') && n.url.length <= 2048;
}
const keyType = (k) => k.slice(0, 1); // 'l' = link, 'c' = collection
const keyId = (k) => Number(k.slice(2));

class SyncError extends Error {
  constructor(msg, { retry = false, status = 0 } = {}) {
    super(msg);
    this.retry = retry; // true: keep the pending change and try again later
    this.status = status;
  }
}

// One thing at a time: queue edits, pushes and pulls never overlap.
let chain = Promise.resolve();
function locked(fn) {
  const p = chain.then(fn);
  chain = p.catch(() => {});
  return p;
}

// ---------------------------------------------------------------- state

let S = null; // { config, map, queue, rootCollectionId, setupDone, skip, status, blocked }
async function ensureLoaded() {
  if (S) return S;
  const s = await B.storage.local.get(null);
  S = {
    config: { ...DEFAULTS, ...(s.config || {}) },
    map: s.map || {}, // local bookmark id -> 'l:<linkId>' | 'c:<collectionId>'
    queue: s.queue || [], // pending changes to send to Linkwarden
    rootCollectionId: s.rootCollectionId || null,
    setupDone: !!s.setupDone,
    skip: s.skip || {}, // local ids Linkwarden refused to take, so we don't retry forever
    status: s.status || {},
    blocked: s.blocked || null,
  };
  return S;
}
const save = (...keys) => B.storage.local.set(Object.fromEntries(keys.map((k) => [k, S[k]])));

async function setStatus(patch) {
  S.status = { ...S.status, ...patch };
  await save('status');
  const bad = S.status.error || S.blocked;
  try {
    await ACTION.setBadgeText({ text: bad ? '!' : '' });
    await ACTION.setBadgeBackgroundColor({ color: '#d93025' });
    await ACTION.setTitle({ title: bad ? `Bookmark Bar Sync: ${S.status.error || 'waiting for you'}` : 'Bookmark Bar Sync' });
  } catch {}
}

function addWarning(msg) {
  const w = (S.status.warnings || []).slice(-9);
  w.push(`${new Date().toLocaleString()}: ${msg}`);
  S.status.warnings = w;
}

// ---------------------------------------------------------------- bookmarks helpers

let barIdCache = null;
async function barId() {
  if (barIdCache) return barIdCache;
  if (IS_FIREFOX) return (barIdCache = 'toolbar_____');
  const tops = (await B.bookmarks.getTree())[0].children;
  const bars = tops.filter((n) => n.folderType === 'bookmarks-bar');
  barIdCache = bars.length ? (bars.find((n) => !n.syncing) || bars[0]).id : tops[0].id;
  return barIdCache;
}
async function otherBookmarksId() {
  if (IS_FIREFOX) return 'unfiled_____';
  const tops = (await B.bookmarks.getTree())[0].children;
  const other = tops.filter((n) => n.folderType === 'other');
  return other.length ? (other.find((n) => !n.syncing) || other[0]).id : tops[1].id;
}
async function getNode(id) {
  try { return (await B.bookmarks.get(id))[0] || null; } catch { return null; }
}
async function inBar(folderId) {
  const bar = await barId();
  let cur = folderId;
  for (let d = 0; cur && d < 60; d++) {
    if (cur === bar) return true;
    const n = await getNode(cur);
    if (!n) return false;
    cur = n.parentId;
  }
  return false;
}

// While a pull is rearranging the bar, ignore the bookmark events it causes. (Any echo that slips
// through afterwards is harmless: it compares with Linkwarden, finds nothing to change, and stops.)
let applying = 0;
const ignoring = () => applying > 0;

// ---------------------------------------------------------------- Linkwarden API

async function lw(method, path, body) {
  const c = S.config;
  if (!c.url || !c.token) throw new SyncError('Linkwarden address or access token not set (open the options page).');
  const headers = { Authorization: `Bearer ${c.token.trim()}`, Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (c.cfId) {
    headers['CF-Access-Client-Id'] = c.cfId.trim();
    headers['CF-Access-Client-Secret'] = c.cfSecret.trim();
  }
  let res;
  try {
    res = await fetch(c.url.trim().replace(/\/+$/, '') + path, {
      method, headers, credentials: 'omit', cache: 'no-store', redirect: 'manual',
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new SyncError(`Can't reach Linkwarden (${e.message}).`, { retry: true });
  }
  const ct = res.headers.get('content-type') || '';
  if (res.type === 'opaqueredirect' || (res.status >= 300 && res.status < 400) || (!ct.includes('json') && res.status < 500)) {
    throw new SyncError('Cloudflare Access turned the request away (check the service token ID and secret).', { retry: true, status: res.status });
  }
  if (!ct.includes('json')) throw new SyncError(`Linkwarden error ${res.status}.`, { retry: true, status: res.status });
  const j = await res.json();
  if (!res.ok) {
    const msg = typeof j.response === 'string' ? j.response : j.message || res.statusText;
    const authProblem = res.status === 401 && /logged in|session|expired/i.test(msg);
    throw new SyncError(authProblem ? `Linkwarden rejected the access token (${msg})` : `Linkwarden: ${msg} (${res.status})`,
      { retry: authProblem || res.status >= 500 || res.status === 429, status: res.status });
  }
  return j;
}

const api = {
  collections: async () => (await lw('GET', '/api/v1/collections')).response,
  collection: async (id) => (await lw('GET', `/api/v1/collections/${id}`)).response,
  link: async (id) => (await lw('GET', `/api/v1/links/${id}`)).response,
  async links(collectionId) {
    const out = [];
    let cursor = null;
    for (let page = 0; page < 2000; page++) {
      const q = `/api/v1/search?sort=1&collectionId=${collectionId}${cursor != null ? `&cursor=${cursor}` : ''}`;
      const d = (await lw('GET', q)).data || {};
      out.push(...(d.links || []));
      if (d.nextCursor == null || !(d.links || []).length) break;
      cursor = d.nextCursor;
    }
    return out.filter((l) => l.collectionId === collectionId && l.url);
  },
  createLink: async (collectionId, url, name) =>
    (await lw('POST', '/api/v1/links', { type: 'url', url, name, collection: { id: collectionId } })).response,
  updateLink: async (l, patch) =>
    (await lw('PUT', `/api/v1/links/${l.id}`, {
      id: l.id,
      name: patch.name ?? l.name,
      url: patch.url ?? l.url,
      description: l.description || '',
      icon: l.icon ?? null,
      iconWeight: l.iconWeight ?? null,
      color: l.color ?? null,
      collection: { id: patch.collectionId ?? l.collectionId, ownerId: l.collection.ownerId },
      tags: (l.tags || []).map((t) => ({ id: t.id, name: t.name })),
    })).response,
  deleteLink: (id) => lw('DELETE', `/api/v1/links/${id}`),
  createCollection: async (name, parentId) =>
    (await lw('POST', '/api/v1/collections', parentId ? { name, parentId } : { name })).response,
  updateCollection: async (c, patch) =>
    (await lw('PUT', `/api/v1/collections/${c.id}`, {
      id: c.id,
      name: patch.name ?? c.name,
      description: patch.description ?? c.description ?? '',
      color: c.color,
      isPublic: c.isPublic,
      icon: c.icon ?? null,
      iconWeight: c.iconWeight ?? null,
      parentId: patch.parentId ?? c.parentId ?? undefined,
      members: (c.members || []).map((m) => ({
        userId: m.userId, canCreate: m.canCreate, canUpdate: m.canUpdate, canDelete: m.canDelete,
      })),
    })).response,
  deleteCollection: (id) => lw('DELETE', `/api/v1/collections/${id}`),
};

function parseOrder(desc) {
  const m = /\[bookmark-bar-sync order:([^\]]*)\]/.exec(desc || '');
  return m ? m[1].split(',').map((s) => s.trim()).filter((s) => /^[lc]:\d+$/.test(s)) : [];
}
function withOrder(desc, keys) {
  const base = (desc || '').replace(ORDER_RE, '\n').trim();
  keys = keys.slice();
  let line;
  for (;;) {
    line = `[bookmark-bar-sync order: ${keys.join(',')}]`;
    if (base.length + line.length + 2 <= DESC_MAX || !keys.length) break;
    keys.pop(); // anything that doesn't fit just sorts after the listed ones
  }
  return (base ? `${base}\n\n${line}` : line).trim();
}

// ---------------------------------------------------------------- pending changes (local -> Linkwarden)

function enqueue(op) {
  const same = (o) => o.k === op.k && o.id === op.id && o.key === op.key;
  if (!S.queue.some(same)) S.queue.push(op);
}

async function remoteFolderFor(localFolderId) {
  if (!S.map[localFolderId]) await opCreate(localFolderId);
  const k = S.map[localFolderId];
  return k && keyType(k) === 'c' ? keyId(k) : null;
}

async function opCreate(id) {
  const n = await getNode(id);
  if (!n || !syncable(n) || S.skip[id]) return;
  if (!S.map[id]) {
    if (!(await inBar(n.parentId))) return;
    const colId = await remoteFolderFor(n.parentId);
    if (!colId) return;
    try {
      if (isFolder(n)) {
        const c = await api.createCollection(normTitle(n.title) || 'Untitled folder', colId);
        S.map[id] = `c:${c.id}`;
      } else {
        const l = await api.createLink(colId, n.url.trim(), normTitle(n.title) || n.url.trim());
        S.map[id] = `l:${l.id}`;
      }
    } catch (e) {
      if (!e.retry) { S.skip[id] = true; await save('skip'); }
      throw e;
    }
    await save('map');
    enqueue({ k: 'order', id: n.parentId });
  }
  if (isFolder(n)) {
    for (const kid of await B.bookmarks.getChildren(id)) await opCreate(kid.id);
    enqueue({ k: 'order', id });
  }
}

async function opUpdate(id) {
  const key = S.map[id];
  const n = await getNode(id);
  if (!key || !n) return;
  if (keyType(key) === 'c') {
    if (id === (await barId())) return;
    const c = await api.collection(keyId(key));
    const name = normTitle(n.title) || 'Untitled folder';
    if (normTitle(c.name) !== name) await api.updateCollection(c, { name });
  } else {
    const l = await api.link(keyId(key));
    const name = normTitle(n.title) || n.url.trim();
    if (normTitle(l.name) !== name || normUrl(l.url) !== normUrl(n.url)) await api.updateLink(l, { name, url: n.url.trim() });
  }
}

async function opMove(id) {
  const key = S.map[id];
  const n = await getNode(id);
  if (!key || !n) return;
  if (!(await inBar(n.parentId))) return;
  const parentCol = await remoteFolderFor(n.parentId);
  if (!parentCol) return;
  if (keyType(key) === 'c') {
    const c = await api.collection(keyId(key));
    if (c.parentId !== parentCol) await api.updateCollection(c, { parentId: parentCol });
  } else {
    const l = await api.link(keyId(key));
    if (l.collectionId !== parentCol) await api.updateLink(l, { collectionId: parentCol });
  }
}

async function opDelete(key) {
  if (keyType(key) === 'c' && keyId(key) === S.rootCollectionId) return; // never delete the top collection
  try {
    if (keyType(key) === 'c') await api.deleteCollection(keyId(key));
    else await api.deleteLink(keyId(key));
  } catch (e) {
    if (e.retry) throw e; // already gone is fine
  }
}

async function opOrder(folderId) {
  const key = S.map[folderId];
  if (!key || keyType(key) !== 'c') return;
  const keys = (await B.bookmarks.getChildren(folderId).catch(() => [])).map((k) => S.map[k.id]).filter(Boolean);
  const c = await api.collection(keyId(key));
  const desc = withOrder(c.description, keys);
  if (desc !== (c.description || '').trim()) await api.updateCollection(c, { description: desc });
}

async function runOp(op) {
  if (op.k === 'create') return opCreate(op.id);
  if (op.k === 'update') return opUpdate(op.id);
  if (op.k === 'move') return opMove(op.id);
  if (op.k === 'delete') return opDelete(op.key);
  if (op.k === 'order') return opOrder(op.id);
}

async function flushQueue() {
  while (S.queue.length) {
    let i = S.queue.findIndex((o) => o.k !== 'order'); // folder orders last
    if (i < 0) i = 0;
    const op = S.queue[i];
    try {
      await runOp(op);
    } catch (e) {
      if (e.retry) throw e;
      addWarning(`Skipped a change (${op.k}): ${e.message}`);
    }
    S.queue.splice(S.queue.indexOf(op), 1);
    await save('queue');
  }
}

// ---------------------------------------------------------------- pull (Linkwarden -> bar)

async function mapLimit(items, n, fn) {
  const out = new Array(items.length);
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, async () => {
    while (next < items.length) { const i = next++; out[i] = await fn(items[i]); }
  }));
  return out;
}

async function fetchRemoteTree() {
  const cols = await api.collections();
  const root = cols.find((c) => c.id === S.rootCollectionId);
  if (!root) {
    throw new SyncError(`The "${S.config.collectionName}" collection isn't in Linkwarden any more. Nothing on the bar was changed; open the options page to set up again.`);
  }
  const kids = new Map();
  for (const c of cols) {
    if (c.parentId == null) continue;
    if (!kids.has(c.parentId)) kids.set(c.parentId, []);
    kids.get(c.parentId).push(c);
  }
  const subtree = [];
  (function walk(c) { subtree.push(c); for (const k of kids.get(c.id) || []) walk(k); })(root);
  const linksByCol = new Map();
  await mapLimit(subtree, 4, async (c) => linksByCol.set(c.id, await api.links(c.id)));

  const nodes = new Map();
  function build(c) {
    const node = { key: `c:${c.id}`, folder: true, title: c.name, children: [] };
    nodes.set(node.key, node);
    const items = [
      ...(kids.get(c.id) || []).map((s) => ({ key: `c:${s.id}`, at: s.createdAt, col: s })),
      ...linksByCol.get(c.id).map((l) => ({ key: `l:${l.id}`, at: l.createdAt, link: l })),
    ];
    const pos = new Map(parseOrder(c.description).map((k, i) => [k, i]));
    const rank = (it) => (pos.has(it.key) ? pos.get(it.key) : Infinity);
    items.sort((a, b) => rank(a) - rank(b) || String(a.at).localeCompare(String(b.at)) || keyId(a.key) - keyId(b.key));
    for (const it of items) {
      if (it.col) node.children.push(build(it.col));
      else {
        const ln = { key: it.key, folder: false, title: it.link.name, url: it.link.url };
        nodes.set(ln.key, ln);
        node.children.push(ln);
      }
    }
    return node;
  }
  return { tree: build(root), nodes };
}

async function applyFolder(localId, rnode, ctx) {
  for (let i = 0; i < rnode.children.length; i++) {
    const rc = rnode.children[i];
    const lid = ctx.byKey.get(rc.key);
    let ln = lid ? await getNode(lid) : null;
    if (ln && ln.parentId !== localId && !(await inBar(ln.parentId))) ln = null;
    if (!ln) {
      const props = { parentId: localId, index: i, title: rc.title };
      if (!rc.folder) props.url = rc.url;
      try {
        ln = await B.bookmarks.create(props);
      } catch (e) {
        addWarning(`Couldn't add "${rc.title}" to the bar: ${e.message}`);
        continue;
      }
      if (lid) delete S.map[lid];
      S.map[ln.id] = rc.key;
      ctx.byKey.set(rc.key, ln.id);
      ctx.changes++;
    } else {
      const here = await B.bookmarks.getChildren(localId);
      if (here[i]?.id !== ln.id) {
        await B.bookmarks.move(ln.id, { parentId: localId, index: i });
        ctx.changes++;
      }
      const titleDiff = normTitle(ln.title) !== normTitle(rc.title);
      const urlDiff = !rc.folder && normUrl(ln.url) !== normUrl(rc.url);
      if (titleDiff || urlDiff) {
        try {
          await B.bookmarks.update(ln.id, rc.folder ? { title: rc.title } : { title: rc.title, url: rc.url });
          ctx.changes++;
        } catch (e) {
          addWarning(`Couldn't update "${rc.title}": ${e.message}`);
        }
      }
    }
    if (rc.folder) await applyFolder(ln.id, rc, ctx);
  }
  // Whatever is left after the synced items: gone from Linkwarden, waiting to be moved elsewhere, or new here.
  const rest = (await B.bookmarks.getChildren(localId)).slice(rnode.children.length);
  for (const k of rest) {
    const key = S.map[k.id];
    if (key && !ctx.nodes.has(key)) {
      if (isFolder(k)) await B.bookmarks.removeTree(k.id);
      else await B.bookmarks.remove(k.id);
      delete S.map[k.id];
      ctx.changes++;
    } else if (!key && syncable(k) && !S.skip[k.id]) {
      ctx.upload.push(k.id);
    }
  }
}

async function countDeletions(nodes) {
  const bar = await barId();
  let gone = 0, mapped = 0;
  for (const [lid, key] of Object.entries(S.map)) {
    if (lid === bar) continue;
    mapped++;
    if (!nodes.has(key) && (await getNode(lid))) gone++;
  }
  return { gone, mapped };
}

async function pull({ force = false } = {}) {
  const { tree, nodes } = await fetchRemoteTree();
  const { gone, mapped } = await countDeletions(nodes);
  if (!force && gone > SAFETY_MIN_DELETES && gone > mapped / 2) {
    S.blocked = { gone, mapped, at: Date.now() };
    await save('blocked');
    throw new SyncError(`Linkwarden is missing ${gone} of your ${mapped} bookmarks, so the bar was left alone. If that's expected, click "Apply anyway" on the options page.`);
  }
  if (S.blocked) { S.blocked = null; await save('blocked'); }

  const bar = await barId();
  const ctx = {
    nodes, changes: 0, upload: [],
    byKey: new Map(Object.entries(S.map).map(([lid, key]) => [key, lid])),
  };
  applying++;
  try {
    await applyFolder(bar, tree, ctx);
  } finally {
    await save('map');
    await sleep(300); // let the bookmark events from our own edits arrive and be ignored
    applying--;
  }
  // Forget mappings for bookmarks that no longer exist anywhere.
  for (const [lid, key] of Object.entries(S.map)) {
    if (lid !== bar && !nodes.has(key) && !(await getNode(lid))) delete S.map[lid];
  }
  await save('map');
  for (const id of ctx.upload) enqueue({ k: 'create', id });
  await save('queue');
  return ctx.changes;
}

// ---------------------------------------------------------------- sync entry points

async function syncNow({ force = false } = {}) {
  await ensureLoaded();
  if (!S.setupDone) return;
  await setStatus({ running: true });
  try {
    await flushQueue();
    const changes = await pull({ force });
    await flushQueue();
    await setStatus({ running: false, error: null, lastSync: Date.now(), lastChanges: changes });
  } catch (e) {
    await setStatus({ running: false, error: e.message, lastAttempt: Date.now() });
  }
}

let kickTimer = null;
function kick() {
  clearTimeout(kickTimer);
  kickTimer = setTimeout(() => locked(async () => {
    await ensureLoaded();
    if (!S.setupDone || !S.queue.length) return;
    try {
      await flushQueue();
      await setStatus({ error: null });
    } catch (e) {
      await setStatus({ error: e.message, lastAttempt: Date.now() });
    }
  }), 1500);
}

async function scheduleAlarm() {
  await ensureLoaded();
  await B.alarms.clear('sync');
  if (S.setupDone) B.alarms.create('sync', { periodInMinutes: Math.max(1, Number(S.config.intervalMin) || 2), delayInMinutes: 0.1 });
}

// ---------------------------------------------------------------- browser events

function onLocalEvent(id, handler) {
  if (ignoring(id)) return;
  locked(async () => {
    await ensureLoaded();
    if (!S.setupDone) return;
    await handler();
    await save('queue');
  }).then(kick, (e) => console.error('bookmark-bar-sync', e));
}

function topMappedKeys(node) {
  if (S.map[node.id]) return [S.map[node.id]];
  return (node.children || []).flatMap(topMappedKeys);
}
function unmapTree(node) {
  delete S.map[node.id];
  delete S.skip[node.id];
  (node.children || []).forEach(unmapTree);
}

B.bookmarks.onCreated.addListener((id, node) => onLocalEvent(id, async () => {
  if (await inBar(node.parentId)) enqueue({ k: 'create', id });
}));

B.bookmarks.onRemoved.addListener((id, info) => onLocalEvent(id, async () => {
  const node = info.node || { id };
  for (const key of topMappedKeys(node)) enqueue({ k: 'delete', key });
  unmapTree(node);
  await save('map', 'skip');
}));

B.bookmarks.onChanged.addListener((id) => onLocalEvent(id, async () => {
  if (S.map[id]) enqueue({ k: 'update', id });
}));

B.bookmarks.onMoved.addListener((id, info) => onLocalEvent(id, async () => {
  const nowIn = await inBar(info.parentId);
  if (S.map[id] && nowIn) {
    enqueue({ k: 'move', id });
    enqueue({ k: 'order', id: info.parentId });
  } else if (S.map[id] && !nowIn) {
    // Dragged off the bar (e.g. into Other Bookmarks): remove it from Linkwarden.
    const sub = (await B.bookmarks.getSubTree(id).catch(() => [{ id }]))[0];
    enqueue({ k: 'delete', key: S.map[id] });
    unmapTree(sub);
    await save('map', 'skip');
  } else if (nowIn) {
    enqueue({ k: 'create', id });
    enqueue({ k: 'order', id: info.parentId });
  }
}));

if (B.bookmarks.onChildrenReordered) {
  B.bookmarks.onChildrenReordered.addListener((id) => onLocalEvent(id, async () => {
    if (S.map[id]) enqueue({ k: 'order', id });
  }));
}

B.alarms.onAlarm.addListener((a) => { if (a.name === 'sync') locked(() => syncNow()); });
B.runtime.onStartup.addListener(() => { scheduleAlarm(); });
B.runtime.onInstalled.addListener(() => { scheduleAlarm(); });
ACTION.onClicked.addListener(() => B.runtime.openOptionsPage());

// ---------------------------------------------------------------- options page commands

async function findRootByName(cols) {
  const name = normTitle(S.config.collectionName) || DEFAULTS.collectionName;
  return cols.find((c) => c.parentId == null && normTitle(c.name) === name) || null;
}

async function cmdTest() {
  const cols = await api.collections();
  const root = await findRootByName(cols);
  if (!root) return { ok: true, exists: false };
  const subs = cols.filter((c) => c.parentId === root.id).length;
  return { ok: true, exists: true, links: root._count?.links ?? 0, subs };
}

async function cmdSetup(mode) {
  const cols = await api.collections();
  let root = await findRootByName(cols);
  const bar = await barId();
  if (mode === 'replace') {
    if (!root) throw new SyncError(`There's no "${S.config.collectionName}" collection in Linkwarden yet. Use "Upload this bar" on your first computer.`);
    const kids = await B.bookmarks.getChildren(bar);
    if (kids.length) {
      applying++;
      try {
        const day = new Date().toISOString().slice(0, 10);
        const backup = await B.bookmarks.create({ parentId: await otherBookmarksId(), title: `Bookmarks bar backup ${day}` });
        for (const k of kids) await B.bookmarks.move(k.id, { parentId: backup.id });
      } finally {
        await sleep(300);
        applying--;
      }
    }
  } else if (!root) {
    root = await api.createCollection(normTitle(S.config.collectionName) || DEFAULTS.collectionName);
  }
  S.rootCollectionId = root.id;
  S.map = { [bar]: `c:${root.id}` };
  S.queue = [];
  S.skip = {};
  S.blocked = null;
  S.setupDone = true;
  S.status = {};
  await save('rootCollectionId', 'map', 'queue', 'skip', 'blocked', 'setupDone', 'status');
  await scheduleAlarm();
  await syncNow();
  return { ok: true };
}

async function cmdDisconnect() {
  S.setupDone = false;
  S.map = {};
  S.queue = [];
  S.skip = {};
  S.blocked = null;
  S.rootCollectionId = null;
  S.status = {};
  await save('setupDone', 'map', 'queue', 'skip', 'blocked', 'rootCollectionId', 'status');
  await B.alarms.clear('sync');
  await setStatus({});
  return { ok: true };
}

B.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const run = async () => {
    await ensureLoaded();
    switch (msg.cmd) {
      case 'saveConfig':
        S.config = { ...DEFAULTS, ...msg.config };
        await save('config');
        await scheduleAlarm();
        return { ok: true };
      case 'test': return cmdTest();
      case 'setup': return cmdSetup(msg.mode);
      case 'syncNow': await syncNow(); return { ok: true };
      case 'forceApply': await syncNow({ force: true }); return { ok: true };
      case 'disconnect': return cmdDisconnect();
      case 'state':
        return { config: S.config, setupDone: S.setupDone, status: S.status, blocked: S.blocked, pending: S.queue.length };
      default: return { ok: false, error: 'unknown command' };
    }
  };
  const p = msg.cmd === 'state' || msg.cmd === 'saveConfig' ? run() : locked(run);
  p.then((r) => sendResponse(r), (e) => sendResponse({ ok: false, error: e.message }));
  return true; // async response
});

// Exposed for tests.
globalThis.__bbs = { ensureLoaded, syncNow: () => locked(() => syncNow()), state: () => S, barId };
