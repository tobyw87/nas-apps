#!/usr/bin/env python3
"""Background market refresh for the stock app (no browser needed).

Scans the market once, headlessly, and saves the results to data/market_snapshot.pkl so the app's pages open
instantly instead of scanning Yahoo when someone visits. daily_scheduler.py runs this every 30 minutes on weekdays
during US market hours. Run it by hand to test:
    sudo docker exec stock-sentiment-app python3 /app/market_refresh.py
"""
import datetime
import json
import os
import sys
import time

os.environ.setdefault("STREAMLIT_LOGGER_LEVEL", "error")
# Runs every 30 minutes in its own process, so it takes half of StockTwits' ~180-an-hour allowance per run
os.environ.setdefault("STOCKTWITS_RUN_BUDGET", "85")

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.environ.get("STOCKAPP_PATH", os.path.join(HERE, "stock_app.py"))
TIMEOUT_SECONDS = 2 * 3600


def data_dir():
    for path in (os.environ.get("SENTIMENT_DATA_DIR"), "/app/data", os.path.join(HERE, "data")):
        if path and os.path.isdir(path) and os.access(path, os.W_OK):
            return path
    return None


def write_status(status):
    folder = data_dir()
    if not folder:
        return
    tmp = os.path.join(folder, ".market_refresh_status.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f)
    os.replace(tmp, os.path.join(folder, "market_refresh_status.json"))


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] refresh: {msg}", flush=True)


def main():
    started = time.time()
    status = {"started_ts": started, "finished_ts": None, "ok": False, "message": ""}
    try:
        import logging
        import streamlit.logger as streamlit_logger
        from streamlit.testing.v1 import AppTest

        streamlit_logger.set_log_level("error")
        for name in ("yfinance", "peewee", "urllib3"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
        logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").disabled = True

        at = AppTest.from_file(APP, default_timeout=TIMEOUT_SECONDS)
        at.session_state["_refresh_job"] = True
        at.run()
        if at.exception:
            raise RuntimeError("; ".join(str(e.value)[:400] for e in at.exception))
        res = at.session_state["_refresh_result"] if "_refresh_result" in at.session_state else {}
        soc = res.get("social") or {}
        summary = (f"{res.get('loaded', 0)} of {res.get('requested', 0)} stocks in the snapshot "
                   f"({res.get('fresh', 0)} of {res.get('scanned', 0)} scanned this run, {res.get('seconds', 0):.0f}s) "
                   f"(StockTwits: {soc.get('live', 0)} fresh, {soc.get('saved', 0)} saved, {soc.get('none', 0)} pending)")
        if res.get("saved"):
            status.update(ok=True, message="Snapshot saved: " + summary)
        else:
            status.update(ok=False, message="Snapshot NOT saved (too little data came back; the previous snapshot is "
                                            "kept): " + summary)
    except Exception as exc:
        status.update(ok=False, message=f"Error: {exc}")
    status["finished_ts"] = time.time()
    write_status(status)
    log(("OK: " if status["ok"] else "FAILED: ") + status["message"])
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
