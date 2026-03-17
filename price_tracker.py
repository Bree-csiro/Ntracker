#!/usr/bin/env python3
"""
Commodity Price Tracker — Ntracker
===================================
Fetches global commodity prices (Brent Crude, Wheat, Urea), stores them in a
local SQLite database with AUD conversion, and generates comparison charts.

Designed as a foundation for multifactor analysis of agricultural input costs.

Usage:
    python price_tracker.py                # Full run: fetch all + store + plot
    python price_tracker.py --plot-only    # Plot from stored data (no fetching)
    python price_tracker.py --update-only  # Fetch and store only (no plots)

Data Sources:
    - Brent Crude Oil:  Yahoo Finance chart API (BZ=F, daily)
    - Wheat:            Yahoo Finance chart API (ZW=F, daily CBOT futures)
    - Urea:             World Bank CMO Pink Sheet (monthly, USD/mt)
    - AUD/USD:          Yahoo Finance chart API (AUDUSD=X)
"""

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR / "prices.db"
FIGURES_DIR = SCRIPT_DIR / "figures"

# Commodities to track via Yahoo Finance
YAHOO_COMMODITIES = {
    "Brent Crude": {"ticker": "BZ=F", "unit": "USD/barrel"},
    "Wheat":       {"ticker": "ZW=F", "unit": "USc/bushel"},
}

AUD_TICKER = "AUDUSD=X"

# Yahoo Finance v8 chart API
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# World Bank CMO Historical Data Monthly (Pink Sheet)
WB_MONTHLY_URL = (
    "https://thedocs.worldbank.org/en/doc/"
    "74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/"
    "related/CMO-Historical-Data-Monthly.xlsx"
)

# Default history start
DEFAULT_START = "2015-01-01"

# Chart style
plt.rcParams.update({
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.grid": True,
    "grid.alpha": 0.3,
    "font.size": 11,
    "axes.titlesize": 14,
    "axes.labelsize": 12,
})

# ---- CSIRO Colour Palette ----
CSIRO_MIDDAY    = "#00A9CE"  # CSIRO Blue Midday
CSIRO_MIDNIGHT  = "#00313C"  # CSIRO Blue Midnight
CSIRO_STEEL     = "#757579"  # CSIRO Steel
CSIRO_MIST      = "#DADBDC"  # CSIRO Mist
CSIRO_BLUEBERRY = "#1E22AA"  # CSIRO Blueberry
CSIRO_OCEAN     = "#004B87"  # CSIRO Ocean Blue
CSIRO_PLUM      = "#6D2077"  # CSIRO Plum
CSIRO_TEAL      = "#007377"  # CSIRO Teal
CSIRO_MINT      = "#007A53"  # CSIRO Mint
CSIRO_FUSCHIA   = "#DF1995"  # CSIRO Fuschia
CSIRO_ORANGE    = "#E87722"  # CSIRO Orange
CSIRO_GOLD      = "#FFB81C"  # CSIRO Gold
CSIRO_LAVENDER  = "#9FAEE5"  # CSIRO Lavender
CSIRO_MINTLIGHT = "#71CC98"  # CSIRO Mint Light
CSIRO_FOREST    = "#78BE20"  # CSIRO Forest
CSIRO_TEALLIGHT = "#36CCD3"  # CSIRO Teal Light

COMMODITY_COLOURS = {
    "Brent Crude": CSIRO_MIDDAY,   # blue
    "Wheat":       CSIRO_ORANGE,   # orange
    "Urea":        CSIRO_TEAL,     # teal green
}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def init_db(db_path=DB_PATH):
    """Create SQLite database and tables."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            date       TEXT NOT NULL,
            commodity  TEXT NOT NULL,
            price_usd  REAL,
            unit       TEXT,
            source     TEXT,
            fetched_at TEXT,
            PRIMARY KEY (date, commodity)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS exchange_rates (
            date       TEXT PRIMARY KEY,
            aud_usd    REAL,
            fetched_at TEXT
        )
    """)
    conn.commit()
    return conn


def upsert_prices(conn, records):
    """Insert or replace price records into the database."""
    now = datetime.utcnow().isoformat()
    count = 0
    for r in records:
        conn.execute(
            """INSERT OR REPLACE INTO prices
               (date, commodity, price_usd, unit, source, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (r["date"], r["commodity"], r["price_usd"],
             r.get("unit", ""), r.get("source", ""), now),
        )
        count += 1
    conn.commit()
    return count


def upsert_exchange_rates(conn, rates_df):
    """Store exchange rate records."""
    now = datetime.utcnow().isoformat()
    count = 0
    for _, row in rates_df.iterrows():
        date_str = row["date"]
        if hasattr(date_str, "strftime"):
            date_str = date_str.strftime("%Y-%m-%d")
        conn.execute(
            """INSERT OR REPLACE INTO exchange_rates (date, aud_usd, fetched_at)
               VALUES (?, ?, ?)""",
            (date_str, float(row["aud_usd"]), now),
        )
        count += 1
    conn.commit()
    return count


def load_prices(conn):
    """Load all stored prices."""
    df = pd.read_sql_query(
        "SELECT date, commodity, price_usd, unit, source FROM prices ORDER BY date",
        conn,
    )
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def load_exchange_rates(conn):
    """Load stored exchange rates."""
    df = pd.read_sql_query(
        "SELECT date, aud_usd FROM exchange_rates ORDER BY date", conn
    )
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def get_last_date(conn, commodity):
    """Get the most recent date stored for a commodity."""
    cur = conn.execute(
        "SELECT MAX(date) FROM prices WHERE commodity = ?", (commodity,)
    )
    result = cur.fetchone()[0]
    return result


# ---------------------------------------------------------------------------
# Data Fetchers
# ---------------------------------------------------------------------------
def fetch_yahoo_chart(ticker, name, unit, start_date=DEFAULT_START):
    """Fetch daily prices from Yahoo Finance v8 chart API."""
    print("  📡 Fetching {} ({}) from Yahoo Finance...".format(name, ticker))
    records = []

    try:
        # Convert start_date to Unix timestamp
        start_ts = int(pd.Timestamp(start_date).timestamp())
        end_ts = int(time.time())

        params = {
            "period1": start_ts,
            "period2": end_ts,
            "interval": "1d",
            "includeAdjustedClose": "true",
        }

        resp = requests.get(
            YAHOO_CHART_URL.format(ticker=ticker),
            headers=YAHOO_HEADERS,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        chart = data.get("chart", {})
        result = chart.get("result", [])
        if not result:
            error = chart.get("error", {})
            print("     ⚠ No data: {}".format(error.get("description", "unknown")))
            return records

        r = result[0]
        timestamps = r.get("timestamp", [])
        quotes = r.get("indicators", {}).get("quote", [{}])[0]
        closes = quotes.get("close", [])

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

        print("     ✓ {} records for {}".format(len(records), name))

    except requests.RequestException as e:
        print("     ⚠ Yahoo Finance error for {}: {}".format(name, e))
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        print("     ⚠ Parse error for {}: {}".format(name, e))

    return records


def fetch_worldbank_urea():
    """Fetch monthly urea prices from the World Bank Pink Sheet Excel."""
    print("  📡 Fetching Urea from World Bank Pink Sheet...")
    records = []

    import tempfile
    tmp_dir = Path(tempfile.gettempdir())
    tmp_path = tmp_dir / "_ntracker_wb_cmo.xlsx"

    try:
        resp = requests.get(WB_MONTHLY_URL, timeout=120)
        resp.raise_for_status()
        with open(str(tmp_path), "wb") as f:
            f.write(resp.content)
        print("     ✓ Downloaded Pink Sheet ({:.1f} MB)".format(
            len(resp.content) / 1024 / 1024))

        xls = pd.ExcelFile(str(tmp_path))

        # Search for the sheet and column containing urea data
        for sheet_name in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet_name, header=None)

            # Find the header row containing "urea"
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
                import re
                yyyym_pattern = re.compile(r'^(\d{4})M(\d{2})$')
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
                            # Handle World Bank format: YYYYMNN (e.g. '1960M01')
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
                break  # stop after first sheet with urea

        print("     ✓ {} monthly records for Urea".format(len(records)))

    except Exception as e:
        print("     ⚠ Error fetching World Bank data: {}".format(e))

    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass  # ignore if OneDrive or another process is holding the file

    return records


def fetch_aud_exchange_rate(start_date=DEFAULT_START):
    """Fetch AUD/USD exchange rate from Yahoo Finance."""
    print("  📡 Fetching AUD/USD exchange rate...")
    try:
        start_ts = int(pd.Timestamp(start_date).timestamp())
        end_ts = int(time.time())

        params = {
            "period1": start_ts,
            "period2": end_ts,
            "interval": "1d",
            "includeAdjustedClose": "true",
        }

        resp = requests.get(
            YAHOO_CHART_URL.format(ticker=AUD_TICKER),
            headers=YAHOO_HEADERS,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        chart = data.get("chart", {})
        result = chart.get("result", [])
        if not result:
            print("     ⚠ No exchange rate data returned")
            return pd.DataFrame()

        r = result[0]
        timestamps = r.get("timestamp", [])
        quotes = r.get("indicators", {}).get("quote", [{}])[0]
        closes = quotes.get("close", [])

        rates = []
        for ts, close in zip(timestamps, closes):
            if close is not None:
                dt = datetime.utcfromtimestamp(ts)
                # AUDUSD=X gives how many USD per 1 AUD
                # We want: 1 USD = ? AUD, so invert
                rates.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "aud_usd": 1.0 / float(close),
                })

        rates_df = pd.DataFrame(rates)
        print("     ✓ {} exchange rate records".format(len(rates_df)))
        return rates_df

    except Exception as e:
        print("     ⚠ Error fetching exchange rate: {}".format(e))
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Master Update
# ---------------------------------------------------------------------------
def update_all(conn):
    """Fetch all commodity data and exchange rates, store in database."""
    print("\n🔄 Updating all commodity data...\n")

    all_records = []

    # Fetch from Yahoo Finance
    for name, info in YAHOO_COMMODITIES.items():
        last = get_last_date(conn, name)
        start = last if last else DEFAULT_START
        records = fetch_yahoo_chart(
            info["ticker"], name, info["unit"], start_date=start
        )
        all_records.extend(records)
        time.sleep(1)  # be polite to Yahoo

    # Urea from World Bank (always re-fetch since it's monthly)
    urea_records = fetch_worldbank_urea()
    all_records.extend(urea_records)

    # Store prices
    if all_records:
        count = upsert_prices(conn, all_records)
        print("\n  💾 Stored {} total price records".format(count))
    else:
        print("\n  ⚠ No new data fetched")

    # Exchange rates
    last_rate = conn.execute("SELECT MAX(date) FROM exchange_rates").fetchone()[0]
    rate_start = last_rate if last_rate else DEFAULT_START
    rates_df = fetch_aud_exchange_rate(start_date=rate_start)
    if not rates_df.empty:
        rate_count = upsert_exchange_rates(conn, rates_df)
        print("  💾 Stored {} exchange rate records".format(rate_count))


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def add_aud_prices(prices_df, rates_df):
    """Add AUD-converted price column using nearest exchange rate."""
    if prices_df.empty or rates_df.empty:
        prices_df["price_aud"] = prices_df["price_usd"] * 1.55  # fallback
        return prices_df

    rates_sorted = rates_df.sort_values("date").reset_index(drop=True)
    prices_sorted = prices_df.sort_values("date").reset_index(drop=True)

    merged = pd.merge_asof(
        prices_sorted,
        rates_sorted[["date", "aud_usd"]],
        on="date",
        direction="nearest",
    )
    merged["price_aud"] = merged["price_usd"] * merged["aud_usd"]
    return merged


def plot_normalized_comparison(df, output_path, start_date="2021-01-01"):
    """
    Plot z-score normalized price comparison across commodities.
    Replicates the user's reference image style.
    """
    print("\n📊 Generating normalized comparison chart...")

    fig, ax = plt.subplots(figsize=(10, 7))

    commodities = sorted(df["commodity"].unique())
    filtered = df[df["date"] >= pd.Timestamp(start_date)].copy()

    for commodity in commodities:
        subset = filtered[filtered["commodity"] == commodity].copy()
        subset = subset.sort_values("date").drop_duplicates(subset="date", keep="last")

        if len(subset) < 2:
            continue

        # Z-score normalization: (x - mean) / std
        mean_price = subset["price_usd"].mean()
        std_price = subset["price_usd"].std()
        if std_price == 0:
            continue
        subset["normalized"] = (subset["price_usd"] - mean_price) / std_price

        colour = COMMODITY_COLOURS.get(commodity, "#333333")
        ax.plot(
            subset["date"], subset["normalized"],
            label=commodity, color=colour, linewidth=1.5, alpha=0.9
        )

    ax.set_xlabel("Date")
    ax.set_ylabel("Normalized Price")
    ax.set_title("Normalized Price Comparison")
    ax.legend(loc="upper left", framealpha=0.9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.axhline(y=0, color="gray", linewidth=0.5, linestyle="-")

    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("     ✓ Saved: {}".format(output_path))


def plot_dual_axis_comparison(
    df, rates_df, output_path,
    commodity_a="Wheat", commodity_b="Urea",
    start_date="2021-01-01",
):
    """Dual-axis chart comparing two commodities."""
    print("\n📊 Generating {} vs {} chart...".format(commodity_a, commodity_b))

    filtered = df[df["date"] >= pd.Timestamp(start_date)].copy()
    a_data = filtered[filtered["commodity"] == commodity_a].sort_values("date")
    b_data = filtered[filtered["commodity"] == commodity_b].sort_values("date")

    if a_data.empty or b_data.empty:
        print("     ⚠ Insufficient data for {} vs {}".format(commodity_a, commodity_b))
        return

    fig, ax1 = plt.subplots(figsize=(12, 6))

    colour_a = COMMODITY_COLOURS.get(commodity_a, "blue")
    colour_b = COMMODITY_COLOURS.get(commodity_b, "red")

    ax1.plot(a_data["date"], a_data["price_usd"],
             color=colour_a, linewidth=1.5, label=commodity_a)
    ax1.set_xlabel("Date")
    ax1.set_ylabel("{} Price".format(commodity_a), color=colour_a)
    ax1.tick_params(axis="y", labelcolor=colour_a)

    ax2 = ax1.twinx()
    ax2.plot(b_data["date"], b_data["price_usd"],
             color=colour_b, linewidth=1.5, label=commodity_b)
    ax2.set_ylabel("{} Price".format(commodity_b), color=colour_b)
    ax2.tick_params(axis="y", labelcolor=colour_b)

    ax1.set_title("{} and {} Price Comparison".format(commodity_a, commodity_b))

    # Combined legend
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right")

    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax1.xaxis.set_major_locator(mdates.YearLocator())

    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("     ✓ Saved: {}".format(output_path))


def plot_individual_aud(df, output_path, start_date="2021-01-01"):
    """Plot all commodities with AUD prices on a multi-panel figure."""
    print("\n📊 Generating individual commodity charts (AUD)...")

    filtered = df[df["date"] >= pd.Timestamp(start_date)].copy()
    commodities = sorted(filtered["commodity"].unique())
    n = len(commodities)

    if n == 0:
        print("     ⚠ No data to plot")
        return

    fig, axes = plt.subplots(n, 1, figsize=(12, 4 * n), sharex=True)
    if n == 1:
        axes = [axes]

    for ax, commodity in zip(axes, commodities):
        subset = filtered[filtered["commodity"] == commodity].sort_values("date")
        subset = subset.drop_duplicates(subset="date", keep="last")

        colour = COMMODITY_COLOURS.get(commodity, "#333333")

        ax.plot(subset["date"], subset["price_usd"],
                color=colour, linewidth=1.2, label="{} (USD)".format(commodity))

        if "price_aud" in subset.columns and subset["price_aud"].notna().any():
            ax.plot(subset["date"], subset["price_aud"],
                    color=colour, linewidth=1.2, linestyle="--",
                    alpha=0.6, label="{} (AUD)".format(commodity))

        ax.set_ylabel("Price")
        ax.set_title(commodity)
        ax.legend(loc="upper left", fontsize=9)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax.xaxis.set_major_locator(mdates.YearLocator())

    axes[-1].set_xlabel("Date")
    fig.suptitle("Commodity Prices — USD and AUD", fontsize=16, y=1.01)
    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("     ✓ Saved: {}".format(output_path))


def print_summary(df):
    """Print a summary of stored data to console."""
    print("\n" + "=" * 60)
    print("📋 Data Summary")
    print("=" * 60)

    for commodity in sorted(df["commodity"].unique()):
        subset = df[df["commodity"] == commodity]
        latest = subset.iloc[-1]
        first = subset.iloc[0]
        print("\n  {}:".format(commodity))
        print("    Records:  {:,}".format(len(subset)))
        print("    Range:    {} → {}".format(
            first["date"].strftime("%Y-%m-%d"),
            latest["date"].strftime("%Y-%m-%d")))
        print("    Latest:   ${:,.2f} USD ({})".format(
            latest["price_usd"],
            latest["date"].strftime("%b %Y")))
        if "price_aud" in latest.index and pd.notna(latest.get("price_aud")):
            print("              ${:,.2f} AUD".format(latest["price_aud"]))
        print("    Min/Max:  ${:,.2f} / ${:,.2f} USD".format(
            subset["price_usd"].min(),
            subset["price_usd"].max()))

    print("\n" + "=" * 60)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def export_csv(df, output_path):
    """Export the combined dataset as CSV for downstream analysis."""
    print("\n💾 Exporting CSV: {}".format(output_path))
    df.to_csv(str(output_path), index=False)
    print("     ✓ {} rows exported".format(len(df)))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="🌾 Commodity Price Tracker — Ntracker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python price_tracker.py              # Full update + charts\n"
            "  python price_tracker.py --plot-only   # Charts from stored data\n"
            "  python price_tracker.py --update-only # Fetch + store only\n"
        ),
    )
    parser.add_argument(
        "--plot-only", action="store_true",
        help="Generate charts from stored data only (no fetching)",
    )
    parser.add_argument(
        "--update-only", action="store_true",
        help="Fetch and store data only (no charts)",
    )
    parser.add_argument(
        "--start", type=str, default="2021-01-01",
        help="Chart start date (default: 2021-01-01)",
    )
    parser.add_argument(
        "--export-csv", action="store_true",
        help="Export combined dataset as CSV",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("🌾  Commodity Price Tracker — Ntracker")
    print("    {}".format(datetime.now().strftime("%d %B %Y %H:%M")))
    print("=" * 60)

    # Init
    conn = init_db()
    FIGURES_DIR.mkdir(exist_ok=True)
    print("\n💾 Database: {}".format(DB_PATH))
    print("📁 Figures:  {}".format(FIGURES_DIR))

    # Fetch + store
    if not args.plot_only:
        update_all(conn)

    # Load data
    prices_df = load_prices(conn)
    rates_df = load_exchange_rates(conn)

    if prices_df.empty:
        print("\n⚠ No price data in database. Run without --plot-only first.")
        conn.close()
        return

    # Add AUD prices
    prices_df = add_aud_prices(prices_df, rates_df)

    # Summary
    print_summary(prices_df)

    # Export CSV
    if args.export_csv:
        export_csv(prices_df, SCRIPT_DIR / "commodity_prices.csv")

    # Charts
    if not args.update_only:
        plot_normalized_comparison(
            prices_df,
            FIGURES_DIR / "Price_Update_normalized_comparison.png",
            start_date=args.start,
        )
        plot_dual_axis_comparison(
            prices_df, rates_df,
            FIGURES_DIR / "Price_Update_wheat_urea.png",
            commodity_a="Wheat", commodity_b="Urea",
            start_date=args.start,
        )
        plot_individual_aud(
            prices_df,
            FIGURES_DIR / "Price_Update_individual_aud.png",
            start_date=args.start,
        )

    conn.close()
    print("\n✅ Done! Charts saved to {}".format(FIGURES_DIR))


if __name__ == "__main__":
    main()
