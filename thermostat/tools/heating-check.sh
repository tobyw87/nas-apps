# Heating check: prints recent hourly data. Paste all of this into your NAS SSH session, then send Claude everything it prints.
cd /volume1/docker/thermostat && echo '== LOG' && sudo docker compose logs --no-log-prefix --since 168h thermostat 2>&1 | grep -E 'pre-heat|ran late|warm-up:|Sleep hours' | tail -40; echo '== DATA' && sudo docker compose exec -T thermostat node - <<'JS'
// Prints hourly data for the heating assessment (run inside the thermostat container).
(async () => {
  const fs = require('fs');
  const H = process.env.HA_URL, TOK = process.env.HA_TOKEN, TZ = 'America/Chicago';
  const START = '2026-11-05T06:00:00Z', NOW = Date.now(), T0 = Date.parse(START);
  const fmt = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  const P = (t) => { const p = Object.fromEntries(fmt.formatToParts(new Date(t)).map((x) => [x.type, x.value])); return { hk: `${p.month}-${p.day} ${p.hour}`, d: `${p.month}-${p.day}`, hm: `${p.hour}:${p.minute}` }; };
  const ws = (cmds) => new Promise((res, rej) => {
    const s = new WebSocket(H.replace(/^http/, 'ws') + '/api/websocket'); const out = []; let left = cmds.length;
    const tm = setTimeout(() => rej(new Error('ws timeout')), 60000);
    s.onmessage = (ev) => { const m = JSON.parse(ev.data);
      if (m.type === 'auth_required') s.send(JSON.stringify({ type: 'auth', access_token: TOK }));
      else if (m.type === 'auth_ok') cmds.forEach((c, i) => s.send(JSON.stringify({ id: i + 1, ...c })));
      else if (m.type === 'result') { if (!m.success) { clearTimeout(tm); return rej(new Error(JSON.stringify(m.error))); } out[m.id - 1] = m.result; if (!--left) { clearTimeout(tm); s.close(); res(out); } } };
  });
  const get = async (p) => (await fetch(H + p, { headers: { Authorization: 'Bearer ' + TOK } })).json();
  const rd = (f) => { try { return JSON.parse(fs.readFileSync('/data/' + f, 'utf8')); } catch { return null; } };
  const cfg = rd('config.json') || {};
  const rooms = (cfg.rooms || []).map((r) => ({ id: r.id, c: r.climate, s: r.tempSensor, blocks: r.blocks, setback: r.setback }));

  // hourly ComEd kWh
  const [ids] = await ws([{ type: 'recorder/list_statistic_ids', statistic_type: 'sum' }]);
  const sids = ids.map((x) => x.statistic_id).filter((s) => /^opower:/.test(s) && /elec/.test(s) && /energy_consumption$/.test(s));
  const [stats] = await ws([{ type: 'recorder/statistics_during_period', start_time: START, statistic_ids: sids, period: 'hour', types: ['change'], units: { energy: 'kWh' } }]);
  const kwh = {};
  for (const s of sids) for (const r of stats[s] || []) { const k = P(r.start).hk; kwh[k] = (kwh[k] || 0) + Number(r.change || 0); }

  // climate (commands) and room sensors, on a 1-minute grid
  // Home Assistant keeps ~10 days of detail; ask for the whole window (the API defaults to one day)
  const HS = new Date(Math.max(T0, NOW - 9.5 * 864e5)).toISOString(), HE = encodeURIComponent(new Date(NOW).toISOString());
  const cl = await get(`/api/history/period/${HS}?end_time=${HE}&filter_entity_id=${rooms.map((r) => r.c).join(',')}&significant_changes_only=0`);
  const se = await get(`/api/history/period/${HS}?end_time=${HE}&filter_entity_id=${rooms.map((r) => r.s).join(',')}&minimal_response&no_attributes&significant_changes_only=0`);
  const byId = {}; for (const a of [...cl, ...se]) if (a.length) byId[a[0].entity_id] = a;
  const hours = {}; const H_ = (k) => (hours[k] ||= { kwh: kwh[k], r: {} });
  const daily = {};
  for (const room of rooms) {
    const ca = (byId[room.c] || []).map((x) => ({ t: Date.parse(x.last_changed || x.last_updated), s: x.state, sp: x.attributes ? x.attributes.temperature : null }));
    const sa = (byId[room.s] || []).map((x) => ({ t: Date.parse(x.last_changed || x.last_updated), v: Number(x.state) })).filter((x) => Number.isFinite(x.v));
    let prev = null; // commands = real changes of mode or setpoint
    for (const x of ca) { if (prev && (x.s !== prev.s || x.sp !== prev.sp)) { const k = P(x.t).hk; const h = (H_(k).r[room.id] ||= {}); h.n = (h.n || 0) + 1; } prev = x; }
    let ci = 0, si = 0, cur = null, temp = null;
    for (let t = T0; t < NOW; t += 60000) {
      while (ci < ca.length && ca[ci].t <= t) cur = ca[ci++];
      while (si < sa.length && sa[si].t <= t) temp = sa[si++].v;
      const p = P(t); const h = (H_(p.hk).r[room.id] ||= {});
      if (cur && cur.s === 'heat') { h.on = (h.on || 0) + 1; h.sp = Math.max(h.sp || 0, cur.sp || 0); }
      if (temp != null) { h.ts = (h.ts || 0) + temp; h.tn = (h.tn || 0) + 1; }
      // comfort: room temp at each block start, and when it got within 0.5° of the block temp
      const dd = (daily[p.d] ||= {}); const rr = (dd[room.id] ||= {});
      for (const b of room.blocks || []) {
        if (!b.enabled || temp == null) continue;
        const dow = new Date(new Date(t).toLocaleString('en-US', { timeZone: TZ })).getDay();
        if (!b.days.includes(dow)) continue;
        const key = b.start + '>' + b.temp;
        if (p.hm === b.start) rr[key] = { at: temp };
        if (rr[key] && !rr[key].ok && temp >= b.temp - 0.5) rr[key].ok = p.hm;
      }
      if (room.id === 'bedroom' && temp != null) { const hh = +p.hm.slice(0, 2); if (hh >= 23 || hh < 9) rr.nightMin = Math.min(rr.nightMin ?? 99, temp); }
    }
  }
  const ids2 = rooms.map((r) => r.id);
  console.log('ROOMS', ids2.join(','), '| hour,kWh,' + ids2.map((i) => i + ':heatMin/maxSet/cmds/avgT').join(','));
  for (const [k, h] of Object.entries(hours).sort()) {
    console.log(k + ',' + (h.kwh == null ? '' : h.kwh.toFixed(2)) + ',' + ids2.map((i) => { const r = h.r[i] || {}; return `${r.on || 0}/${r.sp || ''}/${r.n || 0}/${r.tn ? (r.ts / r.tn).toFixed(1) : ''}`; }).join(','));
  }
  console.log('COMFORT', JSON.stringify(daily));
  console.log('CONFIG', JSON.stringify({ rooms: rooms.map((r) => ({ id: r.id, setback: r.setback, blocks: (r.blocks || []).map((b) => [b.start, b.end, b.temp, b.days.join(''), b.enabled]) })), sensorControl: cfg.sensorControl, sleep: cfg.sleep, preheat: cfg.preheat, away: cfg.away }));
  const st = rd('state.json') || {}; console.log('RATES', JSON.stringify(Object.fromEntries(Object.entries(st).filter(([k]) => !k.startsWith('_')).map(([k, v]) => [k, v.rate]))));
  console.log('RUNLOG', JSON.stringify(rd('runtime-log.json')));
})().catch((e) => console.log('ERROR', e.message));
JS
