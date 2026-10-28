"""PortfolioScan — Streamlit portfolio analyzer: stocks, ETFs, SCPI, euro funds and cash.

Resolves each stock's name / country / sector through a cascade of sources:
FinanceDatabase -> yfinance -> OpenFIGI (each one fills what the previous missed).
FinanceDatabase is read from an embedded snapshot (data/fd_equities.csv.gz), not the library.
ETFs are spread over their countries and sectors from data/etf_*.csv.gz; broker exports are read by importer.py.
"""
import os
import html
import io
import json
import re
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pycountry
import requests
import streamlit as st
import yfinance as yf
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.polygon import orient

from importer import SEPARATORS, column_labels, detect, extract, read_tables

st.set_page_config(page_title="PortfolioScan", page_icon="◆", layout="wide")

# ----------------------------------------------------------------------------
# Design tokens (dark palette)
# ----------------------------------------------------------------------------
PLANE = "#0a0c10"
STAGE = "#07090d"   # page / globe backdrop
SURFACE = "#12151b"
INK = "#f0efec"
INK2 = "#c3c2b7"
MUTED = "#898781"
GRID = "#23262d"
AXIS = "#383835"
BLUE = "#3987e5"
RED = "#e66767"
GOOD = "#0ca30c"
WARN = "#fab219"
CRIT = "#d03b3b"
# Sequential blue, low values recede toward the dark surface.
SEQ = ["#104281", "#184f95", "#1c5cab", "#2a78d6", "#3987e5", "#5598e7", "#86b6ef", "#cde2fb"]

SOURCES = ["FinanceDatabase", "yfinance", "OpenFIGI"]
SRC_TAG = {"FinanceDatabase": "FD", "yfinance": "YF", "OpenFIGI": "FIGI", "SYS": "SYS"}
FIELDS = ("name", "country", "sector")
# Not real countries/sectors: shown grey and last, never on the globe or in the country/sector counts.
# "Other" is an ETF's tail beyond its top 10 countries/sectors; "Unknown" is exposure that could not be identified.
CASH_LABEL = "Euro funds & cash"  # country and sector of positions that have neither
BOND_SECTOR = "Bonds"              # sector of bond ETFs: GICS sectors describe companies, not bonds
FUND_CASH = "Cash & Derivatives"   # an ETF's own cash and futures lines
NEUTRAL = ("Unknown", "Other", CASH_LABEL, BOND_SECTOR, FUND_CASH)
NEUTRAL_LABEL = {"Other": "Other (outside top 10)"}
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

# yfinance uses Morningstar sector names; FinanceDatabase uses GICS. Normalise to GICS.
YF_TO_GICS = {
    "Technology": "Information Technology",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Healthcare": "Health Care",
    "Financial Services": "Financials",
    "Basic Materials": "Materials",
    "Communication Services": "Communication Services",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
}
OPENFIGI_NON_STOCK = {
    "ETP", "Open-End Fund", "Closed-End Fund", "Mutual Fund", "Fund of Funds",
    "Unit", "Right", "Warrant", "Index", "Equity Index",
}
COUNTRY_ALIASES = {
    "south korea": "KOR", "korea": "KOR", "russia": "RUS", "taiwan": "TWN",
    "vietnam": "VNM", "iran": "IRN", "czech republic": "CZE", "turkey": "TUR",
    "bolivia": "BOL", "venezuela": "VEN", "tanzania": "TZA", "macau": "MAC",
    "macao": "MAC", "hong kong": "HKG", "laos": "LAO", "syria": "SYR",
    "moldova": "MDA", "ivory coast": "CIV", "cote d'ivoire": "CIV",
    "curacao": "CUW", "british virgin islands": "VGB", "united states": "USA",
    "united kingdom": "GBR", "netherlands": "NLD", "bermuda": "BMU",
    "cayman islands": "CYM", "jersey": "JEY", "guernsey": "GGY", "isle of man": "IMN",
}

# ----------------------------------------------------------------------------
# Styling
# ----------------------------------------------------------------------------
TERM_CSS = f"""
/* Terminal */
.term {{
  border-radius: 12px; border: 1px solid rgba(255,255,255,0.08); overflow: hidden;
  background: #07090c; box-shadow: inset 0 0 40px rgba(57,135,229,0.05);
}}
.term-bar {{
  display: flex; align-items: center; gap: 6px; padding: .5rem .8rem;
  background: rgba(255,255,255,0.03); border-bottom: 1px solid rgba(255,255,255,0.06);
  font-family: 'JetBrains Mono', monospace; font-size: 11px; color: {MUTED};
}}
.term-bar i {{ width: 9px; height: 9px; border-radius: 50%; background: #2c2f36; display: inline-block; }}
.term-bar .t {{ margin-left: .6rem; letter-spacing: .08em; }}
.term-body {{
  height: 360px; overflow-y: auto; padding: .6rem .8rem;
  display: flex; flex-direction: column-reverse;   /* keeps the view pinned to the newest line */
  font-family: 'JetBrains Mono', monospace; font-size: 12px; line-height: 1.65;
}}
.ln {{ display: flex; gap: .7rem; white-space: nowrap; }}
.ln .ts {{ color: #4b4e56; }}
.ln .src {{
  display: inline-block; min-width: 3.4em; text-align: center; border-radius: 4px;
  padding: 0 .35em; font-size: 10.5px; font-weight: 700; letter-spacing: .06em;
  border: 1px solid rgba(255,255,255,0.12); color: {INK2};
}}
.ln .src.FD {{ border-color: rgba(57,135,229,.5); color: #86b6ef; }}
.ln .src.YF {{ border-color: rgba(134,182,239,.35); color: #cde2fb; }}
.ln .src.FIGI {{ border-color: rgba(205,226,251,.25); color: {INK}; }}
.ln .src.SYS {{ border-color: rgba(255,255,255,.1); color: {MUTED}; }}
.ln .ic {{ width: 1em; text-align: center; }}
.ln.ok .ic {{ color: {GOOD}; }}  .ln.ok .msg {{ color: {INK}; }}
.ln.miss .ic, .ln.miss .msg {{ color: {MUTED}; }}
.ln.warn .ic {{ color: {WARN}; }} .ln.warn .msg {{ color: #f3d58c; }}
.ln.err .ic {{ color: {CRIT}; }}  .ln.err .msg {{ color: #f0a3a3; }}
.ln.info .ic {{ color: {BLUE}; }} .ln.info .msg {{ color: {INK2}; }}
.ln.head .msg {{ color: {INK}; font-weight: 700; }}
.ln.head .ic {{ color: {BLUE}; }}

"""

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap');

html, body, [data-testid="stAppViewContainer"] {{
  font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif;
}}
/* The whole page is the globe's "space": deep black with a soft blue glow, no grid. */
[data-testid="stAppViewContainer"] {{
  background:
    radial-gradient(1100px 700px at 50% 32%, rgba(57,135,229,0.10), transparent 62%),
    radial-gradient(700px 400px at 50% 110%, rgba(57,135,229,0.05), transparent 60%),
    {STAGE};
}}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stSidebar"] {{
  background: rgba(10,13,18,0.96);
  border-right: 1px solid rgba(255,255,255,0.06);
}}
.block-container {{ padding-top: 1.6rem; max-width: 1680px; }}

.hero {{ margin-bottom: .8rem; }}
.hero-tag {{
  font-family: 'JetBrains Mono', monospace; font-size: 11px; letter-spacing: .22em;
  color: {BLUE}; text-transform: uppercase;
}}
.hero h1 {{
  font-size: 2rem; font-weight: 700; letter-spacing: -0.03em; margin: .2rem 0 .3rem; padding: 0;
  color: {INK};
}}
.hero h1 span {{
  background: linear-gradient(90deg, {BLUE}, #86b6ef);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}}
.hero p {{ color: {MUTED}; margin: 0; font-size: .95rem; }}

.section-label {{
  font-family: 'JetBrains Mono', monospace; font-size: 11px; letter-spacing: .18em;
  text-transform: uppercase; color: {MUTED}; margin: 1.6rem 0 .6rem;
  display: flex; align-items: center; gap: .6rem;
}}
.section-label::after {{ content: ""; flex: 1; height: 1px; background: rgba(255,255,255,0.07); }}

.card {{
  background: linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.015));
  border: 1px solid rgba(255,255,255,0.07); border-radius: 14px; padding: 1rem 1.1rem;
}}

.kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }}
.kpi {{
  background: linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.012));
  border: 1px solid rgba(255,255,255,0.07); border-radius: 14px; padding: .9rem 1rem;
  position: relative; overflow: hidden;
}}
.kpi::before {{
  content: ""; position: absolute; inset: 0 0 auto 0; height: 2px;
  background: linear-gradient(90deg, {BLUE}, transparent);
}}
.kpi .k {{ font-family: 'JetBrains Mono', monospace; font-size: 10.5px; letter-spacing: .16em; text-transform: uppercase; color: {MUTED}; }}
.kpi .v {{ font-size: 1.7rem; font-weight: 600; color: {INK}; margin-top: .25rem; letter-spacing: -0.02em; }}
.kpi .d {{ font-family: 'JetBrains Mono', monospace; font-size: 12px; color: {INK2}; margin-top: .1rem; }}

/* Source pipeline */
.pipe {{ display: flex; align-items: stretch; gap: 10px; flex-wrap: wrap; }}
.node {{
  flex: 1 1 180px; border-radius: 12px; padding: .75rem .9rem;
  border: 1px solid rgba(255,255,255,0.08); background: rgba(255,255,255,0.02);
  transition: all .2s ease;
}}
.node .idx {{ font-family: 'JetBrains Mono', monospace; font-size: 10px; color: {MUTED}; letter-spacing: .14em; }}
.node .nm {{ font-weight: 600; color: {INK}; margin-top: .15rem; }}
.node .sub {{ font-size: 12px; color: {MUTED}; }}
.node .hits {{ font-family: 'JetBrains Mono', monospace; font-size: 12px; color: {INK2}; margin-top: .45rem; }}
.node.active {{
  border-color: {BLUE};
  box-shadow: 0 0 0 1px rgba(57,135,229,.35), 0 0 24px rgba(57,135,229,.25);
  background: rgba(57,135,229,0.08);
}}
.node.active .idx::after {{ content: "  ● QUERYING"; color: {BLUE}; animation: blink 1s infinite; }}
.arrow {{ display: flex; align-items: center; color: {AXIS}; font-family: 'JetBrains Mono', monospace; }}
@keyframes blink {{ 50% {{ opacity: .25; }} }}


{TERM_CSS}
.spec {{ font-family: 'JetBrains Mono', monospace; font-size: 12.5px; color: {INK2}; }}
.spec table {{ border-collapse: collapse; margin-top: .5rem; width: 100%; }}
.spec td, .spec th {{ border: 1px solid rgba(255,255,255,0.08); padding: .35rem .6rem; text-align: left; }}
.spec th {{ color: {BLUE}; font-weight: 500; }}

.pill {{
  display: inline-block; font-family: 'JetBrains Mono', monospace; font-size: 11px;
  padding: .1rem .55rem; border-radius: 999px; border: 1px solid rgba(57,135,229,.45);
  color: #86b6ef; margin: 0 .25rem .25rem 0;
}}
.note {{ font-size: 12.5px; color: {MUTED}; }}


[data-testid="stFileUploaderDropzone"] {{
  background: rgba(57,135,229,0.05); border: 1px dashed rgba(57,135,229,0.5); border-radius: 14px;
}}
.stTabs [data-baseweb="tab-list"] {{ gap: 4px; }}
.stTabs [data-baseweb="tab"] {{ font-family: 'JetBrains Mono', monospace; font-size: 12.5px; letter-spacing: .06em; }}

/* Desktop-only notice: covers the whole app on phones, tablets and narrow windows. */
[data-testid="stElementContainer"]:has(.mobile-block) {{ display: none; }}
@media (max-width: 900px), (hover: none) and (pointer: coarse) {{
  [data-testid="stElementContainer"]:has(.mobile-block) {{ display: block; }}
  html, body {{ overflow: hidden !important; }}
}}
.mobile-block {{
  position: fixed; inset: 0; z-index: 2147483647; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 1rem; padding: 2rem; text-align: center;
  background: radial-gradient(600px 400px at 50% 35%, rgba(57,135,229,0.18), transparent 70%), {STAGE};
  font-family: 'Inter', system-ui, sans-serif; color: {INK};
}}
.mobile-block .logo {{ font-weight: 700; font-size: 1.05rem; }}
.mobile-block .logo i {{ font-style: normal; color: {BLUE}; margin-right: .35rem; }}
.mobile-block .icon {{ font-size: 2.6rem; filter: drop-shadow(0 0 18px rgba(57,135,229,.7)); }}
.mobile-block h2 {{ margin: 0; padding: 0; font-size: 1.5rem; font-weight: 700; letter-spacing: -.02em; color: {INK}; }}
.mobile-block p {{ margin: 0; max-width: 22rem; font-size: .95rem; line-height: 1.6; color: {INK2}; }}
.mobile-block .credit {{ margin-top: 1rem; font: 11px 'JetBrains Mono', monospace; color: #86b6ef; text-decoration: none; }}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)
st.markdown(
    '<div class="mobile-block"><div class="logo"><i>◆</i>PortfolioScan</div><div class="icon">🖥️</div>'
    '<h2>Desktop only</h2>'
    '<p>PortfolioScan is built for a computer screen, with its 3D globe, hover details and side panels. '
    'Please open this page on a desktop or laptop.</p>'
    '<a class="credit" href="https://www.linkedin.com/in/gabin-le/" target="_blank" rel="noopener noreferrer">'
    'Made by Gabin Lê · 2026 ↗</a></div>',
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------
# State
# ----------------------------------------------------------------------------
def init_state():
    ss = st.session_state
    ss.setdefault("results", {})   # isin -> resolved record
    ss.setdefault("log", [])       # (ts, src, msg, level)
    ss.setdefault("sims", [])      # theoretical positions: {"isin", "value"}
    ss.setdefault("pf", None)      # loaded portfolio: {"name", "positions"} (imports) or {"name", "data"} (demo)
    ss.setdefault("imports", None)    # files waiting for review: [{"name", "data"}]
    ss.setdefault("import_step", 0)   # index of the file under review
    ss.setdefault("import_done", [])  # confirmed files: [{"name", "positions"}]
    ss.setdefault("overrides", {})   # isin -> {"country": {label: %}, "sector": {label: %}} set by hand
    ss.setdefault("start_kick", None)  # "pending" / "armed": start the analysis once the import dialog has closed
    ss.setdefault("pending_sim", None)  # test position waiting for its scan: {"isin", "value"}
    ss.setdefault("flash", None)   # (message, icon) toast to show on the next run


init_state()


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def isin_checksum_ok(isin: str) -> bool:
    digits = "".join(str(int(c, 36)) for c in isin[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            n = n - 9 if n > 9 else n
        total += n
    return (10 - total % 10) % 10 == int(isin[-1])


def to_float(x):
    if isinstance(x, (int, float, np.number)) and not pd.isna(x):
        return float(x)
    if isinstance(x, str):
        s = x.replace(" ", "").replace(" ", "").replace("€", "").replace("$", "")
        if s.count(",") == 1 and "." not in s:
            s = s.replace(",", ".")
        s = s.replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def iso3_of(country: str | None) -> str | None:
    if not country:
        return None
    key = country.strip().lower()
    if key in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[key]
    try:
        return pycountry.countries.lookup(country).alpha_3
    except LookupError:
        try:
            return pycountry.countries.search_fuzzy(country)[0].alpha_3
        except LookupError:
            return None


def country_from_isin_prefix(isin: str) -> str | None:
    c = pycountry.countries.get(alpha_2=isin[:2])
    if not c:
        return None
    return getattr(c, "common_name", None) or c.name


def special_kind(code: str) -> str | None:
    """'scpi', 'euro_fund' or 'cash' for the pseudo-codes insurers and brokers give these positions
    (SCPI00004799, SUR000001730, FONDSEURNG, NETIS075, CASH); None for a regular ISIN."""
    c = str(code).strip().upper()
    if c.startswith("SCPI"):
        return "scpi"
    if c.startswith(("CASH", "LIQUIDIT", "ESPECE", "ESPÈCE")):
        return "cash"
    if re.match(r"^(FONDS|SUR\d|NETIS|EURO)", c):  # no real ISIN starts with SU, and FONDS/EURO are not codes
        return "euro_fund"
    return None


def fmt_money(v: float) -> str:
    if abs(v) >= 1e9:
        return f"{v / 1e9:,.2f}B"
    if abs(v) >= 1e6:
        return f"{v / 1e6:,.2f}M"
    if abs(v) >= 1e3:
        return f"{v / 1e3:,.1f}k"
    return f"{v:,.0f}"


@st.cache_data(show_spinner=False)
def parse_portfolio(data: bytes):
    """Read ISIN / value pairs from an Excel workbook, or from CSV text (used by the demo portfolio)."""
    is_excel = data[:2] == b"PK" or data[:4] == b"\xd0\xcf\x11\xe0"  # .xlsx is a zip, .xls an OLE file
    raw = (pd.read_excel(io.BytesIO(data), header=None) if is_excel
           else pd.read_csv(io.BytesIO(data), header=None, sep=None, engine="python"))
    if raw.shape[1] < 2:
        raise ValueError("The file needs at least two columns: ISIN, position value.")
    raw = raw.iloc[:, :2]
    raw.columns = ["isin", "value"]
    raw["isin"] = raw["isin"].astype(str).str.strip().str.upper()
    raw["value"] = raw["value"].map(to_float)
    valid = (raw["isin"].str.match(ISIN_RE) | raw["isin"].map(special_kind).notna()) & raw["value"].notna()
    rejected = raw[~valid & (raw["isin"] != "NAN")]
    df = raw[valid].groupby("isin", as_index=False, sort=False)["value"].sum()
    return df, rejected


DATA_DIR = Path(__file__).resolve().parent / "data"
DEMO_FILE = DATA_DIR / "demo_portfolio.csv"     # isin,value,name,account: 3 accounts, stocks, ETFs, SCPI, cash
DEMO_RESULTS = DATA_DIR / "demo_results.json"   # its lookups, pre-scanned so the demo opens instantly


def load_demo():
    """Load the demo portfolio, seeding the lookup cache with its pre-scanned results when available."""
    ss = st.session_state
    ss.pf = {"name": "Demo portfolio", "positions": pd.read_csv(DEMO_FILE, keep_default_na=False)}
    if DEMO_RESULTS.exists():
        cached = json.loads(DEMO_RESULTS.read_text(encoding="utf-8"))
        ss.results = {**cached, **ss.results}
        ss.log.append((datetime.now().strftime("%H:%M:%S.%f")[:-3], "SYS",
                       f"demo portfolio loaded · {len(cached)} assets from the pre-scanned cache", "head"))


# ----------------------------------------------------------------------------
# Data sources
# ----------------------------------------------------------------------------
FD_SNAPSHOT = DATA_DIR / "fd_equities.csv.gz"  # slim FinanceDatabase extract, refreshed offline


ETF_FUNDS = DATA_DIR / "etf_funds.csv.gz"        # one row per ETF, built offline from issuers' holdings data
ETF_EXPOSURE = DATA_DIR / "etf_exposure.csv.gz"  # each ETF's 10 largest countries and sectors, the rest as "Other"


@st.cache_resource(show_spinner=False)
def load_etf_snapshot() -> dict:
    """ISIN -> fund fields plus {"country": {label: weight}, "sector": {...}}. Empty if the files are missing."""
    if not (ETF_FUNDS.exists() and ETF_EXPOSURE.exists()):
        return {}
    funds = pd.read_csv(ETF_FUNDS, keep_default_na=False)  # "NA" is Namibia, not a missing value
    expo = pd.read_csv(ETF_EXPOSURE, keep_default_na=False)
    out = {f["isin"]: {**f, "country": {}, "sector": {}} for f in funds.to_dict("records")}
    for r in expo.itertuples(index=False):
        if r.isin in out and r.weight > 0:  # short derivative legs carry negative weights
            out[r.isin][r.dimension][r.label] = r.weight
    return out


def position_name(isin: str) -> str:
    etf = load_etf_snapshot().get(isin)
    return (etf or st.session_state.results.get(isin, {})).get("name") or isin


def server_figi_key() -> str | None:
    """OpenFIGI key from the environment or from Streamlit secrets (Community Cloud's Secrets panel)."""
    key = os.environ.get("OPENFIGI_API_KEY")
    if not key:
        try:
            key = st.secrets.get("OPENFIGI_API_KEY")
        except Exception:  # noqa: BLE001 — no secrets file configured
            key = None
    return key or None


@st.cache_resource(show_spinner=False)
def load_financedatabase():
    """ISIN -> listing details and company name -> sector, from the embedded FinanceDatabase snapshot."""
    df = pd.read_csv(FD_SNAPSHOT, dtype=str)
    by_isin = df[df["isin"].notna()].set_index("isin")[["symbol", "name", "country", "sector"]].to_dict("index")
    named = df.dropna(subset=["sector", "name"]).copy()
    named["_k"] = named["name"].str.lower().str.strip()
    by_name = named.drop_duplicates("_k").set_index("_k")["sector"].to_dict()
    by_symbol = df.dropna(subset=["symbol", "country"]).drop_duplicates("symbol").set_index("symbol")["country"].to_dict()
    return by_isin, by_name, by_symbol


FD_ETF_TICKERS = DATA_DIR / "fd_etf_tickers.csv.gz"  # every Yahoo ticker of each ETF (from FinanceDatabase)
YF_FUND_SECTORS = {  # Yahoo fund sector keys -> GICS
    "realestate": "Real Estate", "consumer_cyclical": "Consumer Discretionary", "basic_materials": "Materials",
    "consumer_defensive": "Consumer Staples", "technology": "Information Technology",
    "communication_services": "Communication Services", "financial_services": "Financials",
    "healthcare": "Health Care", "industrials": "Industrials", "energy": "Energy", "utilities": "Utilities",
}
SUFFIX_COUNTRY = {  # Yahoo ticker suffix -> country of the listing
    "T": "Japan", "AX": "Australia", "SI": "Singapore", "HK": "Hong Kong", "L": "United Kingdom", "PA": "France",
    "DE": "Germany", "F": "Germany", "AS": "Netherlands", "SW": "Switzerland", "MI": "Italy", "MC": "Spain",
    "ST": "Sweden", "CO": "Denmark", "HE": "Finland", "OL": "Norway", "TO": "Canada", "V": "Canada",
    "KS": "South Korea", "KQ": "South Korea", "TW": "Taiwan", "TWO": "Taiwan", "SS": "China", "SZ": "China",
    "NS": "India", "BO": "India", "BR": "Belgium", "LS": "Portugal", "VI": "Austria", "IR": "Ireland",
    "NZ": "New Zealand", "SA": "Brazil", "MX": "Mexico", "JO": "South Africa", "KL": "Malaysia", "BK": "Thailand",
    "JK": "Indonesia", "TA": "Israel", "SR": "Saudi Arabia", "IS": "Turkey", "WA": "Poland",
}
# A fund named after one country (and no region) holds that country.
REGION_WORDS = re.compile(r"\b(world|global|europe|european|euro|emu|asia|asian|pacific|emerging|nordic|"
                          r"international|ex|developed|america|americas|latin|africa|frontier|eafe|acwi|all.country)\b",
                          re.I)
COUNTRY_WORDS = [
    (r"\bjapan(ese)?\b|\btopix\b|\bnikkei\b", "Japan"), (r"\bswitzerland\b|\bswiss\b|\bsmi\b", "Switzerland"),
    (r"\bgerman[y]?\b|\bdax\b", "Germany"), (r"\bfrance\b|\bfrench\b|\bcac\b", "France"),
    (r"\bunited kingdom\b|\buk\b|\bftse 100\b|\bftse 250\b", "United Kingdom"),
    (r"\bunited states\b|\busa\b|\bus\b|\bs&p 500\b|\bnasdaq\b|\brussell\b", "United States"),
    (r"\bchina\b|\bchinese\b|\bcsi 300\b", "China"), (r"\bindia(n)?\b|\bnifty\b", "India"),
    (r"\bbrazil\b", "Brazil"), (r"\bcanada\b|\bcanadian\b", "Canada"), (r"\baustralia(n)?\b", "Australia"),
    (r"\bkorea(n)?\b", "South Korea"), (r"\btaiwan\b", "Taiwan"), (r"\bital(y|ian)\b", "Italy"),
    (r"\bspain\b|\bspanish\b|\bibex\b", "Spain"), (r"\bnetherlands\b|\bdutch\b|\baex\b", "Netherlands"),
    (r"\bsweden\b|\bswedish\b", "Sweden"), (r"\bmexico\b", "Mexico"), (r"\bsouth africa\b", "South Africa"),
    (r"\bsaudi\b", "Saudi Arabia"), (r"\bturkey\b", "Turkey"), (r"\bpoland\b", "Poland"),
    (r"\bindonesia\b", "Indonesia"), (r"\bhong kong\b", "Hong Kong"), (r"\bsingapore\b", "Singapore"),
    (r"\bvietnam\b", "Vietnam"), (r"\bbelgium\b", "Belgium"), (r"\baustria\b", "Austria"),
    (r"\bnorway\b", "Norway"), (r"\bdenmark\b", "Denmark"), (r"\bfinland\b", "Finland"),
]


@st.cache_resource(show_spinner=False)
def load_fd_etf_tickers() -> dict:
    """ISIN -> Yahoo tickers, main venues first (they carry Yahoo's fund data most often)."""
    if not FD_ETF_TICKERS.exists():
        return {}
    t = pd.read_csv(FD_ETF_TICKERS)
    pref = (".DE", ".PA", ".AS", ".L", ".SW", ".MI")
    t["rank"] = t["symbol"].map(lambda x: next((i for i, sfx in enumerate(pref) if x.endswith(sfx)), len(pref)))
    return t.sort_values(["isin", "rank"]).groupby("isin")["symbol"].apply(list).to_dict()


def country_in_name(name: str) -> str | None:
    if not name or REGION_WORDS.search(name):
        return None
    found = {country for pattern, country in COUNTRY_WORDS if re.search(pattern, name, re.I)}
    return found.pop() if len(found) == 1 else None


def yahoo_etf(isin: str, first: str | None, name: str | None, fd_maps, log) -> dict | None:
    """An ETF missing from our snapshot, estimated from Yahoo: its own sector weights, and countries from its name
    when it names a single country, else from its top 10 holdings (the rest stays Unknown). None if Yahoo has no
    fund data on any of the ETF's listings."""
    candidates = list(dict.fromkeys(x for x in [first] + load_fd_etf_tickers().get(isin, []) if x))[:5]
    for sym in candidates:
        try:
            yt = yf.Ticker(sym)
            fund = yt.funds_data
            sw, th, ac = fund.sector_weightings or {}, fund.top_holdings, fund.asset_classes or {}
        except Exception:  # noqa: BLE001 — that listing has no fund data: try the next one
            continue
        if sw or (th is not None and len(th)):
            break
    else:
        log("yfinance", f"no fund data on {', '.join(candidates) or 'any listing'}", "miss")
        return None
    if not name:
        try:
            name = yt.info.get("longName") or yt.info.get("shortName")
        except Exception:  # noqa: BLE001
            name = None
    name = name or isin
    sectors = {YF_FUND_SECTORS.get(k, k): 100 * float(v) for k, v in sw.items() if v and v > 0}
    country = country_in_name(name)
    if country:
        countries, how = {country: 100.0}, f"countries from its name ({country})"
    else:
        countries, by_symbol = {}, (fd_maps[2] if fd_maps else {})
        for hsym, row in (th.iterrows() if th is not None else []):
            c = by_symbol.get(hsym) or SUFFIX_COUNTRY.get(hsym.rsplit(".", 1)[1] if "." in hsym else "", None) \
                or ("United States" if "." not in hsym else "Unknown")
            countries[c] = countries.get(c, 0) + 100 * float(row.get("Holding Percent") or 0)
        countries["Unknown"] = countries.get("Unknown", 0) + max(0.0, 100 - sum(countries.values()))
        how = f"countries from its top {0 if th is None else len(th)} holdings"
    log("yfinance", f"fund data on {sym} → {len(sectors)} sectors · {how}", "ok")
    return {"name": name, "ticker": sym, "issuer": "Yahoo Finance", "basis": f"estimate, {how}",
            "as_of": datetime.now().strftime("%d-%b-%Y"),
            "asset_class": "Bonds" if (ac.get("bondPosition") or 0) > 0.5 else "Equities",
            "country": countries, "sector": sectors}


def clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return None if s.lower() in ("", "nan", "none", "n/a") else s


def resolve(isin: str, log, fd_maps, figi_key: str | None) -> dict:
    rec = {"isin": isin, "name": None, "country": None, "sector": None,
           "ticker": None, "src": {}, "status": "not_found"}

    def fill(field, value, source):
        value = clean(value)
        if value and not rec[field]:
            rec[field] = value
            rec["src"][field] = source

    def missing():
        return [f for f in FIELDS if not rec[f]]

    # 1 ─ FinanceDatabase
    if fd_maps is None:
        log("FinanceDatabase", "source unavailable — skipped", "warn")
    else:
        log("FinanceDatabase", f"lookup isin={isin}", "info")
        row = fd_maps[0].get(isin)
        if row:
            rec["ticker"] = row["symbol"]
            for f in FIELDS:
                fill(f, row[f], "FinanceDatabase")
            log("FinanceDatabase", f"hit → {row['name']} [{row['symbol']}] · {row['country']} · {row['sector']}", "ok")
        else:
            log("FinanceDatabase", "no match in equity universe", "miss")

    # 2 ─ yfinance
    if missing():
        log("yfinance", f"query isin={isin} · need {', '.join(missing())}", "info")
        try:
            t = yf.Ticker(isin)
            info = t.info or {}
            qt = info.get("quoteType")
            if not (info.get("longName") or info.get("shortName")):
                log("yfinance", "no quote found", "miss")
            elif qt and qt != "EQUITY":
                etf = yahoo_etf(isin, t.ticker, info.get("longName") or info.get("shortName"), fd_maps, log)
                if etf:
                    rec.update(name=etf["name"], ticker=etf["ticker"], etf=etf, status="ok", src={"etf": "yfinance"})
                    log("SYS", f"{isin} resolved as an ETF (Yahoo estimate)", "ok")
                    return rec
                rec["status"] = "not_stock"
                rec["note"] = f"yfinance: quoteType {qt}"
                log("yfinance", f"quoteType={qt} — not a stock, and not in our ETF data: excluded", "warn")
                return rec
            else:
                rec["ticker"] = rec["ticker"] or t.ticker
                before = set(missing())
                fill("name", info.get("longName") or info.get("shortName"), "yfinance")
                fill("country", info.get("country"), "yfinance")
                sector = info.get("sector")
                fill("sector", YF_TO_GICS.get(sector, sector), "yfinance")
                got = before - set(missing())
                log("yfinance", f"hit [{t.ticker}] → filled {', '.join(sorted(got)) or 'nothing new'}",
                    "ok" if got else "miss")
        except Exception as e:  # noqa: BLE001 — any network/parse failure just falls through
            log("yfinance", f"error: {type(e).__name__}: {str(e)[:80]}", "err")

    # 3 ─ OpenFIGI
    if missing():
        log("OpenFIGI", f"POST /v3/mapping ID_ISIN={isin} · need {', '.join(missing())}", "info")
        headers = {"Content-Type": "application/json"}
        if figi_key:
            headers["X-OPENFIGI-APIKEY"] = figi_key
        try:
            for attempt in range(2):
                r = requests.post("https://api.openfigi.com/v3/mapping",
                                  json=[{"idType": "ID_ISIN", "idValue": isin}],
                                  headers=headers, timeout=15)
                if r.status_code == 429 and attempt == 0:
                    log("OpenFIGI", "rate limited (429) — backing off 7s", "warn")
                    time.sleep(7)
                    continue
                break
            r.raise_for_status()
            data = r.json()[0].get("data") or []
            stocks = [d for d in data if d.get("marketSector") == "Equity"
                      and d.get("securityType") not in OPENFIGI_NON_STOCK
                      and d.get("securityType2") not in OPENFIGI_NON_STOCK]
            if not data:
                log("OpenFIGI", "no mapping", "miss")
            elif not stocks:
                kind = data[0].get("securityType") or data[0].get("marketSector")
                if not rec["name"]:
                    etf = yahoo_etf(isin, None, data[0].get("name"), fd_maps, log)
                    if etf:
                        rec.update(name=etf["name"], ticker=etf["ticker"], etf=etf, status="ok",
                                   src={"etf": "yfinance"})
                        log("SYS", f"{isin} resolved as an ETF (Yahoo estimate)", "ok")
                        return rec
                    rec["status"] = "not_stock"
                    rec["note"] = f"OpenFIGI: securityType {kind}"
                    log("OpenFIGI", f"securityType={kind} — not a stock, and not in our ETF data: excluded", "warn")
                    return rec
                log("OpenFIGI", f"only non-stock listings ({kind})", "miss")
            else:
                d = stocks[0]
                rec["ticker"] = rec["ticker"] or d.get("ticker")
                fill("name", d.get("name"), "OpenFIGI")
                log("OpenFIGI", f"hit → {d.get('name')} [{d.get('ticker')} {d.get('exchCode')}] · {d.get('securityType')}", "ok")
                # OpenFIGI has no sector: try a name match back into FinanceDatabase.
                if not rec["sector"] and fd_maps is not None and d.get("name"):
                    sec = fd_maps[1].get(d["name"].lower().strip())
                    if sec:
                        fill("sector", sec, "FinanceDatabase (name match)")
                        log("FinanceDatabase", f"sector via name match → {sec}", "ok")
        except Exception as e:  # noqa: BLE001
            log("OpenFIGI", f"error: {type(e).__name__}: {str(e)[:80]}", "err")

    # Last resort for geography: the ISIN prefix is the issuer's country code.
    if not rec["country"]:
        c = country_from_isin_prefix(isin)
        if c:
            fill("country", c, "ISIN prefix")
            log("SYS", f"country inferred from ISIN prefix {isin[:2]} → {c}", "info")

    if rec["name"]:
        rec["status"] = "ok" if not missing() else "partial"
    level = {"ok": "ok", "partial": "warn"}.get(rec["status"], "err")
    summary = {"ok": "resolved", "partial": f"partial — missing {', '.join(missing())}", "not_found": "unresolved"}
    log("SYS", f"{isin} {summary[rec['status']]}", level)
    return rec


# ----------------------------------------------------------------------------
# Live scan UI
# ----------------------------------------------------------------------------
LEVEL_ICON = {"ok": "✓", "miss": "·", "warn": "!", "err": "✗", "info": "›", "head": "◆"}


def render_terminal(lines, live=True, limit=400) -> str:
    rows = []
    for ts, src, msg, level in reversed(lines[-limit:]):
        tag = SRC_TAG.get(src, src)
        rows.append(
            f'<div class="ln {level}"><span class="ts">{ts}</span>'
            f'<span class="src {tag}">{tag}</span><span class="ic">{LEVEL_ICON.get(level, "·")}</span>'
            f'<span class="msg">{html.escape(msg)}</span></div>'
        )
    title = "scan.log — live" if live else f"scan.log — {len(lines)} lines"
    return (f'<div class="term"><div class="term-bar"><i></i><i></i><i></i><span class="t">{title}</span></div>'
            f'<div class="term-body">{"".join(rows)}</div></div>')


def render_pipeline(active: str | None, hits: dict) -> str:
    subs = {
        "FinanceDatabase": "local equity universe · ISIN index",
        "yfinance": "Yahoo Finance quote profile",
        "OpenFIGI": "Bloomberg FIGI mapping API",
    }
    parts = []
    for i, s in enumerate(SOURCES):
        cls = "node active" if s == active else "node"
        parts.append(f'<div class="{cls}"><div class="idx">0{i + 1}</div><div class="nm">{s}</div>'
                     f'<div class="sub">{subs[s]}</div><div class="hits">{hits.get(s, 0)} fields filled</div></div>')
        if i < len(SOURCES) - 1:
            parts.append('<div class="arrow">→</div>')
    return f'<div class="pipe">{"".join(parts)}</div>'


STATUS_STYLE = {  # scan outcome -> (label, colour)
    "ok": ("resolved", "#86b6ef"), "partial": ("partial", WARN),
    "not_found": ("not found", CRIT), "not_stock": ("not a stock", MUTED),
}

SCAN_CSS = f"""
<style>
html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{ overflow: hidden !important; }}
[data-testid="stMainBlockContainer"], .block-container {{ padding: 2.2vh 3.5vw 0 !important; max-width: 100% !important; }}
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stHeader"],
[data-testid="stDecoration"], [data-testid="stStatusWidget"] {{ display: none !important; }}
.scanhead {{ display: flex; align-items: center; gap: 1.2rem; margin-bottom: 2.4vh; }}
.scanhead .logo {{ font-weight: 700; font-size: 1.05rem; color: {INK}; }}
.scanhead .logo i {{ font-style: normal; color: {BLUE}; margin-right: .35rem; }}
.scanhead .live {{ font: 600 10.5px 'JetBrains Mono', monospace; letter-spacing: .18em; color: #fff;
                  background: rgba(57,135,229,.18); border: 1px solid rgba(57,135,229,.55); border-radius: 999px;
                  padding: .3rem .75rem; }}
.scanhead .live b {{ color: {BLUE}; animation: blink 1s infinite; }}
.scanhead .file {{ font: 12px 'JetBrains Mono', monospace; color: {MUTED}; }}
.st-key-scanr .term-body {{ height: calc(100vh - 300px); }}

/* Loading circle: a progress ring wrapped by two counter-rotating glowing arcs. */
.loader {{ position: relative; width: min(40vh, 360px); aspect-ratio: 1; margin: 2vh auto 3.5vh; }}
.loader .halo {{ position: absolute; inset: 14%; border-radius: 50%; animation: breathe 3s ease-in-out infinite;
  background: radial-gradient(circle, rgba(57,135,229,.22), rgba(57,135,229,.04) 60%, transparent 72%); }}
@keyframes breathe {{ 50% {{ transform: scale(1.08); opacity: .65; }} }}
.loader .arc {{ position: absolute; border-radius: 50%; animation: spin 1.6s linear infinite;
  -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 3px), #000 calc(100% - 2px));
          mask: radial-gradient(farthest-side, transparent calc(100% - 3px), #000 calc(100% - 2px)); }}
.loader .arc.a {{ inset: 0; background: conic-gradient(from 0deg, transparent 0 55%, rgba(57,135,229,.15) 70%,
  {BLUE} 92%, #cde2fb 100%); filter: drop-shadow(0 0 8px rgba(57,135,229,.9)); }}
.loader .arc.b {{ inset: 7%; animation-duration: 2.6s; animation-direction: reverse; opacity: .7;
  background: conic-gradient(from 90deg, transparent 0 70%, rgba(134,182,239,.1) 80%, #86b6ef 100%); }}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
.loader svg {{ position: absolute; inset: 15%; width: 70%; height: 70%; transform: rotate(-90deg); }}
.loader svg .bg {{ fill: none; stroke: rgba(255,255,255,.06); stroke-width: 6; }}
.loader svg .fg {{ fill: none; stroke: url(#rg); stroke-width: 6; stroke-linecap: round;
                  filter: drop-shadow(0 0 6px rgba(57,135,229,.8)); }}
.loader .center {{ position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
                  justify-content: center; }}
.loader .pct {{ font-size: clamp(2.4rem, 6.5vh, 3.6rem); font-weight: 700; letter-spacing: -.04em; color: {INK};
               text-shadow: 0 0 30px rgba(57,135,229,.6); line-height: 1; }}
.loader .pct small {{ font-size: .45em; color: #86b6ef; margin-left: .1em; }}
.loader .cnt {{ font: 12px 'JetBrains Mono', monospace; color: {INK2}; margin-top: .5rem; }}
.scannow {{ text-align: center; font: 12px 'JetBrains Mono', monospace; color: {MUTED}; margin-bottom: 1.6vh; }}
.scannow b {{ color: {INK}; font-weight: 500; letter-spacing: .04em; }}
.lastcard {{ border: 1px solid rgba(57,135,229,.35); border-radius: 14px; padding: .9rem 1.1rem;
            background: linear-gradient(135deg, rgba(57,135,229,.12), rgba(57,135,229,.02));
            box-shadow: 0 0 30px rgba(57,135,229,.12); }}
.lastcard .k {{ font: 500 10px 'JetBrains Mono', monospace; letter-spacing: .2em; color: {BLUE}; text-transform: uppercase; }}
.lastcard .n {{ font-size: 1.05rem; font-weight: 600; color: {INK}; margin: .25rem 0 .1rem;
               white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.lastcard .d {{ font-size: .82rem; color: {INK2}; }}
.lastcard .srcs {{ margin-top: .5rem; display: flex; gap: .35rem; flex-wrap: wrap; }}
.lastcard .srcs span {{ font: 600 10px 'JetBrains Mono', monospace; letter-spacing: .06em; color: #86b6ef;
                       border: 1px solid rgba(57,135,229,.45); border-radius: 4px; padding: .05rem .4rem; }}
.scount {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: .5rem; margin-top: 1.4vh; }}
.scount div {{ border: 1px solid rgba(255,255,255,.07); border-radius: 10px; padding: .55rem .6rem;
              background: rgba(255,255,255,.02); }}
.scount b {{ display: block; font-size: 1.25rem; font-weight: 600; }}
.scount span {{ font: 9.5px 'JetBrains Mono', monospace; letter-spacing: .08em; text-transform: uppercase; color: {MUTED}; }}
.stime {{ display: flex; justify-content: space-between; font: 11px 'JetBrains Mono', monospace; color: {MUTED};
         margin-top: 1.2vh; }}
.stime b {{ color: {INK2}; font-weight: 500; }}
</style>
"""


def _clock(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def render_loader(isins: list[str], done: int, current: str, started: float) -> str:
    """Loading circle with the progress %, plus live counters, the last find and timing."""
    res = st.session_state.results
    total = len(isins)
    pct = done / total if total else 1.0
    elapsed = time.time() - started

    def phase(period):  # redraws replace the element; a negative delay keeps each animation's phase continuous
        return f"animation-delay:-{elapsed % period:.2f}s"

    circ = 2 * np.pi * 130
    counts = {k: 0 for k in STATUS_STYLE}
    for isin in isins[:done]:
        status = res.get(isin, {}).get("status", "not_found")
        counts[status] = counts.get(status, 0) + 1

    last = next((res[i] for i in reversed(isins[:done]) if res.get(i, {}).get("name")), None)
    if last:
        tags = sorted({"ISIN" if v == "ISIN prefix" else SRC_TAG.get(v.split(" ")[0], v) for v in last["src"].values()})
        geo = " · ".join(x for x in (last.get("country"), last.get("sector")) if x)
        last_html = (f'<div class="lastcard"><div class="k">Last resolved</div>'
                     f'<div class="n">{html.escape(last["name"])}</div><div class="d">{html.escape(geo or "—")}</div>'
                     f'<div class="srcs">{"".join(f"<span>{t}</span>" for t in tags)}</div></div>')
    else:
        last_html = ('<div class="lastcard"><div class="k">Last resolved</div><div class="n">Warming up…</div>'
                     '<div class="d">Loading the equity universe and data sources</div></div>')
    eta = elapsed / done * (total - done) if done else None
    cells = "".join(f'<div><b style="color:{c}">{counts.get(k, 0)}</b><span>{lbl}</span></div>'
                    for k, (lbl, c) in STATUS_STYLE.items())
    return f"""
<div class="loader">
  <div class="halo" style="{phase(3)}"></div>
  <div class="arc a" style="{phase(1.6)}"></div><div class="arc b" style="{phase(2.6)}"></div>
  <svg viewBox="0 0 300 300"><defs><linearGradient id="rg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#1c5cab"/><stop offset="1" stop-color="#86b6ef"/></linearGradient></defs>
    <circle class="bg" cx="150" cy="150" r="130"/>
    <circle class="fg" cx="150" cy="150" r="130" style="stroke-dasharray:{circ:.1f};stroke-dashoffset:{circ * (1 - pct):.1f}"/>
  </svg>
  <div class="center"><div class="pct">{pct * 100:.0f}<small>%</small></div><div class="cnt">{done} / {total} assets</div></div>
</div>
<div class="scannow">{"SCANNING" if done < total else "COMPLETE"} · <b>{html.escape(current)}</b></div>
{last_html}
<div class="scount">{cells}</div>
<div class="stime"><span>elapsed <b>{_clock(elapsed)}</b></span>
  <span>remaining <b>{_clock(eta) if eta is not None else "--:--"}</b></span>
  <span>speed <b>{done / elapsed if elapsed > 0 else 0:.1f}</b>/s</span></div>"""


def run_scan(isins: list[str], figi_key: str | None, file_name: str = ""):
    """Full-screen scan view: loading circle + counters on the left, source pipeline + live log on the right."""
    ss = st.session_state
    st.markdown(SCAN_CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="scanhead"><span class="logo"><i>◆</i>PortfolioScan</span>'
                f'<span class="live"><b>●</b> LIVE SCAN</span><span class="file">{html.escape(file_name)} · '
                f'{len(isins)} asset(s) · FinanceDatabase → yfinance → OpenFIGI</span></div>',
                unsafe_allow_html=True)
    left, right = st.columns([5, 7], gap="large")
    with left:
        loader = st.empty()
    with right, st.container(key="scanr"):
        pipe = st.empty()
        st.write("")
        term = st.empty()

    started = time.time()
    hits = {s: 0 for s in SOURCES}
    state = {"active": None}

    def log(src, msg, level="info"):
        ss.log.append((datetime.now().strftime("%H:%M:%S.%f")[:-3], src, msg, level))
        if src in SOURCES and state["active"] != src:
            state["active"] = src
            pipe.markdown(render_pipeline(src, hits), unsafe_allow_html=True)
        term.markdown(render_terminal(ss.log), unsafe_allow_html=True)

    loader.markdown(render_loader(isins, 0, "initialising sources…", started), unsafe_allow_html=True)
    pipe.markdown(render_pipeline(None, hits), unsafe_allow_html=True)
    log("SYS", f"scan started · {len(isins)} asset(s) · cascade FD → YF → FIGI", "head")

    fd_maps = None
    log("SYS", "loading FinanceDatabase equity universe (embedded snapshot)…", "info")
    try:
        t0 = time.time()
        fd_maps = load_financedatabase()
        log("SYS", f"FinanceDatabase ready · {len(fd_maps[0]):,} ISINs indexed · {time.time() - t0:.1f}s", "ok")
    except Exception as e:  # noqa: BLE001
        log("SYS", f"FinanceDatabase failed to load ({type(e).__name__}) — continuing without it", "err")

    for n, isin in enumerate(isins):
        loader.markdown(render_loader(isins, n, isin, started), unsafe_allow_html=True)
        log("SYS", f"[{n + 1}/{len(isins)}] {isin}", "head")
        if not isin_checksum_ok(isin):
            log("SYS", "ISIN checksum invalid — attempting lookup anyway", "warn")
        rec = resolve(isin, log, fd_maps, figi_key)
        for src in rec["src"].values():
            key = "FinanceDatabase" if src.startswith("FinanceDatabase") else src
            if key in hits:
                hits[key] += 1
        ss.results[isin] = rec
        pipe.markdown(render_pipeline(state["active"], hits), unsafe_allow_html=True)

    state["active"] = None
    loader.markdown(render_loader(isins, len(isins), "building your globe…", started), unsafe_allow_html=True)
    pipe.markdown(render_pipeline(None, hits), unsafe_allow_html=True)
    log("SYS", "scan complete", "head")


# ----------------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------------
SPECIAL_ROWS = {  # kind -> (display name, asset class, country, sector)
    "scpi": ("SCPI", "Real estate (SCPI)", "France", "Real Estate"),  # a SCPI is a French vehicle: its issuer's country
    "euro_fund": ("Euro fund", CASH_LABEL, CASH_LABEL, CASH_LABEL),
    "cash": ("Cash", CASH_LABEL, CASH_LABEL, CASH_LABEL),
}


def spread(countries: dict, sectors: dict, value: float) -> list[tuple[str, str, float]]:
    """Spread a value over countries x sectors. Both lists are rescaled to 100%, so the totals per country and
    per sector are exact; how they pair up inside a fund is approximated as independent."""
    cw, sw = countries or {"Unknown": 1.0}, sectors or {"Unknown": 1.0}
    ct, stot = sum(cw.values()), sum(sw.values())
    return [(c, s, value * wc / ct * ws / stot) for c, wc in cw.items() for s, ws in sw.items()]


def position_spec(isin: str, given: str, res: dict, etfs: dict) -> dict:
    """What we know about one position: name, ticker, asset class, country and sector weights, status."""
    kind = special_kind(isin)
    etf = etfs.get(isin) or res.get(isin, {}).get("etf")
    if kind:
        label, asset, country, sector = SPECIAL_ROWS[kind]
        return {"name": given or f"{label} · {isin}", "ticker": "", "asset": asset, "country": {country: 1.0},
                "sector": {sector: 1.0}, "status": "ok", "sources": "classified from its code"}
    if etf:
        return {"name": etf["name"], "ticker": etf["ticker"], "asset": etf["asset_class"], "country": etf["country"],
                "sector": {BOND_SECTOR: 1.0} if etf["asset_class"] == "Bonds" else etf["sector"], "status": "ok",
                "sources": f"ETF data: {etf['issuer']} · {etf['basis'] or 'holdings'} · {etf['as_of']}"}
    r = res.get(isin, {})
    return {"name": r.get("name") or given or isin, "ticker": r.get("ticker") or "", "asset": "Equities",
            "country": {r.get("country") or "Unknown": 1.0}, "sector": {r.get("sector") or "Unknown": 1.0},
            "status": r.get("status", "not_found"),
            "sources": " · ".join(f"{k}: {v}" for k, v in r.get("src", {}).items())}


def build_frame(positions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Join positions with resolved data. A stock is one row; an ETF from the snapshot is one row per
    country x sector; SCPI, euro funds and cash get fixed rows. Returns (frame, excluded)."""
    res, overrides = st.session_state.results, st.session_state.overrides
    etfs = load_etf_snapshot()
    rows = []
    for p in positions.itertuples(index=False):
        acct = getattr(p, "account", None)
        given = getattr(p, "name", "")  # name column of the imported file, when there was one
        given = given.strip() if isinstance(given, str) else ""
        spec = position_spec(p.isin, given, res, etfs)
        manual = overrides.get(p.isin, {})
        for dim in ("country", "sector"):  # a correction made in the Issues window replaces what was found
            if manual.get(dim):
                spec[dim] = manual[dim]
        if manual and spec["status"] != "ok":  # a corrected position is no longer missing or excluded
            spec["status"] = "ok"
        base = {"isin": p.isin, "origin": p.origin, "status": spec["status"], "name": spec["name"],
                "ticker": spec["ticker"], "asset": spec["asset"], "sources": spec["sources"],
                "manual": ",".join(d for d in ("country", "sector") if manual.get(d)),
                "account": acct if isinstance(acct, str) else "Portfolio"}
        rows += [{**base, "value": v, "country": c, "sector": sct} for c, sct, v in
                 spread(spec["country"], spec["sector"], p.value)]
    df = pd.DataFrame(rows)
    excluded = df[df["status"] == "not_stock"]
    df = df[df["status"] != "not_stock"].copy()
    total = df["value"].sum()
    df["weight"] = df["value"] / total if total else 0
    df["iso3"] = df["country"].map(lambda c: None if c in NEUTRAL else iso3_of(c))
    df["label"] = df["name"] + np.where(df["origin"] == "Simulated", " ◇", "")
    return df, excluded


# Landing-page demo: 50 well-known stocks, already "resolved", so the page needs no scan to show the globe.
DEMO = [
    ("Apple", "AAPL", "United States", "Information Technology"),
    ("Microsoft", "MSFT", "United States", "Information Technology"),
    ("NVIDIA", "NVDA", "United States", "Information Technology"),
    ("Amazon", "AMZN", "United States", "Consumer Discretionary"),
    ("Alphabet", "GOOGL", "United States", "Communication Services"),
    ("Meta Platforms", "META", "United States", "Communication Services"),
    ("Broadcom", "AVGO", "United States", "Information Technology"),
    ("JPMorgan Chase", "JPM", "United States", "Financials"),
    ("Visa", "V", "United States", "Financials"),
    ("Exxon Mobil", "XOM", "United States", "Energy"),
    ("UnitedHealth", "UNH", "United States", "Health Care"),
    ("Eli Lilly", "LLY", "United States", "Health Care"),
    ("Johnson & Johnson", "JNJ", "United States", "Health Care"),
    ("Procter & Gamble", "PG", "United States", "Consumer Staples"),
    ("Coca-Cola", "KO", "United States", "Consumer Staples"),
    ("Home Depot", "HD", "United States", "Consumer Discretionary"),
    ("Caterpillar", "CAT", "United States", "Industrials"),
    ("Boeing", "BA", "United States", "Industrials"),
    ("Salesforce", "CRM", "United States", "Information Technology"),
    ("Netflix", "NFLX", "United States", "Communication Services"),
    ("Mastercard", "MA", "United States", "Financials"),
    ("Chevron", "CVX", "United States", "Energy"),
    ("Pfizer", "PFE", "United States", "Health Care"),
    ("Walmart", "WMT", "United States", "Consumer Staples"),
    ("Tesla", "TSLA", "United States", "Consumer Discretionary"),
    ("Toyota Motor", "7203", "Japan", "Consumer Discretionary"),
    ("Sony Group", "6758", "Japan", "Consumer Discretionary"),
    ("Mitsubishi UFJ", "8306", "Japan", "Financials"),
    ("Keyence", "6861", "Japan", "Information Technology"),
    ("SAP", "SAP", "Germany", "Information Technology"),
    ("Siemens", "SIE", "Germany", "Industrials"),
    ("Allianz", "ALV", "Germany", "Financials"),
    ("LVMH", "MC", "France", "Consumer Discretionary"),
    ("TotalEnergies", "TTE", "France", "Energy"),
    ("Schneider Electric", "SU", "France", "Industrials"),
    ("AstraZeneca", "AZN", "United Kingdom", "Health Care"),
    ("Shell", "SHEL", "United Kingdom", "Energy"),
    ("HSBC", "HSBA", "United Kingdom", "Financials"),
    ("Nestlé", "NESN", "Switzerland", "Consumer Staples"),
    ("Novartis", "NOVN", "Switzerland", "Health Care"),
    ("ASML", "ASML", "Netherlands", "Information Technology"),
    ("ING Groep", "INGA", "Netherlands", "Financials"),
    ("Royal Bank of Canada", "RY", "Canada", "Financials"),
    ("Shopify", "SHOP", "Canada", "Information Technology"),
    ("BHP Group", "BHP", "Australia", "Materials"),
    ("CSL", "CSL", "Australia", "Health Care"),
    ("TSMC", "2330", "Taiwan", "Information Technology"),
    ("Samsung Electronics", "005930", "South Korea", "Information Technology"),
    ("Novo Nordisk", "NOVO-B", "Denmark", "Health Care"),
    ("Petrobras", "PETR4", "Brazil", "Energy"),
]


def demo_frame() -> pd.DataFrame:
    """The DEMO stocks shaped like build_frame() output, with gently decreasing position sizes."""
    df = pd.DataFrame(DEMO, columns=["name", "ticker", "country", "sector"])
    df["value"] = [round(3200 * 0.955 ** i + 350) for i in range(len(df))]
    df["isin"] = [f"DEMO{i:02d}" for i in range(len(df))]
    df["weight"] = df["value"] / df["value"].sum()
    df["iso3"] = df["country"].map(iso3_of)
    return df.assign(origin="Portfolio", status="ok", sources="", asset="Equities", account="Demo", manual="",
                     label=df["name"])


STOCK_SOURCES = "FinanceDatabase → yfinance → OpenFIGI"
ETF_SOURCES = "ETF data (iShares, SPDR, Amundi, Xtrackers)"


def data_issues(df: pd.DataFrame, excluded: pd.DataFrame, off_map: list[str]) -> list[dict]:
    """Every position whose data is missing or guessed: what is wrong, its weight in the whole portfolio
    (excluded positions included) and where the data was looked for."""
    res = st.session_state.results
    total = df["value"].sum() + (excluded["value"].sum() if len(excluded) else 0)
    out = []

    def add(name, isin, issue, value, looked):
        out.append({"n": name, "i": isin, "issue": issue, "p": 100 * value / total if total else 0.0,
                    "looked": looked})

    corrected = set(df.loc[df["manual"] != "", "isin"])
    for x in excluded.itertuples():
        note = res.get(x.isin, {}).get("note") or "classed as an ETF or fund"
        add(x.name, x.isin, "ETF / fund without data: excluded", x.value, f"{ETF_SOURCES}: not found · {note}")
    for isin, rows in df.groupby("isin", sort=False):
        r0, value = rows.iloc[0], rows["value"].sum()
        if isin in corrected:
            continue
        if r0["sources"].startswith("ETF data"):  # holdings the issuer's file and FinanceDatabase could not place
            issuer = r0["sources"].split("·")[0].replace("ETF data:", "").strip()
            if issuer == "Yahoo Finance":
                # Yahoo has every fund's sectors but no country breakdown. A fund named after one country is
                # complete anyway; otherwise only its top 10 holdings have a country.
                unknown = rows.loc[rows["country"] == "Unknown", "value"].sum()
                if unknown >= 0.05 * value:
                    add(r0["name"], isin, f"{unknown / value:.0%} of the fund has no country: Yahoo Finance gives "
                        f"its sectors but only its top 10 holdings, not its countries", unknown,
                        f"{ETF_SOURCES}: not found · Yahoo Finance fund data ({r0['ticker']})")
                continue
            looked = (f"{issuer} country / sector breakdown" if issuer == "Amundi" else
                      f"{issuer} holdings file, then FinanceDatabase by each holding's ISIN")
            for col in ("country", "sector"):
                v = rows.loc[rows[col] == "Unknown", "value"].sum()
                if v >= 0.05 * value:  # a few % unclassified (fund cash, derivatives, odd lines) is normal
                    add(r0["name"], isin, f"{v / value:.1%} of the fund has no {col}", v, looked)
            continue
        if r0["status"] == "not_found":
            add(r0["name"], isin, "not identified: no country, no sector", value, f"{STOCK_SOURCES}: no match")
            continue
        missing = [f for f in ("country", "sector") if r0[f] == "Unknown"]
        if missing:
            add(r0["name"], isin, f"no {' or '.join(missing)}", value, f"{STOCK_SOURCES}: not found")
        elif res.get(isin, {}).get("src", {}).get("country") == "ISIN prefix":
            add(r0["name"], isin, f"country guessed from the ISIN prefix: {r0['country']}", value,
                f"{STOCK_SOURCES}: no country")
        if r0["country"] in off_map:
            add(r0["name"], isin, f"country not on the map: {r0['country']}", value, "world map shapes")
    return sorted(out, key=lambda x: -x["p"])


SRC_LABEL = {  # resolve() source -> (tag, explanation)
    "FinanceDatabase": ("database", "FinanceDatabase (embedded snapshot), matched by ISIN"),
    "FinanceDatabase (name match)": ("database", "FinanceDatabase, matched by company name"),
    "yfinance": ("yahoo", "Yahoo Finance, looked up by ISIN"),
    "OpenFIGI": ("database", "OpenFIGI, matched by ISIN"),
    "ISIN prefix": ("estimate", "estimated from the ISIN's country prefix"),
}


def data_lineage(df: pd.DataFrame, excluded: pd.DataFrame) -> list[dict]:
    """Per position and per field (name, country, sector, asset class): a tag (database, issuer, yahoo, estimate,
    manual, file, none) and a sentence saying exactly where the value came from."""
    res, etfs, overrides = st.session_state.results, load_etf_snapshot(), st.session_state.overrides
    out = []
    frames = [(isin, rows.iloc[0], rows) for isin, rows in df.groupby("isin", sort=False)]
    frames += [(x.isin, pd.Series(x._asdict()), None) for x in excluded.itertuples()]
    for isin, r0, rows in frames:
        rec, kind = res.get(isin, {}), special_kind(isin)
        etf = etfs.get(isin) or rec.get("etf")
        manual = overrides.get(isin, {})
        if rows is None:  # excluded
            line = {"name": ("none", "only the name in your file"),
                    "country": ("none", f"not found: {ETF_SOURCES}, then Yahoo Finance fund data"),
                    "sector": ("none", "not found: no fund data on any Yahoo listing"),
                    "asset": ("estimate", rec.get("note") or "classed as an ETF or fund")}
        elif kind:
            line = {"name": ("file", "the name in your file, or its code"),
                    "country": ("estimate", "France: a SCPI is a French vehicle, its issuer's country") if kind == "scpi"
                    else ("file", "none by nature: euro fund / cash"),
                    "sector": ("estimate", "Real Estate: a SCPI holds property") if kind == "scpi"
                    else ("file", "none by nature: euro fund / cash"),
                    "asset": ("file", f"recognised from its code or name ({isin})")}
        elif etf and etf["issuer"] == "Yahoo Finance":
            how = etf["basis"].replace("estimate, ", "")
            named = "from its name" in how
            line = {"name": ("yahoo", f"Yahoo Finance ({etf['ticker']})"),
                    "country": ("estimate", f"estimated: the fund is named after {next(iter(etf['country']))} "
                                "(Yahoo has no country data)") if named else
                    ("estimate", "estimated from its top 10 holdings (country of each from FinanceDatabase or its "
                     "stock exchange); the rest of the fund is Unknown"),
                    "sector": ("yahoo", f"Yahoo Finance sector weights of the fund ({etf['ticker']}), complete"),
                    "asset": ("yahoo", "Yahoo Finance asset classes (bonds if over 50%)")}
        elif etf:
            issuer, basis, asof = etf["issuer"], etf["basis"] or "holdings", etf["as_of"]
            if issuer == "Amundi":
                what = ("Amundi's breakdown of the index it tracks (swap ETF: its own basket is only collateral)"
                        if basis == "index" else "Amundi's breakdown of the fund's portfolio")
            else:
                what = (f"{issuer} holdings file: every holding summed by {{}}; holdings without one looked up "
                        "in FinanceDatabase by ISIN")
            line = {"name": ("issuer", f"{issuer} fund list"),
                    "country": ("issuer", what.format("country") + f" · as of {asof} · top 10 kept"),
                    "sector": ("issuer", what.format("sector") + f" · as of {asof} · top 10 kept"),
                    "asset": ("estimate", "from the fund's name, then FinanceDatabase's category")}
        else:
            src = rec.get("src", {})

            def from_src(field):
                if field in src:
                    return SRC_LABEL.get(src[field], ("database", src[field]))
                return ("none", f"not found ({STOCK_SOURCES})")
            line = {"name": from_src("name"), "country": from_src("country"), "sector": from_src("sector"),
                    "asset": (from_src("name")[0], "a stock: found as an equity by the source of its name")}
        for dim in ("country", "sector"):
            if manual.get(dim):
                line[dim] = ("manual", "set by hand in the Issues window: "
                             + ", ".join(f"{k} {v:.0f}%" for k, v in manual[dim].items()))
        out.append({"n": str(r0["name"]), "i": isin, **line})
    return out


def one_or_mixed(s: pd.Series) -> str:
    """Aggregator: the single value of a position's rows, or "Diversified" when an ETF spans several."""
    u = s.unique()
    return u[0] if len(u) == 1 else "Diversified"


def weights_by(df: pd.DataFrame, col: str) -> pd.Series:
    w = df.groupby(col)["value"].sum().div(df["value"].sum()).sort_values(ascending=False)
    return pd.concat([w[~w.index.isin(NEUTRAL)], w[w.index.isin(NEUTRAL)]])


# ----------------------------------------------------------------------------
# Charts
# ----------------------------------------------------------------------------
ECHARTS_JS = "https://cdnjs.cloudflare.com/ajax/libs/echarts/5.6.0/echarts.min.js"
GEO_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
           "geojson/ne_50m_admin_0_countries.geojson")
GEO_PATH = DATA_DIR / "countries_50m.geojson"
GLOBE_JS = "https://cdn.jsdelivr.net/npm/globe.gl@2.46.2/dist/globe.gl.min.js"
GLOBE_IMG = "https://cdn.jsdelivr.net/npm/three-globe@2.45.0/example/img/earth-dark.jpg"
GLOBE_BUMP = "https://cdn.jsdelivr.net/npm/three-globe@2.45.0/example/img/earth-topology.png"
ADM0_ALIASES = {"SSD": "SDS", "PSE": "PSX"}  # pycountry alpha-3 -> Natural Earth ADM0_A3
GITHUB_URL = "https://github.com/gabinle/PortfolioScan2/"
Y_LABEL_W = 150
MONO = "JetBrains Mono, monospace"
SANS = "Inter, system-ui, sans-serif"
TOOLTIP = {
    "backgroundColor": SURFACE, "borderColor": BLUE, "borderWidth": 1, "padding": [8, 12],
    "textStyle": {"color": INK, "fontFamily": MONO, "fontSize": 12},
    "extraCssText": "border-radius:8px;box-shadow:0 0 24px rgba(57,135,229,.25);",
}


def hex_rgb(h: str) -> list[int]:
    return [int(h[i:i + 2], 16) for i in (1, 3, 5)]


def rgb_hex(rgb) -> str:
    return "#" + "".join(f"{c:02x}" for c in rgb)


def ramp(t: float, stops=SEQ[2:]) -> list[int]:
    """Interpolate the sequential blue ramp; t in [0, 1]."""
    t = min(max(t, 0.0), 1.0) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    a, b = hex_rgb(stops[i]), hex_rgb(stops[i + 1])
    return [round(a[k] + (b[k] - a[k]) * (t - i)) for k in range(3)]


# ── Globe cockpit (globe.gl / three.js) ──────────────────────────────────────
# Weight scale on the globe: light = small, dark blue = large.
GLOBE_STOPS = ["#eaf3ff", "#b7d3f6", "#6da7ec", "#2a78d6", "#1c5cab"]
HELD_ALT = 0.028  # every held country gets the same gentle relief


def _ring(coords) -> list:
    return [[round(x, 3), round(y, 3)] for x, y in coords]


def _globe_geometry(geom):
    """Simplify for the globe, keeping only well-formed clockwise parts.

    Over-simplified or rounded slivers can flip orientation, which the globe then reads as
    "the whole sphere minus this island" — a planet-sized polygon that flickers everywhere.
    """
    parts = []
    for p in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom]):
        q = p.simplify(min(0.08, p.area ** 0.5 * 0.08), preserve_topology=True)
        if q.is_empty or q.geom_type != "Polygon":
            continue
        q = Polygon(_ring(q.exterior.coords), [_ring(i.coords) for i in q.interiors])
        if not q.is_valid or q.area < 1e-4:
            continue
        q = orient(q, sign=-1.0)  # Natural Earth / d3 convention: clockwise exterior
        parts.append([[list(c) for c in q.exterior.coords]] + [[list(c) for c in i.coords] for i in q.interiors])
    return {"type": "MultiPolygon", "coordinates": parts} if parts else None


@st.cache_resource(show_spinner=False)
def load_country_shapes() -> dict:
    """ADM0 code -> globe polygon, label point, name and UN subregion, from Natural Earth 1:50m."""
    if not GEO_PATH.exists():
        GEO_PATH.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(GEO_URL, timeout=60)
        r.raise_for_status()
        GEO_PATH.write_bytes(r.content)
    gj = json.loads(GEO_PATH.read_text(encoding="utf-8"))
    shapes = {}
    for f in gj["features"]:
        props = f["properties"]
        geom = shape(f["geometry"])
        if geom.geom_type == "MultiPolygon":
            # Drop tiny outlying islands (e.g. the Aleutians) relative to the main landmass.
            biggest = max(p.area for p in geom.geoms)
            parts = [p for p in geom.geoms if p.area >= 0.02 * biggest]
            geom = MultiPolygon(parts) if len(parts) > 1 else parts[0]
        main = max(geom.geoms, key=lambda g: g.area) if geom.geom_type == "MultiPolygon" else geom
        pt = main.representative_point()
        entry = {
            "geometry": _globe_geometry(geom), "area": geom.area,
            "lon": pt.x, "lat": pt.y, "name": props.get("NAME") or props.get("ADMIN"),
            "region": props.get("SUBREGION") or "Other",
        }
        for key in ("ADM0_A3", "ISO_A3_EH"):
            code = props.get(key)
            if code and code != "-99":
                shapes.setdefault(code, entry)
    return shapes


def shape_for(iso3: str | None):
    return load_country_shapes().get(ADM0_ALIASES.get(iso3, iso3)) if iso3 else None


def _rows(items, kind=None, *, fly=False, grey=NEUTRAL) -> str:
    """Ranked list rows: name · % · bar. items = [(name, pct, sub, lat, lng)].

    `kind` ('s' sector, 'c' country, 'r' region) makes hovering the row open the positions inspector.
    """
    if not items:
        return '<div class="empty">—</div>'
    top = max(p for _, p, *_ in items) or 1
    out = []
    for name, pct, sub, lat, lng in items:
        attrs = f' data-k="{kind}" data-v="{html.escape(name, quote=True)}"' if kind else ""
        flies = fly and lat is not None
        if flies:
            attrs += f' data-lat="{lat:.3f}" data-lng="{lng:.3f}"'
        cls = "row" + (" hov" if kind else "") + (" fly" if flies else "") + (" unk" if name in grey else "")
        out.append(
            f'<div class="{cls}"{attrs}><span class="nm">{html.escape(NEUTRAL_LABEL.get(name, name))}</span>'
            f'<span class="pc">{pct:.1f}%</span>'
            + (f'<span class="sub">{html.escape(sub)}</span>' if sub else "")
            + f'<div class="trk"><div style="width:{pct / top * 100:.0f}%"></div></div></div>')
    return "".join(out)


def _delta_rows(d: pd.Series) -> str:
    if d.empty:
        return '<div class="empty">no change</div>'
    out = []
    for k, v in d.items():
        up = v >= 0
        out.append(f'<div class="drow"><span class="nm">{html.escape(k)}</span>'
                   f'<span class="{"up" if up else "dn"}">{"▲" if up else "▼"} {v:+.2f} pp</span></div>')
    return "".join(out)


def top_deltas(before: pd.Series, after: pd.Series, n=3) -> pd.Series:
    idx = after.index.union(before.index)
    d = (after.reindex(idx, fill_value=0) - before.reindex(idx, fill_value=0)) * 100
    d = d[d.abs() > 0.005]
    return d.reindex(d.abs().sort_values(ascending=False).index).head(n)


def _js_json(obj) -> str:
    """JSON safe to inline inside a <script> block."""
    return json.dumps(obj).replace("</", "<\\/")


def region_of(iso3) -> str:
    return (shape_for(iso3) or {}).get("region", "Unknown")


def cockpit(df, sim: dict | None = None, notes=(), demo=False, excluded: pd.DataFrame | None = None):
    """The whole app view: a spinning globe with glass cards around it, hover inspectors and detail windows.

    `demo` renders the landing-page showcase instead: just the globe and a column of sector/country cards.
    """
    shapes = load_country_shapes()
    df = df.assign(region=np.where(df["country"].isin(NEUTRAL), df["country"], df["iso3"].map(region_of)))
    total = df["value"].sum()
    g = (df[df["iso3"].notna()].groupby(["iso3", "country"], as_index=False)
         .agg(value=("value", "sum"), n=("isin", "nunique")))
    g["pct"] = 100 * g["value"] / total
    g = g.sort_values("pct", ascending=False)
    wmax = float(g["pct"].max()) if len(g) else 1.0
    wmin = float(g["pct"].min()) if len(g) else 0.0

    held, missing = {}, []
    for r in g.itertuples():
        s = shape_for(r.iso3)
        if s is None or s["geometry"] is None:
            missing.append(r.country)
        else:
            held[id(s)] = r

    features, rings, seen = [], [], set()
    for s in shapes.values():
        if id(s) in seen or s["geometry"] is None:
            continue
        seen.add(id(s))
        r = held.get(id(s))
        if r is None:
            if s["area"] < 0.3:  # microstates add noise and nothing else
                continue
            props = {"name": s["name"], "held": False}
        else:
            t = (r.pct - wmin) / (wmax - wmin) if wmax > wmin else 1.0
            rgb = ramp(t, GLOBE_STOPS)
            props = {"name": r.country, "held": True, "cap": f"rgba({rgb[0]},{rgb[1]},{rgb[2]},0.96)",
                     "lat": s["lat"], "lng": s["lon"]}
            rings.append({"lat": s["lat"], "lng": s["lon"]})
        features.append({"type": "Feature", "geometry": s["geometry"], "properties": props})

    # Euro funds & cash have no country by nature, and "Other" is an ETF's many small countries: neither is "unmapped".
    unmapped = df.loc[(df["iso3"].isna() | df["country"].isin(missing)) & ~df["country"].isin([CASH_LABEL, "Other"]),
                      "weight"].sum()
    notes = list(notes) + ([f"{unmapped:.1%} of value has no mappable country"] if unmapped > 0 else [])
    excluded = excluded if excluded is not None else df.iloc[:0]
    issues = [] if demo else data_issues(df, excluded, missing)
    # Fix editor: the current breakdown of every position that has an issue or a correction.
    corrected = list(dict.fromkeys(df.loc[df["manual"] != "", "isin"]))
    fix = {}
    for isin in dict.fromkeys([x["i"] for x in issues] + corrected):
        rows = df[df["isin"] == isin]
        if len(rows):
            tot = rows["value"].sum()
            fix[isin] = {"n": str(rows["name"].iloc[0]),
                         "c": [[k, round(100 * v / tot, 1)] for k, v in rows.groupby("country")["value"].sum()
                               .sort_values(ascending=False).items()],
                         "s": [[k, round(100 * v / tot, 1)] for k, v in rows.groupby("sector")["value"].sum()
                               .sort_values(ascending=False).items()]}
        else:  # excluded: nothing known yet
            fix[isin] = {"n": str(excluded.loc[excluded["isin"] == isin, "name"].iloc[0]),
                         "c": [["", 100]], "s": [["", 100]]}
    country_names = sorted(set(df["country"]) | {getattr(c, "common_name", None) or c.name
                                                  for c in pycountry.countries} - set(NEUTRAL))
    sector_names = sorted(set(YF_TO_GICS.values())) + [BOND_SECTOR, CASH_LABEL, "Unknown"]
    fix_tpl = (
        '<div id="fixbox" hidden><div class="fxh"><b id="fx-name"></b><span id="fx-isin"></span></div>'
        '<div class="mg half">'
        '<div><div class="mh">Countries</div><div id="fx-c"></div>'
        '<button class="fx-add" data-d="c">＋ country</button><div class="fx-tot" id="fx-c-tot"></div></div>'
        '<div><div class="mh">Sectors</div><div id="fx-s"></div>'
        '<button class="fx-add" data-d="s">＋ sector</button><div class="fx-tot" id="fx-s-tot"></div></div></div>'
        '<div class="fx-btns"><button class="cta" id="fx-save">Save correction</button>'
        '<button class="fx-cancel" id="fx-cancel">Cancel</button></div>'
        '<div class="hint ihint">Each list is rescaled to 100% when saved. The correction replaces what was found, '
        'for this session.</div></div>'
        '<datalist id="dl-c">' + "".join(f'<option value="{html.escape(c)}">' for c in country_names) + '</datalist>'
        '<datalist id="dl-s">' + "".join(f'<option value="{html.escape(c)}">' for c in sector_names) + '</datalist>')
    lineage = [] if demo else data_lineage(df, excluded)
    TAG = {"database": "Database", "issuer": "Issuer data", "yahoo": "Yahoo Finance", "estimate": "Estimate",
           "manual": "Manual", "file": "Your file", "none": "Not found"}

    def cell(v):
        tag, text = v
        return f'<td class="lin"><span class="tg tg-{tag}">{TAG[tag]}</span><div>{html.escape(text)}</div></td>'
    lineage_tpl = (
        '<div class="hint ihint">Where each piece of data comes from, position by position.</div>'
        '<table><thead><tr><th>Position</th><th>Name</th><th>Country</th><th>Sector</th><th>Asset class</th>'
        '</tr></thead><tbody>'
        + "".join(f'<tr><td class="wr">{html.escape(x["n"])}<div class="m isn">{html.escape(x["i"])}</div></td>'
                  + cell(x["name"]) + cell(x["country"]) + cell(x["sector"]) + cell(x["asset"]) + "</tr>"
                  for x in lineage)
        + "</tbody></table>")
    if issues:
        hit = {}  # a position with two issues (no country, no sector) counts once, at its larger weight
        for x in issues:
            hit[x["i"]] = max(hit.get(x["i"], 0.0), x["p"])
        issues_tpl = (
            f'<div class="isum"><b>{len(issues)} issue{"s" if len(issues) > 1 else ""}</b> · '
            f'{sum(hit.values()):.1f}% of the portfolio affected</div>'
            '<div class="hint ihint">Weights are shares of the whole portfolio, excluded positions included.</div>'
            + fix_tpl
            + '<table><thead><tr><th>Position</th><th>ISIN / code</th><th>Issue</th><th>Weight</th>'
            '<th>Where we looked</th><th></th></tr></thead><tbody>'
            + "".join(f'<tr><td class="wr">{html.escape(str(x["n"]))}</td><td class="m">{html.escape(x["i"])}</td>'
                      f'<td class="wr">{html.escape(x["issue"])}</td><td class="m">{x["p"]:.2f}%</td>'
                      f'<td class="m lk">{html.escape(x["looked"])}</td>'
                      f'<td><button class="fx-open" data-fix="{html.escape(x["i"])}">✎ Fix</button></td></tr>'
                      for x in issues)
            + "</tbody></table>")
    else:
        issues_tpl = '<div class="hint ihint">No data issues: every position was found and mapped.</div>' + fix_tpl
    if corrected:
        issues_tpl += (
            '<div class="mh fx-sec">Manual corrections</div><table><tbody>'
            + "".join(f'<tr><td class="wr">{html.escape(fix[i]["n"])}</td><td class="m">{html.escape(i)}</td>'
                      f'<td class="wr lk">' + html.escape(" · ".join(
                          f"{k} {v:.0f}%" for k, v in fix[i]["c"][:4]) + "  |  " + " · ".join(
                          f"{k} {v:.0f}%" for k, v in fix[i]["s"][:4])) + '</td>'
                      f'<td><button class="fx-open" data-fix="{html.escape(i)}">✎ Edit</button> '
                      f'<button class="fx-reset" data-reset="{html.escape(i)}">⟲ Reset</button></td></tr>'
                      for i in corrected)
            + "</tbody></table>")


    # ── cards ────────────────────────────────────────────────────────────────
    sec_w, country_w, region_w = (weights_by(df, c) for c in ("sector", "country", "region"))
    country_rows = []
    for r in g.head(7).itertuples():
        s = shape_for(r.iso3)
        country_rows.append((r.country, r.pct, "", s["lat"] if s else None, s["lon"] if s else None))
    # An ETF is many rows (one per country x sector): lists of positions regroup them per ISIN.
    holdings = (df.groupby("isin", as_index=False, sort=False)
                .agg(label=("label", "first"), ticker=("ticker", "first"), value=("value", "sum"),
                     weight=("weight", "sum"), country=("country", one_or_mixed), sector=("sector", one_or_mixed),
                     region=("region", one_or_mixed), asset=("asset", "first"), status=("status", "first"),
                     sources=("sources", "first"))
                .sort_values("value", ascending=False))
    resolved = int((holdings["status"] == "ok").sum())
    asset_w = df.groupby("asset")["value"].sum().div(total).sort_values(ascending=False)

    def shares(sub, col, n=10):
        return [[k, 100 * float(v)] for k, v in weights_by(sub, col).head(n).items()]

    accounts = []  # per account: its share of the portfolio, and its own breakdowns (shares of the account)
    for name, sub in df.groupby("account", sort=False):
        tot = sub["value"].sum()
        hold = (sub.groupby("isin", sort=False).agg(n=("label", "first"), a=("asset", "first"), v=("value", "sum"))
                .sort_values("v", ascending=False))
        accounts.append({
            "name": name, "v": float(tot), "p": 100 * float(tot / total), "n": len(hold),
            "assets": [[k, 100 * float(v / tot)] for k, v in
                       sub.groupby("asset")["value"].sum().sort_values(ascending=False).items()],
            "countries": shares(sub, "country"), "sectors": shares(sub, "sector"),
            "holdings": [{"n": r.n, "a": r.a, "v": float(r.v), "p": 100 * float(r.v / tot)} for r in hold.itertuples()],
        })
    accounts.sort(key=lambda a: -a["v"])
    top_acc = max((a["p"] for a in accounts), default=1) or 1
    acc_card = "".join(
        f'<div class="row hov accr" data-acc="{i}" title="Open this account">'
        f'<span class="nm">{html.escape(str(a["name"]))}</span><span class="pc">{a["p"]:.1f}%</span>'
        f'<span class="sub">{fmt_money(a["v"])} · {a["n"]} position{"s" if a["n"] > 1 else ""}</span>'
        f'<div class="trk"><div style="width:{a["p"] / top_acc * 100:.0f}%"></div></div></div>'
        for i, a in enumerate(accounts))
    note_html = "".join(f'<div class="warn">⚠ {html.escape(n)}</div>' for n in notes)

    kpis = f"""
      <div class="big">{fmt_money(total)}</div><div class="bigsub">total value · {len(holdings)} positions</div>
      <div class="stats">
        <div><b>{df.loc[~df['country'].isin(NEUTRAL), 'country'].nunique()}</b><span>countries</span></div>
        <div><b>{df.loc[~df['sector'].isin(NEUTRAL), 'sector'].nunique()}</b><span>sectors</span></div>
        <div><b>{resolved / len(holdings):.0%}</b><span>resolved</span></div>
      </div>{note_html}
      <button class="cta" data-open="test" data-title="Test a position" data-small="1">＋ Test a position</button>"""
    left = [
        ("Portfolio", kpis),
        ("Asset classes", _rows([(k, 100 * v, "", None, None) for k, v in asset_w.items()], grey=())),
        ("Accounts", acc_card),
        ("Top holdings", _rows([(h.label, 100 * h.weight,
                                 f"ETF · {h.asset.lower()}" if h.country == h.sector == "Diversified"
                                 else " · ".join(dict.fromkeys([h.sector, h.country])), None, None)
                                for h in holdings.head(6).itertuples()])),
    ]
    right = [("Top countries", _rows(country_rows, "c", fly=True)),
             ("Top sectors", _rows([(k, 100 * v, "", None, None) for k, v in sec_w.head(7).items()], "s"))]
    buttons = [("sectors", "▦ Sectors", "Sectors"), ("countries", "◉ Countries", "Countries & regions"),
               ("holdings", "≡ Holdings", "Holdings"), ("accounts", "▤ Accounts", "Accounts"),
               ("log", "› Log", "Scan log"),
               ("issues", f"⚠ Issues · {len(issues)}", "Data issues")]
    charts = {"tree": treemap_spec(df),
              "ctrybars": bars_spec(country_w, top=20), "regbars": bars_spec(region_w, top=20)}
    sim_tpl = ""
    if sim:
        pos = "".join(f'<div class="simpos">◇ <b>{html.escape(p["name"])}</b>'
                      f'<span>{p["value"]:,.0f} · {p["pct"]:.1f}%</span></div>' for p in sim["positions"])
        # Card version: each test position can be removed or have its value edited in place.
        editable = "".join(
            f'<div class="sp"><div class="sp-top"><span class="sp-nm">◇ <b>{html.escape(p["name"])}</b></span>'
            f'<button class="sp-x" data-i="{p["i"]}" title="Remove this test position">✕</button></div>'
            f'<div class="sp-ctl"><input class="sp-val" data-i="{p["i"]}" type="number" min="1" step="any" '
            f'value="{p["value"]:.0f}" title="Edit, then press Enter"><span>{p["pct"]:.1f}%</span></div></div>'
            for p in sim["positions"])
        right.append(("Simulation", f'{editable}<div class="dh">sector shift</div>'
                                    f'{_delta_rows(top_deltas(sim["cur_sector"], sim["new_sector"]))}'
                                    f'<div class="dh">country shift</div>'
                                    f'{_delta_rows(top_deltas(sim["cur_country"], sim["new_country"]))}'))
        buttons.insert(3, ("sim", "± Sim.", "Simulation impact"))
        charts["dsec"] = delta_spec(sim["cur_sector"], sim["new_sector"])
        charts["dctry"] = delta_spec(sim["cur_country"], sim["new_country"])
        sim_tpl = (f'<div class="simlist">{pos}</div><div class="mg half">'
                   f'<div><div class="mh">Sector weight change</div><div class="ch" data-chart="dsec"></div></div>'
                   f'<div><div class="mh">Country weight change</div><div class="ch" data-chart="dctry"></div></div>'
                   f'</div>')
    dock = ('<div class="btns">' + "".join(
        f'<button data-open="{k}" data-title="{t}">{label}</button>' for k, label, t in buttons)
        + '<button id="restart" class="restart" title="Back to the upload page">⟲ Restart</button>'
        + f'<a href="{GITHUB_URL}" target="_blank" rel="noopener noreferrer" title="Source code on GitHub">'
        + '⌥ GitHub ↗</a></div>')
    right.append(("Details", dock))
    title_k, title_v = "◆ Global exposure", f"{len(g)} countries"
    hint = "drag to spin · scroll to zoom · hover for positions · click a country to fly"
    if demo:  # showcase: no portfolio card, no detail windows, just the globe and two ranking cards
        left, charts = [], {}
        right = [("Top sectors", _rows([(k, 100 * v, "", None, None) for k, v in sec_w.head(7).items()], "s")),
                 ("Top countries", _rows(country_rows[:7], "c", fly=True))]
        title_k, title_v = "◆ Live demo", f"{len(df)} stocks · {len(g)} countries"
        hint = "sample portfolio · drag to spin · hover for positions"

    # Card titles that open the matching detail window (the rows keep their own hover / fly behaviour).
    title_opens = {} if demo else {"Top countries": ("countries", "Countries & regions"),
                                   "Top sectors": ("sectors", "Sectors")}

    def cards(items):
        # The inner stack centres vertically yet still scrolls from the top when it overflows.
        def head(t):
            if t not in title_opens:
                return f"<h3>{t}</h3>"
            k, title = title_opens[t]
            return f'<h3 class="lnk" data-open="{k}" data-title="{title}" title="Open {title}">{t}</h3>'
        return '<div class="stack">' + "".join(
            f'<section class="card">{head(t)}{body}</section>' for t, body in items) + "</div>"

    positions = [{"n": h.label, "i": h.isin, "t": h.ticker, "c": h.country, "s": h.sector, "r": h.region,
                  "v": float(h.value), "p": 100 * float(h.weight), "src": h.sources}
                 for h in holdings.itertuples()]

    def per_position(col):
        """Inspector list for one dimension: each position's share of a country / sector / region (ETF rows summed)."""
        a = (df.assign(_k=df[col]).groupby(["isin", "_k"], as_index=False, sort=False)
             .agg(n=("label", "first"), v=("value", "sum"), p=("weight", "sum"), c=("country", one_or_mixed),
                  s=("sector", one_or_mixed), r=("region", one_or_mixed))
             .sort_values("v", ascending=False))
        return [{"n": x.n, "c": x.c, "s": x.s, "r": x.r, "v": float(x.v), "p": 100 * float(x.p)}
                for x in a.itertuples()]

    inspect = {"s": per_position("sector"), "c": per_position("country"), "r": per_position("region")}

    def drill(col):
        """[isin, name, account, country|sector|region, value] rows for the drill-down lists."""
        a = (df.assign(key=df[col]).groupby(["isin", "account", "key"], as_index=False, sort=False)
             .agg(n=("label", "first"), v=("value", "sum")))
        return [[x.isin, x.n, x.account, x.key, round(float(x.v), 2)] for x in a.itertuples()]

    drills = {"c": drill("country"), "s": drill("sector"), "r": drill("region")}
    chart_js = "".join(
        f"CH[{json.dumps(k)}] = {{h: {json.dumps(c['h'])}, option: (() => {{ const option = {_js_json(c['option'])};\n"
        f"{c['js']}\nreturn option; }})()}};\n" for k, c in charts.items())

    top = f"{g.iloc[0]['country']} · {g.iloc[0]['pct']:.1f}%" if len(g) else "—"
    legend = ", ".join(rgb_hex(ramp(i / 4, GLOBE_STOPS)) for i in range(5))
    payload = _js_json({"features": features, "rings": rings})

    st.iframe(f"""<!doctype html><html><head>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<script src="{GLOBE_JS}"></script>
<script src="{ECHARTS_JS}"></script>
<style>
  * {{ box-sizing:border-box; }}
  html, body {{ margin:0; padding:0; height:100%; overflow:hidden; color:{INK}; font-family:Inter,system-ui,sans-serif;
               background:radial-gradient(1100px 700px at 50% 45%, rgba(57,135,229,0.10), transparent 62%), {STAGE}; }}
  #stage {{ position:relative; width:100%; height:100vh; }}
  #globe {{ position:absolute; inset:0; }}
  #glow {{ position:absolute; inset:0; pointer-events:none;
          background:radial-gradient(circle at 50% 50%, rgba(57,135,229,0.12), transparent 46%); }}
  .col {{ position:absolute; top:14px; bottom:14px; width:300px; display:flex; flex-direction:column; gap:12px;
         overflow-y:auto; scrollbar-width:none; z-index:5; }}
  .col::-webkit-scrollbar {{ display:none; }}
  #left {{ left:16px; }}
  /* Landing demo: a full-screen layer; JS shifts the globe right of the page's text column (placeDemoGlobe). */
  body.demo #right {{ right:24px; width:280px; }}
  .chip {{ position:absolute; top:4vh; right:24px; z-index:6; font:500 10.5px 'JetBrains Mono',monospace;
          letter-spacing:.14em; text-transform:uppercase; color:#86b6ef; border:1px solid rgba(57,135,229,.4);
          border-radius:999px; padding:.3rem .7rem; background:rgba(57,135,229,.08); }}
  html:has(body.demo), body.demo {{ background:transparent; }}  /* blend into the landing page behind it */
  .stack {{ margin:auto 0; display:flex; flex-direction:column; gap:12px; }}
  #right {{ right:16px; }}
  .card {{ background:rgba(12,15,21,.74); border:1px solid rgba(255,255,255,.07); border-radius:14px;
          padding:14px 16px 10px; backdrop-filter:blur(8px); -webkit-backdrop-filter:blur(8px);
          box-shadow:0 8px 30px rgba(0,0,0,.35); flex-shrink:0; }}
  .card h3 {{ margin:0 0 10px; font:500 10.5px 'JetBrains Mono',monospace; letter-spacing:.2em;
             text-transform:uppercase; color:{BLUE}; }}
  .card h3.lnk {{ cursor:pointer; transition:color .15s; }}
  .card h3.lnk::after {{ content:" ›"; }}
  .card h3.lnk:hover {{ color:{INK}; }}
  .cta {{ width:100%; margin:10px 0 4px; font:600 12.5px Inter,sans-serif; color:#fff; cursor:pointer;
         background:linear-gradient(90deg,#1c5cab,{BLUE}); border:1px solid rgba(134,182,239,.5); border-radius:10px;
         padding:9px 12px; box-shadow:0 0 18px rgba(57,135,229,.3); transition:all .15s; }}
  .cta:hover {{ box-shadow:0 0 26px rgba(57,135,229,.55); filter:brightness(1.08); }}
  .btns .restart {{ border-color:rgba(230,103,103,.4); background:rgba(230,103,103,.07); }}
  .btns .restart:hover {{ background:rgba(230,103,103,.16); box-shadow:0 0 16px rgba(230,103,103,.3); }}
  .btns .restart.armed {{ color:#fff; background:rgba(230,103,103,.35); }}
  .tform {{ display:flex; flex-direction:column; gap:12px; }}
  .tform label {{ font:10.5px 'JetBrains Mono',monospace; letter-spacing:.14em; text-transform:uppercase; color:{MUTED}; }}
  .tform input {{ width:100%; margin-top:6px; background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.12);
                 border-radius:9px; color:{INK}; padding:10px 12px; font:14px 'JetBrains Mono',monospace; outline:none; }}
  .tform input:focus {{ border-color:{BLUE}; box-shadow:0 0 0 3px rgba(57,135,229,.15); }}
  .tform .hint {{ font-size:12px; color:{MUTED}; line-height:1.5; }}
  .tform .err {{ font:12px 'JetBrains Mono',monospace; color:{RED}; min-height:16px; }}
  .tform .cta {{ margin:0; padding:11px; }}
  .mbox.sm {{ width:min(440px, 92vw); height:auto; }}
  .row {{ display:grid; grid-template-columns:1fr auto; column-gap:8px; font-size:12.5px; margin-bottom:9px; }}
  .row .nm {{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .row .pc {{ font-family:'JetBrains Mono',monospace; color:#86b6ef; font-size:12px; }}
  .row .sub {{ grid-column:1 / -1; font-size:10.5px; color:{MUTED}; margin-top:1px;
              white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .row .trk {{ grid-column:1 / -1; height:3px; border-radius:3px; background:rgba(255,255,255,.06); margin-top:4px; }}
  .row .trk div {{ height:100%; border-radius:3px; background:linear-gradient(90deg,#1c5cab,#5598e7);
                  box-shadow:0 0 8px rgba(57,135,229,.55); }}
  .row.unk .nm, .row.unk .pc {{ color:{MUTED}; }}
  .row.unk .trk div {{ background:{MUTED}; box-shadow:none; }}
  .row.hov {{ cursor:default; border-radius:6px; margin-inline:-6px; padding:3px 6px 0; }}
  .row.fly {{ cursor:pointer; }}
  .row.hov:hover {{ background:rgba(57,135,229,.10); }}
  .big {{ font-size:30px; font-weight:700; letter-spacing:-.02em; }}
  .bigsub {{ font:11.5px 'JetBrains Mono',monospace; color:{MUTED}; margin:2px 0 12px; }}
  .stats {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px 8px; padding-bottom:6px; }}
  .stats b {{ display:block; font-size:16px; font-weight:600; }}
  .stats span {{ font:10px 'JetBrains Mono',monospace; color:{MUTED}; letter-spacing:.04em; }}
  .warn {{ font:11px 'JetBrains Mono',monospace; color:{MUTED}; margin:6px 0 2px; }}
  .ihint {{ font-size:12px; color:{MUTED}; margin:0 0 12px; }}
  .isum {{ font-size:15px; color:{INK2}; margin:0 0 4px; }}
  .accr {{ cursor:pointer; }}
  .accl {{ cursor:pointer; border-radius:6px; margin-inline:-6px; padding:3px 6px 0; }}
  .accl:hover {{ background:rgba(57,135,229,.10); }}
  .lback {{ margin:0 0 12px; }}
  .acc {{ display:grid; grid-template-columns:250px 1fr; gap:22px; align-items:start; }}
  .acc-list {{ display:flex; flex-direction:column; gap:6px; position:sticky; top:0; }}
  .acc-item {{ text-align:left; background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.07);
              border-radius:10px; padding:9px 11px; color:{INK}; cursor:pointer; font:inherit; }}
  .acc-item .nm {{ display:block; font-size:13px; font-weight:600; }}
  .acc-item .v {{ display:block; font:11px 'JetBrains Mono',monospace; color:{MUTED}; margin-top:3px; }}
  .acc-item:hover {{ border-color:rgba(57,135,229,.45); }}
  .acc-item.on {{ border-color:{BLUE}; background:rgba(57,135,229,.12); }}
  .acc-head {{ margin-bottom:16px; }}
  .acc-head b {{ display:block; font-size:19px; font-weight:700; letter-spacing:-.01em; }}
  .acc-head span {{ font:11.5px 'JetBrains Mono',monospace; color:{MUTED}; }}
  .acc-detail table {{ font-size:12px; }}
  @media (max-width:820px) {{ .acc {{ grid-template-columns:1fr; }} .acc-list {{ position:static; }} }}
  .isum b {{ color:{INK}; font-weight:600; }}
  td.lk {{ white-space:normal; color:{MUTED}; min-width:220px; }}
  td.wr {{ white-space:normal; min-width:180px; }}
  .fx-open, .fx-reset, .fx-add, .fx-cancel {{ font:11px 'JetBrains Mono',monospace; color:#86b6ef; cursor:pointer;
    background:rgba(57,135,229,.08); border:1px solid rgba(57,135,229,.35); border-radius:7px; padding:4px 9px; }}
  .fx-open:hover, .fx-reset:hover, .fx-add:hover, .fx-cancel:hover {{ color:{INK}; border-color:{BLUE}; }}
  #fixbox {{ border:1px solid rgba(57,135,229,.35); border-radius:12px; padding:14px 16px; margin:0 0 16px;
    background:rgba(57,135,229,.05); }}
  .fxh {{ margin-bottom:12px; }} .fxh b {{ font-size:15px; margin-right:10px; }}
  .fxh span {{ font:11.5px 'JetBrains Mono',monospace; color:{MUTED}; }}
  .fx-row {{ display:flex; align-items:center; gap:6px; margin-bottom:6px; }}
  .fx-row .fx-k {{ flex:1; min-width:0; }}
  .fx-row input {{ background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.1); border-radius:7px;
    color:{INK}; padding:5px 8px; font:12px Inter,sans-serif; outline:none; }}
  .fx-row .fx-p {{ width:72px; font-family:'JetBrains Mono',monospace; }}
  .fx-row input:focus {{ border-color:{BLUE}; }}
  .fx-row span {{ font-size:11px; color:{MUTED}; }}
  .fx-x {{ width:24px; height:24px; border-radius:6px; cursor:pointer; color:{MUTED}; background:transparent;
    border:1px solid rgba(255,255,255,.1); }}
  .fx-x:hover {{ color:#fff; border-color:rgba(230,103,103,.6); }}
  .fx-tot {{ font:11px 'JetBrains Mono',monospace; color:{MUTED}; margin-top:6px; }}
  .fx-btns {{ display:flex; gap:10px; align-items:center; margin:14px 0 6px; }}
  .fx-btns .cta {{ width:auto; padding:8px 16px; margin:0; }}
  .fx-sec {{ margin-top:22px; }}
  .tabs {{ display:flex; gap:6px; margin-bottom:12px; }}
  .tab {{ font:11.5px 'JetBrains Mono',monospace; color:{MUTED}; background:transparent; cursor:pointer;
    border:1px solid rgba(255,255,255,.1); border-radius:8px; padding:6px 12px; }}
  .tab.on {{ color:{INK}; border-color:{BLUE}; background:rgba(57,135,229,.12); }}
  td.lin {{ white-space:normal; min-width:170px; vertical-align:top; font-size:11.5px; color:{INK2}; }}
  td.lin div {{ margin-top:4px; line-height:1.4; }}
  .isn {{ font-size:10.5px; color:{MUTED}; margin-top:3px; }}
  .tg {{ display:inline-block; font:600 9.5px 'JetBrains Mono',monospace; letter-spacing:.06em; text-transform:uppercase;
    border-radius:5px; padding:2px 6px; }}
  .tg-database {{ color:#86b6ef; background:rgba(57,135,229,.15); }}
  .tg-issuer {{ color:#7fd17f; background:rgba(12,163,12,.14); }}
  .tg-yahoo {{ color:#c9a6ff; background:rgba(150,100,255,.15); }}
  .tg-estimate {{ color:#f3d58c; background:rgba(250,178,25,.14); }}
  .tg-manual {{ color:#ffb3b3; background:rgba(230,103,103,.15); }}
  .tg-file {{ color:{INK2}; background:rgba(255,255,255,.07); }}
  .tg-none {{ color:{MUTED}; background:rgba(255,255,255,.04); }}
  .simpos {{ font-size:12.5px; margin-bottom:6px; color:#86b6ef; }}
  .simpos b {{ color:{INK}; font-weight:600; }}
  .simpos span {{ display:block; font:11px 'JetBrains Mono',monospace; color:{MUTED}; margin-left:14px; }}
  .sp {{ margin-bottom:9px; }}
  .sp-top {{ display:flex; align-items:center; justify-content:space-between; gap:8px; font-size:12.5px; color:#86b6ef; }}
  .sp-nm {{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .sp-nm b {{ color:{INK}; font-weight:600; }}
  .sp-x {{ flex-shrink:0; width:22px; height:22px; border-radius:6px; cursor:pointer; font-size:11px; line-height:1;
          color:{MUTED}; background:transparent; border:1px solid rgba(255,255,255,.1); }}
  .sp-x:hover {{ color:#fff; border-color:rgba(230,103,103,.6); background:rgba(230,103,103,.18); }}
  .sp-ctl {{ display:flex; align-items:center; gap:8px; margin:4px 0 0 14px; }}
  .sp-val {{ width:96px; background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.1); border-radius:7px;
            color:{INK}; padding:3px 7px; font:11.5px 'JetBrains Mono',monospace; outline:none; }}
  .sp-val:hover {{ border-color:rgba(57,135,229,.45); }}
  .sp-val:focus {{ border-color:{BLUE}; box-shadow:0 0 0 3px rgba(57,135,229,.15); }}
  .sp-val {{ -moz-appearance:textfield; appearance:textfield; }}
  .sp-val::-webkit-inner-spin-button, .sp-val::-webkit-outer-spin-button {{ -webkit-appearance:none; margin:0; }}
  .sp-ctl span {{ font:11px 'JetBrains Mono',monospace; color:{MUTED}; }}
  .sp.busy {{ opacity:.4; pointer-events:none; }}
  .dh {{ font:10px 'JetBrains Mono',monospace; letter-spacing:.14em; text-transform:uppercase;
        color:{MUTED}; margin:10px 0 6px; }}
  .drow {{ display:flex; justify-content:space-between; font-size:12px; margin-bottom:5px; gap:8px; }}
  .drow .nm {{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .drow .up {{ color:#86b6ef; font-family:'JetBrains Mono',monospace; white-space:nowrap; }}
  .drow .dn {{ color:{RED}; font-family:'JetBrains Mono',monospace; white-space:nowrap; }}
  .empty {{ color:{MUTED}; font-size:12px; padding-bottom:8px; }}
  .btns {{ display:grid; grid-template-columns:repeat(3, 1fr); gap:6px; }}
  .btns button, .btns a {{ font:11px 'JetBrains Mono',monospace; color:{INK2}; white-space:nowrap;
                 background:rgba(57,135,229,.08); border:1px solid rgba(57,135,229,.35); border-radius:9px;
                 padding:8px 4px; cursor:pointer; transition:all .15s; text-align:center; text-decoration:none; }}
  .btns button:hover, .btns a:hover {{ color:{INK}; background:rgba(57,135,229,.18); box-shadow:0 0 16px rgba(57,135,229,.35); }}
  .hud {{ position:absolute; z-index:4; pointer-events:none; text-align:center; left:50%; transform:translateX(-50%); }}
  #title {{ top:14px; }}
  #title .k {{ font:500 10.5px 'JetBrains Mono',monospace; letter-spacing:.22em; color:{BLUE}; text-transform:uppercase; }}
  #title .v {{ font-size:28px; font-weight:700; letter-spacing:-.02em; margin-top:2px; }}
  #title .s {{ font:12px 'JetBrains Mono',monospace; color:{INK2}; margin-top:2px; }}
  #legend {{ bottom:12px; font:11px 'JetBrains Mono',monospace; color:{INK2}; white-space:nowrap; }}
  #legend .lg {{ display:flex; align-items:center; justify-content:center; gap:10px; }}
  #legend .bar {{ width:150px; height:6px; border-radius:6px; background:linear-gradient(90deg,{legend}); }}
  #legend .hint {{ color:{MUTED}; margin-top:6px; font-size:10.5px; }}
  #legend .credit {{ color:#86b6ef; margin-left:.9em; padding-left:.9em; border-left:1px solid rgba(255,255,255,.15);
                    text-decoration:none; pointer-events:auto; transition:color .15s, text-shadow .15s; }}
  #legend .credit:hover {{ color:{INK}; text-shadow:0 0 12px rgba(57,135,229,.8); }}
  #title[hidden] {{ display:none; }}
  .tt {{ font:12px 'JetBrains Mono',monospace; color:{MUTED}; background:{SURFACE};
        border:1px solid rgba(255,255,255,.15); border-radius:8px; padding:6px 10px; }}

  /* Positions inspector (hover a sector, country or region). It never takes the pointer: a hover-able panel
     sat right where the cursor was heading and kept itself open. The wheel over its anchor scrolls it. */
  #insp[hidden] {{ display:none !important; }}  /* `display:flex` below would otherwise override [hidden] */
  #insp {{ position:fixed; z-index:40; width:320px; max-height:min(440px, 70vh); display:flex; flex-direction:column;
          pointer-events:none;
          background:rgba(13,17,24,.94); border:1px solid rgba(57,135,229,.55); border-radius:14px;
          box-shadow:0 0 0 1px rgba(57,135,229,.15), 0 18px 50px rgba(0,0,0,.55), 0 0 30px rgba(57,135,229,.2);
          backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px); overflow:hidden; }}
  #insp .ih {{ padding:12px 14px 10px; border-bottom:1px solid rgba(255,255,255,.07); }}
  #insp .ik {{ font:500 10px 'JetBrains Mono',monospace; letter-spacing:.2em; text-transform:uppercase; color:{BLUE}; }}
  #insp .ih b {{ display:block; font-size:15px; margin-top:2px; }}
  #insp .ih span {{ font:11.5px 'JetBrains Mono',monospace; color:#86b6ef; }}
  #insp .il {{ overflow-y:auto; padding:6px 6px 8px; scrollbar-width:thin; scrollbar-color:#2a3546 transparent; }}
  #insp .ir {{ display:flex; justify-content:space-between; gap:10px; padding:7px 8px; border-radius:8px; }}
  #insp .more {{ font:10px 'JetBrains Mono',monospace; letter-spacing:.08em; color:{MUTED}; text-align:center;
                padding:6px 0 8px; border-top:1px solid rgba(255,255,255,.06); }}
  #insp .ia {{ min-width:0; }}
  #insp .in {{ font-size:12.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  #insp .is {{ font-size:10.5px; color:{MUTED}; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  #insp .iv {{ font:12px 'JetBrains Mono',monospace; color:#86b6ef; text-align:right; white-space:nowrap; }}
  #insp .iv span {{ display:block; font-size:10.5px; color:{MUTED}; }}

  /* Detail windows */
  #modal {{ position:fixed; inset:0; z-index:30; display:flex; align-items:center; justify-content:center;
           background:rgba(4,6,9,.62); backdrop-filter:blur(3px); -webkit-backdrop-filter:blur(3px); }}
  #modal[hidden] {{ display:none; }}
  .mbox {{ width:min(1180px, 94vw); height:min(780px, 90vh); display:flex; flex-direction:column;
          background:rgba(12,15,21,.96); border:1px solid rgba(57,135,229,.4); border-radius:18px;
          box-shadow:0 30px 80px rgba(0,0,0,.6), 0 0 40px rgba(57,135,229,.15); overflow:hidden;
          animation:pop .18s ease-out; }}
  @keyframes pop {{ from {{ transform:scale(.97); opacity:0; }} to {{ transform:none; opacity:1; }} }}
  .mhead {{ display:flex; align-items:center; justify-content:space-between; padding:14px 18px;
           border-bottom:1px solid rgba(255,255,255,.07); }}
  .mhead h2 {{ margin:0; font:500 12px 'JetBrains Mono',monospace; letter-spacing:.2em; text-transform:uppercase; color:{BLUE}; }}
  #mclose {{ background:none; border:1px solid rgba(255,255,255,.12); color:{INK2}; border-radius:8px;
            width:30px; height:30px; cursor:pointer; font-size:14px; }}
  #mclose:hover {{ color:{INK}; border-color:{BLUE}; }}
  #mbody {{ flex:1; overflow:auto; padding:16px 18px; }}
  .mg {{ display:grid; gap:20px; }}
  .mg.half {{ grid-template-columns:1fr 1fr; }}
  .mh {{ font:10.5px 'JetBrains Mono',monospace; letter-spacing:.18em; text-transform:uppercase; color:{MUTED}; margin-bottom:8px; }}
  .simlist {{ display:flex; flex-wrap:wrap; gap:6px 24px; margin-bottom:14px; }}
  .tools {{ display:flex; gap:10px; margin-bottom:12px; }}
  .tools input {{ flex:1; background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.1); border-radius:9px;
                 color:{INK}; padding:8px 12px; font:12.5px Inter,sans-serif; outline:none; }}
  .tools input:focus {{ border-color:{BLUE}; }}
  .tools button {{ font:12px 'JetBrains Mono',monospace; color:{INK2}; background:rgba(57,135,229,.1);
                  border:1px solid rgba(57,135,229,.4); border-radius:9px; padding:8px 12px; cursor:pointer; }}
  .tools button:hover {{ color:{INK}; }}
  table {{ width:100%; border-collapse:collapse; font-size:12.5px; }}
  th {{ position:sticky; top:-16px; background:#10141b; text-align:left; font:500 10.5px 'JetBrains Mono',monospace;
       letter-spacing:.12em; text-transform:uppercase; color:{MUTED}; padding:9px 10px; cursor:pointer;
       border-bottom:1px solid rgba(255,255,255,.08); white-space:nowrap; }}
  th.on {{ color:{BLUE}; }}
  td {{ padding:8px 10px; border-bottom:1px solid rgba(255,255,255,.045); white-space:nowrap; }}
  td.m {{ font-family:'JetBrains Mono',monospace; font-size:12px; color:{INK2}; }}
  td.src {{ font-size:11px; color:{MUTED}; max-width:280px; overflow:hidden; text-overflow:ellipsis; }}
  td.w {{ min-width:150px; }}
  td.w .wb {{ display:inline-block; vertical-align:middle; width:80px; height:4px; border-radius:4px;
             background:rgba(255,255,255,.06); margin-right:8px; }}
  td.w .wb div {{ height:100%; border-radius:4px; background:linear-gradient(90deg,#1c5cab,#5598e7); }}
  tr:hover td {{ background:rgba(57,135,229,.06); }}
  {TERM_CSS}
  #mbody .term-body {{ height:calc(min(780px, 90vh) - 180px); }}

  {"" if demo else f'''@media (max-width: 980px) {{
    html, body {{ overflow-y:auto; }}
    #stage {{ height:auto; }}
    #globe, #glow {{ position:relative; height:460px; }}
    #glow {{ margin-top:-460px; }}
    .col {{ position:static; width:auto; overflow:visible; padding:12px 12px 0; }}
    .mg.half {{ grid-template-columns:1fr; }}
    #legend .hint {{ display:none; }}
  }}'''}  /* the landing demo sits in a narrower frame but keeps its side-by-side layout */
</style></head><body class="{'demo' if demo else ''}">
<div id="stage">
  <div id="globe"></div><div id="glow"></div>
  <div class="hud" id="title"{" hidden" if demo else ""}><div class="k">{title_k}</div><div class="v">{title_v}</div>
    <div class="s">largest · {html.escape(top)}</div></div>
  <div class="col" id="left">{cards(left)}</div>
  <div class="col" id="right">{cards(right)}</div>
  <div class="hud" id="legend"><div class="lg"><span>{wmin:.1f}%</span><div class="bar"></div>
    <span>{wmax:.1f}%</span></div>
    <div class="hint">{hint}<a class="credit" href="https://www.linkedin.com/in/gabin-le/" target="_blank"
      rel="noopener noreferrer" title="Gabin Lê on LinkedIn">Made by Gabin Lê · 2026 ↗</a></div></div>
</div>
<div id="insp" hidden></div>
<div id="modal" hidden><div class="mbox">
  <div class="mhead"><h2 id="mtitle"></h2><button id="mclose" title="Close (Esc)">✕</button></div>
  <div id="mbody"></div></div></div>

<template id="t-sectors"><div class="ch" data-chart="tree"></div></template>
<template id="t-countries"><div class="tools"><input id="cq" placeholder="Search a country or region…"
  autocomplete="off"></div><div class="mg half">
  <div><div class="mh">Weight by country</div><div class="ch" data-chart="ctrybars"></div></div>
  <div><div class="mh">Weight by region</div><div class="ch" data-chart="regbars"></div></div></div></template>
<template id="t-holdings"><div class="tools"><input id="q" placeholder="Filter by name, ISIN, ticker, country, sector…">
  <button id="csv">⤓ Export CSV</button></div><table><thead id="hh"></thead><tbody id="hb"></tbody></table></template>
<template id="t-sim">{sim_tpl}</template>
<template id="t-log"><div class="tabs"><button class="tab on" data-tab="scan">Scan log</button>
  <button class="tab" data-tab="src">Data sources</button></div>
  <div class="tabp" data-p="scan">{render_terminal(st.session_state.log, live=False)}</div>
  <div class="tabp" data-p="src" hidden>{lineage_tpl}</div></template>
<template id="t-issues">{issues_tpl}</template>
<template id="t-accounts"><div class="acc"><div class="acc-list" id="acc-list"></div>
  <div class="acc-detail" id="acc-detail"></div></div></template>
<template id="t-test"><form class="tform" id="tf" autocomplete="off">
  <div class="hint">An ETF from our data joins the portfolio right away; any other ISIN is scanned first
    (FinanceDatabase → yfinance → OpenFIGI) and joins as a simulated position if it resolves to a stock.</div>
  <label>ISIN<input id="t-isin" placeholder="e.g. US67066G1040" maxlength="12" required></label>
  <label>Position value<input id="t-val" type="number" min="1" step="any" value="10000" required></label>
  <div class="err" id="t-err"></div>
  <button class="cta" type="submit">Scan &amp; add</button>
</form></template>

<script>
const D = {payload};
const P = {_js_json(positions)};
const PI = {_js_json(inspect)};
const FIX = {_js_json(fix)};
const PD = {_js_json(drills)}, TOTAL = {float(total)};
const CW = {_js_json([[k, 100 * float(v)] for k, v in country_w.items()])};
const RW = {_js_json([[k, 100 * float(v)] for k, v in region_w.items()])};
const BAR = {{ grad: {_js_json(BAR_GRADIENT)}, muted: {_js_json(MUTED)} }};
const ACC = {_js_json(accounts)};
const NEUTRAL = {_js_json(list(NEUTRAL))}, NLABEL = {_js_json(NEUTRAL_LABEL)};  // per country / sector / region: an ETF appears once, with its share
const CH = {{}};
const TREE = {{ chart: null }};  // the live treemap, so its label formatter can read tile sizes
{chart_js}
const HELD = {HELD_ALT};
const esc = s => String(s).replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
const fmt = v => Math.round(v).toLocaleString();

// ── globe ───────────────────────────────────────────────────────────────────
const el = document.getElementById('globe');
const narrow = () => window.innerWidth <= 980 && !document.body.classList.contains('demo');
const world = Globe({{ animateIn: true }})(el)
  .width(el.clientWidth).height(el.clientHeight)
  .backgroundColor('rgba(0,0,0,0)')
  .globeImageUrl('{GLOBE_IMG}')
  .bumpImageUrl('{GLOBE_BUMP}')
  .showAtmosphere(true).atmosphereColor('{BLUE}').atmosphereAltitude(0.2)
  .polygonsData(D.features)
  // Unheld countries are outlines only: a filled cap hugging the sphere z-fights with the texture.
  .polygonCapColor(d => d.properties.held ? d.properties.cap : 'rgba(0,0,0,0)')
  .polygonSideColor(d => d.properties.held ? 'rgba(57,135,229,0.55)' : 'rgba(0,0,0,0)')
  .polygonStrokeColor(d => d.properties.held ? 'rgba(234,243,255,0.9)' : 'rgba(110,140,190,0.32)')
  .polygonAltitude(d => d.properties.held ? HELD : 0.006)
  .polygonCapCurvatureResolution(3)
  .polygonsTransitionDuration(300)
  .polygonLabel(({{ properties: p }}) => p.held ? '' : `<div class="tt">${{esc(p.name)}} · no exposure</div>`)
  .ringsData(D.rings)
  .ringColor(() => t => `rgba(134,182,239,${{(1 - t) * 0.8}})`)
  .ringMaxRadius(4).ringPropagationSpeed(1.4).ringRepeatPeriod(1800).ringAltitude(0.002);

const controls = world.controls();
controls.autoRotate = true;
controls.autoRotateSpeed = 0.4;
controls.enableDamping = true;
world.pointOfView({{ lat: 25, lng: 0, altitude: narrow() ? 2.4 : {2.35 if demo else 1.8} }}, 0);

let resumeT = null;
function holdSpin(ms) {{  // stop spinning; resume after `ms` (0 = until told otherwise)
  controls.autoRotate = false; clearTimeout(resumeT);
  if (ms) resumeT = setTimeout(() => {{ controls.autoRotate = true; }}, ms);
}}
function highlight(name) {{
  world.polygonAltitude(d => !d.properties.held ? 0.006 : (d.properties.name === name ? HELD + 0.025 : HELD));
}}
function flyTo(lat, lng) {{ holdSpin(9000); world.pointOfView({{ lat, lng, altitude: 1.5 }}, 1200); }}

// ── positions inspector ─────────────────────────────────────────────────────
const insp = document.getElementById('insp');
const KIND = {{ s: 'sector', c: 'country', r: 'region' }};
let hideT = null, inspKey = null;
let inspAnchor = null;  // the row element, or 'globe', that opened the panel
function hideInsp() {{ clearTimeout(hideT); insp.hidden = true; inspKey = null; inspAnchor = null; }}
function hideSoon() {{ clearTimeout(hideT); hideT = setTimeout(hideInsp, 90); }}  // tiny grace avoids row-to-row flicker
function showInsp(k, v, x, y, side, anchor) {{
  clearTimeout(hideT);
  inspAnchor = anchor;
  if (inspKey === k + '|' + v && !insp.hidden) return;
  const list = PI[k].filter(p => p[k] === v);
  if (!list.length) {{ hideInsp(); return; }}
  inspKey = k + '|' + v;
  const tot = list.reduce((a, p) => a + p.p, 0);
  const sub = k === 's' ? (p => p.c) : (p => p.s);
  insp.innerHTML = `<div class="ih"><div class="ik">${{KIND[k]}}</div><b>${{esc(v)}}</b>`
    + `<span>${{tot.toFixed(1)}}% · ${{list.length}} position${{list.length > 1 ? 's' : ''}}</span></div>`
    + `<div class="il">${{list.map(p => `<div class="ir"><div class="ia"><div class="in">${{esc(p.n)}}</div>`
    + `<div class="is">${{esc(sub(p))}}</div></div><div class="iv">${{p.p.toFixed(2)}}%<span>${{fmt(p.v)}}</span></div></div>`).join('')}}</div>`
    + `<div class="more" hidden>⇅ scroll the wheel to see all ${{list.length}}</div>`;
  insp.hidden = false;
  const il = insp.querySelector('.il');
  insp.querySelector('.more').hidden = il.scrollHeight <= il.clientHeight + 2;
  const r = insp.getBoundingClientRect();
  const left = side === 'left' ? x - r.width : x;
  insp.style.left = Math.max(8, Math.min(left, innerWidth - r.width - 8)) + 'px';
  insp.style.top = Math.max(8, Math.min(y, innerHeight - r.height - 8)) + 'px';
}}
// The wheel over the row / country that opened the panel scrolls the panel's list instead.
function wheelToInsp(e) {{
  const il = !insp.hidden && insp.querySelector('.il');
  if (!il || il.scrollHeight <= il.clientHeight) return;
  e.preventDefault(); e.stopPropagation();
  il.scrollTop += e.deltaY;
}}
function closeAll() {{ hideInsp(); endGlobeHover(); world.enablePointerInteraction(false); }}
// Safety nets on top of mouseleave: the browser's live :hover state says what is really under the cursor,
// and any movement over the host page (outside this frame) means the cursor has left us.
let lostAt = 0;
setInterval(() => {{
  if (insp.hidden) {{ lostAt = 0; return; }}
  const keep = inspAnchor === 'globe' ? !!globeHot && el.matches(':hover') : !!inspAnchor && inspAnchor.matches(':hover');
  if (keep) {{ lostAt = 0; return; }}
  if (!lostAt) lostAt = Date.now();
  else if (Date.now() - lostAt > 200) {{ closeAll(); lostAt = 0; }}
}}, 100);
document.addEventListener('mouseout', e => {{ if (!e.relatedTarget) closeAll(); }});
window.addEventListener('blur', closeAll);
try {{
  const host = window.parent.document;
  if (host !== document) {{
    const onHostMove = () => {{ if (!insp.hidden || globeHot) closeAll(); }};
    host.addEventListener('mousemove', onHostMove, {{ passive: true }});
    window.addEventListener('pagehide', () => host.removeEventListener('mousemove', onHostMove));
  }}
}} catch (e) {{}}

document.querySelectorAll('.row.hov').forEach(row => {{
  const {{ k, v }} = row.dataset;
  row.addEventListener('mouseenter', () => {{
    const r = row.getBoundingClientRect(), onRight = !!row.closest('#right');
    showInsp(k, v, onRight ? r.left - 12 : r.right + 12, r.top - 10, onRight ? 'left' : 'right', row);
    if (k === 'c') {{ highlight(v); holdSpin(0); }}
  }});
  row.addEventListener('mouseleave', () => {{ hideSoon(); if (k === 'c') {{ highlight(null); holdSpin(1500); }} }});
  row.addEventListener('wheel', wheelToInsp, {{ passive: false }});
  if (row.dataset.lat) row.addEventListener('click', () => flyTo(+row.dataset.lat, +row.dataset.lng));
}});

// Globe hover. globe.gl re-checks hover on every frame at the *last known* pointer position and never
// forgets it, so once the cursor is on a card (or outside the frame) the spinning globe kept "hovering"
// whatever country rotated under that stale spot. Hover is only honoured while the cursor is on the globe,
// and switching pointer interaction off on leave also clears globe.gl's stale hover state.
let mx = 0, my = 0, globeHot = null;
function startGlobeHover(f) {{
  globeHot = f;
  highlight(f.properties.name); holdSpin(0); el.style.cursor = 'pointer';
  showInsp('c', f.properties.name, mx + 18, my - 30, 'right', 'globe');
}}
function endGlobeHover() {{
  el.style.cursor = 'grab';
  if (!globeHot) return;
  globeHot = null; highlight(null); holdSpin(1500); hideSoon();
}}
el.addEventListener('pointermove', e => {{
  mx = e.clientX; my = e.clientY;
  if (!world.enablePointerInteraction()) world.enablePointerInteraction(true);
}});
el.addEventListener('pointerleave', () => {{ world.enablePointerInteraction(false); endGlobeHover(); }});
// Captured before the globe's own zoom handler: over a country with a long list, the wheel scrolls the list.
el.addEventListener('wheel', e => {{ if (globeHot) wheelToInsp(e); }}, {{ passive: false, capture: true }});
document.documentElement.addEventListener('mouseleave', () => {{
  world.enablePointerInteraction(false); endGlobeHover(); hideSoon();
}});
world.onPolygonHover(h => {{
  if (!world.enablePointerInteraction()) return;
  if (h && h.properties.held) startGlobeHover(h);
  else endGlobeHover();
}});
world.onPolygonClick(d => {{ if (d.properties.held) flyTo(d.properties.lat, d.properties.lng); }});

// ── detail windows ──────────────────────────────────────────────────────────
const modal = document.getElementById('modal'), mbody = document.getElementById('mbody');
const CHS = {{}};  // charts of the open window, by name
let live = [];
function openModal(id, title, small) {{
  hideInsp(); endGlobeHover();
  live.forEach(c => c.dispose()); live = [];
  for (const k in CHS) delete CHS[k];
  mbody.replaceChildren(document.getElementById('t-' + id).content.cloneNode(true));
  document.getElementById('mtitle').textContent = title;
  modal.querySelector('.mbox').classList.toggle('sm', !!small);
  modal.hidden = false;
  world.pauseAnimation();
  requestAnimationFrame(() => {{
    mbody.querySelectorAll('[data-chart]').forEach(c => {{
      const spec = CH[c.dataset.chart];
      c.style.height = (spec.h === 'fill' ? mbody.clientHeight - 34 : spec.h) + 'px';
      const chart = echarts.init(c, null, {{ renderer: 'canvas' }});
      CHS[c.dataset.chart] = chart;
      if (c.dataset.chart === 'tree') TREE.chart = chart;  // before setOption: the label formatter reads it
      chart.setOption(spec.option);
      if (c.dataset.chart === 'tree') {{
        // ECharts treemaps can only zoom on click; drill instead: the clicked sector becomes the whole view.
        chart.on('click', p => {{
          const path = p.treePathInfo || [];
          if (path.length >= 2) chart.dispatchAction({{ type: 'treemapRootToNode', seriesIndex: 0, targetNodeId: 's:' + path[1].name }});
        }});
      }}
      live.push(chart);
    }});
    if (id === 'holdings') initTable();
    if (id === 'accounts') initAccounts();
    if (id === 'issues') initFix();
    if (id === 'countries') initCountries();
    if (id === 'log') initTabs();
    if (id === 'test') initTest();
  }});
}}
function closeModal() {{
  hideInsp();
  live.forEach(c => c.dispose()); live = [];
  for (const k in CHS) delete CHS[k];
  modal.hidden = true; mbody.replaceChildren();
  world.resumeAnimation();
}}
document.querySelectorAll('[data-open]').forEach(b => b.addEventListener('click',
  () => openModal(b.dataset.open, b.dataset.title, b.dataset.small)));

// ── drill-down: every position in one country / sector / region, for the portfolio or one account ──
let back = null;  // the window to return to: {{ id, title, acc }}
function openList(k, key, acct, from) {{
  hideInsp();
  live.forEach(c => c.dispose()); live = [];
  for (const x in CHS) delete CHS[x];
  back = from || null;
  const rows = PD[k].filter(r => !acct || r[2] === acct);
  const whole = {{}};  // each position's value over all keys (in that account when filtered)
  rows.forEach(r => {{ whole[r[0]] = (whole[r[0]] || 0) + r[4]; }});
  const by = {{}};
  rows.filter(r => r[3] === key).forEach(r => {{
    const b = by[r[0]] || (by[r[0]] = {{ n: r[1], i: r[0], v: 0, a: new Set() }});
    b.v += r[4]; b.a.add(r[2]);
  }});
  const list = Object.values(by).sort((a, b) => b.v - a.v);
  const base = acct ? Object.values(whole).reduce((a, b) => a + b, 0) : TOTAL;
  const sum = list.reduce((a, x) => a + x.v, 0);
  const label = NLABEL[key] || key, of = acct ? 'of the account' : 'of the portfolio';
  document.getElementById('mtitle').textContent = KIND[k][0].toUpperCase() + KIND[k].slice(1) + ' · ' + label;
  modal.querySelector('.mbox').classList.remove('sm');
  mbody.innerHTML = (back ? '<button class="fx-cancel lback" id="lback">← Back</button>' : '')
    + `<div class="isum"><b>${{esc(label)}}</b>${{acct ? ' in <b>' + esc(acct) + '</b>' : ''}} · `
    + `${{(100 * sum / (base || 1)).toFixed(1)}}% ${{of}} · ${{fmt(sum)}} · ${{list.length}} position${{list.length > 1 ? 's' : ''}}</div>`
    + `<div class="hint ihint">"Part of the position" is how much of each position sits in ${{esc(label)}}: `
    + `100% for a stock, a share for a fund spread over several ${{KIND[k] === 'country' ? 'countries' : KIND[k] + 's'}}.</div>`
    + '<table><thead><tr><th>Position</th><th>ISIN / code</th>' + (acct ? '' : '<th>Account</th>')
    + `<th>Value here</th><th>% ${{of}}</th><th>Part of the position</th></tr></thead><tbody>`
    + list.map(x => `<tr><td class="wr">${{esc(x.n)}}</td><td class="m">${{esc(x.i)}}</td>`
      + (acct ? '' : `<td class="wr">${{esc([...x.a].join(', '))}}</td>`)
      + `<td class="m">${{fmt(x.v)}}</td><td class="m">${{(100 * x.v / (base || 1)).toFixed(2)}}%</td>`
      + `<td class="m">${{(100 * x.v / (whole[x.i] || 1)).toFixed(0)}}%</td></tr>`).join('')
    + '</tbody></table>';
  modal.hidden = false;
  world.pauseAnimation();
  const b = document.getElementById('lback');
  if (b) b.onclick = () => {{
    if (back.acc != null) accPick = back.acc;
    openModal(back.id, back.title);
  }};
}}

// ── countries window: search, and the positions inspector on hover (as on the cards) ──
const NLABEL_REV = Object.fromEntries(Object.entries(NLABEL).map(([k, v]) => [v, k]));
function barData(list, q) {{
  const hit = list.filter(([k]) => !q || (NLABEL[k] || k).toLowerCase().includes(q));
  const rows = hit.filter(([k]) => !NEUTRAL.includes(k)).slice(0, 20).concat(hit.filter(([k]) => NEUTRAL.includes(k)));
  return {{ names: rows.map(([k]) => NLABEL[k] || k),
           data: rows.map(([k, p]) => ({{ value: +p.toFixed(3), itemStyle: {{ color: NEUTRAL.includes(k) ? BAR.muted : BAR.grad }} }})) }};
}}
function initCountries() {{
  const q = document.getElementById('cq');
  const charts = [['ctrybars', CW, 'c'], ['regbars', RW, 'r']];
  q.addEventListener('input', () => {{
    const v = q.value.trim().toLowerCase();
    charts.forEach(([id, list]) => {{
      const ch = CHS[id];
      if (!ch) return;
      const d = barData(list, v);
      ch.setOption({{ yAxis: {{ data: d.names }}, series: [{{ data: d.data }}] }});
    }});
  }});
  charts.forEach(([id, , k]) => {{
    const ch = CHS[id];
    if (!ch) return;
    const box = ch.getDom();
    ch.on('mouseover', p => {{
      const e = p.event && p.event.event;
      if (!e) return;
      showInsp(k, NLABEL_REV[p.name] || p.name, e.clientX + 14, e.clientY + 10, 'right', box);
    }});
    ch.on('mouseout', hideSoon);
    ch.on('click', p => openList(k, NLABEL_REV[p.name] || p.name, null, {{ id: 'countries', title: 'Countries & regions' }}));
    box.addEventListener('mouseleave', hideSoon);
  }});
  q.focus();
}}

// ── log window tabs ──
function initTabs() {{
  mbody.querySelectorAll('.tab').forEach(t => t.onclick = () => {{
    mbody.querySelectorAll('.tab').forEach(x => x.classList.toggle('on', x === t));
    mbody.querySelectorAll('.tabp').forEach(p => p.hidden = p.dataset.p !== t.dataset.tab);
  }});
}}

// ── issues window: manual correction of a position's countries and sectors ──
function initFix() {{
  const box = document.getElementById('fixbox');
  let cur = null;
  const row = (d, k, p) => `<div class="fx-row"><input class="fx-k" list="dl-${{d}}" value="${{esc(k)}}" `
    + `placeholder="${{d === 'c' ? 'Country' : 'Sector'}}"><input class="fx-p" type="number" min="0" max="100" `
    + `step="0.1" value="${{p}}"><span>%</span><button class="fx-x" title="Remove">✕</button></div>`;
  const total = d => {{
    const t = [...box.querySelectorAll(`#fx-${{d}} .fx-p`)].reduce((a, i) => a + (+i.value || 0), 0);
    document.getElementById(`fx-${{d}}-tot`).textContent = `total ${{t.toFixed(1)}}%` + (Math.abs(t - 100) > 0.5 ? ' · rescaled to 100% on save' : '');
  }};
  const fill = (d, list) => {{
    const el = document.getElementById(`fx-${{d}}`);
    el.innerHTML = list.map(([k, p]) => row(d, k, p)).join('');
    total(d);
  }};
  box.addEventListener('input', e => {{ if (e.target.classList.contains('fx-p')) total(e.target.closest('#fx-c') ? 'c' : 's'); }});
  box.addEventListener('click', e => {{
    if (e.target.classList.contains('fx-x')) {{
      const d = e.target.closest('#fx-c') ? 'c' : 's'; e.target.parentElement.remove(); total(d);
    }}
    if (e.target.classList.contains('fx-add')) {{
      const d = e.target.dataset.d;
      document.getElementById(`fx-${{d}}`).insertAdjacentHTML('beforeend', row(d, '', 0)); total(d);
    }}
  }});
  mbody.querySelectorAll('[data-fix]').forEach(b => b.onclick = () => {{
    cur = b.dataset.fix; const f = FIX[cur];
    document.getElementById('fx-name').textContent = f.n;
    document.getElementById('fx-isin').textContent = cur;
    fill('c', f.c); fill('s', f.s);
    box.hidden = false; box.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  }});
  mbody.querySelectorAll('[data-reset]').forEach(b => b.onclick = () => send({{ action: 'override_reset', isin: b.dataset.reset }}));
  document.getElementById('fx-cancel').onclick = () => {{ box.hidden = true; cur = null; }};
  document.getElementById('fx-save').onclick = () => {{
    if (!cur) return;
    const read = d => Object.fromEntries([...box.querySelectorAll(`#fx-${{d}} .fx-row`)]
      .map(r => [r.querySelector('.fx-k').value.trim(), +r.querySelector('.fx-p').value || 0])
      .filter(([k, p]) => k && p > 0));
    send({{ action: 'override', isin: cur, country: read('c'), sector: read('s') }});
  }};
}}

// ── accounts window: account list on the left, the picked account's breakdowns on the right ──
let accPick = 0;
document.querySelectorAll('[data-acc]').forEach(r => r.addEventListener('click', () => {{
  accPick = +r.dataset.acc; openModal('accounts', 'Accounts');
}}));
function accRows(list, grey = true, dim = '') {{
  const top = Math.max(...list.map(x => x[1]), 1e-9);
  return list.map(([k, p]) => `<div class="row${{grey && NEUTRAL.includes(k) ? ' unk' : ''}}${{dim ? ' accl' : ''}}"`
    + (dim ? ` data-dk="${{dim}}" data-dv="${{esc(k)}}" title="Positions of this account in ${{esc(NLABEL[k] || k)}}"` : '') + '>'
    + `<span class="nm">${{esc(NLABEL[k] || k)}}</span><span class="pc">${{p.toFixed(1)}}%</span>`
    + `<div class="trk"><div style="width:${{p / top * 100}}%"></div></div></div>`).join('');
}}
function initAccounts() {{
  const list = document.getElementById('acc-list'), det = document.getElementById('acc-detail');
  list.innerHTML = ACC.map((a, i) => `<button class="acc-item" data-i="${{i}}"><span class="nm">${{esc(a.name)}}</span>`
    + `<span class="v">${{fmt(a.v)}} · ${{a.p.toFixed(1)}}%</span></button>`).join('');
  const show = i => {{
    list.querySelectorAll('.acc-item').forEach(b => b.classList.toggle('on', +b.dataset.i === i));
    const a = ACC[i];
    det.innerHTML = `<div class="acc-head"><b>${{esc(a.name)}}</b><span>${{fmt(a.v)}} · ${{a.p.toFixed(1)}}% of the `
      + `portfolio · ${{a.n}} position${{a.n > 1 ? 's' : ''}}</span></div><div class="mg half">`
      + `<div><div class="mh">Asset classes</div>${{accRows(a.assets, false)}}</div>`
      + `<div><div class="mh">Top countries</div>${{accRows(a.countries, true, 'c')}}</div>`
      + `<div><div class="mh">Top sectors</div>${{accRows(a.sectors, true, 's')}}</div>`
      + `<div><div class="mh">Positions</div><table><tbody>${{a.holdings.map(h => `<tr><td>${{esc(h.n)}}</td>`
      + `<td class="m">${{fmt(h.v)}}</td><td class="m">${{h.p.toFixed(1)}}%</td></tr>`).join('')}}</tbody></table></div>`
      + `</div>`;
    det.querySelectorAll('[data-dk]').forEach(r => r.onclick = () =>
      openList(r.dataset.dk, r.dataset.dv, a.name, {{ id: 'accounts', title: 'Accounts', acc: i }}));
  }};
  list.querySelectorAll('.acc-item').forEach(b => b.onclick = () => show(+b.dataset.i));
  show(Math.min(accPick, ACC.length - 1)); accPick = 0;
}}

// ── messages back to the Streamlit app ──────────────────────────────────────
// This frame shares the app's origin, so it types a JSON message into a hidden Streamlit text input.
function send(msg) {{
  const input = window.parent.document.querySelector('.st-key-bridge_box input');
  if (!input) return false;
  const setValue = Object.getOwnPropertyDescriptor(window.parent.HTMLInputElement.prototype, 'value').set;
  input.focus();
  setValue.call(input, JSON.stringify({{ ...msg, nonce: Date.now() }}));
  input.dispatchEvent(new Event('input', {{ bubbles: true }}));
  input.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }}));
  input.blur();
  return true;
}}

const ISIN_RE = /^[A-Z]{{2}}[A-Z0-9]{{9}}[0-9]$/;
function initTest() {{
  const f = document.getElementById('tf'), isin = document.getElementById('t-isin');
  const val = document.getElementById('t-val'), err = document.getElementById('t-err');
  isin.focus();
  isin.addEventListener('input', () => {{ isin.value = isin.value.toUpperCase().replace(/\\s/g, ''); err.textContent = ''; }});
  f.addEventListener('submit', e => {{
    e.preventDefault();
    const code = isin.value.trim(), v = parseFloat(val.value);
    if (!ISIN_RE.test(code)) {{ err.textContent = 'Not a valid ISIN (2 letters + 9 characters + 1 digit).'; return; }}
    if (!(v > 0)) {{ err.textContent = 'The value must be greater than 0.'; return; }}
    if (!send({{ action: 'test', isin: code, value: v }})) {{ err.textContent = 'Could not reach the app; use the sidebar form.'; return; }}
    const b = f.querySelector('button'); b.textContent = 'Scanning…'; b.disabled = true;
  }});
}}

// Simulation card: remove a test position, or edit its value (Enter / leaving the field applies it).
document.querySelectorAll('.sp-x').forEach(b => b.addEventListener('click', () => {{
  b.closest('.sp').classList.add('busy');
  send({{ action: 'sim_remove', index: +b.dataset.i }});
}}));
document.querySelectorAll('.sp-val').forEach(inp => {{
  inp.addEventListener('keydown', e => {{
    if (e.key === 'Enter') inp.blur();
    if (e.key === 'Escape') {{ inp.value = inp.defaultValue; inp.blur(); }}
  }});
  inp.addEventListener('change', () => {{
    const v = parseFloat(inp.value);
    if (!(v > 0)) {{ inp.value = inp.defaultValue; return; }}
    if (v === +inp.defaultValue) return;
    inp.closest('.sp').classList.add('busy');
    send({{ action: 'sim_value', index: +inp.dataset.i, value: v }});
  }});
}});

const restart = document.getElementById('restart');
let armT = null;
if (restart) restart.addEventListener('click', () => {{
  if (!restart.classList.contains('armed')) {{  // first click arms, second confirms
    restart.classList.add('armed'); restart.textContent = 'Sure?';
    armT = setTimeout(() => {{ restart.classList.remove('armed'); restart.textContent = '⟲ Restart'; }}, 3000);
    return;
  }}
  clearTimeout(armT); restart.textContent = '…';
  send({{ action: 'restart' }});
}});
document.getElementById('mclose').onclick = closeModal;
modal.addEventListener('click', e => {{ if (e.target === modal) closeModal(); }});
document.addEventListener('keydown', e => {{ if (e.key === 'Escape' && !modal.hidden) closeModal(); }});

// Holdings table: sortable, filterable, exportable.
const COLS = [['n', 'Name'], ['i', 'ISIN'], ['t', 'Ticker'], ['c', 'Country'], ['s', 'Sector'],
              ['v', 'Value'], ['p', 'Weight'], ['src', 'Data sources']];
let sortK = 'v', sortDir = -1;
function initTable() {{
  document.getElementById('q').addEventListener('input', renderTable);
  document.getElementById('csv').addEventListener('click', () => {{
    const q = s => `"${{String(s).replace(/"/g, '""')}}"`;
    const lines = [COLS.map(c => q(c[1])).join(',')].concat(P.map(p => COLS.map(([k]) => q(p[k])).join(',')));
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([lines.join('\\n')], {{ type: 'text/csv' }}));
    a.download = 'portfolio_enriched.csv'; a.click();
  }});
  renderTable();
}}
function renderTable() {{
  const q = document.getElementById('q').value.toLowerCase();
  const maxP = Math.max(...P.map(p => p.p));
  const rows = P.filter(p => !q || [p.n, p.i, p.t, p.c, p.s].join(' ').toLowerCase().includes(q))
    .sort((a, b) => (a[sortK] > b[sortK] ? 1 : a[sortK] < b[sortK] ? -1 : 0) * sortDir);
  document.getElementById('hh').innerHTML = '<tr>' + COLS.map(([k, l]) =>
    `<th data-k="${{k}}" class="${{k === sortK ? 'on' : ''}}">${{l}}${{k === sortK ? (sortDir > 0 ? ' ▲' : ' ▼') : ''}}</th>`).join('') + '</tr>';
  document.getElementById('hb').innerHTML = rows.map(p => `<tr><td>${{esc(p.n)}}</td><td class="m">${{p.i}}</td>`
    + `<td class="m">${{esc(p.t)}}</td><td>${{esc(p.c)}}</td><td>${{esc(p.s)}}</td><td class="m">${{fmt(p.v)}}</td>`
    + `<td class="w m"><span class="wb"><div style="width:${{p.p / maxP * 100}}%"></div></span>${{p.p.toFixed(2)}}%</td>`
    + `<td class="src" title="${{esc(p.src)}}">${{esc(p.src)}}</td></tr>`).join('');
  document.querySelectorAll('#hh th').forEach(th => th.onclick = () => {{
    sortDir = th.dataset.k === sortK ? -sortDir : (['v', 'p'].includes(th.dataset.k) ? -1 : 1);
    sortK = th.dataset.k; renderTable();
  }});
}}

// Landing demo: the frame spans the whole screen behind the page text, so centre the globe between the end of
// that text column and the card column with globe.gl's own view offset (picking stays exact; setting the
// camera's offset directly gets cleared by globe.gl's next update).
function placeDemoGlobe() {{
  if (!document.body.classList.contains('demo')) return;
  const W = el.clientWidth, H = el.clientHeight;
  if (!W || !H) return;  // not laid out yet: the ResizeObserver calls again once it is
  let textEdge = W * 0.42;
  try {{
    const t = window.parent.document.querySelector('.st-key-landing');
    if (t) textEdge = t.getBoundingClientRect().right;
  }} catch (e) {{}}
  const cardsEdge = document.getElementById('right').getBoundingClientRect().left;
  const cx = (textEdge + cardsEdge) / 2;
  // Size the globe to the free space between the text and the cards, with room for its halo:
  // screen radius r -> camera distance through the perspective camera's vertical field of view.
  const r = Math.max(140, H * 0.46);  // large hero globe: it may tuck behind the text and cards
  const half = (world.camera().fov * Math.PI / 180) / 2;
  const ang = Math.atan(r * Math.tan(half) / (H / 2));
  world.pointOfView({{ altitude: 1 / Math.sin(ang) - 1 }}, 0);
  world.globeOffset([cx - W / 2, 0]);
  document.querySelectorAll('.hud').forEach(h => h.style.left = cx + 'px');
  document.getElementById('glow').style.background =
    `radial-gradient(circle ${{r * 1.7}}px at ${{cx}}px 50%, rgba(57,135,229,0.16), transparent)`;
}}
placeDemoGlobe();

new ResizeObserver(() => {{
  world.width(el.clientWidth).height(el.clientHeight);
  placeDemoGlobe();
  live.forEach(c => c.resize());
}}).observe(document.body);
</script></body></html>""", height=900)


# ── ECharts specs (rendered inside the cockpit's detail windows) ─────────────
BAR_GRADIENT = {"type": "linear", "x": 0, "y": 0, "x2": 1, "y2": 0,
                "colorStops": [{"offset": 0, "color": "#1c5cab"}, {"offset": 1, "color": "#5598e7"}]}


def bars_spec(weights: pd.Series, top=12) -> dict:
    real, neutral = weights[~weights.index.isin(NEUTRAL)], weights[weights.index.isin(NEUTRAL)]
    w = (pd.concat([real.head(top), neutral]) * 100).round(3)  # grey rows stay last even past the cut-off
    data = [{"value": v, "itemStyle": {"color": MUTED if k in NEUTRAL else BAR_GRADIENT}} for k, v in w.items()]
    option = {
        "animationDuration": 900, "animationEasing": "cubicOut",
        # Fixed left gutter: containLabel ignores axisLabel.width, which clipped long names.
        "grid": {"left": Y_LABEL_W + 14, "right": 56, "top": 6, "bottom": 24},
        "tooltip": {**TOOLTIP, "trigger": "item"},
        "xAxis": {"type": "value", "splitNumber": 4,
                  "axisLabel": {"color": MUTED, "fontFamily": MONO, "fontSize": 10, "formatter": "{value}%"},
                  "splitLine": {"lineStyle": {"color": GRID}}},
        "yAxis": {"type": "category", "inverse": True, "data": [NEUTRAL_LABEL.get(k, k) for k in w.index],
                  "axisLabel": {"color": INK2, "fontFamily": SANS, "fontSize": 12,
                                "width": Y_LABEL_W, "overflow": "truncate"},
                  "axisLine": {"lineStyle": {"color": AXIS}}, "axisTick": {"show": False}},
        "series": [{
            "type": "bar", "data": data, "barWidth": "56%",
            "itemStyle": {"borderRadius": [0, 4, 4, 0]},
            "showBackground": True,
            "backgroundStyle": {"color": "rgba(255,255,255,0.025)", "borderRadius": [0, 4, 4, 0]},
            "label": {"show": True, "position": "right", "color": INK2, "fontFamily": MONO, "fontSize": 11},
            "emphasis": {"itemStyle": {"shadowBlur": 16, "shadowColor": "rgba(57,135,229,.7)"}},
        }],
    }
    js = """
option.series[0].label.formatter = p => p.value.toFixed(1) + '%';
option.tooltip.formatter = p => `<b>${p.name}</b><br/>${p.value.toFixed(2)}% of portfolio`;
"""
    return {"option": option, "js": js, "h": max(200, 36 * len(w) + 30)}


def treemap_spec(df) -> dict:
    """Sector → holding treemap: sector panels with header bands, holdings as glowing gradient tiles."""
    total = df["value"].sum()
    sec = df.groupby("sector")["value"].sum().sort_values(ascending=False)
    df = (df.groupby(["sector", "isin"], as_index=False, sort=False)
          .agg(label=("label", "first"), name=("name", "first"), ticker=("ticker", "first"),
               value=("value", "sum"), country=("country", one_or_mixed)))
    hmax = df["value"].max()
    data = []
    for s, v in sec.items():
        kids = []
        for k in df[df["sector"] == s].sort_values("value", ascending=False).itertuples():
            if s in NEUTRAL:
                lo, hi = "#3a3a38", "#5a5955"
            else:
                # Brighter tile = bigger position; capped so white labels keep contrast.
                t = k.value / hmax
                lo, hi = rgb_hex(ramp(t, SEQ[0:5])), rgb_hex(ramp(min(t + 0.3, 1), SEQ[1:6]))
            kids.append({
                "name": k.label, "value": float(k.value), "pct": float(100 * k.value / total),
                "tk": short_ticker(k.ticker, k.name), "sector": s, "country": k.country,
                "itemStyle": {"color": {"type": "linear", "x": 0, "y": 0, "x2": 1, "y2": 1,
                                        "colorStops": [{"offset": 0, "color": hi}, {"offset": 1, "color": lo}]}},
            })
        data.append({"id": f"s:{s}", "name": s, "value": float(v), "pct": 100 * v / total, "children": kids})

    option = {
        "tooltip": {**TOOLTIP, "trigger": "item"},
        "series": [{
            # Clicks drill into the sector (handled in the cockpit JS); the breadcrumb goes back.
            "type": "treemap", "name": "Portfolio", "data": data, "roam": False, "nodeClick": False,
            "left": 0, "right": 0, "top": 0, "bottom": 34, "squareRatio": 1.2,
            "visibleMin": 200, "animationDurationUpdate": 700, "animationEasing": "cubicInOut",
            "breadcrumb": {
                "show": True, "bottom": 4, "left": 0, "height": 24, "emptyItemWidth": 24,
                "itemStyle": {"color": SURFACE, "borderColor": "rgba(57,135,229,0.45)", "borderWidth": 1,
                              "shadowBlur": 0, "textStyle": {"color": INK2, "fontFamily": MONO, "fontSize": 11}},
                "emphasis": {"itemStyle": {"color": "#1a2433", "textStyle": {"color": INK}}},
            },
            # A treemap parent is painted with its borderColor (header band + gaps), not its fill.
            "levels": [
                {   # root
                    "itemStyle": {"borderColor": "rgba(0,0,0,0)", "borderWidth": 0, "gapWidth": 8},
                    "upperLabel": {"show": False},
                },
                {   # sectors: dark panels with a header band
                    "itemStyle": {"borderColor": "#141b26", "borderWidth": 5, "gapWidth": 3,
                                  "borderRadius": 12},
                    # Header reads "20.9%  Health Care": the weight comes first so truncation only eats the name.
                    # One line height and middle alignment for both fonts, so the mono % and the name share a
                    # baseline centred in the band; the small side padding lines the text up with the tiles.
                    "upperLabel": {"show": True, "height": 28, "color": INK, "fontFamily": SANS,
                                   "fontWeight": 600, "fontSize": 12.5, "lineHeight": 18, "verticalAlign": "middle",
                                   "padding": [2, 3, 0, 3], "overflow": "truncate", "ellipsis": "…", "rich": {
                                       "p": {"color": "#86b6ef", "fontFamily": MONO, "fontSize": 12,
                                             "fontWeight": 500, "lineHeight": 18, "verticalAlign": "middle"}}},
                    "emphasis": {"itemStyle": {"borderColor": "#1b2a40", "shadowBlur": 24,
                                               "shadowColor": "rgba(57,135,229,0.45)"}},
                },
                {   # holdings: the label adapts to each tile's real size (see the formatter)
                    "itemStyle": {"borderWidth": 0, "gapWidth": 3, "borderRadius": 8},
                    "label": {"show": True, "position": "insideTopLeft", "padding": [7, 8],
                              "overflow": "truncate", "ellipsis": "…", "rich": {
                                  "n": {"color": "#ffffff", "fontFamily": SANS, "fontWeight": 600,
                                        "fontSize": 12.5, "lineHeight": 18},
                                  "t": {"color": "#ffffff", "fontFamily": SANS, "fontWeight": 700,
                                        "fontSize": 12, "lineHeight": 16},
                                  "ts": {"color": "#ffffff", "fontFamily": SANS, "fontWeight": 700,
                                         "fontSize": 10, "lineHeight": 13},
                                  "p": {"color": "#cde2fb", "fontFamily": MONO, "fontSize": 10.5, "lineHeight": 15},
                                  "ps": {"color": "#cde2fb", "fontFamily": MONO, "fontSize": 9.5, "lineHeight": 12}}},
                    "emphasis": {"itemStyle": {"shadowBlur": 22, "shadowColor": "rgba(134,182,239,0.7)"},
                                 "label": {"show": True}},
                },
            ],
        }],
    }
    js = """
const s = option.series[0];
s.levels[1].upperLabel.formatter = p => `{p|${p.data.pct.toFixed(1)}%}  ${p.name}`;
// Pick what a tile shows from its real on-screen size, so labels also grow when zooming into a sector:
// name + ticker · weight on big tiles, ticker + weight on medium ones, a compact ticker on small ones.
s.levels[2].label.formatter = p => {
  let w = 0, h = 0;
  try {
    const l = TREE.chart.getModel().getSeriesByIndex(0).getData().getItemLayout(p.dataIndex);
    w = l.width; h = l.height;
  } catch (e) { w = h = Math.sqrt(p.data.pct) * 80; }  // fallback: rough size from the weight
  const pct = p.data.pct.toFixed(1) + '%', tk = p.data.tk;
  if (w < 30 || h < 22) return '';
  if (w >= 92 && h >= 56) return `{n|${p.name}}\\n{t|${tk}}  {p|${pct}}`;
  if (w >= 52 && h >= 40) return `{t|${tk}}\\n{p|${pct}}`;
  if (h >= 30) return `{ts|${tk}}\\n{ps|${pct}}`;
  return `{ts|${tk}}`;
};
option.tooltip.formatter = p => {
  if (!p.data || p.data.pct == null) return p.name;
  const sub = p.data.tk ? `${p.data.tk} · ${p.data.sector} · ${p.data.country}<br/>` : '';
  return `<b>${p.name}</b><br/>${sub}${p.data.pct.toFixed(2)}% of portfolio<br/>value ${Math.round(p.value).toLocaleString()}`;
};
"""
    return {"option": option, "js": js, "h": "fill"}  # takes the detail window's full height


def short_ticker(ticker: str, name: str) -> str:
    """Compact tile label: the ticker without its exchange suffix, else the name's first word."""
    t = (ticker or "").split(".")[0].strip()
    return (t or (name or "?").split()[0])[:8]


def delta_spec(before: pd.Series, after: pd.Series, top=12) -> dict:
    idx = after.index.union(before.index)
    b, a = before.reindex(idx, fill_value=0) * 100, after.reindex(idx, fill_value=0) * 100
    d = a - b
    d = d[d.abs() > 0.005]
    d = d.reindex(d.abs().sort_values(ascending=False).index).head(top)
    data = [{
        "value": round(float(v), 4), "before": float(b[k]), "after": float(a[k]),
        "itemStyle": {"color": BLUE if v >= 0 else RED,
                      "borderRadius": [0, 4, 4, 0] if v >= 0 else [4, 0, 0, 4]},
        "label": {"position": "right" if v >= 0 else "left"},
    } for k, v in d.items()]
    span = max(float(d.abs().max()) * 1.4, 0.5) if len(d) else 1
    option = {
        "animationDuration": 900,
        "grid": {"left": Y_LABEL_W + 14, "right": 64, "top": 6, "bottom": 24},
        "tooltip": {**TOOLTIP, "trigger": "item"},
        "xAxis": {"type": "value", "min": -span, "max": span, "splitNumber": 4,
                  "axisLabel": {"color": MUTED, "fontFamily": MONO, "fontSize": 10},
                  "splitLine": {"lineStyle": {"color": GRID}}},
        "yAxis": {"type": "category", "inverse": True, "data": list(d.index),
                  "axisLabel": {"color": INK2, "fontFamily": SANS, "width": Y_LABEL_W, "overflow": "truncate"},
                  "axisLine": {"show": False}, "axisTick": {"show": False}},
        "series": [{
            "type": "bar", "data": data, "barWidth": "52%",
            "label": {"show": True, "color": INK2, "fontFamily": MONO, "fontSize": 11},
            "markLine": {"silent": True, "symbol": "none", "data": [{"xAxis": 0}],
                         "lineStyle": {"color": AXIS, "type": "solid"}, "label": {"show": False}},
        }],
    }
    js = """
option.series[0].label.formatter = p => (p.value >= 0 ? '+' : '') + p.value.toFixed(2) + ' pp';
option.xAxis.axisLabel.formatter = v => v.toFixed(1) + ' pp';
option.tooltip.formatter = p => `<b>${p.name}</b><br/>${p.data.before.toFixed(2)}% → ${p.data.after.toFixed(2)}%`
  + `<br/>Δ ${(p.value >= 0 ? '+' : '') + p.value.toFixed(2)} pp`;
"""
    return {"option": option, "js": js, "h": max(180, 36 * len(d) + 30)}


# ----------------------------------------------------------------------------
# Page
# ----------------------------------------------------------------------------
# Once results show, the app is a single full-screen stage: no page scroll, no Streamlit chrome.
STAGE_CSS = """
<style>
html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] { overflow: hidden !important; }
[data-testid="stMainBlockContainer"], .block-container { padding: 0 !important; max-width: 100% !important; }
[data-testid="stMain"] [data-testid="stVerticalBlock"] { gap: 0 !important; }
[data-testid="stMain"] iframe { height: 100vh !important; display: block; }
[data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"] { display: none !important; }
[data-testid="stHeader"] { pointer-events: none; background: transparent; }
[data-testid="stHeader"] button { pointer-events: auto; }
.st-key-bridge_box { position: absolute !important; left: -10000px; width: 10px; height: 0; overflow: hidden; }
</style>
"""


LANDING_CSS = f"""
<style>
html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{ overflow: hidden !important; }}
[data-testid="stMainBlockContainer"], .block-container {{ padding: 0 0 0 4.5vw !important; max-width: 100% !important; }}
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stHeader"],
[data-testid="stDecoration"], [data-testid="stStatusWidget"] {{ display: none !important; }}
/* The demo globe frame is a full-screen layer behind the page, so the globe and its halo are never clipped
   by a column box; the text column floats above it. */
[data-testid="stMain"] iframe {{ position: fixed !important; inset: 0; width: 100vw !important; height: 100vh !important;
  display: block; z-index: 0; }}
[data-testid="stMain"] .st-key-landing {{ z-index: 2; }}
[data-testid="stMain"] [data-testid="stVerticalBlock"] {{ gap: 0 !important; }}
[data-testid="stMain"] [data-testid="stElementContainer"]:has(> [data-testid="stMarkdown"] style) {{ display: none; }}
[data-testid="stMain"] .st-key-landing {{ position: relative; min-height: 100vh !important; height: 100vh !important;
  display: flex !important; flex-direction: column !important; justify-content: center !important;
  gap: .9rem !important; padding-right: 1.5rem; }}
.st-key-landing > * {{ animation: rise .7s cubic-bezier(.2,.7,.2,1) both; }}
.st-key-landing > *:nth-child(2) {{ animation-delay: .12s; }}
.st-key-landing > *:nth-child(3) {{ animation-delay: .22s; }}
.st-key-landing > *:nth-child(4) {{ animation-delay: .32s; }}
@keyframes rise {{ from {{ opacity: 0; transform: translateY(14px); }} to {{ opacity: 1; transform: none; }} }}

.st-key-landing > :first-child {{ position: absolute !important; top: 4vh; left: 0; right: 1.5rem; }}  /* the nav */
.lnav {{ display: flex; align-items: center; justify-content: space-between; }}
.lnav .logo {{ font-weight: 700; font-size: 1.05rem; letter-spacing: -.01em; color: {INK}; }}
.lnav .logo i {{ font-style: normal; color: {BLUE}; margin-right: .35rem; }}
.lnav .chip {{ font: 500 10.5px 'JetBrains Mono', monospace; letter-spacing: .14em; text-transform: uppercase;
              color: #86b6ef; border: 1px solid rgba(57,135,229,.4); border-radius: 999px; padding: .3rem .7rem;
              background: rgba(57,135,229,.08); }}
.leyebrow {{ font: 500 11px 'JetBrains Mono', monospace; letter-spacing: .24em; text-transform: uppercase;
            color: {BLUE}; margin-bottom: .7rem; display: flex; align-items: center; gap: .6rem; }}
.leyebrow::before {{ content: ""; width: 26px; height: 1px; background: {BLUE}; }}
.lh1 {{ font-size: clamp(2.4rem, 4.4vw, 4.2rem) !important; line-height: 1.02 !important; font-weight: 700 !important;
       letter-spacing: -.045em; margin: 0 0 1rem !important; padding: 0 !important; color: {INK}; }}
.lh1 span {{ background: linear-gradient(90deg, {BLUE} 0%, #86b6ef 55%, #cde2fb 100%);
            -webkit-background-clip: text; background-clip: text; color: transparent; }}
.lsub {{ font-size: 1.02rem; line-height: 1.6; color: {INK2}; max-width: 34rem; margin: 0 0 1.6vh; }}
.lsub b {{ color: {INK}; font-weight: 600; }}
.st-key-landing [data-testid="stFileUploaderDropzone"] {{
  padding: 1.5rem 1.4rem; border-radius: 16px; border: 1px dashed rgba(57,135,229,.65);
  background: linear-gradient(135deg, rgba(57,135,229,.14), rgba(57,135,229,.03));
  box-shadow: 0 0 0 1px rgba(57,135,229,.08), 0 20px 50px -20px rgba(57,135,229,.45); transition: all .2s;
}}
.st-key-landing [data-testid="stFileUploaderDropzone"]:hover {{
  border-color: #86b6ef; box-shadow: 0 0 0 1px rgba(57,135,229,.25), 0 24px 60px -18px rgba(57,135,229,.6);
}}
.lrow {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: .4rem; }}
.lbox {{ border: 1px solid rgba(255,255,255,.07); border-radius: 14px; padding: .9rem 1rem;
        background: rgba(255,255,255,.025); }}
.lk {{ font: 500 10px 'JetBrains Mono', monospace; letter-spacing: .2em; text-transform: uppercase; color: {MUTED};
      margin-bottom: .6rem; }}
.lbox table {{ width: 100%; border-collapse: collapse; font: 12px 'JetBrains Mono', monospace; color: {INK2}; }}
.lbox th {{ text-align: left; color: {BLUE}; font-weight: 500; padding: 0 0 .35rem; }}
.lbox td {{ padding: .2rem 0; border-top: 1px solid rgba(255,255,255,.05); }}
.lstep {{ display: flex; gap: .7rem; align-items: baseline; font-size: .86rem; color: {INK2}; margin-bottom: .35rem; }}
.lstep b {{ font: 600 11px 'JetBrains Mono', monospace; color: {BLUE}; }}
.lfoot {{ display: flex; flex-wrap: wrap; gap: .4rem 1.2rem; font: 11px 'JetBrains Mono', monospace; color: {MUTED};
         margin-top: .4rem; }}
.lfoot span::before {{ content: "◆ "; color: {BLUE}; }}
.st-key-demo button {{ padding: 0 !important; min-height: 0 !important; }}
.st-key-demo button p {{ font: 600 13px 'JetBrains Mono', monospace !important; letter-spacing: .04em; color: #86b6ef; }}
.st-key-demo button:hover p {{ color: {INK}; text-shadow: 0 0 14px rgba(57,135,229,.8); }}
</style>
"""


def landing():
    """Full-screen landing page: pitch and upload on the left, the live demo globe on the right."""
    st.markdown(LANDING_CSS, unsafe_allow_html=True)
    left, right = st.columns([5, 7], gap="small")
    with left, st.container(key="landing"):
        st.markdown('<div class="lnav"><span class="logo"><i>◆</i>PortfolioScan</span>'
                    '</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="leyebrow">Portfolio intelligence</div>'
            '<h1 class="lh1">See where your<br><span>money lives.</span></h1>'
            '<p class="lsub">Drop your <b>broker or insurer exports</b> (CSV or Excel, several at once). Stocks, '
            'ETFs, SCPI and euro funds are mapped by <b>country, sector and asset class</b> on a live 3D globe. ETF COMPATIBILITY IS LIMITED ON THE WEB DEPLOYMENT DUE TO API RESTRICTION. FOR BETTER ETF COMPATIBILITY, PLEASE USE RUN THE APP LOCALLY. </p>',
            unsafe_allow_html=True,
        )
        files = st.file_uploader("Portfolio exports", type=["xlsx", "xls", "csv", "txt"], accept_multiple_files=True,
                                 label_visibility="collapsed", key="up_main")
        start = bool(files) and st.button(f"▶ Analyse {len(files)} file{'s' if len(files) > 1 else ''}",
                                          type="primary", key="start")
        if DEMO_FILE.exists() and st.button("▶ Explore a demo portfolio", type="tertiary", key="demo"):
            load_demo()
            st.rerun()
        st.markdown(
            '<div class="lrow">'
            '<div class="lbox"><div class="lk">File format</div><table>'
            '<tr><th>Any export</th><th>Auto-detected</th></tr>'
            '<tr><td>ISIN column</td><td>✓</td></tr><tr><td>Value column</td><td>✓</td></tr>'
            '</table></div>'
            '<div class="lbox"><div class="lk">How it works</div>'
            '<div class="lstep"><b>01</b>Upload, check the detected columns</div>'
            '<div class="lstep"><b>02</b>Live scan across 3 sources</div>'
            '<div class="lstep"><b>03</b>Explore, then test new positions</div></div></div>'
            '<div class="lfoot"><span>45k+ stocks indexed</span><span>2,600+ ETFs</span>'
            '<span>SCPI, euro funds &amp; cash</span></div>',
            unsafe_allow_html=True,
        )
    with right:
        try:
            cockpit(demo_frame(), demo=True)
        except Exception:  # noqa: BLE001 — the showcase is optional (e.g. offline on first run)
            pass
    return files if start else None


@st.dialog("Check the import", width="large")
def review_import(i: int):
    """Show what was detected in file i, let the user correct it, and confirm or skip the file."""
    ss = st.session_state
    f = ss.imports[i]
    st.caption(f"File {i + 1} of {len(ss.imports)} · {f['name']}")
    try:
        tables = read_tables(f["name"], f["data"])
    except Exception as e:  # noqa: BLE001 — unreadable file: say so, let the user skip it
        st.error(f"This file could not be read ({type(e).__name__}: {e}).")
        tables = []
    if tables and tables[0].sep is not None:  # CSV / text: the separator can be changed
        seps = list(SEPARATORS)
        sep = st.selectbox("Separator", seps, index=seps.index(tables[0].sep), format_func=SEPARATORS.get,
                           key=f"imp{i}_sep")
        if sep != tables[0].sep:
            tables = read_tables(f["name"], f["data"], sep)
    elif len(tables) > 1:  # Excel with several sheets: default to the one with the most positions
        found = [len(extract(t.raw, detect(t.raw))[0]) for t in tables]
        k = st.selectbox("Sheet", range(len(tables)), index=found.index(max(found)),
                         format_func=lambda k: f"{tables[k].label} ({found[k]} positions found)", key=f"imp{i}_sheet")
        tables = [tables[k]]

    if tables:
        t = tables[0]
        guess = detect(t.raw)
        tag = f"imp{i}_{t.label}_{t.sep}"  # new widgets (and new defaults) whenever the table changes
        heads = [None] + list(range(min(len(t.raw), 30)))
        header = st.selectbox(
            "Header row", heads, index=heads.index(guess["header"]) if guess["header"] in heads else 0,
            format_func=lambda h: "none" if h is None else
            f"row {h + 1} · " + " | ".join(v for v in t.raw.iloc[h].astype(str) if v.strip())[:90],
            key=f"{tag}_header")
        labels = column_labels(t.raw, header)
        cols = list(range(t.raw.shape[1]))
        c1, c2, c3 = st.columns(3)
        code = c1.selectbox("ISIN / code column", cols, index=guess["code"], format_func=labels.__getitem__,
                            key=f"{tag}_code")
        value = c2.selectbox("Value column (amount held)", cols, index=guess["value"],
                             format_func=labels.__getitem__, key=f"{tag}_value")
        names = [None] + cols
        name = c3.selectbox("Name column (optional)", names, index=names.index(guess["name"]),
                            format_func=lambda c: "none" if c is None else labels[c], key=f"{tag}_name")
        a1, a2, a3 = st.columns(3)
        account = a1.selectbox("Account column (optional)", names, index=names.index(guess["account"]),
                               format_func=lambda c: "none" if c is None else labels[c], key=f"{tag}_account",
                               help="One account per distinct value, e.g. a contract number.")
        account_label = a2.selectbox("Account label (optional)", names, index=names.index(guess["account_label"]),
                                     format_func=lambda c: "none" if c is None else labels[c],
                                     key=f"{tag}_account_label", disabled=account is None,
                                     help="Put in front of the account value, e.g. the product name.")
        account_name = a3.text_input("Account name", value=Path(f["name"]).stem, key=f"{tag}_account_name",
                                     help="Used for every line when there is no account column.")

        st.markdown("**File preview**: the selected columns are highlighted")
        preview = t.raw.head(12).copy()
        preview.columns = labels
        picked = {labels[code]: "rgba(57,135,229,.30)", labels[value]: "rgba(12,163,12,.30)"}
        if name is not None:
            picked[labels[name]] = "rgba(250,178,25,.22)"
        st.dataframe(preview.style.apply(lambda col: [f"background-color: {picked.get(col.name, '')}"] * len(col)),
                     hide_index=True, height=250)

        pos, ignored = extract(t.raw, {"header": header, "code": code, "value": value, "name": name,
                                       "account": account, "account_label": account_label}, account_name)
        st.markdown(f"**{len(pos)} position{'s' if len(pos) != 1 else ''} recognised** · total "
                    f"{pos['value'].sum():,.2f} · {len(ignored)} row{'s' if len(ignored) != 1 else ''} ignored")
        st.dataframe(pos.rename(columns={"isin": "ISIN / code", "value": "Value", "name": "Name", "kind": "Type",
                                         "account": "Account"}), hide_index=True, height=220)
        by_acct = pos.groupby("account", sort=False)["value"].sum()
        if len(by_acct) > 1:
            st.caption(f"{len(by_acct)} accounts: " + " · ".join(f"{a} ({v:,.0f})" for a, v in by_acct.items()))
        if len(ignored):
            with st.expander(f"Ignored rows ({len(ignored)})"):
                st.dataframe(ignored, hide_index=True)
    else:
        pos = pd.DataFrame()

    b1, b2, b3 = st.columns([2, 1, 1])
    if b1.button("✓ Confirm this file", type="primary", disabled=pos.empty, key=f"imp{i}_ok", width="stretch"):
        ss.import_done.append({"name": f["name"], "positions": pos})
        ss.import_step += 1
        st.rerun()
    if b2.button("Skip file", key=f"imp{i}_skip", width="stretch"):
        ss.import_step += 1
        st.rerun()
    if b3.button("Cancel", key=f"imp{i}_cancel", width="stretch"):
        ss.update(imports=None, import_step=0, import_done=[])
        st.rerun()


def hero():
    st.markdown(
        '<div class="hero"><div class="hero-tag">◆ Portfolio intelligence · stocks, ETFs, SCPI &amp; euro funds</div>'
        '<h1>Portfolio<span>Scan</span></h1>'
        '<p>Upload holdings → resolve every ISIN across FinanceDatabase, yfinance &amp; OpenFIGI '
        '→ see where your money lives.</p></div>',
        unsafe_allow_html=True,
    )


def on_bridge():
    """Handle a JSON message typed into the hidden bridge input by the cockpit frame."""
    raw = st.session_state.get("bridge") or ""
    st.session_state.bridge = ""
    try:
        msg = json.loads(raw)
    except ValueError:
        return
    if msg.get("action") == "test":
        isin, value = str(msg.get("isin", "")).strip().upper(), to_float(msg.get("value"))
        if ISIN_RE.match(isin) and value and value > 0:
            st.session_state.pending_sim = {"isin": isin, "value": value}
    elif msg.get("action") == "override" and isinstance(msg.get("isin"), str):
        clean_dim = {}
        for dim in ("country", "sector"):
            raw = msg.get(dim) if isinstance(msg.get(dim), dict) else {}
            vals = {str(k).strip()[:60]: float(v) for k, v in list(raw.items())[:40]
                    if str(k).strip() and isinstance(v, (int, float)) and v > 0}
            tot = sum(vals.values())
            if tot:
                clean_dim[dim] = {k: 100 * v / tot for k, v in vals.items()}
        if clean_dim:
            st.session_state.overrides[msg["isin"]] = clean_dim
            st.session_state.flash = (f"Correction saved for {position_name(msg['isin'])}", ":material/edit:")
    elif msg.get("action") == "override_reset" and isinstance(msg.get("isin"), str):
        st.session_state.overrides.pop(msg["isin"], None)
        st.session_state.flash = (f"Correction removed for {position_name(msg['isin'])}", ":material/undo:")
    elif msg.get("action") == "restart":
        st.session_state.update(pf=None, sims=[], pending_sim=None, log=[], imports=None, import_step=0,
                                import_done=[])
    elif msg.get("action") in ("sim_remove", "sim_value"):
        sims, i = st.session_state.sims, msg.get("index")
        if not isinstance(i, int) or not 0 <= i < len(sims):
            return
        if msg["action"] == "sim_remove":
            sims.pop(i)
        elif (value := to_float(msg.get("value"))) and value > 0:
            sims[i] = {**sims[i], "value": value}


figi_key = server_figi_key()  # server-side only (env var or Streamlit secret)

# ── Landing: ask for the file ────────────────────────────────────────────────
ss = st.session_state
if ss.pf is None and ss.imports and ss.import_step >= len(ss.imports):  # every file reviewed: merge them
    done = ss.import_done
    if done:
        ss.pf = {"name": ", ".join(d["name"] for d in done),
                 "positions": pd.concat([d["positions"] for d in done], ignore_index=True)}
        ss.start_kick = "pending"
    else:
        ss.flash = ("No file was imported", ":material/info:")
    ss.update(imports=None, import_step=0, import_done=[])
    if not done:
        st.rerun()


@st.fragment(run_every=0.25)
def start_analysis():
    """Streamlit closes a dialog only when a run finishes. So the run after the last confirmation ends right away
    (closing it), and this timer starts the analysis on the next tick instead of inside that run."""
    if st.session_state.start_kick == "armed":
        st.session_state.start_kick = None
        st.rerun(scope="app")
    st.session_state.start_kick = "armed"


if ss.start_kick:
    landing()
    start_analysis()
    st.stop()

if ss.pf is None:
    files = landing()
    if ss.flash:
        st.toast(ss.flash[0], icon=ss.flash[1])
        ss.flash = None
    if files:
        ss.update(imports=[{"name": f.name, "data": f.getvalue()} for f in files], import_step=0, import_done=[])
        st.rerun()
    if ss.imports:
        review_import(ss.import_step)
    st.stop()

pf = ss.pf
parse_error = None
try:
    if "positions" in pf:  # reviewed imports: one row per line, several files may hold the same ISIN
        portfolio = (pf["positions"].groupby(["isin", "account"], as_index=False, sort=False)
                     .agg(value=("value", "sum"), name=("name", "first")))
        rejected = pd.DataFrame()
    else:
        portfolio, rejected = parse_portfolio(pf["data"])
        portfolio = portfolio.assign(account=pf["name"])
except Exception as e:  # noqa: BLE001
    portfolio, rejected, parse_error = pd.DataFrame(), pd.DataFrame(), e

if parse_error is not None or portfolio.empty:
    hero()
    st.error(f"Could not read {pf['name']}: {parse_error}" if parse_error is not None else
             "No valid rows found: no line has both an ISIN (or SCPI / euro fund / cash code) and a value.")
    st.stop()

# ── Scan whatever hasn't been resolved yet (portfolio, simulation, the position being tested) ──
ps = st.session_state.pending_sim
wanted = list(dict.fromkeys(list(portfolio["isin"]) + [s["isin"] for s in st.session_state.sims]
                            + ([ps["isin"]] if ps else [])))
etf_data = load_etf_snapshot()
# SCPI / euro funds / cash are classified from their code, ETFs from the snapshot: only the rest needs a scan.
pending = [i for i in wanted if i not in st.session_state.results and not special_kind(i) and i not in etf_data]
if pending:
    run_scan(pending, figi_key, pf["name"])
    time.sleep(0.6)
    st.rerun()

# A tested position joins the simulation only once its scan shows it is a stock.
if ps:
    rec = st.session_state.results.get(ps["isin"], {})
    st.session_state.pending_sim = None
    if special_kind(ps["isin"]) or ps["isin"] in etf_data or rec.get("status") in ("ok", "partial"):
        st.session_state.sims.append(ps)
        st.session_state.flash = (f"Added {position_name(ps['isin'])} · {ps['value']:,.0f} as a test position",
                                  ":material/science:")
    elif rec.get("status") == "not_stock":
        st.session_state.flash = (f"{ps['isin']} is an ETF or fund we have no data for yet, so it was not added.",
                                  ":material/block:")
    else:
        st.session_state.flash = (f"{ps['isin']} could not be resolved, so it was not added", ":material/error:")
    st.rerun()

# ── Build views ──────────────────────────────────────────────────────────────
base_pos = portfolio.assign(origin="Portfolio")
sim_pos = pd.DataFrame(st.session_state.sims or [], columns=["isin", "value"]).assign(origin="Simulated",
                                                                                      account="Simulation")
current, excluded = build_frame(base_pos)
has_sim = not sim_pos.empty
with_sim, excluded_sim = build_frame(pd.concat([base_pos, sim_pos], ignore_index=True)) if has_sim else (current, excluded)

if current.empty:
    hero()
    st.error("None of the positions could be analysed (no stock, and no ETF or fund we have data for).")
    st.stop()

df, excl = with_sim, excluded_sim
notes = []
if len(excl):
    share = excl["value"].sum() / (df["value"].sum() + excl["value"].sum())
    notes.append(f"{excl['isin'].nunique()} ETF/fund(s) excluded ({share:.1%} of total value) · not in our ETF data yet")
if len(rejected):
    notes.append(f"{len(rejected)} row(s) ignored (not an ISIN or no value)")

sim = None
if has_sim:
    sim = {
        # Indexed by position in st.session_state.sims, which the card's edit/remove messages refer to.
        "positions": [{"i": i, "name": position_name(s["isin"]),
                       "value": s["value"], "pct": 100 * s["value"] / with_sim["value"].sum()}
                      for i, s in enumerate(st.session_state.sims)],
        "cur_sector": weights_by(current, "sector"), "new_sector": weights_by(with_sim, "sector"),
        "cur_country": weights_by(current, "country"), "new_country": weights_by(with_sim, "country"),
    }

# ── The stage ────────────────────────────────────────────────────────────────
st.markdown(STAGE_CSS, unsafe_allow_html=True)
if st.session_state.flash:
    msg, icon = st.session_state.flash
    st.session_state.flash = None
    st.toast(msg, icon=icon)
with st.container(key="bridge_box"):  # off-screen input the cockpit frame writes messages into
    st.text_input("bridge", key="bridge", on_change=on_bridge, label_visibility="collapsed")
try:
    cockpit(df, sim, notes, excluded=excl)
except Exception as e:  # noqa: BLE001 — e.g. no internet for the first shape download
    hero()
    st.error(f"Globe unavailable: {e}")
