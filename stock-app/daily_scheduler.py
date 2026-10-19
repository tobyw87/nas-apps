#!/usr/bin/env python3
"""Background jobs for the stock app, for servers without cron:
  * market_refresh.py every 30 minutes on weekdays during US market hours (8:30 AM - 3:00 PM Central), so the app
    opens instantly from saved data instead of scanning Yahoo on every visit
  * daily_scan.py once each weekday at 3:30 PM Central (Track Record picks)
US market holidays are skipped.

Started as its own small Docker container that shares the ~/stockapp folder with the app:
    docker run -d --name stock-daily-scan --restart unless-stopped \\
        -v "$HOME/stockapp:/app" -v /usr/share/zoneinfo:/usr/share/zoneinfo:ro \\
        --entrypoint python3 tobyw87-stock-radar:live /app/daily_scheduler.py

Settings (optional, as -e VAR=value on docker run):
    SCAN_TIME=15:30          daily Track Record scan, 24-hour clock
    SCAN_TZ=America/Chicago  time zone for all the times here (handles CDT / CST changes)
    REFRESH_START=08:30      first market refresh of the day
    REFRESH_END=15:00        last market refresh of the day
    REFRESH_EVERY=30         minutes between market refreshes
On start-up it catches up: a missing or out-of-date snapshot is refreshed right away, and today's daily scan runs if
its time has passed and it hasn't run yet.
"""
import datetime
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN_SCRIPT = os.path.join(HERE, "daily_scan.py")
REFRESH_SCRIPT = os.path.join(HERE, "market_refresh.py")
SCAN_TIME = os.environ.get("SCAN_TIME", "15:30")
SCAN_TZ = os.environ.get("SCAN_TZ", "America/Chicago")
REFRESH_START = os.environ.get("REFRESH_START", "08:30")
REFRESH_END = os.environ.get("REFRESH_END", "15:00")
REFRESH_EVERY = int(os.environ.get("REFRESH_EVERY", "30"))


def get_tz(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


def data_dir():
    for path in (os.environ.get("SENTIMENT_DATA_DIR"), "/app/data", os.path.join(HERE, "data")):
        if path and os.path.isdir(path):
            return path
    return HERE


def log(msg, tz):
    now = datetime.datetime.now(tz) if tz else datetime.datetime.now()
    line = f"[{now.strftime('%Y-%m-%d %H:%M:%S %Z')}] scheduler: {msg}"
    print(line, flush=True)
    try:
        with open(os.path.join(data_dir(), "daily_scan.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def parse_time(text):
    hh, mm = text.strip().split(":")
    return int(hh), int(mm)


# --- US stock market holidays (NYSE rules), so nothing runs when the market is closed ---
def _nth_weekday(year, month, weekday, n):
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(weekday - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def _last_weekday(year, month, weekday):
    d = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1) if month < 12 else datetime.date(year, 12, 31)
    return d - datetime.timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return datetime.date(year, month, day)


def _observed(d):
    if d.weekday() == 5:
        return d - datetime.timedelta(days=1)       # Saturday holiday: closed Friday
    if d.weekday() == 6:
        return d + datetime.timedelta(days=1)       # Sunday holiday: closed Monday
    return d


def market_holidays(year):
    days = {
        _observed(datetime.date(year, 1, 1)),                      # New Year's Day
        _nth_weekday(year, 1, 0, 3),                               # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),                               # Presidents' Day
        _easter(year) - datetime.timedelta(days=2),                # Good Friday
        _last_weekday(year, 5, 0),                                 # Memorial Day
        _observed(datetime.date(year, 6, 19)),                     # Juneteenth
        _observed(datetime.date(year, 7, 4)),                      # Independence Day
        _nth_weekday(year, 9, 0, 1),                               # Labor Day
        _nth_weekday(year, 11, 3, 4),                              # Thanksgiving
        _observed(datetime.date(year, 12, 25)),                    # Christmas
    }
    # New Year's Day on a Saturday is not made up on the Friday before (NYSE rule)
    days.discard(datetime.date(year - 1, 12, 31))
    return days


def is_trading_day(d):
    return d.weekday() < 5 and d not in market_holidays(d.year)


def day_events(day, tz):
    """[(when, job)] for one date: market refreshes every REFRESH_EVERY minutes, then the daily scan."""
    if not is_trading_day(day):
        return []
    events = []
    sh, sm = parse_time(REFRESH_START)
    eh, em = parse_time(REFRESH_END)
    t = datetime.datetime(day.year, day.month, day.day, sh, sm, tzinfo=tz)
    end = datetime.datetime(day.year, day.month, day.day, eh, em, tzinfo=tz)
    while t <= end:
        events.append((t, "refresh"))
        t += datetime.timedelta(minutes=REFRESH_EVERY)
    hh, mm = parse_time(SCAN_TIME)
    events.append((datetime.datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz), "daily"))
    return sorted(events)


def next_event(now):
    """The first scheduled (when, job) strictly after `now` (a timezone-aware datetime)."""
    day = now.date()
    for _ in range(15):
        for when, job in day_events(day, now.tzinfo):
            if when > now:
                return when, job
        day += datetime.timedelta(days=1)
    raise RuntimeError("no trading day in the next two weeks")


def _status_ts(name, key="finished_ts"):
    try:
        with open(os.path.join(data_dir(), name), encoding="utf-8") as f:
            return json.load(f).get(key)
    except (OSError, ValueError):
        return None


def ran_today(now):
    """True if daily_scan.py already finished today (in the scan time zone)."""
    finished = _status_ts("daily_scan_status.json")
    return bool(finished) and datetime.datetime.fromtimestamp(finished, now.tzinfo).date() == now.date()


def snapshot_age_minutes(now):
    path = os.path.join(data_dir(), "market_snapshot.pkl")
    if not os.path.exists(path):
        return None
    return (now.timestamp() - os.path.getmtime(path)) / 60.0


def refresh_due_at_start(now):
    """At start-up: refresh now if there's no snapshot, or the market is open and the snapshot is out of date."""
    age = snapshot_age_minutes(now)
    if age is None:
        return True
    events = [w for w, j in day_events(now.date(), now.tzinfo) if j == "refresh"]
    in_hours = bool(events) and events[0] <= now <= events[-1] + datetime.timedelta(minutes=REFRESH_EVERY)
    return in_hours and age > REFRESH_EVERY


def run_job(job, tz):
    script, label = (REFRESH_SCRIPT, "market refresh") if job == "refresh" else (SCAN_SCRIPT, "daily scan")
    log(f"starting {label}", tz)
    started = time.time()
    with open(os.path.join(data_dir(), "daily_scan.log"), "a", encoding="utf-8") as out:
        code = subprocess.call([sys.executable, script], stdout=out, stderr=subprocess.STDOUT, cwd=HERE)
    log(f"{label} finished in {(time.time() - started) / 60:.1f} min (exit code {code})", tz)


def main():
    tz = get_tz(SCAN_TZ)
    if tz is None:
        print(f"Time zone {SCAN_TZ!r} isn't available in this container. Mount the server's zone files with "
              "-v /usr/share/zoneinfo:/usr/share/zoneinfo:ro", flush=True)
        return 1
    now = datetime.datetime.now(tz)
    if refresh_due_at_start(now):
        log("market snapshot missing or out of date: refreshing now", tz)
        run_job("refresh", tz)
    hh, mm = parse_time(SCAN_TIME)
    now = datetime.datetime.now(tz)
    if is_trading_day(now.date()) and (now.hour, now.minute) >= (hh, mm) and not ran_today(now):
        log("today's daily scan hasn't run yet (catching up after a restart)", tz)
        run_job("daily", tz)
    while True:
        now = datetime.datetime.now(tz)
        when, job = next_event(now)
        log(f"next: {'market refresh' if job == 'refresh' else 'daily scan'} {when.strftime('%a %b %d at %I:%M %p %Z')}", tz)
        # Sleep in short steps so the wall clock (not elapsed time) decides when to run
        while datetime.datetime.now(tz) < when:
            time.sleep(min(60, max(1, (when - datetime.datetime.now(tz)).total_seconds())))
        # A refresh slot missed by more than 20 minutes (e.g. the server was asleep) is skipped rather than run late;
        # the daily scan always runs
        if job == "daily" or (datetime.datetime.now(tz) - when).total_seconds() <= 20 * 60:
            run_job(job, tz)


if __name__ == "__main__":
    sys.exit(main())
