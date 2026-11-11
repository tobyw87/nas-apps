// Electricity for the Power tab.
//  - Daily use: Home Assistant's Opower (ComEd) long-term statistics, read over HA's WebSocket API.
//  - Outdoor temperature: daily means from Open-Meteo (free, no key) for zone.home's location.
//  - Weather model: kWh/day = base + heatSlope * max(0, balance - temp) [+ coolSlope * max(0, temp - coolBalance)],
//    fitted to past days (recent days count more). "Expected" always means "for that day's weather".
//  - Heating hours per room come from the thermostat's own on/off decisions (room-sensor control).
'use strict';
const fs = require('fs');
const path = require('path');

const DAY_MS = 864e5;
const HISTORY_DAYS = 420;
const HALF_LIFE_DAYS = 60;      // recency weighting when fitting the model
const WEEK = 7, USUAL_DAYS = 56; // "this week" vs the 8 weeks before it

module.exports = function createPower({ HA_URL, HA_TOKEN, TZ, DATA_DIR, getStates }) {
  const ymdFmt = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' });
  const ymd = (d) => ymdFmt.format(d);
  const addDays = (s, n) => new Date(Date.parse(s + 'T12:00:00Z') + n * DAY_MS).toISOString().slice(0, 10);
  const daysBetween = (a, b) => Math.round((Date.parse(b + 'T12:00:00Z') - Date.parse(a + 'T12:00:00Z')) / DAY_MS);
  const round = (v, n = 1) => (v == null || !Number.isFinite(v) ? null : Math.round(v * 10 ** n) / 10 ** n);
  const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);

  const file = (n) => path.join(DATA_DIR, n);
  const readJson = (n, dflt) => { try { return JSON.parse(fs.readFileSync(file(n), 'utf8')); } catch { return dflt; } };
  const writeJson = (n, v) => {
    try { fs.writeFileSync(file(n) + '.tmp', JSON.stringify(v)); fs.renameSync(file(n) + '.tmp', file(n)); }
    catch (e) { console.error(`Could not save ${n}:`, e.message); }
  };

  // ---------- Home Assistant WebSocket (long-term statistics aren't in the REST API) ----------
  function haWs(commands, timeoutMs = 20000) {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(HA_URL.replace(/^http/, 'ws') + '/api/websocket');
      const results = new Array(commands.length); let left = commands.length;
      const timer = setTimeout(() => { try { ws.close(); } catch {} reject(new Error('Home Assistant WebSocket timed out')); }, timeoutMs);
      const done = (err) => { clearTimeout(timer); try { ws.close(); } catch {} err ? reject(err) : resolve(results); };
      ws.onerror = (e) => done(new Error('Home Assistant WebSocket error' + (e && e.message ? ': ' + e.message : '')));
      ws.onmessage = (ev) => {
        let m; try { m = JSON.parse(ev.data); } catch { return; }
        if (m.type === 'auth_required') ws.send(JSON.stringify({ type: 'auth', access_token: HA_TOKEN }));
        else if (m.type === 'auth_invalid') done(new Error('Home Assistant rejected the token'));
        else if (m.type === 'auth_ok') commands.forEach((c, i) => ws.send(JSON.stringify({ id: i + 1, ...c })));
        else if (m.type === 'result') {
          if (!m.success) return done(new Error('HA: ' + ((m.error && m.error.message) || 'request failed')));
          results[m.id - 1] = m.result;
          if (--left === 0) done();
        }
      };
    });
  }

  // Daily kWh (all Opower electric accounts added together, so an old and a new ComEd account join up)
  // and how many hours each recent day has, to tell finished days from ones still filling in.
  async function loadUsage() {
    const [ids] = await haWs([{ type: 'recorder/list_statistic_ids', statistic_type: 'sum' }]);
    const sids = (ids || []).map((x) => x.statistic_id)
      .filter((s) => /^opower:/.test(s) && /elec/.test(s) && /energy_consumption$/.test(s));
    if (!sids.length) throw new Error('No ComEd (Opower) electricity statistics found in Home Assistant');
    const now = new Date();
    const [daily, hourly] = await haWs([
      { type: 'recorder/statistics_during_period', start_time: new Date(now - HISTORY_DAYS * DAY_MS).toISOString(),
        statistic_ids: sids, period: 'day', types: ['change'], units: { energy: 'kWh' } },
      { type: 'recorder/statistics_during_period', start_time: new Date(now - 6 * DAY_MS).toISOString(),
        statistic_ids: sids, period: 'hour', types: ['change'], units: { energy: 'kWh' } },
    ]);
    const toDate = (t) => ymd(new Date(typeof t === 'number' ? t : Date.parse(t)));
    const kwh = {}, hours = {};
    for (const sid of sids) {
      for (const r of (daily || {})[sid] || []) {
        const ch = Number(r.change);
        if (!Number.isFinite(ch) || ch < 0) continue;
        const d = toDate(r.start); kwh[d] = (kwh[d] || 0) + ch;
      }
      const seen = {};
      for (const r of (hourly || {})[sid] || []) {
        if (!Number.isFinite(Number(r.change))) continue;
        const d = toDate(r.start); (seen[d] ||= new Set()).add(r.start);
      }
      for (const [d, s] of Object.entries(seen)) hours[d] = Math.max(hours[d] || 0, s.size);
    }
    const dates = Object.keys(kwh).sort();
    const recentCut = ymd(new Date(now - 5 * DAY_MS));
    return dates.map((d) => ({
      date: d, kwh: kwh[d],
      // the last few days only count once they have (nearly) every hour; older days are complete
      complete: d < recentCut ? true : (hours[d] || 0) >= 23,
    }));
  }

  // ---------- outdoor temperatures (Open-Meteo), cached in data/weather.json ----------
  let wx = readJson('weather.json', { key: '', days: {}, fc: {}, fcAt: 0, archAt: 0 });
  function location() {
    const z = getStates()['zone.home'];
    const lat = Number(process.env.LATITUDE || (z && z.attributes.latitude));
    const lon = Number(process.env.LONGITUDE || (z && z.attributes.longitude));
    return Number.isFinite(lat) && Number.isFinite(lon) && (lat || lon) ? { lat: round(lat, 2), lon: round(lon, 2) } : null;
  }
  async function getJson(url) {
    const r = await fetch(url, { signal: AbortSignal.timeout(15000) });
    if (!r.ok) throw new Error(`weather service ${r.status}`);
    return r.json();
  }
  async function loadWeather() {
    const loc = location();
    if (!loc) throw new Error('No home location in Home Assistant (zone.home) for the outdoor temperatures');
    const key = loc.lat + ',' + loc.lon;
    if (wx.key !== key) wx = { key, days: {}, fc: {}, fcAt: 0, archAt: 0 };
    const tz = encodeURIComponent(TZ), today = ymd(new Date());
    let changed = false;
    if (Date.now() - wx.fcAt > 3 * 3600e3) {
      const j = await getJson(`https://api.open-meteo.com/v1/forecast?latitude=${loc.lat}&longitude=${loc.lon}` +
        `&daily=temperature_2m_mean&past_days=92&forecast_days=16&temperature_unit=fahrenheit&timezone=${tz}`);
      wx.fc = {};
      (j.daily.time || []).forEach((d, i) => {
        const t = j.daily.temperature_2m_mean[i];
        if (t == null) return;
        if (d < today) wx.days[d] = t; else wx.fc[d] = t; // today is still part forecast
      });
      wx.fcAt = Date.now(); changed = true;
    }
    if (Date.now() - wx.archAt > 20 * 3600e3) {
      try {
        const j = await getJson(`https://archive-api.open-meteo.com/v1/archive?latitude=${loc.lat}&longitude=${loc.lon}` +
          `&start_date=${addDays(today, -HISTORY_DAYS - 10)}&end_date=${addDays(today, -80)}&daily=temperature_2m_mean` +
          `&temperature_unit=fahrenheit&timezone=${tz}`);
        (j.daily.time || []).forEach((d, i) => { const t = j.daily.temperature_2m_mean[i]; if (t != null && !(d in wx.days)) wx.days[d] = t; });
        wx.archAt = Date.now(); changed = true;
      } catch (e) { console.error('Weather history unavailable:', e.message); }
    }
    const oldest = addDays(today, -HISTORY_DAYS - 400);
    for (const d of Object.keys(wx.days)) if (d < oldest) { delete wx.days[d]; changed = true; }
    if (changed) writeJson('weather.json', wx);
    return wx;
  }
  // A day's temperature: what happened, else the forecast, else the same date last year.
  function tempFor(d) {
    if (d in wx.days) return { t: wx.days[d], src: 'observed' };
    if (d in wx.fc) return { t: wx.fc[d], src: 'forecast' };
    const ly = addDays(d, -364);
    for (const x of [ly, addDays(ly, -1), addDays(ly, 1)]) if (x in wx.days) return { t: wx.days[x], src: 'last-year' };
    return null;
  }

  // ---------- the weather model ----------
  function solve(X, y, w) {
    const k = X[0].length, A = Array.from({ length: k }, () => new Array(k + 1).fill(0));
    for (let i = 0; i < X.length; i++) for (let a = 0; a < k; a++) {
      A[a][k] += w[i] * X[i][a] * y[i];
      for (let c = 0; c < k; c++) A[a][c] += w[i] * X[i][a] * X[i][c];
    }
    for (let c = 0; c < k; c++) {
      let p = c; for (let r = c + 1; r < k; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
      if (Math.abs(A[p][c]) < 1e-9) return null;
      [A[c], A[p]] = [A[p], A[c]];
      for (let r = 0; r < k; r++) if (r !== c) { const f = A[r][c] / A[c][c]; for (let j = c; j <= k; j++) A[r][j] -= f * A[c][j]; }
    }
    return A.map((row, i) => row[k] / row[i]);
  }
  const predict = (m, t) => m.base + (m.heatSlope ? m.heatSlope * Math.max(0, m.balance - t) : 0) +
    (m.coolSlope ? m.coolSlope * Math.max(0, t - m.coolBalance) : 0);

  function fit(days, refDate) {
    if (days.length < 14) return null;
    const y = days.map((d) => d.kwh);
    const w = days.map((d) => 0.5 ** (daysBetween(d.date, refDate) / HALF_LIFE_DAYS));
    const W = w.reduce((a, b) => a + b, 0), N = days.length;
    let best = null;
    const consider = (tb, tc) => {
      const cols = [];
      const H = tb == null ? null : days.map((d) => Math.max(0, tb - d.temp));
      const C = tc == null ? null : days.map((d) => Math.max(0, d.temp - tc));
      if (H && H.filter((v) => v > 2).length < 3) return;
      if (C && C.filter((v) => v > 2).length < 3) return;
      const X = days.map((_, i) => [1, ...(H ? [H[i]] : []), ...(C ? [C[i]] : [])]);
      const b = solve(X, y, w); if (!b) return;
      if (b.slice(1).some((v) => v <= 0) || b[0] < 0) return;
      let sse = 0; X.forEach((row, i) => { const e = y[i] - row.reduce((s, v, j) => s + v * b[j], 0); sse += w[i] * e * e; });
      const k = b.length, score = N * Math.log(Math.max(sse, 1e-9) / W) + k * Math.log(N); // BIC: extra terms must earn their place
      if (!best || score < best.score) best = { score, sse, base: b[0], heatSlope: H ? b[1] : 0, balance: tb, coolSlope: C ? b[b.length - 1] : 0, coolBalance: tc };
    };
    consider(null, null);
    for (let tb = 45; tb <= 66; tb++) { consider(tb, null); for (let tc = Math.max(62, tb + 4); tc <= 80; tc += 2) consider(tb, tc); }
    for (let tc = 62; tc <= 80; tc += 2) consider(null, tc);
    if (!best) return null;
    const ybar = y.reduce((s, v, i) => s + w[i] * v, 0) / W;
    const sst = y.reduce((s, v, i) => s + w[i] * (v - ybar) ** 2, 0);
    return {
      base: best.base, heatSlope: best.heatSlope, balance: best.balance, coolSlope: best.coolSlope, coolBalance: best.coolBalance,
      r2: sst > 0 ? Math.max(0, 1 - best.sse / sst) : null, sigma: Math.sqrt(best.sse / W),
      trainDays: N, from: days[0].date, to: days[days.length - 1].date,
      coldDays: days.filter((d) => d.temp <= 50).length,
    };
  }

  // ---------- bill cycle (Opower "current bill" sensors) ----------
  function billSensors() {
    const st = getStates(); const pick = (suffix) => {
      const c = Object.values(st).filter((s) => s.entity_id.startsWith('sensor.') && s.entity_id.endsWith(suffix) &&
        !['unknown', 'unavailable', ''].includes(s.state));
      c.sort((a, b) => String(b.last_updated).localeCompare(String(a.last_updated)));
      return c[0] || null;
    };
    const n = (s) => (s && Number.isFinite(Number(s.state)) ? Number(s.state) : null);
    const dt = (s) => (s && /^\d{4}-\d{2}-\d{2}/.test(s.state) ? s.state.slice(0, 10) : null);
    const budget = st['sensor.electric_monthly_budget'];
    return {
      used: n(pick('_current_bill_electric_usage_to_date')), forecast: n(pick('_current_bill_electric_forecasted_usage')),
      cost: n(pick('_current_bill_electric_cost_to_date')), forecastCost: n(pick('_current_bill_electric_forecasted_cost')),
      typical: n(pick('_typical_monthly_electric_usage')),
      start: dt(pick('_current_bill_electric_start_date')), end: dt(pick('_current_bill_electric_end_date')),
      budget: n(budget),
    };
  }

  // ---------- heating hours per room (recorded by the scheduler every tick) ----------
  let rlog = readJson('runtime-log.json', {}); let lastAt = 0, lastSave = 0;
  function recordRuntime(rooms, rt) {
    const now = Date.now();
    if (lastAt) {
      const mins = Math.min(now - lastAt, 120000) / 60000, d = ymd(new Date(now));
      const day = (rlog[d] ||= {});
      for (const room of rooms) {
        const r = rt[room.id]; if (!r || r.hvac === 'off') continue;
        const k = r.running === true ? room.id : r.running == null ? room.id + ':unit' : null; // :unit = the unit's own thermostat decides
        if (k) day[k] = round((day[k] || 0) + mins, 2);
      }
    }
    lastAt = now;
    if (now - lastSave > 5 * 60000) {
      const cut = ymd(new Date(now - 120 * DAY_MS));
      for (const d of Object.keys(rlog)) if (d < cut) delete rlog[d];
      writeJson('runtime-log.json', rlog); lastSave = now;
    }
  }
  function flushRuntime() { writeJson('runtime-log.json', rlog); }

  // ---------- put it together ----------
  async function compute(rooms) {
    const today = ymd(new Date());
    const [usage, weatherErr] = await Promise.all([loadUsage(), loadWeather().then(() => null, (e) => e.message)]);
    const days = usage.map((u) => ({ ...u, temp: (wx.days[u.date] ?? null) }));
    const complete = days.filter((d) => d.complete && d.temp != null);
    const lastDate = days.length ? days[days.length - 1].date : null;

    // judge the latest 7 finished days against a model trained on the days before them, once there are enough
    // cold days to learn from; until then train on everything (including this week) and say so.
    const week = complete.slice(-WEEK);
    const before = complete.slice(0, -WEEK).filter((d) => daysBetween(d.date, week[0] ? week[0].date : today) <= 365);
    const enoughCold = before.filter((d) => d.temp <= 50).length >= 8 && before.length >= 28;
    const train = enoughCold ? before : complete.filter((d) => daysBetween(d.date, today) <= 365);
    const model = weatherErr ? null : fit(train, today);
    if (model) model.includesThisWeek = !enoughCold;
    const exp = (t) => (model && t != null ? predict(model, t) : null);

    const daily = days.slice(-35).map((d) => ({
      date: d.date, kwh: round(d.kwh, 2), complete: d.complete, temp: round(d.temp),
      expected: round(exp(d.temp), 2),
    }));

    // this week vs usual (the 8 weeks before it), and how much of the change the weather explains
    let verdict = null;
    if (week.length >= 5) {
      const usualDays = complete.slice(0, -WEEK).slice(-USUAL_DAYS);
      const wk = mean(week.map((d) => d.kwh)), wkExp = model ? mean(week.map((d) => exp(d.temp))) : null;
      const usual = usualDays.length >= 14 ? mean(usualDays.map((d) => d.kwh)) : null;
      const usualExp = model && usual != null ? mean(usualDays.map((d) => exp(d.temp))) : null;
      const pct = wkExp ? (wk / wkExp - 1) * 100 : null;
      verdict = {
        from: week[0].date, to: week[week.length - 1].date, days: week.length,
        actual: round(wk), expected: round(wkExp), pct: round(pct, 0),
        level: pct == null ? null : pct <= -10 ? 'below' : pct < 10 ? 'expected' : pct < 25 ? 'above' : 'well-above',
        temp: round(mean(week.map((d) => d.temp))),
        usual: round(usual), usualTemp: usual != null ? round(mean(usualDays.map((d) => d.temp))) : null,
        weatherEffect: usualExp != null ? round(wkExp - usualExp) : null,
        otherEffect: usualExp != null ? round(wk - usual - (wkExp - usualExp)) : null,
      };
    }

    // bill cycle: what's used so far plus the expected use for the rest of the cycle at the forecast weather
    const b = billSensors();
    let bill = null;
    if (b.used != null) {
      let start = b.start;
      if (!start && lastDate) { // the first day whose running total back from the latest reported day reaches the bill's usage
        let s = 0; for (let i = days.length - 1; i >= 0; i--) { s += days[i].kwh; start = days[i].date; if (s >= b.used - 0.5) break; }
      }
      const end = b.end || (start ? addDays(start, 29) : null); // last day of the cycle
      const rate = b.cost && b.used ? b.cost / b.used : null;
      let rest = 0, restDays = 0, fcDays = 0, lyDays = 0;
      if (model && end && lastDate) {
        for (let d = addDays(lastDate, 1); d <= end; d = addDays(d, 1)) {
          const t = tempFor(d); if (!t) continue;
          rest += predict(model, t.t); restDays++; if (t.src === 'forecast') fcDays++; if (t.src === 'last-year') lyDays++;
        }
      }
      const projected = model && end ? b.used + rest : null;
      bill = {
        start, end, endEstimated: !b.end, through: lastDate, daysLeft: end ? Math.max(0, daysBetween(today, end) + 1) : null,
        used: round(b.used), cost: round(b.cost, 2), rate: round(rate, 3),
        comedForecast: round(b.forecast), comedForecastCost: round(b.forecastCost, 2), typical: b.typical ? round(b.typical) : null,
        projected: round(projected), projectedCost: projected != null && rate ? round(projected * rate, 2) : null,
        restDays, forecastDays: fcDays, lastYearDays: lyDays, budget: b.budget,
      };
    }

    // the next week at the forecast temperatures
    const outlook = [];
    if (model) for (let i = 0; i < 7; i++) {
      const d = addDays(today, i), t = tempFor(d);
      if (t && t.src !== 'last-year') outlook.push({ date: d, temp: round(t.t), kwh: round(predict(model, t.t)), cost: bill && bill.rate ? round(predict(model, t.t) * bill.rate, 2) : null });
    }

    // what to expect at different outdoor temperatures
    const guide = model ? [70, 60, 50, 40, 30, 20, 10, 0].map((t) => ({ temp: t, kwh: round(predict(model, t)), cost: bill && bill.rate ? round(predict(model, t) * bill.rate, 2) : null })) : [];

    // months: actual vs expected for the weather
    const monthsMap = {};
    for (const d of complete) {
      const m = d.date.slice(0, 7); const o = (monthsMap[m] ||= { month: m, kwh: 0, expected: 0, days: 0, tsum: 0 });
      o.kwh += d.kwh; o.expected += exp(d.temp) || 0; o.days++; o.tsum += d.temp;
    }
    const months = Object.values(monthsMap).sort((a, c) => a.month.localeCompare(c.month)).slice(-13)
      .map((o) => ({ month: o.month, kwh: round(o.kwh, 0), expected: model ? round(o.expected, 0) : null, days: o.days, temp: round(o.tsum / o.days), partial: o.month === today.slice(0, 7) }));

    // heating hours by room
    const dates = []; for (let i = 6; i >= 0; i--) dates.push(addDays(today, -i));
    const heating = {
      dates,
      rooms: rooms.map((r) => ({
        id: r.id, name: r.name,
        hours: dates.map((d) => round(((rlog[d] || {})[r.id] || 0) / 60)),
        unitHours: dates.map((d) => round(((rlog[d] || {})[r.id + ':unit'] || 0) / 60)),
      })),
      since: Object.keys(rlog).sort()[0] || null,
    };

    return {
      ok: true, at: Date.now(), today, weatherError: weatherErr, model: model && {
        base: round(model.base), heatSlope: round(model.heatSlope, 2), balance: model.balance,
        coolSlope: round(model.coolSlope, 2), coolBalance: model.coolBalance, r2: round(model.r2, 2), sigma: round(model.sigma),
        trainDays: model.trainDays, from: model.from, to: model.to, coldDays: model.coldDays, includesThisWeek: model.includesThisWeek,
      },
      verdict, bill, daily, outlook, guide, months, heating,
    };
  }

  let cache = null, inflight = null, failedAt = 0, failure = null;
  async function get(rooms, force) {
    if (!force && cache && Date.now() - cache.at < 10 * 60000) return cache;
    if (!force && failure && Date.now() - failedAt < 2 * 60000) return failure; // don't hammer HA after an error
    if (!inflight) inflight = compute(rooms)
      .then((v) => { failure = null; return (cache = v); })
      .catch((e) => {
        console.error('Power:', e.message); failedAt = Date.now();
        return (failure = cache ? { ...cache, stale: e.message } : { ok: false, error: e.message });
      })
      .finally(() => { inflight = null; });
    return inflight;
  }

  return { get, recordRuntime, flushRuntime, _test: { fit, predict } };
};
