# stock-app

Streamlit stock sentiment / recommendations app (`stock_app.py`) plus its background jobs:
`market_refresh.py` (market snapshot every 30 min during US hours) and `daily_scan.py`
(Track Record picks at 3:30 PM Central), both started by `daily_scheduler.py`.

Runs on the NAS in two containers sharing the `stockapp` folder. Data (`data/`: score history, track
record, caches, settings) stays on the NAS and is not in this repo.
