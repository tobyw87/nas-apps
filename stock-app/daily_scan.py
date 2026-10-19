#!/usr/bin/env python3
"""Automatic daily scan for the Sentiment Engine (no browser needed).

Runs the Recommended Stocks page once, headlessly, at the Track Record's pinned coverage level. That
records a score-history reading for every scanned stock and saves today's Track Record picks.

Schedule it on the server with cron, e.g. weekdays at 3:30 PM Central, after the US market closes:
    30 15 * * 1-5  docker exec stock-sentiment-app python3 /app/daily_scan.py >> ~/stockapp/data/daily_scan.log 2>&1

Run it by hand to test:
    sudo docker exec stock-sentiment-app python3 /app/daily_scan.py
"""
import datetime
import json
import os
import sys
import time

os.environ.setdefault("STREAMLIT_LOGGER_LEVEL", "error")   # keep the log readable

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.environ.get("STOCKAPP_PATH", os.path.join(HERE, "stock_app.py"))
TIMEOUT_SECONDS = 4 * 3600          # the largest coverage level can take a while on a cold start


def data_dir():
    for path in (os.environ.get("SENTIMENT_DATA_DIR"), "/app/data", os.path.join(HERE, "data")):
        if path and os.path.isdir(path) and os.access(path, os.W_OK):
            return path
    return None


def market_date(ts):
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/New_York")
    except Exception:
        tz = datetime.timezone(datetime.timedelta(hours=-5))
    return datetime.datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")


def write_status(status):
    folder = data_dir()
    if not folder:
        return
    tmp = os.path.join(folder, ".daily_scan_status.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f)
    os.replace(tmp, os.path.join(folder, "daily_scan_status.json"))


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def main():
    started = time.time()
    status = {"started_ts": started, "finished_ts": None, "ok": False, "message": "", "coverage": ""}
    log(f"Daily scan starting ({APP})")
    try:
        import logging
        import streamlit.logger as streamlit_logger
        from streamlit.testing.v1 import AppTest

        # Keep the log to the summary lines: hide Streamlit's "missing ScriptRunContext" chatter from the scan
        # threads (harmless in a headless run) and yfinance's per-request connection messages
        streamlit_logger.set_log_level("error")
        for name in ("yfinance", "peewee", "urllib3"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
        logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").disabled = True

        at = AppTest.from_file(APP, default_timeout=TIMEOUT_SECONDS)
        at.session_state["_daily_job"] = True
        at.session_state["current_view"] = "🏆 Recommended Stocks"
        at.run()
        if at.exception:
            raise RuntimeError("; ".join(str(e.value)[:400] for e in at.exception))

        coverage_line = next((c.value for c in at.caption if c.value.startswith("📡")), "")
        coverage_line = coverage_line.replace("**", "").replace("&nbsp;", " ").replace("📡 ", "")
        status["coverage"] = coverage_line

        record = {}
        folder = data_dir()
        if folder and os.path.exists(os.path.join(folder, "track_record.json")):
            with open(os.path.join(folder, "track_record.json"), encoding="utf-8") as f:
                record = json.load(f)
        snaps = record.get("snapshots", [])
        last = snaps[-1] if snaps else {}
        today = market_date(time.time())
        if last.get("date") == today and last.get("ts", 0) >= started:
            picks = sum(len(v) for v in (last.get("lists") or {}).values())
            status.update(ok=True, message=f"Saved today's Track Record picks ({picks} picks)")
        elif last.get("date") == today:
            status.update(ok=True, message="Today's picks had already been saved earlier today")
        else:
            status.update(ok=False, message="Scan finished but no picks were saved: no stock data came back "
                                            "(Yahoo may be rate-limiting or unreachable). It will try again at the next scheduled run.")
    except Exception as exc:
        status.update(ok=False, message=f"Error: {exc}")

    status["finished_ts"] = time.time()
    write_status(status)
    if status["coverage"]:
        log(status["coverage"])
    log(("OK: " if status["ok"] else "FAILED: ") + status["message"]
        + f" (took {(status['finished_ts'] - started) / 60:.1f} min)")
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
