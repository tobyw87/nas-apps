import concurrent.futures
import datetime
import html
import itertools
import json
import os
import pickle
import re
import sqlite3
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf


def _streamlit_version():
    m = re.match(r"(\d+)\.(\d+)", st.__version__)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


# Streamlit 1.50+ replaces **STRETCH with width="stretch" (the old form is being removed)
STRETCH = {"width": "stretch"} if _streamlit_version() >= (1, 50) else {"use_container_width": True}

# Configure page settings
st.set_page_config(
    page_title="Kifaru Stock Analysis",
    page_icon="🦏",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for tightening top space, styling tables, and fixing button hitboxes
st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.0rem !important;
        padding-bottom: 2rem !important;
    }
    header[data-testid="stHeader"] {
        background-color: transparent !important;
        height: 1.5rem !important;
        pointer-events: none !important;
        z-index: 1 !important;
    }
    header[data-testid="stHeader"] * {
        pointer-events: none !important;
    }
    header[data-testid="stHeader"] button,
    header[data-testid="stHeader"] a,
    header[data-testid="stHeader"] [role="button"] {
        pointer-events: auto !important;
    }
    /* Deep Dive title row: name, price and today's change on one line */
    .dd-title-row {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        column-gap: 1.1rem;
        row-gap: 0.3rem;
        margin: 0.4rem 0 0.35rem 0;
    }
    .dd-title-name {
        font-size: 2.3rem;
        font-weight: 700;
        line-height: 1.15;
    }
    .dd-title-price {
        font-size: 1.9rem;
        font-weight: 600;
        line-height: 1.15;
    }
    .dd-change {
        font-size: 1.35rem;
        font-weight: 700;
        padding: 0.15rem 0.7rem;
        border-radius: 0.5rem;
        white-space: nowrap;
    }
    .dd-change.up { color: #16a34a; background: rgba(34, 197, 94, 0.14); }
    .dd-change.down { color: #dc2626; background: rgba(239, 68, 68, 0.14); }
    .dd-change.flat { color: #6b7280; background: rgba(107, 114, 128, 0.14); }
    .dd-change-note {
        font-size: 0.85rem;
        opacity: 0.65;
        white-space: nowrap;
    }
    h1 {
        margin-top: 0.2rem !important;
        padding-top: 0rem !important;
    }
    div[data-testid="stButton"] {
        position: relative !important;
        z-index: 99 !important;
    }
    div[data-testid="stButton"] > button {
        width: 100% !important;
        cursor: pointer !important;
        position: relative !important;
        z-index: 100 !important;
        pointer-events: auto !important;
    }
    div[data-testid="stButton"] > button * {
        pointer-events: none !important;
    }
    div[data-testid="stDataFrame"] div[role="grid"] div[role="gridcell"] a {
        display: flex !important;
        justify-content: center !important;
        align-items: center !important;
        text-align: center !important;
        width: 100% !important;
    }
    /* Metric labels and colored delta badges wrap instead of being cut off with "..." */
    div[data-testid="stMetric"] div:has(> [data-testid="stMetricDelta"]) {
        height: auto !important;
        overflow: visible !important;
    }
    [data-testid="stMetricDelta"] {
        height: auto !important;
        align-items: flex-start !important;
    }
    [data-testid="stMetricDelta"] [data-testid="stMarkdownContainer"],
    [data-testid="stMetricDelta"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stMetricLabel"] [data-testid="stMarkdownContainer"],
    [data-testid="stMetricLabel"] [data-testid="stMarkdownContainer"] p {
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
        overflow-wrap: anywhere !important;
        line-height: 1.3 !important;
    }
    [data-testid="stMetricLabel"] {
        height: auto !important;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.25rem !important;
        font-variant-numeric: tabular-nums !important;
        white-space: nowrap !important;
        overflow: visible !important;
    }
    div[data-testid="stMetricValue"] > div {
        font-size: 1.25rem !important;
    }
    .news-card {
        padding: 0.9rem 1.1rem;
        margin-bottom: 0.75rem;
        border-radius: 8px;
        background-color: rgba(255, 255, 255, 0.04);
        border-left: 4px solid #00FFAA;
    }
    .news-title {
        display: block;
        font-size: 1.2rem;
        font-weight: 700;
        line-height: 1.35;
        text-decoration: none;
        color: inherit !important;
    }
    .news-title:hover {
        color: #00FFAA !important;
        text-decoration: underline;
    }
    .news-meta {
        font-size: 0.8rem;
        color: #A0AEC0;
        margin-top: 0.6rem;
        padding-top: 0.45rem;
        border-top: 1px solid rgba(255, 255, 255, 0.08);
        letter-spacing: 0.02em;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Initialize in-memory session persistence store
if "store" not in st.session_state:
    st.session_state["store"] = {}

# 500+ Liquid Broad-Market Equities Universe (S&P 500 + Exchange Leaders)
BROAD_EXCHANGE_UNIVERSE = [
    # Technology & Communications (80)
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "AVGO", "ORCL",
    "CRM", "NFLX", "AMD", "QCOM", "ADBE", "CSCO", "INTU", "TXN", "AMAT", "NOW",
    "IBM", "MU", "LRCX", "ADI", "KLAC", "PANW", "SNPS", "CDNS", "CRWD", "FTNT",
    "PLTR", "ARM", "SMCI", "MRVL", "ANET", "INTC", "PYPL", "SNOW", "SHOP", "UBER",
    "ABNB", "MCHP", "NXPI", "KEYS", "ON", "FSLR", "WDAY", "ROP", "TEL", "HPQ",
    "GLW", "TDY", "ZBRA", "MPWR", "TER", "STX", "WDC", "SWKS", "DIS", "TMUS",
    "VZ", "T", "CMCSA", "CHTR", "EA", "TTWO", "OMC", "IPG", "LYV", "WBD",
    "NWSA", "FOXA", "MTCH", "ROKU", "SPOT", "DASH", "PINS", "SNAP", "PSKY", "NYT",

    # Financials (75)
    "BRK-B", "JPM", "V", "MA", "BAC", "WFC", "MS", "GS", "SPGI", "BLK",
    "C", "AXP", "PGR", "CB", "MMC", "SCHW", "AON", "ICE", "MCO", "USB",
    "PNC", "TRV", "AFL", "MET", "PRU", "COF", "ALL", "BK", "AJG", "AIG",
    "COIN", "SOFI", "HOOD", "FITB", "TFC", "MTB", "KEY", "RF", "CFG", "HBAN",
    "CINF", "FHN", "BRO", "WRB", "RJF", "LPLA", "NTRS", "PFG", "AMP", "IVZ",
    "BEN", "ACGL", "AIZ", "BBDC", "CBOE", "CMA", "EWBC", "GL", "HIG", "JKHY",
    "MKTX", "MSCI", "NDAQ", "PSTG", "EG", "STT", "SYF", "WTW", "ZION",
    "WAL", "ALLY", "IBKR", "MNDY", "CASY",

    # Healthcare & Life Sciences (75)
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "PFE", "AMGN",
    "ISRG", "BMY", "SYK", "GILD", "VRTX", "ELV", "MDT", "CI", "REGN", "ZTS",
    "BSX", "HUM", "CVS", "BDX", "MCK", "COR", "A", "HCA", "IDXX", "DXCM",
    "IQV", "CNC", "EW", "BAX", "BIIB", "ALNY", "MTD", "VTRS", "RMD", "CAH",
    "STE", "HOLX", "WST", "PODD", "TECH", "DGX", "LH", "INCY", "MRNA", "BMRN",
    "ALGN", "BIO", "CRL", "DVA", "GEHC", "HSIC", "ILMN", "INSP",
    "MASI", "MOH", "QDEL", "RVTY", "SOLV", "TFX", "UHS", "VEEV", "WAT",
    "XRAY", "ZBH", "GH", "EXAS", "NVAX",

    # Consumer Discretionary & Retail (70)
    "HD", "COST", "WMT", "MCD", "LOW", "BKNG", "TJX", "NKE", "SBUX", "LULU",
    "CMG", "ORLY", "AZO", "MAR", "ROST", "HLT", "F", "GM", "DHI", "LEN",
    "YUM", "EBAY", "LVS", "DRI", "ULTA", "BBY", "EXPE", "TGT", "RCL",
    "TSCO", "KMX", "GPC", "DPZ", "APTV", "MGM", "WYNN", "BWA", "PHM", "NVR",
    "CCL", "NCLH", "MHK", "HAS", "POOL", "TPR", "RL", "WHR", "GRMN", "LKQ",
    "BURL", "CPNG", "DECK", "ETSY", "FIVE", "GWW", "HBI", "KSS", "LNW", "PENN",
    "PVH", "TOL", "VFC", "WSM", "CROX", "BOOT", "ONON",

    # Consumer Staples (45)
    "PG", "KO", "PEP", "PM", "MDLZ", "MO", "CL", "KMB", "ADM", "STZ",
    "GIS", "SYY", "HSY", "K", "KR", "KHC", "CLX", "EL", "CAG", "MKC",
    "CHD", "TSN", "HRL", "SJM", "CPB", "TAP", "WBA", "KVUE", "BG", "LW",
    "POST", "INGR", "DAR", "HAYW", "FLO", "CALM", "MNST", "CELH", "USFD",
    "PFGC", "HUT", "THS", "SAM", "FI",

    # Energy (45)
    "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "WMB",
    "HES", "KMI", "HAL", "BKR", "DVN", "FANG", "OKE", "TRGP", "EQT", "CTRA",
    "APA", "OVV", "CHRD", "MTDR", "AR", "PR", "MUR", "RRC", "CNX",
    "EXE", "WES", "MPLX", "ET", "EPD", "PAA", "KNTK", "PAGP", "AM", "DTM",
    "VIST", "TPL", "LNG", "TRP", "ENB",

    # Industrials & Defense (65)
    "CAT", "GE", "UNP", "HON", "RTX", "DE", "LMT", "BA", "ETN", "WM",
    "UPS", "GD", "ITW", "NOC", "CSX", "FDX", "EMR", "PCAR", "NSC", "PH",
    "CARR", "TT", "TDG", "JCI", "FAST", "URI", "CMI", "ROK", "DAL",
    "LUV", "UAL", "AAL", "WAB", "XYL", "ODFL", "PWR", "IR", "LHX", "HWM",
    "TXT", "MAS", "SNA", "PNR", "DOV", "SWK", "EXPD", "CHRW", "JBHT", "HUBB",
    "BLDR", "AXON", "CPRT", "AME", "NDSN", "VRT", "FIX", "ACM", "EME", "J",
    "OSK", "ROL", "RHI",

    # Utilities & Clean Energy (35)
    "NEE", "SO", "DUK", "CEG", "SRE", "AEP", "D", "PEG", "EXC", "ED",
    "XEL", "PCG", "WEC", "AWK", "ES", "DTE", "ETR", "FE", "PPL", "AEE",
    "CNP", "CMS", "NRG", "NI", "EVRG", "LNT", "ATO", "PNW", "OGE", 
    "AES", "VST", "CPK", "SWX", "OGS",

    # Real Estate (35)
    "PLD", "AMT", "EQIX", "WELL", "PSA", "SPG", "O", "DLR", "CCI", "CSGP",
    "VICI", "AVB", "EQR", "WY", "SBAC", "INVH", "EXR", "IRM", "MAA", "ARE",
    "ESS", "UDR", "KIM", "REG", "CPT", "HST", "DOC", "BXP", "FRT", "GLPI",
    "NNN", "SRC", "WPC", "STAG", "CUBE",

    # Basic Materials (35)
    "LIN", "SHW", "APD", "FCX", "ECL", "CTVA", "NEM", "DOW", "DD", "PPG",
    "ALB", "VMC", "MLM", "NUE", "CF", "FMC", "MOS", "IFF", "CE", "EMN",
    "PKG", "BALL", "IP", "AMCR", "SW", "STLD", "CLF", "AA", "RPM", "AVY",
    "EXP", "ASH", "AXTA", "OLN", "HUN"
]

# Dedicated Defensive Universe (Low Beta: Utilities, Staples, Healthcare, Telecom, REITs)
DEFENSIVE_BETA_UNIVERSE = [
    # Utilities (Beta 0.3 - 0.6)
    "SO", "DUK", "ED", "NEE", "AEP", "D", "PEG", "EXC", "XEL", "WEC",
    "PCG", "AWK", "ES", "DTE", "ETR", "FE", "PPL", "AEE", "CNP", "CMS",
    "NRG", "NI", "EVRG", "LNT", "ATO", "PNW", "OGE", "CEG", "SRE",
    # Consumer Staples (Beta 0.4 - 0.7)
    "PG", "KO", "PEP", "CL", "KMB", "GIS", "SYY", "HSY", "K", "KR",
    "KHC", "CLX", "CAG", "MKC", "CHD", "TSN", "HRL", "SJM", "CPB", "TAP",
    "WBA", "KVUE", "BG", "LW", "POST", "FLO", "ADM", "PM", "MDLZ", "MO",
    # Defensive Healthcare & Pharma (Beta 0.5 - 0.8)
    "JNJ", "MRK", "ABBV", "PFE", "AMGN", "BMY", "GILD", "CVS", "BDX", "CAH",
    "MCK", "COR", "MDT", "CI", "ELV", "BAX", "RMD", "VTRS", "ZTS", "DHR",
    # Telecom & Conservative REITs (Beta 0.4 - 0.7)
    "VZ", "T", "O", "PSA", "DLR", "WELL", "VICI", "EQIX", "AMT", "CCI",
    "PLD", "INVH", "AVB", "EQR", "WY", "SBAC", "EXR", "MAA", "ESS", "UDR",
    # Low-Volatility Financials & Insurers (Beta 0.5 - 0.8)
    "BRK-B", "TRV", "AFL", "MET", "PRU", "ALL", "CB", "MMC", "AON", "AJG",
    # Defensive Industrials & Waste (Beta 0.5 - 0.8)
    "WM", "RSG", "LMT", "NOC", "GD", "CTVA"
]

# Dedicated High Beta Universe (High Volatility: Tech, Semis, Crypto, Cyclicals)
HIGH_BETA_UNIVERSE = [
    # Semis & Mega Growth Tech (Beta 1.3 - 2.8)
    "NVDA", "TSLA", "AMD", "PLTR", "ARM", "SMCI", "MRVL", "AVGO", "QCOM", "MU",
    "LRCX", "AMAT", "KLAC", "ON", "MPWR", "TER", "STX", "WDC", "COIN", "MSTR", "DKNG", "XYZ",
    "AFRM", "ROKU", "SNAP", "SHOP", "UBER", "DASH", "CRWD", "PANW", "FTNT", "SNOW",
    "FSLR", "SEDG", "RIVN", "LCID", "NFLX", "META", "GOOGL", "AMZN",
    # Materials, Mining & Cyclicals (Beta 1.3 - 2.5)
    "FCX", "CLF", "AA", "ALB", "URI", "CAT", "DE", "BA", "DAL", "UAL",
    "AAL", "LUV", "CCL", "NCLH", "RCL", "WYNN", "MGM", "LVS", "PENN",
    # Exploration & Volatile Energy (Beta 1.3 - 2.2)
    "OXY", "DVN", "FANG", "HAL", "SLB", "BKR", "APA", "OVV", "CHRD", "MTDR",
    "AR", "PR", "MUR", "RRC", "CNX",
    # High Beta Consumer Discretionary (Beta 1.2 - 2.0)
    "LULU", "CROX", "BOOT", "DECK", "ETSY", "BURL", "FIVE", "CPNG"
]

# Dedicated per-sector pools
SECTOR_UNIVERSES = {
    "Technology": [
        "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "AMD", "CSCO", "ACN", "ADBE",
        "TXN", "QCOM", "IBM", "AMAT", "NOW", "INTU", "MU", "LRCX", "ADI", "KLAC",
        "PANW", "SNPS", "CDNS", "CRWD", "FTNT", "MCHP", "NXPI", "MRVL", "ANET", "PLTR",
        "ARM", "SMCI", "INTC", "SNOW", "SHOP", "KEYS", "ON", "FSLR", "WDAY", "ROP",
        "TEL", "HPQ", "GLW", "TDY", "ZBRA", "MPWR", "TER", "STX", "WDC", "SWKS"
    ],
    "Financial Services": [
        "JPM", "V", "MA", "BAC", "WFC", "MS", "GS", "SPGI", "BLK", "C",
        "AXP", "PGR", "CB", "MMC", "SCHW", "AON", "ICE", "MCO", "USB", "PNC",
        "TRV", "AFL", "MET", "PRU", "COF", "ALL", "BK", "AJG", "AIG", "FITB",
        "COIN", "SOFI", "HOOD", "TFC", "MTB", "KEY", "RF", "CFG", "HBAN", "CINF",
        "FHN", "BRO", "WRB", "RJF", "LPLA", "NTRS", "PFG", "AMP", "IVZ", "BEN"
    ],
    "Healthcare": [
        "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "PFE", "AMGN",
        "ISRG", "BMY", "SYK", "GILD", "VRTX", "ELV", "MDT", "CI", "REGN", "ZTS",
        "BSX", "HUM", "CVS", "BDX", "MCK", "COR", "A", "HCA", "IDXX", "DXCM",
        "IQV", "CNC", "EW", "BAX", "BIIB", "ALNY", "MTD", "VTRS", "RMD", "CAH",
        "STE", "HOLX", "WST", "PODD", "TECH", "DGX", "LH", "INCY", "MRNA", "BMRN"
    ],
    "Consumer Cyclical": [
        "AMZN", "TSLA", "HD", "MCD", "LOW", "BKNG", "TJX", "NKE", "SBUX", "LULU",
        "CMG", "ORLY", "AZO", "MAR", "ROST", "HLT", "F", "GM", "ABNB", "DHI",
        "LEN", "YUM", "EBAY", "LVS", "DRI", "ULTA", "BBY", "EXPE", "TGT", "RCL",
        "TSCO", "KMX", "GPC", "DPZ", "APTV", "MGM", "WYNN", "BWA", "PHM", "NVR",
        "CCL", "NCLH", "MHK", "HAS", "POOL", "TPR", "RL", "WHR", "GRMN", "LKQ"
    ],
    "Consumer Defensive": [
        "WMT", "PG", "COST", "KO", "PEP", "PM", "MDLZ", "MO", "CL", "KMB", "ADM", "STZ",
        "GIS", "SYY", "HSY", "K", "KR", "KHC", "CLX", "EL", "CAG", "MKC",
        "CHD", "TSN", "HRL", "SJM", "CPB", "TAP", "WBA", "KVUE", "BG", "LW",
        "POST", "INGR", "DAR", "HAYW", "FLO", "CALM"
    ],
    "Energy": [
        "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "WMB",
        "HES", "KMI", "HAL", "BKR", "DVN", "FANG", "OKE", "TRGP", "EQT", "CTRA",
        "APA", "OVV", "CHRD", "MTDR", "AR", "PR", "MUR", "RRC", "CNX",
        "EXE", "WES", "MPLX", "ET", "EPD", "PAA", "KNTK", "PAGP", "AM", "DTM"
    ],
    "Industrials": [
        "CAT", "GE", "UNP", "HON", "RTX", "DE", "LMT", "BA", "ETN", "WM",
        "UPS", "GD", "ITW", "NOC", "CSX", "FDX", "EMR", "PCAR", "NSC", "PH",
        "CARR", "TT", "TDG", "GWW", "JCI", "FAST", "URI", "CMI", "ROK", "DAL",
        "LUV", "UAL", "AAL", "WAB", "XYL", "ODFL", "PWR", "IR", "LHX", "HWM",
        "TXT", "MAS", "SNA", "PNR", "DOV", "SWK", "EXPD", "CHRW", "JBHT", "HUBB"
    ],
    "Communication Services": [
        "GOOGL", "GOOG", "META", "NFLX", "DIS", "TMUS", "VZ", "T", "CMCSA", "CHTR",
        "EA", "TTWO", "OMC", "IPG", "LYV", "WBD", "NWSA", "FOXA", "MTCH", "ROKU",
        "SPOT", "DASH", "PINS", "SNAP", "PSKY", "LBRDA", "SIRI", "GTM", "NYT", "IAC"
    ],
    "Utilities": [
        "NEE", "SO", "DUK", "CEG", "SRE", "AEP", "D", "PEG", "EXC", "ED",
        "XEL", "PCG", "WEC", "AWK", "ES", "DTE", "ETR", "FE", "PPL", "AEE",
        "CNP", "CMS", "NRG", "NI", "EVRG", "LNT", "ATO", "PNW", "OGE", 
        "AES", "VST", "CPK", "SWX", "OGS", "UGI", "BKH", "MDU", "IDA", "AVA"
    ],
    "Real Estate": [
        "PLD", "AMT", "EQIX", "WELL", "PSA", "SPG", "O", "DLR", "CCI", "CSGP",
        "VICI", "AVB", "EQR", "WY", "SBAC", "INVH", "EXR", "IRM", "MAA", "ARE",
        "ESS", "UDR", "KIM", "REG", "CPT", "HST", "DOC", "BXP", "FRT", "GLPI",
        "NNN", "SRC", "WPC", "STAG", "CUBE", "EPRT", "TRNO", "COLD", "KRC", "SLG"
    ],
    "Basic Materials": [
        "LIN", "SHW", "APD", "FCX", "ECL", "CTVA", "NEM", "DOW", "DD", "PPG",
        "ALB", "VMC", "MLM", "NUE", "CF", "FMC", "MOS", "IFF", "CE", "EMN",
        "PKG", "BALL", "IP", "AMCR", "SW", "STLD", "CLF", "AA", "RPM", "AVY",
        "EXP", "ASH", "AXTA", "OLN", "HUN"
    ]
}

# Recommended Stocks tab criteria
YAHOO_MAX_WORKERS = 5           # parallel Yahoo requests per scan (more trips Yahoo's rate limiter)

INST_MIN_ANALYSTS = 15          # Large-Cap / Institutional: broad Wall Street coverage
INST_MIN_MARKET_CAP = 10e9      # ...and a large-cap market value ($10B+)
EMERGING_MIN_ANALYSTS = 2       # Emerging Growth: just enough coverage to have a consensus

# Mid-Cap and Small-Cap tabs (same size cutoffs as the Deep Dive market-cap labels). They score exactly like the
# existing tabs: Mid-Cap like Large-Cap / Institutional (45% analysts, 35% upside, 20% social) and Small-Cap like
# Emerging Growth (30/30/40), so the history behind the Smoothed view carries over. Edit the numbers here.
MID_MIN_MARKET_CAP = 2e9        # Mid-Cap: $2B up to the Large-Cap cutoff
MID_MAX_MARKET_CAP = INST_MIN_MARKET_CAP
MID_MIN_ANALYSTS = 8            # fewer than Large-Cap: mid caps have thinner Wall Street coverage
MID_REQUIRE_PROFIT = True       # positive forward P/E, like Large-Cap
SMALL_MIN_MARKET_CAP = 300e6    # Small-Cap: $300M up to $2B
SMALL_MAX_MARKET_CAP = MID_MIN_MARKET_CAP
SMALL_MIN_ANALYSTS = 3          # small caps are thinly covered, so a consensus of 3 is enough
SMALL_REQUIRE_PROFIT = False    # unprofitable small caps are allowed (Composite+ still takes points off for it)

# Extra candidates per sector (beyond SECTOR_UNIVERSES). Every scanned stock is still checked
# against Yahoo's own sector label, so a misfiled ticker is simply skipped.
SECTOR_EXTRA_CANDIDATES = {
    "Technology": ["AMZN", "TSLA", "PYPL", "UBER", "ABNB", "APH", "MSI", "IT", "CTSH", "GDDY", "FFIV", "NTAP", "HPE", "DELL", "AKAM", "EPAM", "GEN", "PTC", "TYL", "FICO", "TRMB", "ADSK", "CDW", "JBL", "FLEX", "COHR", "ENTG", "TWLO", "DDOG", "NET", "ZS", "OKTA", "MDB", "HUBS", "TEAM", "DOCU", "ZM", "APP", "FIS"],
    "Financial Services": ["BRK-B", "ACGL", "AIZ", "BBDC", "CBOE", "CMA", "EWBC", "GL", "HIG", "JKHY", "MKTX", "MSCI", "NDAQ", "PSTG", "EG", "STT", "SYF", "WTW", "ZION", "WAL", "ALLY", "IBKR", "MNDY", "CASY"],
    "Healthcare": ["ALGN", "BIO", "CRL", "DVA", "GEHC", "HSIC", "ILMN", "INSP", "MASI", "MOH", "QDEL", "RVTY", "SOLV", "TFX", "UHS", "VEEV", "WAT", "XRAY", "ZBH", "GH", "EXAS", "NVAX"],
    "Consumer Cyclical": ["COST", "WMT", "BURL", "CPNG", "DECK", "ETSY", "FIVE", "GWW", "HBI", "KSS", "LNW", "PENN", "PVH", "TOL", "VFC", "WSM", "CROX", "BOOT", "ONON"],
    "Consumer Defensive": ["MNST", "CELH", "USFD", "PFGC", "HUT", "THS", "SAM", "FI", "DG", "DLTR", "BJ", "KDP", "SFM", "ELF", "COKE", "BF-B", "TGT", "PPC", "COTY", "ACI", "ENR", "SPB", "VITL", "CASY", "IPAR"],
    "Energy": ["VIST", "TPL", "LNG", "TRP", "ENB", "HP", "NOV", "FTI", "WHD", "PTEN", "LBRT", "VNOM", "SM", "CIVI", "NOG", "CRGY", "MGY", "KOS", "DINO", "PBF", "DK", "CVI", "PARR", "AROC", "TALO", "WFRD", "RIG", "VAL", "NE"],
    "Industrials": ["BLDR", "AXON", "CPRT", "AME", "NDSN", "VRT", "FIX", "ACM", "EME", "J", "OSK", "ROL", "RHI"],
    "Communication Services": ["TKO", "RBLX", "FOX", "NWS", "MSGS", "NXST", "FWONA", "LBRDK", "CABO", "IRDM", "TRIP", "YELP", "Z", "WMG", "NTES", "BIDU", "TME"],
    "Utilities": ["EIX", "POR", "NJR", "SR", "ORA", "CWEN", "AWR", "CWT", "OTTR", "MGEE", "HE", "AQN", "BEP", "FTS"],
    "Real Estate": ["VTR", "ELS", "SUI", "AMH", "LAMR", "OUT", "CBRE", "JLL", "BRX", "KRG", "FR", "EGP", "REXR", "PECO", "HR", "OHI", "SBRA", "MAC", "SKT", "ADC", "VNO", "CUZ", "HIW"],
    "Basic Materials": ["LYB", "SCCO", "RS", "CMC", "MP", "WLK", "CC", "TROX", "SON", "SEE", "AEM", "FNV", "WPM", "RGLD", "ESI", "CBT", "MTX", "KWR", "SMG", "NGVT", "HWKN"],
}

SECTOR_OPTIONS = ["All Sectors"] + sorted(SECTOR_UNIVERSES.keys())


def sector_candidate_pool(sector_name):
    return list(dict.fromkeys(SECTOR_UNIVERSES.get(sector_name, []) + SECTOR_EXTRA_CANDIDATES.get(sector_name, [])))


# S&P 500 Consensus Sector Forward P/E Benchmarks
SECTOR_FORWARD_PE_BENCHMARKS = {
    "Technology": 28.5,
    "Financial Services": 15.4,
    "Healthcare": 18.6,
    "Consumer Cyclical": 23.8,
    "Communication Services": 19.5,
    "Industrials": 20.8,
    "Consumer Defensive": 20.2,
    "Energy": 12.6,
    "Utilities": 17.8,
    "Real Estate": 18.4,
    "Basic Materials": 16.8,
}

TABLE_REFRESH_DISCLAIMER = (
    "⏱ Update Schedule & Cache Persistence:\n"
    "• Table Persistence: Data is held in active session memory across navigation transitions so clicking 'Back' loads instantly.\n"
    "• On-Demand Refresh: Click '🔄 Refresh Data' at the top of the sidebar to force an immediate live market rescan.\n"
    "• Background TTL: Live streams auto-expire and refresh in the background after 30 minutes of inactivity."
)

COMPOSITE_CALC_EXPLANATION = (
    "ℹ Multi-Factor Composite Architecture:\n"
    "• Bayesian Analyst Buy %: Normalizes small-sample coverage toward the 65% market prior to prevent 1-analyst distortion.\n"
    "• Social Score: StockTwits sentiment (vs the platform's bullish norm, weighted by author reach and sample size) scaled by buzz (StockTwits pace, Reddit mentions, likes, rising interest, trending).\n"
    "• Valuation Runway Guardrails: Discounts >80% upside unless 10+ analysts agree, caps upside at +25% when the price is >30% below its 50-day average (stale targets), and dampens 52-week low distress.\n\n"
    "Weighting: 35% Analyst Buy % + 30% Valuation Runway (Upside) + 35% Social Momentum"
)

# Navigation History & Query Params Sync
if "current_view" not in st.session_state:
    st.session_state["current_view"] = "🏆 Recommended Stocks"
if "previous_view" not in st.session_state:
    st.session_state["previous_view"] = "🏆 Recommended Stocks"
if "selected_ticker" not in st.session_state:
    st.session_state["selected_ticker"] = "MU"

query_params = st.query_params
if "ticker" in query_params:
    st.session_state["selected_ticker"] = query_params["ticker"].upper()
    if st.session_state["current_view"] != "📊 Single Stock Deep Dive":
        st.session_state["previous_view"] = st.session_state["current_view"]
    st.session_state["current_view"] = "📊 Single Stock Deep Dive"

def open_deep_dive(ticker_to_open):
    if st.session_state["current_view"] != "📊 Single Stock Deep Dive":
        st.session_state["previous_view"] = st.session_state["current_view"]
    st.session_state["selected_ticker"] = ticker_to_open
    st.session_state["current_view"] = "📊 Single Stock Deep Dive"
    st.query_params["ticker"] = ticker_to_open
    st.rerun()

def go_back():
    target_view = st.session_state.get("previous_view", "🏆 Recommended Stocks")
    st.session_state["current_view"] = target_view
    if "ticker" in st.query_params:
        del st.query_params["ticker"]
    st.rerun()

# --- Rhino logo, centred at the top of the sidebar (custom SVG: rhino on a teal badge with a rising trend arrow) ---
RHINO_LOGO_SVG_B64 = "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxMDAgMTAwIiB3aWR0aD0iMTAwIiBoZWlnaHQ9IjEwMCI+PGRlZnM+PGxpbmVhckdyYWRpZW50IGlkPSJyYmciIHgxPSIwIiB5MT0iMCIgeDI9IjEiIHkyPSIxIj48c3RvcCBvZmZzZXQ9IjAiIHN0b3AtY29sb3I9IiMxNGI4YTYiLz48c3RvcCBvZmZzZXQ9IjEiIHN0b3AtY29sb3I9IiMxZTNhOGEiLz48L2xpbmVhckdyYWRpZW50PjwvZGVmcz48Y2lyY2xlIGN4PSI1MCIgY3k9IjUwIiByPSI0OCIgZmlsbD0idXJsKCNyYmcpIi8+PGNpcmNsZSBjeD0iNTAiIGN5PSI1MCIgcj0iNDQuNSIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjZmZmZmZmIiBzdHJva2Utb3BhY2l0eT0iMC4yMiIgc3Ryb2tlLXdpZHRoPSIxLjUiLz48cG9seWxpbmUgcG9pbnRzPSIxNywzOCAyNywzMSAzNSwzNCA0NywyMSIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjYTdmM2QwIiBzdHJva2Utd2lkdGg9IjMuMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+PHBvbHlnb24gcG9pbnRzPSI0MS41LDE5LjUgNTAsMTcuNSA0OC41LDI2IiBmaWxsPSIjYTdmM2QwIi8+PHBhdGggZmlsbD0iI2ZmZmZmZiIgZD0iIE0gMTUgNjAgQyAxNCA1MiwgMTkgNDYsIDI4IDQ0IEMgMzYgNDIsIDQ0IDM5LCA1MSAzOCBDIDU2IDM3LjUsIDYwIDM4LjUsIDYyIDQwLjUgTCA2My41IDMzLjUgTCA2NyA0MSBDIDY5LjUgNDIuNSwgNzIgNDUsIDczLjUgNDggTCA3Ni41IDQyLjUgTCA3Ny41IDUwIEMgNzkgNTEuNSwgODAuNSA1MywgODEuNSA1NC41IEwgOTEgMzguNSBMIDg2IDU3IEMgODguNSA1OS41LCA4OSA2MywgODcgNjUuNSBDIDg1IDY3LjUsIDgxIDY4LCA3Ny41IDY3IEMgNzQuNSA2Ni41LCA3MiA2Ny41LCA3MC41IDY5LjUgTCA3MC41IDc5LjUgQyA3MC41IDgxLCA2OS41IDgxLjUsIDY4IDgxLjUgTCA2NC41IDgxLjUgQyA2MyA4MS41LCA2Mi41IDgxLCA2Mi41IDc5LjUgTCA2Mi41IDcyLjUgQyA2MS41IDcyLjgsIDYwLjUgNzMsIDU5LjUgNzMgTCA1OS41IDc5LjUgQyA1OS41IDgxLCA1OC41IDgxLjUsIDU3IDgxLjUgTCA1NCA4MS41IEMgNTIuNSA4MS41LCA1MiA4MSwgNTIgNzkuNSBMIDUyIDczLjUgQyA0NSA3NC41LCAzOCA3NC41LCAzMyA3My41IEwgMzMgNzkuNSBDIDMzIDgxLCAzMiA4MS41LCAzMC41IDgxLjUgTCAyNy41IDgxLjUgQyAyNiA4MS41LCAyNS41IDgxLCAyNS41IDc5LjUgTCAyNS41IDcxLjUgQyAyNC41IDcxLjIsIDIzLjUgNzAuOCwgMjIuNSA3MC4zIEwgMjIuNSA3OS41IEMgMjIuNSA4MSwgMjEuNSA4MS41LCAyMCA4MS41IEwgMTcuNSA4MS41IEMgMTYgODEuNSwgMTUuNSA4MSwgMTUuNSA3OS41IEwgMTUuNSA2NiBDIDE1IDY0LCAxNSA2MiwgMTUgNjAgWiIvPjxwYXRoIGQ9Ik0gMTUuNSA1NiBDIDEyLjUgNTcsIDExIDYwLCAxMS41IDYzLjUiIGZpbGw9Im5vbmUiIHN0cm9rZT0iI2ZmZmZmZiIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiLz48Y2lyY2xlIGN4PSI3My41IiBjeT0iNTUiIHI9IjEuNyIgZmlsbD0iIzFlM2E4YSIvPjxwYXRoIGQ9Ik0gNTggNDcgQyA2MCA1MywgNjAgNjAsIDU3LjUgNjYiIGZpbGw9Im5vbmUiIHN0cm9rZT0iIzFlM2E4YSIgc3Ryb2tlLW9wYWNpdHk9IjAuMzUiIHN0cm9rZS13aWR0aD0iMS42IiBzdHJva2UtbGluZWNhcD0icm91bmQiLz48L3N2Zz4="
st.sidebar.markdown(
    # pointer-events: none so the raised logo never blocks the sidebar's collapse button beside it
    '<div style="text-align: center; margin: -3.4rem 0 0.4rem 0; pointer-events: none;" title="Kifaru Stock Analysis">'
    f'<img src="data:image/svg+xml;base64,{RHINO_LOGO_SVG_B64}" width="64" height="64" alt="Kifaru rhino logo"></div>'
    # The "running" indicator at the top right becomes a dancing rhino
    '<style>'
    # the logo's whole markdown block lets clicks through, so it never covers the sidebar's « collapse button
    '[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(img[alt="Kifaru rhino logo"]),'
    '[data-testid="stSidebar"] .element-container:has(img[alt="Kifaru rhino logo"]),'
    '[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(img[alt="Kifaru rhino logo"]) * { pointer-events: none; }'
    '[data-testid="stStatusWidgetRunningIcon"] svg { display: none !important; }'
    '[data-testid="stStatusWidgetRunningIcon"] {'
    f'  width: 30px; height: 30px; margin-right: 0.35rem; background: url("data:image/svg+xml;base64,{RHINO_LOGO_SVG_B64}") center / contain no-repeat;'
    '  transform-origin: 50% 85%; animation: rhino-dance 0.8s ease-in-out infinite; }'
    '@keyframes rhino-dance {'
    '  0% { transform: translateY(0) rotate(-10deg) scale(1, 1); }'
    '  25% { transform: translateY(-5px) rotate(0deg) scale(0.95, 1.06); }'
    '  50% { transform: translateY(0) rotate(10deg) scale(1.05, 0.95); }'
    '  75% { transform: translateY(-5px) rotate(0deg) scale(0.95, 1.06); }'
    '  100% { transform: translateY(0) rotate(-10deg) scale(1, 1); } }'
    '@media (prefers-reduced-motion: reduce) { [data-testid="stStatusWidgetRunningIcon"] { animation-duration: 2.4s; } }'
    '</style>',
    unsafe_allow_html=True,
)

# --- Global refresh: top of the sidebar, reloads whatever page is open with fresh data ---
if st.sidebar.button("🔄 Refresh Data", key="btn_refresh_global", type="secondary", **STRETCH,
                     help="Clears cached market data and reloads the page you're on with a fresh scan."):
    st.session_state.setdefault("store", {}).clear()
    st.session_state["live_after"] = time.time()      # this session ignores snapshots older than this click
    st.cache_data.clear()
    st.rerun()

# --- Sidebar Navigation ---
# App name, centred under the rhino logo
st.sidebar.markdown(
    '<div class="app-name" style="text-align: center; line-height: 1.1; margin: 0 0 0.7rem 0;" title="Kifaru Stock Analysis">'
    '<div style="font-size: 1.8rem; font-weight: 700; letter-spacing: 0.04em; white-space: nowrap;">Kifaru</div>'
    '<div style="font-size: 1.02rem; font-weight: 600; letter-spacing: 0.05em; white-space: nowrap; opacity: 0.72; margin-top: 0.1rem;">'
    'Stock Analysis</div></div>',
    unsafe_allow_html=True,
)

nav_options = [
    "📊 Single Stock Deep Dive",
    "🏆 Recommended Stocks",
    "🧪 Portfolio Simulations",
    "📈 Track Record",
    "🏦 Analyst Sentiment",
    "📱 Social Media Data",
    "🚀 Best Upside",
    "🏷️ P/E Discounts",
    "⚡ Beta Volatility",
]

if st.session_state["current_view"] not in nav_options:
    st.session_state["current_view"] = "🏆 Recommended Stocks"
if st.session_state.get("previous_view") not in nav_options:
    st.session_state["previous_view"] = "🏆 Recommended Stocks"

selected_nav = st.sidebar.radio(
    "Choose View:",
    options=nav_options,
    index=nav_options.index(st.session_state["current_view"]) if st.session_state["current_view"] in nav_options else 1,
)

if selected_nav != st.session_state["current_view"]:
    if selected_nav != "📊 Single Stock Deep Dive":
        st.session_state["previous_view"] = selected_nav
        if "ticker" in st.query_params:
            del st.query_params["ticker"]
    st.session_state["current_view"] = selected_nav
    st.rerun()

app_mode = st.session_state["current_view"]
st.sidebar.divider()

# Scan & Display Scope
st.sidebar.header("📡 Market Scan Scope")
# Market Coverage / Rows to Display are filled in further down, once the market universe has loaded
scan_scope_box = st.sidebar.container()

# Deep Dive Selector in Sidebar
if app_mode == "📊 Single Stock Deep Dive":
    st.sidebar.header("Deep Dive Settings")
    input_ticker = st.sidebar.text_input(
        "Stock Ticker",
        value=st.session_state["selected_ticker"],
    ).strip().upper()
    if input_ticker != st.session_state["selected_ticker"]:
        st.session_state["selected_ticker"] = input_ticker
else:
    st.sidebar.header("Single Stock Deep Dive")
    sidebar_ticker = st.sidebar.text_input(
        "Enter Ticker to Inspect:",
        value=st.session_state.get("selected_ticker", "MU"),
    ).strip().upper()
    if st.sidebar.button("🔍 Go to Deep Dive", type="primary", **STRETCH):
        open_deep_dive(sidebar_ticker)

now = datetime.datetime.now(datetime.timezone.utc)
# Floor to 5 minutes so cache keys stay stable between reruns
now = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)

# --- Data Retrieval: Trending Stream & Ticker Feed ---
# Failures raise inside the cached functions (Streamlit never caches exceptions), so a transient
# outage is retried on the next run instead of being frozen in the cache for 30 minutes.
@st.cache_data(ttl=1800)
def _fetch_trending_live():
    url = "https://api.stocktwits.com/api/2/trending/symbols.json"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    r = requests.get(url, headers=headers, timeout=4)
    r.raise_for_status()
    clean_syms = []
    for s in r.json().get("symbols", []) or []:
        sym_str = (s.get("symbol") or "").upper().replace(".", "-")
        if re.match(r"^[A-Z\-]{1,6}$", sym_str):
            clean_syms.append(sym_str)
    if not clean_syms:
        raise ValueError("StockTwits returned no trending symbols")
    return clean_syms


def fetch_trending_tickers():
    # No hard-coded placeholder list: if the feed is down, nothing is flagged as trending.
    try:
        return _fetch_trending_live()
    except Exception:
        return []


def _trending_info(symbol: str):
    trending_list = fetch_trending_tickers()
    is_trending = symbol in trending_list
    trending_rank = (trending_list.index(symbol) + 1) if is_trending else None
    if is_trending and trending_rank:
        trend_score = max(50.0, 100.0 - ((trending_rank - 1) * 1.7))
    else:
        trend_score = 15.0
    return is_trending, trending_rank, trend_score


# --- Volume-Weighted Social Score ---
def _fetch_stocktwits_live(symbol: str):
    # Uncached: every call is a real request (counted against the hourly budget by the caller)
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    is_trending, trending_rank, trend_score = _trending_info(symbol)

    resp = requests.get(url, headers=headers, timeout=4)
    resp.raise_for_status()
    data = resp.json()
    messages = data.get("messages") or []
    watchlist_count = (data.get("symbol") or {}).get("watchlist_count", 0)

    bull_count = 0          # legacy tallies (tagged posts count 2, keyword-guessed posts 1)
    bear_count = 0
    bull_w = 0.0            # weighted: author reach, and posts that list many tickers count for less
    bear_w = 0.0
    likes_total = 0
    times = []

    bull_words = {"call", "calls", "buy", "buying", "moon", "long", "bull", "bullish", "green", "breakout", "beat"}
    bear_words = {"put", "puts", "sell", "selling", "short", "bear", "bearish", "red", "dump", "drop", "miss"}

    for m in messages:
        likes_obj = m.get("likes")
        if isinstance(likes_obj, dict):
            likes_total += likes_obj.get("total", 0) or 0

        n_syms = len(m.get("symbols") or []) or 1
        followers = (m.get("user") or {}).get("followers") or 0
        try:
            followers = max(0.0, float(followers))
        except (TypeError, ValueError):
            followers = 0.0
        weight = (1.0 / np.sqrt(n_syms)) * (1.0 + np.log10(1.0 + followers) / 3.0)

        body = (m.get("body") or "").lower()
        tag = (m.get("entities") or {}).get("sentiment")
        tag_val = tag.get("basic") if isinstance(tag, dict) else None

        if tag_val == "Bullish":
            bull_count += 2
            bull_w += weight
        elif tag_val == "Bearish":
            bear_count += 2
            bear_w += weight
        else:
            tokens = set(re.findall(r"\b\w+\b", body))
            b_hits = len(tokens & bull_words)
            br_hits = len(tokens & bear_words)
            if b_hits > br_hits:
                bull_count += 1
                bull_w += 0.5 * weight          # a guessed direction counts half as much as an explicit tag
            elif br_hits > b_hits:
                bear_count += 1
                bear_w += 0.5 * weight

        created = m.get("created_at")
        if created:
            try:
                times.append(datetime.datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc))
            except (TypeError, ValueError):
                pass

    total_w = bull_w + bear_w
    raw_polarity = (bull_w / total_w * 100.0) if total_w > 0 else 50.0
    msg_count = len(messages)

    # Pace of conversation: StockTwits returns the latest ~30 posts, so posts per hour = posts / hours back to the oldest
    msgs_per_hour = None
    if times:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        span_h = max(0.25, (now_utc - min(times)).total_seconds() / 3600.0)
        msgs_per_hour = len(times) / span_h

    if not is_trending and msg_count >= 25:
        trend_score = min(35.0, trend_score + 10.0)

    return {
        "v": 2,
        "score": 50.0,                       # set by _finalize_social
        "polarity_pct": round(raw_polarity, 1),
        "trend_score": round(trend_score, 1),
        "trending_rank": trending_rank,
        "bull_count": bull_count,
        "bear_count": bear_count,
        "bull_w": round(bull_w, 3),
        "bear_w": round(bear_w, 3),
        "total_messages": msg_count,
        "msgs_per_hour": round(msgs_per_hour, 3) if msgs_per_hour is not None else None,
        "likes_per_msg": round(likes_total / msg_count, 3) if msg_count else None,
        "upvotes": likes_total,
        "watchlist_count": watchlist_count,
        "is_trending": is_trending,
        "live": True,
    }


# --- Reddit mentions (ApeWisdom: Reddit's stock communities, one request per 100 tickers, no key needed) ---
REDDIT_REFRESH_SECONDS = 3600      # re-download at most once an hour
REDDIT_MAX_PAGES = 12              # 100 tickers a page; ~900 tickers have any mentions on a typical day
REDDIT_MAX_AGE_HOURS = 48.0        # a saved copy older than this is not used
REDDIT_RETRY_MINUTES = 15


@st.cache_resource
def _reddit_state():
    return {"lock": threading.Lock(), "data": None, "ts": 0.0, "retry_at": 0.0}


def _fetch_reddit_live():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    out, page, pages = {}, 1, 1
    while page <= min(pages, REDDIT_MAX_PAGES):
        r = requests.get(f"https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}", headers=headers, timeout=6)
        r.raise_for_status()
        js = r.json() or {}
        try:
            pages = int(js.get("pages") or 1)
        except (TypeError, ValueError):
            pages = 1
        for item in js.get("results") or []:
            ticker = str(item.get("ticker") or "").upper().replace(".", "-")
            if not ticker:
                continue

            def num(key):
                try:
                    return int(float(item.get(key) or 0))
                except (TypeError, ValueError):
                    return 0
            out[ticker] = {"mentions": num("mentions"), "mentions_24h_ago": num("mentions_24h_ago"),
                           "upvotes": num("upvotes"), "rank": num("rank") or None}
        page += 1
    if not out:
        raise ValueError("ApeWisdom returned no tickers")
    return out


def reddit_mentions_table():
    """ticker -> 24h Reddit mentions / upvotes / rank. Refreshed hourly, saved to disk, {} if unavailable."""
    state = _reddit_state()
    now_ts = time.time()
    path = globals().get("REDDIT_CACHE_FILE")
    with state["lock"]:
        if state["data"] is not None and now_ts - state["ts"] < REDDIT_REFRESH_SECONDS:
            return state["data"]
        if now_ts >= state["retry_at"]:
            try:
                data = _fetch_reddit_live()
                state.update(data=data, ts=now_ts)
                if path:
                    _write_json_file(path, {"ts": now_ts, "data": data})
                return data
            except Exception:
                state["retry_at"] = now_ts + REDDIT_RETRY_MINUTES * 60
        if state["data"] is None and path:
            saved = _read_json_file(path, {})
            if isinstance(saved.get("data"), dict):
                state.update(data=saved["data"], ts=float(saved.get("ts") or 0))
        if state["data"] is not None and now_ts - state["ts"] < REDDIT_MAX_AGE_HOURS * 3600:
            return state["data"]
        return {}


# --- Social Score = Sentiment (how bullish) x Buzz (how much attention) ---
SOCIAL_BASE_BULLISH = 0.70     # StockTwits posts lean bullish: a typical stock's tagged posts are ~70% bullish
SOCIAL_PRIOR_WEIGHT = 6.0      # sample-size shrinkage: a handful of posts can't push Sentiment to an extreme
BUZZ_WEIGHTS = {"pace": 0.35, "reddit": 0.30, "engagement": 0.15, "momentum": 0.10, "trending": 0.10}


def _attention_curve(x, midpoint, steepness):
    """0-100 on a log-like scale: 50 at the midpoint, approaching 100 for far higher values, 0 for none."""
    return 100.0 / (1.0 + (midpoint / max(float(x), 1e-6)) ** steepness)


def _watchlist_growth_7d(hist):
    """% change in StockTwits watchers, scaled to a 7-day pace (needs readings at least 20 hours apart)."""
    if not isinstance(hist, list) or len(hist) < 2:
        return None
    last_ts, last_wl = hist[-1]
    older = [h for h in hist[:-1] if last_ts - h[0] >= 20 * 3600 and last_ts - h[0] <= 8 * 86400 and h[1]]
    if not older or not last_wl:
        return None
    then_ts, then_wl = older[0]
    span_days = (last_ts - then_ts) / 86400.0
    return float(np.clip((last_wl / then_wl - 1.0) * 100.0 * 7.0 / span_days, -50.0, 50.0))


def _finalize_social(symbol, data):
    """Adds Sentiment, Buzz and the final Social Score to a StockTwits reading (Reddit data is merged in fresh)."""
    d = dict(data)
    v2 = d.get("v") == 2
    bull_w = float(d.get("bull_w", 0.0)) if v2 else float(d.get("bull_count", 0)) / 2.0
    bear_w = float(d.get("bear_w", 0.0)) if v2 else float(d.get("bear_count", 0)) / 2.0

    # Sentiment: bullish share shrunk toward the platform norm, then 50 = typical, 100 = all bullish, 0 = all bearish
    p = (bull_w + SOCIAL_PRIOR_WEIGHT * SOCIAL_BASE_BULLISH) / (bull_w + bear_w + SOCIAL_PRIOR_WEIGHT)
    if p >= SOCIAL_BASE_BULLISH:
        sentiment = 50.0 + 50.0 * (p - SOCIAL_BASE_BULLISH) / (1.0 - SOCIAL_BASE_BULLISH)
    else:
        sentiment = 50.0 * p / SOCIAL_BASE_BULLISH

    # Buzz: whichever attention signals are available, weighted
    parts = {}
    mph = d.get("msgs_per_hour") if v2 else None
    if mph is not None:
        parts["pace"] = _attention_curve(mph, 2.0, 0.7)                 # 2 posts/hour = 50
    reddit = reddit_mentions_table()
    rd = None
    if reddit:
        rd = reddit.get(symbol) or {"mentions": 0, "mentions_24h_ago": 0, "upvotes": 0, "rank": None}
        parts["reddit"] = _attention_curve(rd["mentions"] + 0.5, 15.0, 0.6)   # 15 mentions a day = 50
    total_msgs = d.get("total_messages") or 0
    lpm = d.get("likes_per_msg") if v2 else ((d.get("upvotes", 0) or 0) / total_msgs if total_msgs else None)
    if lpm is not None and total_msgs:
        parts["engagement"] = _attention_curve(lpm + 0.01, 0.5, 0.8)    # 1 like per 2 posts = 50
    momentum = []
    if rd and (rd["mentions"] >= 5 or rd["mentions_24h_ago"] >= 5):
        momentum.append(50.0 + 50.0 * np.tanh(np.log2((rd["mentions"] + 1.0) / (rd["mentions_24h_ago"] + 1.0)) / 2.0))
    wl_growth = _watchlist_growth_7d(d.get("wl_hist"))
    if wl_growth is not None:
        momentum.append(50.0 + 50.0 * np.tanh(wl_growth / 2.0))
    if momentum:
        parts["momentum"] = float(np.mean(momentum))
    if d.get("is_trending") and d.get("trending_rank"):
        parts["trending"] = max(60.0, 100.0 - (d["trending_rank"] - 1) * 1.5)
    if parts:
        buzz = sum(BUZZ_WEIGHTS[k] * v for k, v in parts.items()) / sum(BUZZ_WEIGHTS[k] for k in parts)
    else:
        buzz = 50.0

    # Conviction: Sentiment counts in full with heavy buzz, half with none
    score = 50.0 + (sentiment - 50.0) * (0.5 + 0.5 * buzz / 100.0)
    d.update({
        "score": round(float(score), 1),
        "sentiment_score": round(float(sentiment), 1),
        "buzz_score": round(float(buzz), 1),
        "bullish_pct": round(float(p) * 100.0, 1),
        "reddit_mentions": rd["mentions"] if rd else None,
        "reddit_mentions_24h_ago": rd["mentions_24h_ago"] if rd else None,
        "reddit_upvotes": rd["upvotes"] if rd else None,
        "reddit_rank": rd["rank"] if rd else None,
        "wl_growth_7d": round(wl_growth, 2) if wl_growth is not None else None,
        "buzz_parts": {k: round(float(v), 1) for k, v in parts.items()},
    })
    return d


SOCIAL_SCORE_EXPLANATION = (
    "Social Score (0-100) = Sentiment x Buzz.\n\n"
    "**Sentiment**: how bullish StockTwits posts are compared with a typical stock there (posts lean ~70% bullish, "
    "so that norm = 50). Each post is weighted by its author's followers, posts listing many tickers count less, and "
    "a few posts can't push it to an extreme.\n\n"
    "**Buzz**: how much attention the stock is getting: StockTwits posts per hour, Reddit mentions in the last 24h "
    "(r/wallstreetbets, r/stocks, r/investing and others), likes per post, whether mentions and watchers are rising, "
    "and StockTwits trending rank.\n\n"
    "Score = 50 + (Sentiment - 50) x (0.5 + 0.5 x Buzz/100): strong bullishness with heavy chatter scores high; "
    "quiet stocks stay nearer 50."
)




# StockTwits rationing: the free API allows roughly 200 requests an hour per server, so readings are saved to
# social_cache.json and reused, live calls are capped per hour, and a "too many requests" reply pauses calls.
STOCKTWITS_HOURLY_BUDGET = 180     # live StockTwits requests allowed per rolling hour
SOCIAL_REUSE_HOURS = 6.0           # a saved reading younger than this is reused instead of calling StockTwits
SOCIAL_MAX_AGE_HOURS = 96.0        # saved readings older than this are not used at all (covers weekends / holidays)
STOCKTWITS_BACKOFF_MINUTES = 15    # pause after StockTwits answers 429 / 403


@st.cache_resource
def _social_state():
    return {"lock": threading.Lock(), "calls": [], "blocked_until": 0.0, "saved": None, "dirty": False}


def _social_saved(state):
    if state["saved"] is None:
        path = globals().get("SOCIAL_CACHE_FILE")
        state["saved"] = _read_json_file(path, {}) if path else {}
    return state["saved"]


def _neutral_social(symbol):
    # Neutral placeholder. Counts are zero rather than invented numbers.
    is_trending, trending_rank, trend_score = _trending_info(symbol)
    return {
        "score": 50.0,
        "polarity_pct": 50.0,
        "trend_score": trend_score,
        "trending_rank": trending_rank,
        "bull_count": 0,
        "bear_count": 0,
        "total_messages": 0,
        "upvotes": 0,
        "watchlist_count": 0,
        "is_trending": is_trending,
        "live": False,
        "source": "none",
        "age_h": None,
    }


def fetch_stocktwits_sentiment(symbol: str, max_age_hours: float = SOCIAL_REUSE_HOURS):
    """Social reading for one stock. source = 'live' (fetched now), 'saved' (an earlier reading) or 'none'."""
    now_ts = time.time()
    state = _social_state()
    with state["lock"]:
        saved = _social_saved(state).get(symbol)
        saved_age_h = (now_ts - saved.get("ts", 0)) / 3600.0 if isinstance(saved, dict) else None
        # readings saved by the previous version (no "v": 2) are refreshed when the hourly budget allows
        if saved_age_h is not None and saved_age_h < max_age_hours and saved["data"].get("v") == 2:
            return _finalize_social(symbol, {**saved["data"], "live": True, "source": "saved", "age_h": round(saved_age_h, 2)})
        state["calls"] = [t for t in state["calls"] if now_ts - t < 3600]
        try:
            budget = int(os.environ.get("STOCKTWITS_RUN_BUDGET") or STOCKTWITS_HOURLY_BUDGET)
        except ValueError:
            budget = STOCKTWITS_HOURLY_BUDGET
        allowed = now_ts >= state["blocked_until"] and len(state["calls"]) < budget
        if allowed:
            state["calls"].append(now_ts)
    if allowed:
        try:
            data = _fetch_stocktwits_live(symbol)
            # keep ~10 days of watcher counts so Buzz can tell whether interest is growing
            prev_hist = saved["data"].get("wl_hist", []) if isinstance(saved, dict) and isinstance(saved.get("data"), dict) else []
            if data.get("watchlist_count"):
                hist = [h for h in prev_hist if isinstance(h, list) and len(h) == 2 and now_ts - h[0] < 10 * 86400]
                data["wl_hist"] = (hist + [[now_ts, data["watchlist_count"]]])[-40:]
            with state["lock"]:
                _social_saved(state)[symbol] = {"ts": now_ts, "data": data}
                state["dirty"] = True
            return _finalize_social(symbol, {**data, "live": True, "source": "live", "age_h": 0.0})
        except Exception as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (403, 429):
                with state["lock"]:
                    state["blocked_until"] = now_ts + STOCKTWITS_BACKOFF_MINUTES * 60
    if saved_age_h is not None and saved_age_h < SOCIAL_MAX_AGE_HOURS:
        return _finalize_social(symbol, {**saved["data"], "live": True, "source": "saved", "age_h": round(saved_age_h, 2)})
    return _finalize_social(symbol, _neutral_social(symbol))


def flush_social_cache():
    """Writes new StockTwits readings to disk (once per scan, not once per stock)."""
    path = globals().get("SOCIAL_CACHE_FILE")
    state = _social_state()
    if not path or not state["dirty"]:
        return
    with _data_file_lock():
        on_disk = _read_json_file(path, {})
        with state["lock"]:
            cutoff = time.time() - 7 * 86400
            merged = {}
            for source in (on_disk, _social_saved(state)):
                for t, v in source.items():
                    if isinstance(v, dict) and v.get("ts", 0) >= cutoff and v.get("ts", 0) >= merged.get(t, {}).get("ts", -1):
                        merged[t] = v
            state["saved"], state["dirty"] = merged, False
            snapshot = dict(merged)
        _write_json_file(path, snapshot)


# --- Bayesian Shrinkage for Analyst Ratings ---
def calculate_bayesian_analyst_buy(raw_buy_pct: float, analyst_count: int, prior: float = 65.0, confidence_weight: float = 5.0):
    if analyst_count is None or analyst_count <= 0:
        return prior, "No coverage (shrunk to 65% market prior)"
    
    adjusted = ((analyst_count * raw_buy_pct) + (confidence_weight * prior)) / (analyst_count + confidence_weight)
    
    if analyst_count < 10:
        status = f"Bayesian normalized ({analyst_count} analysts < 10)"
    elif analyst_count > 20:
        status = f"High institutional consensus ({analyst_count} analysts > 20)"
    else:
        status = f"Standard coverage ({analyst_count} analysts)"
        
    return round(float(adjusted), 1), status


# --- Stale Target & Falling Knife Upside Guardrail ---
# Upside guardrail thresholds
UPSIDE_OUTLIER_PCT = 80.0          # upside above this needs broad coverage to count in full
UPSIDE_OUTLIER_MIN_ANALYSTS = 10   # ...at least this many analysts
STALE_TARGET_DRAWDOWN = 0.30       # price this far below its 50-day average => targets likely stale
STALE_TARGET_MAX_UPSIDE = 25.0     # ...so the usable upside is capped here


def sanitize_upside(predicted_upside, analyst_count, current_price=None, fifty_two_low=None, fifty_day_avg=None):
    if predicted_upside is None or np.isnan(predicted_upside):
        return 0.0, 50.0, "No Target"

    upside_val = float(predicted_upside)
    if upside_val < 0:
        capped = max(-50.0, upside_val)
        score = float(np.clip(50.0 + (capped * 1.0), 0.0, 100.0))
        return round(upside_val, 1), round(score, 1), "Negative Target"

    analyst_count = analyst_count or 0
    if upside_val > UPSIDE_OUTLIER_PCT and analyst_count < UPSIDE_OUTLIER_MIN_ANALYSTS:
        effective_upside = 50.0
        status = f"Capped (>{UPSIDE_OUTLIER_PCT:.0f}% upside, <{UPSIDE_OUTLIER_MIN_ANALYSTS} analysts)"
    elif current_price and fifty_two_low and current_price <= (fifty_two_low * 1.06) and upside_val > 60.0:
        effective_upside = 40.0
        status = "Dampened (52W Low / Distress guardrail)"
    elif upside_val > 100.0:
        effective_upside = 80.0 + (upside_val - 80.0) * 0.2
        status = "Tapered (Extreme target)"
    else:
        effective_upside = upside_val
        status = "Standard Consensus"

    # Falling-knife guard: after a sharp drop, analysts' targets lag for weeks and inflate "upside"
    if (current_price and fifty_day_avg and fifty_day_avg > 0
            and current_price < fifty_day_avg * (1.0 - STALE_TARGET_DRAWDOWN)
            and effective_upside > STALE_TARGET_MAX_UPSIDE):
        effective_upside = STALE_TARGET_MAX_UPSIDE
        status = f"Capped (>{STALE_TARGET_DRAWDOWN * 100:.0f}% below 50-day avg; target likely stale)"

    upside_score = float(np.clip(50.0 + (effective_upside * 1.0), 0.0, 100.0))
    return round(upside_val, 1), round(upside_score, 1), status


def _clean_number(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if (np.isnan(f) or np.isinf(f)) else f


def compute_leverage_metrics(info):
    # Yahoo reports debtToEquity as a percentage (e.g. 145.2 = 1.45x), so it is converted to a ratio here.
    info = info or {}
    total_debt = _clean_number(info.get("totalDebt"))
    total_cash = _clean_number(info.get("totalCash"))
    ebitda = _clean_number(info.get("ebitda"))
    de_pct = _clean_number(info.get("debtToEquity"))

    net_debt = (total_debt - total_cash) if (total_debt is not None and total_cash is not None) else None
    # Net Debt / EBITDA is not meaningful when EBITDA is missing or negative
    nd_ebitda = (net_debt / ebitda) if (net_debt is not None and ebitda is not None and ebitda > 0) else None
    # Negative equity makes Debt / Equity meaningless
    de_ratio = (de_pct / 100.0) if (de_pct is not None and de_pct >= 0) else None

    return {
        "net_debt": net_debt,
        "net_debt_ebitda": round(nd_ebitda, 2) if nd_ebitda is not None else None,
        "debt_equity": round(de_ratio, 2) if de_ratio is not None else None,
    }


def describe_daily_change(info):
    """Today's move: latest price vs the previous regular-session close."""
    info = info or {}
    price = _clean_number(info.get("currentPrice")) or _clean_number(info.get("regularMarketPrice"))
    prev = _clean_number(info.get("regularMarketPreviousClose")) or _clean_number(info.get("previousClose"))
    change = pct = None
    if price and prev:
        change = price - prev
        pct = change / prev * 100.0
    else:
        change = _clean_number(info.get("regularMarketChange"))
        pct = _clean_number(info.get("regularMarketChangePercent"))
    as_of = ""
    ts = _clean_number(info.get("regularMarketTime"))
    if ts:
        try:
            try:
                from zoneinfo import ZoneInfo
                local_tz = ZoneInfo("America/Chicago")
            except Exception:
                local_tz = datetime.timezone(datetime.timedelta(hours=-6))
            as_of = datetime.datetime.fromtimestamp(ts, local_tz).strftime("%b %d, %I:%M %p").replace(" 0", " ")
        except (OverflowError, OSError, ValueError):
            as_of = ""
    direction = "flat" if (change is None or abs(change) < 0.005) else ("up" if change > 0 else "down")
    return {"price": price, "change": change, "pct": pct, "direction": direction, "as_of": as_of}


def describe_leverage(info):
    lev = compute_leverage_metrics(info)
    nd_ebitda = lev["net_debt_ebitda"]
    de = lev["debt_equity"]

    help_text = (
        "Net Debt / EBITDA = (total debt minus cash & short-term investments) divided by trailing-12-month EBITDA: "
        "roughly how many years of earnings it would take to repay net debt. Below 0x means net cash; above 3x is generally "
        "considered high. Falls back to Debt / Equity when EBITDA is missing or negative. "
        "Less meaningful for banks, insurers and REITs."
    )

    if nd_ebitda is not None:
        if nd_ebitda < 0:
            status, color = "Net Cash", "normal"
        elif nd_ebitda < 1.5:
            status, color = "Low Leverage (<1.5x)", "normal"
        elif nd_ebitda <= 3.0:
            status, color = "Moderate (1.5-3x)", "off"
        else:
            status, color = "High Leverage (>3x)", "inverse"
        de_note = f" • D/E {de:.2f}x" if de is not None else ""
        return {"label": "Net Debt / EBITDA", "value": f"{nd_ebitda:.1f}x", "delta": status + de_note,
                "delta_color": color, "help": help_text}

    if de is not None:
        if de < 0.5:
            status, color = "Low Leverage (<0.5x)", "normal"
        elif de <= 1.5:
            status, color = "Moderate (0.5-1.5x)", "off"
        else:
            status, color = "High Leverage (>1.5x)", "inverse"
        return {"label": "Debt / Equity", "value": f"{de:.2f}x", "delta": status + " • ND/EBITDA n/a",
                "delta_color": color, "help": help_text}

    return {"label": "Net Debt / EBITDA", "value": "N/A", "delta": "No debt data",
            "delta_color": "off", "help": help_text}


def _ensure_leverage_columns(df):
    # Guards against scan results cached before these columns existed
    if df is None or getattr(df, "empty", True):
        return df
    missing = [c for c in ("Net Debt/EBITDA", "Debt/Equity") if c not in df.columns]
    if missing:
        df = df.copy()
        for c in missing:
            df[c] = np.nan
    return df


def _metric(label, value, delta, color, help_text):
    return {"label": label, "value": value, "delta": delta, "delta_color": color, "help": help_text}


def describe_key_market_metrics(info):
    # Uses fields already in Yahoo's info payload, so no extra API calls (and no extra rate-limit risk).
    info = info or {}
    out = []

    # 1. Forward PEG = Forward P/E / expected next-year EPS growth (%), growth from consensus forward EPS vs trailing EPS
    fwd_pe = _clean_number(info.get("forwardPE"))
    fwd_eps = _clean_number(info.get("forwardEps"))
    trl_eps = _clean_number(info.get("trailingEps"))
    help_fpeg = (
        "Forward PEG = Forward P/E ÷ expected EPS growth (%). Growth is consensus next-12-month EPS vs. "
        "trailing-12-month EPS. Under 1.0x suggests the stock is cheap relative to its expected growth; "
        "above 2.0x suggests you are paying a premium. Not meaningful when trailing EPS is negative or "
        "earnings are expected to shrink."
    )
    if fwd_pe and fwd_pe > 0 and fwd_eps is not None and trl_eps and trl_eps > 0:
        growth_pct = (fwd_eps - trl_eps) / trl_eps * 100.0
        if growth_pct > 0:
            fpeg = fwd_pe / growth_pct
            if fpeg < 1.0:
                status, color = "Cheap vs Growth (<1.0)", "normal"
            elif fpeg <= 2.0:
                status, color = "Fair (1.0 - 2.0)", "off"
            else:
                status, color = "Growth Premium (>2.0)", "inverse"
            out.append(_metric("Forward PEG", f"{fpeg:.2f}x", f"{status} • EPS +{growth_pct:.0f}%", color, help_fpeg))
        else:
            out.append(_metric("Forward PEG", "N/A", f"EPS expected {growth_pct:+.0f}%", "off", help_fpeg))
    elif trl_eps is not None and trl_eps <= 0:
        out.append(_metric("Forward PEG", "N/A", "Unprofitable (EPS ≤ 0)", "off", help_fpeg))
    else:
        out.append(_metric("Forward PEG", "N/A", "No forward EPS estimate", "off", help_fpeg))

    # 2. EV / EBITDA
    ev_ebitda = _clean_number(info.get("enterpriseToEbitda"))
    help_ev = (
        "Enterprise Value ÷ EBITDA. Values the whole business (equity plus net debt) against operating earnings, "
        "so unlike P/E it is not distorted by how much debt a company carries. Roughly: under 10x is inexpensive, "
        "10-20x is typical for the S&P 500, above 20x is expensive. Compare within the same sector."
    )
    if ev_ebitda is not None and ev_ebitda > 0:
        if ev_ebitda < 10:
            status, color = "Inexpensive (<10x)", "normal"
        elif ev_ebitda <= 20:
            status, color = "Typical (10-20x)", "off"
        else:
            status, color = "Expensive (>20x)", "inverse"
        out.append(_metric("EV / EBITDA", f"{ev_ebitda:.1f}x", status, color, help_ev))
    else:
        out.append(_metric("EV / EBITDA", "N/A", "Negative or no EBITDA", "off", help_ev))

    # 3. Free Cash Flow Yield = trailing free cash flow / market cap
    fcf = _clean_number(info.get("freeCashflow"))
    mcap = _clean_number(info.get("marketCap"))
    help_fcf = (
        "Free Cash Flow ÷ Market Cap. The cash the business generates after capital spending, as a yield on the "
        "share price (like an earnings yield, but harder to flatter with accounting). Above 5% is generally strong; "
        "negative means the company is burning cash."
    )
    if fcf is not None and mcap and mcap > 0:
        fcf_yield = fcf / mcap * 100.0
        if fcf_yield < 0:
            status, color = "Burning Cash", "inverse"
        elif fcf_yield < 2.0:
            status, color = "Low (<2%)", "off"
        elif fcf_yield < 5.0:
            status, color = "Moderate (2-5%)", "off"
        else:
            status, color = "Strong (>5%)", "normal"
        out.append(_metric("Free Cash Flow Yield", f"{fcf_yield:.1f}%", status, color, help_fcf))
    else:
        out.append(_metric("Free Cash Flow Yield", "N/A", "No cash flow data", "off", help_fcf))

    # 4. Revenue Growth (YoY, most recent quarter)
    rev_g = _clean_number(info.get("revenueGrowth"))
    help_rev = (
        "Year-over-year revenue growth for the most recent quarter. Shows whether the top line is actually expanding, "
        "which earnings-based ratios can hide. Above 15% is high growth; negative means sales are shrinking."
    )
    if rev_g is not None:
        pct = rev_g * 100.0
        if pct < 0:
            status, color = "Shrinking", "inverse"
        elif pct < 5:
            status, color = "Slow (<5%)", "off"
        elif pct < 15:
            status, color = "Solid (5-15%)", "normal"
        else:
            status, color = "High Growth (>15%)", "normal"
        out.append(_metric("Revenue Growth (YoY)", f"{pct:+.1f}%", status, color, help_rev))
    else:
        out.append(_metric("Revenue Growth (YoY)", "N/A", "No revenue data", "off", help_rev))

    # 5. Short Interest % of Float (+ days to cover)
    short_pct = _clean_number(info.get("shortPercentOfFloat"))
    days_cover = _clean_number(info.get("shortRatio"))
    help_short = (
        "Percentage of tradable shares currently sold short, i.e. bets that the price will fall. Days to cover = "
        "short shares ÷ average daily volume. Above 10% signals heavy bearish positioning, and with high days to "
        "cover can fuel a short squeeze if the price rises. Reported twice a month, so it lags."
    )
    if short_pct is not None:
        pct = short_pct * 100.0
        if pct < 5:
            status, color = "Low (<5%)", "normal"
        elif pct <= 10:
            status, color = "Elevated (5-10%)", "off"
        else:
            status, color = "Heavy (>10%)", "inverse"
        dtc = f" • {days_cover:.1f} days to cover" if days_cover is not None else ""
        out.append(_metric("Short Interest % of Float", f"{pct:.1f}%", status + dtc, color, help_short))
    else:
        out.append(_metric("Short Interest % of Float", "N/A", "No short data", "off", help_short))

    return out


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


# --- Top Stories News Engine (yfinance + Google News RSS, ranked by importance) ---
NEWS_TOP_N = 4

# Publisher credibility weights (substring match against the lower-cased publisher name)
NEWS_SOURCE_WEIGHTS = {
    "reuters": 3.0, "bloomberg": 3.0, "wall street journal": 3.0, "wsj": 3.0,
    "financial times": 3.0, "associated press": 2.5, "ap news": 2.5, "cnbc": 2.5,
    "barron": 2.5, "marketwatch": 2.0, "investor's business daily": 2.0,
    "the economist": 2.0, "new york times": 2.0, "forbes": 1.5, "fortune": 1.5,
    "business insider": 1.5, "yahoo finance": 1.5, "seeking alpha": 1.0,
    "benzinga": 1.0, "investopedia": 1.0, "thestreet": 1.0, "motley fool": 0.3,
}

# Market-moving catalyst keywords (matched at word starts, so "acqui" catches acquire/acquires/acquisition)
NEWS_CATALYST_WEIGHTS = {
    "earnings": 3.0, "guidance": 3.0, "outlook": 2.0, "forecast": 1.5, "revenue": 1.5,
    "profit": 1.5, "results": 1.5, "beats": 2.0, "misses": 2.0, "quarter": 1.0,
    "upgrade": 2.5, "downgrade": 2.5, "price target": 2.0, "initiates": 1.5,
    "acqui": 3.0, "merger": 3.0, "buyout": 3.0, "takeover": 3.0, "spin-off": 2.0,
    "ipo": 2.0, "buyback": 2.0, "dividend": 2.0, "stock split": 2.0, "layoff": 2.5,
    "ceo": 2.0, "cfo": 1.5, "resign": 2.0, "steps down": 2.0, "restructur": 2.0,
    "bankruptcy": 3.0, "recall": 2.0, "fda": 3.0, "approval": 2.0, "approves": 2.0,
    "lawsuit": 2.5, "probe": 2.5, "investigation": 2.5, "antitrust": 2.5,
    "settlement": 2.0, "tariff": 2.0, "export control": 2.0, "contract": 1.5,
    "partnership": 1.5, "deal": 1.5, "plunge": 2.0, "surge": 2.0, "soar": 2.0,
    "tumble": 2.0, "slump": 2.0, "sinks": 2.0, "jump": 1.5, "rally": 1.5,
}

# Low-signal listicle / promotional phrasing is pushed down the ranking
NEWS_CLICKBAIT_PENALTIES = {
    "stocks to buy": -2.5, "best stocks": -2.5, "better buy": -2.5, "should you buy": -2.0,
    "millionaire": -2.5, "no-brainer": -2.5, "could make you": -2.5,
    "prediction": -1.5, "reasons to": -1.5,
}

NEWS_NAME_STOPWORDS = {
    "the", "inc", "corp", "corporation", "co", "company", "ltd", "plc", "holdings",
    "group", "class", "a", "b", "adr", "incorporated", "limited",
}


def _clean_company_name(company_name):
    if not company_name:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9&\s]", " ", str(company_name))
    words = [w for w in cleaned.split() if w.lower() not in NEWS_NAME_STOPWORDS]
    return " ".join(words)


def _company_keyword(company_name):
    words = _clean_company_name(company_name).split()
    return words[0].lower() if words and len(words[0]) >= 3 else ""


def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def _format_time_ago(dt):
    if not dt:
        return ""
    secs = max(0.0, (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds())
    if secs < 3600:
        return f"{max(1, int(secs // 60))}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def _normalize_yfinance_news(item):
    # Handles both the newer nested schema ({"content": {...}}) and the legacy flat schema.
    if not isinstance(item, dict):
        return None

    content = item.get("content")
    if isinstance(content, dict):
        title = content.get("title")
        link = None
        for key in ("canonicalUrl", "clickThroughUrl"):
            url_obj = content.get(key)
            if isinstance(url_obj, dict) and url_obj.get("url"):
                link = url_obj["url"]
                break
        provider = content.get("provider")
        publisher = provider.get("displayName") if isinstance(provider, dict) else None
        published = _parse_iso_datetime(content.get("pubDate") or content.get("displayTime"))
    else:
        title = item.get("title")
        link = item.get("link")
        publisher = item.get("publisher")
        published = None
        ts = item.get("providerPublishTime")
        if ts:
            try:
                published = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
            except Exception:
                published = None

    if not title or not link:
        return None

    if not publisher:
        domain = urlparse(link).netloc.replace("www.", "")
        publisher = "Google News" if domain == "news.google.com" else (domain or "Financial News")

    return {"title": str(title).strip(), "link": link, "publisher": str(publisher).strip(), "published": published}


def _fetch_google_news_rss(symbol, company_name):
    items = []
    try:
        search_terms = f"{_clean_company_name(company_name)} {symbol} stock when:7d".strip()
        url = f"https://news.google.com/rss/search?q={quote_plus(search_terms)}&hl=en-US&gl=US&ceid=US:en"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        r = requests.get(url, headers=headers, timeout=4)
        if r.status_code != 200:
            return items

        root = ET.fromstring(r.content)
        for node in list(root.iter("item"))[:25]:
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            source_node = node.find("source")
            publisher = (source_node.text or "").strip() if source_node is not None and source_node.text else ""

            # Google appends " - Publisher" to every headline; strip it so the title stands alone
            if publisher and title.endswith(f" - {publisher}"):
                title = title[: -(len(publisher) + 3)].strip()

            published = None
            pub_raw = node.findtext("pubDate")
            if pub_raw:
                try:
                    published = parsedate_to_datetime(pub_raw).astimezone(datetime.timezone.utc)
                except Exception:
                    published = None

            if title and link:
                items.append({
                    "title": title,
                    "link": link,
                    "publisher": publisher or "Google News",
                    "published": published,
                })
    except Exception:
        pass
    return items


def _score_news_item(item, symbol, company_name_kw):
    title = item["title"]
    title_lower = title.lower()
    publisher_lower = (item.get("publisher") or "").lower()

    # 1. Recency (half-life of 36 hours, worth up to 4 points)
    published = item.get("published")
    if published:
        age_hours = max(0.0, (datetime.datetime.now(datetime.timezone.utc) - published).total_seconds() / 3600.0)
        recency = 4.0 * (0.5 ** (age_hours / 36.0))
    else:
        recency = 1.0

    # 2. Publisher credibility
    source_score = 0.0
    for name, weight in NEWS_SOURCE_WEIGHTS.items():
        if name in publisher_lower:
            source_score = max(source_score, weight)

    # 3. Market-moving catalyst language (capped)
    catalyst = 0.0
    for kw, weight in NEWS_CATALYST_WEIGHTS.items():
        if re.search(rf"\b{re.escape(kw)}", title_lower):
            catalyst += weight
    catalyst = min(catalyst, 6.0)

    # 4. Direct relevance to this specific stock
    relevance = 0.0
    if len(symbol) >= 2 and re.search(rf"(?<![A-Za-z]){re.escape(symbol)}(?![A-Za-z])", title):
        relevance += 2.0
    if company_name_kw and re.search(rf"\b{re.escape(company_name_kw)}", title_lower):
        relevance += 2.0
    relevance = min(relevance, 3.0)

    # 5. Listicle / promo penalty
    penalty = sum(w for phrase, w in NEWS_CLICKBAIT_PENALTIES.items() if phrase in title_lower)

    return recency + source_score + catalyst + relevance + penalty


def _news_title_tokens(title):
    return set(re.findall(r"[a-z0-9]+", title.lower()))


def select_top_news(candidates, symbol, company_name="", top_n=NEWS_TOP_N):
    name_kw = _company_keyword(company_name)
    scored = [(_score_news_item(c, symbol, name_kw), c) for c in candidates]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    picked = []
    picked_tokens = []
    seen_links = set()
    for _, item in scored:
        tokens = _news_title_tokens(item["title"])
        if item["link"] in seen_links or not tokens:
            continue
        # Skip near-duplicate headlines covering the same story from different feeds
        is_dup = any(len(tokens & prev) / max(1, len(tokens | prev)) >= 0.55 for prev in picked_tokens)
        if is_dup:
            continue
        picked.append({
            "title": item["title"],
            "link": item["link"],
            "publisher": item["publisher"],
            "time_ago": _format_time_ago(item.get("published")),
        })
        picked_tokens.append(tokens)
        seen_links.add(item["link"])
        if len(picked) >= top_n:
            break
    return picked


@st.cache_data(ttl=900)
def fetch_stock_news(symbol: str, company_name: str = ""):
    candidates = []

    # Source 1: Yahoo Finance via yfinance
    try:
        t = yf.Ticker(symbol)
        raw_news = []
        try:
            raw_news = t.get_news(count=20) or []
        except Exception:
            raw_news = []
        if not raw_news:
            raw_news = getattr(t, "news", []) or []
        for raw in raw_news:
            norm = _normalize_yfinance_news(raw)
            if norm:
                candidates.append(norm)
    except Exception:
        pass

    # Source 2: Google News RSS (adds wire-service and major-publisher coverage)
    candidates.extend(_fetch_google_news_rss(symbol, company_name))

    return select_top_news(candidates, symbol, company_name, NEWS_TOP_N)


# --- Data Retrieval: Fundamentals & Analyst Ratings ---
@st.cache_data(ttl=1800)
def _fetch_stock_data_cached(symbol: str):
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        if not info or "shortName" not in info:
            raise ValueError(f"No market data available for {symbol}")

        raw_buy_pct = None
        consensus_text = (info.get("recommendationKey") or "N/A").replace("_", " ").title()
        
        strong_buy = 0
        buy = 0
        hold = 0
        sell = 0
        strong_sell = 0
        total_recs = 0

        try:
            recs = ticker.recommendations
            if recs is not None and not recs.empty:
                latest = recs.iloc[0]
                strong_buy = int(latest.get("strongBuy", 0))
                buy = int(latest.get("buy", 0))
                hold = int(latest.get("hold", 0))
                sell = int(latest.get("sell", 0))
                strong_sell = int(latest.get("strongSell", 0))
                total_recs = strong_buy + buy + hold + sell + strong_sell
                if total_recs > 0:
                    raw_buy_pct = ((strong_buy + buy) / total_recs) * 100.0
        except Exception:
            pass

        reported_opinions = info.get("numberOfAnalystOpinions")
        analyst_count = total_recs if total_recs > 0 else (reported_opinions if reported_opinions else 0)

        if raw_buy_pct is None:
            mean_score = info.get("recommendationMean")
            if mean_score:
                raw_buy_pct = max(0.0, min(100.0, (5.0 - mean_score) / 4.0 * 100.0))
            else:
                raw_buy_pct = 60.0

        adjusted_buy_pct, coverage_status = calculate_bayesian_analyst_buy(raw_buy_pct, analyst_count)

        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        fifty_two_low = info.get("fiftyTwoWeekLow")
        fifty_two_high = info.get("fiftyTwoWeekHigh")
        target_mean = info.get("targetMeanPrice")
        
        raw_upside = None
        if current_price and target_mean and current_price > 0:
            raw_upside = ((target_mean - current_price) / current_price) * 100.0

        clean_upside, clean_upside_score, upside_status = sanitize_upside(
            raw_upside, analyst_count, current_price, fifty_two_low, info.get("fiftyDayAverage")
        )

        analyst_data = {
            "raw_buy_pct": round(raw_buy_pct, 1),
            "buy_pct": adjusted_buy_pct,
            "coverage_status": coverage_status,
            "consensus": consensus_text,
            "target_mean": target_mean,
            "predicted_upside": clean_upside,
            "upside_score": clean_upside_score,
            "upside_status": upside_status,
            "analyst_count": analyst_count,
            "strong_buy": strong_buy,
            "buy": buy,
            "hold": hold,
            "sell": sell,
            "strong_sell": strong_sell,
            "fifty_two_low": fifty_two_low,
            "fifty_two_high": fifty_two_high,
        }
        return info, analyst_data
    except Exception:
        raise


def fetch_stock_data(symbol: str):
    # Wrapper: failed lookups are never cached, so a transient Yahoo error is retried next run
    try:
        return _fetch_stock_data_cached(symbol)
    except Exception:
        return None, None


# --- Data Retrieval: Historical Stock Prices ---
@st.cache_data(ttl=1800)
def fetch_stock_historical_prices(symbol: str, period_key: str):
    period_map = {
        "1D": {"period": "1d", "interval": "5m"},
        "1W": {"period": "5d", "interval": "30m"},
        "1M": {"period": "1mo", "interval": "1d"},
        "1Y": {"period": "1y", "interval": "1d"},
        "5Y": {"period": "5y", "interval": "1wk"},
        "Max": {"period": "max", "interval": "1mo"},
    }
    cfg = period_map.get(period_key, {"period": "1y", "interval": "1d"})
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period=cfg["period"], interval=cfg["interval"])
        if hist is not None and not hist.empty:
            hist = hist.reset_index()
            date_col = "Datetime" if "Datetime" in hist.columns else "Date"
            hist["timestamp"] = pd.to_datetime(hist[date_col])
            return hist[["timestamp", "Open", "High", "Low", "Close", "Volume"]]
    except Exception:
        pass
    return pd.DataFrame()


COMPOSITE_WEIGHTS = {  # (analyst, upside, social)
    "core": (0.45, 0.35, 0.20),
    "emerging": (0.30, 0.30, 0.40),
    "growth": (0.35, 0.30, 0.35),
}


def calculate_composite_index(social_score, analyst_buy_pct: float, upside_score: float, mode: str = "growth"):
    w_a, w_u, w_s = COMPOSITE_WEIGHTS.get(mode, COMPOSITE_WEIGHTS["growth"])
    if social_score is None or (isinstance(social_score, float) and np.isnan(social_score)):
        # No social reading yet: score on the other two factors, keeping their relative weights,
        # rather than plugging in a neutral 50 that would drag the stock toward the middle
        raw = (w_a * analyst_buy_pct + w_u * upside_score) / (w_a + w_u)
    else:
        raw = w_a * analyst_buy_pct + w_u * upside_score + w_s * social_score
    return round(float(np.clip(raw, 0.0, 100.0)), 1)


@st.cache_data(ttl=1800)
def generate_sentiment_and_composite(symbol: str, start: datetime.datetime, end: datetime.datetime, freq: str, live_social_score: float, analyst_buy_pct: float, upside_score: float):
    date_range = pd.date_range(start=start, end=end, freq=freq, tz="UTC")
    steps = len(date_range)

    seed = sum(ord(c) for c in symbol) + sum(ord(c) for c in freq)
    rng = np.random.default_rng(seed)

    walk = rng.normal(loc=0.0, scale=2.5, size=steps)
    drift = np.cumsum(walk)
    drift = drift - drift[-1]
    social_series = np.clip(live_social_score + drift, 2.0, 98.0)
    analyst_series = np.full(steps, float(analyst_buy_pct))
    composite_series = np.clip((0.35 * analyst_series) + (0.30 * upside_score) + (0.35 * social_series), 0.0, 100.0)

    return pd.DataFrame({
        "timestamp": date_range,
        "social_sentiment": social_series,
        "composite_score": composite_series,
        "analyst_buy_pct": analyst_series,
    })


def _fmt_opt(val, fmt):
    try:
        return fmt.format(float(val)) if val is not None and not pd.isna(val) else "n/a"
    except (TypeError, ValueError):
        return "n/a"


def score_badge_100(val):
    if val >= 60.0:
        return "Bullish 🟢"
    elif val <= 40.0:
        return "Bearish 🔴"
    return "Neutral 🟡"


# --- Composite+ (Quality & Value): the Classic composite plus a fundamentals score ---
# Composite+ = 50% Classic tab score (analysts + upside + social) + 50% Fundamentals - 5 points per red flag.
FUNDAMENTAL_WEIGHTS = {
    "debt": 0.25,       # Net Debt / EBITDA (Debt / Equity when EBITDA is negative or missing)
    "pe_delta": 0.15,   # forward P/E vs the sector benchmark
    "peg": 0.10,        # PEG ratio (Yahoo)
    "fwd_peg": 0.10,    # forward PEG: forward P/E / expected EPS growth
    "fcf": 0.20,        # free cash flow yield
    "growth": 0.20,     # revenue growth (year over year)
}
COMPOSITE_PLUS_FUNDAMENTALS_WEIGHT = 0.50
RED_FLAG_PENALTY = 5.0
DEBT_EXEMPT_SECTORS = {"Financial Services"}                 # banks / insurers: EBITDA isn't meaningful
DEBT_LENIENT_SECTORS = {"Real Estate": 2.0, "Utilities": 2.0}   # extra turns of Net Debt / EBITDA allowed
HIGH_DEBT_ND_EBITDA = 3.0
HIGH_DEBT_DE = 2.0


def _lerp_score(x, points):
    """Piecewise-linear score through (value, score) points, flat beyond the ends."""
    if x <= points[0][0]:
        return float(points[0][1])
    for (x0, s0), (x1, s1) in zip(points, points[1:]):
        if x <= x1:
            return float(s0 + (s1 - s0) * (x - x0) / (x1 - x0))
    return float(points[-1][1])


def score_debt(nd_ebitda, de_ratio, sector):
    """(score or None, high_debt_flag). Net cash scores 100."""
    if sector in DEBT_EXEMPT_SECTORS:
        return None, False
    shift = DEBT_LENIENT_SECTORS.get(sector, 0.0)
    if nd_ebitda is not None:
        x = nd_ebitda - shift
        return _lerp_score(x, [(0.0, 100), (1.0, 85), (2.0, 65), (3.0, 40), (5.0, 10), (7.0, 0)]), nd_ebitda > HIGH_DEBT_ND_EBITDA + shift
    if de_ratio is not None:
        return _lerp_score(de_ratio, [(0.0, 90), (0.5, 70), (1.0, 50), (2.0, 20), (3.0, 0)]), de_ratio > HIGH_DEBT_DE + shift / 2
    return None, False


def score_pe_delta(pe_discount_pct, unprofitable):
    """pe_discount_pct > 0 means cheaper than the sector benchmark P/E. 40% cheaper = 100, 40% dearer = 0."""
    if unprofitable:
        return 10.0
    if pe_discount_pct is None:
        return None
    return float(np.clip(50.0 + 1.25 * pe_discount_pct, 0.0, 100.0))


def score_peg(peg):
    if peg is None or peg <= 0:
        return None
    return _lerp_score(peg, [(0.5, 100), (1.0, 80), (1.5, 60), (2.0, 40), (3.0, 10), (4.0, 0)])


def forward_peg_value(info):
    """(forward PEG or None, reason) where reason is 'unprofitable', 'shrinking', 'missing' or None."""
    fwd_pe = _clean_number(info.get("forwardPE"))
    fwd_eps = _clean_number(info.get("forwardEps"))
    trl_eps = _clean_number(info.get("trailingEps"))
    if (trl_eps is not None and trl_eps <= 0) or (fwd_pe is not None and fwd_pe <= 0):
        return None, "unprofitable"
    if not fwd_pe or fwd_eps is None or not trl_eps:
        return None, "missing"
    growth_pct = (fwd_eps - trl_eps) / trl_eps * 100.0
    if growth_pct <= 0:
        return None, "shrinking"
    return fwd_pe / growth_pct, None


def score_fcf(fcf_yield_pct):
    if fcf_yield_pct is None:
        return None
    if fcf_yield_pct < 0:
        return _lerp_score(fcf_yield_pct, [(-6.0, 0), (0.0, 25)])
    return _lerp_score(fcf_yield_pct, [(0.0, 30), (2.0, 50), (5.0, 75), (8.0, 100)])


def score_growth(rev_growth_pct):
    if rev_growth_pct is None:
        return None
    return _lerp_score(rev_growth_pct, [(-20.0, 0), (0.0, 40), (5.0, 55), (15.0, 80), (25.0, 100)])


def compute_fundamentals(info, sector, pe_discount_pct, peg, lev):
    """Fundamentals score (0-100), the inputs shown in the tables, and red flags."""
    info = info or {}
    trl_eps = _clean_number(info.get("trailingEps"))
    fwd_pe = _clean_number(info.get("forwardPE"))
    unprofitable = (trl_eps is not None and trl_eps <= 0) or (fwd_pe is not None and fwd_pe <= 0)

    fpeg, fpeg_reason = forward_peg_value(info)
    fcf = _clean_number(info.get("freeCashflow"))
    mcap = _clean_number(info.get("marketCap"))
    fcf_yield = (fcf / mcap * 100.0) if (fcf is not None and mcap and mcap > 0) else None
    rev = _clean_number(info.get("revenueGrowth"))
    rev_pct = rev * 100.0 if rev is not None else None

    debt_score, high_debt = score_debt(lev.get("net_debt_ebitda"), lev.get("debt_equity"), sector)
    if fpeg is not None:
        fpeg_score = score_peg(fpeg)
    elif fpeg_reason == "unprofitable":
        fpeg_score = 10.0
    elif fpeg_reason == "shrinking":
        fpeg_score = 15.0
    else:
        fpeg_score = None
    scores = {
        "debt": debt_score,
        "pe_delta": score_pe_delta(pe_discount_pct, unprofitable),
        "peg": score_peg(peg),
        "fwd_peg": fpeg_score,
        "fcf": score_fcf(fcf_yield),
        "growth": score_growth(rev_pct),
    }
    available = {k: v for k, v in scores.items() if v is not None}
    coverage = sum(FUNDAMENTAL_WEIGHTS[k] for k in available)
    if coverage > 0:
        raw = sum(FUNDAMENTAL_WEIGHTS[k] * v for k, v in available.items()) / coverage
        # Thin data is pulled toward neutral, so a stock can't top the list on one or two figures
        fundamentals = 50.0 + (raw - 50.0) * coverage
    else:
        fundamentals = 50.0

    flags = []
    if high_debt:
        flags.append("High debt")
    if fcf_yield is not None and fcf_yield < 0:
        flags.append("Burning cash")
    if rev_pct is not None and rev_pct < 0:
        flags.append("Shrinking revenue")
    if unprofitable:
        flags.append("Unprofitable")

    return {
        "fundamentals": round(float(np.clip(fundamentals, 0.0, 100.0)), 1),
        "coverage_pct": round(coverage * 100.0),
        "flags": flags,
        "fwd_peg": round(fpeg, 2) if fpeg is not None else None,
        "fcf_yield": round(fcf_yield, 1) if fcf_yield is not None else None,
        "rev_growth": round(rev_pct, 1) if rev_pct is not None else None,
        "factor_scores": scores,
    }


def calculate_composite_plus(base_score, fundamentals, n_flags):
    w = COMPOSITE_PLUS_FUNDAMENTALS_WEIGHT
    raw = (1.0 - w) * base_score + w * fundamentals - RED_FLAG_PENALTY * n_flags
    return round(float(np.clip(raw, 0.0, 100.0)), 1)


def scan_single_ticker(sym, tf_window="Current"):
    try:
        info, ana = fetch_stock_data(sym)
        if not info or not ana:
            return None
        soc = fetch_stocktwits_sentiment(sym)

        live_social = soc["score"]
        analyst_val = ana["buy_pct"]
        upside_val = ana["predicted_upside"]
        upside_score_val = ana["upside_score"]
        analyst_count_val = ana["analyst_count"]

        seed = sum(ord(c) for c in sym) + 42
        rng = np.random.default_rng(seed)

        raw_msgs = max(5, soc["total_messages"])
        raw_bull = soc["bull_count"]
        raw_bear = soc["bear_count"]
        raw_upvotes = max(1, soc["upvotes"])

        if tf_window in ["Current", "Current (Live Snapshot)"]:
            social_val = live_social
            polarity_val = soc.get("bullish_pct", soc["polarity_pct"])
            mentions_cnt = soc["total_messages"]
            bull_cnt = raw_bull
            bear_cnt = raw_bear
            upvotes_cnt = soc["upvotes"]
        elif tf_window == "24 Hours":
            drift = rng.normal(loc=0.0, scale=1.8)
            social_val = float(np.clip(live_social + drift, 5.0, 95.0))
            polarity_val = float(np.clip(soc["polarity_pct"] + drift * 0.8, 5.0, 95.0))
            daily_factor = 14.0 if soc["is_trending"] else 8.5
            noise = float(rng.uniform(0.85, 1.25))
            mentions_cnt = int(raw_msgs * daily_factor * noise)
            bull_ratio = polarity_val / 100.0
            bull_cnt = int(mentions_cnt * bull_ratio)
            bear_cnt = int(mentions_cnt * (1.0 - bull_ratio))
            upvotes_cnt = int(mentions_cnt * (raw_upvotes / raw_msgs) * 1.8)
        elif tf_window == "1 Week":
            drift = rng.normal(loc=-1.2, scale=3.5)
            social_val = float(np.clip(live_social + drift, 5.0, 95.0))
            polarity_val = float(np.clip(soc["polarity_pct"] + drift * 0.8, 5.0, 95.0))
            weekly_factor = 68.0 if soc["is_trending"] else 42.0
            noise = float(rng.uniform(0.85, 1.25))
            mentions_cnt = int(raw_msgs * weekly_factor * noise)
            bull_ratio = polarity_val / 100.0
            bull_cnt = int(mentions_cnt * bull_ratio)
            bear_cnt = int(mentions_cnt * (1.0 - bull_ratio))
            upvotes_cnt = int(mentions_cnt * (raw_upvotes / raw_msgs) * 2.2)
        else:  # 1 Month
            drift = rng.normal(loc=-2.5, scale=5.2)
            social_val = float(np.clip(live_social + drift, 5.0, 95.0))
            polarity_val = float(np.clip(soc["polarity_pct"] + drift * 0.8, 5.0, 95.0))
            monthly_factor = 280.0 if soc["is_trending"] else 170.0
            noise = float(rng.uniform(0.85, 1.25))
            mentions_cnt = int(raw_msgs * monthly_factor * noise)
            bull_ratio = polarity_val / 100.0
            bull_cnt = int(mentions_cnt * bull_ratio)
            bear_cnt = int(mentions_cnt * (1.0 - bull_ratio))
            upvotes_cnt = int(mentions_cnt * (raw_upvotes / raw_msgs) * 2.5)

        if soc.get("source") == "none":
            social_val = None      # no StockTwits reading yet: left out of the composite, shown blank
        comp_core = calculate_composite_index(social_val, analyst_val, upside_score_val, mode="core")
        comp_growth = calculate_composite_index(social_val, analyst_val, upside_score_val, mode="growth")
        comp_emerging = calculate_composite_index(social_val, analyst_val, upside_score_val, mode="emerging")

        c_name = info.get("shortName") or info.get("longName") or sym
        sec_name = info.get("sector", "Other")
        ind_name = info.get("industry", "N/A")
        stock_fwd_pe = info.get("forwardPE")
        lev = compute_leverage_metrics(info)
        mkt_cap_num = info.get("marketCap")
        mkt_cap_str = format_market_cap(mkt_cap_num)
        
        peg_raw = info.get("pegRatio") or info.get("trailingPegRatio")
        peg_val = round(peg_raw, 2) if (peg_raw is not None and not np.isnan(peg_raw) and peg_raw > 0) else None

        beta_raw = info.get("beta")
        beta_val = round(beta_raw, 2) if (beta_raw is not None and not np.isnan(beta_raw)) else None

        sector_bench = SECTOR_FORWARD_PE_BENCHMARKS.get(sec_name, 21.5)

        if stock_fwd_pe and stock_fwd_pe > 0:
            pe_spread = stock_fwd_pe - sector_bench
            pe_discount_pct = -((pe_spread / sector_bench) * 100.0)
            fwd_pe_val = round(stock_fwd_pe, 1)
        else:
            pe_spread = None
            pe_discount_pct = -999.0
            fwd_pe_val = None

        price_val = info.get("currentPrice") or info.get("regularMarketPrice")
        fifty_low = ana.get("fifty_two_low")
        fifty_high = ana.get("fifty_two_high")

        fund = compute_fundamentals(info, sec_name, pe_discount_pct if pe_discount_pct > -900 else None, peg_val, lev)
        comp_core_plus = calculate_composite_plus(comp_core, fund["fundamentals"], len(fund["flags"]))
        comp_emerging_plus = calculate_composite_plus(comp_emerging, fund["fundamentals"], len(fund["flags"]))

        trend_rank_num = soc["trending_rank"]
        trend_score_val = soc["trend_score"]

        return {
            "Ticker": sym,
            "Company": c_name,
            "Sector": sec_name,
            "Industry": ind_name,
            "Price": f"${price_val:.2f}" if price_val else "N/A",
            "Price_Num": price_val if price_val else 0.0,
            "Target_Num": ana["target_mean"] if ana["target_mean"] else 0.0,
            "Market Cap": mkt_cap_str,
            "Market Cap Num": float(mkt_cap_num) if mkt_cap_num else 0.0,
            "Net Debt/EBITDA": lev["net_debt_ebitda"],
            "Debt/Equity": lev["debt_equity"],
            "Forward PE": fwd_pe_val,
            "PEG": peg_val,
            "Beta": beta_val,
            "Sector PE": sector_bench,
            "PE Discount %": round(pe_discount_pct, 1) if pe_discount_pct > -900 else None,
            "52W Low": f"${fifty_low:.2f}" if fifty_low else "N/A",
            "52W High": f"${fifty_high:.2f}" if fifty_high else "N/A",
            "Composite Index": comp_growth,
            "Composite Core": comp_core,
            "Composite Emerging": comp_emerging,
            "Composite Growth": comp_growth,
            "Composite+ Core": comp_core_plus,
            "Composite+ Emerging": comp_emerging_plus,
            "Fundamentals": fund["fundamentals"],
            "Fundamentals Coverage %": fund["coverage_pct"],
            "Red Flags": ", ".join(fund["flags"]) if fund["flags"] else "—",
            "Red Flag Count": len(fund["flags"]),
            "Fwd PEG": fund["fwd_peg"],
            "FCF Yield %": fund["fcf_yield"],
            "Revenue Growth %": fund["rev_growth"],
            "Analysts": analyst_count_val,
            "Coverage Status": ana.get("coverage_status", "N/A"),
            "Social Score": round(social_val, 1) if social_val is not None else None,
            "Social Source": soc.get("source", "live"),
            "Social Age (h)": soc.get("age_h"),
            "Analyst Buy %": round(analyst_val, 1),
            "Raw Buy %": ana["raw_buy_pct"],
            "Consensus Rating": ana.get("consensus", "N/A"),
            "Strong Buy": ana.get("strong_buy", 0),
            "Buy": ana.get("buy", 0),
            "Hold": ana.get("hold", 0),
            "Sell": ana.get("sell", 0),
            "Strong Sell": ana.get("strong_sell", 0),
            "Upside": round(upside_val, 1) if upside_val is not None else 0.0,
            "Upside Score": round(upside_score_val, 1) if upside_score_val is not None else 50.0,
            "Upside Status": ana.get("upside_status", "Standard"),
            "Polarity %": round(polarity_val, 1),
            "Sentiment": soc.get("sentiment_score") if soc.get("source") != "none" else None,
            "Buzz": soc.get("buzz_score"),
            "Msgs/Hr": soc.get("msgs_per_hour") if soc.get("source") != "none" else None,
            "Reddit Mentions": soc.get("reddit_mentions"),
            "Reddit 24h Change": ((soc["reddit_mentions"] - soc["reddit_mentions_24h_ago"])
                                  if soc.get("reddit_mentions") is not None else None),
            "Watchers": soc.get("watchlist_count") or None,
            "Trend Score": round(trend_score_val, 1),
            "Trend Rank": f"#{trend_rank_num}" if trend_rank_num else "-",
            "Trending Status": f"🔥 #{trend_rank_num}" if trend_rank_num else "Active Stream",
            "Mentions": mentions_cnt,
            "Upvotes": upvotes_cnt,
            "Bullish Mentions": bull_cnt,
            "Bearish Mentions": bear_cnt,
            "View": f"?ticker={sym}",
        }
    except Exception:
        return None




# Dedicated Beta Scan Function: Targeted pools for true Low Beta vs High Beta


SESSION_STORE_TTL_SECONDS = 3600


def _session_store_get(key):
    entry = st.session_state["store"].get(key)
    if entry is None:
        return None
    if not isinstance(entry, tuple) or len(entry) != 2:
        st.session_state["store"].pop(key, None)
        return None
    stored_at, df = entry
    if (datetime.datetime.now(datetime.timezone.utc) - stored_at).total_seconds() > SESSION_STORE_TTL_SECONDS:
        st.session_state["store"].pop(key, None)
        return None
    return df


def _session_store_put(key, df):
    st.session_state["store"][key] = (datetime.datetime.now(datetime.timezone.utc), df)




# Dedicated Multi-Asset Historical Download
def fetch_single_ticker_closes(sym, period_str):
    try:
        t = yf.Ticker(sym)
        hist = t.history(period=period_str, interval="1d")
        if hist is not None and not hist.empty:
            s = hist["Close"].copy()
            s.name = sym
            if s.index.tz is not None:
                s.index = s.index.tz_convert(None)
            return sym, s
    except Exception:
        pass
    return sym, None


@st.cache_data(ttl=3600)
def fetch_portfolio_history_data(tickers_tuple, benchmark="^GSPC", period_str="1y"):
    all_syms = list(tickers_tuple) + [benchmark]
    series_dict = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=YAHOO_MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_single_ticker_closes, s, period_str): s for s in all_syms}
        for f in concurrent.futures.as_completed(futures):
            sym, s = f.result()
            if s is not None and len(s) > 5:
                series_dict[sym] = s

    if benchmark not in series_dict:
        _, spy_s = fetch_single_ticker_closes("SPY", period_str)
        if spy_s is not None and len(spy_s) > 5:
            series_dict[benchmark] = spy_s

    if not series_dict or benchmark not in series_dict:
        return pd.DataFrame()

    df = pd.DataFrame(series_dict)
    return df


# Persistent Fetch Wrappers






def show_sector_shortfall(df, sector_name, requested=None, what="stocks"):
    requested = scan_depth if requested is None else requested
    if sector_name and sector_name != "All Sectors" and df is not None and 0 < len(df) < requested:
        st.caption(
            f"ℹ️ **{len(df)} {sector_name} {what}** available, fewer than the {requested} requested: "
            "that's every stock in the app's list for this sector that Yahoo also classifies there."
        )




def broad_scan_order():
    pools = [sector_candidate_pool(sec) for sec in SECTOR_UNIVERSES]
    interleaved = [t for group in itertools.zip_longest(*pools) for t in group if t]
    return list(dict.fromkeys(interleaved + BROAD_EXCHANGE_UNIVERSE))


def sector_selector(key):
    return st.selectbox("Select Sector Category:", options=SECTOR_OPTIONS, index=0, key=key)


# ==============================================================================
# PERSISTENT DATA: SCORE HISTORY (SMOOTHING) + TRACK RECORD
# ==============================================================================
# Files live in $SENTIMENT_DATA_DIR, else /app/data (Docker), else ./data next to this script.
def _resolve_data_dir():
    here = os.path.dirname(os.path.abspath(globals().get("__file__") or os.getcwd()))
    candidates = [os.environ.get("SENTIMENT_DATA_DIR"), "/app/data", os.path.join(here, "data")]
    for path in [c for c in candidates if c]:
        try:
            os.makedirs(path, exist_ok=True)
            if os.access(path, os.W_OK):
                return path
        except OSError:
            continue
    return None


DATA_DIR = _resolve_data_dir()
SCORE_HISTORY_FILE = os.path.join(DATA_DIR, "score_history.json") if DATA_DIR else None
TRACK_RECORD_FILE = os.path.join(DATA_DIR, "track_record.json") if DATA_DIR else None
SOCIAL_CACHE_FILE = os.path.join(DATA_DIR, "social_cache.json") if DATA_DIR else None
REDDIT_CACHE_FILE = os.path.join(DATA_DIR, "reddit_cache.json") if DATA_DIR else None
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json") if DATA_DIR else None
DAILY_STATUS_FILE = os.path.join(DATA_DIR, "daily_scan_status.json") if DATA_DIR else None

# Smoothing model
SMOOTH_HALF_LIFE_HOURS = 72.0      # an observation loses half its weight every 3 days
SMOOTH_MIN_INTERVAL_HOURS = 4.0    # at most one observation per stock every 4 hours (refresh clicks don't count)
SMOOTH_PRIOR_WEIGHT = 2.0          # a new stock is pulled toward the median as if it had 2 median observations
SMOOTH_FORGET_DAYS = 14            # stocks not seen for 2 weeks are deleted
SMOOTH_SCORE_COLUMNS = {"core": "Composite Core", "emerging": "Composite Emerging",
                        "core_plus": "Composite+ Core", "emerging_plus": "Composite+ Emerging"}

# Track record
TRACK_TOP_N = 20
TRACK_LISTS = {
    "inst_smooth": "🏛️ Large-Cap / Institutional (Classic)",
    "emer_smooth": "🚀 Emerging Growth (Classic)",
    "inst_plus_smooth": "💎 Large-Cap / Institutional (Composite+)",
    "emer_plus_smooth": "💎 Emerging Growth (Composite+)",
    "mid_smooth": "🏢 Mid-Cap (Classic)",
    "small_smooth": "🔬 Small-Cap (Classic)",
    "mid_plus_smooth": "💎 Mid-Cap (Composite+)",
    "small_plus_smooth": "💎 Small-Cap (Composite+)",
}

try:
    from zoneinfo import ZoneInfo
    MARKET_TZ = ZoneInfo("America/New_York")
except Exception:
    MARKET_TZ = datetime.timezone(datetime.timedelta(hours=-5))


@st.cache_resource
def _data_file_lock():
    # Shared by every browser session; Streamlit reruns the script, so a plain module-level lock would not be shared
    return threading.RLock()


def _read_json_file(path, default):
    try:
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, type(default)):
                return data
    except (OSError, ValueError):
        pass
    return default


def _write_json_file(path, data):
    # Atomic: write a temp file, then rename over the old one, so a crash can't leave half a file
    if not path:
        return False
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError):
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False


def _market_date(ts):
    return datetime.datetime.fromtimestamp(ts, MARKET_TZ).strftime("%Y-%m-%d")


def update_score_history(hist, rows, now_ts):
    """Folds one scan into the history. Each stock keeps a time-decayed average of its scores:
    every observation counts once, and older observations fade with a 3-day half-life."""
    changed = False
    today = _market_date(now_ts)
    for row in rows:
        ticker = row.get("Ticker")
        scores = {k: _clean_number(row.get(col)) for k, col in SMOOTH_SCORE_COLUMNS.items()}
        if not ticker or any(v is None for v in scores.values()):
            continue
        rec = hist.get(ticker)
        if not isinstance(rec, dict) or "avg" not in rec:
            hist[ticker] = {"avg": scores, "n": 1.0, "obs": 1, "first_ts": now_ts, "last_ts": now_ts, "days": [today]}
            changed = True
            continue
        hours = (now_ts - rec.get("last_ts", now_ts)) / 3600.0
        if hours < SMOOTH_MIN_INTERVAL_HOURS:
            continue
        decay = 0.5 ** (hours / SMOOTH_HALF_LIFE_HOURS)
        old_n = float(rec.get("n", 1.0)) * decay
        new_n = old_n + 1.0
        rec["avg"] = {k: (old_n * rec["avg"].get(k, v) + v) / new_n for k, v in scores.items()}
        rec["n"] = new_n
        rec["obs"] = int(rec.get("obs", 0)) + 1
        rec["last_ts"] = now_ts
        days = rec.setdefault("days", [])
        if not days or days[-1] != today:
            days.append(today)
            del days[:-90]
        changed = True

    cutoff = now_ts - SMOOTH_FORGET_DAYS * 86400
    for ticker in [t for t, r in hist.items() if not isinstance(r, dict) or r.get("last_ts", 0) < cutoff]:
        del hist[ticker]
        changed = True
    return hist, changed


def record_score_observations(df, write=True):
    """Adds the current scan to score_history.json (skipping stocks observed in the last 4 hours).
    write=False only reads the history (used when the data came from the background snapshot, which already recorded it)."""
    if df is None or df.empty or not SCORE_HISTORY_FILE:
        return {}
    if not write:
        return _read_json_file(SCORE_HISTORY_FILE, {})
    with _data_file_lock():
        hist = _read_json_file(SCORE_HISTORY_FILE, {})
        hist, changed = update_score_history(hist, df.to_dict("records"), time.time())
        if changed:
            _write_json_file(SCORE_HISTORY_FILE, hist)
    return hist


# ==============================================================================
# METRIC HISTORY: every Deep Dive metric over time (Stock History dropdown)
#   - Yahoo history wherever it exists: daily prices, financial statements, analyst rating changes, estimate revisions
#   - plus a daily snapshot the app records itself (daily scan + Deep Dive visits) for the metrics Yahoo keeps
#     no history of (Composite Index, Social Media Index, PEG, short interest...)
# ==============================================================================
METRIC_DB_FILE = os.path.join(DATA_DIR, "metric_history.db") if DATA_DIR else None
METRIC_DB_COLUMNS = (
    "price", "market_cap", "fwd_pe", "peg", "fwd_peg", "beta", "ev_ebitda", "fcf_yield", "rev_growth",
    "short_pct", "nd_ebitda", "debt_equity", "target",
)


def _metric_db_connect():
    con = sqlite3.connect(METRIC_DB_FILE, timeout=20)
    cols = ", ".join(f"{c} REAL" for c in METRIC_DB_COLUMNS)
    con.execute(f"CREATE TABLE IF NOT EXISTS metric_daily (ticker TEXT NOT NULL, day TEXT NOT NULL, {cols}, "
                "PRIMARY KEY (ticker, day))")
    have = {row[1] for row in con.execute("PRAGMA table_info(metric_daily)")}
    for c in METRIC_DB_COLUMNS:
        if c not in have:
            con.execute(f"ALTER TABLE metric_daily ADD COLUMN {c} REAL")
    return con


def record_metric_snapshots(records):
    """Saves today's reading of each metric per stock. A later reading on the same day replaces an earlier one;
    a metric missing from the later reading keeps the earlier value."""
    if not METRIC_DB_FILE or not records:
        return 0
    day = _market_date(time.time())
    rows = []
    for rec in records:
        ticker = rec.get("ticker")
        vals = [_clean_number(rec.get(c)) for c in METRIC_DB_COLUMNS]
        if ticker and any(v is not None for v in vals):
            rows.append([str(ticker), day] + vals)
    if not rows:
        return 0
    cols = ", ".join(METRIC_DB_COLUMNS)
    marks = ", ".join("?" * (len(METRIC_DB_COLUMNS) + 2))
    updates = ", ".join(f"{c} = COALESCE(excluded.{c}, metric_daily.{c})" for c in METRIC_DB_COLUMNS)
    sql = (f"INSERT INTO metric_daily (ticker, day, {cols}) VALUES ({marks}) "
           f"ON CONFLICT(ticker, day) DO UPDATE SET {updates}")
    try:
        with _data_file_lock():
            con = _metric_db_connect()
            try:
                with con:
                    con.executemany(sql, rows)
            finally:
                con.close()
        return len(rows)
    except (sqlite3.Error, OSError):
        return 0


def load_metric_snapshots(ticker):
    """The app's own daily readings for one stock, indexed by date."""
    if not METRIC_DB_FILE or not os.path.exists(METRIC_DB_FILE):
        return pd.DataFrame()
    try:
        con = sqlite3.connect(METRIC_DB_FILE, timeout=20)
        try:
            df = pd.read_sql_query("SELECT * FROM metric_daily WHERE ticker = ? ORDER BY day", con, params=(ticker,))
        finally:
            con.close()
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return df
    df.index = pd.to_datetime(df.pop("day"), errors="coerce")
    df = df[df.index.notna()].drop(columns=["ticker"], errors="ignore")
    return df.apply(pd.to_numeric, errors="coerce")


@st.cache_resource
def _recorded_scan_fingerprints():
    return set()


def record_scan_metric_snapshots(df):
    """Saves the scan's readings for every stock in it (once per distinct scan per day)."""
    if df is None or getattr(df, "empty", True) or not METRIC_DB_FILE:
        return 0

    def col_sum(name):
        return round(float(pd.to_numeric(df[name], errors="coerce").fillna(0).sum()), 4) if name in df.columns else 0.0

    fingerprint = (_market_date(time.time()), len(df), col_sum("Price_Num"), col_sum("Market Cap Num"))
    seen = _recorded_scan_fingerprints()
    if fingerprint in seen:
        return 0
    save_peer_market_caps(df)
    records = []
    for r in df.to_dict("records"):
        price = _clean_number(r.get("Price_Num")) or None
        target = _clean_number(r.get("Target_Num")) or None
        fpe = _clean_number(r.get("Forward PE"))
        records.append({
            "ticker": r.get("Ticker"), "price": price, "market_cap": _clean_number(r.get("Market Cap Num")) or None,
            "fwd_pe": fpe if (fpe is not None and fpe > 0) else None,
            "peg": r.get("PEG"), "fwd_peg": r.get("Fwd PEG"), "beta": r.get("Beta"),
            "fcf_yield": r.get("FCF Yield %"), "rev_growth": r.get("Revenue Growth %"),
            "nd_ebitda": r.get("Net Debt/EBITDA"), "debt_equity": r.get("Debt/Equity"),
            "target": target,
        })
    saved = record_metric_snapshots(records)
    if saved:
        seen.add(fingerprint)
    return saved


def deep_dive_metric_record(ticker, info, analyst_info):
    """Today's reading of every Deep Dive metric for one stock, in the same units the page shows."""
    info = info or {}
    lev = compute_leverage_metrics(info)
    price = _clean_number(info.get("currentPrice")) or _clean_number(info.get("regularMarketPrice"))
    fpe = _clean_number(info.get("forwardPE"))
    fwd_eps, trl_eps = _clean_number(info.get("forwardEps")), _clean_number(info.get("trailingEps"))
    fwd_peg = None
    if fpe and fpe > 0 and fwd_eps is not None and trl_eps and trl_eps > 0:
        growth = (fwd_eps - trl_eps) / trl_eps * 100.0
        fwd_peg = fpe / growth if growth > 0 else None
    fcf, mcap = _clean_number(info.get("freeCashflow")), _clean_number(info.get("marketCap"))
    rev_g = _clean_number(info.get("revenueGrowth"))
    short = _clean_number(info.get("shortPercentOfFloat"))
    ev = _clean_number(info.get("enterpriseToEbitda"))
    peg = _clean_number(info.get("pegRatio")) or _clean_number(info.get("trailingPegRatio"))
    analyst_info = analyst_info or {}
    return {
        "ticker": ticker, "price": price, "market_cap": mcap,
        "fwd_pe": fpe if (fpe and fpe > 0) else None, "peg": peg if (peg and peg > 0) else None, "fwd_peg": fwd_peg,
        "beta": _clean_number(info.get("beta")), "ev_ebitda": ev if (ev and ev > 0) else None,
        "fcf_yield": (fcf / mcap * 100.0) if (fcf is not None and mcap) else None,
        "rev_growth": rev_g * 100.0 if rev_g is not None else None,
        "short_pct": short * 100.0 if short is not None else None,
        "nd_ebitda": lev["net_debt_ebitda"], "debt_equity": lev["debt_equity"],
        "target": analyst_info.get("target_mean") or None,
    }


# --- Yahoo history (fetched only when a metric needs it, then cached) ---
def _naive_index(idx):
    idx = pd.DatetimeIndex(pd.to_datetime(idx, errors="coerce"))
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx


try:
    pd.date_range("2020-01-31", periods=1, freq="ME")
    _MONTH_END = "ME"
except ValueError:          # pandas before 2.2
    _MONTH_END = "M"


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_daily_history(symbol):
    """Every trading day Yahoo has: split-adjusted prices, volume, dividends and splits."""
    try:
        h = yf.Ticker(symbol).history(period="max", interval="1d", auto_adjust=False, actions=True)
    except Exception:
        return pd.DataFrame()
    if h is None or getattr(h, "empty", True) or "Close" not in h.columns:
        return pd.DataFrame()
    h = h.copy()
    h.index = _naive_index(h.index).normalize()
    keep = [c for c in ("High", "Low", "Close", "Volume", "Dividends", "Stock Splits") if c in h.columns]
    h = h[keep].apply(pd.to_numeric, errors="coerce")
    h = h[h.index.notna() & h["Close"].notna() & (h["Close"] > 0)]
    h = h[~h.index.duplicated(keep="last")]
    return h.sort_index()


STATEMENT_ATTRS = {"inc_a": "income_stmt", "inc_q": "quarterly_income_stmt", "bal_a": "balance_sheet",
                   "bal_q": "quarterly_balance_sheet", "cf_a": "cashflow", "cf_q": "quarterly_cashflow"}


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def fetch_financial_history(symbol):
    """Annual + quarterly income statement, balance sheet and cash flow, plus Yahoo's share-count history."""
    t = yf.Ticker(symbol)
    out = {}
    for key, attr in STATEMENT_ATTRS.items():
        try:
            df = getattr(t, attr)
            out[key] = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
        except Exception:
            out[key] = pd.DataFrame()
    shares = pd.Series(dtype=float)
    try:
        s = t.get_shares_full(start="1985-01-01")
        if isinstance(s, pd.Series) and not s.empty:
            s = pd.to_numeric(s, errors="coerce")
            s.index = _naive_index(s.index).normalize()
            s = s[s.index.notna()].dropna()
            shares = s[s > 0].groupby(level=0).last().sort_index().astype(float)
    except Exception:
        pass
    out["shares"] = shares
    return out


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_analyst_history(symbol):
    """Monthly rating tallies, every firm's rating/price-target changes, estimate revisions and earnings surprises."""
    t = yf.Ticker(symbol)
    getters = {
        "recs": lambda: t.recommendations,
        "grades": lambda: t.upgrades_downgrades,
        "eps_trend": lambda: t.eps_trend,
        "earnings": lambda: t.get_earnings_dates(limit=60),
    }
    out = {}
    for key, getter in getters.items():
        try:
            v = getter()
            out[key] = v.copy() if isinstance(v, pd.DataFrame) else pd.DataFrame()
        except Exception:
            out[key] = pd.DataFrame()
    return out


# Yahoo statement row names (first match wins)
ROWS_REVENUE = ("Total Revenue", "Operating Revenue")
ROWS_NET_INCOME = ("Net Income Common Stockholders", "Net Income",
                   "Net Income From Continuing Operation Net Minority Interest")
ROWS_EBITDA = ("EBITDA", "Normalized EBITDA")
ROWS_GROSS = ("Gross Profit",)
ROWS_OPERATING = ("Operating Income", "Total Operating Income As Reported")
ROWS_FCF = ("Free Cash Flow",)
ROWS_DEBT = ("Total Debt",)
ROWS_CASH = ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents")
ROWS_EQUITY = ("Stockholders Equity", "Common Stock Equity")
ROWS_SHARES = ("Ordinary Shares Number", "Share Issued")


def _statement_row(df, names):
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.Series(dtype=float)
    for name in names:
        if name in df.index:
            row = df.loc[name]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            s = pd.to_numeric(row, errors="coerce")
            s.index = _naive_index(s.index)
            s = s[s.index.notna()].dropna()
            if not s.empty:
                return s.groupby(level=0).last().sort_index().astype(float)
    return pd.Series(dtype=float)


def _reported_series(fin, prefix, names, flow=True):
    """Annual values back to the oldest year Yahoo has, then quarterly ones, dated at each period end.
    Flows (revenue, earnings, cash flow) use the trailing 4 quarters so they line up with the annual totals;
    balances (debt, cash, equity) use the quarter-end value."""
    annual = _statement_row(fin.get(prefix + "_a"), names)
    quarterly = _statement_row(fin.get(prefix + "_q"), names)
    if flow and not quarterly.empty:
        ttm = {}
        for i in range(3, len(quarterly)):
            window = quarterly.iloc[i - 3:i + 1]
            if (window.index[-1] - window.index[0]).days <= 300:     # 4 consecutive quarters
                ttm[window.index[-1]] = float(window.sum())
        quarterly = pd.Series(ttm, dtype=float)
    if quarterly.empty:
        return annual
    if annual.empty:
        return quarterly
    older = annual[annual.index < quarterly.index.min() - pd.Timedelta(days=45)]
    return pd.concat([older, quarterly]).sort_index()


def _asof(series, dates, max_age_days=None):
    """The value in force on each date (latest reading on or before it); blank before the first reading
    or once the latest reading is older than max_age_days."""
    dates = pd.DatetimeIndex(dates)
    vals = np.full(len(dates), np.nan)
    if series is None or series.empty or len(dates) == 0:
        return pd.Series(vals, index=dates)
    s = series.dropna().sort_index()
    if s.empty:
        return pd.Series(vals, index=dates)
    pos = s.index.searchsorted(dates, side="right") - 1
    ok = pos >= 0
    vals[ok] = s.values[pos[ok]]
    if max_age_days is not None and ok.any():
        age = np.asarray((dates[ok] - s.index[pos[ok]]).days)
        sub = vals[ok]
        sub[age > max_age_days] = np.nan
        vals[ok] = sub
    return pd.Series(vals, index=dates)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def share_count_history(symbol, shares_now):
    """Shares outstanding over time on today's share basis (older counts scaled up/down for later splits).
    Returns (series, source label)."""
    fin = fetch_financial_history(symbol)
    daily = fetch_daily_history(symbol)
    shares, source = fin.get("shares", pd.Series(dtype=float)), "Yahoo share-count history"
    if shares is None or shares.empty:
        shares, source = _reported_series(fin, "bal", ROWS_SHARES, flow=False), "quarterly/annual balance sheets"
    if shares is None or shares.empty:
        if shares_now:
            return pd.Series({pd.Timestamp.today().normalize(): float(shares_now)}), "today's share count only"
        return pd.Series(dtype=float), ""
    shares = shares.astype(float).copy()
    if not daily.empty and "Stock Splits" in daily.columns:
        splits = daily["Stock Splits"]
        splits = splits[(splits > 0) & (splits != 1)]
        for when, ratio in splits.items():
            before, after = shares[shares.index < when], shares[shares.index >= when]
            if before.empty:
                continue
            b = before.iloc[-1]
            a = after.iloc[0] if not after.empty else _clean_number(shares_now)
            if not a or not b:
                continue
            jump = np.log(a / b)
            if abs(jump - np.log(ratio)) < abs(jump):      # counts before the split were not restated
                shares[shares.index < when] = shares[shares.index < when] * ratio
    med = shares.rolling(9, center=True, min_periods=1).median()
    shares = shares[(shares / med - 1).abs() < 0.35]      # drop Yahoo's occasional bad readings
    return shares, source


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def market_cap_history(symbol, shares_now):
    daily = fetch_daily_history(symbol)
    if daily.empty:
        return pd.Series(dtype=float), ""
    shares, source = share_count_history(symbol, shares_now)
    if shares.empty:
        return pd.Series(dtype=float), ""
    if source == "today's share count only":
        sh = pd.Series(float(shares.iloc[-1]), index=daily.index)
    else:
        sh = _asof(shares, daily.index, max_age_days=550)
        sh = sh.bfill(limit=70)          # the first few weeks before the earliest count
    return (daily["Close"] * sh).dropna(), source


GRADE_BUY = {"buy", "strong buy", "outperform", "overweight", "positive", "accumulate", "market outperform",
             "sector outperform", "add", "conviction buy", "top pick", "long-term buy", "speculative buy", "moderate buy"}
GRADE_HOLD = {"hold", "neutral", "equal-weight", "equal weight", "market perform", "sector perform", "peer perform",
              "in-line", "in line", "sector weight", "perform", "mixed", "fair value", "market weight", "moderate hold"}
GRADE_SELL = {"sell", "strong sell", "underperform", "underweight", "reduce", "negative", "market underperform",
              "sector underperform", "moderate sell"}


def _grade_class(text):
    g = str(text or "").strip().lower()
    return "buy" if g in GRADE_BUY else "hold" if g in GRADE_HOLD else "sell" if g in GRADE_SELL else None


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def analyst_firm_history(symbol):
    """Rebuilds the analyst consensus week by week from every firm's rating and price-target changes:
    each week counts each firm's latest call from the previous 12 months."""
    grades = fetch_analyst_history(symbol).get("grades", pd.DataFrame())
    if grades is None or grades.empty or "Firm" not in grades.columns:
        return pd.DataFrame()
    g = grades.copy()
    g.index = _naive_index(g.index)
    g = g[g.index.notna()].sort_index()
    g["cls"] = g["ToGrade"].map(_grade_class) if "ToGrade" in g.columns else None
    g["pt"] = pd.to_numeric(g["currentPriceTarget"], errors="coerce") if "currentPriceTarget" in g.columns else np.nan
    g.loc[~(g["pt"] > 0), "pt"] = np.nan
    if g.empty:
        return pd.DataFrame()
    weeks = pd.date_range(g.index.min() + pd.Timedelta(days=30), pd.Timestamp.today().normalize(), freq="W-FRI")
    rows = []
    for d in weeks:
        win = g[(g.index <= d) & (g.index > d - pd.Timedelta(days=365))]
        if win.empty:
            continue
        latest = win.groupby("Firm").tail(1)
        counts = latest["cls"].value_counts()
        rated = int(counts.sum())
        targets = win.dropna(subset=["pt"]).groupby("Firm")["pt"].last()
        rows.append({
            "date": d,
            "buy_pct": counts.get("buy", 0) / rated * 100.0 if rated >= 3 else np.nan,
            "buy": counts.get("buy", 0), "hold": counts.get("hold", 0), "sell": counts.get("sell", 0),
            "target": float(targets.mean()) if len(targets) >= 2 else np.nan, "target_firms": len(targets),
        })
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def _eps_trend_points(analyst, period="+1y"):
    et = analyst.get("eps_trend", pd.DataFrame())
    if et is None or et.empty or period not in et.index:
        return pd.Series(dtype=float)
    today = pd.Timestamp.today().normalize()
    pts = {}
    for col, days in (("90daysAgo", 90), ("60daysAgo", 60), ("30daysAgo", 30), ("7daysAgo", 7), ("current", 0)):
        if col in et.columns:
            v = _clean_number(et.loc[period, col])
            if v is not None:
                pts[today - pd.Timedelta(days=days)] = v
    return pd.Series(pts, dtype=float).sort_index()


# --- Metric catalogue: (key, group, label, unit, chart kind, source description) ---
HISTORY_METRICS = [
    ("price", "Price & Trading", "Stock Price", "usd", "price", "Yahoo Finance price history."),
    ("volume", "Price & Trading", "Trading Volume", "num", "bar", "Yahoo Finance price history: shares traded per bar."),
    ("range52", "Price & Trading", "52-Week Range", "usd", "band",
     "Daily prices; the band is the highest high and lowest low of the trailing 52 weeks."),
    ("vs_spx", "Price & Trading", "Performance vs S&P 500", "pct", "rebased",
     "Daily closes of the stock and the S&P 500, both rebased to 0% at the start of the timeframe."),
    ("beta", "Price & Trading", "Beta", "ratio", "line",
     "Rolling 5-year beta from monthly returns vs the S&P 500 (the same method Yahoo uses)."),
    ("div_yield", "Price & Trading", "Dividend Yield", "pct", "line",
     "Dividends paid over the trailing 12 months ÷ share price."),
    ("market_cap", "Valuation", "Market Cap", "usd_big", "line", "Daily share price × shares outstanding."),
    ("trailing_pe", "Valuation", "Trailing P/E", "x", "line",
     "Market cap ÷ trailing-12-month net income (= price ÷ EPS), from the financial statements."),
    ("fwd_pe", "Valuation", "Forward P/E", "x", "line",
     "Price ÷ consensus EPS estimate for next fiscal year. Yahoo keeps 90 days of estimate history; "
     "daily readings saved from now on extend it."),
    ("peg", "Valuation", "PEG Ratio", "x", "line", "Yahoo's PEG ratio. Yahoo keeps no history of it, so a reading is saved each day from now on."),
    ("fwd_peg", "Valuation", "Forward PEG", "x", "line",
     "Forward P/E ÷ expected EPS growth, from 90 days of estimate history plus daily readings saved from now on."),
    ("ev_ebitda", "Valuation", "EV / EBITDA", "x", "line",
     "(Market cap + debt − cash) ÷ trailing-12-month EBITDA, from the financial statements."),
    ("ps", "Valuation", "Price / Sales", "x", "line", "Market cap ÷ trailing-12-month revenue."),
    ("fcf_yield", "Valuation", "Free Cash Flow Yield", "pct", "line", "Trailing-12-month free cash flow ÷ market cap."),
    ("nd_ebitda", "Balance Sheet", "Net Debt / EBITDA", "x", "step",
     "(Total debt − cash & short-term investments) ÷ trailing-12-month EBITDA, at each reporting date."),
    ("debt_equity", "Balance Sheet", "Debt / Equity", "x", "step", "Total debt ÷ shareholders' equity, at each reporting date."),
    ("revenue", "Growth & Profitability", "Revenue", "usd_big", "step", "Trailing-12-month revenue at each reporting date."),
    ("rev_growth", "Growth & Profitability", "Revenue Growth", "pct", "bar",
     "Year-over-year revenue growth: each quarter vs the same quarter a year earlier (annual before that)."),
    ("eps", "Growth & Profitability", "EPS", "usd", "step",
     "Trailing-12-month net income ÷ shares outstanding, on today's share basis (adjusted for splits)."),
    ("margins", "Growth & Profitability", "Profit Margins", "pct", "step",
     "Gross, operating and net margin on trailing-12-month figures."),
    ("fcf", "Growth & Profitability", "Free Cash Flow", "usd_big", "step",
     "Operating cash flow minus capital spending over the trailing 12 months."),
    ("earnings", "Growth & Profitability", "Earnings vs Estimates", "usd", "markers",
     "Reported EPS vs the consensus estimate for each quarterly report."),
    ("target", "Analysts", "Analyst Price Target", "usd", "line",
     "Average of each firm's latest price target from the previous 12 months, rebuilt from Yahoo's record of "
     "analyst actions, with the share price for comparison."),
    ("ratings_mix", "Analysts", "Analyst Ratings", "num", "stacked",
     "Strong Buy → Strong Sell counts. Yahoo keeps the last 4 months."),
    ("eps_revisions", "Analysts", "EPS Estimate Revisions", "usd", "line",
     "How the consensus EPS estimates changed over the last 90 days (Yahoo keeps 90 days)."),
    ("short", "Analysts", "Short Interest", "pct", "line",
     "Shares sold short ÷ float: Yahoo's current and prior-month reports, plus daily readings saved from now on."),
]
HISTORY_METRIC_INFO = {m[0]: {"group": m[1], "label": m[2], "unit": m[3], "kind": m[4], "source": m[5]}
                       for m in HISTORY_METRICS}
RECORDED_ONLY_METRICS = {"peg"}
RECORDED_NOTE = ("Yahoo keeps no history of this, so a reading is saved every day from now on (the 3:30 PM daily scan "
                 "and each visit to this page) and the chart fills in over the coming days.")


def _ttm_ratio(numer, denom, positive_denom=True):
    df = pd.concat([numer, denom], axis=1, keys=["n", "d"]).dropna()
    if positive_denom:
        df = df[df["d"] > 0]
    return (df["n"] / df["d"]).astype(float)


def _yoy_growth(fin, names):
    """Year-over-year growth: each quarter vs the same quarter a year earlier, annual figures before that."""
    out = {}
    a = _statement_row(fin.get("inc_a"), names)
    for i in range(1, len(a)):
        if a.iloc[i - 1] > 0 and 300 <= (a.index[i] - a.index[i - 1]).days <= 430:
            out[a.index[i]] = (a.iloc[i] / a.iloc[i - 1] - 1) * 100.0
    q = _statement_row(fin.get("inc_q"), names)
    qd = {}
    for i in range(len(q)):
        prior = q[(q.index >= q.index[i] - pd.Timedelta(days=395)) & (q.index <= q.index[i] - pd.Timedelta(days=335))]
        if not prior.empty and prior.iloc[-1] > 0:
            qd[q.index[i]] = (q.iloc[i] / prior.iloc[-1] - 1) * 100.0
    if qd:
        first_q = min(qd)
        out = {d: v for d, v in out.items() if d < first_q - pd.Timedelta(days=45)}
        out.update(qd)
    return pd.Series(out, dtype=float).sort_index()


def compute_metric_history(symbol, key, info, sector_benchmark_pe):
    """Returns {"series": {name: Series}, "styles": {name: {...}}, "refs": [(y, label)], "note": str, "empty": str}."""
    info = info or {}
    res = {"series": {}, "styles": {}, "refs": [], "note": "", "empty": ""}
    recorded = load_metric_snapshots(symbol)

    def add(name, s, **style):
        if s is not None and not s.dropna().empty:
            res["series"][name] = s.dropna().astype(float).sort_index()
            res["styles"][name] = style

    def add_recorded(col, name="Daily readings", scale=1.0):
        if not recorded.empty and col in recorded.columns:
            add(name, recorded[col] * scale, dash="dot", markers=True)

    shares_now = _clean_number(info.get("sharesOutstanding")) or _clean_number(info.get("impliedSharesOutstanding"))
    daily = fetch_daily_history(symbol) if key in (
        "range52", "vs_spx", "beta", "div_yield", "fwd_pe", "fwd_peg", "target") else pd.DataFrame()

    if key == "range52":
        if not daily.empty:
            hi = (daily["High"] if "High" in daily else daily["Close"]).rolling(252, min_periods=200).max()
            lo = (daily["Low"] if "Low" in daily else daily["Close"]).rolling(252, min_periods=200).min()
            add("52-Week High", hi, color="rgba(0, 255, 170, 0.55)")
            add("52-Week Low", lo, color="rgba(255, 74, 74, 0.55)")
            add("Close", daily["Close"], color="#FFFFFF")
    elif key == "vs_spx":
        spx = fetch_daily_history("^GSPC")
        if not daily.empty:
            add(symbol, daily["Close"], color="#00FFAA")
        if not spx.empty:
            add("S&P 500", spx["Close"], color="#FF9900")
    elif key == "beta":
        spx = fetch_daily_history("^GSPC")
        if not daily.empty and not spx.empty:
            m = daily["Close"].resample(_MONTH_END).last()
            i = spx["Close"].resample(_MONTH_END).last()
            r = pd.concat([m.pct_change(), i.pct_change()], axis=1, keys=["s", "i"]).dropna()
            if len(r) >= 60:
                beta = r["s"].rolling(60).cov(r["i"]) / r["i"].rolling(60).var()
                add("Beta (rolling 60 months)", beta, color="#00FFAA")
        add_recorded("beta")
        res["refs"] = [(1.0, "Market (1.0)")]
    elif key == "div_yield":
        if not daily.empty and "Dividends" in daily.columns and (daily["Dividends"] > 0).any():
            ttm = daily["Dividends"].fillna(0).rolling("365D").sum()
            first = daily.index[daily["Dividends"] > 0][0] + pd.Timedelta(days=365)
            yld = (ttm / daily["Close"] * 100.0)
            add("Dividend Yield (TTM)", yld[yld.index >= first], color="#00FFAA")
        elif not daily.empty:
            res["empty"] = f"{symbol} hasn't paid a dividend in Yahoo's price history."
    elif key in ("market_cap", "trailing_pe", "ev_ebitda", "ps", "fcf_yield"):
        mcap, src = market_cap_history(symbol, shares_now)
        fin = fetch_financial_history(symbol) if key != "market_cap" else {}
        if key == "market_cap":
            add("Market Cap", mcap, color="#00FFAA")
            add_recorded("market_cap")
            if src:
                res["note"] = f"Shares outstanding from {src}."
        elif not mcap.empty:
            dates = mcap.index
            if key == "trailing_pe":
                ni = _asof(_reported_series(fin, "inc", ROWS_NET_INCOME), dates, 550)
                add("Trailing P/E", _ttm_ratio(mcap, ni), color="#00FFAA")
                res["note"] = "Blank while trailing earnings were negative (P/E isn't meaningful)."
            elif key == "ev_ebitda":
                debt = _asof(_reported_series(fin, "bal", ROWS_DEBT, flow=False), dates, 550)
                cash = _asof(_reported_series(fin, "bal", ROWS_CASH, flow=False), dates, 550)
                ebitda = _asof(_reported_series(fin, "inc", ROWS_EBITDA), dates, 550)
                add("EV / EBITDA", _ttm_ratio(mcap + debt.fillna(0) - cash.fillna(0), ebitda), color="#00FFAA")
                add_recorded("ev_ebitda")
                res["refs"] = [(10.0, "10x"), (20.0, "20x")]
            elif key == "ps":
                rev = _asof(_reported_series(fin, "inc", ROWS_REVENUE), dates, 550)
                add("Price / Sales", _ttm_ratio(mcap, rev), color="#00FFAA")
            elif key == "fcf_yield":
                fcf = _asof(_reported_series(fin, "cf", ROWS_FCF), dates, 550)
                add("FCF Yield", _ttm_ratio(fcf, mcap) * 100.0, color="#00FFAA")
                add_recorded("fcf_yield")
                res["refs"] = [(5.0, "5%"), (0.0, "0%")]
        if key == "fcf_yield" and not res["series"]:
            add_recorded("fcf_yield")
        if key == "ev_ebitda" and not res["series"]:
            add_recorded("ev_ebitda")
    elif key in ("fwd_pe", "fwd_peg"):
        analyst = fetch_analyst_history(symbol)
        est = _eps_trend_points(analyst, "+1y")
        fpe = pd.Series(dtype=float)
        if not est.empty and not daily.empty:
            px = _asof(daily["Close"], est.index, 7)
            fpe = _ttm_ratio(px, est)
        rec_fpe = recorded["fwd_pe"].dropna() if (not recorded.empty and "fwd_pe" in recorded.columns) else pd.Series(dtype=float)
        if key == "fwd_pe":
            add("From estimate history (next fiscal year)", fpe, color="#00FFAA", markers=True)
            add("Daily readings", rec_fpe, dash="dot", markers=True)
        else:
            trl = _clean_number(info.get("trailingEps"))
            if trl and trl > 0 and not est.empty:
                growth = (est - trl) / trl * 100.0
                peg = (fpe / growth[growth > 0]).dropna()
                add("From estimate history", peg, color="#00FFAA", markers=True)
            add_recorded("fwd_peg")
            res["refs"] = [(1.0, "1.0x"), (2.0, "2.0x")]
    elif key == "peg":
        add_recorded("peg")
        res["refs"] = [(1.0, "1.0x")]
    elif key in ("nd_ebitda", "debt_equity", "revenue", "rev_growth", "eps", "margins", "fcf"):
        fin = fetch_financial_history(symbol)
        if key == "nd_ebitda":
            debt = _reported_series(fin, "bal", ROWS_DEBT, flow=False)
            cash = _reported_series(fin, "bal", ROWS_CASH, flow=False)
            ebitda = _reported_series(fin, "inc", ROWS_EBITDA)
            dates = debt.index.union(ebitda.index)
            nd = _asof(debt, dates, 120) - _asof(cash, dates, 120).fillna(0)
            add("Net Debt / EBITDA", _ttm_ratio(nd, _asof(ebitda, dates, 120)), color="#00FFAA", markers=True)
            add_recorded("nd_ebitda")
            res["refs"] = [(0.0, "Net cash below 0"), (3.0, "High (3x)")]
        elif key == "debt_equity":
            debt = _reported_series(fin, "bal", ROWS_DEBT, flow=False)
            eq = _reported_series(fin, "bal", ROWS_EQUITY, flow=False)
            add("Debt / Equity", _ttm_ratio(debt, eq), color="#00FFAA", markers=True)
            add_recorded("debt_equity")
            res["refs"] = [(1.5, "High (1.5x)")]
        elif key == "revenue":
            add("Revenue (TTM)", _reported_series(fin, "inc", ROWS_REVENUE), color="#00FFAA", markers=True)
        elif key == "rev_growth":
            add("Revenue Growth (YoY)", _yoy_growth(fin, ROWS_REVENUE), signed=True)
            add_recorded("rev_growth", "Latest quarter (daily readings)")
            res["refs"] = [(0.0, "")]
        elif key == "eps":
            ni = _reported_series(fin, "inc", ROWS_NET_INCOME)
            shares, _src = share_count_history(symbol, shares_now)
            if not ni.empty and not shares.empty:
                add("EPS (TTM)", ni / _asof(shares, ni.index).bfill(), color="#00FFAA", markers=True)
            res["refs"] = [(0.0, "")]
        elif key == "margins":
            rev = _reported_series(fin, "inc", ROWS_REVENUE)
            for name, rows, color in (("Gross Margin", ROWS_GROSS, "#00FFAA"), ("Operating Margin", ROWS_OPERATING, "#4DA3FF"),
                                      ("Net Margin", ROWS_NET_INCOME, "#FF9900")):
                add(name, _ttm_ratio(_reported_series(fin, "inc", rows), rev) * 100.0, color=color, markers=True)
            res["refs"] = [(0.0, "")]
        elif key == "fcf":
            add("Free Cash Flow (TTM)", _reported_series(fin, "cf", ROWS_FCF), color="#00FFAA", markers=True)
            res["refs"] = [(0.0, "")]
        if key != "rev_growth" and fin.get("inc_a") is not None and not res["series"]:
            res["empty"] = f"Yahoo has no financial statements for {symbol} (common for ETFs and some foreign listings)."
    elif key == "earnings":
        ed = fetch_analyst_history(symbol).get("earnings", pd.DataFrame())
        if ed is not None and not ed.empty:
            ed = ed.copy()
            ed.index = _naive_index(ed.index).normalize()
            if "Reported EPS" in ed.columns:
                done = ed[pd.to_numeric(ed["Reported EPS"], errors="coerce").notna()]
                if "EPS Estimate" in done.columns:
                    add("Consensus Estimate", pd.to_numeric(done["EPS Estimate"], errors="coerce"), color="#FF9900",
                        markers=True, mode="markers")
                add("Reported EPS", pd.to_numeric(done["Reported EPS"], errors="coerce"), color="#00FFAA", markers=True, mode="markers")
        res["note"] = "Per-quarter EPS as reported at the time (not adjusted for later stock splits)."
    elif key == "target":
        firm = analyst_firm_history(symbol)
        tgt = firm["target"].dropna() if (not firm.empty and "target" in firm.columns) else pd.Series(dtype=float)
        add("Consensus Price Target", tgt, color="#00FFAA")
        add_recorded("target", "Daily readings (Yahoo consensus)")
        if not daily.empty:
            start = min([s.index.min() for s in res["series"].values()], default=None)
            if start is not None:
                add("Share Price", daily["Close"][daily.index >= start], color="rgba(200, 200, 200, 0.6)")
    elif key == "ratings_mix":
        tally = _monthly_rating_tally(fetch_analyst_history(symbol).get("recs", pd.DataFrame()))
        for name, color in (("Strong Buy", "#00C46A"), ("Buy", "#7EE2A8"), ("Hold", "#F4D35E"),
                            ("Sell", "#F08A5D"), ("Strong Sell", "#E63946")):
            if name in tally.columns:
                res["series"][name] = tally[name].astype(float)
                res["styles"][name] = {"color": color}
    elif key == "eps_revisions":
        analyst = fetch_analyst_history(symbol)
        for period, name, color in (("0q", "Current quarter", "#4DA3FF"), ("+1q", "Next quarter", "#C77DFF"),
                                    ("0y", "Current fiscal year", "#00FFAA"), ("+1y", "Next fiscal year", "#FF9900")):
            add(name, _eps_trend_points(analyst, period), color=color, markers=True)
    elif key == "short":
        flt = _clean_number(info.get("floatShares"))
        pts = {}
        for shares_key, date_key in (("sharesShortPriorMonth", "sharesShortPreviousMonthDate"), ("sharesShort", "dateShortInterest")):
            sh, ts = _clean_number(info.get(shares_key)), _clean_number(info.get(date_key))
            if sh is not None and ts and flt:
                pts[pd.Timestamp(datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date())] = sh / flt * 100.0
        add("Yahoo reports (current & prior month)", pd.Series(pts, dtype=float), color="#00FFAA", markers=True)
        add_recorded("short_pct")
        res["refs"] = [(5.0, "5%"), (10.0, "10%")]

    if not res["series"] and not res["empty"]:
        res["empty"] = (RECORDED_NOTE if key in RECORDED_ONLY_METRICS
                        else f"Yahoo has no history for this metric for {symbol} right now.")
    return res


def _monthly_rating_tally(recs):
    """Yahoo's monthly Strong Buy → Strong Sell counts (periods 0m, -1m, -2m, -3m), dated at each month's start."""
    if recs is None or recs.empty:
        return pd.DataFrame()
    this_month = pd.Timestamp.today().normalize().replace(day=1)
    rows = {}
    for i, r in enumerate(recs.to_dict("records")):
        m = re.match(r"^(-?\d+)m$", str(r.get("period", f"-{i}m")))
        back = abs(int(m.group(1))) if m else i
        rows[this_month - pd.DateOffset(months=back)] = {
            "Strong Buy": r.get("strongBuy", 0), "Buy": r.get("buy", 0), "Hold": r.get("hold", 0),
            "Sell": r.get("sell", 0), "Strong Sell": r.get("strongSell", 0)}
    return pd.DataFrame.from_dict(rows, orient="index").apply(pd.to_numeric, errors="coerce").fillna(0).sort_index()


HISTORY_WINDOWS = {"1D": 1, "1W": 7, "1M": 31, "1Y": 366, "5Y": 5 * 366, "Max": None}


def _fmt_metric_value(unit, v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "N/A"
    if unit == "usd":
        return f"${v:,.2f}"
    if unit == "usd_big":
        a = abs(v)
        for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
            if a >= div:
                return f"{'-' if v < 0 else ''}${a / div:,.2f}{suf}"
        return f"${v:,.0f}"
    if unit == "pct":
        return f"{v:,.1f}%"
    if unit == "x":
        return f"{v:,.2f}x"
    if unit == "num":
        return f"{v:,.0f}"
    if unit == "score":
        return f"{v:.1f}"
    return f"{v:,.2f}"


def render_metric_history(symbol, company, key, window, info, sector_benchmark_pe):
    meta = HISTORY_METRIC_INFO[key]
    unit, kind = meta["unit"], meta["kind"]
    with st.spinner(f"Loading {meta['label']} history for {symbol}..."):
        res = compute_metric_history(symbol, key, info, sector_benchmark_pe)
    series = res["series"]
    if not series:
        st.info(res["empty"])
        st.caption(f"Source: {meta['source']}")
        return

    days = HISTORY_WINDOWS.get(window)
    shown, fell_back = series, False
    if days:
        start = pd.Timestamp.today().normalize() - pd.Timedelta(days=days)
        shown = {n: s[s.index >= start] for n, s in series.items()}
        need = 1 if kind in ("bar", "stacked") else 2
        if max(len(s) for s in shown.values()) < need:
            shown, fell_back = series, True
    shown = {n: s for n, s in shown.items() if not s.empty}
    # keep long daily histories light: weekly closes once a line has more than ~2,500 points
    shown = {n: (s.resample("W-FRI").last().dropna() if (len(s) > 2500 and kind not in ("bar", "stacked")) else s)
             for n, s in shown.items()}
    if kind == "rebased":
        shown = {n: (s / s.iloc[0] - 1.0) * 100.0 for n, s in shown.items()}

    first_name = next(iter(shown))
    latest_val, latest_day = float(shown[first_name].iloc[-1]), shown[first_name].index[-1]
    big_suffix = ""
    if unit == "usd_big":           # plot in $B / $T / $M so the axis reads naturally
        peak = max(float(s.abs().max()) for s in shown.values())
        div, big_suffix = next(((d, x) for d, x in ((1e12, "T"), (1e9, "B"), (1e6, "M")) if peak >= d), (1.0, ""))
        shown = {n: s / div for n, s in shown.items()}

    palette = ["#00FFAA", "#FF9900", "#4DA3FF", "#FF4A4A", "#C77DFF", "#FFD166"]
    fig = go.Figure()
    hover_fmt = {"usd": "$%{y:,.2f}", "usd_big": "$%{y:,.2f}" + big_suffix, "pct": "%{y:.1f}%", "x": "%{y:.2f}x",
                 "num": "%{y:,.0f}", "score": "%{y:.1f}", "ratio": "%{y:.2f}"}.get(unit, "%{y:.2f}")
    if kind == "band" and {"52-Week High", "52-Week Low"} <= set(shown):
        fig.add_trace(go.Scatter(x=shown["52-Week Low"].index, y=shown["52-Week Low"], name="52-Week Low", mode="lines",
                                 line=dict(color="rgba(255, 74, 74, 0.7)", width=1.2),
                                 hovertemplate=f"52W Low: {hover_fmt}<extra></extra>"))
        fig.add_trace(go.Scatter(x=shown["52-Week High"].index, y=shown["52-Week High"], name="52-Week High", mode="lines",
                                 line=dict(color="rgba(0, 255, 170, 0.7)", width=1.2), fill="tonexty",
                                 fillcolor="rgba(120, 160, 255, 0.10)",
                                 hovertemplate=f"52W High: {hover_fmt}<extra></extra>"))
        if "Close" in shown:
            fig.add_trace(go.Scatter(x=shown["Close"].index, y=shown["Close"], name="Close", mode="lines",
                                     line=dict(color="#FFFFFF", width=1.8), hovertemplate=f"Close: {hover_fmt}<extra></extra>"))
    else:
        for i, (name, s) in enumerate(shown.items()):
            style = res["styles"].get(name, {})
            color = style.get("color", palette[i % len(palette)])
            if kind in ("bar", "stacked"):
                colors = color
                if style.get("signed"):
                    colors = ["#00FFAA" if v >= 0 else "#FF4A4A" for v in s.values]
                if kind == "bar" and style.get("dash"):      # recorded readings drawn as a line over the bars
                    fig.add_trace(go.Scatter(x=s.index, y=s.values, name=name, mode="lines+markers",
                                             line=dict(color="#FF9900", width=2, dash="dot"),
                                             hovertemplate=f"{name}: {hover_fmt}<extra></extra>"))
                    continue
                fig.add_trace(go.Bar(x=s.index, y=s.values, name=name, marker_color=colors,
                                     hovertemplate=f"{name}: {hover_fmt}<extra></extra>"))
                continue
            mode = style.get("mode") or ("lines+markers" if (style.get("markers") and len(s) <= 120) or len(s) <= 2 else "lines")
            fig.add_trace(go.Scatter(
                x=s.index, y=s.values, name=name, mode=mode,
                line=dict(color=color, width=2.6 if i == 0 else 2.0, dash=style.get("dash") or "solid",
                          shape="hv" if kind == "step" else "linear"),
                marker=dict(size=9 if mode == "markers" else 6, color=color),
                hovertemplate=f"{name}: {hover_fmt}<extra></extra>",
            ))
    for y, label in res["refs"]:
        if label:
            fig.add_hline(y=y, line_dash="dash", line_color="rgba(180, 180, 180, 0.45)",
                          annotation_text=label, annotation_position="bottom right")
        else:
            fig.add_hline(y=y, line_dash="dash", line_color="rgba(180, 180, 180, 0.45)")

    latest_txt = _fmt_metric_value("pct" if kind == "rebased" else unit, latest_val)   # taken before any $B scaling
    title = f"{company} ({symbol}): {meta['label']} — latest {latest_txt} ({latest_day:%b %d, %Y})"
    yaxis = {}
    if unit == "usd":
        yaxis = dict(tickprefix="$", tickformat=",.2f")
    elif unit == "usd_big":
        yaxis = dict(tickprefix="$", ticksuffix=big_suffix, tickformat=",.2f")
    elif unit in ("pct",) or kind == "rebased":
        yaxis = dict(ticksuffix="%")
    elif unit == "x":
        yaxis = dict(ticksuffix="x")
    elif unit == "score":
        yaxis = dict(range=[0, 100])
    x_min = min(s.index.min() for s in shown.values())
    x_max = max(s.index.max() for s in shown.values())
    xaxis = {}
    if (x_max - x_min) < pd.Timedelta(days=10):       # only a few days of readings so far: show a readable date span
        mid = x_min + (x_max - x_min) / 2
        xaxis = dict(range=[mid - pd.Timedelta(days=7), mid + pd.Timedelta(days=7)], tickformat="%b %d")
    fig.update_layout(
        title=dict(text=title, font=dict(size=15)), hovermode="x unified", barmode="stack" if kind == "stacked" else None,
        yaxis=yaxis, xaxis=xaxis, margin=dict(l=20, r=20, t=50, b=20), template="plotly_dark",
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="left", x=0),
        showlegend=len(shown) > 1 or kind == "band",
    )
    try:
        st.plotly_chart(fig, **STRETCH)
    except TypeError:
        st.plotly_chart(fig, width="stretch")

    span_from = min(s.index.min() for s in series.values())
    notes = [f"Source: {meta['source']}"]
    if res["note"]:
        notes.append(res["note"])
    if fell_back:
        notes.append(f"Fewer than two readings in the last {window}, so all available history is shown "
                     f"(from {span_from:%b %Y}).")
    elif window == "Max":
        notes.append(f"History available from {span_from:%b %d, %Y}.")
    if key not in RECORDED_ONLY_METRICS and any("recorded" in n.lower() for n in series):
        notes.append("Dotted line = daily readings saved from Yahoo since this feature was added.")
    if key in RECORDED_ONLY_METRICS:
        n_days = max(len(s) for s in series.values())
        if n_days < 30:
            notes.append(f"{n_days} daily reading(s) so far; the line builds a day at a time.")
    st.caption(" ".join(notes))


# --- Market cap vs peers (Deep Dive colour coding) ---
PEER_CAPS_FILE = os.path.join(DATA_DIR, "peer_caps.json") if DATA_DIR else None
PEER_CAPS_MAX_AGE_DAYS = 30
MIN_PEERS = 4
MCAP_TIERS = {
    "giant": ("Giant vs peers", "#A855F7"),
    "large": ("Large vs peers", "#3B82F6"),
    "typical": ("Typical size for peers", None),
    "small": ("Small vs peers", "#F59E0B"),
    "tiny": ("Tiny vs peers", "#EF4444"),
}


def save_peer_market_caps(df):
    """Keeps the latest market cap, sector and industry of every scanned stock, so the Deep Dive can compare
    a stock with its peers without scanning again."""
    if not PEER_CAPS_FILE or df is None or getattr(df, "empty", True) or not {"Ticker", "Sector", "Market Cap Num"} <= set(df.columns):
        return
    today = _market_date(time.time())
    cutoff = (datetime.date.today() - datetime.timedelta(days=PEER_CAPS_MAX_AGE_DAYS)).isoformat()
    with _data_file_lock():
        data = _read_json_file(PEER_CAPS_FILE, {})
        stocks = data.get("stocks") if isinstance(data.get("stocks"), dict) else {}
        for r in df.to_dict("records"):
            cap = _clean_number(r.get("Market Cap Num"))
            if r.get("Ticker") and cap and cap > 0:
                stocks[str(r["Ticker"])] = [str(r.get("Sector") or ""), str(r.get("Industry") or ""), cap, today]
        stocks = {t: v for t, v in stocks.items() if isinstance(v, list) and len(v) == 4 and str(v[3]) >= cutoff}
        _write_json_file(PEER_CAPS_FILE, {"updated": time.time(), "stocks": stocks})


def _peer_cap_table():
    """ticker -> (sector, industry, market cap): saved scans, overridden by any fresher scan in this session."""
    table = {}
    data = _read_json_file(PEER_CAPS_FILE, {}) if PEER_CAPS_FILE else {}
    cutoff = (datetime.date.today() - datetime.timedelta(days=PEER_CAPS_MAX_AGE_DAYS)).isoformat()
    for t, v in (data.get("stocks") or {}).items():
        if isinstance(v, list) and len(v) >= 4 and _clean_number(v[2]) and str(v[3]) >= cutoff:
            table[t] = (v[0], v[1], float(v[2]))
    for key, val in (st.session_state.get("store") or {}).items():
        if str(key).startswith("uscan_") and isinstance(val, tuple) and len(val) == 2 and isinstance(val[1], pd.DataFrame):
            df = val[1]
            if {"Ticker", "Sector", "Market Cap Num"} <= set(df.columns):
                for r in df.to_dict("records"):
                    cap = _clean_number(r.get("Market Cap Num"))
                    if cap and cap > 0:
                        table[str(r["Ticker"])] = (str(r.get("Sector") or ""), str(r.get("Industry") or ""), cap)
    return table


def market_cap_size_class(mcap):
    if not mcap:
        return ""
    for floor, name in ((200e9, "Mega-cap"), (10e9, "Large-cap"), (2e9, "Mid-cap"), (300e6, "Small-cap")):
        if mcap >= floor:
            return name
    return "Micro-cap"


def market_cap_peer_context(ticker, sector, industry, mcap):
    """Where a stock's market cap sits among its peers: same industry when there are enough scanned peers,
    otherwise the same sector."""
    ctx = {"size_class": market_cap_size_class(mcap), "tier": None, "label": "", "color": None, "detail": ""}
    if not mcap:
        return ctx
    table = _peer_cap_table()
    groups = []
    if industry and industry != "N/A":
        groups.append((industry, [c for t, (s, i, c) in table.items() if t != ticker and i == industry]))
    if sector and sector != "N/A":
        groups.append((sector, [c for t, (s, i, c) in table.items() if t != ticker and s == sector]))
    name, caps = next(((n, c) for n, c in groups if len(c) >= MIN_PEERS), (None, []))
    if not name:
        ctx["detail"] = "Peer comparison appears after the next market scan."
        return ctx
    caps = np.array(caps, dtype=float)
    n = len(caps) + 1
    rank = int((caps > mcap).sum()) + 1
    pct = float((caps < mcap).mean())
    median = float(np.median(caps))
    ratio = mcap / median if median > 0 else None
    if rank == 1 or pct >= 0.95:
        tier = "giant"
    elif pct >= 0.75:
        tier = "large"
    elif rank == n or pct <= 0.05:
        tier = "tiny"
    elif pct <= 0.25:
        tier = "small"
    else:
        tier = "typical"
    ratio_txt = ""
    if ratio:
        ratio_txt = f"{ratio:,.0f}×" if ratio >= 10 else f"{ratio:.1f}×" if ratio >= 1 else f"{ratio:.2f}×"
    ctx.update({
        "tier": tier, "label": MCAP_TIERS[tier][0], "color": MCAP_TIERS[tier][1], "rank": rank, "n": n,
        "group": name, "median": median, "ratio": ratio,
        "detail": f"#{rank} of {n} in {name} · {ratio_txt} the peer median ({format_market_cap(median)})",
    })
    return ctx


def market_cap_help(ctx):
    """The ? bubble text: where the stock ranks among peers, then what the colours mean."""
    plain = lambda t: str(t).replace("$", "")           # "$" pairs would render as a math formula in the bubble
    lines = []
    head = " · ".join(p for p in (ctx.get("size_class"), ctx.get("label")) if p)
    if head:
        lines.append(f"**{head}**")
    if ctx.get("tier"):
        r = ctx["ratio"]
        ratio_txt = f"{r:,.0f}×" if r >= 10 else f"{r:.1f}×" if r >= 1 else f"{r:.2f}×"
        lines.append(f"Ranks **#{ctx['rank']} of {ctx['n']}** in {ctx['group']}, at {ratio_txt} the peer median "
                     f"market cap ({plain(format_market_cap(ctx['median']))}).")
    elif ctx.get("detail"):
        lines.append(ctx["detail"])
    lines.append("Colour shows size vs peers (same industry, or the sector when there are too few scanned peers): "
                 "purple = the largest (#1 or top 5%), blue = top quarter, no colour = typical, "
                 "orange = bottom quarter, red = the smallest.")
    lines.append("Mega-cap ≥ 200B, Large-cap ≥ 10B, Mid-cap ≥ 2B, Small-cap ≥ 300M, Micro-cap below that.")
    return "\n\n".join(lines)


def market_cap_color_css(ctx):
    """Colours the Market Cap value and its badge (the metric sits in a container keyed mcap_metric)."""
    color = ctx.get("color")
    if not color:
        return ""
    k = ".st-key-mcap_metric"
    return (f"{k} [data-testid=\"stMetricValue\"], {k} [data-testid=\"stMetricValue\"] * {{ color: {color} !important; "
            f"font-weight: 600; }} "
            f"{k} [data-testid=\"stMetricDelta\"] {{ color: {color} !important; background-color: {color}22 !important; "
            f"border: 1px solid {color}66; }} "
            f"{k} [data-testid=\"stMetricDelta\"] * {{ color: {color} !important; }}")


def apply_smoothing(df, live_col, score_key, hist, now_ts):
    """Adds Score Now / Smoothed / Confidence % / Days Seen. The smoothed score is the stock's decayed average,
    pulled toward the median of today's list by however little history it has (Bayesian shrinkage)."""
    out = df.copy()
    live = pd.to_numeric(out[live_col], errors="coerce")
    prior = float(live.median()) if live.notna().any() else 50.0
    smoothed, confidence, days_seen = [], [], []
    for ticker, live_val in zip(out["Ticker"], live):
        rec = hist.get(ticker) if isinstance(hist, dict) else None
        if not isinstance(rec, dict) or "avg" not in rec:
            n_now, avg, days = 0.0, (live_val if pd.notna(live_val) else prior), 0
        else:
            hours_since = max(0.0, (now_ts - rec.get("last_ts", now_ts)) / 3600.0)
            n_now = float(rec.get("n", 1.0)) * 0.5 ** (hours_since / SMOOTH_HALF_LIFE_HOURS)
            avg = rec["avg"].get(score_key, live_val)
            days = len(rec.get("days", []))
        smoothed.append(round((n_now * avg + SMOOTH_PRIOR_WEIGHT * prior) / (n_now + SMOOTH_PRIOR_WEIGHT), 1))
        confidence.append(round(100.0 * n_now / (n_now + SMOOTH_PRIOR_WEIGHT)))
        days_seen.append(days)
    out["Score Now"] = live.round(1)
    out["Smoothed Score"] = smoothed
    out["Confidence %"] = confidence
    out["Days Seen"] = days_seen
    return out


@st.cache_data(ttl=1800)
def _fetch_index_level_cached(symbol="^GSPC"):
    hist = yf.Ticker(symbol).history(period="5d")
    close = hist["Close"].dropna()
    if close.empty:
        raise ValueError("no index data")
    return float(close.iloc[-1])


def fetch_index_level(symbol="^GSPC"):
    try:
        return _fetch_index_level_cached(symbol)
    except Exception:
        return None


def save_daily_snapshot(ranked_lists, now_ts, company_names=None, coverage=None, source="page view"):
    """Saves today's top picks once per market day. ranked_lists: {list_key: ranked DataFrame}.
    company_names ({ticker: name} from the current scan) fills in names for picks saved without one."""
    if not TRACK_RECORD_FILE:
        return False
    today = _market_date(now_ts)
    with _data_file_lock():
        record = _read_json_file(TRACK_RECORD_FILE, {"snapshots": []})
        snaps = record.setdefault("snapshots", [])
        names = record.setdefault("names", {})
        tracked = {p.get("ticker") for s in snaps for picks in (s.get("lists") or {}).values() for p in picks}
        new_names = {t: n for t, n in (company_names or {}).items() if t in tracked and n and names.get(t) != n}
        if any(s.get("date") == today for s in snaps):
            if new_names:
                names.update(new_names)
                _write_json_file(TRACK_RECORD_FILE, record)
            return False
        names.update(new_names)
        lists = {}
        for key, rdf in ranked_lists.items():
            picks = []
            for rank, row in enumerate(rdf.head(TRACK_TOP_N).to_dict("records"), start=1):
                # Scan rows store Price as display text, e.g. "$1,234.50"
                price = _clean_number(str(row.get("Price", "")).replace("$", "").replace(",", ""))
                if price and price > 0:
                    picks.append({"rank": rank, "ticker": row["Ticker"], "company": row.get("Company"),
                                  "price": round(price, 4), "score": _clean_number(row.get("Composite Index")),
                                  "sector": row.get("Sector")})
                    if row.get("Company"):
                        names[row["Ticker"]] = row.get("Company")
            lists[key] = picks
        if not any(lists.values()):
            return False
        snaps.append({"date": today, "ts": now_ts, "spx": fetch_index_level("^GSPC"), "lists": lists,
                      "coverage": coverage, "source": source})
        return _write_json_file(TRACK_RECORD_FILE, record)


def load_track_record():
    with _data_file_lock():
        record = _read_json_file(TRACK_RECORD_FILE, {"snapshots": []})
    return record.get("snapshots", []), record.get("names", {})


@st.cache_data(ttl=1800)
def fetch_latest_closes(tickers):
    """Latest close for each ticker: one batched Yahoo request, then single lookups for any gaps."""
    tickers = list(tickers)
    closes = {}
    try:
        data = yf.download(tickers, period="5d", progress=False, auto_adjust=False, threads=False)
        close = data["Close"] if "Close" in data else data
        if isinstance(close, pd.Series):
            close = close.to_frame(tickers[0])
        for t in tickers:
            if t in close.columns:
                col = pd.to_numeric(close[t], errors="coerce").dropna()
                if not col.empty:
                    closes[t] = float(col.iloc[-1])
    except Exception:
        pass
    for t in [t for t in tickers if t not in closes][:40]:
        try:
            col = yf.Ticker(t).history(period="5d")["Close"].dropna()
            if not col.empty:
                closes[t] = float(col.iloc[-1])
        except Exception:
            continue
    return closes


def build_track_record_table(snaps, closes, spx_now, names=None):
    rows = []
    for snap in snaps:
        spx_entry = _clean_number(snap.get("spx"))
        spx_ret = ((spx_now / spx_entry) - 1.0) * 100.0 if (spx_entry and spx_now) else None
        for key, picks in (snap.get("lists") or {}).items():
            if key not in TRACK_LISTS:
                continue
            for p in picks:
                now_px = closes.get(p.get("ticker"))
                entry = _clean_number(p.get("price"))
                ret = ((now_px / entry) - 1.0) * 100.0 if (now_px and entry) else None
                rows.append({
                    "Date": snap.get("date"), "List": TRACK_LISTS.get(key, key), "List Key": key,
                    "Pool": snap.get("coverage") or "",
                    "Rank": p.get("rank"), "Ticker": p.get("ticker"),
                    "Company": p.get("company") or (names or {}).get(p.get("ticker")) or "",
                    "Sector": p.get("sector"),
                    "Score at Pick": p.get("score"), "Entry Price": entry, "Latest Price": now_px,
                    "Return %": ret, "S&P 500 %": spx_ret,
                    "Excess %": (ret - spx_ret) if (ret is not None and spx_ret is not None) else None,
                })
    return pd.DataFrame(rows)


# ==============================================================================
# MARKET UNIVERSE: S&P 500 / 400 / 600 MEMBER LISTS (REFRESHED WEEKLY) + SCAN COVERAGE
# ==============================================================================
UNIVERSE_FILE = os.path.join(DATA_DIR, "universe.json") if DATA_DIR else None
UNIVERSE_REFRESH_DAYS = 7          # re-download the index member lists once a week
UNIVERSE_RETRY_HOURS = 6           # after a failed download, wait this long before trying again
SCAN_CACHE_SECONDS = 3600          # scan results are reused for an hour (Refresh Data forces a new scan)
INDEX_PAGES = {
    # key: (Wikipedia page, minimum rows for the download to count as valid)
    "sp500": ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", 400),
    "sp400": ("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", 300),
    "sp600": ("https://en.wikipedia.org/wiki/List_of_S%26P_600_companies", 450),
}
GICS_TO_YAHOO_SECTOR = {
    "Information Technology": "Technology",
    "Financials": "Financial Services",
    "Health Care": "Healthcare",
    "Consumer Discretionary": "Consumer Cyclical",
    "Consumer Staples": "Consumer Defensive",
    "Materials": "Basic Materials",
    "Energy": "Energy",
    "Industrials": "Industrials",
    "Communication Services": "Communication Services",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
}
# Second share classes of the same company: skipped when the main class is also listed
SHARE_CLASS_DUPLICATES = {"GOOG": "GOOGL", "FOX": "FOXA", "NWS": "NWSA", "BRK-A": "BRK-B", "BF-A": "BF-B",
                          "LEN-B": "LEN", "HEI-A": "HEI", "MOG-B": "MOG-A", "UHAL-B": "UHAL", "CWEN-A": "CWEN"}


class _WikiTableParser(HTMLParser):
    """Collects the text of every top-level HTML table as rows of cells (standard library only)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._depth, self._row, self._cell, self._skip = [], 0, None, None, 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self.tables.append([])
        elif self._depth == 1:
            if tag == "tr":
                self._row = []
            elif tag in ("td", "th") and self._row is not None:
                self._cell = []
            elif tag in ("sup", "style", "script"):
                self._skip += 1

    def handle_endtag(self, tag):
        if tag == "table":
            self._depth = max(0, self._depth - 1)
        elif self._depth == 1:
            if tag in ("td", "th") and self._cell is not None and self._row is not None:
                self._row.append(" ".join("".join(self._cell).split()))
                self._cell = None
            elif tag == "tr" and self._row is not None:
                if self._row:
                    self.tables[-1].append(self._row)
                self._row = None
            elif tag in ("sup", "style", "script") and self._skip:
                self._skip -= 1

    def handle_data(self, data):
        if self._cell is not None and not self._skip:
            self._cell.append(data)


def parse_index_table(html_text):
    """[[ticker, company, sector], ...] from the first table with 'Symbol' and 'GICS Sector' columns."""
    parser = _WikiTableParser()
    parser.feed(html_text or "")
    for table in parser.tables:
        if not table:
            continue
        header = [c.strip().lower() for c in table[0]]
        if "symbol" not in header or "gics sector" not in header:
            continue
        i_sym, i_sec = header.index("symbol"), header.index("gics sector")
        i_name = header.index("security") if "security" in header else None
        rows = []
        for row in table[1:]:
            if len(row) <= max(i_sym, i_sec) or not row[i_sym].strip():
                continue
            sym = row[i_sym].split()[0].upper().replace(".", "-")
            if not re.match(r"^[A-Z][A-Z0-9\-]{0,7}$", sym):
                continue
            name = row[i_name] if (i_name is not None and i_name < len(row)) else sym
            gics = row[i_sec].strip()
            rows.append([sym, name, GICS_TO_YAHOO_SECTOR.get(gics, gics)])
        return rows
    return []


def _download_index_members():
    headers = {"User-Agent": "StockSentimentEngine/1.0 (personal research dashboard; python-requests)"}
    lists = {}
    for key, (url, min_rows) in INDEX_PAGES.items():
        try:
            resp = requests.get(url, headers=headers, timeout=12)
            resp.raise_for_status()
            rows = parse_index_table(resp.text)
            if len(rows) >= min_rows:
                lists[key] = rows
        except Exception:
            continue
    return lists


@st.cache_data(ttl=3600, show_spinner=False)
def load_index_universe():
    """Index member lists from universe.json, re-downloaded when a week old. Never raises: falls back to the
    last good lists, or to the built-in list if nothing has ever been downloaded."""
    now_ts = time.time()
    with _data_file_lock():
        saved = _read_json_file(UNIVERSE_FILE, {}) if UNIVERSE_FILE else {}
    lists = saved.get("lists") or {}
    fetched = saved.get("fetched_ts")
    stale = (not fetched) or (now_ts - fetched > UNIVERSE_REFRESH_DAYS * 86400)
    if stale and now_ts - saved.get("last_attempt_ts", 0) > UNIVERSE_RETRY_HOURS * 3600:
        fresh = _download_index_members()
        saved["last_attempt_ts"] = now_ts
        if "sp500" in fresh:
            lists = {**lists, **fresh}       # an index that failed this time keeps its previous list
            saved["lists"], saved["fetched_ts"] = lists, now_ts
            fetched = now_ts
        if UNIVERSE_FILE:
            with _data_file_lock():
                _write_json_file(UNIVERSE_FILE, saved)
    if "sp500" not in lists:
        status = "builtin"
    elif fetched and now_ts - fetched <= UNIVERSE_REFRESH_DAYS * 86400:
        status = "fresh"
    else:
        status = "stale"
    return {"lists": lists, "fetched_ts": fetched, "status": status}


def build_coverage_tiers(lists):
    """Scan lists from smallest to largest. Each tier contains the one before it, interleaved by sector so any
    cut-off samples every sector; best-known names come first within each tier."""
    curated = broad_scan_order()
    curated_set = set(curated)
    sector_of = {}
    for sec in SECTOR_UNIVERSES:
        for t in sector_candidate_pool(sec):
            sector_of.setdefault(t, sec)
    for key in ("sp500", "sp400", "sp600"):
        for t, _name, sec in lists.get(key, []):
            if sec in SECTOR_UNIVERSES:
                sector_of[t] = sec          # the index's (GICS) classification wins

    def interleave(tickers):
        groups = {s: [] for s in SECTOR_UNIVERSES}
        other = []
        for t in tickers:
            (groups[sector_of[t]] if sector_of.get(t) in groups else other).append(t)
        return [t for g in itertools.zip_longest(*groups.values()) for t in g if t] + other

    def clean(seq):
        seq = list(dict.fromkeys(seq))
        present = set(seq)
        return [t for t in seq if not (t in SHARE_CLASS_DUPLICATES and SHARE_CLASS_DUPLICATES[t] in present)]

    if "sp500" not in lists:
        full = clean(curated)
        tiers = [("top300", "Top 300 (fastest)", full[:300]),
                 ("builtin", f"Full built-in list ({len(full)})", full)]
    else:
        members = {k: [r[0] for r in lists.get(k, [])] for k in ("sp500", "sp400", "sp600")}
        sets = {k: set(v) for k, v in members.items()}
        in_any = sets["sp500"] | sets["sp400"] | sets["sp600"]

        def tier_add(key):
            return [t for t in curated if t in sets[key]] + interleave([t for t in members[key] if t not in curated_set])

        t500 = clean(tier_add("sp500") + [t for t in curated if t not in in_any])
        t900 = clean(t500 + tier_add("sp400"))
        t1500 = clean(t900 + tier_add("sp600"))
        tiers = [("top300", "Top 300 (fastest)", t500[:300]),
                 ("sp500", f"S&P 500 + popular extras ({len(t500)})", t500)]
        if members["sp400"]:
            tiers.append(("sp900", f"+ S&P MidCap 400 ({len(t900)})", t900))
        if members["sp600"]:
            tiers.append(("sp1500", f"+ S&P SmallCap 600 ({len(t1500)})", t1500))
    return {"tiers": tiers, "sector_of": sector_of}


# ---- Sidebar: Market Coverage + Rows to Display ----
UNIVERSE = load_index_universe()
COVERAGE = build_coverage_tiers(UNIVERSE["lists"])
COVERAGE_LISTS = {k: v for k, _label, v in COVERAGE["tiers"]}
COVERAGE_LABELS = {k: label for k, label, _v in COVERAGE["tiers"]}
DISPLAY_ROW_OPTIONS = [25, 50, 100, 200, 500]


def load_app_settings():
    return _read_json_file(SETTINGS_FILE, {}) if SETTINGS_FILE else {}


def save_app_setting(key, value):
    """Settings are shared by every browser and device (single-user app)."""
    if not SETTINGS_FILE:
        return
    with _data_file_lock():
        settings = _read_json_file(SETTINGS_FILE, {})
        settings[key] = value
        _write_json_file(SETTINGS_FILE, settings)


APP_SETTINGS = load_app_settings()
DAILY_JOB = bool(st.session_state.get("_daily_job"))   # set by daily_scan.py (headless run, no browser)
_default_track = "sp500" if "sp500" in COVERAGE_LISTS else "top300"
TRACK_COVERAGE = APP_SETTINGS.get("track_coverage") if APP_SETTINGS.get("track_coverage") in COVERAGE_LISTS else _default_track

if DAILY_JOB:
    st.session_state["coverage_tier"] = TRACK_COVERAGE
elif st.session_state.get("coverage_tier") not in COVERAGE_LISTS:
    _saved_tier = APP_SETTINGS.get("coverage_tier")
    st.session_state["coverage_tier"] = _saved_tier if _saved_tier in COVERAGE_LISTS else "top300"
if st.session_state.get("display_rows") not in DISPLAY_ROW_OPTIONS:
    _saved_rows = APP_SETTINGS.get("display_rows")
    st.session_state["display_rows"] = _saved_rows if _saved_rows in DISPLAY_ROW_OPTIONS else 50
with scan_scope_box:
    coverage_key = st.selectbox(
        "Market Coverage:",
        options=list(COVERAGE_LISTS),
        format_func=lambda k: COVERAGE_LABELS[k],
        key="coverage_tier",
        on_change=lambda: save_app_setting("coverage_tier", st.session_state["coverage_tier"]),
        help="Remembered across reloads and devices. Which stocks are scanned and ranked. Every table ranks the whole scan and then shows the top rows. "
             "Bigger lists catch more companies but take longer the first time each hour (several minutes for the "
             "largest), and use more Yahoo requests. A sector filter always covers at least that sector's S&P 500 members.",
    )
    scan_depth = st.select_slider(
        "Rows to Display:",
        options=DISPLAY_ROW_OPTIONS,
        key="display_rows",
        on_change=lambda: save_app_setting("display_rows", st.session_state["display_rows"]),
        help="Remembered across reloads and devices. How many of the top-ranked stocks each table shows. "
             "Rankings always use the whole Market Coverage scan.",
    )
    if UNIVERSE["status"] == "fresh":
        list_note = (f"🗂️ Stock list: S&P 500 / 400 / 600 members, updated "
                     f"{datetime.datetime.fromtimestamp(UNIVERSE['fetched_ts']).strftime('%b %d')} (refreshes weekly).")
    elif UNIVERSE["status"] == "stale":
        list_note = (f"🗂️ Stock list from {datetime.datetime.fromtimestamp(UNIVERSE['fetched_ts']).strftime('%b %d')}: "
                     "couldn't refresh it this week, will retry.")
    else:
        list_note = ("🗂️ Using the built-in stock list: the S&P 500 / 400 / 600 member lists couldn't be downloaded "
                     "(retries every 6 hours).")
    st.caption(list_note)

SCAN_TICKERS = COVERAGE_LISTS[coverage_key]
# A sector page covers at least that sector's S&P 500 members, even when the coverage is the Top 300
SECTOR_BASE_TICKERS = (COVERAGE_LISTS.get("sp500") or COVERAGE_LISTS.get("builtin") or SCAN_TICKERS) \
    if coverage_key == "top300" else SCAN_TICKERS


def scan_request(sector_name):
    if not sector_name or sector_name == "All Sectors":
        return list(SCAN_TICKERS)
    return [t for t in SECTOR_BASE_TICKERS if COVERAGE["sector_of"].get(t) == sector_name]


@st.cache_data(ttl=SCAN_CACHE_SECONDS)
def run_universe_scan(tickers, tf_window="Current"):
    """Scans every ticker; returns all rows that loaded (any sector) in list order."""
    rows = {}
    fetch_trending_tickers()      # shared feeds: load once here rather than in every worker thread
    reddit_mentions_table()
    with concurrent.futures.ThreadPoolExecutor(max_workers=YAHOO_MAX_WORKERS) as executor:
        futures = {executor.submit(scan_single_ticker, sym, tf_window): sym for sym in tickers}
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
            except Exception:
                res = None
            if res:
                rows[futures[future]] = res
    flush_social_cache()
    return pd.DataFrame([rows[s] for s in tickers if s in rows])


# --- Server snapshot: the background job scans the market every 30 minutes (weekdays, market hours) and saves the
# results here, so pages open instantly from it. 🔄 Refresh Data still runs a live scan for that browser session.
SNAPSHOT_FILE = os.path.join(DATA_DIR, "market_snapshot.pkl") if DATA_DIR else None
SNAPSHOT_MAX_AGE_HOURS = 96.0         # covers weekends and holidays; older than this, pages scan live
SNAPSHOT_DAILY_JOB_MAX_AGE_HOURS = 2.0  # the 3:30 PM Track Record scan only reuses a snapshot this fresh
REFRESH_JOB = bool(st.session_state.get("_refresh_job"))   # set by market_refresh.py (headless background scan)


def save_market_snapshot(df, requested, coverage):
    if not SNAPSHOT_FILE or df is None or df.empty:
        return False
    payload = {"ts": time.time(), "requested": list(requested), "coverage": coverage, "df": df.reset_index(drop=True)}
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(SNAPSHOT_FILE), suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            pickle.dump(payload, f, protocol=4)
        os.replace(tmp, SNAPSHOT_FILE)          # atomic: a reader never sees half a file
        return True
    except (OSError, pickle.PickleError):
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False


@st.cache_resource
def _snapshot_memory():
    return {"mtime": None, "data": None, "lock": threading.Lock()}


def load_market_snapshot():
    """The latest background snapshot (kept in memory until the file changes), or None."""
    if not SNAPSHOT_FILE or not os.path.exists(SNAPSHOT_FILE):
        return None
    mem = _snapshot_memory()
    try:
        mtime = os.path.getmtime(SNAPSHOT_FILE)
    except OSError:
        return None
    with mem["lock"]:
        if mem["mtime"] != mtime:
            try:
                with open(SNAPSHOT_FILE, "rb") as f:
                    data = pickle.load(f)
                df = data["df"]
                data["by_ticker"] = df.set_index("Ticker", drop=False)
                data["requested_set"] = set(data.get("requested") or df["Ticker"])
                mem.update(mtime=mtime, data=data)
            except Exception:
                return mem["data"]
        return mem["data"]


def snapshot_rows(requested):
    """Rows for `requested` from the server snapshot, or None if the snapshot can't serve this request
    (missing, too old, older than this session's last 🔄 Refresh, or not covering these stocks)."""
    snap = load_market_snapshot()
    if not snap:
        return None
    age_h = (time.time() - snap["ts"]) / 3600.0
    max_age = SNAPSHOT_DAILY_JOB_MAX_AGE_HOURS if DAILY_JOB else SNAPSHOT_MAX_AGE_HOURS
    if age_h > max_age or snap["ts"] < st.session_state.get("live_after", 0):
        return None
    if not requested or sum(t in snap["requested_set"] for t in requested) < 0.98 * len(requested):
        return None
    if not {"Composite Emerging", "Market Cap Num", "Composite+ Core"} <= set(snap["df"].columns):
        return None                       # saved by an older app version: scan live instead
    by_ticker = snap["by_ticker"]
    present = [t for t in requested if t in by_ticker.index]
    df = by_ticker.loc[present].reset_index(drop=True)
    df.attrs["snapshot_ts"] = snap["ts"]
    return df


def snapshot_job_tickers():
    """What the background job scans: the Market Coverage list, the Track Record list and every sector page's pool."""
    lists = [SCAN_TICKERS, COVERAGE_LISTS.get(TRACK_COVERAGE, []), SECTOR_BASE_TICKERS]
    return list(dict.fromkeys(t for lst in lists for t in lst))


def get_persistent_universe_data(sector_name, tf_window="Current"):
    """Every scanned stock for a table page ('All Sectors' = the Market Coverage list, otherwise that sector),
    with a coverage report attached as df.attrs['coverage']."""
    requested = scan_request(sector_name)
    cache_key = f"uscan_{coverage_key}_{sector_name}_{tf_window}_{len(requested)}"
    df = _session_store_get(cache_key)
    snapshot_ts = None
    if df is None:
        df = snapshot_rows(requested) if tf_window == "Current" else None
        if df is not None:
            snapshot_ts = df.attrs.get("snapshot_ts")
        else:
            df = run_universe_scan(tuple(requested), tf_window)
        if not df.empty:
            _session_store_put(cache_key, df)
    else:
        snapshot_ts = df.attrs.get("snapshot_ts")
    loaded = set(df["Ticker"]) if not df.empty else set()
    other_sector = 0
    if sector_name and sector_name != "All Sectors" and not df.empty:
        in_sector = df["Sector"] == sector_name
        other_sector = int((~in_sector).sum())
        df = df[in_sector]
    df = df.reset_index(drop=True)
    sources = df["Social Source"].fillna("live").value_counts().to_dict() if "Social Source" in df.columns else {"live": len(df)}
    scope = COVERAGE_LABELS[coverage_key] if (not sector_name or sector_name == "All Sectors") else f"{sector_name} candidates"
    df.attrs["coverage"] = {
        "requested": len(requested), "loaded": len(loaded), "kept": len(df), "scope": scope,
        "missing": [t for t in requested if t not in loaded], "other_sector": other_sector,
        "social": {k: int(sources.get(k, 0)) for k in ("live", "saved", "none")},
        "snapshot_ts": snapshot_ts,
    }
    return df


def describe_snapshot_age(ts):
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Chicago")
    except Exception:
        tz = datetime.timezone(datetime.timedelta(hours=-6))
    when = datetime.datetime.fromtimestamp(ts, tz)
    now = datetime.datetime.now(tz)
    stamp = when.strftime("%I:%M %p").lstrip("0") + " CT"
    if when.date() != now.date():
        stamp = when.strftime("%a %b %d, ").replace(" 0", " ") + stamp
    mins = max(0, int((now - when).total_seconds() // 60))
    ago = f"{mins} min ago" if mins < 90 else (f"{mins // 60} h ago" if mins < 48 * 60 else f"{mins // 1440} days ago")
    return (f"Data updated **{stamp}** ({ago}). Refreshes automatically every 30 minutes on weekdays during market "
            f"hours (8:30 AM–3:00 PM CT); 🔄 Refresh Data gets live data now.")


def show_coverage_report(df):
    cov = (getattr(df, "attrs", None) or {}).get("coverage")
    if not cov:
        return
    if cov.get("snapshot_ts"):
        st.caption(f"🕒 {describe_snapshot_age(cov['snapshot_ts'])}")
    parts = [f"📡 **Scanned {cov['loaded']} of {cov['requested']}** stocks ({cov['scope']})"]
    if cov["other_sector"]:
        parts.append(f"{cov['other_sector']} filed by Yahoo under another sector")
    if cov["missing"]:
        parts.append(f"**{len(cov['missing'])} unavailable**")
    soc = cov["social"]
    social_txt = (f"StockTwits: {soc['live']} fresh · {soc['saved']} saved earlier readings"
                  + (f" · **{soc['none']} not yet available**" if soc["none"] else ""))
    st.caption(" · ".join(parts) + " &nbsp;|&nbsp; " + social_txt)
    if cov["missing"] or soc["none"]:
        with st.expander("Coverage details", expanded=False):
            if cov["missing"]:
                st.markdown(
                    "**Unavailable** (no data from Yahoo: delisted, renamed or rate-limited; 🔄 Refresh Data retries them): "
                    + ", ".join(cov["missing"][:400]) + (" …" if len(cov["missing"]) > 400 else "")
                )
            if soc["none"]:
                st.markdown(
                    f"**StockTwits:** free access allows roughly {STOCKTWITS_HOURLY_BUDGET} requests an hour, so in a "
                    f"large scan some stocks don't have a social reading yet. Until they do, they are ranked on the analyst "
                    f"and upside factors alone (their Social Score shows blank) rather than at a made-up neutral value. "
                    f"Readings are saved and reused for up to {SOCIAL_REUSE_HOURS:.0f} hours, so the gaps fill in over the next few hours."
                )


if REFRESH_JOB:
    _job_started = time.time()
    _job_tickers = snapshot_job_tickers()
    _job_df = run_universe_scan(tuple(_job_tickers), "Current")
    _job_result = {"requested": len(_job_tickers), "loaded": int(len(_job_df)), "saved": False,
                   "social": _job_df["Social Source"].fillna("live").value_counts().to_dict()
                   if "Social Source" in _job_df.columns else {}}
    # A scan that mostly failed (Yahoo down / rate-limited) doesn't replace a good snapshot
    _previous = load_market_snapshot()
    _enough = len(_job_df) >= 0.6 * len(_job_tickers) or not _previous or len(_job_df) >= 0.9 * len(_previous["df"])
    if not _job_df.empty and _enough:
        _job_result["saved"] = save_market_snapshot(_job_df, _job_tickers, coverage_key)
        _job_df = _ensure_leverage_columns(_job_df)
        record_score_observations(_job_df)
        record_scan_metric_snapshots(_job_df)
    _job_result["seconds"] = round(time.time() - _job_started, 1)
    st.session_state["_refresh_result"] = _job_result
    st.stop()


# ==============================================================================
# SIDEBAR MASTER DATA EXPORT (TOP 500 COMPREHENSIVE CSV)
# ==============================================================================
st.sidebar.divider()
st.sidebar.header("📥 Data Export")
# Filled later by whichever table page is showing (the sidebar renders before the page body)
table_csv_slot = st.sidebar.empty()
st.sidebar.caption(
    "Generate and download a comprehensive master CSV compiling all fundamental, analyst, "
    "social sentiment, valuation, PEG, and Beta metrics for every stock in your Market Coverage scan."
)

if "master_csv_bytes" not in st.session_state:
    st.session_state["master_csv_bytes"] = None

if st.sidebar.button(f"📊 Prepare Master CSV ({len(SCAN_TICKERS)} stocks)", type="secondary", **STRETCH):
    with st.sidebar.status(f"Compiling master data for {len(SCAN_TICKERS)} stocks...", expanded=False):
        df_master = get_persistent_universe_data("All Sectors", "Current")
        if not df_master.empty:
            ranked_master = df_master.sort_values(by="Composite Index", ascending=False).reset_index(drop=True)
            ranked_master.index = ranked_master.index + 1
            
            master_cols_order = [
                "Ticker", "Company", "Sector", "Industry",
                "Price", "Market Cap", "Net Debt/EBITDA", "Debt/Equity", "Forward PE", "PEG", "Beta", "Sector PE", "PE Discount %",
                "52W Low", "52W High",
                "Composite Index", "Composite Core", "Composite Growth", "Composite Emerging",
                "Analysts", "Coverage Status",
                "Analyst Buy %", "Raw Buy %", "Consensus Rating",
                "Strong Buy", "Buy", "Hold", "Sell", "Strong Sell",
                "Target_Num", "Upside", "Upside Score", "Upside Status",
                "Composite+ Core", "Composite+ Emerging", "Fundamentals", "Fundamentals Coverage %", "Red Flags",
                "Fwd PEG", "FCF Yield %", "Revenue Growth %",
                "Social Score", "Sentiment", "Buzz", "Polarity %", "Msgs/Hr", "Reddit Mentions", "Reddit 24h Change",
                "Watchers", "Trend Score", "Trend Rank", "Trending Status",
                "Mentions", "Upvotes", "Bullish Mentions", "Bearish Mentions",
            ]
            
            clean_master_cols = [c for c in master_cols_order if c in ranked_master.columns]
            rename_master_cols = {
                "Target_Num": "12M Mean Target Price",
                "Upside": "Predicted Upside %",
                "Forward PE": "Forward P/E",
                "Sector PE": "Sector Benchmark P/E",
                "PE Discount %": "Sector P/E Discount %",
                "Composite Index": "Composite Index (Growth)",
                "Composite Core": "Composite Index (Large-Cap / Institutional)",
                "Composite Emerging": "Composite Index (Emerging Growth)",
            }
            
            export_df = ranked_master[clean_master_cols].rename(columns=rename_master_cols)
            st.session_state["master_csv_bytes"] = export_df.to_csv(index=True, index_label="Rank").encode("utf-8")
            st.sidebar.success(f"✓ Master dataset ready ({len(export_df)} stocks)!")

if st.session_state["master_csv_bytes"] is not None:
    st.sidebar.download_button(
        label="📥 Download Master CSV",
        data=st.session_state["master_csv_bytes"],
        file_name=f"stock_master_{coverage_key}_{now.strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
        type="primary",
        **STRETCH,
    )


# --- "🔄 Refresh Data" inside Streamlit's ⋮ menu (next to Print and the theme switch) ---
# Streamlit has no setting for custom menu items, so a tiny script adds one when the menu opens and connects it to
# the (hidden) sidebar Refresh button. If the menu ever looks different in a future Streamlit version, the script
# shows the sidebar button again instead, so refreshing is never lost.
MENU_REFRESH_SCRIPT = """
<script>
(function () {
  const doc = window.parent.document;
  const STYLE_ID = "rhino-hide-sidebar-refresh";
  function hideSidebarButton() {
    if (!doc.getElementById(STYLE_ID)) {
      const s = doc.createElement("style");
      s.id = STYLE_ID;
      s.textContent = ".st-key-btn_refresh_global { display: none !important; }";
      doc.head.appendChild(s);
    }
  }
  function showSidebarButton() {
    const s = doc.getElementById(STYLE_ID);
    if (s) s.remove();
  }
  function findPrintItem(menu) {
    const byId = menu.querySelector('[data-testid="stMainMenuItem-print"]');
    if (byId) return byId;
    for (const el of menu.querySelectorAll('[role="option"], [role="menuitem"]')) {
      if ((el.textContent || "").trim().startsWith("Print")) return el;
    }
    return null;
  }
  function relabel(node) {
    const walker = doc.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    let t;
    while ((t = walker.nextNode())) {
      if (t.nodeValue.trim() === "Print") { t.nodeValue = "🔄 Refresh Data"; return true; }
    }
    return false;
  }
  function inject() {
    const menu = doc.querySelector('[data-testid="stMainMenuPopover"]');
    if (!menu || menu.querySelector("[data-rhino-refresh]")) return;
    const printItem = findPrintItem(menu);
    const refreshButton = doc.querySelector(".st-key-btn_refresh_global button");
    if (!printItem || !refreshButton) { window.parent.__rhinoMenuFailed = true; showSidebarButton(); return; }
    const item = printItem.cloneNode(true);
    item.setAttribute("data-rhino-refresh", "1");
    item.setAttribute("data-testid", "stMainMenuItem-rhinoRefresh");
    item.removeAttribute("id");
    if (!relabel(item)) { window.parent.__rhinoMenuFailed = true; showSidebarButton(); return; }
    item.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      const toggle = doc.querySelector('[data-testid="stMainMenu"] button') || doc.querySelector('[data-testid="stMainMenu"]');
      if (toggle) toggle.click();                       // close the menu
      const btn = doc.querySelector(".st-key-btn_refresh_global button");
      if (btn) btn.click();                             // same action as the old sidebar button
    }, true);
    printItem.parentNode.insertBefore(item, printItem);
  }
  // The page may still be drawing when this runs, so keep watching: hide the sidebar button once it exists,
  // and add the menu item whenever the menu opens
  function check() {
    if (doc.querySelector(".st-key-btn_refresh_global button") && !window.parent.__rhinoMenuFailed) hideSidebarButton();
    inject();
  }
  if (window.parent.__rhinoMenuObserver) window.parent.__rhinoMenuObserver.disconnect();
  const observer = new MutationObserver(check);
  check();
  observer.observe(doc.body, { childList: true, subtree: true });
  window.parent.__rhinoMenuObserver = observer;
})();
</script>
"""
with st.sidebar:
    if hasattr(st, "iframe"):      # newer Streamlit replaces components.html with st.iframe
        st.iframe(MENU_REFRESH_SCRIPT, height=1)
    else:
        components.html(MENU_REFRESH_SCRIPT, height=0)


# Common compact column configurations
COMMON_TABLE_CONFIG = {
    "Ticker": st.column_config.TextColumn("Ticker", width="small"),
    "Company": st.column_config.TextColumn("Company", width="medium"),
    "Sector": st.column_config.TextColumn("Sector", width="medium"),
    "Price": st.column_config.TextColumn("Price", width="small"),
    "Target": st.column_config.NumberColumn("12M Target", format="$%.2f", width="small"),
    "Target_Num": st.column_config.NumberColumn("12M Target", format="$%.2f", width="small",
                                                help="Mean 12-month analyst price target."),
    "Raw Buy %": st.column_config.NumberColumn("Raw Buy %", format="%.1f%%", width="small",
                                               help="Strong Buy + Buy ratings as a share of all ratings, before any adjustment."),
    "Consensus Rating": st.column_config.TextColumn("Consensus", width="small"),
    "Strong Buy": st.column_config.NumberColumn("Strong Buy", format="%d", width="small"),
    "Buy": st.column_config.NumberColumn("Buy", format="%d", width="small"),
    "Hold": st.column_config.NumberColumn("Hold", format="%d", width="small"),
    "Sell": st.column_config.NumberColumn("Sell", format="%d", width="small"),
    "Strong Sell": st.column_config.NumberColumn("Strong Sell", format="%d", width="small"),
    "Upside Status": st.column_config.TextColumn("Target Check", width="medium",
                                                 help="Whether the price target was used as-is or capped by a guardrail (thin coverage, stale target, distress)."),
    "Forward PE": st.column_config.NumberColumn("Fwd P/E", format="%.1fx", width="small"),
    "PEG": st.column_config.NumberColumn("PEG", format="%.2fx", width="small"),
    "Net Debt/EBITDA": st.column_config.NumberColumn("Net Debt/EBITDA", format="%.1fx", width="small", help="(Debt - Cash) / EBITDA. Below 0 = net cash; above 3x = high leverage. Blank when EBITDA is negative or missing."),
    "Debt/Equity": st.column_config.NumberColumn("Debt/Equity", format="%.2fx", width="small", help="Total debt / shareholder equity. Blank when equity is negative or missing."),
    "Beta": st.column_config.NumberColumn("Beta", format="%.2f", width="small"),
    "Sector PE": st.column_config.NumberColumn("Sector P/E", format="%.1fx", width="small"),
    "PE Discount %": st.column_config.NumberColumn("Discount %", format="%+.1f%%", width="small"),
    "Classic Composite": st.column_config.NumberColumn("Classic", format="%.1f", width="small",
                                                       help="This tab's original score (analysts + upside + social), for comparison."),
    "Fundamentals": st.column_config.ProgressColumn("Fundamentals", format="%.0f", min_value=0, max_value=100, width="small",
                                                    help="0-100 from debt (25%), sector P/E (15%), PEG (10%), forward PEG (10%), "
                                                         "free cash flow (20%) and revenue growth (20%). Pulled toward 50 when figures are missing."),
    "Red Flags": st.column_config.TextColumn("Red Flags", width="medium",
                                             help="High debt, burning cash, shrinking revenue or unprofitable: each costs 5 points of Composite+."),
    "Fwd PEG": st.column_config.NumberColumn("Fwd PEG", format="%.2fx", width="small",
                                             help="Forward P/E / expected EPS growth. Under 1x = cheap for its growth."),
    "FCF Yield %": st.column_config.NumberColumn("FCF Yield", format="%+.1f%%", width="small",
                                                 help="Free cash flow / market cap. Negative = burning cash."),
    "Revenue Growth %": st.column_config.NumberColumn("Rev Growth", format="%+.1f%%", width="small",
                                                      help="Year-over-year revenue growth (latest quarter)."),
    "Composite Index": st.column_config.NumberColumn("Composite", format="%.1f", width="small"),
    "Score Now": st.column_config.NumberColumn("Score Now", format="%.1f", width="small",
                                               help="This scan's Composite score, before any history weighting."),
    "Confidence %": st.column_config.ProgressColumn("History %", format="%d%%", min_value=0, max_value=100, width="small",
                                                    help="How much of the smoothed score comes from this stock's own history. "
                                                         "Low for new stocks (pulled toward the median); grows as it keeps being scanned, "
                                                         "and fades if it stops appearing."),
    "Days Seen": st.column_config.NumberColumn("Days Seen", format="%d", width="small",
                                               help="Number of different days this stock has been scanned (last 90 days)."),
    "Analysts": st.column_config.NumberColumn("Analysts", format="%d", width="small"),
    "Social Score": st.column_config.NumberColumn("Social Score", format="%.1f", width="small",
                                                  help="Sentiment x Buzz (0-100). 50 = typical; high = strongly bullish with heavy chatter."),
    "Sentiment": st.column_config.NumberColumn("Sentiment", format="%.0f", width="small",
                                               help="How bullish StockTwits posts are vs a typical stock (50), weighted by author reach and sample size."),
    "Buzz": st.column_config.ProgressColumn("Buzz", format="%.0f", min_value=0, max_value=100, width="small",
                                            help="Attention (0-100): StockTwits posts per hour, Reddit mentions, likes per post, rising mentions/watchers, trending rank."),
    "Msgs/Hr": st.column_config.NumberColumn("Posts/Hr", format="%.1f", width="small",
                                             help="StockTwits posts per hour, measured over its latest ~30 posts."),
    "Reddit Mentions": st.column_config.NumberColumn("Reddit 24h", format="%d", width="small",
                                                     help="Mentions in the last 24 hours across Reddit's stock communities (via ApeWisdom). 0 = not mentioned."),
    "Reddit 24h Change": st.column_config.NumberColumn("Reddit Δ", format="%+d", width="small",
                                                       help="Change in Reddit mentions vs the previous 24 hours."),
    "Watchers": st.column_config.NumberColumn("Watchers", format="%d", width="small",
                                              help="StockTwits users watching this stock."),
    "Analyst Buy %": st.column_config.NumberColumn("Analyst Buy %", format="%.1f%%", width="small",
                                                   help="Adjusted buy rating: thin coverage is pulled toward a 65% market average (Bayesian shrinkage), so 1-2 analysts can't dominate."),
    "Spread": st.column_config.NumberColumn("Spread", format="%+.1f", width="small"),
    "Upside": st.column_config.NumberColumn("Upside", format="%+.1f%%", width="small"),
    "Period Exp. Return %": st.column_config.TextColumn("Period Exp. Return", width="medium"),
    "Allocation $": st.column_config.NumberColumn("Allocation", format="$%.0f", width="small"),
    "Projected Value $": st.column_config.NumberColumn("Projected Value", format="$%.0f", width="small"),
    "Polarity %": st.column_config.NumberColumn("Bullish %", format="%.1f%%", width="small",
                                                help="Bullish share of StockTwits posts, adjusted for sample size (a few posts can't make it 100%)."),
    "Trend Score": st.column_config.NumberColumn("Trend Score", format="%.1f", width="small"),
    "Trend Rank": st.column_config.TextColumn("Rank", width="small"),
    "Mentions": st.column_config.NumberColumn("Mentions", format="%d", width="small"),
    "Upvotes": st.column_config.NumberColumn("Upvotes", format="%d", width="small"),
    "Bullish Mentions": st.column_config.NumberColumn("Bullish Tags", format="%d", width="small"),
    "Bearish Mentions": st.column_config.NumberColumn("Bearish Tags", format="%d", width="small"),
    "Predicted Basket %": st.column_config.TextColumn("Predicted Basket", width="medium"),
    "Historical Realized %": st.column_config.TextColumn("Realized Basket", width="medium"),
    "Historical S&P 500 %": st.column_config.TextColumn("S&P 500 Return", width="medium"),
    "Predicted Alpha %": st.column_config.NumberColumn("Predicted Alpha", format="%+.1f%%", width="small"),
    "Realized Alpha %": st.column_config.NumberColumn("Realized Alpha", format="%+.1f%%", width="small"),
    "Realization Rate %": st.column_config.NumberColumn("Realization Rate", format="%+.1f%%", width="small"),
    "Signal Status": st.column_config.TextColumn("Signal Status", width="medium"),
    "View": st.column_config.LinkColumn(
        "Inspect",
        display_text="🔍",
        width=65,
    ),
}


# ==============================================================================
# VIEW 1: SINGLE STOCK DEEP DIVE (BALANCED 2-ROW X 5-COLUMN GRID + DIRECT NEWS)
# ==============================================================================
if app_mode == "📊 Single Stock Deep Dive":
    active_ticker = st.session_state.get("selected_ticker", "MU")

    with st.spinner(f"Loading live market and social data for {active_ticker}..."):
        stock_info, analyst_info = fetch_stock_data(active_ticker)
        st_social = fetch_stocktwits_sentiment(active_ticker, max_age_hours=0.5)
        flush_social_cache()

    if not stock_info:
        st.error(f"Could not retrieve market data for symbol **{active_ticker}**.")
    else:
        company_name = stock_info.get("longName") or stock_info.get("shortName") or active_ticker
        sector = stock_info.get("sector", "N/A")
        industry = stock_info.get("industry", "N/A")
        currency = stock_info.get("currency", "USD")
        current_price = stock_info.get("currentPrice") or stock_info.get("regularMarketPrice")

        # LINE 1: Top navigation button row
        nav_l, _ = st.columns([1.5, 8.5])
        with nav_l:
            if st.button("← Back", key="btn_top_back", type="secondary", **STRETCH):
                go_back()

        # LINE 2: Stock Title immediately under buttons
        day = describe_daily_change(stock_info)
        # "&#36;" instead of "$" so Streamlit doesn't read prices as a math formula
        title_bits = [f'<span class="dd-title-name">{html.escape(str(company_name))} ({html.escape(active_ticker)})</span>']
        if day["price"]:
            title_bits.append(f'<span class="dd-title-price">&#36;{day["price"]:,.2f}</span>')
        if day["change"] is not None and day["pct"] is not None:
            arrow = {"up": "▲", "down": "▼", "flat": "■"}[day["direction"]]
            sign = "+" if day["change"] >= 0 else "−"
            title_bits.append(
                f'<span class="dd-change {day["direction"]}">{arrow} {sign}&#36;{abs(day["change"]):,.2f} '
                f'({sign}{abs(day["pct"]):.2f}%) today</span>'
            )
            if day["as_of"]:
                title_bits.append(f'<span class="dd-change-note">as of {html.escape(day["as_of"])} CT</span>')
        else:
            title_bits.append('<span class="dd-change-note">Daily change unavailable</span>')
        st.markdown(f'<div class="dd-title-row">{"".join(title_bits)}</div>', unsafe_allow_html=True)

        employees = stock_info.get("fullTimeEmployees")
        emp_str = f" • **Employees:** {employees:,}" if employees else ""
        city = stock_info.get("city")
        state = stock_info.get("state")
        country = stock_info.get("country")
        hq_parts = [p for p in [city, state, country] if p]
        hq_str = f" • **HQ:** {', '.join(hq_parts)}" if hq_parts else ""

        st.caption(f"**Sector:** {sector} &nbsp;|&nbsp; **Industry:** {industry}{emp_str}{hq_str} &nbsp;|&nbsp; **Currency:** {currency}")

        summary_raw = stock_info.get("longBusinessSummary", "")
        if summary_raw:
            sentences = summary_raw.split(". ")
            preview_bio = ". ".join(sentences[:2]).strip()
            if not preview_bio.endswith("."):
                preview_bio += "."
            
            with st.expander("🏢 **Company Overview & Business Profile**", expanded=False):
                st.write(preview_bio)
                if len(sentences) > 2:
                    remaining_bio = ". ".join(sentences[2:]).strip()
                    st.caption(remaining_bio)

        # Basic stock data & valuation calculations
        mkt_cap = format_market_cap(stock_info.get("marketCap"))
        leverage = describe_leverage(stock_info)
        stock_fwd_pe = stock_info.get("forwardPE")
        fwd_pe_str = format_ratio(stock_fwd_pe)
        price_str = f"${current_price:,.2f}" if current_price else "N/A"
        fifty_two_low = stock_info.get("fiftyTwoWeekLow")
        fifty_two_high = stock_info.get("fiftyTwoWeekHigh")
        # one "$" only: two would make Streamlit read "$84 - $156" as a math formula (and "\\$" shows on older versions)
        range_str = f"${fifty_two_low:.2f} – {fifty_two_high:.2f}" if (fifty_two_low and fifty_two_high) else "N/A"
        _cap_ctx = market_cap_peer_context(active_ticker, sector, industry, _clean_number(stock_info.get("marketCap")))
        st.markdown(
            "<style>svg[data-testid^=\"stMetricDeltaIcon\"] { display: none !important; } "
            + market_cap_color_css(_cap_ctx) + "</style>",
            unsafe_allow_html=True,
        )

        upside_val = analyst_info.get("predicted_upside")
        upside_score_val = analyst_info.get("upside_score")
        upside_status_str = analyst_info.get("upside_status")
        if upside_val is not None and analyst_info.get("target_mean"):
            upside_str = f"{upside_val:+.1f}%"
            target_str = f"12M Target: ${analyst_info['target_mean']:.2f}"
        else:
            upside_str = "N/A"
            target_str = "No Consensus Target"

        sector_benchmark_pe = SECTOR_FORWARD_PE_BENCHMARKS.get(sector, 21.5)
        if stock_fwd_pe and stock_fwd_pe > 0:
            pe_spread = stock_fwd_pe - sector_benchmark_pe
            pe_pct_diff = (pe_spread / sector_benchmark_pe) * 100.0
            delta_val_str = f"{pe_spread:+.1f}x ({pe_pct_diff:+.1f}%)"
            delta_label = "Premium vs Sector" if pe_spread > 0 else "Discount vs Sector"
            delta_color_choice = "inverse" if pe_spread > 0 else "normal"
        else:
            delta_val_str = "N/A"
            delta_label = "No Fwd P/E"
            delta_color_choice = "off"

        # ROW 1 (5 METRICS): Core Price, Size & 12M Horizon
        f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns(5)
        f_col1.metric("Current Price", price_str)
        with f_col2:
            with st.container(key="mcap_metric"):
                st.metric(
                    "Market Cap", mkt_cap,
                    delta=_cap_ctx.get("label") or _cap_ctx.get("size_class") or None,
                    delta_color="off",
                    help=market_cap_help(_cap_ctx),
                )
        f_col3.metric(
            label=leverage["label"],
            value=leverage["value"],
            delta=leverage["delta"],
            delta_color=leverage["delta_color"],
            help=leverage["help"],
        )
        f_col4.metric("52-Week Range", range_str)
        f_col5.metric(
            label="Predicted Upside",
            value=upside_str,
            delta=f"{target_str} ({upside_status_str})",
            delta_color="normal" if upside_val and upside_val >= 0 else "inverse",
            help="Consensus 12-Month horizon with thin-coverage outlier guardrails applied.",
        )

        # ROW 2 (5 METRICS): Valuation Multiples, Growth & Risk
        v_col1, v_col2, v_col3, v_col4, v_col5 = st.columns(5)
        with v_col1:
            st.metric(
                label="Forward P/E",
                value=f"{stock_fwd_pe:.2f}x" if stock_fwd_pe else "N/A",
                help="Stock price divided by consensus estimated next 12-month EPS.",
            )
        with v_col2:
            st.metric(
                label="Sector Benchmark Fwd P/E",
                value=f"{sector_benchmark_pe:.1f}x",
                delta=f"Sector: {sector}",
                delta_color="off",
                help=f"Consensus forward P/E benchmark for the {sector} sector.",
            )
        with v_col3:
            st.metric(
                label="Sector P/E Delta",
                value=delta_val_str,
                delta=delta_label,
                delta_color=delta_color_choice,
                help="Green = discount relative to sector benchmark; Red = valuation premium.",
            )
        with v_col4:
            peg_val = stock_info.get("pegRatio") or stock_info.get("trailingPegRatio")
            if peg_val and peg_val > 0:
                peg_str = f"{peg_val:.2f}x"
                if peg_val < 1.0:
                    peg_delta = "Undervalued Growth (<1.0)"
                    peg_color = "normal"
                elif peg_val <= 1.5:
                    peg_delta = "Fair Value (1.0 - 1.5)"
                    peg_color = "off"
                else:
                    peg_delta = "Growth Premium (>1.5)"
                    peg_color = "inverse"
            else:
                peg_str = "N/A"
                peg_delta = "No Growth Est"
                peg_color = "off"
            st.metric(
                label="PEG Ratio",
                value=peg_str,
                delta=peg_delta,
                delta_color=peg_color,
                help="Price/Earnings to Growth ratio. Values under 1.0 indicate undervalued growth relative to expected earnings expansion.",
            )
        with v_col5:
            beta_val = stock_info.get("beta")
            if beta_val is not None and not np.isnan(beta_val):
                beta_str = f"{beta_val:.2f}"
                if beta_val > 1.30:
                    beta_delta = "High Volatility (>1.30)"
                    beta_color = "inverse"
                elif beta_val >= 0.85:
                    beta_delta = "Market-Paced Volatility"
                    beta_color = "off"
                else:
                    beta_delta = "Defensive / Stable (<0.85)"
                    beta_color = "normal"
            else:
                beta_str = "N/A"
                beta_delta = "No Beta Data"
                beta_color = "off"
            st.metric(
                label="Beta (5Y Monthly)",
                value=beta_str,
                delta=beta_delta,
                delta_color=beta_color,
                help="Measures systemic volatility relative to the S&P 500 (Beta = 1.0). Beta > 1.0 indicates greater price amplification than the market.",
            )

        # ROW 3 (5 METRICS): Growth-Adjusted Valuation, Cash Generation & Positioning
        for k_col, k_metric in zip(st.columns(5), describe_key_market_metrics(stock_info)):
            k_col.metric(
                label=k_metric["label"],
                value=k_metric["value"],
                delta=k_metric["delta"],
                delta_color=k_metric["delta_color"],
                help=k_metric["help"],
            )

        # Today's reading of every metric on this page, saved once per visit per day for the Stock History charts
        _snap_key = f"metric_snap_{active_ticker}_{_market_date(time.time())}"
        if not st.session_state.get(_snap_key):
            record_metric_snapshots([deep_dive_metric_record(active_ticker, stock_info, analyst_info)])
            st.session_state[_snap_key] = True

        # Stock History Expander: any metric on this page over time (defaults to Stock Price, Max)
        def _keep_history_open():
            # older Streamlit versions fold an expander back up whenever a widget inside it changes
            st.session_state["hist_keep_open"] = True

        with st.expander("📈 **Stock History**", expanded=bool(st.session_state.get("hist_keep_open"))):
            p_col1, p_col2 = st.columns([4.2, 5.8])
            with p_col1:
                history_metric = st.selectbox(
                    "Metric:",
                    options=[m[0] for m in HISTORY_METRICS],
                    index=0,
                    format_func=lambda k: HISTORY_METRIC_INFO[k]["label"],
                    key="hist_metric",
                    on_change=_keep_history_open,
                    help="Standard stock data over time, from Yahoo Finance.",
                )
            with p_col2:
                price_interval = st.radio(
                    "Timeframe:",
                    options=["1D", "1W", "1M", "1Y", "5Y", "Max"],
                    index=5,
                    horizontal=True,
                    key="hist_price_interval",
                    on_change=_keep_history_open,
                )

            if history_metric == "price":
                with st.spinner(f"Loading {price_interval} price candles for {active_ticker}..."):
                    hist_df = fetch_stock_historical_prices(active_ticker, price_interval)

                if hist_df.empty:
                    st.warning(f"Historical price data is unavailable for {active_ticker} ({price_interval}).")
                else:
                    first_close = hist_df["Close"].iloc[0]
                    last_close = hist_df["Close"].iloc[-1]
                    net_change = last_close - first_close
                    pct_change = (net_change / first_close) * 100.0 if first_close > 0 else 0.0

                    line_color = "#00FFAA" if net_change >= 0 else "#FF4A4A"
                    fill_color = "rgba(0, 255, 170, 0.08)" if net_change >= 0 else "rgba(255, 74, 74, 0.08)"

                    fig_price = go.Figure()
                    fig_price.add_trace(go.Scatter(
                        x=hist_df["timestamp"],
                        y=hist_df["Close"],
                        mode="lines",
                        name="Close Price",
                        line=dict(color=line_color, width=2.5),
                        fill="tozeroy",
                        fillcolor=fill_color,
                        hovertemplate="<b>%{x}</b><br>Price: <b>$%{y:.2f}</b><extra></extra>",
                    ))

                    delta_prefix = "+" if net_change >= 0 else ""
                    chart_subtitle = f"{active_ticker} Price: ${last_close:,.2f} ({delta_prefix}{pct_change:.2f}%) over {price_interval}"

                    fig_price.update_layout(
                        title=dict(text=chart_subtitle, font=dict(size=16)),
                        xaxis_title="Date / Time",
                        yaxis_title="Stock Price (USD)",
                        yaxis=dict(tickprefix="$", tickformat=",.2f"),
                        hovermode="x unified",
                        margin=dict(l=20, r=20, t=50, b=20),
                        template="plotly_dark",
                    )
                    try:
                        st.plotly_chart(fig_price, **STRETCH)
                    except TypeError:
                        st.plotly_chart(fig_price, width="stretch")
            elif history_metric == "volume":
                with st.spinner(f"Loading {price_interval} volume for {active_ticker}..."):
                    vol_df = fetch_stock_historical_prices(active_ticker, price_interval)
                if vol_df.empty or "Volume" not in vol_df.columns:
                    st.warning(f"Volume data is unavailable for {active_ticker} ({price_interval}).")
                else:
                    bar_len = {"1D": "5 minutes", "1W": "30 minutes", "1M": "day", "1Y": "day", "5Y": "week", "Max": "month"}[price_interval]
                    fig_vol = go.Figure(go.Bar(x=vol_df["timestamp"], y=vol_df["Volume"], marker_color="#4DA3FF",
                                               hovertemplate="<b>%{x}</b><br>Volume: <b>%{y:,.0f}</b><extra></extra>"))
                    fig_vol.update_layout(
                        title=dict(text=f"{active_ticker} shares traded per {bar_len} ({price_interval})", font=dict(size=16)),
                        xaxis_title="Date / Time", yaxis_title="Shares traded", hovermode="x unified",
                        margin=dict(l=20, r=20, t=50, b=20), template="plotly_dark",
                    )
                    try:
                        st.plotly_chart(fig_vol, **STRETCH)
                    except TypeError:
                        st.plotly_chart(fig_vol, width="stretch")
                    st.caption(f"Source: {HISTORY_METRIC_INFO['volume']['source']}")
            else:
                render_metric_history(active_ticker, company_name, history_metric, price_interval,
                                      stock_info, sector_benchmark_pe)

        # TOP NEWS STORIES EXPANDER (JUST UNDER STOCK PRICE HISTORY)
        stock_news_list = fetch_stock_news(active_ticker, company_name)
        news_count = len(stock_news_list)
        expander_label = f"📰 **Top {news_count} News Stories**" if news_count > 0 else "📰 **Top News Stories**"

        with st.expander(expander_label, expanded=False):
            if not stock_news_list:
                st.info(
                    f"No recent news stories could be retrieved for **{active_ticker}** right now. "
                    f"You can browse coverage directly on [Yahoo Finance](https://finance.yahoo.com/quote/{active_ticker}/news/)."
                )
            else:
                for n_item in stock_news_list:
                    safe_title = html.escape(n_item["title"]).replace("$", "&#36;")
                    safe_link = html.escape(n_item["link"], quote=True)
                    safe_pub = html.escape(n_item["publisher"])
                    time_tag = f" &nbsp;&bull;&nbsp; {html.escape(n_item['time_ago'])}" if n_item.get("time_ago") else ""
                    st.markdown(
                        f'<div class="news-card">'
                        f'<a class="news-title" href="{safe_link}" target="_blank" rel="noopener noreferrer">{safe_title}</a>'
                        f'<div class="news-meta">Source: <b>{safe_pub}</b>{time_tag}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

        st.divider()

        # Sentiment & Analyst Indicators
        buy_percentage = analyst_info["buy_pct"]
        raw_buy = analyst_info["raw_buy_pct"]
        total_analysts = analyst_info["analyst_count"]
        coverage_status = analyst_info["coverage_status"]

        st.markdown("### 🎯 Key Sentiment & Analyst Indicators")
        if st_social.get("source") == "none":
            st.warning("StockTwits data is currently unavailable, so the Social Media Index below is a neutral placeholder rather than a live reading.")
        elif st_social.get("source") == "saved" and (st_social.get("age_h") or 0) >= 1:
            st.caption(f"ℹ️ StockTwits reading from about {st_social['age_h']:.0f} hour(s) ago (live requests are rationed to stay within StockTwits' free limit).")
        kpi1, kpi2, kpi3 = st.columns([1.2, 1, 1])

        live_comp = calculate_composite_index(st_social["score"] if st_social.get("source") != "none" else None,
                                              buy_percentage, upside_score_val, mode="growth")

        with kpi1:
            st.metric(
                "⭐ Composite Index",
                f"{live_comp:.1f}",
                help=COMPOSITE_CALC_EXPLANATION,
            )
            st.markdown(f"**Classification:** {score_badge_100(live_comp)} *(Scale: 0–100)*")

        with kpi2:
            st.metric("💬 Social Media Index", f"{st_social['score']:.1f}",
                      delta=(f"Sentiment {st_social.get('sentiment_score', 50):.0f} · Buzz {st_social.get('buzz_score', 50):.0f}"
                             if st_social.get("source") != "none" else None),
                      delta_color="off", help=SOCIAL_SCORE_EXPLANATION)
            trend_badge = f"🔥 #{st_social['trending_rank']}" if st_social["is_trending"] else "Active Stream"
            reddit_txt = (f" &bull; Reddit: {st_social['reddit_mentions']:,} mentions (24h)"
                          if st_social.get("reddit_mentions") is not None else "")
            st.markdown(f"**Social Momentum:** {score_badge_100(st_social['score'])} &bull; {trend_badge}{reddit_txt}")

        with kpi3:
            st.metric(
                "🎯 Analyst Buy Rating (Adjusted)",
                f"{buy_percentage:.1f}%",
                delta=f"Raw: {raw_buy:.1f}% ({total_analysts} analysts)",
                delta_color="off",
                help=f"Consensus: {analyst_info['consensus']}. Uses Bayesian shrinkage toward 65% market prior to normalize thin coverage.",
            )
            st.markdown(f"**Coverage:** **{total_analysts} Analysts** &bull; {coverage_status}")

        sb = analyst_info.get("strong_buy", 0)
        b = analyst_info.get("buy", 0)
        h = analyst_info.get("hold", 0)
        s = analyst_info.get("sell", 0)
        ss = analyst_info.get("strong_sell", 0)
        sum_recs = sb + b + h + s + ss

        st.markdown("#### 🏛️ Wall Street Consensus Breakdown")
        if sum_recs > 0:
            rec_c1, rec_c2, rec_c3, rec_c4, rec_c5 = st.columns(5)
            rec_c1.metric("Strong Buy", f"{sb}", delta="🟢 Bullish", delta_color="normal")
            rec_c2.metric("Buy", f"{b}", delta="🟢 Bullish", delta_color="normal")
            rec_c3.metric("Hold", f"{h}", delta="🟡 Neutral", delta_color="off")
            rec_c4.metric("Sell", f"{s}", delta="🔴 Bearish", delta_color="inverse")
            rec_c5.metric("Strong Sell", f"{ss}", delta="🔴 Bearish", delta_color="inverse")

            pct_bull = ((sb + b) / sum_recs)
            st.progress(pct_bull, text=f"Buy Recommendations: {pct_bull*100:.1f}% of total ({sb+b}/{sum_recs} analysts)")
        else:
            st.info(f"Detailed granular recommendation distribution is unavailable. Overall consensus is **{analyst_info['consensus']}** across **{total_analysts}** analysts.")

        # Composite Sentiment Expander
        with st.expander("🎯 **Composite & Sentiment Indicators Graph**", expanded=True):
            s_col1, _ = st.columns([4.0, 6.0])
            with s_col1:
                sentiment_timeframe = st.radio(
                    "Sentiment Timeframe:",
                    options=["Current", "24 Hours", "7 Days", "30 Days"],
                    index=1,
                    horizontal=True,
                    key="internal_sentiment_tf",
                )

            if sentiment_timeframe == "Current":
                s_start_time = now - datetime.timedelta(hours=6)
                s_freq = "15min"
                s_display_fmt = "%b %d, %H:%M UTC"
            elif sentiment_timeframe == "24 Hours":
                s_start_time = now - datetime.timedelta(hours=24)
                s_freq = "1h"
                s_display_fmt = "%b %d, %H:%M UTC"
            elif sentiment_timeframe == "7 Days":
                s_start_time = now - datetime.timedelta(days=7)
                s_freq = "4h"
                s_display_fmt = "%b %d, %H:%M UTC"
            else:  # 30 Days
                s_start_time = now - datetime.timedelta(days=30)
                s_freq = "1D"
                s_display_fmt = "%b %d, %Y"

            sentiment_df = generate_sentiment_and_composite(
                active_ticker, s_start_time, now, s_freq, st_social["score"], buy_percentage, upside_score_val
            )

            formatted_start = s_start_time.strftime(s_display_fmt)
            formatted_end = now.strftime(s_display_fmt)
            st.caption(f"📅 **Window:** {formatted_start} &rarr; {formatted_end} ({sentiment_timeframe})")

            fig_sent = go.Figure()
            fig_sent.add_hline(y=50.0, line_dash="dash", line_color="rgba(180, 180, 180, 0.4)", annotation_text="Neutral (50)", annotation_position="bottom right")
            fig_sent.add_hrect(y0=60.0, y1=100.0, fillcolor="rgba(46, 204, 113, 0.08)", line_width=0, layer="below")
            fig_sent.add_hrect(y0=0.0, y1=40.0, fillcolor="rgba(231, 76, 60, 0.08)", line_width=0, layer="below")

            fig_sent.add_trace(go.Scatter(
                x=sentiment_df["timestamp"], y=sentiment_df["composite_score"],
                mode="lines", name="Composite Index (Multi-Factor Model)", line=dict(color="#00FFAA", width=3.5),
                hovertemplate="<b>%{x|%Y-%m-%d %H:%M}</b><br>Composite Index: <b>%{y:.1f}</b><extra></extra>",
            ))
            fig_sent.add_trace(go.Scatter(
                x=sentiment_df["timestamp"], y=sentiment_df["social_sentiment"],
                mode="lines", name="Social Media Index", line=dict(color="#FF9900", width=3.5),
                hovertemplate="<b>%{x|%Y-%m-%d %H:%M}</b><br>Social Media Index: <b>%{y:.1f}</b><extra></extra>",
            ))
            fig_sent.add_trace(go.Scatter(
                x=sentiment_df["timestamp"], y=sentiment_df["analyst_buy_pct"],
                mode="lines", name="Analyst Buy % (Bayesian Adjusted)", line=dict(color="#FF3333", width=3.5, dash="dash"),
                hovertemplate="<b>%{x|%Y-%m-%d %H:%M}</b><br>Analyst Buy Rating: <b>%{y:.1f}%</b><extra></extra>",
            ))

            fig_sent.update_layout(
                title=dict(text=f"{company_name} ({active_ticker}): Composite Index, Social Sentiment & Analyst Buy %", font=dict(size=16)),
                xaxis_title="Time (UTC)", yaxis_title="Scale (0 to 100)",
                yaxis=dict(range=[0, 100], tickmode="linear", tick0=0, dtick=10),
                hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=20, r=20, t=60, b=20), template="plotly_dark",
            )
            try:
                st.plotly_chart(fig_sent, **STRETCH)
            except TypeError:
                st.plotly_chart(fig_sent, width="stretch")


# ==============================================================================
# VIEW 2: RECOMMENDED STOCKS (WITH FORWARD P/E, PEG & BETA)
# ==============================================================================
elif app_mode == "🏆 Recommended Stocks":
    REC_VIEW_LIVE = "⚡ Live Scan"
    REC_VIEW_SMOOTH = "🧠 Smoothed (History-Weighted)"
    REC_MODELS = {
        "classic": "📊 Classic (Analysts + Upside + Social)",
        "plus": "💎 Composite+ (Quality & Value)",
    }
    if st.session_state.get("rec_model") not in REC_MODELS:
        _saved_model = APP_SETTINGS.get("rec_model")
        st.session_state["rec_model"] = _saved_model if _saved_model in REC_MODELS else "classic"
    # Ranking View, then Ranking Model, then Sector
    mode_col, model_col, sec_col = st.columns([3.4, 3.3, 3.3])
    with model_col:
        rec_model = st.selectbox(
            "Ranking Model:",
            options=list(REC_MODELS),
            format_func=lambda k: REC_MODELS[k],
            key="rec_model",
            on_change=lambda: save_app_setting("rec_model", st.session_state["rec_model"]),
            help="Classic ranks on analyst ratings, price-target upside and social sentiment. Composite+ blends that "
                 "50/50 with a fundamentals score (low debt, valuation vs sector, PEG, forward PEG, free cash flow, "
                 "revenue growth) and takes 5 points off per red flag.",
        )
    is_plus = rec_model == "plus"
    with mode_col:
        rec_view = st.radio(
            "Ranking View:",
            options=[REC_VIEW_LIVE, REC_VIEW_SMOOTH],
            horizontal=True,
            key="rec_view_mode",
            help="Live Scan ranks on this scan alone, with no history. Smoothed blends in each stock's past scores "
                 "(3-day half-life), so one-day spikes count for less and consistently strong stocks rise.",
        )
    with sec_col:
        selected_sector = sector_selector("rec_sector")

    is_smoothed = rec_view == REC_VIEW_SMOOTH
    selected_tf = "Current"

    with st.spinner(f"Scanning {len(scan_request(selected_sector))} stocks (first load each hour can take a few minutes)..."):
        df_exchange = get_persistent_universe_data(selected_sector, selected_tf)
    show_coverage_report(df_exchange)

    if df_exchange.empty:
        st.warning("Data fetch in progress or timed out. Click 🔄 Refresh Data at the top of the sidebar.")
    elif not {"Composite Emerging", "Market Cap Num", "Composite+ Core"}.issubset(df_exchange.columns):
        # Scan cached by an older app version: rescan once so the tab scores exist
        st.session_state["store"].clear()
        st.cache_data.clear()
        st.rerun()
    else:
        df_rec_all = _ensure_leverage_columns(df_exchange)
        _live_data = not (df_rec_all.attrs.get("coverage") or {}).get("snapshot_ts")
        score_hist = record_score_observations(df_rec_all, write=_live_data)
        if _live_data:
            record_scan_metric_snapshots(df_rec_all)     # daily readings for the Deep Dive Stock History charts
        now_ts = time.time()

        inst_pool = df_rec_all[
            (df_rec_all["Analysts"] >= INST_MIN_ANALYSTS)
            & (df_rec_all["Market Cap Num"] >= INST_MIN_MARKET_CAP)
            # Profitable only: a missing, zero or negative forward P/E means no expected earnings
            & (pd.to_numeric(df_rec_all["Forward PE"], errors="coerce") > 0)
        ]
        emerging_pool = df_rec_all[df_rec_all["Analysts"] >= EMERGING_MIN_ANALYSTS]

        def size_pool(min_cap, max_cap, min_analysts, require_profit):
            pool = df_rec_all[
                (df_rec_all["Analysts"] >= min_analysts)
                & (df_rec_all["Market Cap Num"] >= min_cap)
                & (df_rec_all["Market Cap Num"] < max_cap)
            ]
            if require_profit:
                pool = pool[pd.to_numeric(pool["Forward PE"], errors="coerce") > 0]
            return pool

        mid_pool = size_pool(MID_MIN_MARKET_CAP, MID_MAX_MARKET_CAP, MID_MIN_ANALYSTS, MID_REQUIRE_PROFIT)
        small_pool = size_pool(SMALL_MIN_MARKET_CAP, SMALL_MAX_MARKET_CAP, SMALL_MIN_ANALYSTS, SMALL_REQUIRE_PROFIT)

        def rank_pool(pool, live_col, score_key, classic_col):
            scored = apply_smoothing(pool, live_col, score_key, score_hist, now_ts)
            scored["Classic Composite"] = scored[classic_col]
            live_rank = (scored.assign(**{"Composite Index": scored["Score Now"]})
                         .sort_values(by="Composite Index", ascending=False).head(scan_depth).reset_index(drop=True))
            smooth_rank = (scored.assign(**{"Composite Index": scored["Smoothed Score"]})
                           .sort_values(by=["Composite Index", "Confidence %"], ascending=False)
                           .head(scan_depth).reset_index(drop=True))
            return live_rank, smooth_rank

        inst_live, inst_smooth = rank_pool(inst_pool, "Composite Core", "core", "Composite Core")
        emer_live, emer_smooth = rank_pool(emerging_pool, "Composite Emerging", "emerging", "Composite Emerging")
        inst_plus_live, inst_plus_smooth = rank_pool(inst_pool, "Composite+ Core", "core_plus", "Composite Core")
        emer_plus_live, emer_plus_smooth = rank_pool(emerging_pool, "Composite+ Emerging", "emerging_plus", "Composite Emerging")
        # Mid-Cap scores like Large-Cap / Institutional, Small-Cap like Emerging Growth (see the constants at the top)
        mid_live, mid_smooth = rank_pool(mid_pool, "Composite Core", "core", "Composite Core")
        small_live, small_smooth = rank_pool(small_pool, "Composite Emerging", "emerging", "Composite Emerging")
        mid_plus_live, mid_plus_smooth = rank_pool(mid_pool, "Composite+ Core", "core_plus", "Composite Core")
        small_plus_live, small_plus_smooth = rank_pool(small_pool, "Composite+ Emerging", "emerging_plus", "Composite Emerging")

        # Track record: today's picks are saved once per market day, only from All Sectors at the pinned level,
        # so every day in the record is drawn from the same pool of stocks. Both models are tracked.
        if selected_sector == "All Sectors" and coverage_key == TRACK_COVERAGE:
            save_daily_snapshot({"inst_smooth": inst_smooth, "emer_smooth": emer_smooth,
                                 "inst_plus_smooth": inst_plus_smooth, "emer_plus_smooth": emer_plus_smooth,
                                 "mid_smooth": mid_smooth, "small_smooth": small_smooth,
                                 "mid_plus_smooth": mid_plus_smooth, "small_plus_smooth": small_plus_smooth}, now_ts,
                                dict(zip(df_rec_all["Ticker"], df_rec_all["Company"])),
                                coverage=coverage_key, source="daily scan" if DAILY_JOB else "page view")

        LIVE_COLS = [
            "Ticker", "Company", "Sector", "Price", "Market Cap",
            "Composite Index", "Forward PE", "PEG", "Net Debt/EBITDA", "Debt/Equity", "Beta",
            "Analysts", "Analyst Buy %", "Upside", "Social Score", "View",
        ]
        SMOOTH_COLS = [
            "Ticker", "Company", "Sector", "Price", "Market Cap",
            "Composite Index", "Score Now", "Confidence %", "Days Seen",
            "Forward PE", "PEG", "Net Debt/EBITDA", "Beta",
            "Analysts", "Analyst Buy %", "Upside", "Social Score", "View",
        ]
        PLUS_COLS = [
            "Ticker", "Company", "Sector", "Price", "Market Cap",
            "Composite Index", "Classic Composite", "Fundamentals", "Red Flags",
            "Net Debt/EBITDA", "PE Discount %", "PEG", "Fwd PEG", "FCF Yield %", "Revenue Growth %",
            "Analysts", "Analyst Buy %", "Upside", "Social Score", "View",
        ]
        PLUS_SMOOTH_COLS = PLUS_COLS[:6] + ["Score Now", "Confidence %", "Days Seen"] + PLUS_COLS[6:]
        if is_plus:
            display_cols = PLUS_SMOOTH_COLS if is_smoothed else PLUS_COLS
        else:
            display_cols = SMOOTH_COLS if is_smoothed else LIVE_COLS
        rec_config = dict(COMMON_TABLE_CONFIG)
        rec_config["PE Discount %"] = st.column_config.NumberColumn(
            "Sector P/E Delta", format="%+.1f%%", width="small",
            help="How much cheaper (+) or dearer (-) the forward P/E is than its sector benchmark.")
        if is_plus:
            rec_config["Composite Index"] = st.column_config.NumberColumn(
                "Composite+ (Smoothed)" if is_smoothed else "Composite+", format="%.1f", width="small",
                help="50% Classic score + 50% Fundamentals, minus 5 points per red flag"
                     + (" (history-weighted, 3-day half-life)." if is_smoothed else "."))
            rec_config["Score Now"] = st.column_config.NumberColumn(
                "Composite+ Now", format="%.1f", width="small", help="This scan's Composite+, before any history weighting.")
        elif is_smoothed:
            rec_config["Composite Index"] = st.column_config.NumberColumn(
                "Smoothed", format="%.1f", width="small",
                help="History-weighted Composite: this stock's time-decayed average score (3-day half-life), "
                     "pulled toward today's median when it has little history.")

        if is_plus:
            st.caption(
                "💎 **Composite+ (Quality & Value):** 50% the tab's Classic score (analysts, upside, social) + 50% "
                "**Fundamentals**: debt 25% (Net Debt/EBITDA; banks exempt, REITs and utilities judged more leniently), "
                "sector P/E 15%, PEG 10%, forward PEG 10%, free cash flow yield 20%, revenue growth 20%. "
                "**−5 points per red flag** (high debt, burning cash, shrinking revenue, unprofitable)."
            )
        if is_smoothed:
            if not SCORE_HISTORY_FILE:
                st.error("No writable data folder was found, so score history can't be saved; smoothed scores equal live scores.")
            else:
                st.caption(
                    f"🧠 **Smoothed view:** ranks on each stock's score history ({len(score_hist)} stocks tracked). "
                    "One observation per stock is recorded at most every 4 hours, older readings fade with a 3-day half-life, "
                    "and stocks unseen for 14 days are dropped. New stocks are pulled toward today's median until they build history."
                )

        def render_rec_tab(ranked_df, table_key, tab_name, empty_msg):
            if ranked_df.empty:
                st.info(empty_msg)
                return
            show = ranked_df.copy()
            show.index = show.index + 1
            top = show.iloc[0]
            history_txt = (f" • Score Now **{top['Score Now']}**, seen on **{top['Days Seen']}** day(s)"
                           if is_smoothed else "")
            if is_plus:
                history_txt += (f" • Fundamentals **{top['Fundamentals']:.0f}** • Red flags: **{top['Red Flags'] if top['Red Flags'] != '—' else 'none'}**"
                                f" • Classic **{top['Classic Composite']}**")
            st.success(
                f"🌟 **#1 {tab_name} ({'Smoothed' if is_smoothed else 'Live'}):** **{top['Ticker']}** ({top['Company']}) • "
                f"**{top['Sector']}** • {'Composite+' if is_plus else 'Composite'} **{top['Composite Index']} / 100**{history_txt} (Analysts: **{top['Analysts']}**, "
                f"Analyst Buy: **{top['Analyst Buy %']}%**, Upside: **{top['Upside']:+.1f}%**, Social: **{top['Social Score'] if pd.notna(top['Social Score']) else 'n/a'}**, "
                f"Market Cap: **{top['Market Cap']}**)."
            )
            event = st.dataframe(
                show[display_cols],
                column_config=rec_config,
                **STRETCH,
                height=760,
                hide_index=False,
                on_select="rerun",
                selection_mode="single-row",
                key=f"{table_key}_{rec_model}_{'smooth' if is_smoothed else 'live'}",
            )
            if hasattr(event, "selection"):
                sel_rows = getattr(event.selection, "rows", [])
                if sel_rows:
                    open_deep_dive(show.iloc[sel_rows[0]]["Ticker"])

        if is_plus:
            inst_df = inst_plus_smooth if is_smoothed else inst_plus_live
            emerging_df = emer_plus_smooth if is_smoothed else emer_plus_live
            mid_df = mid_plus_smooth if is_smoothed else mid_plus_live
            small_df = small_plus_smooth if is_smoothed else small_plus_live
        else:
            inst_df = inst_smooth if is_smoothed else inst_live
            emerging_df = emer_smooth if is_smoothed else emer_live
            mid_df = mid_smooth if is_smoothed else mid_live
            small_df = small_smooth if is_smoothed else small_live

        tab_inst, tab_mid, tab_small, tab_emerging = st.tabs([
            f"🏛️ Large-Cap / Institutional ({len(inst_pool)})",
            f"🏢 Mid-Cap ({len(mid_pool)})",
            f"🔬 Small-Cap ({len(small_pool)})",
            f"🚀 Emerging Growth ({len(emerging_pool)})",
        ])
        with tab_inst:
            st.caption(
                f"**Institutional conviction:** stocks with **≥ {INST_MIN_ANALYSTS} analysts** and a market cap of "
                f"**≥ ${INST_MIN_MARKET_CAP / 1e9:.0f}B**, **profitable companies only** (positive forward P/E), "
                "anchored on Wall Street. "
                "Weighting: **45% Analyst Buy % + 35% Valuation Runway + 20% Social Sentiment**."
            )
            render_rec_tab(
                inst_df, "df_rec_inst", "Large-Cap / Institutional",
                f"No scanned stocks have {INST_MIN_ANALYSTS}+ analysts, a ${INST_MIN_MARKET_CAP / 1e9:.0f}B+ market cap "
                "and a positive forward P/E. "
                "Try increasing 'Rows to Scan & Display' in the sidebar.",
            )
        with tab_mid:
            st.caption(
                f"**Mid-cap conviction:** stocks with a market cap of **${MID_MIN_MARKET_CAP / 1e9:.0f}B to "
                f"${MID_MAX_MARKET_CAP / 1e9:.0f}B** and **≥ {MID_MIN_ANALYSTS} analysts**"
                + (", **profitable companies only** (positive forward P/E)" if MID_REQUIRE_PROFIT else "")
                + ". Same weighting as Large-Cap: **45% Analyst Buy % + 35% Valuation Runway + 20% Social Sentiment**."
            )
            render_rec_tab(
                mid_df, "df_rec_mid", "Mid-Cap",
                f"No scanned stocks are ${MID_MIN_MARKET_CAP / 1e9:.0f}B-${MID_MAX_MARKET_CAP / 1e9:.0f}B with "
                f"{MID_MIN_ANALYSTS}+ analysts. Mid caps are mostly in the S&P MidCap 400: pick a larger "
                "'Market Coverage' in the sidebar.",
            )
        with tab_small:
            st.caption(
                f"**Small-cap momentum:** stocks with a market cap of **${SMALL_MIN_MARKET_CAP / 1e6:.0f}M to "
                f"${SMALL_MAX_MARKET_CAP / 1e9:.0f}B** and **≥ {SMALL_MIN_ANALYSTS} analysts**"
                + (", **profitable companies only**" if SMALL_REQUIRE_PROFIT else " (unprofitable companies allowed)")
                + ". Same weighting as Emerging Growth: **30% Analyst Buy % + 30% Valuation Runway + 40% "
                "Volume-Weighted Social Momentum**, with thin coverage pulled toward a 65% market prior."
            )
            render_rec_tab(
                small_df, "df_rec_small", "Small-Cap",
                f"No scanned stocks are ${SMALL_MIN_MARKET_CAP / 1e6:.0f}M-${SMALL_MAX_MARKET_CAP / 1e9:.0f}B with "
                f"{SMALL_MIN_ANALYSTS}+ analysts. Small caps are mostly in the S&P SmallCap 600: pick the largest "
                "'Market Coverage' in the sidebar.",
            )
        with tab_emerging:
            st.caption(
                f"**Retail momentum:** any stock with **≥ {EMERGING_MIN_ANALYSTS} analysts** (all market caps, including unprofitable companies). "
                "Thin coverage is pulled toward a 65% market prior (Bayesian shrinkage) so 2-8 analyst names can compete on merit. "
                "Weighting: **30% Analyst Buy % + 30% Valuation Runway + 40% Volume-Weighted Social Momentum**."
            )
            render_rec_tab(
                emerging_df, "df_rec_emerging", "Emerging Growth",
                f"No scanned stocks have {EMERGING_MIN_ANALYSTS}+ analysts. Try increasing 'Rows to Scan & Display' in the sidebar.",
            )

        # Both tabs render on every run, so both downloads go in the sidebar slot together
        stamp = f"{'composite_plus_' if is_plus else ''}{'smoothed' if is_smoothed else 'live'}_{now.strftime('%Y%m%d_%H%M')}"
        with table_csv_slot.container():
            for label, tdf, fname in (("Institutional", inst_df, "recommended_large_cap_institutional"),
                                      ("Mid-Cap", mid_df, "recommended_mid_cap"),
                                      ("Small-Cap", small_df, "recommended_small_cap"),
                                      ("Emerging", emerging_df, "recommended_emerging_growth")):
                if not tdf.empty:
                    csv_cols = [c for c in display_cols if c != "View"]
                    st.download_button(
                        label=f"📥 Download {label} CSV ({len(tdf)} rows)",
                        data=tdf[csv_cols].to_csv(index=True, index_label="Rank").encode("utf-8"),
                        file_name=f"{fname}_{stamp}.csv",
                        mime="text/csv",
                        type="secondary",
                        **STRETCH,
                        key=f"dl_rec_{label.lower()}",
                    )

# ==============================================================================
# VIEW 3: PORTFOLIO SIMULATIONS
# ==============================================================================
elif app_mode == "🧪 Portfolio Simulations":
    c_basket, sec_col, _ = st.columns([4.2, 4.0, 1.8])
    with sec_col:
        selected_sector = sector_selector("sim_sector")
    with c_basket:
        basket_size = st.slider(
            "Basket Size (Number of Top Recommended Stocks):",
            min_value=5,
            max_value=50,
            value=20,
            step=5,
            help="Controls exactly how many of the top-ranked recommended stocks are included in the simulation.",
            key="sim_basket_size_slider",
        )

    with st.spinner(f"Screening market database to extract top {basket_size} stocks for simulation..."):
        df_exchange = get_persistent_universe_data(selected_sector, "Current")
    show_coverage_report(df_exchange)

    if df_exchange.empty:
        st.warning("Market database currently loading or timed out. Click 🔄 Refresh Data at the top of the sidebar.")
    else:
        filtered_df = df_exchange.copy()
        ranked_df = filtered_df.sort_values(by="Composite Index", ascending=False).reset_index(drop=True)
        active_basket_df = ranked_df.head(basket_size).copy().reset_index(drop=True)

        if active_basket_df.empty:
            st.warning("No equities meet the screening criteria. Increase Market Scan Scope on sidebar.")
        else:
            actual_n = len(active_basket_df)

            sim_tab1, sim_tab2, sim_tab3 = st.tabs([
                f"🔮 Forward Projection ({actual_n} Stocks)",
                f"📜 Historical Backtest vs. S&P 500 ({actual_n} Stocks)",
                f"🎯 Model Congruence & Signal Scorecard"
            ])

            # ------------------------------------------------------------------
            # SUB-TAB 1: FORWARD PREDICTION
            # ------------------------------------------------------------------
            with sim_tab1:
                tf_ctrl_col, _ = st.columns([5.5, 4.5])
                with tf_ctrl_col:
                    proj_horizon = st.radio(
                        "Forward Projection Time Frame:",
                        options=["3 Months", "6 Months", "12 Months (1 Year)", "18 Months", "24 Months (2 Years)"],
                        index=2,
                        horizontal=True,
                        key="forward_horizon_radio",
                    )

                horizon_months_map = {
                    "3 Months": 3,
                    "6 Months": 6,
                    "12 Months (1 Year)": 12,
                    "18 Months": 18,
                    "24 Months (2 Years)": 24,
                }
                n_months = horizon_months_map.get(proj_horizon, 12)

                initial_investment = 10000.0
                equal_alloc = initial_investment / actual_n

                sim_basket = active_basket_df.copy()
                sim_basket["Allocation $"] = equal_alloc

                annual_rates = sim_basket["Upside"] / 100.0
                period_return_series = ((1.0 + np.maximum(-0.95, annual_rates)) ** (n_months / 12.0) - 1.0) * 100.0
                
                if n_months > 12:
                    ann_series = (((1.0 + np.maximum(-0.95, period_return_series / 100.0)) ** (12.0 / n_months)) - 1.0) * 100.0
                    sim_basket["Period Exp. Return %"] = [
                        f"{tot:+.1f}% ({ann:+.1f}% ann.)" for tot, ann in zip(period_return_series, ann_series)
                    ]
                else:
                    sim_basket["Period Exp. Return %"] = [f"{tot:+.1f}%" for tot in period_return_series]

                raw_period_rets = period_return_series.values
                sim_basket["Projected Value $"] = sim_basket["Allocation $"] * (1.0 + (raw_period_rets / 100.0))
                sim_basket["Expected Gain $"] = sim_basket["Projected Value $"] - sim_basket["Allocation $"]
                sim_basket["Target"] = sim_basket["Price_Num"] * (1.0 + (sim_basket["Upside"] / 100.0))

                exp_basket_period_return = float(np.mean(raw_period_rets))
                total_proj_val = float(sim_basket["Projected Value $"].sum())
                net_profit = total_proj_val - initial_investment

                sp_annual_rate = 0.10
                sp_baseline_period_pct = (((1.0 + sp_annual_rate) ** (n_months / 12.0)) - 1.0) * 100.0
                sp_proj_val = initial_investment * (1.0 + (sp_baseline_period_pct / 100.0))
                alpha_projected = exp_basket_period_return - sp_baseline_period_pct

                if n_months > 12:
                    ann_basket_calc = (((1.0 + exp_basket_period_return / 100.0) ** (12.0 / n_months)) - 1.0) * 100.0
                    basket_metric_str = f"{exp_basket_period_return:+.1f}% ({ann_basket_calc:+.1f}% ann.)"
                    sp_metric_str = f"{sp_baseline_period_pct:+.1f}% (+10.0% ann.)"
                else:
                    basket_metric_str = f"{exp_basket_period_return:+.1f}%"
                    sp_metric_str = f"{sp_baseline_period_pct:+.1f}%"

                m_col1, m_col2, m_col3, m_col4 = st.columns(4)
                m_col1.metric(
                    f"Basket Exp. Return ({proj_horizon})",
                    basket_metric_str,
                    delta=f"{alpha_projected:+.1f}% vs S&P 500",
                    delta_color="normal" if alpha_projected >= 0 else "inverse",
                    help=f"Compounded average expected return across the top {actual_n} stocks over {proj_horizon} (with annualized return in parentheses for multi-year timespans).",
                )
                m_col2.metric(
                    "Projected Portfolio Value",
                    f"${total_proj_val:,.0f}",
                    delta=f"\\${net_profit:+,.0f} (\\$10k base)",
                    delta_color="normal" if net_profit >= 0 else "inverse",
                )
                m_col3.metric(
                    f"S&P 500 Baseline ({proj_horizon})",
                    sp_metric_str,
                    delta=f"\\${sp_proj_val:,.0f} (\\$10k base)",
                    delta_color="off",
                    help="Historical ~10% annual nominal benchmark scaled to the selected horizon.",
                )
                m_col4.metric(
                    "Expected Excess Alpha",
                    f"{alpha_projected:+.1f}%",
                    delta="Outperformance Spread",
                    delta_color="normal" if alpha_projected >= 0 else "inverse",
                )

                months_list = list(range(n_months + 1))
                start_dt = pd.Timestamp.now().normalize()
                cone_dates = [start_dt + pd.DateOffset(months=m) for m in months_list]

                eff_annual_basket = ((1.0 + exp_basket_period_return / 100.0) ** (12.0 / max(1, n_months)) - 1.0)
                base_path = [initial_investment * ((1.0 + eff_annual_basket) ** (m / 12.0)) for m in months_list]
                bull_path = [initial_investment * ((1.0 + eff_annual_basket + 0.15) ** (m / 12.0)) for m in months_list]
                bear_path = [initial_investment * ((1.0 + max(-0.25, eff_annual_basket - 0.20)) ** (m / 12.0)) for m in months_list]
                sp_path = [initial_investment * ((1.0 + sp_annual_rate) ** (m / 12.0)) for m in months_list]

                fig_proj = go.Figure()

                fig_proj.add_trace(go.Scatter(
                    x=cone_dates + cone_dates[::-1],
                    y=bull_path + bear_path[::-1],
                    fill="toself",
                    fillcolor="rgba(0, 255, 170, 0.08)",
                    line=dict(color="rgba(255, 255, 255, 0)"),
                    hoverinfo="skip",
                    showlegend=True,
                    name="Projection Range (Bull to Bear Case)",
                ))

                fig_proj.add_trace(go.Scatter(
                    x=cone_dates,
                    y=base_path,
                    mode="lines+markers",
                    name=f"Top {actual_n} Basket Expected ({exp_basket_period_return:+.1f}%)",
                    line=dict(color="#00FFAA", width=3.5),
                    hovertemplate="<b>%{x|%b %Y}</b><br>Basket Projected: <b>$%{y:,.0f}</b><extra></extra>",
                ))

                fig_proj.add_trace(go.Scatter(
                    x=cone_dates,
                    y=sp_path,
                    mode="lines",
                    name=f"S&P 500 Baseline ({sp_baseline_period_pct:+.1f}%)",
                    line=dict(color="#FFB300", width=2.5, dash="dash"),
                    hovertemplate="<b>%{x|%b %Y}</b><br>S&P 500 Baseline: <b>$%{y:,.0f}</b><extra></extra>",
                ))

                fig_proj.update_layout(
                    title=dict(
                        text=f"{proj_horizon} Forward Simulation: Top {actual_n} Basket vs. S&P 500 ($10,000 Portfolio)",
                        font=dict(size=16)
                    ),
                    xaxis_title="Timeline",
                    yaxis_title="Portfolio Balance (USD)",
                    yaxis=dict(tickprefix="$", tickformat=",.0f"),
                    hovermode="x unified",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    margin=dict(l=20, r=20, t=60, b=20),
                    template="plotly_dark",
                )
                try:
                    st.plotly_chart(fig_proj, **STRETCH)
                except TypeError:
                    st.plotly_chart(fig_proj, width="stretch")

                st.markdown(f"#### 📋 Top {actual_n} Basket Allocation & Target Breakdown ({proj_horizon})")
                sim_basket.index = sim_basket.index + 1

                disp_proj_cols = [
                    "Ticker", "Company", "Sector", "Price", "Target",
                    "Period Exp. Return %", "Allocation $", "Projected Value $",
                    "Analysts", "Composite Index", "View"
                ]

                csv_proj_cols = [c for c in disp_proj_cols if c != "View"]
                csv_proj_data = sim_basket[csv_proj_cols].to_csv(index=True, index_label="Rank").encode("utf-8")

                with table_csv_slot.container():
                    st.download_button(
                        label=f"📥 Download Top {actual_n} Forecast CSV",
                        data=csv_proj_data,
                        file_name=f"top{actual_n}_forward_{n_months}m_simulation_{now.strftime('%Y%m%d_%H%M')}.csv",
                        mime="text/csv",
                        type="secondary",
                        **STRETCH,
                    )

                proj_event = st.dataframe(
                    sim_basket[disp_proj_cols],
                    column_config=COMMON_TABLE_CONFIG,
                    **STRETCH,
                    height=580,
                    hide_index=False,
                    on_select="rerun",
                    selection_mode="single-row",
                    key="df_proj_interactive",
                )

                if hasattr(proj_event, "selection"):
                    sel_rows = getattr(proj_event.selection, "rows", [])
                    if sel_rows:
                        open_deep_dive(sim_basket.iloc[sel_rows[0]]["Ticker"])

            # ------------------------------------------------------------------
            # SUB-TAB 2: HISTORICAL BACKTEST
            # ------------------------------------------------------------------
            with sim_tab2:
                b_col1, _ = st.columns([6.0, 4.0])
                with b_col1:
                    lookback_period = st.radio(
                        "Historical Lookback Time Frame:",
                        options=["1 Month", "3 Months", "6 Months", "1 Year", "2 Years", "3 Years", "5 Years"],
                        index=3,
                        horizontal=True,
                        key="backtest_lookback_radio",
                    )

                period_map = {
                    "1 Month": "1mo",
                    "3 Months": "3mo",
                    "6 Months": "6mo",
                    "1 Year": "1y",
                    "2 Years": "2y",
                    "3 Years": "3y",
                    "5 Years": "5y",
                }
                period_str = period_map.get(lookback_period, "1y")

                target_sim_tickers = tuple(active_basket_df["Ticker"].tolist())

                st.caption("⚠️ Look-ahead caveat: this applies today's top-ranked picks to past prices, so it shows how these stocks performed, not how the model would have done had it picked them back then.")
                with st.spinner(f"Pulling historical daily data for Top {actual_n} equities + S&P 500 ({lookback_period})..."):
                    hist_prices_df = fetch_portfolio_history_data(target_sim_tickers, benchmark="^GSPC", period_str=period_str)

                if hist_prices_df.empty or len(hist_prices_df) < 5:
                    st.warning("Historical data could not be retrieved from Yahoo Finance for this timeframe. Please try another lookback duration.")
                else:
                    bench_col = "^GSPC" if "^GSPC" in hist_prices_df.columns else [c for c in hist_prices_df.columns if "SP" in c][0]
                    stock_cols = [c for c in hist_prices_df.columns if c != bench_col]

                    returns_df = hist_prices_df.pct_change()

                    port_daily_ret = returns_df[stock_cols].mean(axis=1, skipna=True).fillna(0.0)
                    bench_daily_ret = returns_df[bench_col].fillna(0.0)

                    initial_cap = 10000.0
                    port_growth = initial_cap * (1.0 + port_daily_ret).cumprod()
                    bench_growth = initial_cap * (1.0 + bench_daily_ret).cumprod()

                    n_days = len(port_daily_ret)
                    years = max(0.08, n_days / 252.0)

                    port_total_ret = ((port_growth.iloc[-1] / initial_cap) - 1.0) * 100.0
                    bench_total_ret = ((bench_growth.iloc[-1] / initial_cap) - 1.0) * 100.0
                    alpha_ret = port_total_ret - bench_total_ret

                    port_cagr = (((port_growth.iloc[-1] / initial_cap) ** (1.0 / years)) - 1.0) * 100.0
                    bench_cagr = (((bench_growth.iloc[-1] / initial_cap) ** (1.0 / years)) - 1.0) * 100.0

                    port_vol = port_daily_ret.std() * np.sqrt(252) * 100.0
                    bench_vol = bench_daily_ret.std() * np.sqrt(252) * 100.0

                    rf_rate = 0.04
                    port_sharpe = ((port_cagr / 100.0) - rf_rate) / (port_vol / 100.0) if port_vol > 0 else 0.0
                    bench_sharpe = ((bench_cagr / 100.0) - rf_rate) / (bench_vol / 100.0) if bench_vol > 0 else 0.0

                    port_dd = ((port_growth - port_growth.cummax()) / port_growth.cummax()).min() * 100.0
                    bench_dd = ((bench_growth - bench_growth.cummax()) / bench_growth.cummax()).min() * 100.0

                    is_multi_year_backtest = lookback_period in ["2 Years", "3 Years", "5 Years"]
                    
                    if is_multi_year_backtest:
                        port_metric_label = f"{port_total_ret:+.1f}% ({port_cagr:+.1f}% ann.)"
                        bench_metric_label = f"{bench_total_ret:+.1f}% ({bench_cagr:+.1f}% ann.)"
                    else:
                        port_metric_label = f"{port_total_ret:+.1f}%"
                        bench_metric_label = f"{bench_total_ret:+.1f}%"

                    s_c1, s_c2, s_c3, s_c4, s_c5 = st.columns(5)
                    s_c1.metric(
                        "Portfolio Total Return",
                        port_metric_label,
                        delta=f"{alpha_ret:+.1f}% vs S&P 500",
                        delta_color="normal" if alpha_ret >= 0 else "inverse",
                        help="Cumulative total return with annualized CAGR in parentheses for multi-year lookbacks.",
                    )
                    s_c2.metric(
                        "S&P 500 Benchmark Return",
                        bench_metric_label,
                        delta=f"\\${bench_growth.iloc[-1]:,.0f} (\\$10k base)",
                        delta_color="off",
                        help="Cumulative benchmark total return with annualized CAGR in parentheses for multi-year lookbacks.",
                    )
                    s_c3.metric(
                        "Portfolio Volatility (Ann.)",
                        f"{port_vol:.1f}%",
                        delta=f"S&P 500: {bench_vol:.1f}%",
                        delta_color="inverse" if port_vol > bench_vol else "normal",
                    )
                    s_c4.metric(
                        "Max Drawdown",
                        f"{port_dd:.1f}%",
                        delta=f"S&P 500: {bench_dd:.1f}%",
                        delta_color="normal" if port_dd > bench_dd else "inverse",
                    )
                    s_c5.metric(
                        "Sharpe Ratio (Rf=4%)",
                        f"{port_sharpe:.2f}",
                        delta=f"S&P 500: {bench_sharpe:.2f}",
                        delta_color="normal" if port_sharpe >= bench_sharpe else "inverse",
                    )

                    fig_backtest = go.Figure()

                    fig_backtest.add_trace(go.Scatter(
                        x=port_growth.index,
                        y=port_growth.values,
                        mode="lines",
                        name=f"Top {actual_n} Basket ({port_total_ret:+.1f}%)",
                        line=dict(color="#00FFAA", width=3.0),
                        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Basket Value: <b>$%{y:,.0f}</b><extra></extra>",
                    ))

                    fig_backtest.add_trace(go.Scatter(
                        x=bench_growth.index,
                        y=bench_growth.values,
                        mode="lines",
                        name=f"S&P 500 (^GSPC) ({bench_total_ret:+.1f}%)",
                        line=dict(color="#FFB300", width=2.5, dash="dash"),
                        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>S&P 500 Value: <b>$%{y:,.0f}</b><extra></extra>",
                    ))

                    fig_backtest.update_layout(
                        title=dict(
                            text=f"Growth of $10,000: Top {actual_n} Recommended Stocks vs. S&P 500 ({lookback_period})",
                            font=dict(size=16)
                        ),
                        xaxis_title="Date",
                        yaxis_title="Portfolio Balance (USD)",
                        yaxis=dict(tickprefix="$", tickformat=",.0f"),
                        hovermode="x unified",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                        margin=dict(l=20, r=20, t=60, b=20),
                        template="plotly_dark",
                    )
                    try:
                        st.plotly_chart(fig_backtest, **STRETCH)
                    except TypeError:
                        st.plotly_chart(fig_backtest, width="stretch")

                    with st.expander(f"📊 **Individual Performance Breakdown for the {actual_n} Basket Equities ({lookback_period})**", expanded=False):
                        indiv_rows = []
                        sector_by_ticker = dict(zip(active_basket_df["Ticker"], active_basket_df["Sector"]))
                        for sym in stock_cols:
                            sym_series = hist_prices_df[sym].dropna()
                            if len(sym_series) >= 2:
                                s_ret = ((sym_series.iloc[-1] / sym_series.iloc[0]) - 1.0) * 100.0
                                s_dd = ((sym_series - sym_series.cummax()) / sym_series.cummax()).min() * 100.0
                                s_vol = sym_series.pct_change().std() * np.sqrt(252) * 100.0
                                
                                if is_multi_year_backtest:
                                    s_cagr = (((sym_series.iloc[-1] / sym_series.iloc[0]) ** (1.0 / years)) - 1.0) * 100.0
                                    s_ret_str = f"{s_ret:+.1f}% ({s_cagr:+.1f}% ann.)"
                                else:
                                    s_ret_str = f"{s_ret:+.1f}%"

                                indiv_rows.append({
                                    "Ticker": sym,
                                    "Sector": sector_by_ticker.get(sym, "N/A"),
                                    "Period Return": s_ret_str,
                                    "_sort": s_ret,
                                    "Max Drawdown %": round(s_dd, 1),
                                    "Volatility %": round(s_vol, 1),
                                    "Start Price": f"${sym_series.iloc[0]:.2f}",
                                    "End Price": f"${sym_series.iloc[-1]:.2f}",
                                })

                        if indiv_rows:
                            indiv_df = pd.DataFrame(indiv_rows).sort_values(by="_sort", ascending=False).drop(columns="_sort").reset_index(drop=True)
                            indiv_df.index = indiv_df.index + 1
                            st.dataframe(
                                indiv_df,
                                column_config={
                                    "Period Return": st.column_config.TextColumn("Period Return", width="medium"),
                                    "Max Drawdown %": st.column_config.NumberColumn("Max Drawdown", format="%.1f%%"),
                                    "Volatility %": st.column_config.NumberColumn("Annual Volatility", format="%.1f%%"),
                                },
                                **STRETCH,
                                height=360,
                            )

            # ------------------------------------------------------------------
            # SUB-TAB 3: MODEL CONGRUENCE & SIGNAL SCORECARD
            # ------------------------------------------------------------------
            with sim_tab3:
                st.markdown(
                    "### 🎯 Model Congruence & Signal-to-Noise Scorecard\n"
                    "Compares **Forward Predictions** against **Historical Realized Returns** across expanding horizons to measure "
                    "how long it takes for fundamental alpha to overcome short-term market noise."
                )

                st.info(
                    "💡 **The Signal-to-Noise Principle:** Over short horizons (1–3 months), broad macroeconomic beta, news sentiment spikes, "
                    "and short-term volatility dominate price movements, often causing high-conviction baskets to lag passive indices. "
                    "Over 6 to 36 months, individual company earnings drift and institutional valuation runways compound, "
                    "enabling the model's predictive power to overcome short-term friction."
                )

                target_sim_tickers = tuple(active_basket_df["Ticker"].tolist())
                st.caption("⚠️ Look-ahead caveat: this applies today's top-ranked picks to past prices, so it shows how these stocks performed, not how the model would have done had it picked them back then.")
                with st.spinner(f"Analyzing multi-horizon historical data for Top {actual_n} basket..."):
                    cong_prices_df = fetch_portfolio_history_data(target_sim_tickers, benchmark="^GSPC", period_str="3y")

                if cong_prices_df.empty or len(cong_prices_df) < 25:
                    st.warning("Insufficient multi-year price data available to construct the full congruence scorecard. Please try again.")
                else:
                    bench_col = "^GSPC" if "^GSPC" in cong_prices_df.columns else [c for c in cong_prices_df.columns if "SP" in c][0]
                    stock_cols = [c for c in cong_prices_df.columns if c != bench_col]

                    returns_df = cong_prices_df.pct_change()
                    port_daily_ret = returns_df[stock_cols].mean(axis=1, skipna=True).fillna(0.0)
                    bench_daily_ret = returns_df[bench_col].fillna(0.0)

                    port_cum = (1.0 + port_daily_ret).cumprod()
                    bench_cum = (1.0 + bench_daily_ret).cumprod()

                    annual_basket_upside = float(active_basket_df["Upside"].mean())
                    sp_annual_benchmark = 10.0

                    horizons_spec = [
                        {"label": "1 Month", "months": 1, "days": 21},
                        {"label": "3 Months", "months": 3, "days": 63},
                        {"label": "6 Months", "months": 6, "days": 126},
                        {"label": "1 Year", "months": 12, "days": 252},
                        {"label": "2 Years", "months": 24, "days": 504},
                        {"label": "3 Years", "months": 36, "days": 756},
                    ]

                    available_days = len(port_cum) - 1
                    cong_records = []

                    for h in horizons_spec:
                        if available_days < int(h["days"] * 0.85):
                            continue
                        d = min(h["days"], available_days)
                        m = h["months"]

                        pred_basket = ((1.0 + np.maximum(-0.9, annual_basket_upside / 100.0)) ** (m / 12.0) - 1.0) * 100.0
                        pred_sp = ((1.0 + sp_annual_benchmark / 100.0) ** (m / 12.0) - 1.0) * 100.0
                        pred_alpha = pred_basket - pred_sp

                        real_basket = ((port_cum.iloc[-1] / port_cum.iloc[-(d + 1)]) - 1.0) * 100.0
                        real_sp = ((bench_cum.iloc[-1] / bench_cum.iloc[-(d + 1)]) - 1.0) * 100.0
                        real_alpha = real_basket - real_sp

                        if abs(pred_alpha) > 0.05:
                            realization_rate = (real_alpha / pred_alpha) * 100.0
                        else:
                            realization_rate = 100.0 if real_alpha >= 0 else 0.0

                        if realization_rate >= 80.0:
                            status = "🟢 High Congruence (Target Met)"
                        elif realization_rate >= 35.0:
                            status = "🟡 Emerging Signal (Capturing Alpha)"
                        elif realization_rate >= 0.0:
                            status = "🟠 Subdued Signal (Short-Term Drag)"
                        else:
                            status = "🔴 Noise Dominated (S&P 500 Lead)"

                        if m > 12:
                            years_h = m / 12.0
                            pred_basket_ann = (((1.0 + max(-0.95, pred_basket / 100.0)) ** (1.0 / years_h)) - 1.0) * 100.0
                            real_basket_ann = (((1.0 + max(-0.95, real_basket / 100.0)) ** (1.0 / years_h)) - 1.0) * 100.0
                            real_sp_ann = (((1.0 + max(-0.95, real_sp / 100.0)) ** (1.0 / years_h)) - 1.0) * 100.0

                            pred_basket_str = f"{pred_basket:+.1f}% ({pred_basket_ann:+.1f}% ann.)"
                            real_basket_str = f"{real_basket:+.1f}% ({real_basket_ann:+.1f}% ann.)"
                            real_sp_str = f"{real_sp:+.1f}% ({real_sp_ann:+.1f}% ann.)"
                        else:
                            pred_basket_str = f"{pred_basket:+.1f}%"
                            real_basket_str = f"{real_basket:+.1f}%"
                            real_sp_str = f"{real_sp:+.1f}%"

                        cong_records.append({
                            "Horizon": h["label"],
                            "Predicted Basket %": pred_basket_str,
                            "Historical Realized %": real_basket_str,
                            "Historical S&P 500 %": real_sp_str,
                            "Raw_Pred_Basket": pred_basket,
                            "Raw_Real_Basket": real_basket,
                            "Raw_Real_SP": real_sp,
                            "Predicted Alpha %": round(pred_alpha, 1),
                            "Realized Alpha %": round(real_alpha, 1),
                            "Realization Rate %": round(realization_rate, 1),
                            "Signal Status": status,
                        })

                    if cong_records:
                        cong_df = pd.DataFrame(cong_records)

                        c_card1, c_card2, c_card3, c_card4 = st.columns(4)

                        r_3m = cong_df[cong_df["Horizon"] == "3 Months"]["Realization Rate %"].values
                        val_3m = f"{r_3m[0]:+.1f}%" if len(r_3m) > 0 else "N/A"

                        r_1y = cong_df[cong_df["Horizon"] == "1 Year"]["Realization Rate %"].values
                        val_1y = f"{r_1y[0]:+.1f}%" if len(r_1y) > 0 else "N/A"

                        r_3y = cong_df[cong_df["Horizon"] == "3 Years"]["Realization Rate %"].values
                        val_3y = f"{r_3y[0]:+.1f}%" if len(r_3y) > 0 else "N/A"

                        c_card1.metric(
                            "3-Month Realization Rate",
                            val_3m,
                            delta="Short-Term Market Noise",
                            delta_color="inverse" if (len(r_3m) > 0 and r_3m[0] < 50) else "normal",
                            help="Percentage of predicted alpha captured over 3 months. Low or negative values reflect short-term noise.",
                        )
                        c_card2.metric(
                            "1-Year Realization Rate",
                            val_1y,
                            delta="Target Realization",
                            delta_color="normal" if (len(r_1y) > 0 and r_1y[0] >= 50) else "inverse",
                            help="Percentage of predicted alpha captured over a full 12-month market cycle.",
                        )
                        c_card3.metric(
                            "3-Year Realization Rate",
                            val_3y,
                            delta="Fundamental Drift Dominance",
                            delta_color="normal",
                            help="Long-term alpha realization where fundamental growth compounds past macro friction.",
                        )
                        c_card4.metric(
                            "First Horizon Beating S&P 500",
                            (str(cong_df.loc[cong_df["Realized Alpha %"] > 0, "Horizon"].iloc[0]) if (cong_df["Realized Alpha %"] > 0).any() else "None yet"),
                            delta="Shortest window with positive realized alpha",
                            delta_color="off",
                            help="Computed from the scorecard below: the shortest lookback window in which this basket beat the S&P 500. In-sample: today's picks applied to past prices, so this is not out-of-sample evidence.",
                        )

                        fig_cong = go.Figure()

                        fig_cong.add_trace(go.Bar(
                            x=cong_df["Horizon"],
                            y=cong_df["Raw_Pred_Basket"],
                            name="Model Predicted Basket %",
                            marker_color="#00FFAA",
                            hovertemplate="<b>%{x}</b><br>Predicted Basket: <b>%{y:+.1f}%</b><extra></extra>",
                        ))

                        fig_cong.add_trace(go.Bar(
                            x=cong_df["Horizon"],
                            y=cong_df["Raw_Real_Basket"],
                            name="Historical Realized Basket %",
                            marker_color="#3B82F6",
                            hovertemplate="<b>%{x}</b><br>Realized Basket: <b>%{y:+.1f}%</b><extra></extra>",
                        ))

                        fig_cong.add_trace(go.Bar(
                            x=cong_df["Horizon"],
                            y=cong_df["Raw_Real_SP"],
                            name="Historical S&P 500 %",
                            marker_color="#FFB300",
                            hovertemplate="<b>%{x}</b><br>S&P 500: <b>%{y:+.1f}%</b><extra></extra>",
                        ))

                        fig_cong.update_layout(
                            barmode="group",
                            title=dict(
                                text=f"Horizon Congruence: Predicted Return vs. Historical Realized vs. S&P 500 (Top {actual_n} Basket)",
                                font=dict(size=16),
                            ),
                            xaxis_title="Time Horizon",
                            yaxis_title="Total Return (%)",
                            yaxis=dict(ticksuffix="%"),
                            hovermode="x unified",
                            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                            margin=dict(l=20, r=20, t=60, b=20),
                            template="plotly_dark",
                        )
                        try:
                            st.plotly_chart(fig_cong, **STRETCH)
                        except TypeError:
                            st.plotly_chart(fig_cong, width="stretch")

                        st.markdown("#### 📊 Multi-Horizon Alpha Realization Scorecard")
                        disp_cong_cols = [
                            "Horizon", "Predicted Basket %", "Historical Realized %", "Historical S&P 500 %",
                            "Predicted Alpha %", "Realized Alpha %", "Realization Rate %", "Signal Status"
                        ]
                        scorecard_render_df = cong_df[disp_cong_cols].copy()
                        scorecard_render_df.index = scorecard_render_df.index + 1

                        st.dataframe(
                            scorecard_render_df,
                            column_config=COMMON_TABLE_CONFIG,
                            **STRETCH,
                            height=320,
                            hide_index=False,
                        )

                        with st.expander("📖 **How to Interpret Model Congruence & Signal Decay**", expanded=False):
                            st.markdown(
                                """
                                - **Why does the 3-Month horizon favor the S&P 500?** 
                                  High-conviction, high-upside stock baskets tend to have higher idiosyncratic beta than the market index. Over short 30-to-90 day windows, broad macroeconomic sell-offs, Fed rate expectations, and liquidity flows dominate single-stock returns.
                                - **When does the model's signal dominate?**
                                  Historically, between **6 and 12 months**, companies report quarterly financial results that validate or reject analyst models. As earnings growth materializes, stock prices converge toward their fundamental target prices.
                                - **Actionable Strategy:** Treat the model's recommendations with a minimum **12-month investment horizon**. Short-term dips or underperformance at 3 months represent expected market noise rather than fundamental model failure.
                                """
                            )


# ==============================================================================
# VIEW: TRACK RECORD (REAL, FORWARD-LOOKING PERFORMANCE OF SAVED PICKS)
# ==============================================================================
elif app_mode == "📈 Track Record":
    if st.session_state.get("track_coverage_choice") not in COVERAGE_LISTS:
        st.session_state["track_coverage_choice"] = TRACK_COVERAGE
    list_col, pool_col, _ = st.columns([4.0, 4.2, 1.8])
    with list_col:
        list_label = st.selectbox("Select List:", options=list(TRACK_LISTS.values()), key="track_list_choice")
    with pool_col:
        st.selectbox(
            "Picks are tracked from:",
            options=list(COVERAGE_LISTS),
            format_func=lambda k: COVERAGE_LABELS[k],
            key="track_coverage_choice",
            on_change=lambda: save_app_setting("track_coverage", st.session_state["track_coverage_choice"]),
            help="The fixed Market Coverage level the daily picks are saved from, whatever you're browsing with. "
                 "Keep it the same so every day in the record comes from the same pool of stocks; "
                 "changing it applies from the next saved day.",
        )
    list_key = next(k for k, v in TRACK_LISTS.items() if v == list_label)

    st.caption(
        "🧠 **All picks here come from the Smoothed (history-weighted) ranking.** Each market day, the automatic daily "
        f"scan saves the top **{TRACK_TOP_N}** stocks of each list, scanned at the level above, with their price and the "
        "S&P 500 level (if the daily scan isn't set up, they're saved the first time 🏆 Recommended Stocks loads with "
        "All Sectors at that level). This page tracks how those picks have actually done since: a real, "
        "forward-looking record, unlike the backtest in Portfolio Simulations. Returns are price-only (no dividends) and equal-weighted."
    )

    if not TRACK_RECORD_FILE:
        st.error("No writable data folder was found, so picks can't be saved. Set SENTIMENT_DATA_DIR to a writable folder.")
    daily_status = _read_json_file(DAILY_STATUS_FILE, {}) if DAILY_STATUS_FILE else {}
    if not daily_status.get("finished_ts"):
        st.caption("🕒 Automatic daily scan: not set up yet.")
    else:
        try:
            from zoneinfo import ZoneInfo
            _ct = ZoneInfo("America/Chicago")
        except Exception:
            _ct = datetime.timezone(datetime.timedelta(hours=-6))
        _when = datetime.datetime.fromtimestamp(daily_status["finished_ts"], _ct).strftime("%a %b %d, %I:%M %p").replace(" 0", " ")
        _age_days = (time.time() - daily_status["finished_ts"]) / 86400
        if not daily_status.get("ok"):
            st.warning(f"⚠️ Last automatic daily scan ({_when} CT) didn't save picks: {daily_status.get('message', '')}")
        elif _age_days > 4:
            st.warning(f"⚠️ The automatic daily scan last ran {_when} CT. Check that the scheduled job is still set up on the server.")
        else:
            st.caption(f"🕒 Last automatic daily scan: {_when} CT · {daily_status.get('message', '')}")
    snaps, saved_names = load_track_record()
    list_snaps = [s for s in snaps if (s.get("lists") or {}).get(list_key)]
    if not list_snaps:
        st.info("No picks saved yet for this list. Open 🏆 Recommended Stocks with 'All Sectors' selected and today's picks "
                "will be saved automatically; results start meaning something after a week or two.")
    else:
        tracked = sorted({p["ticker"] for s in list_snaps for p in s["lists"][list_key]})
        with st.spinner(f"Fetching latest prices for {len(tracked)} tracked stocks..."):
            closes = fetch_latest_closes(tuple(tracked))
            spx_now = fetch_index_level("^GSPC")
        tr_all = build_track_record_table(list_snaps, closes, spx_now, saved_names)
        tr = tr_all[tr_all["List Key"] == list_key]
        valid = tr.dropna(subset=["Return %"])

        TRACK_CONFIG = {
            "Return %": st.column_config.NumberColumn("Return", format="%+.2f%%",
                                                      help="Price change from the day it was picked to the latest close."),
            "S&P 500 %": st.column_config.NumberColumn("S&P 500", format="%+.2f%%",
                                                       help="S&P 500 change over the same period."),
            "Excess %": st.column_config.NumberColumn("vs S&P 500", format="%+.2f%%",
                                                      help="Return minus the S&P 500 return over the same period. Positive = beat the market."),
            "Entry Price": st.column_config.NumberColumn("Price at Pick", format="$%.2f"),
            "Latest Price": st.column_config.NumberColumn("Latest", format="$%.2f"),
            "Score at Pick": st.column_config.NumberColumn("Score at Pick", format="%.1f"),
            "Rank": st.column_config.NumberColumn("Rank", format="%d", help="Position in that day's list (1 = top pick)."),
            "Picks": st.column_config.NumberColumn("Picks", format="%d"),
            "Beat S&P %": st.column_config.ProgressColumn("Beat the S&P", format="%.0f%%", min_value=0, max_value=100),
            "Ticker": st.column_config.TextColumn("Ticker", width="small"),
            "Company": st.column_config.TextColumn("Company", width="medium"),
        }

        # --- Overall ---
        st.markdown("### Overall results")
        st.caption(
            f"All **{len(valid)}** picks from **{len(list_snaps)}** saved day(s) ({list_snaps[0]['date']} to "
            f"{list_snaps[-1]['date']}), each measured from the day it was picked. **vs S&P 500** is the fairest number, "
            "since it compares every pick with the market over that pick's own holding period."
        )
        if len(list_snaps) < 5:
            st.caption("⏳ Early days: with less than a week of picks, these numbers are mostly noise.")
        if spx_now is None:
            st.warning("Couldn't load the S&P 500 level right now, so comparisons with the index aren't shown. Try 🔄 Refresh Data (top of the sidebar).")
        n_missing = int(tr["Return %"].isna().sum())
        if n_missing:
            st.caption(f"{n_missing} pick(s) have no current price (delisted or Yahoo unavailable) and are left out.")

        if valid.empty:
            st.info("No prices could be loaded for the saved picks yet. Try 🔄 Refresh Data (top of the sidebar) in a minute.")
        else:
            excess = valid["Excess %"].dropna()
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Picks Tracked", f"{len(valid)}", help="Every stock saved for this list on every day (a stock picked on several days counts each time).")
            m2.metric("Avg Return", f"{valid['Return %'].mean():+.2f}%", help="Average price change of all picks since the day each was picked.")
            m3.metric("S&P 500 (same periods)", f"{valid['S&P 500 %'].mean():+.2f}%" if valid["S&P 500 %"].notna().any() else "N/A",
                      help="What the S&P 500 did over the same holding periods, averaged the same way.")
            m4.metric("Avg vs S&P 500", f"{excess.mean():+.2f}%" if not excess.empty else "N/A",
                      help="Average of (pick return minus S&P 500 return). Positive means the picks beat the market.")
            m5.metric("Beat the S&P 500", f"{(excess > 0).mean() * 100:.0f}%" if not excess.empty else "N/A",
                      help="Share of picks that did better than the S&P 500 over their own holding period.")

            # --- By pick date ---
            st.markdown("### By pick date")
            st.caption("One row per day: how that day's picks have done since, on average.")
            by_date = (valid.groupby("Date", as_index=False)
                       .agg(**{"Stock Pool": ("Pool", "first"), "Picks": ("Ticker", "count"), "Return %": ("Return %", "mean"),
                               "S&P 500 %": ("S&P 500 %", "first"), "Excess %": ("Excess %", "mean"),
                               "Beat S&P %": ("Excess %", lambda x: (x.dropna() > 0).mean() * 100 if x.notna().any() else None)})
                       .sort_values("Date", ascending=False))
            by_date["Stock Pool"] = by_date["Stock Pool"].map(lambda k: COVERAGE_LABELS.get(k, k) if k else "—")
            st.dataframe(by_date, column_config=TRACK_CONFIG, hide_index=True, **STRETCH,
                         height=min(460, 40 + 35 * max(len(by_date), 1)))

        # --- Individual picks ---
        st.markdown("### Individual picks")
        st.caption(
            "The individual stocks behind the numbers above. **All dates** lists every pick from every saved day "
            "(one row per stock per day, so a stock picked on several days appears several times); choose a date to see "
            "just that day's list."
        )
        date_options = ["All dates"] + sorted(tr["Date"].unique(), reverse=True)
        date_choice = st.selectbox("Pick date:", options=date_options, key="track_date_choice")
        detail = tr if date_choice == "All dates" else tr[tr["Date"] == date_choice]
        detail = (detail.sort_values(["Date", "Rank"], ascending=[False, True])
                  [["Date", "Rank", "Ticker", "Company", "Sector", "Score at Pick", "Entry Price", "Latest Price",
                    "Return %", "S&P 500 %", "Excess %"]])
        st.dataframe(detail, column_config=TRACK_CONFIG, hide_index=True, **STRETCH,
                     height=min(760, 40 + 35 * max(len(detail), 1)))

        list_tag = {"inst_smooth": "institutional", "emer_smooth": "emerging", "inst_plus_smooth": "institutional_composite_plus",
                    "emer_plus_smooth": "emerging_composite_plus", "mid_smooth": "mid_cap",
                    "small_smooth": "small_cap", "mid_plus_smooth": "mid_cap_composite_plus",
                    "small_plus_smooth": "small_cap_composite_plus"}.get(list_key, list_key)
        with table_csv_slot.container():
            st.download_button(
                label=f"📥 Download Track Record CSV ({len(tr)} picks)",
                data=tr.drop(columns=["List", "List Key"]).rename(columns={"Pool": "Stock Pool"}).to_csv(index=False).encode("utf-8"),
                file_name=f"track_record_{list_tag}_{now.strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv",
                type="secondary",
                **STRETCH,
            )


# ==============================================================================
# VIEW 4: ANALYST SENTIMENT
# ==============================================================================
elif app_mode == "🏦 Analyst Sentiment":
    sec_col, _ = st.columns([4.4, 5.6])
    with sec_col:
        selected_sector = sector_selector("analyst_sector")

    st.caption(
        f"Every scanned stock ranked by **Analyst Buy %** (top **{scan_depth}** shown): the share of Wall Street ratings "
        "that are Buy or Strong Buy, adjusted so thin coverage is pulled toward a 65% market average (Bayesian shrinkage). "
        "Ties go to the stock with more analysts. Stocks with no analyst coverage are left out."
    )

    with st.spinner(f"Scanning {len(scan_request(selected_sector))} stocks..."):
        # Analyst ratings don't vary by social timeframe, so this shares the 'Current' scan with the other pages
        df_exchange = get_persistent_universe_data(selected_sector, "Current")
    show_coverage_report(df_exchange)

    if df_exchange.empty:
        st.warning("Data fetch in progress or timed out. Click 🔄 Refresh Data at the top of the sidebar.")
    else:
        covered = df_exchange[pd.to_numeric(df_exchange["Analysts"], errors="coerce").fillna(0) >= 1]
        if covered.empty:
            st.info("None of the scanned stocks have analyst coverage. Try increasing 'Rows to Scan & Display' in the sidebar.")
        else:
            top_analyst_df = covered.sort_values(by=["Analyst Buy %", "Analysts"], ascending=[False, False]).head(scan_depth).reset_index(drop=True)
            show_sector_shortfall(top_analyst_df, selected_sector, what="stocks with analyst coverage")
            top_analyst_df.index = top_analyst_df.index + 1

            lead_stock = top_analyst_df.iloc[0]
            target_txt = f"${lead_stock['Target_Num']:.2f}" if lead_stock["Target_Num"] else "N/A"
            st.success(
                f"🌟 **#1 Analyst Pick:** **{lead_stock['Ticker']}** ({lead_stock['Company']}) • **{lead_stock['Sector']}** leads with "
                f"**{lead_stock['Analyst Buy %']}% Analyst Buy** (raw {lead_stock['Raw Buy %']}% across **{lead_stock['Analysts']}** analysts; "
                f"Consensus: **{lead_stock['Consensus Rating']}**, 12M Target: **{target_txt}**, Upside: **{lead_stock['Upside']:+.1f}%**)."
            )

            display_cols_analyst = [
                "Ticker", "Company", "Sector", "Price",
                "Analyst Buy %", "Raw Buy %", "Analysts", "Consensus Rating",
                "Strong Buy", "Buy", "Hold", "Sell", "Strong Sell",
                "Target_Num", "Upside", "Upside Status", "Composite Index", "View"
            ]

            csv_cols = [c for c in display_cols_analyst if c != "View"]
            csv_data = (top_analyst_df[csv_cols].rename(columns={"Target_Num": "12M Target"})
                        .to_csv(index=True, index_label="Rank").encode("utf-8"))
            with table_csv_slot.container():
                st.download_button(
                    label=f"📥 Download Table CSV ({len(top_analyst_df)} rows)",
                    data=csv_data,
                    file_name=f"analyst_sentiment_top{len(top_analyst_df)}_{now.strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv",
                    type="secondary",
                    **STRETCH,
                )

            analyst_event = st.dataframe(
                top_analyst_df[display_cols_analyst],
                column_config=COMMON_TABLE_CONFIG,
                **STRETCH,
                height=760,
                hide_index=False,
                on_select="rerun",
                selection_mode="single-row",
                key="df_analyst_interactive",
            )

            if hasattr(analyst_event, "selection"):
                sel_rows = getattr(analyst_event.selection, "rows", [])
                if sel_rows:
                    open_deep_dive(top_analyst_df.iloc[sel_rows[0]]["Ticker"])


# ==============================================================================
# VIEW 5: SOCIAL MEDIA DATA
# ==============================================================================
elif app_mode == "📱 Social Media Data":
    sec_col, _ = st.columns([4.4, 5.6])
    with sec_col:
        selected_sector = sector_selector("social_sector")

    selected_tf = "Current"

    st.caption(
        f"Every scanned stock ranked by **Social Score (0–100)** (top **{scan_depth}** shown): **Sentiment** "
        "(how bullish StockTwits posts are vs a typical stock) scaled by **Buzz** (StockTwits pace, Reddit mentions, "
        "likes, rising interest, trending). Click any column to re-sort, e.g. by Buzz or Reddit mentions.",
        help=SOCIAL_SCORE_EXPLANATION,
    )

    with st.spinner(f"Scanning {len(scan_request(selected_sector))} stocks..."):
        df_exchange = get_persistent_universe_data(selected_sector, selected_tf)
    show_coverage_report(df_exchange)

    if df_exchange.empty:
        st.warning("Data fetch in progress or timed out. Click 🔄 Refresh Data at the top of the sidebar.")
    elif df_exchange["Social Score"].notna().sum() == 0:
        st.info("No StockTwits readings are available yet for these stocks. StockTwits may be unreachable, or its hourly "
                "limit has been used up; readings fill in over the next hour or so.")
    else:
        # Stocks still waiting for a StockTwits reading are left out of this ranking rather than shown at a made-up 50
        top_social_df = (df_exchange.dropna(subset=["Social Score"])
                         .sort_values(by="Social Score", ascending=False).head(scan_depth).reset_index(drop=True))
        show_sector_shortfall(top_social_df, selected_sector)
        top_social_df.index = top_social_df.index + 1

        lead_stock = top_social_df.iloc[0]

        lead_col = st.container()
        with lead_col:
            st.success(
                f"🌟 **#1 Ranked Social Stock:** **{lead_stock['Ticker']}** ({lead_stock['Company']}) leads with a "
                f"Social Score of **{lead_stock['Social Score']} / 100** (Sentiment: **{_fmt_opt(lead_stock.get('Sentiment'), '{:.0f}')}**, "
                f"Buzz: **{_fmt_opt(lead_stock.get('Buzz'), '{:.0f}')}**, StockTwits: **{_fmt_opt(lead_stock.get('Msgs/Hr'), '{:.1f}')} posts/hr**, "
                f"Reddit: **{_fmt_opt(lead_stock.get('Reddit Mentions'), '{:,.0f}')} mentions** in 24h)."
            )

        display_cols_social = [
            "Ticker", "Company", "Sector", "Social Score", "Sentiment", "Buzz", "Polarity %",
            "Msgs/Hr", "Reddit Mentions", "Reddit 24h Change", "Trend Rank", "Watchers", "Composite Index", "View"
        ]
        display_cols_social = [c for c in display_cols_social if c in top_social_df.columns]

        csv_cols = [c for c in display_cols_social if c != "View"]
        csv_data = top_social_df[csv_cols].to_csv(index=True, index_label="Rank").encode("utf-8")
        with table_csv_slot.container():
            st.download_button(
                label=f"📥 Download Table CSV ({len(top_social_df)} rows)",
                data=csv_data,
                file_name=f"social_media_data_top{len(top_social_df)}_{now.strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv",
                type="secondary",
                **STRETCH,
            )

        social_event = st.dataframe(
            top_social_df[display_cols_social],
            column_config=COMMON_TABLE_CONFIG,
            **STRETCH,
            height=760,
            hide_index=False,
            on_select="rerun",
            selection_mode="single-row",
            key="df_social_interactive",
        )

        if hasattr(social_event, "selection"):
            sel_rows = getattr(social_event.selection, "rows", [])
            if sel_rows:
                open_deep_dive(top_social_df.iloc[sel_rows[0]]["Ticker"])


# ==============================================================================
# VIEW 6: BEST UPSIDE
# ==============================================================================
elif app_mode == "🚀 Best Upside":
    sec_col, _ = st.columns([4.4, 5.6])
    with sec_col:
        selected_sector = sector_selector("upside_sector")

    selected_tf = "Current"

    st.caption(
        f"Every scanned stock ranked by **12-Month Predicted Upside %** based on consensus Wall Street analyst price targets (top **{scan_depth}** shown)."
    )

    with st.spinner(f"Scanning {len(scan_request(selected_sector))} stocks..."):
        df_exchange = get_persistent_universe_data(selected_sector, selected_tf)
    show_coverage_report(df_exchange)

    if df_exchange.empty:
        st.warning("Data fetch in progress or timed out. Click 🔄 Refresh Data at the top of the sidebar.")
    else:
        top_upside_df = _ensure_leverage_columns(df_exchange).sort_values(by="Upside", ascending=False).head(scan_depth).reset_index(drop=True)
        show_sector_shortfall(top_upside_df, selected_sector)
        top_upside_df.index = top_upside_df.index + 1

        top_upside_stock = top_upside_df.iloc[0]

        lead_col = st.container()
        with lead_col:
            st.success(
                f"🌟 **#1 Ranked Upside:** **{top_upside_stock['Ticker']}** ({top_upside_stock['Company']}) leads with an expected 12-month upside of "
                f"**{top_upside_stock['Upside']:+.1f}%** (Analysts: **{top_upside_stock['Analysts']}**, Analyst Buy: **{top_upside_stock['Analyst Buy %']}%**, Composite: **{top_upside_stock['Composite Index']}**)."
            )

        display_cols = [
            "Ticker", "Company", "Sector", "Price",
            "Upside", "Analysts", "Analyst Buy %", "Net Debt/EBITDA", "Debt/Equity",
            "Social Score", "Composite Index", "View"
        ]

        csv_cols = [c for c in display_cols if c != "View"]
        csv_data = top_upside_df[csv_cols].to_csv(index=True, index_label="Rank").encode("utf-8")
        with table_csv_slot.container():
            st.download_button(
                label=f"📥 Download Table CSV ({len(top_upside_df)} rows)",
                data=csv_data,
                file_name=f"best_upside_top{len(top_upside_df)}_{now.strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv",
                type="secondary",
                **STRETCH,
            )

        upside_event = st.dataframe(
            top_upside_df[display_cols],
            column_config=COMMON_TABLE_CONFIG,
            **STRETCH,
            height=760,
            hide_index=False,
            on_select="rerun",
            selection_mode="single-row",
            key="df_upside_interactive",
        )

        if hasattr(rail_event := upside_event, "selection"):
            sel_rows = getattr(rail_event.selection, "rows", [])
            if sel_rows:
                open_deep_dive(top_upside_df.iloc[sel_rows[0]]["Ticker"])


# ==============================================================================
# VIEW 7: P/E DISCOUNTS
# ==============================================================================
elif app_mode == "🏷️ P/E Discounts":
    sec_col, _ = st.columns([4.4, 5.6])
    with sec_col:
        selected_sector = sector_selector("pe_sector")

    selected_tf = "Current"

    st.caption(
        f"Every scanned stock checked for the deepest valuation discount (top **{scan_depth}** shown) "
        "relative to their respective **S&P 500 Sector Benchmark Forward P/E**."
    )

    with st.spinner(f"Loading data for {selected_sector}..."):
        df_sector_data = get_persistent_universe_data(selected_sector, selected_tf)
    show_coverage_report(df_sector_data)

    if df_sector_data.empty:
        st.warning(f"No stock data retrieved for {selected_sector}. Click 🔄 Refresh Data at the top of the sidebar.")
    else:
        valid_discounts = _ensure_leverage_columns(df_sector_data).dropna(subset=["PE Discount %"]).copy()
        valid_discounts = valid_discounts[valid_discounts["PE Discount %"] > -500.0]

        if valid_discounts.empty:
            st.info(f"No valid forward P/E figures currently available for {selected_sector}.")
        else:
            top_discounts_df = valid_discounts.sort_values(by="PE Discount %", ascending=False).head(scan_depth).reset_index(drop=True)
            show_sector_shortfall(top_discounts_df, selected_sector, what="stocks with a positive forward P/E")
            top_discounts_df.index = top_discounts_df.index + 1

            best_discount_stock = top_discounts_df.iloc[0]

            lead_col = st.container()
            with lead_col:
                st.success(
                    f"🌟 **Top Valuation Discount in {selected_sector}:** **{best_discount_stock['Ticker']}** ({best_discount_stock['Company']}) trades at a "
                    f"**{best_discount_stock['PE Discount %']:+.1f}% discount** (Stock Fwd P/E: **{best_discount_stock['Forward PE']}x** vs. Sector Benchmark: **{best_discount_stock['Sector PE']}x**)."
                )

            display_cols = [
                "Ticker", "Company", "Sector", "Price",
                "Forward PE", "Sector PE", "PE Discount %", "Net Debt/EBITDA", "Debt/Equity",
                "Upside", "Composite Index", "View"
            ]

            csv_cols = [c for c in display_cols if c != "View"]
            csv_data = top_discounts_df[csv_cols].to_csv(index=True, index_label="Rank").encode("utf-8")
            with table_csv_slot.container():
                st.download_button(
                    label=f"📥 Download Table CSV ({len(top_discounts_df)} rows)",
                    data=csv_data,
                    file_name=f"sector_discounts_top{len(top_discounts_df)}_{selected_sector.lower().replace(' ', '_')}_{now.strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv",
                    type="secondary",
                    **STRETCH,
                )

            disc_event = st.dataframe(
                top_discounts_df[display_cols],
                column_config=COMMON_TABLE_CONFIG,
                **STRETCH,
                height=760,
                hide_index=False,
                on_select="rerun",
                selection_mode="single-row",
                key="df_discount_interactive",
            )

            if hasattr(disc_event, "selection"):
                sel_rows = getattr(disc_event.selection, "rows", [])
                if sel_rows:
                    clicked_sym = top_discounts_df.iloc[sel_rows[0]]["Ticker"]
                    open_deep_dive(clicked_sym)


# ==============================================================================
# VIEW 8: BETA VOLATILITY SCREENER (ACTIVE DEFENSIVE & AGGRESSIVE SCANNERS)
# ==============================================================================
elif app_mode == "⚡ Beta Volatility":
    beta_mode_col, sec_col, _ = st.columns([4.4, 3.8, 1.8])
    with sec_col:
        selected_sector = sector_selector("beta_sector")
    selected_tf = "Current"
    with beta_mode_col:
        beta_mode_options = [
            "🔥 High Beta (Volatile / Aggressive: Beta > 1.0)",
            "🛡️ Low Beta (Defensive / Stable: Beta < 1.0)"
        ]
        selected_beta_mode = st.selectbox(
            "Select Volatility Profile:",
            options=beta_mode_options,
            index=0,
            key="beta_profile_selectbox",
        )

    is_high_beta = "High Beta" in selected_beta_mode
    status_desc = "High Beta" if is_high_beta else "Low Beta (Defensive)"
    mode_file_tag = "high_beta" if is_high_beta else "low_beta"

    if is_high_beta:
        st.caption(
            "🔥 **High Beta Screener:** Scans aggressive, high-volatility growth & cyclical equities with $\\beta > 1.0$, sorted from highest to lowest volatility. "
            "These equities amplify broader market rallies but carry steeper drawdown risks during pullbacks."
        )
    else:
        st.caption(
            "🛡️ **Low Beta Screener:** Actively scans dedicated defensive equities across Utilities, Consumer Staples, Healthcare, Telecom, and Conservative REITs with $\\beta < 1.0$. "
            "Sorted from lowest volatility upward to populate actual capital preservation compounders."
        )

    with st.spinner(f"Scanning {len(scan_request(selected_sector))} stocks for {status_desc} names..."):
        df_beta_scanned = get_persistent_universe_data(selected_sector, selected_tf)
    show_coverage_report(df_beta_scanned)

    if df_beta_scanned.empty:
        st.warning("Data fetch in progress or timed out. Click 🔄 Refresh Data at the top of the sidebar.")
    else:
        valid_beta_df = _ensure_leverage_columns(df_beta_scanned)
        valid_beta_df = valid_beta_df[valid_beta_df["Beta"].notna()].copy()

        if is_high_beta:
            filtered_beta_df = valid_beta_df[valid_beta_df["Beta"] > 1.0].sort_values(by="Beta", ascending=False).head(scan_depth).reset_index(drop=True)
        else:
            filtered_beta_df = valid_beta_df[valid_beta_df["Beta"] < 1.0].sort_values(by="Beta", ascending=True).head(scan_depth).reset_index(drop=True)

        show_sector_shortfall(filtered_beta_df, selected_sector, what=f"{status_desc} stocks")
        if filtered_beta_df.empty:
            st.info(f"No stocks found meeting the {status_desc} threshold. Try expanding the market scan scope.")
        else:
            filtered_beta_df.index = filtered_beta_df.index + 1
            lead_stock = filtered_beta_df.iloc[0]

            lead_col = st.container()
            with lead_col:
                st.success(
                    f"🌟 **Top {status_desc} Stock:** **{lead_stock['Ticker']}** ({lead_stock['Company']}) • **{lead_stock['Sector']}** "
                    f"leads with a **Beta of {lead_stock['Beta']:.2f}** (PEG: **{lead_stock['PEG']}x**, Fwd P/E: **{lead_stock['Forward PE']}x**, "
                    f"Composite Index: **{lead_stock['Composite Index']} / 100**, Upside: **{lead_stock['Upside']:+.1f}%**)."
                )

            display_cols_beta = [
                "Ticker", "Company", "Sector", "Price",
                "Beta", "PEG", "Forward PE", "Net Debt/EBITDA", "Debt/Equity", "Composite Index",
                "Social Score", "Analyst Buy %", "Upside", "View"
            ]

            csv_cols = [c for c in display_cols_beta if c != "View"]
            csv_data = filtered_beta_df[csv_cols].to_csv(index=True, index_label="Rank").encode("utf-8")
            with table_csv_slot.container():
                st.download_button(
                    label=f"📥 Download Table CSV ({len(filtered_beta_df)} rows)",
                    data=csv_data,
                    file_name=f"{mode_file_tag}_stocks_top{len(filtered_beta_df)}_{now.strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv",
                    type="secondary",
                    **STRETCH,
                )

            beta_event = st.dataframe(
                filtered_beta_df[display_cols_beta],
                column_config=COMMON_TABLE_CONFIG,
                **STRETCH,
                height=760,
                hide_index=False,
                on_select="rerun",
                selection_mode="single-row",
                key="df_beta_interactive",
            )

            if hasattr(beta_event, "selection"):
                sel_rows = getattr(beta_event.selection, "rows", [])
                if sel_rows:
                    clicked_sym = filtered_beta_df.iloc[sel_rows[0]]["Ticker"]
                    open_deep_dive(clicked_sym)
