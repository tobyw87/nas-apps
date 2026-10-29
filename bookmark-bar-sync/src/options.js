'use strict';
const B = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);
const FIELDS = ['url', 'token', 'cfId', 'cfSecret', 'collectionName', 'intervalMin'];
// Firefox's browser.* returns promises; Chrome's chrome.* does too in Manifest V3.
const send = (msg) => B.runtime.sendMessage(msg).catch((e) => ({ ok: false, error: e.message }));

function ago(t) {
  if (!t) return 'never';
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return new Date(t).toLocaleString();
}

async function refresh() {
  const st = await send({ cmd: 'state' });
  if (!st || !st.config) return;
  if (!refresh.filled) {
    for (const f of FIELDS) $(f).value = st.config[f] ?? '';
    refresh.filled = true;
  }
  $('status-box').hidden = !st.setupDone;
  $('setup-box').hidden = st.setupDone || !refresh.tested;
  const s = st.status || {};
  $('status-line').textContent = s.running ? 'Syncing…'
    : `Last synced ${ago(s.lastSync)}${st.pending ? ` · ${st.pending} change(s) waiting to be sent` : ''}`;
  $('error-line').hidden = !s.error;
  $('error-line').textContent = s.error || '';
  $('blocked-box').hidden = !st.blocked;
  if (st.blocked) $('blocked-text').textContent = `Linkwarden is missing ${st.blocked.gone} of the ${st.blocked.mapped} bookmarks on this bar. Nothing was deleted. If you emptied the collection on purpose, apply it here too:`;
  const w = s.warnings || [];
  $('warnings-box').hidden = !w.length;
  $('warnings').replaceChildren(...w.slice().reverse().map((t) => Object.assign(document.createElement('li'), { textContent: t })));
}

async function busy(btn, fn) {
  btn.disabled = true;
  try { await fn(); } finally { btn.disabled = false; refresh(); }
}

$('save').onclick = () => busy($('save'), async () => {
  const config = Object.fromEntries(FIELDS.map((f) => [f, $(f).value.trim()]));
  config.intervalMin = Number(config.intervalMin) || 2;
  if (!config.collectionName) config.collectionName = 'Bookmarks Bar';
  await send({ cmd: 'saveConfig', config });
  const out = $('test-result');
  out.className = '';
  out.textContent = 'Testing…';
  const r = await send({ cmd: 'test' });
  if (!r.ok) { out.className = 'error'; out.textContent = r.error; return; }
  out.className = 'ok';
  out.textContent = r.exists
    ? `Connected. "${config.collectionName}" has ${r.links} bookmark(s) and ${r.subs} folder(s) at the top level.`
    : `Connected. "${config.collectionName}" doesn't exist yet; "Upload this bar" will create it.`;
  refresh.tested = true;
  $('replace').disabled = !r.exists;
});

$('upload').onclick = () => busy($('upload'), async () => {
  if (!confirm('Send everything on this bar to Linkwarden and start syncing?')) return;
  const r = await send({ cmd: 'setup', mode: 'upload' });
  if (!r.ok) alert(r.error);
});
$('replace').onclick = () => busy($('replace'), async () => {
  if (!confirm('Move what is on this bar now into a backup folder in Other Bookmarks, and fill the bar from Linkwarden?')) return;
  const r = await send({ cmd: 'setup', mode: 'replace' });
  if (!r.ok) alert(r.error);
});
$('sync').onclick = () => busy($('sync'), () => send({ cmd: 'syncNow' }));
$('force').onclick = () => busy($('force'), async () => {
  if (confirm('Remove the missing bookmarks from this bar?')) await send({ cmd: 'forceApply' });
});
$('disconnect').onclick = () => busy($('disconnect'), async () => {
  if (confirm('Stop syncing this browser? Your bookmarks stay where they are.')) await send({ cmd: 'disconnect' });
});

refresh();
setInterval(refresh, 3000);
