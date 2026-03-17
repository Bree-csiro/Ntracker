# 🌾 Ntracker — Commodity Price Tracker

Fetches, stores, and visualises global commodity prices relevant to Australian agriculture, with CSIRO branding.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Fetch data and generate charts
python price_tracker.py

# Launch interactive dashboard
streamlit run app.py
```

## Commands

| Command | Description |
|---------|-------------|
| `python price_tracker.py` | Fetch data + store + generate chart PNGs |
| `python price_tracker.py --plot-only` | Regenerate charts from stored data |
| `python price_tracker.py --export-csv` | Also export `commodity_prices.csv` |
| `streamlit run app.py` | Launch interactive web dashboard |

## Data Sources

| Commodity | Source | Frequency |
|-----------|--------|-----------|
| Brent Crude | Yahoo Finance (`BZ=F`) | Daily |
| Wheat | Yahoo Finance (`ZW=F`) | Daily |
| Urea | World Bank Pink Sheet | Monthly |
| AUD/USD | Yahoo Finance (`AUDUSD=X`) | Daily |

## Output Files

- **`prices.db`** — SQLite database with all historical data
- **`commodity_prices.csv`** — Combined export for analysis
- **`figures/`** — Chart PNGs with CSIRO colour palette
- **`app.py`** — Interactive Streamlit dashboard

## For Collaborators

**Prerequisites:** Python 3.8+ with pip

```bash
# 1. Navigate to the Ntracker folder
cd Ntracker

# 2. Install dependencies
pip install pandas matplotlib openpyxl requests streamlit

# 3. Fetch the data (first time only, ~2 min)
python price_tracker.py

# 4. Launch the dashboard
streamlit run app.py
```

The dashboard will open in your browser at `http://localhost:8501`.

## Dependencies

```
pandas, matplotlib, openpyxl, requests, streamlit
```
