"""Metrics window: the extra data it needs (valuation, fees, a world-index benchmark) and the window itself.

Every figure is computed in the browser from the exposures already sent for the Rebalance Assistant (RB) plus the
per-position data built here (MT), so the fee inputs update the totals as you type.

Sources, per position:
  - stocks: Yahoo Finance (P/E, forward P/E, dividend yield, market cap), fetched when the results open and
    kept for a day;
  - ETFs and funds: data/etf_costs.csv.gz (annual fee, P/E, dividend yield; built by the update script from Amundi's API
    and Yahoo), or Yahoo directly for an ETF that is not in the snapshot;
  - the world-index benchmark: the iShares MSCI ACWI breakdown from the ETF snapshot.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

BENCH_ISIN = "US4642882579"  # iShares MSCI ACWI ETF: the world index the portfolio is compared with
BENCH_NAME = "MSCI ACWI"

EURO_AREA = ["Austria", "Belgium", "Croatia", "Cyprus", "Estonia", "Finland", "France", "Germany", "Greece", "Ireland",
             "Italy", "Latvia", "Lithuania", "Luxembourg", "Malta", "Netherlands", "Portugal", "Slovakia", "Slovenia",
             "Spain"]
CURRENCY = {**{c: "EUR" for c in EURO_AREA}, "United States": "USD", "Japan": "JPY", "United Kingdom": "GBP",
            "Switzerland": "CHF", "Canada": "CAD", "Australia": "AUD", "New Zealand": "NZD", "Sweden": "SEK",
            "Norway": "NOK", "Denmark": "DKK", "Poland": "PLN", "Czech Republic": "CZK", "Hungary": "HUF",
            "China": "CNY", "Hong Kong": "HKD", "Taiwan": "TWD", "South Korea": "KRW", "India": "INR",
            "Singapore": "SGD", "Brazil": "BRL", "Mexico": "MXN", "South Africa": "ZAR", "Israel": "ILS",
            "Saudi Arabia": "SAR", "United Arab Emirates": "AED", "Turkey": "TRY", "Indonesia": "IDR",
            "Thailand": "THB", "Malaysia": "MYR", "Philippines": "PHP", "Chile": "CLP", "Colombia": "COP",
            "Peru": "PEN", "Qatar": "QAR", "Kuwait": "KWD", "Egypt": "EGP"}
EMERGING = ["China", "India", "Taiwan", "South Korea", "Brazil", "Saudi Arabia", "South Africa", "Mexico", "Indonesia",
            "Thailand", "Malaysia", "United Arab Emirates", "Qatar", "Kuwait", "Poland", "Turkey", "Philippines",
            "Chile", "Greece", "Peru", "Hungary", "Czech Republic", "Colombia", "Egypt"]

_CACHE: dict[str, tuple[float, dict]] = {}  # Yahoo symbol -> (when, fields)
_TTL = 24 * 3600


def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


# Yahoo sometimes returns nonsense (a P/E of 0.1 for a stock quoted in a minor unit). One such value
# wrecks a harmonic average, so anything outside these ranges is treated as missing.
PLAUSIBLE = {"pe": (1, 300), "fpe": (2, 150), "dy": (0, 30), "mc": (1e6, 1e14), "ter": (0, 3)}


def clean(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k not in PLAUSIBLE or PLAUSIBLE[k][0] <= v <= PLAUSIBLE[k][1]}


def _yahoo(symbols: list[str], etf: bool) -> dict:
    """Valuation fields of the first of `symbols` Yahoo knows. Empty if none does."""
    import yfinance as yf
    for sym in symbols:
        if not sym:
            continue
        hit = _CACHE.get(sym)
        if hit and time.time() - hit[0] < _TTL:
            return hit[1]
        try:
            info = yf.Ticker(sym).info or {}
        except Exception:  # noqa: BLE001 — throttled / unknown symbol: try the next one
            continue
        if not (info.get("longName") or info.get("shortName")):
            continue
        out = {"pe": _num(info.get("trailingPE")), "dy": _num(info.get("dividendYield"))}
        if etf:
            out["ter"] = _num(info.get("netExpenseRatio"))
        else:
            out.update(fpe=_num(info.get("forwardPE")), mc=_num(info.get("marketCap")))
        out = clean({k: v for k, v in out.items() if v is not None})
        _CACHE[sym] = (time.time(), out)
        return out
    return {}


def load_costs(path: Path) -> dict:
    """ISIN -> {ter, pe, dy} from the update script's file."""
    if not path.exists():
        return {}
    d = pd.read_csv(path, keep_default_na=False)
    out = {}
    for r in d.itertuples(index=False):
        rec = {k: float(v) for k, v in (("ter", r.ter), ("pe", r.pe), ("dy", r.yld)) if v not in ("", None)}
        rec = clean(rec)
        if rec:
            out[r.isin] = rec
    return out


def gather(items: list[dict], costs: dict, cached: dict, budget: float = 25.0) -> dict:
    """items: [{"i": isin, "k": "stock" | "etf" | "special", "t": ticker, "snap": bool}] -> isin -> fields.

    Stocks always go to Yahoo (in parallel, at most `budget` seconds in all); an ETF takes its row from the update
    script's file, and goes to Yahoo only when it is not in the snapshot."""
    out, todo = {}, []
    for it in items:
        if it["k"] == "special":
            continue
        if it["i"] in cached:
            out[it["i"]] = cached[it["i"]]
        elif it["k"] == "etf" and it["snap"]:
            if it["i"] in costs:
                out[it["i"]] = costs[it["i"]]
        else:
            todo.append(it)
    if todo:
        end = time.time() + budget
        pool = ThreadPoolExecutor(max_workers=8)
        jobs = {pool.submit(_yahoo, [it["t"], it["i"]], it["k"] == "etf"): it["i"] for it in todo}
        try:
            for f in as_completed(jobs, timeout=max(1.0, end - time.time())):
                try:
                    got = f.result()
                except Exception:  # noqa: BLE001
                    got = {}
                if got:
                    out[jobs[f]] = got
        except Exception:  # noqa: BLE001 — out of time: keep what arrived
            pass
        pool.shutdown(wait=False, cancel_futures=True)
    return out


def benchmark(snapshot: dict) -> dict:
    """The world index's country and sector weights (in %), from the ETF snapshot; empty when it is not there."""
    b = snapshot.get(BENCH_ISIN)
    if not b:
        return {}
    return {"name": BENCH_NAME, "c": b["country"], "s": b["sector"]}


def payload(fund: dict, items: list[dict], bench: dict, cash: list[str]) -> dict:
    return {"f": fund, "k": {it["i"]: it["k"] for it in items}, "bench": bench,
            "ccy": CURRENCY, "em": EMERGING, "cash": cash}


def js_data(m: dict) -> str:
    return json.dumps(m, ensure_ascii=False, allow_nan=False).replace("</", "<\\/")


def css(blue: str, ink: str, ink2: str, muted: str, red: str, mono: str, sans: str) -> str:
    out = CSS
    for k, v in {"@BLUE@": blue, "@INK@": ink, "@INK2@": ink2, "@MUTED@": muted, "@RED@": red,
                 "@MONO@": mono, "@SANS@": sans}.items():
        out = out.replace(k, v)
    return out


CSS = """
  .mt-sec { margin:0 0 26px; }
  .mt-sec > h4 { margin:0 0 12px; font:500 11px '@MONO@',monospace; letter-spacing:.2em; text-transform:uppercase; color:@BLUE@; }
  .mt-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(340px, 1fr)); gap:14px; }
  .mt-card { border:1px solid rgba(255,255,255,.09); border-radius:14px; padding:14px 16px; background:rgba(255,255,255,.025);
    min-width:0; }
  .mt-card.wide { grid-column:1 / -1; }
  .mt-k { display:flex; justify-content:space-between; gap:8px; font:10px '@MONO@',monospace; letter-spacing:.16em;
    text-transform:uppercase; color:@MUTED@; }
  .mt-k i { font-style:normal; cursor:help; color:#86b6ef; letter-spacing:0; }
  .mt-v { font:600 26px '@MONO@',monospace; color:@INK@; margin:8px 0 2px; white-space:nowrap; }
  .mt-v small { font-size:12px; color:@MUTED@; font-weight:400; margin-left:6px; }
  .mt-s { font-size:12px; color:@MUTED@; line-height:1.5; margin-bottom:10px; }
  .mt-s b { color:@INK2@; font-weight:600; }
  .mt-b { display:grid; grid-template-columns:minmax(80px,38%) 1fr 66px; gap:8px; align-items:center; margin:5px 0; font-size:12px;
    color:@INK2@; }
  .mt-b span:first-child { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .mt-b em { font:11px '@MONO@',monospace; font-style:normal; text-align:right; color:@INK2@; }
  .mt-t { position:relative; height:6px; border-radius:5px; background:rgba(255,255,255,.07); }
  .mt-t div { position:absolute; left:0; top:0; bottom:0; border-radius:5px; background:linear-gradient(90deg,#1c5cab,#5598e7); }
  .mt-t u { position:absolute; top:-3px; bottom:-3px; width:2px; background:#fff; text-decoration:none; }
  .mt-t.dn div { background:linear-gradient(90deg,#9c3b3b,#e66767); }
  .mt-note { font-size:11.5px; color:@MUTED@; margin-top:8px; line-height:1.5; }
  .mt-cov { display:inline-block; font:10.5px '@MONO@',monospace; color:@MUTED@; border:1px solid rgba(255,255,255,.12);
    border-radius:999px; padding:1px 8px; margin-top:6px; }
  .mt-cov.low { color:#c9b37a; border-color:rgba(201,179,122,.4); }
  .mt-card table { font-size:12px; margin-top:6px; }
  .mt-card th { position:static; cursor:default; padding:6px 8px; }
  .mt-card td { padding:6px 8px; white-space:normal; }
  .mt-card select { background:#10141b; border:1px solid rgba(255,255,255,.12); border-radius:7px; color:@INK@; padding:3px 6px;
    font:12px '@SANS@',sans-serif; max-width:150px; }
  .mt-tilt { display:grid; grid-template-columns:1fr 1fr; gap:18px; }
  @media (max-width: 760px) { .mt-tilt { grid-template-columns:1fr; } }
"""

TEMPLATE = '<div class="mt" id="mt"></div>'

JS = r"""
// ── Metrics ─────────────────────────────────────────────────────────────────
function initMetrics() {
  const H = RB.h, T = H.reduce((s, h) => s + h.v, 0), F = MT.f, K = MT.k;
  const NEUTRAL = new Set(RB.neutral), root = document.getElementById('mt');
  const money = v => Math.round(v).toLocaleString();
  const p = (v, d = 1) => v.toFixed(d) + '%';
  const sum = a => a.reduce((s, x) => s + x, 0);

  // value (in money) of each category of a dimension, looking through funds
  const euros = d => { const m = {}; H.forEach(h => { const x = RB.x[d][h.i]; if (x) for (const k in x) m[k] = (m[k] || 0) + h.v * x[k]; }); return m; };
  const mapped = d => {  // shares (0-1) among what has a real category (no unknown / cash / "other")
    const e = euros(d), keys = Object.keys(e).filter(k => !NEUTRAL.has(k)), t = sum(keys.map(k => e[k]));
    const out = {}; keys.forEach(k => out[k] = e[k] / t); return {shares: out, total: t, all: e};
  };
  const hhi = sh => sum(Object.values(sh).map(x => x * x));
  const B = MT.bench || {};
  const benchShares = d => {  // the index's named categories as shares (0-1); "Other" and unknown buckets left out
    const src = B[d]; if (!src) return null;
    const keys = Object.keys(src).filter(k => !NEUTRAL.has(k)), t = sum(keys.map(k => src[k]));
    const out = {}; keys.forEach(k => out[k] = src[k] / t); return out;
  };
  const benchAll = d => {  // the index with its "Other" bucket kept, as shares (0-1)
    const src = B[d]; if (!src) return null;
    const keys = Object.keys(src).filter(k => k !== 'Unknown' && !MT.cash.includes(k)), t = sum(keys.map(k => src[k]));
    const out = {}; keys.forEach(k => out[k] = src[k] / t); return out;
  };
  const bar = (label, v, max, extra = '', ref = null, cls = '') => `<div class="mt-b"><span title="${esc(label)}">${esc(label)}</span>
    <div class="mt-t ${cls}"><div style="width:${Math.max(0, Math.min(100, 100 * v / max))}%"></div>${ref === null ? '' : `<u style="left:${Math.min(100, 100 * ref / max)}%"></u>`}</div>
    <em>${extra || p(100 * v)}</em></div>`;
  const card = (title, big, sub, body, tip, wide) => `<div class="mt-card${wide ? ' wide' : ''}"><div class="mt-k"><span>${title}</span>${tip ? `<i title="${esc(tip)}">ⓘ</i>` : ''}</div>
    ${big === null ? '' : `<div class="mt-v">${big}</div>`}${sub ? `<div class="mt-s">${sub}</div>` : ''}${body || ''}</div>`;
  const cov = (share, what) => `<span class="mt-cov${share < 0.6 ? ' low' : ''}" title="Share of ${what} for which the data was found">data for ${Math.round(100 * Math.min(1, share))}% of ${what}</span>`;

  // ── diversification ──
  const C = mapped('c'), S = mapped('s');
  const bc = benchShares('c'), bs = benchShares('s');
  const divCard = (title, m, bsh, unit, tip) => {
    const h = hhi(m.shares), n = h ? 1 / h : 0, top = Object.entries(m.shares).sort((a, b) => b[1] - a[1]);
    const bn = bsh ? 1 / hhi(bsh) : null;
    return card(title, n.toFixed(1) + `<small>effective ${unit}</small>`,
      `Concentration index (HHI) <b>${Math.round(10000 * h).toLocaleString()}</b> out of 10,000. ` + (bn ? `The world index (${MT.bench.name}) scores <b>${bn.toFixed(1)}</b> by the same measure, so you are <b>${n > bn ? 'more' : 'less'} spread</b> than it.` : ''),
      top.slice(0, 5).map(([k, v]) => bar(k, v, top[0][1])).join(''), tip);
  };
  const posSorted = H.slice().sort((a, b) => b.v - a.v);
  const posShare = n => sum(posSorted.slice(0, n).map(h => h.v)) / T;
  const effPos = 1 / sum(H.map(h => (h.v / T) ** 2));
  const em = (() => { const s = sum(Object.entries(C.shares).filter(([k]) => MT.em.includes(k)).map(([, v]) => v)); return s; })();
  const homeOpts = Object.keys(C.shares).sort((a, b) => C.shares[b] - C.shares[a]);
  const home0 = homeOpts.includes('France') ? 'France' : homeOpts[0];
  const homeCard = h => {
    const ba = benchAll('c'), mine = C.shares[h] || 0, ref = ba && ba[h] !== undefined ? ba[h] : null;
    return `<div class="mt-k"><span>Home bias</span><i title="Share of your portfolio in one country, against its weight in the world index.">ⓘ</i></div>
      <div class="mt-v">${p(100 * mine)}</div><div class="mt-s">in <select id="mt-home">${homeOpts.map(c => `<option${c === h ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select>
      ${ref !== null ? `· the world index holds <b>${p(100 * ref)}</b>` : `· under ${p(Math.min(...Object.values(B.c || {1: 2}).filter(x => x > 0)))} in the world index (outside its ten largest countries)`}</div>
      ${bar('You', mine, Math.max(mine, ref || 0, 0.01) * 1.1)}${ref !== null ? bar('World index', ref, Math.max(mine, ref) * 1.1) : ''}`;
  };
  const ccy = (() => {
    const e = euros('c'), m = {};
    for (const k in e) { const c = MT.ccy[k] || (MT.cash.includes(k) ? 'EUR' : 'Other'); m[c] = (m[c] || 0) + e[k]; }
    return Object.entries(m).sort((a, b) => b[1] - a[1]);
  })();
  const tilt = (title, mine0, ref, nTop) => {
    if (!ref) return '';
    const mine = {};  // what the index does not name goes with its "Other" bucket
    for (const k in mine0) { const key = ref[k] !== undefined ? k : 'Other'; mine[key] = (mine[key] || 0) + mine0[k]; }
    const rows = [...new Set([...Object.keys(mine), ...Object.keys(ref)])].map(k => [k === 'Other' ? 'Everything else' : k, (mine[k] || 0) - (ref[k] || 0)]);
    const active = 0.5 * sum(rows.map(r => Math.abs(r[1])));
    rows.sort((a, b) => b[1] - a[1]);
    const pick = [...rows.slice(0, nTop), ...rows.slice(-nTop).reverse()].filter((r, i, a) => a.findIndex(x => x[0] === r[0]) === i);
    const mx = Math.max(...pick.map(r => Math.abs(r[1])), 0.01);
    return `<div><div class="mt-k"><span>${title}</span></div><div class="mt-s">Active share <b>${p(100 * active)}</b> (0% means the world index itself)</div>
      ${pick.map(r => bar(r[0], Math.abs(r[1]), mx, (r[1] >= 0 ? '+' : '−') + (100 * Math.abs(r[1])).toFixed(1) + ' pt', null, r[1] < 0 ? 'dn' : '')).join('')}</div>`;
  };
  const redund = (() => {
    const funds = H.filter(h => K[h.i] === 'etf' && Object.keys(RB.x.c[h.i] || {}).length >= 3);
    const vec = (h, dims) => { const o = {}; for (const d of dims) for (const k in (RB.x[d][h.i] || {})) o[d + k] = RB.x[d][h.i][k]; return o; };
    const cos = (a, b) => { let dot = 0, na = 0, nb = 0; for (const k in a) { na += a[k] * a[k]; if (b[k]) dot += a[k] * b[k]; } for (const k in b) nb += b[k] * b[k]; return dot / Math.sqrt(na * nb); };
    const out = [];
    for (let i = 0; i < funds.length; i++) for (let j = i + 1; j < funds.length; j++) {
      if (funds[i].a !== funds[j].a) continue;
      const dims = funds[i].a === 'Equities' ? ['c', 's'] : ['c'];  // bond funds all share one "sector"
      const c = cos(vec(funds[i], dims), vec(funds[j], dims));
      if (c >= 0.97) out.push([funds[i], funds[j], c]);
    }
    return out.sort((a, b) => b[2] - a[2]).slice(0, 5);
  })();
  // ── fees ──
  const fundLines = H.filter(h => K[h.i] === 'etf');
  const withTer = fundLines.filter(h => F[h.i] && F[h.i].ter !== undefined);
  const stockV = sum(H.filter(h => K[h.i] === 'stock').map(h => h.v));
  const covered = sum(withTer.map(h => h.v)) + stockV;
  const unknownFees = H.filter(h => K[h.i] === 'special' || (K[h.i] === 'etf' && !(F[h.i] && F[h.i].ter !== undefined)));
  const fundFees = sum(withTer.map(h => h.v * F[h.i].ter / 100));
  // ── valuation ──
  const equityV = sum(H.filter(h => h.a === 'Equities').map(h => h.v));
  const harmonic = key => {
    const rows = H.filter(h => F[h.i] && F[h.i][key] > 0);
    const v = sum(rows.map(h => h.v)); if (!v) return null;
    return {val: v / sum(rows.map(h => h.v / F[h.i][key])), cov: equityV ? v / equityV : 0};
  };
  const pe = harmonic('pe'), fpe = harmonic('fpe');
  const divRows = H.filter(h => F[h.i] && F[h.i].dy !== undefined);
  const divInc = sum(divRows.map(h => h.v * F[h.i].dy / 100)), divV = sum(divRows.map(h => h.v));
  const caps = [['Mega · over 200B', 2e11, Infinity], ['Large · 10B to 200B', 1e10, 2e11], ['Mid · 2B to 10B', 2e9, 1e10], ['Small · under 2B', 0, 2e9]];
  const capRows = H.filter(h => K[h.i] === 'stock' && F[h.i] && F[h.i].mc > 0), capV = sum(capRows.map(h => h.v));

  root.innerHTML = `
  <div class="mt-sec"><h4>Diversification</h4><div class="mt-grid">
    ${divCard('Geographic diversification', C, bc, 'countries', 'Effective number = 1 / HHI. HHI is the sum of the squared weights of the countries (10,000 means everything in one). Fund contents are looked through. Unknown and cash are left out.')}
    ${divCard('Sector diversification', S, bs, 'sectors', 'Same measure on the 11 sectors, looking through funds.')}
    ${card('Position concentration', effPos.toFixed(1) + '<small>effective positions</small>',
      `Largest line <b>${p(100 * posShare(1))}</b> · top 5 <b>${p(100 * posShare(5))}</b> · top 10 <b>${p(100 * posShare(10))}</b> of the portfolio. A fund counts as one line here.`,
      posSorted.slice(0, 5).map(h => bar(h.n, h.v / T, posSorted[0].v / T)).join(''), 'Effective positions = 1 / sum of squared line weights: 10 equal lines give 10.')}
    <div class="mt-card" id="mt-home-card">${homeCard(home0)}</div>
    ${card('Emerging markets', p(100 * em), `<b>${p(100 * (1 - em))}</b> in developed markets. Counted by country, looking through funds.`, bar('Emerging', em, 1) + bar('Developed', 1 - em, 1), 'Emerging countries follow the usual MSCI list (China, India, Taiwan, Korea, Brazil, Gulf states, and so on).')}
    ${card('Currency exposure', null, 'Where the underlying companies are, not the currency a fund trades in. Cash and euro funds count as euro.', ccy.slice(0, 6).map(([c, v]) => bar(c, v / T, ccy[0][1] / T)).join(''), 'Estimated from each country’s currency.')}
    ${B.c ? `<div class="mt-card wide"><div class="mt-k"><span>Tilt against the world index (${esc(B.name)})</span><i title="Positive: you hold more than the index. Active share is half the sum of the gaps.">ⓘ</i></div>
      <div class="mt-tilt" style="margin-top:10px">${tilt('Countries', C.shares, benchAll('c'), 4)}${tilt('Sectors', S.shares, benchAll('s'), 4)}</div>
      <div class="mt-note">The index breakdown is its ten largest countries; the rest is grouped, so countries outside that list are shown together under your own total.</div></div>` : ''}
    <div class="mt-card wide"><div class="mt-k"><span>Funds that may overlap</span><i title="We only see each fund’s countries and sectors, not its stocks, so this is a hint, not a measure.">ⓘ</i></div>
      ${redund.length ? '<div class="mt-s">These pairs of funds hold very similar countries and sectors. Owning both may add little diversification.</div><table><tbody>' +
        redund.map(r => `<tr><td>${esc(r[0].n)}</td><td>${esc(r[1].n)}</td><td class="m">${r[2] >= 0.995 ? 'near-identical' : 'very similar'}</td><td class="m">${p(100 * (r[0].v + r[1].v) / T)}</td></tr>`).join('') + '</tbody></table>'
        : '<div class="mt-s">No pair of funds with a near-identical breakdown.</div>'}</div>
  </div></div>

  <div class="mt-sec"><h4>Costs</h4><div class="mt-grid">
    ${card('Fund fees', withTer.length || (stockV && !fundLines.length) ? p(covered ? fundFees / covered * 100 : 0, 2) + `<small>average · ${money(fundFees)} per year</small>` : '—',
      `Weighted annual fee (TER) of your ETFs and funds, on the part of the portfolio where we found it. ${cov(covered / T, 'the portfolio')}`,
      withTer.slice().sort((a, b) => b.v * F[b.i].ter - a.v * F[a.i].ter).slice(0, 6).map(h => bar(h.n, h.v * F[h.i].ter / 100, Math.max(...withTer.map(x => x.v * F[x.i].ter / 100)), F[h.i].ter.toFixed(2) + '%')).join('') +
      (unknownFees.length ? `<div class="mt-note">No fee found for: ${unknownFees.slice(0, 6).map(h => esc(h.n)).join(' · ')}${unknownFees.length > 6 ? ' …' : ''}</div>` : ''),
      'Total expense ratio, the yearly fee taken inside the fund. From the fund manager (Amundi) or Yahoo Finance.')}
  </div></div>

  <div class="mt-sec"><h4>Valuation and income</h4><div class="mt-grid">
    ${card('P/E ratio', pe ? pe.val.toFixed(1) : '—', pe ? `Price paid per unit of yearly profit${fpe ? `, <b>${fpe.val.toFixed(1)}</b> on expected profits` : ''}. ${cov(pe.cov, 'your equities')}` : 'No data.',
      `<div class="mt-note">Averaged the harmonic way (total price over total profit), the right way to average a ratio. Loss-making companies are left out. ETFs count when Yahoo publishes their P/E.</div>`,
      'Price / earnings.')}
    ${card('Dividend income', money(divInc) + '<small>per year (estimate)</small>', divV ? `About <b>${p(100 * divInc / divV, 2)}</b> yield on the part we have data for. ${cov(divV / (equityV || T), 'your equities')}` : 'No data.',
      `<div class="mt-note">Accumulating ETFs reinvest and pay nothing out, so they show a zero yield. This is the past year’s payout, not a promise.</div>`, 'Yield times value, per line.')}
    ${card('Company size', null, capRows.length ? 'Market value of the companies you hold directly. ' + cov(capV / (stockV || 1), 'your stocks') : 'No data.',
      capRows.length ? caps.map(([l, lo, hi]) => bar(l, sum(capRows.filter(h => F[h.i].mc >= lo && F[h.i].mc < hi).map(h => h.v)) / capV, 1)).join('') : '', 'Stocks only; ETFs are not broken down.')}
  </div></div>`;
  root.addEventListener('change', e => { if (e.target.id === 'mt-home') document.getElementById('mt-home-card').innerHTML = homeCard(e.target.value); });
}
"""
