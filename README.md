# PortfolioScan

See where your money lives. Upload the exports of your brokers and insurers (CSV or Excel, several at once):
every stock, ETF, bond fund, SCPI, euro fund and cash line is mapped by **country, sector, asset class and account**
on an interactive 3D globe, with a clear record of where each piece of data comes from.

Made by [Gabin Lê](https://www.linkedin.com/in/gabin-le/) · 2026

---

## Contents

1. [Quick start](#quick-start)
2. [Importing your files](#importing-your-files)
3. [How each position is analysed](#how-each-position-is-analysed)
4. [The cockpit](#the-cockpit)
5. [Detail windows](#detail-windows)
6. [Data issues and manual corrections](#data-issues-and-manual-corrections)
7. [Testing a position](#testing-a-position)
8. [Rebalance Assistant](#rebalance-assistant)
9. [Metrics](#metrics)
10. [Demo portfolio](#demo-portfolio)
11. [Privacy](#privacy)
12. [Limitations](#limitations)
13. [Run locally](#run-locally) · [Deploy](#deploy-on-streamlit-community-cloud) ·
    [Project layout](#project-layout) · [Refreshing the data](#refreshing-the-data)

---

## Quick start

1. Drop one or more exports on the landing page and click **▶ Analyse N files**.
2. For each file, check the columns the app detected, correct them if needed, and click **✓ Confirm this file**.
3. The scan runs (only for the stocks it does not already know), then the cockpit opens.

Or click **▶ Explore a demo portfolio** to see everything without a file.

---

## Importing your files

### Accepted files

`.csv`, `.txt`, `.xlsx` and `.xls`, up to 5 MB each, as many files as you like. Each file is reviewed on its own,
then all of them are merged into one portfolio: the same ISIN held in several files is summed.

### What is detected automatically

| What | How |
|------|-----|
| Separator (CSV) | comma, semicolon, tab or pipe, guessed from the first lines |
| Sheet (Excel) | the sheet with the most positions |
| Header row | the nearest all-text row above the first ISIN, so title lines on top are skipped |
| ISIN / code column | the column with the most ISINs or recognised codes |
| Value column | by header ("Montant en EUR", "Somme en compte", "Valorisation", "Market value", "Value"…), never a price, quantity, cost, gain or performance column; otherwise the numeric column with the largest total |
| Name column | a name-like header ("Produit", "Nom", "Libellé", "Name"…), then the most varied text column |
| Account column | a header such as "N° Contrat", "Compte", "Account", "Portfolio", with few distinct values |
| Account label | a column constant within each account (e.g. the product name), put in front of the account number |

Numbers are read in any common format: `3 561,18 €`, `1,234.56`, `1.234,56`, `- 111,61`. Lines that Degiro wraps
entirely in quotes are unwrapped.

### The review window

For every file, a **Check the import** window shows:

- the separator or sheet, the header row and the ISIN, value, name and account columns, each in a menu you can change;
- an **Account name**, used for every line when the file has no account column (the file name by default);
- a preview of the file with the chosen columns highlighted;
- the positions recognised (code, name, value, type, account), the accounts found with their totals, and the
  ignored rows with the reason (no ISIN, no value, value 0).

Buttons: **Confirm this file**, **Skip file**, **Cancel**.

### Lines without an ISIN

| Line | Recognised from |
|------|-----------------|
| Cash | code `CASH…` / `LIQUIDIT…` / `ESPECES…`, or a name containing "cash", "liquidités", "espèces" |
| Euro fund | codes such as `SUR000001730`, `FONDSEURNG`, `NETIS075`, or a name like "Fonds en euros…" |
| SCPI | code `SCPI…`, or a name starting with "SCPI" |

A line with a real ISIN is never reclassified from its name (a money-market ETF called "… Cash" stays an ETF).

The simplest possible file is two columns, ISIN then value:

| A · ISIN     | B · Value |
|--------------|-----------|
| US0378331005 | 12 000    |
| FR0000121014 | 7 000     |

---

## How each position is analysed

### Stocks

Name, country and sector are looked up in a cascade, each source filling only what the previous one missed:

1. **FinanceDatabase**, embedded as a snapshot (45,000+ ISINs), so most stocks resolve instantly and offline;
2. **Yahoo Finance** (yfinance), by ISIN;
3. **OpenFIGI**, by ISIN, with the sector matched back by company name in FinanceDatabase;
4. last resort for the country: the ISIN's country prefix (shown as an estimate).

ADRs (e.g. ARM, Infosys) get the company's country, not the United States.

### ETFs from the snapshot (2,600+ funds)

iShares, SPDR, Amundi and Xtrackers ETFs come with their real country and sector breakdown, computed from the
issuers' published fund data (every holding summed by country and by sector). Each ETF keeps its **10 largest
countries and 10 largest sectors**; the rest is grouped as **Other**, and holdings the issuer did not classify are
**Unknown**.

- Synthetic (swap) ETFs hold a collateral basket unrelated to what they track, so their breakdown is the one of the
  **index** they replicate.
- Sector names are normalised to the 11 GICS sectors.
- An ETF is spread over its countries and sectors: totals per country and per sector are exact, how they pair up
  inside the fund is approximated.

### Other ETFs: Yahoo Finance estimate

An ETF missing from the snapshot is looked up on Yahoo Finance, trying each of its listings until one has fund data.

- **Sectors**: the fund's own sector weights, complete.
- **Countries**: Yahoo has no country breakdown. A fund named after a single country ("MSCI Japan", "Switzerland")
  is that country at 100%. Otherwise the countries of its top 10 holdings are used (from FinanceDatabase or their
  stock exchange) and the rest of the fund is Unknown.

An ETF with no fund data anywhere is excluded and listed in the Issues window.

### Bonds, SCPI, euro funds and cash

| Type | Country | Sector | Asset class |
|------|---------|--------|-------------|
| Bond ETF | the issuers' countries | "Bonds" (GICS sectors describe companies) | Bonds |
| SCPI | France (a SCPI is a French vehicle) | Real Estate | Real estate (SCPI) |
| Euro fund, cash | none by nature | none by nature | Euro funds & cash |

### Grey lines

**Unknown**, **Other (outside top 10)**, **Bonds**, **Euro funds & cash** and an ETF's own **Cash & Derivatives**
are not real countries or sectors: they are shown in grey at the bottom of every list, never on the globe, and not
counted in the number of countries and sectors.

---

## The cockpit

A full-screen spinning globe, coloured by the weight of each country, with cards on both sides.

**Left column**

- **Portfolio**: total value, number of positions, countries, sectors, share resolved, and notes (ETFs excluded with
  their share of the total value, value with no mappable country). Button **＋ Test a position**.
- **Asset classes**: equities, bonds, real estate (SCPI), euro funds & cash.
- **Accounts**: value, share and number of positions of each account. Click one to open it in the Accounts window.
- **Top holdings**: the six largest positions (an ETF appears once, as "ETF · equities").

**Right column**

- **Top countries** (7): hover a row for the positions in that country, click it to fly the globe there, click the
  title to open the Countries window.
- **Top sectors** (7): hover a row for its positions, click the title to open the Sectors window.
- **Simulation** (only with test positions): edit or remove each one, and see the largest sector and country shifts.
- **Details**: buttons for the detail windows, **⟲ Restart** (back to the upload page) and a link to GitHub.

**On the globe**: drag to spin, scroll to zoom, hover a country to list its positions, click to fly to it.

---

## Detail windows

| Window | What it shows |
|--------|---------------|
| **▦ Sectors** | Treemap of sectors and the positions inside each; click a sector to zoom in |
| **◉ Countries** | Bar charts of every country and region. **Search box** to filter them. Hover a bar for its positions; **click a bar** to list every position in that country or region |
| **≡ Holdings** | Every position with name, ISIN, ticker, country, sector, value, weight and sources; filter, sort by any column, **Export CSV** |
| **▤ Accounts** | List of accounts; for the selected one: value, share, asset classes, top countries, top sectors and every position with its weight in the account. **Click a country or sector** to list that account's positions in it |
| **∑ Metrics** | Diversification, fees, valuation and income of the whole portfolio (see below) |
| **⚠ Data** | Three tabs. **Issues**: every missing or guessed piece of data, with manual correction (see below). **Scan log**: every lookup of the scan, source by source. **Data sources**: for each position, where its name, country, sector and asset class come from |
| **⚖ Rebalance Assistant** | Opened from the Portfolio card: what to buy with your next contribution to reach a target allocation (see below) |
| **± Sim.** | Only with test positions: how they shift the sector and country weights |

**Position lists** (opened from a country, region or account): each position with its account, its value in that
country / sector, its share of the portfolio (or of the account) and the part of the position that sits there (100%
for a stock, a share for a fund). **← Back** returns to the previous window.

**Data sources tags**: *Database* (FinanceDatabase, OpenFIGI), *Issuer data*, *Yahoo Finance*, *Estimate* (with what
it was estimated from), *Manual*, *Your file*, *Not found*.

---

## Data issues and manual corrections

The **Issues** tab of the **⚠ Data** window lists, sorted by weight and with a summary ("3 issues · 2.8% of the portfolio affected"):

- ETFs and funds with no data at all, and why (e.g. "yfinance: quoteType MUTUALFUND");
- stocks not identified, or missing a country or a sector;
- countries guessed from the ISIN prefix, or not found on the map;
- ETFs with 5% or more of the fund without a country or sector;
- for each: its weight in the whole portfolio and where the data was looked for.

**✎ Fix** opens an editor prefilled with the position's current breakdown: one line per country and per sector with
a percentage, suggestions while typing, lines to add or remove. **Save correction** replaces what was found
everywhere in the app (each list is rescaled to 100%); it also brings an excluded ETF back into the portfolio.
Corrections are listed under **Manual corrections** with **Edit** and **Reset**, and tagged *Manual* in Data
sources. They last for the session.

---

## Testing a position

**＋ Test a position**: enter an ISIN and a value. An ETF from the snapshot is added at once; any other ISIN is
scanned first and added if it resolves to a stock or a Yahoo-estimated ETF. Test positions go into a "Simulation"
account and show in the Simulation card and the **± Sim.** window.

---

## Rebalance Assistant

Set a target allocation and an amount to invest; the assistant tells you exactly what to buy, line by line, to get
closer to it **without selling anything** (selling is an option you switch on).

- **Targets** mix freely: countries, regions, sectors and asset classes, for example 55% United States, 15% Europe,
  12% Information Technology. **Start from my current** fills a dimension with today's top categories to edit.
  Categories are measured through your funds (an ETF counts for each country and sector it holds).
- **The plan** lists each line to buy (or sell) with its amount, its weight before and after, and which targets it
  helps. **Export plan (CSV)** and **Copy** give you the list to place your orders.
- **Options**: trade in at most N lines (a monthly contribution is rarely spread over 30 ETFs), round amounts to
  10 / 50 / 100, leave out the lines you do not want to touch, and allow selling overweight lines. Selling is only
  proposed when a target is off by more than half a point.
- **Insights**: the average gap to your targets before and after; how much you would have to invest in total for
  buying alone to fix an overweight (with this month's amount that may not be enough); how selling would compare;
  the number of months of the same contribution to get within one point of every target (markets assumed flat);
  and a warning when none of your lines gives exposure to a target.
- **How it works**: the plan minimises the squared gap between each target and the portfolio's weight after the
  trade, solved in your browser so it updates as you type. Your targets are remembered in your browser.

---

## Metrics

The **∑ Metrics** window measures the portfolio as a whole. Funds are looked through, so an ETF counts for each country and
sector it holds. Every figure has an ⓘ with its definition, and every figure built from incomplete data says how much
of the portfolio it covers.

**Diversification**
- **Geographic and sector diversification**: the Herfindahl-Hirschman index (sum of squared weights, out of 10,000) and
  the *effective number* of countries or sectors (1 / HHI), next to the same figure for the world index (MSCI ACWI).
  Unknown and cash are left out.
- **Position concentration**: largest line, top 5, top 10, effective number of positions.
- **Home bias**: your weight in a country of your choice (France by default) against the world index.
- **Emerging markets** and **currency exposure** (by the underlying country, not the fund's trading currency).
- **Tilt against the world index**: biggest over- and underweights by country and sector, and the active share.
- **Funds that may overlap**: pairs of funds with a near-identical country and sector breakdown. A hint only: the
  data has no individual stocks.

**Costs**
- **Fund fees**: weighted annual fee (TER) of your ETFs and funds, the yearly amount, and the lines that cost most.
  Trading fees per order (fixed, percent, minimum) are entered in the Rebalance Assistant, which shows the cost of its
  plan.

**Valuation and income**: P/E (trailing and forward), dividend income and yield, company size. The P/E is averaged
the harmonic way (total price over total earnings); loss-making companies and impossible values are left out.

**Where the numbers come from.** Stocks: Yahoo Finance, looked up when the results open (about 8 at a time, kept for a
day). ETFs: `data/etf_costs.csv.gz` (annual fee from Amundi's data and Yahoo, P/E and yield from Yahoo), or Yahoo
directly for an ETF that is not in the snapshot. Stocks have no fund fee; SCPI and euro funds have fees we do not know,
so they are listed as missing. Your trading fees stay in your browser.

---

## Demo portfolio

About 183k over three accounts (Interactive Brokers, Fidelity, Charles Schwab): world, emerging, US, Europe,
Pacific, Latin America, quality, water, property and small-cap ETFs, two bond ETFs, around fifty stocks from every
region, a SCPI, a euro fund and cash. It covers 50 countries and all 11 sectors, and opens instantly (its lookups are
pre-computed).

---

## Privacy

Uploaded files stay in the memory of your session: nothing is written to disk or kept after the session ends. For a
stock or ETF the embedded data does not cover, only its ISIN (or a fund ticker) is sent to Yahoo Finance or OpenFIGI;
values, names and accounts never leave the app.

---

## Limitations

- ETFs outside iShares, SPDR, Amundi and Xtrackers are Yahoo estimates; multi-country ones only have the countries of
  their top 10 holdings.
- Each ETF keeps its 10 largest countries and sectors; the rest is "Other".
- An ETF's country × sector pairing is approximated (the totals per country and per sector are exact).
- Manual corrections and test positions last for the session only.
- PDF exports are not read: export CSV or Excel from your broker.

---

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
| `app.py` | The app: scan, analysis, cockpit and detail windows |
| `importer.py` | Reads broker exports and detects their columns, accounts and special lines |
| `rebalance.py` | The Rebalance Assistant: exposure data, window and calculation |
| `metrics.py` | The Metrics window: Yahoo valuation data, fees, world-index benchmark and the figures |
| `run.bat` | Starts the app on Windows |
| `.streamlit/config.toml` | Theme and the 5 MB upload limit |
| `data/fd_equities.csv.gz` | FinanceDatabase snapshot: ISIN → ticker, name, country, sector |
| `data/fd_etf_tickers.csv.gz` | Every Yahoo ticker of each ETF, to estimate ETFs missing from the snapshot |
| `data/etf_funds.csv.gz` | One row per ETF: name, issuer, date, asset class, how its breakdown was built |
| `data/etf_exposure.csv.gz` | Each ETF's 10 largest countries and sectors, the rest as "Other" |
| `data/countries_50m.geojson` | Natural Earth country shapes for the globe |
| `data/demo_portfolio.csv` | The demo portfolio (ISIN, value, name, account) |
| `data/demo_results.json` | The demo stocks' pre-computed lookups, so the demo opens instantly |
| `data/demo_fundamentals.json` | The demo stocks' P/E, yield and market cap, so its metrics open instantly |
| `data/etf_costs.csv.gz` | Each ETF's annual fee, P/E and dividend yield |

## Refreshing the data

The files in `data/` are snapshots, refreshed offline about once a month by a single maintenance script that is not
part of this repository: it rebuilds the FinanceDatabase extracts and the ETF country / sector snapshot. The app only
reads these files at runtime; stocks missing from the snapshot are still resolved live through Yahoo Finance and
OpenFIGI.
