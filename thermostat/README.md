# thermostat

Web page for the three Amana wall units (office, living room, bedroom): current temperature and
humidity per room, manual control, and the heating/cooling schedule with away mode. A kiosk view
at `/kiosk` is made for the Pi slideshow.

- Lives in `/volume1/docker/thermostat` (Node 20, no npm packages; state in `data/config.json`).
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
room set to T heats when it drops to T − B/2 and switches the unit off at T + B/2; a running unit is
sent T + 4° so its own thermostat doesn't stop it early. Units never switch on/off more than once
every 5 minutes (`MIN_SWITCH_MINUTES` in `.env` to change). If a room's sensor is unavailable, that
unit falls back to its own thermostat at the set temperature. Runtime state is kept in `data/state.json`.
