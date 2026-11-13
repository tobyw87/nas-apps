# thermostat

Web page for the three Amana wall units (office, living room, bedroom): current temperature and
humidity per room, manual control, the heating/cooling schedule with away mode, and a **Power** tab
for electricity use. A kiosk view at `/kiosk` is made for the Pi slideshow.

- Lives in `/volume1/docker/thermostat` (Node 22, no npm packages; state in `data/config.json`).
- Talks to Home Assistant's REST API with a long-lived token in `.env`. HA controls the units
  through the SmartIR climate entities (`climate.office_heat`, `climate.living_room_heat`,
  `climate.bedroom_heat`, code file 9901 = Gree protocol in °F).
- **The schedule runs in this app** (checked every 30 s). It only sends a command when the
  scheduled value changes, so a manual change holds until the next scheduled change. On startup
  it switches off the old HA automation `amana_heat_schedule` so the two never fight.
- Away mode uses `zone.home` (number of people home, from the HA Companion app).
- Listens only on `127.0.0.1:8120`; reached over Tailscale at
  `https://treebeard.taileefdd9.ts.net:8445/` via `tailscale serve`.

Install / update: `sudo sh /volume1/docker/thermostat/install.sh` (never overwrites `.env` or `data/`).
Logs: `cd /volume1/docker/thermostat && sudo docker compose logs --tail 50 thermostat`.

## Room-sensor control

With **Use room sensors** on (Schedule tab → General), the Govee room sensors decide when each unit
runs instead of the unit's own thermostat, which reads cold air at the wall. With a buffer of B°, a
room set to T heats when it drops to T − B/2 and stops at T + B/2. A running unit is sent T + 1°
(`BOOST_DEGREES` in `.env`) so its own thermostat doesn't stop it early; a stopped unit is switched off.
The small push is to keep the units on their heat pump: with T + 4° the electric strips came on a lot
(Nov 2026: about twice the energy per degree of cold).

**Gentle warm-up (default on, `GENTLE_WARMUP=off` to disable)** — for scheduled temperatures only; a
temperature set by hand on the page or the kiosk goes straight to the unit (target + 1°). Tested Nov 11 2026 on the office unit
(56° outside): the units switch from heat pump to electric strips when their own sensor is ~4° below the
setpoint, and their sensor reads ~3–4° below the room sensor. So a running unit's heat setpoint is capped at
room − `UNIT_SENSOR_OFFSET` (4) + `MAX_SETPOINT_GAP` (3), i.e. about 1° below the room, and raised as the room
warms, at most every `STEP_MINUTES` (5). Each step is one beep. If the room hasn't warmed in 10 minutes the
setpoint goes up 1° anyway (the unit's sensor read higher than estimated). The Rooms tab shows
"Heating to 70° · step 67°" while this is happening. `SKIP_TEMPS` (default `69`): setpoints whose IR code
the units misread (69°F came out as 68°F); heat setpoints step past them downward. `IDLE_MODE=heat` would idle units in Heat at 61°
instead of off, but the units' own sensors read below 61° at the wall, so they'd keep running. Units never switch on/off more than once
every 5 minutes (`MIN_SWITCH_MINUTES` in `.env` to change). If a room's sensor is unavailable, that
unit falls back to its own thermostat at the set temperature. Runtime state is kept in `data/state.json`.

## Power tab

`/#power` (Tailscale) or `http://192.168.50.133:8120/power` (home network, read-only).

- **Daily use** comes from Home Assistant's ComEd (Opower) long-term statistics over HA's WebSocket API
  (every `opower:*elec*energy_consumption` statistic is added together, so an old and a new account join up).
  A recent day counts as finished once it has 23+ hours of data.
- **Outdoor temperatures** are daily means from Open-Meteo (free, no key) for `zone.home`'s location
  (`LATITUDE`/`LONGITUDE` in `.env` to override), cached in `data/weather.json`.
- **Weather model:** kWh/day = base + heat slope × degrees below a balance point (+ a cooling term above
  another), fitted to the past year with recent weeks weighted most (60-day half-life). The balance points
  are chosen by best fit (BIC). The last 7 finished days are judged against a model trained on the days
  before them once there are 8+ days at 50° or colder; until then the model includes this week and the
  page says the comparison is rough.
- **Bill projection:** ComEd's usage to date + expected use for the rest of the cycle at the forecast
  temperatures (last year's temperatures past the 16-day forecast). Cycle end comes from Opower's
  end-date sensor if present, else start + 29 days.
- **Heating hours** per room are logged by the scheduler in `data/runtime-log.json` (120 days kept).
- `/api/power` returns all of it as JSON (cached 10 minutes).
