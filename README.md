# PortfolioScan

See where your money lives. Upload an Excel file of ISINs and position values. Every line is resolved across
FinanceDatabase, yfinance and OpenFIGI, then mapped by country and sector on an interactive 3D globe.

Stocks only for now; ETF support is coming soon.

Made by [Gabin Lê](https://www.linkedin.com/in/gabin-le/) · 2026

## Features

- Live scan with a source-by-source log (FinanceDatabase → yfinance → OpenFIGI)
- Spinning 3D globe of country exposure, with sector, country and region breakdowns
- Hover any country, sector or region to list the positions in it
- Test a position: scan a new ISIN and see how the allocation would change
- One-click demo portfolio (97 stocks, 53 countries)

## Input format

An `.xlsx` / `.xls` file, max 5 MB. Column A holds the ISIN and column B the position value. A header row is optional.

| A · ISIN     | B · Value |
|--------------|-----------|
| US0378331005 | 12 000    |
| FR0000121014 | 7 000     |

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

On Windows you can also double-click `run.bat`.

## Deploy on Streamlit Community Cloud

1. Push this folder to a GitHub repository.
2. At [share.streamlit.io](https://share.streamlit.io), click **Create app**, pick the repository and `app.py`.
3. Under **Advanced settings**, choose Python 3.12. To lift OpenFIGI's rate limit, add a secret:
   `OPENFIGI_API_KEY = "your-key"`. Get a free key at [openfigi.com](https://www.openfigi.com/api).

## Project layout

| Path | What it is |
|------|------------|
| `app.py` | The whole app |
| `.streamlit/config.toml` | Theme and the 5 MB upload limit |
| `data/fd_equities.csv.gz` | Embedded FinanceDatabase snapshot (ISIN → ticker, name, country, sector) |
| `data/countries_50m.geojson` | Natural Earth country shapes for the globe |
| `data/demo_portfolio.csv` | The demo portfolio |
| `data/demo_results.json` | The demo portfolio's pre-scanned results, so it opens instantly |
| `scripts/build_fd_snapshot.py` | Rebuilds the FinanceDatabase snapshot |

## Refreshing the data

FinanceDatabase is embedded as a snapshot rather than downloaded at runtime. To refresh it every few months:

```bash
pip install financedatabase
python scripts/build_fd_snapshot.py
```

Stocks missing from the snapshot are still resolved through yfinance and OpenFIGI.
