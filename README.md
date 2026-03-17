# 🌾 Ntracker — Commodity Price Dashboard

A decision-support tool for Australian growers, tracking **Brent Crude Oil**, **Wheat**, and **Urea** prices with forecasting and early warning signals.

**Live app →** [HuggingFace Space](https://huggingface.co/spaces/bree-csiro/Ntracker)

---

## What does it do?

| Tab | What it shows |
|-----|---------------|
| 📈 **Normalized Comparison** | All commodities on one chart (z-score scaled) so you can compare trends |
| 📊 **Individual Prices** | Each commodity's price history in USD or AUD |
| 🔗 **Wheat vs Urea** | Dual-axis chart comparing these two key grower commodities |
| 🔮 **Forecast & Signals** | Traffic-light alerts, lead-lag analysis, rolling correlations, and 6-month directional forecast |
| 📋 **Data Table** | Raw data with CSV download |

### Forecast & Signals — what's in there?

- **🚦 Traffic Lights** — Is the current price normal, elevated, or at a warning level? Helps decide *when* to buy inputs or sell grain.
- **⏱️ Lead-Lag Analysis** — Which prices move first? E.g. "oil leads urea by ~3 months" gives you an early warning window.
- **📉 Rolling Correlation** — Are the usual price relationships holding, or is something unusual happening?
- **🔮 Directional Forecast** — Where are prices likely heading over the next 6 months? (with confidence bands)

---

## Running locally

### 1. Install Python

You need Python 3.8 or later. Check with:
```bash
python --version
```

### 2. Clone the repo
```bash
git clone https://github.com/Bree-csiro/Ntracker.git
cd Ntracker
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Run the app
```bash
streamlit run app.py
```

A browser tab will open automatically at `http://localhost:8501`. That's it!

> **Note:** The first run may take 30–60 seconds as it fetches live commodity data from the APIs.

---

## Data sources

| Commodity | Source | Frequency | History |
|-----------|--------|-----------|---------|
| Brent Crude Oil | Yahoo Finance | Daily | ~2015–present |
| Wheat (CBOT) | Yahoo Finance | Daily | ~2015–present |
| Urea (granular) | World Bank Pink Sheet | Monthly | 1960–present |
| AUD/USD rate | Yahoo Finance | Daily | ~2015–present |

Data is fetched live from these free public APIs — no API keys needed.

---

## Project structure

```
Ntracker/
├── app.py              ← Main Streamlit dashboard (this is the app)
├── price_tracker.py    ← Data fetching + SQLite storage (for local caching)
├── requirements.txt    ← Python dependencies
├── Dockerfile          ← For HuggingFace Spaces deployment
├── .streamlit/
│   └── config.toml     ← CSIRO theme colours
└── README.md           ← You're reading this!
```

### Key files explained

- **`app.py`** — The entire dashboard. Everything runs from this one file. It fetches data, builds charts, and runs the forecast models.
- **`price_tracker.py`** — Optional helper that saves data to a local SQLite database (`prices.db`). If `prices.db` exists, the app reads from it (faster). If not, it fetches fresh data from the APIs.
- **`requirements.txt`** — Run `pip install -r requirements.txt` to install everything the app needs.

---

## Making changes

### Want to add a new commodity?

Edit `app.py` and add it to the `YAHOO_COMMODITIES` dictionary near the top:

```python
YAHOO_COMMODITIES = {
    "Brent Crude": {"ticker": "BZ=F", "unit": "USD/barrel"},
    "Wheat":       {"ticker": "ZW=F", "unit": "USc/bushel"},
    # Add new ones here, e.g.:
    "Natural Gas": {"ticker": "NG=F", "unit": "USD/MMBtu"},
    "Canola":      {"ticker": "RS=F", "unit": "CAD/mt"},
}
```

You can find ticker symbols on [Yahoo Finance](https://finance.yahoo.com/).

### Want to change the colour scheme?

The CSIRO colour palette is defined at the top of `app.py` — just edit the hex colour codes.

### Want to change the forecast horizon?

Search for `forecast_months = 6` in `app.py` and change the number.

---

## Deploying updates to HuggingFace

1. Go to your [HuggingFace Space](https://huggingface.co/spaces/bree-csiro/Ntracker)
2. Click the **Files** tab
3. Upload the updated `app.py` (and `requirements.txt` if it changed)
4. It will auto-rebuild in 2–5 minutes

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` |
| App is slow to start | First run fetches data from APIs — takes 30–60s. Subsequent runs use cached data. |
| Charts look wrong after date change | Clear cache: click the ☰ menu → "Clear cache" in Streamlit |
| Forecast shows a warning | Usually means not enough overlapping data — try widening the date range |

---

Built with 🔬 [CSIRO](https://www.csiro.au) · Data from World Bank & Yahoo Finance
