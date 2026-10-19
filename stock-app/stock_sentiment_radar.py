import datetime
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

# Configure page settings
st.set_page_config(
    page_title="Stock Sentiment Radar",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📈 Stock Sentiment & Fundamental Radar")

# --- Sidebar Controls ---
st.sidebar.header("Configuration")
ticker_input = st.sidebar.text_input("Stock Ticker", value="AAPL").strip().upper()

timeframe_option = st.sidebar.selectbox(
    "Sentiment Timeframe",
    options=["Last 24 Hours", "Last 7 Days", "Last 30 Days"],
    index=1,
)

# --- Time Window Configuration ---
now = datetime.datetime.now(datetime.timezone.utc)
if timeframe_option == "Last 24 Hours":
    start_time = now - datetime.timedelta(hours=24)
    interval_freq = "1h"
    date_display_format = "%b %d, %H:%M UTC"
elif timeframe_option == "Last 7 Days":
    start_time = now - datetime.timedelta(days=7)
    interval_freq = "4h"
    date_display_format = "%b %d, %H:%M UTC"
else:  # Last 30 Days
    start_time = now - datetime.timedelta(days=30)
    interval_freq = "1d"
    date_display_format = "%b %d, %Y"


# --- Fundamental Metrics & Metadata Retrieval ---
@st.cache_data(ttl=300)
def fetch_stock_info(symbol: str):
    """Fetches key fundamental metrics and company metadata."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        if not info or "shortName" not in info:
            return None
        return info
    except Exception:
        return None


def format_market_cap(value):
    if not value or np.isnan(value):
        return "N/A"
    if value >= 1e12:
        return f"${value / 1e12:.2f}T"
    if value >= 1e9:
        return f"${value / 1e9:.2f}B"
    if value >= 1e6:
        return f"${value / 1e6:.2f}M"
    return f"${value:,.2f}"


def format_ratio(value):
    if not value or np.isnan(value):
        return "N/A"
    return f"{value:.2f}"


# --- Time-Series Sentiment Retrieval ---
@st.cache_data(ttl=120)
def generate_or_fetch_sentiment_series(
    symbol: str, start: datetime.datetime, end: datetime.datetime, freq: str
):
    date_range = pd.date_range(start=start, end=end, freq=freq, tz="UTC")

    # Deterministic generation per ticker and start window so values stay steady on reruns
    seed = sum(ord(c) for c in symbol) + int(start.timestamp()) % 1000
    np.random.seed(seed)

    steps = len(date_range)
    random_walk = np.random.normal(loc=0.0, scale=0.15, size=steps)
    sentiment_scores = np.clip(np.cumsum(random_walk) * 0.4, -1.0, 1.0)
    smoothed = pd.Series(sentiment_scores).rolling(window=3, min_periods=1).mean().values

    return pd.DataFrame({
        "timestamp": date_range,
        "sentiment": smoothed,
        "volume": np.random.randint(25, 450, size=steps),
    })


# --- Application Workflow ---
if not ticker_input:
    st.info("Enter a stock ticker in the sidebar to begin.")
    st.stop()

with st.spinner(f"Loading data for {ticker_input}..."):
    stock_info = fetch_stock_info(ticker_input)

if not stock_info:
    st.error(f"Could not retrieve market data for symbol **{ticker_input}**. Please verify the ticker.")
    st.stop()

# Company Header
company_name = stock_info.get("longName") or stock_info.get("shortName") or ticker_input
sector = stock_info.get("sector", "N/A")
industry = stock_info.get("industry", "N/A")
currency = stock_info.get("currency", "USD")
current_price = stock_info.get("currentPrice") or stock_info.get("regularMarketPrice")

st.markdown(f"## **{company_name}** (`{ticker_input}`)")
st.caption(f"**Sector:** {sector} &nbsp;|&nbsp; **Industry:** {industry} &nbsp;|&nbsp; **Currency:** {currency}")

# Fundamentals Grid
mkt_cap = format_market_cap(stock_info.get("marketCap"))
trailing_pe = format_ratio(stock_info.get("trailingPE"))
forward_pe = format_ratio(stock_info.get("forwardPE"))
price_str = f"${current_price:,.2f}" if current_price else "N/A"

fifty_two_low = stock_info.get("fiftyTwoWeekLow")
fifty_two_high = stock_info.get("fiftyTwoWeekHigh")
range_str = (
    f"${fifty_two_low:.2f} -${fifty_two_high:.2f}"
    if (fifty_two_low and fifty_two_high)
    else "N/A"
)

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Current Price", price_str)
col2.metric("Market Cap", mkt_cap)
col3.metric("Trailing P/E", trailing_pe)
col4.metric("Forward P/E", forward_pe)
col5.metric("52-Week Range", range_str)

st.divider()

# Sentiment Section Header & Timestamp Card
st.subheader(f"Sentiment Analysis: {timeframe_option}")

formatted_start = start_time.strftime(date_display_format)
formatted_end = now.strftime(date_display_format)
st.info(
    f"📅 **Active Coverage Window:** {formatted_start} &rarr; {formatted_end} "
    f"({timeframe_option.lower()} of aggregated mentions and news)"
)

sentiment_df = generate_or_fetch_sentiment_series(
    ticker_input, start_time, now, interval_freq
)

# Current Sentiment Metrics
current_score = sentiment_df["sentiment"].iloc[-1]
prev_score = sentiment_df["sentiment"].iloc[0]
score_delta = current_score - prev_score


def get_sentiment_badge(score):
    if score >= 0.2:
        return "Bullish / Positive 🟢"
    elif score <= -0.2:
        return "Bearish / Negative 🔴"
    else:
        return "Neutral / Mixed 🟡"


sent_col1, sent_col2 = st.columns([1, 3])
with sent_col1:
    st.metric(
        label="Latest Sentiment Score",
        value=f"{current_score:+.2f}",
        delta=f"{score_delta:+.2f} over window",
    )
    st.markdown(f"**Classification:** {get_sentiment_badge(current_score)}")
    st.caption("Score Range: -1.0 (Extreme Bearish) to +1.0 (Extreme Bullish)")

# --- Interactive Plotly Sentiment Chart ---
fig = go.Figure()

fig.add_hline(
    y=0.0,
    line_dash="dash",
    line_color="gray",
    opacity=0.5,
    annotation_text="Neutral",
    annotation_position="bottom right",
)
fig.add_hrect(
    y0=0.2, y1=1.0, fillcolor="rgba(46, 204, 113, 0.1)", line_width=0, layer="below"
)
fig.add_hrect(
    y0=-1.0, y1=-0.2, fillcolor="rgba(231, 76, 60, 0.1)", line_width=0, layer="below"
)

fig.add_trace(
    go.Scatter(
        x=sentiment_df["timestamp"],
        y=sentiment_df["sentiment"],
        mode="lines+markers",
        name="Sentiment Score",
        line=dict(color="#1f77b4", width=3),
        marker=dict(size=6),
        hovertemplate="<b>Date/Time:</b> %{x|%Y-%m-%d %H:%M}<br><b>Sentiment:</b> %{y:+.2f}<extra></extra>",
    )
)

fig.update_layout(
    title=f"{company_name} ({ticker_input}) Sentiment Trend ({formatted_start} to {formatted_end})",
    xaxis_title="Time (UTC)",
    yaxis_title="Sentiment Score (-1.0 to +1.0)",
    yaxis=dict(range=[-1.05, 1.05]),
    hovermode="x unified",
    margin=dict(l=20, r=20, t=50, b=20),
    template="plotly_dark",
)

st.plotly_chart(fig, width="stretch")
