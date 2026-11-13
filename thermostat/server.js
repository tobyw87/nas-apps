// Thermostat for Treebeard: room status, manual control and the heating/cooling schedule
// for the three Amana wall units. Talks to Home Assistant's REST API with a long-lived token
// (kept server-side). The schedule lives here (data/config.json) and is applied through HA's
// SmartIR climate entities; the old HA schedule automation is switched off on startup.
// With sensor control on, the room sensors decide when each unit runs: the unit gets Heat (with a
// setpoint a degree above the target so its own thermostat doesn't stop it early) when the room drops
// half the buffer below the target, and Off once the room is half the buffer above it.
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');
const createPower = require('./power');

const HA_URL = (process.env.HA_URL || '').replace(/\/$/, '');
const HA_TOKEN = process.env.HA_TOKEN || '';
const TZ = process.env.TZ || 'America/Chicago';
const PORT = Number(process.env.PORT) || 8080;
const LISTEN_HOST = process.env.LISTEN_HOST || '0.0.0.0';
const DATA_DIR = process.env.DATA_DIR || '/data';
const CONFIG = path.join(DATA_DIR, 'config.json');
const RUNTIME = path.join(DATA_DIR, 'state.json');
const MIN_SWITCH_MS = (Number(process.env.MIN_SWITCH_MINUTES) || 5) * 60000; // never switch a unit on/off more often than this
// Degrees past the target sent to a running unit. Kept small: a big gap between a unit's setpoint and
// the room makes these heat pumps bring on their electric strips (about 4x the power of the heat pump).
const BOOST = Math.max(0, Math.min(6, /^\d+(\.\d+)?$/.test(process.env.BOOST_DEGREES || '') ? Number(process.env.BOOST_DEGREES) : 1));
// Between runs a unit is switched off. IDLE_MODE=heat keeps it in Heat at 61° (Cool at 86°) instead, which
// avoids cold restarts but only works if the unit's own sensor reads above 61° (these read cold at the wall).
const IDLE_OFF = process.env.IDLE_MODE !== 'heat';
const idleCmd = (cool) => (IDLE_OFF ? { hvac: 'off' } : cool ? { hvac: 'cool', temp: 86 } : { hvac: 'heat', temp: 61 });
// Gentle warm-up. These units bring on the electric strips (about 4x the heat pump's power) when their own
// sensor is ~4° or more below the setpoint (tested Nov 11 2026: +2-3° stayed on the heat pump, +4-5°
// switched to strips after a few minutes). Their sensors read about UNIT_SENSOR_OFFSET below the room
// sensor, so a running unit's setpoint is capped at room - offset + MAX_SETPOINT_GAP and raised in steps
// (at most once every STEP_MINUTES) as the room warms, instead of jumping straight to the target.
const envNum = (k, d) => (/^-?\d+(\.\d+)?$/.test(process.env[k] || '') ? Number(process.env[k]) : d);
const UNIT_OFFSET = envNum('UNIT_SENSOR_OFFSET', 4);
const MAX_GAP = envNum('MAX_SETPOINT_GAP', 3);
const STEP_MS = envNum('STEP_MINUTES', 5) * 60000;
const GENTLE = process.env.GENTLE_WARMUP !== 'off';
// Setpoints whose IR code the units misread (69°F came out as 68°F); stepped past downward when heating.
const SKIP_TEMPS = (process.env.SKIP_TEMPS ?? '69').split(',').map(Number).filter(Number.isFinite);
const PUBLIC = path.join(__dirname, 'public');
const OLD_AUTOMATION_ID = 'amana_heat_schedule';
const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];

const DEFAULT_CONFIG = {
  scheduleEnabled: true,
  mode: 'heat',
  away: { enabled: true, temp: 61 },
  sensorControl: { enabled: true, buffer: 3 },
  // Sleep hours: the quiet room's unit stays off; the helper rooms' units heat the house whenever the quiet
  // room's sensor drops below keepAt (helper rooms themselves go no higher than helperMax).
  sleep: { enabled: true, start: '23:00', end: '09:00', days: ALL_DAYS, quietRoom: 'bedroom', helpers: ['living'], keepAt: 60, helperMax: 68 },
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
    sensorControl: {
      enabled: !c.sensorControl || c.sensorControl.enabled !== false,
      buffer: Math.max(1, Math.min(8, Math.round(Number((c.sensorControl && c.sensorControl.buffer) || 3) * 2) / 2)),
    },
    sleep: null,
    rooms: [],
  };
  const sl = c.sleep || DEFAULT_CONFIG.sleep;
  const roomIds = c.rooms.map((r) => r && r.id);
  if (!isTime(sl.start) || !isTime(sl.end) || sl.start === sl.end) throw new Error('bad sleep hours');
  out.sleep = {
    enabled: !!sl.enabled, start: sl.start, end: sl.end,
    days: [...new Set((sl.days || ALL_DAYS).map(Number).filter((d) => d >= 0 && d <= 6))].sort(),
    quietRoom: roomIds.includes(sl.quietRoom) ? sl.quietRoom : null,
    helpers: (sl.helpers || []).filter((id) => roomIds.includes(id) && id !== sl.quietRoom),
    keepAt: Math.max(50, Math.min(75, Math.round(Number(sl.keepAt) || 60))),
    helperMax: clampTemp(sl.helperMax || 68),
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

const sleepActive = (parts) => blockActive({ ...config.sleep, enabled: true }, parts);
// During sleep hours, should the helper rooms heat the house for the quiet room? (hysteresis on its sensor)
function updateHelp() {
  const sl = config.sleep, prev = !!rt._help;
  let on = false;
  if (sl.enabled && sl.quietRoom && sl.helpers.length && config.scheduleEnabled && config.mode === 'heat' &&
      config.sensorControl.enabled && sleepActive(localParts(new Date()))) {
    const q = config.rooms.find((r) => r.id === sl.quietRoom);
    const b = q && num(states[q.tempSensor]);
    const half = config.sensorControl.buffer / 2;
    if (b != null) on = prev ? b < sl.keepAt + half : b <= sl.keepAt - half;
  }
  if (on !== prev) {
    rt._help = on;
    for (const id of sl.helpers) if (rt[id]) { rt[id].running = null; rt[id].stage = null; }
    console.log(new Date().toISOString(), on ? `Sleep hours: ${sl.quietRoom} below ${sl.keepAt}°, ${sl.helpers.join(', ')} heating the house`
                                             : `Sleep hours: ${sl.quietRoom} warm enough, helpers back to their own settings`);
    saveRuntime();
  }
}

// What the schedule wants for a room right now: {hvac, temp, reason} or null (schedule off).
function desiredFor(room, at, home) {
  if (!config.scheduleEnabled || !room.scheduleEnabled) return null;
  const parts = localParts(at);
  if (config.sleep.enabled && config.sleep.quietRoom === room.id && sleepActive(parts)) return { hvac: 'off', temp: null, reason: 'sleep' };
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
const power = createPower({ HA_URL, HA_TOKEN, TZ, DATA_DIR, getStates: () => states });
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
// Humidity sensors: the humidity entity in the room's Home Assistant area (looked up every 10 min),
// else one whose name contains the room name.
let areaHumidity = {}; let areaLookupAt = 0;
async function lookupAreaHumidity() {
  if (Date.now() - areaLookupAt < 600000) return;
  areaLookupAt = Date.now();
  const parts = config.rooms.map((r) => `${JSON.stringify(r.id)}: (area_entities(${JSON.stringify(r.name)}) | select('match', 'sensor\\.') | select('is_state_attr', 'device_class', 'humidity') | list)`);
  try {
    const res = await fetch(HA_URL + '/api/template', {
      method: 'POST', headers: { Authorization: 'Bearer ' + HA_TOKEN, 'Content-Type': 'application/json' },
      body: JSON.stringify({ template: `{{ { ${parts.join(', ')} } | tojson }}` }), signal: AbortSignal.timeout(10000),
    });
    if (res.ok) areaHumidity = JSON.parse(await res.text());
  } catch (e) { console.error('Area humidity lookup failed:', e.message); }
}
function findHumidity(room) {
  if (room.humiditySensor) return room.humiditySensor;
  const inArea = (areaHumidity[room.id] || []).filter((id) => states[id]);
  if (inArea.length) return inArea.sort((a, b) => b.includes('calibrated') - a.includes('calibrated'))[0];
  const words = KEYWORDS[room.id] || [room.id];
  const cands = Object.values(states).filter((s) => s.entity_id.startsWith('sensor.') &&
    (s.attributes.device_class === 'humidity' || s.entity_id.includes('humidity')) &&
    words.some((w) => s.entity_id.includes(w)));
  cands.sort((a, b) => (b.entity_id.includes('calibrated') - a.entity_id.includes('calibrated')));
  return cands.length ? cands[0].entity_id : '';
}
const num = (s) => (s && s.state !== 'unavailable' && s.state !== 'unknown' && Number.isFinite(Number(s.state)) ? Number(s.state) : null);

async function sendToRoom(room, d) {
  if (d.hvac !== 'off' && SKIP_TEMPS.includes(d.temp)) {
    let t = d.temp; while (SKIP_TEMPS.includes(t)) t += d.hvac === 'cool' ? 1 : -1;
    d = { ...d, temp: t };
  }
  if (d.hvac === 'off') {
    await ha('POST', '/api/services/climate/set_hvac_mode', { entity_id: room.climate, hvac_mode: 'off' });
  } else {
    await ha('POST', '/api/services/climate/set_temperature',
      { entity_id: room.climate, hvac_mode: d.hvac, temperature: d.temp });
  }
}

// ---------- scheduler and room control ----------
// Per room: the thermostat setting (hvac + target, from the schedule or a manual change), the key of
// the schedule value it was last taken from, whether the unit is running, and the last command sent.
let rt = {};
try { rt = JSON.parse(fs.readFileSync(RUNTIME, 'utf8')) || {}; } catch { rt = {}; }
function saveRuntime() {
  try { fs.writeFileSync(RUNTIME + '.tmp', JSON.stringify(rt)); fs.renameSync(RUNTIME + '.tmp', RUNTIME); }
  catch (e) { console.error('Could not save state.json:', e.message); }
}
function roomRt(room) {
  if (!rt[room.id]) {
    const c = states[room.climate];
    rt[room.id] = c && c.state !== 'unavailable'
      ? { hvac: c.state === 'off' ? 'off' : c.state === 'cool' ? 'cool' : 'heat', target: c.attributes.temperature ?? 61 }
      : { hvac: 'off', target: null };
  }
  return rt[room.id];
}
// source: 'schedule' or 'manual' (set from the page, the kiosk or the app's API). Manual settings skip the
// gentle warm-up and go straight to the target; scheduled ones warm up in steps.
function setSetting(room, hvac, target, source = 'schedule') {
  const r = roomRt(room);
  if (r.hvac !== hvac || r.target !== target) { r.hvac = hvac; r.target = target; r.lastSwitch = 0; r.running = null; r.stage = null; }
  r.manual = source === 'manual';
}

// What to send the unit now, given the setting and the room sensor.
function command(room) {
  const r = roomRt(room);
  r.helping = false;
  if (r.hvac === 'off' || r.target == null) { r.running = false; return { hvac: 'off' }; }
  const t = num(states[room.tempSensor]);
  if (!config.sensorControl.enabled || t == null) {
    r.running = null; // unit's own thermostat decides
    return { hvac: r.hvac, temp: r.target };
  }
  const half = config.sensorControl.buffer / 2;
  const cool = r.hvac === 'cool';
  // sleep hours: a helper room heats toward helperMax while the quiet room needs warmth
  r.helping = !cool && !!rt._help && config.sleep.helpers.includes(room.id);
  const target = r.helping ? Math.max(r.target, config.sleep.helperMax) : r.target;
  const wantOn = cool ? t >= target + half : t <= target - half;
  const wantOff = cool ? t <= target - half : t >= target + half;
  const canSwitch = !r.lastSwitch || Date.now() - r.lastSwitch >= MIN_SWITCH_MS;
  if (r.running == null) r.running = cool ? t > target : t < target;
  else if (!r.running && wantOn && canSwitch) r.running = true;
  else if (r.running && wantOff && canSwitch) r.running = false;
  if (!r.running) { r.stage = null; return idleCmd(cool); }
  if (cool) return { hvac: 'cool', temp: Math.max(64, r.target - BOOST) };
  return { hvac: 'heat', temp: GENTLE && (!r.manual || r.helping) ? stagedHeat(r, t, target) : Math.min(80, target + BOOST) };
}
// Heat setpoint for a running unit: the target (+BOOST), but never more than MAX_GAP above where the unit's own
// sensor probably reads. Steps up at most every STEP_MS; if the room hasn't warmed in two steps' time (the unit
// isn't running because its sensor reads higher than estimated), it goes up 1° anyway.
function stagedHeat(r, t, target = r.target) {
  const want = Math.min(80, target + BOOST);
  const x = t - UNIT_OFFSET + MAX_GAP;            // highest setpoint that keeps the gap under the strip threshold
  const st = r.stage;
  const now = Date.now();
  let s;
  if (!st) s = Math.floor(x);
  else if (st.temp > x + 0.5 && t < st.room - 0.3) s = Math.floor(x);   // room cooled: gap got too big, come down now
  else if (now - st.at < STEP_MS) s = st.temp;
  else if (Math.floor(x) > st.temp) s = Math.floor(x);
  else if (now - st.at >= 2 * STEP_MS && t <= st.room + 0.2) s = st.temp + 1;
  else s = st.temp;
  s = Math.max(61, Math.min(want, s));
  if (!st || s !== st.temp) r.stage = { temp: s, at: now, room: t };
  return s;
}
async function controlRoom(room) {
  if (!states[room.climate] || states[room.climate].state === 'unavailable') return false;
  const r = roomRt(room);
  const wasRunning = r.running;
  const cmd = command(room);
  if (r.sent === key(cmd)) return false;
  await sendToRoom(room, cmd);
  r.sent = key(cmd);
  if (wasRunning !== r.running) r.lastSwitch = Date.now();
  saveRuntime();
  const t = num(states[room.tempSensor]);
  console.log(new Date().toISOString(), `${room.name}: ${r.hvac} ${r.target ?? ''} -> unit ${cmd.hvac} ${cmd.temp ?? ''}` +
    (r.helping ? ' [sleep hours: heating for ' + config.sleep.quietRoom + ']' : '') +
    (r.stage && cmd.hvac === 'heat' && cmd.temp < Math.min(80, r.target + BOOST) ? ' [warming up gently]' : '') +
    (t != null ? ` (room ${t}°)` : ''));
  return true;
}

let busy = false;
async function tick() {
  if (busy) return; busy = true;
  try {
    await poll();
    if (haError) return;
    await lookupAreaHumidity();
    const home = homeState();
    updateHelp();
    for (const room of config.rooms) {
      const r = roomRt(room);
      const d = desiredFor(room, new Date(), home);
      // the schedule takes over whenever its value changes; manual settings hold until then
      if (r.schedKey !== key(d)) {
        r.schedKey = key(d);
        if (d) setSetting(room, d.hvac, d.hvac === 'off' ? null : d.temp);
        saveRuntime();
      }
      try {
        if (await controlRoom(room)) await new Promise((res) => setTimeout(res, 3000)); // space out IR bursts
      } catch (e) { console.error(`${room.name}: ${e.message}`); }
    }
    power.recordRuntime(config.rooms, rt);
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
  const r = roomRt(room);
  return {
    ...room,
    humiditySensorUsed: hum,
    current: {
      available: !!c && c.state !== 'unavailable',
      hvac: r.hvac,                       // the thermostat setting
      target: r.hvac === 'off' ? r.target ?? null : r.target,
      running: r.hvac === 'off' ? false : r.running ?? null, // null: the unit's own thermostat decides
      helping: r.hvac !== 'off' && !!r.helping,  // sleep hours: heating the house for the quiet room
      unit: c ? { hvac: c.state, target: c.attributes.temperature ?? null } : null,
      temp: num(states[room.tempSensor]) ?? (c ? c.attributes.current_temperature ?? null : null),
      humidity: num(states[hum]),
    },
    scheduled: desired,
    next: desired ? nextChange(room, home) : null,
  };
}
const CONDITIONS = { 'clear-night': 'Clear', cloudy: 'Cloudy', exceptional: 'Unusual', fog: 'Fog', hail: 'Hail',
  lightning: 'Lightning', 'lightning-rainy': 'Storms', partlycloudy: 'Partly cloudy', pouring: 'Heavy rain',
  rainy: 'Rain', snowy: 'Snow', 'snowy-rainy': 'Sleet', sunny: 'Sunny', windy: 'Windy', 'windy-variant': 'Windy' };
// Outdoor conditions from HA's weather entity (WEATHER_ENTITY in .env, else the first weather.* found).
function outdoorView() {
  const id = process.env.WEATHER_ENTITY ||
    (states['weather.forecast_home'] ? 'weather.forecast_home' : Object.keys(states).find((k) => k.startsWith('weather.')));
  const w = id && states[id];
  if (!w || w.state === 'unavailable') return null;
  const a = w.attributes;
  const t = Number(a.temperature);
  if (!Number.isFinite(t)) return null;
  const unit = a.temperature_unit || '°F';
  return { temp: unit.includes('C') ? t * 9 / 5 + 32 : t, humidity: a.humidity ?? null,
           condition: CONDITIONS[w.state] || String(w.state).replace(/-/g, ' ') };
}
function stateView() {
  const home = homeState();
  return { ok: !haError, error: haError, lastPoll, home, outdoor: outdoorView(), scheduleEnabled: config.scheduleEnabled,
           mode: config.mode, away: config.away, sensorControl: config.sensorControl, sleep: config.sleep,
           sleepHelp: !!rt._help, rooms: config.rooms.map((r) => roomView(r, home)) };
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

// Requests from the LAN are read-only (kiosk view and status data), except from the devices in
// CONTROL_IPS (the Pi kiosk's touchscreen). Full control also comes through "tailscale serve",
// which connects from 127.0.0.1.
const CONTROL_IPS = (process.env.CONTROL_IPS || '').split(',').map((x) => x.trim()).filter(Boolean);
const ipOf = (req) => (req.socket.remoteAddress || '').replace(/^::ffff:/, '');
const canControl = (req) => ['127.0.0.1', '::1'].includes(ipOf(req)) || CONTROL_IPS.includes(ipOf(req));
const LAN_OK = new Set(['/kiosk', '/kiosk.html', '/power', '/api/state', '/api/power', '/healthz']);

const viewFor = (req) => ({ ...stateView(), canControl: canControl(req) });

http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  if (!canControl(req) && !(req.method === 'GET' && LAN_OK.has(url.pathname))) {
    res.writeHead(url.pathname === '/' ? 302 : 403, url.pathname === '/' ? { Location: '/kiosk' } : {});
    return res.end(url.pathname === '/' ? '' : 'Read-only on the home network. Use the Tailscale address to make changes.');
  }
  try {
    if (url.pathname === '/api/state' && req.method === 'GET') {
      if (Date.now() - lastPoll > 5000) await poll();
      return send(res, 200, viewFor(req));
    }
    if (url.pathname === '/api/config' && req.method === 'PUT') {
      const c = validate(await readBody(req));
      config = c; saveConfig(c); configVersion++;
      tick(); // sends only to rooms whose scheduled setting changed
      return send(res, 200, viewFor(req));
    }
    const m = url.pathname.match(/^\/api\/room\/([\w-]+)$/);
    if (m && req.method === 'POST') {
      const room = config.rooms.find((r) => r.id === m[1]);
      if (!room) return send(res, 404, { error: 'no such room' });
      const b = await readBody(req);
      const cur = roomRt(room);
      if (b.hvac === 'off') setSetting(room, 'off', cur.target, 'manual');
      else {
        const hvac = b.hvac === 'cool' || b.hvac === 'heat' ? b.hvac : (cur.hvac === 'cool' ? 'cool' : config.mode);
        setSetting(room, hvac, clampTemp(b.temp ?? cur.target ?? 70), 'manual');
      }
      saveRuntime();
      await controlRoom(room);
      await poll();
      return send(res, 200, viewFor(req));
    }
    if (url.pathname === '/api/power' && req.method === 'GET') {
      return send(res, 200, await power.get(config.rooms, url.searchParams.has('refresh') && canControl(req)));
    }
    if (url.pathname === '/healthz') return send(res, 200, { ok: !haError });
    // static files
    let f = url.pathname === '/' || url.pathname === '/power' ? '/index.html' : url.pathname === '/kiosk' ? '/kiosk.html' : url.pathname;
    f = path.join(PUBLIC, path.normalize(f).replace(/^(\.\.[\/\\])+/, ''));
    if (!f.startsWith(PUBLIC) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) return send(res, 404, { error: 'not found' });
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(f)] || 'application/octet-stream', 'Cache-Control': 'no-cache' });
    fs.createReadStream(f).pipe(res);
  } catch (e) {
    send(res, 400, { error: e.message });
  }
}).listen(PORT, LISTEN_HOST, () => console.log(`Thermostat listening on ${LISTEN_HOST}:${PORT}; HA at ${HA_URL}`));

if (!HA_URL || !HA_TOKEN) console.error('HA_URL and HA_TOKEN must be set in .env');
disableOldAutomation().then(tick).then(() => power.get(config.rooms)); // warm the Power tab's data
setInterval(tick, 30000);
for (const sig of ['SIGTERM', 'SIGINT']) process.on(sig, () => { power.flushRuntime(); process.exit(0); });
