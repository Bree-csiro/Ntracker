"""
Ntracker — Commodity Price Dashboard
=====================================
Interactive Streamlit app for exploring commodity price data.

Runs locally (reads from prices.db) or on Streamlit Cloud (fetches live data).

    streamlit run app.py
"""

import re
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ---------------------------------------------------------------------------
# CSIRO Colour Palette
# ---------------------------------------------------------------------------
CSIRO_MIDDAY    = "#00A9CE"
CSIRO_MIDNIGHT  = "#00313C"
CSIRO_STEEL     = "#757579"
CSIRO_MIST      = "#DADBDC"
CSIRO_OCEAN     = "#004B87"
CSIRO_TEAL      = "#007377"
CSIRO_ORANGE    = "#E87722"
CSIRO_GOLD      = "#FFB81C"
CSIRO_LAVENDER  = "#9FAEE5"
CSIRO_TEALLIGHT = "#36CCD3"

COMMODITY_COLOURS = {
    "Brent Crude": CSIRO_MIDDAY,
    "Wheat":       CSIRO_ORANGE,
    "Urea":        CSIRO_TEAL,
}

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR / "prices.db"

# Yahoo Finance API
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

YAHOO_COMMODITIES = {
    "Brent Crude": {"ticker": "BZ=F", "unit": "USD/barrel"},
    "Wheat":       {"ticker": "ZW=F", "unit": "USc/bushel"},
}

WB_MONTHLY_URL = (
    "https://thedocs.worldbank.org/en/doc/"
    "74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/"
    "related/CMO-Historical-Data-Monthly.xlsx"
)

# Common Plotly layout settings
PLOTLY_LAYOUT = dict(
    font=dict(family="Segoe UI, system-ui, sans-serif", color=CSIRO_MIDNIGHT),
    paper_bgcolor="white",
    plot_bgcolor="white",
    hovermode="x unified",
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="right",
        x=1,
    ),
    margin=dict(l=60, r=30, t=50, b=40),
)


def styled_figure(**kwargs):
    """Create a Plotly figure with CSIRO styling."""
    fig = go.Figure()
    layout = {**PLOTLY_LAYOUT, **kwargs}
    fig.update_layout(**layout)
    fig.update_xaxes(
        gridcolor="#E8E8E8",
        linecolor=CSIRO_MIST,
        showgrid=True,
    )
    fig.update_yaxes(
        gridcolor="#E8E8E8",
        linecolor=CSIRO_MIST,
        showgrid=True,
    )
    return fig


# ---------------------------------------------------------------------------
# Data Fetchers (for cloud deployment — no local DB)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner="Fetching commodity data...")
def fetch_all_data():
    """Fetch all commodity data and exchange rates from APIs.
    Cached for 1 hour to avoid excessive API calls.
    """
    all_records = []

    # Yahoo Finance commodities
    for name, info in YAHOO_COMMODITIES.items():
        records = _fetch_yahoo_chart(
            info["ticker"], name, info["unit"], start_date="2000-01-01"
        )
        all_records.extend(records)
        time.sleep(0.5)

    # Urea from World Bank
    urea_records = _fetch_worldbank_urea()
    all_records.extend(urea_records)

    # Exchange rates
    rates = _fetch_aud_exchange_rate()

    # Build DataFrames
    prices_df = pd.DataFrame(all_records)
    if not prices_df.empty:
        prices_df["date"] = pd.to_datetime(prices_df["date"])

    rates_df = pd.DataFrame(rates)
    if not rates_df.empty:
        rates_df["date"] = pd.to_datetime(rates_df["date"])

    return prices_df, rates_df


def _fetch_yahoo_chart(ticker, name, unit, start_date="2015-01-01"):
    """Fetch daily prices from Yahoo Finance v8 chart API."""
    records = []
    try:
        start_ts = int(pd.Timestamp(start_date).timestamp())
        end_ts = int(time.time())
        params = {
            "period1": start_ts, "period2": end_ts,
            "interval": "1d", "includeAdjustedClose": "true",
        }
        resp = requests.get(
            YAHOO_CHART_URL.format(ticker=ticker),
            headers=YAHOO_HEADERS, params=params, timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return records
        r = result[0]
        timestamps = r.get("timestamp", [])
        closes = r.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        for ts, close in zip(timestamps, closes):
            if close is not None:
                dt = datetime.utcfromtimestamp(ts)
                records.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "commodity": name,
                    "price_usd": float(close),
                    "unit": unit,
                    "source": "YahooFinance",
                })
    except Exception:
        pass
    return records


def _fetch_worldbank_urea():
    """Fetch monthly urea prices from the World Bank Pink Sheet."""
    records = []
    tmp_path = Path(tempfile.gettempdir()) / "_ntracker_wb_cmo.xlsx"
    try:
        resp = requests.get(WB_MONTHLY_URL, timeout=120)
        resp.raise_for_status()
        with open(str(tmp_path), "wb") as f:
            f.write(resp.content)
        xls = pd.ExcelFile(str(tmp_path))
        yyyym_pattern = re.compile(r'^(\d{4})M(\d{2})$')
        for sheet_name in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet_name, header=None)
            urea_col = None
            header_row = None
            for idx in range(min(10, len(df))):
                row = df.iloc[idx]
                for col_idx, val in enumerate(row.values):
                    if pd.notna(val) and "urea" in str(val).lower():
                        urea_col = col_idx
                        header_row = idx
                        break
                if urea_col is not None:
                    break
            if urea_col is not None and header_row is not None:
                data_df = df.iloc[header_row + 1:]
                for _, data_row in data_df.iterrows():
                    date_val = data_row.iloc[0]
                    price_val = data_row.iloc[urea_col]
                    if pd.notna(date_val) and pd.notna(price_val):
                        try:
                            price = float(price_val)
                        except (ValueError, TypeError):
                            continue
                        try:
                            date_str = str(date_val).strip()
                            m = yyyym_pattern.match(date_str)
                            if m:
                                year, month = m.groups()
                                parsed = pd.Timestamp(
                                    year=int(year), month=int(month), day=1
                                )
                            elif isinstance(date_val, str):
                                parsed = pd.to_datetime(date_val)
                            else:
                                parsed = pd.Timestamp(date_val)
                            records.append({
                                "date": parsed.strftime("%Y-%m-%d"),
                                "commodity": "Urea",
                                "price_usd": price,
                                "unit": "USD/mt",
                                "source": "WorldBank_PinkSheet",
                            })
                        except (ValueError, TypeError):
                            continue
                break
    except Exception:
        pass
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
    return records


def _fetch_aud_exchange_rate(start_date="2015-01-01"):
    """Fetch AUD/USD exchange rate from Yahoo Finance."""
    rates = []
    try:
        start_ts = int(pd.Timestamp(start_date).timestamp())
        end_ts = int(time.time())
        params = {
            "period1": start_ts, "period2": end_ts,
            "interval": "1d", "includeAdjustedClose": "true",
        }
        resp = requests.get(
            YAHOO_CHART_URL.format(ticker="AUDUSD=X"),
            headers=YAHOO_HEADERS, params=params, timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return rates
        r = result[0]
        timestamps = r.get("timestamp", [])
        closes = r.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        for ts, close in zip(timestamps, closes):
            if close is not None:
                dt = datetime.utcfromtimestamp(ts)
                rates.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "aud_usd": 1.0 / float(close),
                })
    except Exception:
        pass
    return rates


# ---------------------------------------------------------------------------
# Data loading — local DB or live fetch
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300)
def load_from_db():
    """Try to load from local SQLite database."""
    if not DB_PATH.exists():
        return pd.DataFrame(), pd.DataFrame()
    conn = sqlite3.connect(str(DB_PATH))
    prices = pd.read_sql_query(
        "SELECT date, commodity, price_usd, unit, source FROM prices ORDER BY date",
        conn,
    )
    rates = pd.read_sql_query(
        "SELECT date, aud_usd FROM exchange_rates ORDER BY date", conn
    )
    conn.close()
    if not prices.empty:
        prices["date"] = pd.to_datetime(prices["date"])
    if not rates.empty:
        rates["date"] = pd.to_datetime(rates["date"])
    return prices, rates


def get_data():
    """Load data from local DB if available, otherwise fetch from APIs."""
    prices_df, rates_df = load_from_db()
    if prices_df.empty:
        prices_df, rates_df = fetch_all_data()
    return prices_df, rates_df


def add_aud_prices(prices_df, rates_df):
    """Add AUD price column."""
    if prices_df.empty or rates_df.empty:
        prices_df["price_aud"] = prices_df["price_usd"] * 1.55
        return prices_df
    merged = pd.merge_asof(
        prices_df.sort_values("date"),
        rates_df[["date", "aud_usd"]].sort_values("date"),
        on="date",
        direction="nearest",
    )
    merged["price_aud"] = merged["price_usd"] * merged["aud_usd"]
    return merged


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Ntracker — Commodity Price Dashboard",
    page_icon="🌾",
    layout="wide",
)

# Custom CSS for CSIRO styling
st.markdown("""
<style>
    .stApp {
        font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    }
    .metric-card {
        background: linear-gradient(135deg, """ + CSIRO_MIDNIGHT + """ 0%, """ + CSIRO_OCEAN + """ 100%);
        padding: 1.2rem;
        border-radius: 12px;
        color: white;
        margin-bottom: 0.5rem;
    }
    .metric-card h3 {
        margin: 0;
        font-size: 0.85rem;
        color: """ + CSIRO_TEALLIGHT + """;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .metric-card .value {
        font-size: 1.6rem;
        font-weight: 700;
        margin: 0.3rem 0;
    }
    .metric-card .sub {
        font-size: 0.8rem;
        color: """ + CSIRO_MIST + """;
    }
    h1 { color: """ + CSIRO_MIDNIGHT + """ !important; }
    .stTabs [data-baseweb="tab"] {
        color: """ + CSIRO_MIDNIGHT + """;
    }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
st.title("🌾 Ntracker — Commodity Price Dashboard")
st.caption(
    "Brent Crude · Wheat · Urea — with AUD conversion  ·  "
    "Last updated: {}".format(datetime.now().strftime("%d %b %Y"))
)

# Load data
prices_df, rates_df = get_data()

if prices_df.empty:
    st.error(
        "⚠️ Could not load data. Check your internet connection and try refreshing."
    )
    st.stop()

prices_df = add_aud_prices(prices_df, rates_df)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.header("⚙️ Settings")

commodities = sorted(prices_df["commodity"].unique())
selected = st.sidebar.multiselect(
    "Commodities",
    options=commodities,
    default=commodities,
)

min_date = prices_df["date"].min().date()
max_date = prices_df["date"].max().date()

# Smart default: start at 2015 (all commodities overlap) but allow full range
default_start = max(min_date, pd.Timestamp("2015-01-01").date())

date_range = st.sidebar.date_input(
    "Date range",
    value=(default_start, max_date),
    min_value=min_date,
    max_value=max_date,
    help="Urea data available from 1960. Brent & Wheat from ~2015.",
)

currency = st.sidebar.radio("Currency", ["USD", "AUD", "Both"], index=0)

st.sidebar.markdown("---")
st.sidebar.markdown(
    "**Data sources**\n"
    "- Brent Crude: Yahoo Finance\n"
    "- Wheat: Yahoo Finance\n"
    "- Urea: World Bank Pink Sheet\n"
    "- AUD/USD: Yahoo Finance"
)
st.sidebar.markdown(
    "---\n"
    "Built with 🔬 [CSIRO](https://www.csiro.au)"
)

# Filter data
if len(date_range) == 2:
    start, end = date_range
    mask = (
        prices_df["commodity"].isin(selected) &
        (prices_df["date"] >= pd.Timestamp(start)) &
        (prices_df["date"] <= pd.Timestamp(end))
    )
else:
    mask = prices_df["commodity"].isin(selected)

filtered = prices_df[mask].copy()

if filtered.empty:
    st.warning("No data for the selected filters.")
    st.stop()


# ---------------------------------------------------------------------------
# Metrics row
# ---------------------------------------------------------------------------
cols = st.columns(len(selected))
for col, commodity in zip(cols, selected):
    subset = filtered[filtered["commodity"] == commodity].sort_values("date")
    if subset.empty:
        continue
    latest = subset.iloc[-1]
    prev = subset.iloc[-2] if len(subset) > 1 else latest

    price_col = "price_aud" if currency == "AUD" else "price_usd"
    curr_sym = "AUD" if currency == "AUD" else "USD"

    current_price = latest[price_col]
    prev_price = prev[price_col]
    change_pct = ((current_price - prev_price) / prev_price * 100) if prev_price else 0

    with col:
        st.markdown("""
        <div class="metric-card">
            <h3>{commodity}</h3>
            <div class="value">${price:,.0f} <span style="font-size:0.7em">{curr}</span></div>
            <div class="sub">{change:+.1f}% · {date}</div>
        </div>
        """.format(
            commodity=commodity,
            price=current_price,
            curr=curr_sym,
            change=change_pct,
            date=latest["date"].strftime("%b %Y"),
        ), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Charts — all Plotly for smooth interactive rendering
# ---------------------------------------------------------------------------
tab1, tab2, tab3, tab4 = st.tabs([
    "📈 Normalized Comparison",
    "📊 Individual Prices",
    "🔗 Wheat vs Urea",
    "📋 Data Table",
])


with tab1:
    st.subheader("Normalized Price Comparison (Z-Score)")

    fig = styled_figure(
        title="Normalized Price Comparison",
        yaxis_title="Normalized Price (z-score)",
        height=500,
    )

    for commodity in sorted(selected):
        subset = filtered[filtered["commodity"] == commodity].copy()
        subset = subset.sort_values("date").drop_duplicates("date", keep="last")
        if len(subset) < 2:
            continue
        mean_p = subset["price_usd"].mean()
        std_p = subset["price_usd"].std()
        if std_p == 0:
            continue
        subset["norm"] = (subset["price_usd"] - mean_p) / std_p
        colour = COMMODITY_COLOURS.get(commodity, CSIRO_STEEL)
        fig.add_trace(go.Scatter(
            x=subset["date"],
            y=subset["norm"],
            name=commodity,
            line=dict(color=colour, width=1.5),
            hovertemplate="%{x|%b %Y}: %{y:.2f}<extra>" + commodity + "</extra>",
        ))

    fig.add_hline(y=0, line_dash="dot", line_color=CSIRO_STEEL, line_width=0.8)
    st.plotly_chart(fig, use_container_width=True)


with tab2:
    st.subheader("Individual Commodity Prices")

    for commodity in sorted(selected):
        subset = filtered[filtered["commodity"] == commodity].copy()
        subset = subset.sort_values("date").drop_duplicates("date", keep="last")
        if subset.empty:
            continue

        colour = COMMODITY_COLOURS.get(commodity, CSIRO_STEEL)
        fig = styled_figure(
            title=commodity,
            yaxis_title="Price",
            height=350,
        )

        if currency in ("USD", "Both"):
            fig.add_trace(go.Scatter(
                x=subset["date"],
                y=subset["price_usd"],
                name="{} (USD)".format(commodity),
                line=dict(color=colour, width=1.5),
                hovertemplate="$%{y:,.0f} USD<extra></extra>",
            ))
        if currency in ("AUD", "Both") and "price_aud" in subset.columns:
            fig.add_trace(go.Scatter(
                x=subset["date"],
                y=subset["price_aud"],
                name="{} (AUD)".format(commodity),
                line=dict(color=colour, width=1.5, dash="dash"),
                opacity=0.7,
                hovertemplate="$%{y:,.0f} AUD<extra></extra>",
            ))

        st.plotly_chart(fig, use_container_width=True)


with tab3:
    st.subheader("Wheat vs Urea — Dual Axis")

    wheat = filtered[filtered["commodity"] == "Wheat"].sort_values("date")
    urea = filtered[filtered["commodity"] == "Urea"].sort_values("date")

    if wheat.empty or urea.empty:
        st.info("Select both Wheat and Urea to see this chart.")
    else:
        fig = make_subplots(specs=[[{"secondary_y": True}]])

        fig.add_trace(
            go.Scatter(
                x=wheat["date"],
                y=wheat["price_usd"],
                name="Wheat",
                line=dict(color=CSIRO_ORANGE, width=1.5),
                hovertemplate="$%{y:,.0f}<extra>Wheat</extra>",
            ),
            secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(
                x=urea["date"],
                y=urea["price_usd"],
                name="Urea",
                line=dict(color=CSIRO_TEAL, width=1.5),
                hovertemplate="$%{y:,.0f}<extra>Urea</extra>",
            ),
            secondary_y=True,
        )

        fig.update_layout(
            **PLOTLY_LAYOUT,
            title="Wheat and Urea Price Comparison",
            height=450,
        )
        fig.update_xaxes(gridcolor="#E8E8E8", linecolor=CSIRO_MIST)
        fig.update_yaxes(
            title_text="Wheat Price (USD)",
            title_font=dict(color=CSIRO_ORANGE),
            tickfont=dict(color=CSIRO_ORANGE),
            gridcolor="#E8E8E8",
            secondary_y=False,
        )
        fig.update_yaxes(
            title_text="Urea Price (USD)",
            title_font=dict(color=CSIRO_TEAL),
            tickfont=dict(color=CSIRO_TEAL),
            gridcolor="#E8E8E8",
            secondary_y=True,
        )

        st.plotly_chart(fig, use_container_width=True)


with tab4:
    st.subheader("Raw Data")

    display_df = filtered[["date", "commodity", "price_usd", "price_aud", "unit", "source"]].copy()
    display_df["date"] = display_df["date"].dt.strftime("%Y-%m-%d")
    display_df = display_df.rename(columns={
        "date": "Date",
        "commodity": "Commodity",
        "price_usd": "USD",
        "price_aud": "AUD",
        "unit": "Unit",
        "source": "Source",
    })

    st.dataframe(display_df, use_container_width=True, height=500)

    csv = display_df.to_csv(index=False)
    st.download_button(
        "📥 Download CSV",
        csv,
        "commodity_prices.csv",
        "text/csv",
    )


# ---------------------------------------------------------------------------
# Correlation preview
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("🔗 Correlation Preview")
st.caption("Monthly correlation between commodities — foundation for multifactor analysis")

pivot = filtered.pivot_table(
    index="date", columns="commodity", values="price_usd", aggfunc="last"
).resample("M").last().dropna(how="all")

if len(pivot.columns) >= 2 and len(pivot) >= 3:
    corr = pivot.corr()

    fig = go.Figure(data=go.Heatmap(
        z=corr.values,
        x=corr.columns.tolist(),
        y=corr.columns.tolist(),
        colorscale="RdYlGn",
        zmin=-1, zmax=1,
        text=[["{:.2f}".format(v) for v in row] for row in corr.values],
        texttemplate="%{text}",
        textfont=dict(size=14),
        hovertemplate="%{x} vs %{y}: %{z:.2f}<extra></extra>",
    ))
    fig.update_layout(
        **PLOTLY_LAYOUT,
        title="Price Correlation (Monthly)",
        height=400,
        width=500,
    )
    st.plotly_chart(fig, use_container_width=False)
else:
    st.info("Select at least 2 commodities with overlapping data to see correlations.")


# Footer
st.markdown("---")
st.caption(
    "Data: World Bank · Yahoo Finance  ·  "
    "Built with CSIRO 🔬  ·  "
    "Prices in nominal terms"
)
