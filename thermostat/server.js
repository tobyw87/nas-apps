// Thermostat for Treebeard: room status, manual control and the heating/cooling schedule
// for the three Amana wall units. Talks to Home Assistant's REST API with a long-lived token
// (kept server-side). The schedule lives here (data/config.json) and is applied through HA's
// SmartIR climate entities; the old HA schedule automation is switched off on startup.
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');

const HA_URL = (process.env.HA_URL || '').replace(/\/$/, '');
const HA_TOKEN = process.env.HA_TOKEN || '';
const TZ = process.env.TZ || 'America/Chicago';
const PORT = Number(process.env.PORT) || 8080;
const LISTEN_HOST = process.env.LISTEN_HOST || '0.0.0.0';
const DATA_DIR = process.env.DATA_DIR || '/data';
const CONFIG = path.join(DATA_DIR, 'config.json');
const PUBLIC = path.join(__dirname, 'public');
const OLD_AUTOMATION_ID = 'amana_heat_schedule';
const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];

const DEFAULT_CONFIG = {
  scheduleEnabled: true,
  mode: 'heat',
  away: { enabled: true, temp: 61 },
  rooms: [
    { id: 'office', name: 'Office', climate: 'climate.office_heat',
      tempSensor: 'sensor.office_temperature_calibrated', humiditySensor: '',
      scheduleEnabled: true, setback: 61,
      blocks: [{ id: 'b1', start: '08:40', end: '18:00', temp: 70, days: ALL_DAYS, enabled: true }] },
    { id: 'living', name: 'Living Room', climate: 'climate.living_room_heat',
      tempSensor: 'sensor.living_room_temperature_calibrated', humiditySensor: '',
      scheduleEnabled: true, setback: 61,
      blocks: [{ id: 'b1', start: '17:40', end: '01:00', temp: 70, days: ALL_DAYS, enabled: true }] },
    { id: 'bedroom', name: 'Bedroom', climate: 'climate.bedroom_heat',
      tempSensor: 'sensor.bedroom_temperature_calibrated', humiditySensor: '',
      scheduleEnabled: true, setback: 61, blocks: [] },
  ],
};

// ---------- config ----------
function loadConfig() {
  try { return validate(JSON.parse(fs.readFileSync(CONFIG, 'utf8'))); }
  catch (e) {
    if (e.code !== 'ENOENT') console.error('config.json unreadable, using defaults:', e.message);
    const c = JSON.parse(JSON.stringify(DEFAULT_CONFIG)); saveConfig(c); return c;
  }
}
function saveConfig(c) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.writeFileSync(CONFIG + '.tmp', JSON.stringify(c, null, 2));
  fs.renameSync(CONFIG + '.tmp', CONFIG);
}
const isTime = (t) => typeof t === 'string' && /^([01]\d|2[0-3]):[0-5]\d$/.test(t);
const clampTemp = (t) => Math.max(61, Math.min(86, Math.round(Number(t))));
function validate(c) {
  if (!c || !Array.isArray(c.rooms)) throw new Error('rooms missing');
  const out = {
    scheduleEnabled: c.scheduleEnabled !== false,
    mode: c.mode === 'cool' ? 'cool' : 'heat',
    away: { enabled: !!(c.away && c.away.enabled), temp: clampTemp((c.away && c.away.temp) || 61) },
    rooms: [],
  };
  for (const r of c.rooms) {
    if (!r || typeof r.id !== 'string' || typeof r.climate !== 'string') throw new Error('bad room');
    out.rooms.push({
      id: r.id, name: String(r.name || r.id), climate: r.climate,
      tempSensor: String(r.tempSensor || ''), humiditySensor: String(r.humiditySensor || ''),
      scheduleEnabled: r.scheduleEnabled !== false,
      setback: r.setback === null ? null : clampTemp(r.setback),
      blocks: (r.blocks || []).map((b, i) => {
        if (!isTime(b.start) || !isTime(b.end) || b.start === b.end) throw new Error('bad time in ' + r.name);
        const days = [...new Set((b.days || []).map(Number).filter((d) => d >= 0 && d <= 6))].sort();
        return { id: String(b.id || 'b' + (i + 1)), start: b.start, end: b.end, temp: clampTemp(b.temp),
                 days, enabled: b.enabled !== false };
      }),
    });
  }
  return out;
}
let config = loadConfig();

// ---------- time ----------
const fmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, weekday: 'short', hour: '2-digit',
  minute: '2-digit', hourCycle: 'h23' });
const DAY = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
function localParts(date) {
  const p = Object.fromEntries(fmt.formatToParts(date).map((x) => [x.type, x.value]));
  return { day: DAY[p.weekday], min: Number(p.hour) * 60 + Number(p.minute) };
}
const toMin = (t) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3));

function blockActive(b, { day, min }) {
  if (!b.enabled) return false;
  const s = toMin(b.start), e = toMin(b.end);
  if (s < e) return b.days.includes(day) && min >= s && min < e;
  // crosses midnight: evening part belongs to today, early-morning part to yesterday's block
  return (b.days.includes(day) && min >= s) || (b.days.includes((day + 6) % 7) && min < e);
}

// What the schedule wants for a room right now: {hvac, temp, reason} or null (schedule off).
function desiredFor(room, at, home) {
  if (!config.scheduleEnabled || !room.scheduleEnabled) return null;
  const parts = localParts(at);
  const active = room.blocks.filter((b) => blockActive(b, parts));
  if (active.length) {
    const temps = active.map((b) => b.temp);
    return awayAdjust({ hvac: config.mode, temp: config.mode === 'cool' ? Math.min(...temps) : Math.max(...temps), reason: 'block' }, home);
  }
  const d = room.setback === null ? { hvac: 'off', temp: null, reason: 'setback' }
                                  : { hvac: config.mode, temp: room.setback, reason: 'setback' };
  return awayAdjust(d, home);
}
// Away mode: every room that would be running goes to the away temperature; rooms that are off stay off.
function awayAdjust(d, home) {
  if (!config.away.enabled || home !== false || d.hvac === 'off') return d;
  return { hvac: config.mode, temp: config.away.temp, reason: 'away' };
}
const key = (d) => (d ? d.hvac + ':' + d.temp : 'none');

// Next time the scheduled value changes (looks ahead up to 48 h, minute by minute; cached per minute).
const nextCache = new Map(); let configVersion = 0;
function nextChange(room, home) {
  const now = new Date();
  const ck = `${room.id}|${home}|${configVersion}|${Math.floor(now.getTime() / 60000)}`;
  if (nextCache.has(ck)) return nextCache.get(ck);
  if (nextCache.size > 50) nextCache.clear();
  const v = scanNext(room, home, now); nextCache.set(ck, v); return v;
}
function scanNext(room, home, now) {
  now = new Date(Math.floor(now.getTime() / 60000) * 60000);
  const cur = key(desiredFor(room, now, home));
  for (let i = 1; i <= 48 * 60; i++) {
    const t = new Date(now.getTime() + i * 60000);
    const d = desiredFor(room, t, home);
    if (key(d) !== cur) return { at: t.toISOString(), desired: d };
  }
  return null;
}

// ---------- Home Assistant ----------
async function ha(method, p, body) {
  const res = await fetch(HA_URL + p, {
    method, headers: { Authorization: 'Bearer ' + HA_TOKEN, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) throw new Error(`HA ${method} ${p}: ${res.status} ${await res.text().catch(() => '')}`.slice(0, 300));
  return res.json();
}
let states = {}; let haError = null; let lastPoll = 0;
async function poll() {
  try {
    const list = await ha('GET', '/api/states');
    states = Object.fromEntries(list.map((s) => [s.entity_id, s]));
    haError = null; lastPoll = Date.now();
  } catch (e) { haError = e.message; console.error(e.message); }
}
function homeState() {
  const z = states['zone.home'];
  if (!z) return null; // unknown -> don't treat as away
  const n = Number(z.state);
  return Number.isFinite(n) ? n > 0 : null;
}
const KEYWORDS = { office: ['office'], living: ['living'], bedroom: ['bedroom'] };
function findHumidity(room) {
  if (room.humiditySensor) return room.humiditySensor;
  const words = KEYWORDS[room.id] || [room.id];
  const cands = Object.values(states).filter((s) => s.entity_id.startsWith('sensor.') &&
    (s.attributes.device_class === 'humidity' || s.entity_id.includes('humidity')) &&
    words.some((w) => s.entity_id.includes(w)));
  cands.sort((a, b) => (b.entity_id.includes('calibrated') - a.entity_id.includes('calibrated')));
  return cands.length ? cands[0].entity_id : '';
}
const num = (s) => (s && s.state !== 'unavailable' && s.state !== 'unknown' && Number.isFinite(Number(s.state)) ? Number(s.state) : null);

async function sendToRoom(room, d) {
  if (d.hvac === 'off') {
    await ha('POST', '/api/services/climate/set_hvac_mode', { entity_id: room.climate, hvac_mode: 'off' });
  } else {
    await ha('POST', '/api/services/climate/set_temperature',
      { entity_id: room.climate, hvac_mode: d.hvac, temperature: d.temp });
  }
}

// ---------- scheduler ----------
const applied = {}; // room id -> key last sent by the schedule
let busy = false;
async function tick() {
  if (busy) return; busy = true;
  try {
    await poll();
    if (haError) return;
    const home = homeState();
    for (const room of config.rooms) {
      const d = desiredFor(room, new Date(), home);
      if (!d) { delete applied[room.id]; continue; }
      if (applied[room.id] === key(d)) continue;
      if (!states[room.climate] || states[room.climate].state === 'unavailable') continue;
      try {
        await sendToRoom(room, d);
        applied[room.id] = key(d);
        console.log(new Date().toISOString(), `${room.name}: schedule -> ${d.hvac} ${d.temp ?? ''} (${d.reason})`);
        await new Promise((r) => setTimeout(r, 3000)); // space out IR bursts
      } catch (e) { console.error(`${room.name}: ${e.message}`); }
    }
  } finally { busy = false; }
}
async function disableOldAutomation() {
  try {
    await poll();
    const a = Object.values(states).find((s) => s.entity_id.startsWith('automation.') && s.attributes.id === OLD_AUTOMATION_ID);
    if (a && a.state === 'on') {
      await ha('POST', '/api/services/automation/turn_off', { entity_id: a.entity_id });
      console.log('Turned off the old HA schedule automation:', a.entity_id);
    }
  } catch (e) { console.error('Could not check old automation:', e.message); }
}

// ---------- API ----------
function roomView(room, home) {
  const c = states[room.climate];
  const hum = findHumidity(room);
  const desired = desiredFor(room, new Date(), home);
  return {
    ...room,
    humiditySensorUsed: hum,
    current: {
      available: !!c && c.state !== 'unavailable',
      hvac: c ? c.state : null,
      target: c ? c.attributes.temperature ?? null : null,
      temp: num(states[room.tempSensor]) ?? (c ? c.attributes.current_temperature ?? null : null),
      humidity: num(states[hum]),
    },
    scheduled: desired,
    next: desired ? nextChange(room, home) : null,
  };
}
function stateView() {
  const home = homeState();
  return { ok: !haError, error: haError, lastPoll, home, scheduleEnabled: config.scheduleEnabled,
           mode: config.mode, away: config.away, rooms: config.rooms.map((r) => roomView(r, home)) };
}
function send(res, code, obj) {
  res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(obj));
}
function readBody(req) {
  return new Promise((resolve, reject) => {
    let b = ''; req.on('data', (c) => { b += c; if (b.length > 1e6) req.destroy(); });
    req.on('end', () => { try { resolve(b ? JSON.parse(b) : {}); } catch (e) { reject(e); } });
  });
}
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css',
                '.svg': 'image/svg+xml', '.json': 'application/json' };

http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  try {
    if (url.pathname === '/api/state' && req.method === 'GET') {
      if (Date.now() - lastPoll > 5000) await poll();
      return send(res, 200, stateView());
    }
    if (url.pathname === '/api/config' && req.method === 'PUT') {
      const c = validate(await readBody(req));
      config = c; saveConfig(c); configVersion++;
      tick(); // sends only to rooms whose scheduled setting changed
      return send(res, 200, stateView());
    }
    const m = url.pathname.match(/^\/api\/room\/([\w-]+)$/);
    if (m && req.method === 'POST') {
      const room = config.rooms.find((r) => r.id === m[1]);
      if (!room) return send(res, 404, { error: 'no such room' });
      const b = await readBody(req);
      if (b.hvac === 'off') await sendToRoom(room, { hvac: 'off' });
      else {
        const hvac = b.hvac === 'cool' || b.hvac === 'heat' ? b.hvac : (states[room.climate]?.state === 'cool' ? 'cool' : config.mode);
        const temp = clampTemp(b.temp ?? states[room.climate]?.attributes.temperature ?? 70);
        await sendToRoom(room, { hvac, temp });
      }
      await poll();
      return send(res, 200, stateView());
    }
    if (url.pathname === '/healthz') return send(res, 200, { ok: !haError });
    // static files
    let f = url.pathname === '/' ? '/index.html' : url.pathname === '/kiosk' ? '/kiosk.html' : url.pathname;
    f = path.join(PUBLIC, path.normalize(f).replace(/^(\.\.[\/\\])+/, ''));
    if (!f.startsWith(PUBLIC) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) return send(res, 404, { error: 'not found' });
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(f)] || 'application/octet-stream', 'Cache-Control': 'no-cache' });
    fs.createReadStream(f).pipe(res);
  } catch (e) {
    send(res, 400, { error: e.message });
  }
}).listen(PORT, LISTEN_HOST, () => console.log(`Thermostat listening on ${LISTEN_HOST}:${PORT}; HA at ${HA_URL}`));

if (!HA_URL || !HA_TOKEN) console.error('HA_URL and HA_TOKEN must be set in .env');
disableOldAutomation().then(tick);
setInterval(tick, 30000);
