"""Rebalance Assistant: the data it needs, and its window (HTML / CSS / JS) for the cockpit frame.

The maths runs in the browser so every change (amount, a target, an option) updates the plan at once:
  minimise  sum over targets of (weight after - target)^2      weight after = (exposure + purchases) / (value + amount)
  subject to  purchases - sales = amount,  purchases >= 0,  0 <= sales <= position value
with a small cost on sales, so a position is only sold when it clearly helps. It is solved by an accelerated projected
gradient method; "use at most N lines" then drops the smallest trades and solves again on the lines that are left.
"""
import pandas as pd

DIMS = (("c", "country"), ("r", "region"), ("s", "sector"), ("a", "asset"))


def payload(df: pd.DataFrame, cats: dict[str, list[str]], neutral: list[str]) -> dict:
    """Each position's value, and its share in every country / region / sector / asset class."""
    pos = (df.groupby("isin", sort=False).agg(n=("label", "first"), v=("value", "sum"), a=("asset", "first"))
           .sort_values("v", ascending=False))
    exposure = {}
    for key, col in DIMS:
        by = df.groupby(["isin", col], sort=False)["value"].sum()
        share = by / by.groupby(level=0, sort=False).transform("sum")
        d: dict[str, dict[str, float]] = {}
        for (isin, cat), f in share.items():
            if f >= 0.0005:
                d.setdefault(isin, {})[cat] = round(float(f), 4)
        exposure[key] = d
    return {"h": [{"i": isin, "n": r.n, "v": round(float(r.v), 2), "a": r.a} for isin, r in pos.iterrows()],
            "x": exposure, "cats": cats, "neutral": list(neutral)}


def css(blue: str, ink: str, ink2: str, muted: str, red: str, mono: str, sans: str) -> str:
    out = CSS
    for k, v in {"@BLUE@": blue, "@INK@": ink, "@INK2@": ink2, "@MUTED@": muted, "@RED@": red,
                 "@MONO@": mono, "@SANS@": sans}.items():
        out = out.replace(k, v)
    return out


CSS = """
  .rb { display:grid; grid-template-columns:400px 1fr; gap:22px; min-height:100%; }
  .rb-in, .rb-out { min-width:0; }
  .rb-sec { margin-bottom:20px; }
  .rb-amt { display:flex; align-items:center; gap:12px; }
  .rb-amt input { width:150px; font:600 20px '@MONO@',monospace; }
  .rb input, .rb select { background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.12); border-radius:8px;
    color:@INK@; padding:7px 9px; font:12.5px '@SANS@',sans-serif; outline:none; min-width:0; }
  .rb select { background:#10141b; cursor:pointer; }
  .rb input:focus, .rb select:focus { border-color:@BLUE@; box-shadow:0 0 0 3px rgba(57,135,229,.15); }
  .rb-hint { font:11px '@MONO@',monospace; color:@MUTED@; }
  .rb-opt { display:flex; align-items:center; gap:8px; margin-bottom:8px; font-size:12.5px; color:@INK2@; }
  .rb-chk { display:flex; align-items:flex-start; gap:9px; font-size:12.5px; color:@INK2@; line-height:1.4; cursor:pointer; }
  .rb-chk input { margin-top:2px; accent-color:@BLUE@; }
  .rb-chk small { display:block; color:@MUTED@; font-size:11px; }
  .rb details { margin-top:12px; font-size:12px; color:@INK2@; }
  .rb summary { cursor:pointer; color:#86b6ef; font:11.5px '@MONO@',monospace; }
  .rb-use { max-height:170px; overflow:auto; margin-top:8px; padding-right:4px; }
  .rb-use label { display:flex; gap:8px; align-items:center; padding:3px 0; font-size:12px; cursor:pointer; }
  .rb-use input { accent-color:@BLUE@; }
  .rb-use span { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .rb-mini { font:11px '@MONO@',monospace; color:#86b6ef; background:none; border:1px solid rgba(57,135,229,.35);
    border-radius:7px; padding:3px 9px; cursor:pointer; margin-right:6px; }
  .rb-mini:hover { color:@INK@; border-color:@BLUE@; }
  .rb-trow { display:grid; grid-template-columns:88px 1fr 64px 12px 24px; gap:6px; align-items:center; margin-bottom:7px; }
  .rb-trow select, .rb-trow input { width:100%; }
  .rb-trow .rb-t { font-family:'@MONO@',monospace; }
  .rb-trow span { color:@MUTED@; font-size:11px; }
  .rb-x { width:24px; height:24px; border-radius:6px; cursor:pointer; color:@MUTED@; background:transparent;
    border:1px solid rgba(255,255,255,.1); }
  .rb-x:hover { color:#fff; border-color:rgba(230,103,103,.6); }
  .rb-addrow { display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-top:10px; }
  .rb-addrow select { padding:4px 6px; font-size:11.5px; }
  .rb-sums { margin-top:10px; display:flex; flex-wrap:wrap; gap:6px; }
  .rb-chip { font:11px '@MONO@',monospace; color:@INK2@; border:1px solid rgba(255,255,255,.12); border-radius:999px;
    padding:3px 10px; }
  .rb-chip.bad { color:#f3b1b1; border-color:rgba(230,103,103,.5); }
  .rb-tiles { display:grid; grid-template-columns:repeat(4, 1fr); gap:10px; margin-bottom:18px; }
  .rb-tile { border:1px solid rgba(255,255,255,.09); border-radius:12px; padding:12px 14px; background:rgba(255,255,255,.025); }
  .rb-tile .k { font:10px '@MONO@',monospace; letter-spacing:.16em; text-transform:uppercase; color:@MUTED@; }
  .rb-tile .v { font:600 20px '@MONO@',monospace; margin-top:6px; color:@INK@; white-space:nowrap; }
  .rb-tile .s { font-size:11px; color:@MUTED@; margin-top:3px; }
  .rb-tile.ok .v { color:#7fd17f; }
  .rb-pill { display:inline-block; font:600 10px '@MONO@',monospace; letter-spacing:.08em; text-transform:uppercase;
    border-radius:5px; padding:2px 7px; }
  .rb-pill.buy { color:#7fd17f; background:rgba(12,163,12,.14); }
  .rb-pill.sell { color:#f3b1b1; background:rgba(230,103,103,.16); }
  td.rb-buy { color:#7fd17f; } td.rb-sell { color:#f3b1b1; }
  .rb-help { display:inline-block; font-size:11px; color:@INK2@; background:rgba(57,135,229,.1); border-radius:5px;
    padding:1px 6px; margin:0 4px 3px 0; white-space:nowrap; }
  .rb-help.dn { background:rgba(230,103,103,.1); }
  .rb-plan td { white-space:normal; vertical-align:top; }
  .rb-tr { margin-bottom:12px; }
  .rb-tr .top { display:flex; justify-content:space-between; gap:10px; font-size:12.5px; margin-bottom:5px; }
  .rb-tr .top span { font:11px '@MONO@',monospace; color:@MUTED@; white-space:nowrap; }
  .rb-track { position:relative; height:8px; border-radius:6px; background:rgba(255,255,255,.06); }
  .rb-track .after { position:absolute; left:0; top:0; bottom:0; border-radius:6px;
    background:linear-gradient(90deg,#1c5cab,#5598e7); }
  .rb-track .now { position:absolute; top:-3px; bottom:-3px; width:2px; background:rgba(255,255,255,.4); }
  .rb-track .goal { position:absolute; top:-5px; bottom:-5px; width:3px; border-radius:2px; background:#fff; }
  .rb-leg { font:10.5px '@MONO@',monospace; color:@MUTED@; margin:2px 0 12px; }
  .rb-leg i { display:inline-block; width:9px; height:9px; border-radius:2px; margin:0 5px 0 12px; vertical-align:-1px; }
  .rb-note { display:flex; gap:10px; border:1px solid rgba(255,255,255,.09); border-radius:10px; padding:10px 12px;
    margin-bottom:8px; font-size:12.5px; line-height:1.5; color:@INK2@; background:rgba(255,255,255,.02); }
  .rb-note b { color:@INK@; font-weight:600; }
  .rb-note.good { border-color:rgba(12,163,12,.35); }
  .rb-note button { font:11px '@MONO@',monospace; color:#86b6ef; background:none; border:1px solid rgba(57,135,229,.4);
    border-radius:7px; padding:2px 9px; cursor:pointer; margin-left:8px; }
  .rb-empty { border:1px dashed rgba(57,135,229,.4); border-radius:14px; padding:26px; color:@INK2@; line-height:1.7;
    font-size:13.5px; background:rgba(57,135,229,.04); }
  .rb-empty b { color:@INK@; }
  .rb-fee { display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; margin-top:4px; }
  .rb-fee label { font-size:11px; color:@MUTED@; }
  .rb-fee input { width:100%; margin-top:4px; }
  .rb-actions { display:flex; gap:8px; margin:6px 0 4px; }
  .rb-out h4 { margin:22px 0 10px; font:10.5px '@MONO@',monospace; letter-spacing:.18em; text-transform:uppercase;
    color:@MUTED@; font-weight:500; }
  @media (max-width: 980px) { .rb { grid-template-columns:1fr; } .rb-tiles { grid-template-columns:1fr 1fr; } }
"""

TEMPLATE = """<div class="rb" id="rb">
<div class="rb-in">
  <div class="rb-sec"><div class="mh">Amount to invest</div>
    <div class="rb-amt"><input id="rb-amt" type="number" min="0" step="any"><span class="rb-hint" id="rb-amt-pct"></span></div></div>
  <div class="rb-sec"><div class="mh">Target allocation</div>
    <div id="rb-targets"></div>
    <div class="rb-addrow"><button class="rb-mini" id="rb-add">＋ Add a target</button>
      <button class="rb-mini" id="rb-fill">Start from my current</button>
      <select id="rb-fdim"></select><button class="rb-mini" id="rb-clear">Clear</button></div>
    <div class="rb-sums" id="rb-sums"></div></div>
  <div class="rb-sec"><div class="mh">Options</div>
    <label class="rb-opt"><span>Trade at most</span><select id="rb-lines"></select><span>lines</span></label>
    <label class="rb-opt"><span>Round amounts to</span><select id="rb-step"></select></label>
    <label class="rb-chk"><input type="checkbox" id="rb-sell"><span>Allow selling overweight lines
      <small>only when a target is off by more than half a point. Untick lines below to keep them out of the plan.</small></span></label>
    <div class="mh" style="margin:14px 0 2px">Trading fees per order</div>
    <div class="rb-fee"><label>Fixed<input id="rb-ff" type="number" min="0" step="any" placeholder="0"></label>
      <label>Percent<input id="rb-fp" type="number" min="0" step="0.01" placeholder="0"></label>
      <label>Minimum<input id="rb-fm" type="number" min="0" step="any" placeholder="0"></label></div>
    <details><summary id="rb-usable"></summary>
      <div style="margin-top:8px"><button class="rb-mini" id="rb-all">All</button><button class="rb-mini" id="rb-none">None</button></div>
      <div class="rb-use" id="rb-uselist"></div></details></div>
</div>
<div class="rb-out" id="rb-out"></div>
</div>"""

JS = r"""
// ── Rebalance Assistant ─────────────────────────────────────────────────────
function initRebalance() {
  const H = RB.h, n = H.length;
  const DIM = {c: 'Country', r: 'Region', s: 'Sector', a: 'Asset class'};
  const V = H.map(h => h.v), T0 = V.reduce((a, b) => a + b, 0);
  const NEUTRAL = new Set(RB.neutral);
  const catsOf = d => RB.cats[d].filter(c => d === 'a' || !NEUTRAL.has(c));
  const expoCache = {};
  const expo = (d, k) => expoCache[d + '|' + k] || (expoCache[d + '|' + k] = H.map(h => (RB.x[d][h.i] || {})[k] || 0));
  const cur = (d, k) => expo(d, k).reduce((s, f, i) => s + f * V[i], 0) / T0;
  const pct = v => (100 * v).toFixed(1) + '%';
  const signed = v => (v < 0 ? '−' : '+') + fmt(Math.abs(v));
  const KEY = 'portfolioscan-rebalance';
  let S = {amount: Math.max(100, Math.round(T0 * 0.01 / 100) * 100), sell: false, lines: 5, step: 10, targets: [], off: [], fee: {flat: 0, pct: 0, min: 0}};
  try { Object.assign(S, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch (e) {}
  S.fee = Object.assign({flat: 0, pct: 0, min: 0}, S.fee || {});
  S.targets = (S.targets || []).filter(t => RB.cats[t.d] && RB.cats[t.d].includes(t.k));
  S.off = (S.off || []).filter(i => H.some(h => h.i === i));
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) {} };
  const $ = id => document.getElementById(id);
  const sum = a => a.reduce((s, x) => s + x, 0);

  // ── the optimiser ──
  // rows: [{a: share of each position in the target's category, tt: target as a fraction}]; v: position values.
  function project(zb, zs, hiB, hiS, C) {  // onto { sum(b) - sum(s) = C, 0 <= b <= hiB, 0 <= s <= hiS }
    const m = zb.length;
    let lo = Math.min(Math.min(...zb) - hiB, -Math.max(...zs)) - 1;
    let hi = Math.max(Math.max(...zb), ...hiS.map((h, i) => h - zs[i])) + 1;
    const g = l => { let t = 0; for (let i = 0; i < m; i++) t += Math.min(hiB, Math.max(0, zb[i] - l)) - Math.min(hiS[i], Math.max(0, zs[i] + l)); return t; };
    for (let k = 0; k < 40; k++) { const mid = (lo + hi) / 2; if (g(mid) > C) lo = mid; else hi = mid; }
    const l = (lo + hi) / 2;
    return [zb.map(z => Math.min(hiB, Math.max(0, z - l))), zs.map((z, i) => Math.min(hiS[i], Math.max(0, z + l)))];
  }
  function optimise(rows, v, C, idx, sell, iters) {
    const m = idx.length, N = sum(v) + C;
    const E = rows.map(r => sum(r.a.map((f, i) => f * v[i])));
    const hiS = idx.map(i => sell ? v[i] : 0), hiB = C + sum(hiS);
    const L = 2 * (2 / (N * N)) * sum(rows.map(r => sum(idx.map(i => r.a[i] * r.a[i])))) + 1e-12;
    const step = 1 / L, mu = (2 / N) * 0.005;
    let xb = new Array(m).fill(C / m), xs = new Array(m).fill(0), yb = xb, ys = xs, t = 1;
    for (let it = 0; it < iters; it++) {
      const w = rows.map((r, k) => (E[k] + sum(idx.map((i, j) => r.a[i] * (yb[j] - ys[j])))) / N - r.tt);
      const g = idx.map(i => (2 / N) * sum(rows.map((r, k) => w[k] * r.a[i])));
      const [nb, ns] = project(yb.map((y, j) => y - step * g[j]), ys.map((y, j) => y - step * (mu - g[j])), hiB, hiS, C);
      const t2 = (1 + Math.sqrt(1 + 4 * t * t)) / 2, q = (t - 1) / t2;
      yb = nb.map((b, j) => b + q * (b - xb[j])); ys = ns.map((s, j) => s + q * (s - xs[j]));
      xb = nb; xs = ns; t = t2;
    }
    return xb.map((b, j) => b - xs[j]);
  }
  function allocate(rows, v, C, usable, sell, lines, iters) {
    let idx = usable.slice(), x = optimise(rows, v, C, idx, sell, iters);
    for (let guard = 0; guard < 8; guard++) {
      const used = idx.map((i, j) => [i, Math.abs(x[j])]).filter(p => p[1] >= 0.5).sort((a, b) => b[1] - a[1]);
      if (used.length <= lines) break;
      idx = used.slice(0, Math.max(lines, Math.ceil(used.length * 0.6))).map(p => p[0]);
      x = optimise(rows, v, C, idx, sell, iters);
    }
    const out = new Array(n).fill(0);
    idx.forEach((i, j) => { if (Math.abs(x[j]) >= 0.5) out[i] = x[j]; });
    return out;
  }
  function snap(x, C, step, v) {  // multiples of `step`, still adding up to C (largest remainders)
    const idx = x.map((a, i) => i).filter(i => x[i] !== 0);
    if (!idx.length) return x;
    const u = idx.map(i => x[i] / step), base = u.map(Math.floor);
    let need = Math.round(C / step) - sum(base);
    const order = u.map((a, j) => [j, a - base[j]]).sort((a, b) => b[1] - a[1]);
    for (let k = 0; need !== 0 && k < 1000; k++) {
      const j = order[(need > 0 ? k : order.length - 1 - k) % order.length][0];
      base[j] += need > 0 ? 1 : -1; need += need > 0 ? -1 : 1;
    }
    const out = new Array(n).fill(0);
    idx.forEach((i, j) => { out[i] = Math.max(-v[i], base[j] * step); });
    const left = C - sum(out);  // C is not a multiple of the step: the rest goes on the largest buy
    if (Math.abs(left) >= 0.5) { const top = idx.reduce((a, i) => out[i] > out[a] ? i : a, idx[0]); out[top] += left; }
    return out.map(a => Math.abs(a) < 0.5 ? 0 : Math.round(a * 100) / 100);
  }
  const weights = (rows, v, x) => { const N = sum(v) + sum(x); return rows.map(r => sum(r.a.map((f, i) => f * (v[i] + x[i]))) / N); };
  const gaps = (rows, v, x) => weights(rows, v, x).map((w, k) => 100 * (w - rows[k].tt));  // in points
  const avgGap = g => g.length ? sum(g.map(Math.abs)) / g.length : 0;

  // ── inputs ──
  const usable = () => H.map((h, i) => i).filter(i => !S.off.includes(H[i].i));
  function rowsNow() {
    const seen = new Set();
    return S.targets.filter(t => { const k = t.d + '|' + t.k; if (seen.has(k)) return false; seen.add(k); return true; })
      .map(t => ({d: t.d, k: t.k, a: expo(t.d, t.k), tt: (+t.t || 0) / 100, label: DIM[t.d] + ' · ' + t.k}));
  }
  function drawTargets() {
    $('rb-targets').innerHTML = S.targets.map((t, i) => `<div class="rb-trow" data-i="${i}">
      <select class="rb-d">${Object.entries(DIM).map(([k, l]) => `<option value="${k}"${k === t.d ? ' selected' : ''}>${l}</option>`).join('')}</select>
      <select class="rb-k">${catsOf(t.d).map(c => `<option value="${esc(c)}"${c === t.k ? ' selected' : ''}>${esc(c)} · ${pct(cur(t.d, c))}</option>`).join('')}</select>
      <input class="rb-t" type="number" min="0" max="100" step="any" value="${t.t}"><span>%</span>
      <button class="rb-x" title="Remove">✕</button></div>`).join('') ||
      '<div class="rb-hint">No target yet. Add one, or start from your current allocation.</div>';
  }
  function drawSums() {
    const by = {};
    S.targets.forEach(t => by[t.d] = (by[t.d] || 0) + (+t.t || 0));
    $('rb-sums').innerHTML = Object.entries(by).map(([d, s]) => `<span class="rb-chip${s > 100.01 ? ' bad' : ''}">${DIM[d]}: ${s.toFixed(1)}%`
      + (s > 100.01 ? ' · over 100%' : s < 99.99 ? ` · ${(100 - s).toFixed(1)}% free` : '') + '</span>').join('');
  }
  function drawUse() {
    $('rb-usable').textContent = `Lines included in the plan: ${usable().length} of ${n}`;
    $('rb-uselist').innerHTML = H.map(h => `<label><input type="checkbox" data-i="${esc(h.i)}"${S.off.includes(h.i) ? '' : ' checked'}><span>${esc(h.n)}</span></label>`).join('');
  }
  $('rb-amt').value = S.amount;
  $('rb-lines').innerHTML = [1, 2, 3, 5, 8, 12, 99].map(k => `<option value="${k}"${k === +S.lines ? ' selected' : ''}>${k === 99 ? 'any number of' : k}</option>`).join('');
  $('rb-step').innerHTML = [1, 10, 50, 100].map(k => `<option value="${k}"${k === +S.step ? ' selected' : ''}>${k === 1 ? 'the unit' : fmt(k)}</option>`).join('');
  $('rb-sell').checked = !!S.sell;
  $('rb-ff').value = S.fee.flat || ''; $('rb-fp').value = S.fee.pct || ''; $('rb-fm').value = S.fee.min || '';
  $('rb-fdim').innerHTML = Object.entries(DIM).map(([k, l]) => `<option value="${k}">${l}</option>`).join('');
  drawTargets(); drawSums(); drawUse();

  // ── results ──
  let simToken = 0;
  function render() {
    save();
    const C = Math.max(0, +S.amount || 0), rows = rowsNow(), out = $('rb-out');
    $('rb-amt-pct').textContent = C ? `${(100 * C / T0).toFixed(2)}% of your portfolio` : '';
    drawSums();
    const use = usable();
    if (!rows.length) {
      out.innerHTML = `<div class="rb-empty"><b>Tell me where you want to be.</b><br>Add targets on the left, for example
        <b>60% United States</b>, <b>20% Europe</b>, <b>10% Health Care</b>. You can mix countries, regions, sectors and asset
        classes. I'll work out exactly what to buy with your next contribution to get closer, without selling anything
        unless you allow it.<div class="rb-actions"><button class="rb-mini" data-act="fill">Start from my current allocation</button></div></div>`;
      return;
    }
    if (!use.length) { out.innerHTML = '<div class="rb-empty">Every line is switched off. Tick at least one line you can buy.</div>'; return; }
    if (!C && !S.sell) { out.innerHTML = '<div class="rb-empty"><b>Enter an amount to invest</b> (or allow selling to rebalance without a new contribution).</div>'; return; }
    const lines = +S.lines, step = +S.step;
    const x = snap(allocate(rows, V, C, use, !!S.sell, lines, 260), C, step, V);
    const before = gaps(rows, V, new Array(n).fill(0)), after = gaps(rows, V, x), wBefore = rows.map(r => cur(r.d, r.k));
    const wAfter = weights(rows, V, x), N = T0 + sum(x);
    const buys = x.filter(a => a > 0), sells = x.filter(a => a < 0);
    const buyTot = sum(buys), sellTot = -sum(sells);
    const gb = avgGap(before), ga = avgGap(after);
    const feeOn = S.fee.flat > 0 || S.fee.pct > 0 || S.fee.min > 0;
    const feeFor = a => Math.max(S.fee.min, S.fee.flat + Math.abs(a) * S.fee.pct / 100);
    const fees = sum(x.filter(a => a !== 0).map(feeFor));
    const acts = x.map((a, i) => [i, a]).filter(p => p[1] !== 0).sort((a, b) => b[1] - a[1]);
    // why each line: the targets it feeds (buy) or trims (sell)
    const why = i => rows.map((r, k) => ({r, k, f: r.a[i]})).filter(o => o.f >= 0.15 && Math.abs(before[o.k]) > 0.2)
      .sort((a, b) => Math.abs(before[b.k]) * b.f - Math.abs(before[a.k]) * a.f).slice(0, 3);
    const plan = acts.map(([i, a]) => {
      const help = why(i).filter(o => a > 0 ? before[o.k] < 0 : before[o.k] > 0)
        .map(o => `<span class="rb-help${a < 0 ? ' dn' : ''}">${a > 0 ? '▲' : '▼'} ${esc(o.r.k)}</span>`).join('');
      return `<tr><td class="wr">${esc(H[i].n)}<div class="m isn">${esc(H[i].i)}</div></td>
        <td><span class="rb-pill ${a > 0 ? 'buy' : 'sell'}">${a > 0 ? 'Buy' : 'Sell'}</span></td>
        <td class="m ${a > 0 ? 'rb-buy' : 'rb-sell'}">${signed(a)}</td>
        <td class="m">${(100 * V[i] / T0).toFixed(1)}% → ${(100 * (V[i] + a) / N).toFixed(1)}%</td><td>${help}</td></tr>`;
    }).join('');
    const scale = Math.max(10, ...rows.map((r, k) => 100 * Math.max(r.tt, wBefore[k], wAfter[k]))) * 1.12;
    const bars = rows.map((r, k) => `<div class="rb-tr"><div class="top"><b style="font-weight:500">${esc(r.label)}</b>
      <span>${pct(wBefore[k])} → ${pct(wAfter[k])} · target ${pct(r.tt)} · ${after[k] >= 0 ? '+' : '−'}${Math.abs(after[k]).toFixed(1)} pt</span></div>
      <div class="rb-track"><div class="after" style="width:${100 * wAfter[k] / scale * 100}%"></div>
      <div class="now" style="left:${100 * wBefore[k] / scale * 100}%"></div><div class="goal" style="left:calc(${100 * r.tt / scale * 100}% - 1px)"></div></div></div>`).join('');

    // insights
    const notes = [];
    if (!sellTot && buyTot) notes.push(['good', '✓', `<b>No sale needed.</b> ${fmt(buyTot)} goes into ${buys.length} line${buys.length > 1 ? 's' : ''}.`]);
    if (sellTot) notes.push(['', '↘', `<b>${fmt(sellTot)} is sold</b> from ${sells.length} overweight line${sells.length > 1 ? 's' : ''}, because it closes a gap of more than half a point.`]);
    const over = rows.map((r, k) => ({r, k, need: r.tt > 0 ? sum(r.a.map((f, i) => f * V[i])) / r.tt - T0 : Infinity}))
      .filter(o => before[o.k] > 0.5).sort((a, b) => b.need - a.need)[0];
    if (!S.sell && over && over.need > C * 1.02 && isFinite(over.need)) {
      notes.push(['', '⏱', `<b>${esc(over.r.label)}</b> is ${before[over.k].toFixed(1)} pt above its target. Buying alone only brings it to
        its target once you have invested at least <b>${fmt(over.need)}</b> in total, so this month's ${fmt(C)} will not be enough.`]);
    }
    rows.forEach((r, k) => {
      const reach = Math.max(...use.map(i => r.a[i]));
      if (before[k] < -0.5 && reach < 0.1) notes.push(['', '⚠', `<b>${esc(r.label)}</b>: no line you can buy gives real exposure to it. Use
        “Test a position” on the main screen to try an ETF that does.`]);
    });
    const byDim = {}; S.targets.forEach(t => byDim[t.d] = (byDim[t.d] || 0) + (+t.t || 0));
    Object.entries(byDim).filter(([d, s]) => s > 100.01).forEach(([d, s]) => notes.push(['', '⚠', `<b>${DIM[d]} targets add up to ${s.toFixed(0)}%.</b> They cannot all be met.`]));
    if (rows.length < S.targets.length) notes.push(['', '⚠', 'Two targets are the same category: only the first one is used.']);
    if (!S.sell && ga > 0.5) {
      const alt = snap(allocate(rows, V, C, use, true, lines, 200), C, step, V), gAlt = avgGap(gaps(rows, V, alt)), sAlt = -sum(alt.filter(a => a < 0));
      if (sAlt > 0 && gAlt < ga - 0.3) notes.push(['', '↔', `<b>Selling would get closer:</b> ${ga.toFixed(1)} → ${gAlt.toFixed(1)} pt average gap, by selling ${fmt(sAlt)}.
        <button data-act="sell">Allow selling</button>`]);
    }
    if (feeOn && acts.length) {
      const volume = buyTot + sellTot;
      notes.push([fees > 0.01 * volume ? '' : 'good', '€', `<b>Trading fees about ${fmt(fees)}</b>, ${(100 * fees / Math.max(volume, 1)).toFixed(2)}% of the amount traded.`
        + (fees > 0.01 * volume && lines > 1 ? ' That is a lot for this size: try fewer lines, or a larger amount less often.' : '')]);
    }
    const monthsNote = '<div class="rb-note" id="rb-months"><span>⏳</span><div>Working out how many months this takes…</div></div>';

    out.innerHTML = `<div class="rb-tiles">
      <div class="rb-tile"><div class="k">You invest</div><div class="v">${fmt(C)}</div><div class="s">${(100 * C / T0).toFixed(2)}% of the portfolio</div></div>
      <div class="rb-tile${sellTot ? '' : ' ok'}"><div class="k">Sales</div><div class="v">${sellTot ? fmt(sellTot) : 'None'}</div><div class="s">${sellTot ? 'to fund better buys' : 'nothing to sell'}</div></div>
      <div class="rb-tile"><div class="k">Lines to trade</div><div class="v">${acts.length}</div><div class="s">${buys.length} buy${sells.length ? ' · ' + sells.length + ' sell' : ''}${feeOn ? ' · fees ' + fmt(fees) : ''}</div></div>
      <div class="rb-tile"><div class="k">Average gap</div><div class="v">${gb.toFixed(1)} → ${ga.toFixed(1)}</div><div class="s">points from your targets</div></div></div>
      ${notes.map(o => `<div class="rb-note ${o[0]}"><span>${o[1]}</span><div>${o[2]}</div></div>`).join('')}${monthsNote}
      <h4>Your plan</h4>
      <table class="rb-plan"><thead><tr><th>Line</th><th></th><th>Amount</th><th>Weight</th><th>Helps</th></tr></thead><tbody>${plan ||
        '<tr><td colspan="5" class="rb-hint">Nothing to trade with these settings.</td></tr>'}</tbody></table>
      <div class="rb-actions"><button class="rb-mini" data-act="csv">⤓ Export plan (CSV)</button><button class="rb-mini" data-act="copy">⧉ Copy</button></div>
      <h4>Targets, before and after</h4>
      <div class="rb-leg"><i style="background:#5598e7"></i>after this plan<i style="background:rgba(255,255,255,.4)"></i>today<i style="background:#fff"></i>target</div>
      ${bars}`;
    out._plan = acts.map(([i, a]) => ({line: H[i].n, isin: H[i].i, action: a > 0 ? 'Buy' : 'Sell', amount: Math.abs(a), fee: feeOn ? Math.round(100 * feeFor(a)) / 100 : 0}));

    // months to get within a point of every target, buying only (asynchronous, so typing stays smooth)
    const token = ++simToken;
    if (Math.max(...before.map(Math.abs)) <= 1) { setMonths(0); return; }
    let v = V.slice(), month = 0;
    const tick = () => {
      if (token !== simToken || !document.getElementById('rb-months')) return;
      month++;
      const add = allocate(rows, v, C, use, false, lines, 120);
      v = v.map((a, i) => a + add[i]);
      const worst = Math.max(...gaps(rows, v, new Array(n).fill(0)).map(Math.abs));
      if (worst <= 1) setMonths(month); else if (month >= 60) setMonths(null); else setTimeout(tick, 0);
    };
    if (C > 0) setTimeout(tick, 60); else setMonths(undefined);
  }
  function setMonths(m) {
    const el = document.getElementById('rb-months'); if (!el) return;
    el.querySelector('span').textContent = m === undefined ? '⏳' : m === null ? '⏳' : '⏳';
    el.querySelector('div').innerHTML = m === 0 ? '<b>You are already within one point of every target.</b>'
      : m === undefined ? 'Without a monthly contribution there is no timeline: only selling can move you toward the targets.'
      : m === null ? '<b>Not within 5 years</b> by buying the same amount each month: some targets are out of reach with this amount or these lines.'
      : `<b>About ${m} month${m > 1 ? 's' : ''}</b> of the same contribution to get within one point of every target, buying only
        (markets assumed flat).`;
  }
  let timer = null;
  const soon = () => { clearTimeout(timer); timer = setTimeout(render, 140); };

  // ── events ──
  const root = $('rb');
  const unused = d => catsOf(d).find(c => !S.targets.some(t => t.d === d && t.k === c)) || catsOf(d)[0];
  root.addEventListener('input', e => {
    if (e.target.id === 'rb-amt') S.amount = +e.target.value || 0;
    else if (e.target.id === 'rb-ff') S.fee.flat = +e.target.value || 0;
    else if (e.target.id === 'rb-fp') S.fee.pct = +e.target.value || 0;
    else if (e.target.id === 'rb-fm') S.fee.min = +e.target.value || 0;
    else if (e.target.classList.contains('rb-t')) S.targets[+e.target.closest('.rb-trow').dataset.i].t = +e.target.value || 0;
    else return;
    soon();
  });
  root.addEventListener('change', e => {
    const el = e.target;
    if (el.id === 'rb-lines') S.lines = +el.value;
    else if (el.id === 'rb-step') S.step = +el.value;
    else if (el.id === 'rb-sell') S.sell = el.checked;
    else if (el.classList.contains('rb-d')) { const t = S.targets[+el.closest('.rb-trow').dataset.i]; t.d = el.value; t.k = unused(t.d); drawTargets(); }
    else if (el.classList.contains('rb-k')) S.targets[+el.closest('.rb-trow').dataset.i].k = el.value;
    else if (el.closest('#rb-uselist')) { const i = el.dataset.i; S.off = el.checked ? S.off.filter(x => x !== i) : [...S.off, i]; drawUse(); }
    else return;
    render();
  });
  const fill = d => {
    S.targets = S.targets.filter(t => t.d !== d);
    catsOf(d).slice(0, 5).forEach(c => { const w = Math.round(1000 * cur(d, c)) / 10; if (w >= 0.5) S.targets.push({d, k: c, t: w}); });
    drawTargets(); render();
  };
  root.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    if (b.classList.contains('rb-x')) { S.targets.splice(+b.closest('.rb-trow').dataset.i, 1); drawTargets(); render(); }
    else if (b.id === 'rb-add') { S.targets.push({d: 'c', k: unused('c'), t: 10}); drawTargets(); render(); }
    else if (b.id === 'rb-fill') fill($('rb-fdim').value);
    else if (b.id === 'rb-clear') { S.targets = []; drawTargets(); render(); }
    else if (b.id === 'rb-all') { S.off = []; drawUse(); render(); }
    else if (b.id === 'rb-none') { S.off = H.map(h => h.i); drawUse(); render(); }
    else if (b.dataset.act === 'fill') fill('c');
    else if (b.dataset.act === 'sell') { S.sell = true; $('rb-sell').checked = true; render(); }
    else if (b.dataset.act === 'csv' || b.dataset.act === 'copy') {
      const plan = $('rb-out')._plan || [];
      const text = 'Line,ISIN,Action,Amount,Fee\n' + plan.map(p => `"${p.line.replace(/"/g, '""')}",${p.isin},${p.action},${p.amount},${p.fee}`).join('\n');
      if (b.dataset.act === 'csv') {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([text], {type: 'text/csv'})); a.download = 'rebalance-plan.csv'; a.click();
      } else if (navigator.clipboard) navigator.clipboard.writeText(text.replace(/,/g, '\t')).then(() => { b.textContent = '✓ Copied'; }, () => {});
    }
  });
  render();
}
"""
