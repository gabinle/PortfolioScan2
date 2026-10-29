"""Export window: a PDF report, an Excel workbook or a CSV of the analysis, with the sections the user picks.

Everything is built in the browser from the data the cockpit frame already holds (positions, breakdowns, accounts and
the REPORT summary), so the export is instant and works offline once the libraries are cached: pdfmake draws a vector
PDF (tables, bars and the asset-class donut are real shapes, sharp in print) and SheetJS writes the workbook. Both are
loaded the first time they are needed only.
"""

PDFMAKE = ("https://cdnjs.cloudflare.com/ajax/libs/pdfmake/0.2.10/pdfmake.min.js",
           "https://cdnjs.cloudflare.com/ajax/libs/pdfmake/0.2.10/vfs_fonts.min.js")
SHEETJS = ("https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js",)

# (key, title, what it holds)
SECTIONS = [
    ("summary", "Key figures", "Total value, positions, precision of the analysis"),
    ("assets", "Asset classes", "Equities, bonds, real estate, cash…"),
    ("geo", "Countries & regions", "Exposure looked through every fund"),
    ("sectors", "Sectors", "The 11 GICS sectors, bonds and cash"),
    ("accounts", "Accounts", "Value, share and positions of each account"),
    ("metrics", "Concentration & diversification", "Largest lines, effective numbers"),
    ("holdings", "All positions", "Every line with its country, sector and weight"),
    ("quality", "Data quality", "Issues, excluded funds, precision"),
]
FORMATS = [("pdf", "PDF report", "A designed summary to share or print"),
           ("xlsx", "Excel workbook", "One sheet per section"),
           ("csv", "CSV", "Plain tables, one after another")]

TEMPLATE = ('<div class="xp" id="xp"><div class="mh">Format</div><div class="xp-fmt">'
            + "".join(f'<label><input type="radio" name="xpf" value="{k}"{" checked" if k == "pdf" else ""}>'
                      f'<div><b>{t}</b><span>{d}</span></div></label>' for k, t, d in FORMATS)
            + '</div><div class="mh xp-gap">Sections</div><div class="xp-secs">'
            + "".join(f'<label><input type="checkbox" data-sec="{k}" checked><div><b>{t}</b><span>{d}</span></div>'
                      '</label>' for k, t, d in SECTIONS)
            + '</div><div class="xp-foot"><button class="cta" id="xp-go">Download PDF report</button>'
              '<span id="xp-st"></span></div></div>')


def css(blue: str, ink: str, ink2: str, muted: str, sans: str) -> str:
    out = CSS
    for k, v in {"@BLUE@": blue, "@INK@": ink, "@INK2@": ink2, "@MUTED@": muted, "@SANS@": sans}.items():
        out = out.replace(k, v)
    return out


CSS = """
  .xp .xp-gap { margin-top:18px; }
  .xp-fmt { display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; }
  .xp-secs { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
  .xp label { display:flex; gap:10px; align-items:flex-start; padding:10px 12px; border-radius:10px; cursor:pointer;
    border:1px solid rgba(255,255,255,.09); background:rgba(255,255,255,.02); transition:border-color .15s, background .15s; }
  .xp label:hover { border-color:rgba(255,255,255,.18); }
  .xp label:has(input:checked) { border-color:rgba(57,135,229,.55); background:rgba(57,135,229,.08); }
  .xp input { accent-color:@BLUE@; margin:2px 0 0; }
  .xp label b { display:block; font:600 13px '@SANS@', sans-serif; color:@INK@; }
  .xp label span { display:block; font-size:11.5px; line-height:1.35; color:@MUTED@; margin-top:2px; }
  .xp-foot { display:flex; align-items:center; gap:14px; margin-top:18px; }
  .xp-foot .cta { width:auto; margin:0; padding:10px 18px; }
  .xp-foot .cta:disabled { opacity:.5; cursor:default; filter:none; }
  #xp-st { font-size:12px; color:@INK2@; }
  @media (max-width:640px) { .xp-fmt, .xp-secs { grid-template-columns:1fr; } }
"""

JS = r"""
// ── Export: a PDF report, an Excel workbook or a CSV of the analysis ─────────
const XP_PDFMAKE = @PDFMAKE@, XP_SHEETJS = @SHEETJS@;
const xpLoaded = {};
function xpLoad(urls) {  // load scripts one after the other, once each
  return urls.reduce((chain, u) => chain.then(() => xpLoaded[u] || (xpLoaded[u] = new Promise((ok, ko) => {
    const s = document.createElement('script');
    s.src = u; s.onload = ok;
    s.onerror = () => { delete xpLoaded[u]; ko(new Error('could not load ' + u.split('/').pop())); };
    document.head.appendChild(s);
  }))), Promise.resolve());
}
const xpLab = k => NLABEL[k] || k;
const xpMoney = v => Math.round(v).toLocaleString('en-US');
const xpPct = (v, d = 1) => (+v).toFixed(d) + '%';
const xpShort = v => v >= 1e9 ? (v / 1e9).toFixed(2) + 'B' : v >= 1e6 ? (v / 1e6).toFixed(2) + 'M'
  : v >= 1e3 ? (v / 1e3).toFixed(1) + 'k' : xpMoney(v);
const xpNeutral = k => NEUTRAL.includes(k);

// The metrics the report lists: [name, value, what it means]
function xpMetrics() {
  const R = REPORT;
  return [
    ['Largest position', `${R.top1[0]} · ${xpPct(R.top1[1], 2)}`, 'Share of the portfolio in its biggest line'],
    ['Top 5 positions', xpPct(R.top5), 'Share held by the five largest lines'],
    ['Top 10 positions', xpPct(R.top10), 'Share held by the ten largest lines'],
    ['Effective positions', R.eff_p.toFixed(1), 'As many equal lines would be this concentrated (1 / HHI)'],
    ['Effective countries', R.eff_c.toFixed(1), 'Geographic spread, looking through funds'],
    ['Effective sectors', R.eff_s.toFixed(1), 'Sector spread, looking through funds'],
  ];
}

// The selected sections as plain tables (Excel and CSV): [{title, head, rows}]
function xpTables(sel) {
  const R = REPORT, T = [];
  if (sel.summary) T.push({ title: 'Key figures', head: ['Figure', 'Value'], rows: [
    ['Portfolio', R.title], ['Date', R.date], ['Total value', Math.round(R.total)], ['Positions', R.n],
    ['Countries', R.countries], ['Sectors', R.sectors], ['Accounts', R.accounts],
    ['Positions resolved (%)', R.resolved], ['Precision of the analysis (%)', R.precision],
    ['Excluded funds', R.excluded]] });
  if (sel.assets) T.push({ title: 'Asset classes', head: ['Asset class', 'Weight (%)', 'Value'],
    rows: AW.map(([k, p]) => [xpLab(k), +p.toFixed(2), Math.round(p * TOTAL / 100)]) });
  if (sel.geo) {
    T.push({ title: 'Countries', head: ['Country', 'Weight (%)', 'Value'],
      rows: CW.map(([k, p]) => [xpLab(k), +p.toFixed(2), Math.round(p * TOTAL / 100)]) });
    T.push({ title: 'Regions', head: ['Region', 'Weight (%)', 'Value'],
      rows: RW.map(([k, p]) => [xpLab(k), +p.toFixed(2), Math.round(p * TOTAL / 100)]) });
  }
  if (sel.sectors) T.push({ title: 'Sectors', head: ['Sector', 'Weight (%)', 'Value'],
    rows: SW.map(([k, p]) => [xpLab(k), +p.toFixed(2), Math.round(p * TOTAL / 100)]) });
  if (sel.accounts) T.push({ title: 'Accounts', head: ['Account', 'Value', 'Weight (%)', 'Positions'],
    rows: ACC.map(a => [a.name, Math.round(a.v), +a.p.toFixed(2), a.n]) });
  if (sel.metrics) T.push({ title: 'Concentration', head: ['Metric', 'Value', 'Meaning'], rows: xpMetrics() });
  if (sel.holdings) T.push({ title: 'Positions',
    head: ['Name', 'ISIN', 'Ticker', 'Asset class', 'Country', 'Sector', 'Value', 'Weight (%)', 'Data sources'],
    rows: [...P].sort((a, b) => b.v - a.v).map(p => [p.n, p.i, p.t || '', p.a, p.c, p.s, Math.round(p.v),
      +p.p.toFixed(3), p.src || '']) });
  if (sel.quality) T.push({ title: 'Data quality', head: ['Position', 'ISIN', 'Issue', 'Weight (%)'],
    rows: R.issues.length ? R.issues.map(x => [x.n, x.i, x.issue, x.p]) : [['No data issue', '', '', '']] });
  return T;
}

function xpCsv(sel, name) {
  const q = v => `"${String(v).replace(/"/g, '""')}"`;
  const lines = [];
  xpTables(sel).forEach(t => { lines.push(q(t.title), t.head.map(q).join(','), ...t.rows.map(r => r.map(q).join(',')), ''); });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' }));
  a.download = name; a.click();
}

function xpXlsx(sel, name) {
  const wb = XLSX.utils.book_new();
  xpTables(sel).forEach(t => {
    const ws = XLSX.utils.aoa_to_sheet([t.head, ...t.rows]);
    ws['!cols'] = t.head.map((h, i) => ({ wch: Math.min(60, Math.max(10, ...[h, ...t.rows.map(r => r[i])]
      .map(v => String(v ?? '').length + 2))) }));
    XLSX.utils.book_append_sheet(wb, ws, t.title.slice(0, 31));
  });
  XLSX.writeFile(wb, name);
}

// ── the PDF ──
function xpPdf(sel) {
  const R = REPORT;
  const BLUE = '#3987e5', INK = '#18181b', MUT = '#71717a', LINE = '#e4e4e7', SOFT = '#f4f4f5', TRACK = '#eceef2';
  const GREY = '#b4b4bb', W = 515;  // A4 width minus the margins, in points
  const PALETTE = ['#2563eb', '#60a5fa', '#0d9488', '#f59e0b', '#8b5cf6', '#ec4899', '#a1a1aa'];
  const out = [];
  const heading = (title, sub) => ({ margin: [0, 20, 0, 10], stack: [
    { text: title, fontSize: 13, bold: true, color: INK },
    sub ? { text: sub, fontSize: 8.5, color: MUT, margin: [0, 2, 0, 0] } : { text: '' },
    { canvas: [{ type: 'line', x1: 0, y1: 5, x2: W, y2: 5, lineWidth: 0.6, lineColor: LINE }] }] });
  // A section: kept on one page with its heading (long tables only keep their heading with their first rows).
  const section = (title, sub, body, long = false) => (long ? [{ ...heading(title, sub), headlineLevel: 1 }, body]
    : [{ unbreakable: true, stack: [heading(title, sub), body] }]);
  const table = (head, rows, widths, right = [], size = 8.5) => ({
    fontSize: size, table: { headerRows: 1, widths, dontBreakRows: true,
      body: [head.map((h, i) => ({ text: h, bold: true, color: MUT, fontSize: size - 0.5, alignment: right.includes(i) ? 'right' : 'left' })),
             ...rows.map(r => r.map((c, i) => (typeof c === 'object' ? c
               : { text: String(c), alignment: right.includes(i) ? 'right' : 'left' })))] },
    layout: { fillColor: i => (i === 0 ? SOFT : i % 2 === 0 ? '#fafafa' : null), hLineWidth: i => (i === 1 ? 0.8 : 0),
              hLineColor: () => LINE, vLineWidth: () => 0, paddingTop: () => 4, paddingBottom: () => 4,
              paddingLeft: () => 6, paddingRight: () => 6 } });
  const bars = (list, width, n) => {  // [[key, pct]] -> label · bar · %
    const shown = list.filter(([, p]) => p >= 0.05);  // a 0.0% line says nothing
    const rows = shown.filter(([k]) => !xpNeutral(k)).slice(0, n).concat(shown.filter(([k]) => xpNeutral(k)));
    const max = Math.max(...rows.map(r => r[1]), 1e-9), bw = width - 150;
    return { table: { widths: [104, bw, 34], body: rows.map(([k, p]) => [
      { text: xpLab(k), fontSize: 8.5, color: xpNeutral(k) ? MUT : INK },
      { canvas: [{ type: 'rect', x: 0, y: 3.5, w: bw, h: 5, r: 2.5, color: TRACK },
                 { type: 'rect', x: 0, y: 3.5, w: Math.max(1.5, bw * p / max), h: 5, r: 2.5, color: xpNeutral(k) ? GREY : BLUE }] },
      { text: xpPct(p), fontSize: 8.5, alignment: 'right', color: INK }]) },
      layout: { hLineWidth: () => 0, vLineWidth: () => 0, paddingTop: () => 2.5, paddingBottom: () => 2.5,
                paddingLeft: () => 0, paddingRight: () => 6 } };
  };
  const donut = (list, size) => {  // an SVG ring, one arc per slice
    const c = size / 2, r = c - 1, ri = r * 0.6, tot = list.reduce((s, x) => s + x[1], 0) || 1;
    let a = -Math.PI / 2;
    const pt = (rad, ang) => `${(c + rad * Math.cos(ang)).toFixed(2)} ${(c + rad * Math.sin(ang)).toFixed(2)}`;
    const arcs = list.map(([, p], i) => {
      const span = Math.min(2 * Math.PI * p / tot, 2 * Math.PI - 1e-4), b = a + span, big = span > Math.PI ? 1 : 0;
      const d = `M ${pt(r, a)} A ${r} ${r} 0 ${big} 1 ${pt(r, b)} L ${pt(ri, b)} A ${ri} ${ri} 0 ${big} 0 ${pt(ri, a)} Z`;
      a = b;
      return `<path d="${d}" fill="${PALETTE[i % PALETTE.length]}" stroke="#ffffff" stroke-width="1.2"/>`;
    }).join('');
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">${arcs}</svg>`;
  };

  // Title band
  out.push({ table: { widths: ['*', 'auto'], body: [[
    { margin: [18, 18, 0, 18], stack: [
      { text: 'PORTFOLIOSCAN', color: '#8fb6ee', fontSize: 7.5, bold: true, characterSpacing: 1.6 },
      { text: 'Portfolio report', fontSize: 22, bold: true, color: '#ffffff', margin: [0, 6, 0, 3] },
      { text: `${R.title}  ·  ${R.date}`, color: '#a1a1aa', fontSize: 9 }] },
    { margin: [0, 22, 18, 18], stack: [
      { text: 'Total value', color: '#a1a1aa', fontSize: 8, alignment: 'right' },
      { text: xpMoney(R.total), fontSize: 22, bold: true, color: '#ffffff', alignment: 'right', margin: [0, 4, 0, 0] }] }]] },
    layout: { fillColor: () => '#111318', hLineWidth: () => 0, vLineWidth: () => 0 }, margin: [0, 0, 0, 6] });

  if (sel.summary) {
    const tile = (label, value, note) => ({ margin: [10, 9, 10, 9], stack: [
      { text: label, fontSize: 7.5, color: MUT },
      { text: value, fontSize: 16, bold: true, color: INK, margin: [0, 3, 0, 0] },
      note ? { text: note, fontSize: 7.5, color: MUT, margin: [0, 1, 0, 0] } : { text: '' }] });
    out.push({ margin: [0, 10, 0, 0], table: { widths: ['*', '*', '*'], body: [
      [tile('Positions', String(R.n)), tile('Countries', String(R.countries)), tile('Sectors', String(R.sectors))],
      [tile('Accounts', String(R.accounts)), tile('Precision', xpPct(R.precision), 'value with a known country and sector'),
       tile('Resolved', xpPct(R.resolved, 0), 'positions identified')]] },
      layout: { fillColor: () => SOFT, hLineWidth: () => 6, vLineWidth: () => 6, hLineColor: () => '#ffffff',
                vLineColor: () => '#ffffff', paddingLeft: () => 0, paddingRight: () => 0, paddingTop: () => 0, paddingBottom: () => 0 } });
  }
  if (sel.assets) {
    out.push(...section('Asset allocation', 'What the portfolio is made of', { columns: [
      { width: 130, svg: donut(AW, 120) },
      { width: '*', margin: [10, 8, 0, 0], table: { widths: [10, '*', 46, 70], body: AW.map(([k, p], i) => [
        { canvas: [{ type: 'rect', x: 0, y: 2.5, w: 7, h: 7, r: 2, color: PALETTE[i % PALETTE.length] }] },
        { text: xpLab(k), fontSize: 9.5, color: INK }, { text: xpPct(p), fontSize: 9.5, bold: true, alignment: 'right' },
        { text: xpMoney(p * TOTAL / 100), fontSize: 9, color: MUT, alignment: 'right' }]) },
        layout: { hLineWidth: (i, node) => (i > 0 && i < node.table.body.length ? 0.5 : 0), hLineColor: () => LINE,
                  vLineWidth: () => 0, paddingTop: () => 5, paddingBottom: () => 5 } }] }));
  }
  if (sel.geo) {
    out.push(...section('Geography', 'Exposure by country and region, looking through every fund', { columnGap: 16, columns: [
      { width: (W - 16) / 2, stack: [{ text: 'Countries', bold: true, fontSize: 9, margin: [0, 0, 0, 4] }, bars(CW, (W - 16) / 2, 15)] },
      { width: (W - 16) / 2, stack: [{ text: 'Regions', bold: true, fontSize: 9, margin: [0, 0, 0, 4] }, bars(RW, (W - 16) / 2, 15)] }] }));
  }
  if (sel.sectors) {
    out.push(...section('Sectors', 'GICS sectors; bonds and cash are shown apart, in grey', bars(SW, W, 15)));
  }
  if (sel.accounts) {
    out.push(...section('Accounts', `${ACC.length} account${ACC.length > 1 ? 's' : ''}`, table(['Account', 'Positions', 'Value', 'Share'],
      ACC.map(a => [a.name, a.n, xpMoney(a.v), xpPct(a.p)]), ['*', 60, 80, 50], [1, 2, 3], 9), ACC.length > 15));
  }
  if (sel.metrics) {
    out.push(...section('Concentration & diversification', 'How spread the portfolio is', table(['Metric', 'Value', 'Meaning'],
      xpMetrics().map(([m, v, d]) => [{ text: m, bold: true }, v, { text: d, color: MUT }]), [120, 150, '*'], [], 9)));
  }
  if (sel.holdings) {
    out.push({ ...heading('All positions', `${P.length} lines, largest first`), headlineLevel: 1,
               pageBreak: out.length > 1 ? 'before' : undefined });
    out.push(table(['Name', 'ISIN', 'Country', 'Sector', 'Asset class', 'Value', 'Weight'],
      [...P].sort((a, b) => b.v - a.v).map(p => [p.n, { text: p.i, color: MUT }, p.c, p.s, p.a, xpMoney(p.v), xpPct(p.p, 2)]),
      ['*', 62, 66, 76, 56, 50, 36], [5, 6], 7.5));
  }
  if (sel.quality) {
    const facts = [];
    if (R.excluded) facts.push(`${R.excluded} fund${R.excluded > 1 ? 's' : ''} excluded (${xpMoney(R.excluded_v)}): no public breakdown found.`);
    R.notes.forEach(n => facts.push(n));
    const quality = [facts.length ? { ul: facts, fontSize: 8.5, color: INK, margin: [0, 0, 0, 8] } : { text: '' },
      R.issues.length
        ? table(['Position', 'ISIN', 'Issue', 'Weight'], R.issues.map(x => [x.n, { text: x.i, color: MUT }, x.issue, xpPct(x.p, 2)]),
            [120, 62, '*', 40], [3], 8)
        : { text: 'No data issue: every position was found and mapped.', fontSize: 9, color: MUT }];
    out.push(...section('Data quality', `Precision ${xpPct(R.precision)} of the value: country and sector both known`,
      { stack: quality }, R.issues.length > 12));
  }

  return {
    pageSize: 'A4', pageMargins: [40, 46, 40, 44], info: { title: `PortfolioScan report · ${R.title}`, creator: 'PortfolioScan' },
    defaultStyle: { font: 'Roboto', fontSize: 9, color: INK, lineHeight: 1.15 },
    header: page => (page === 1 ? null : { margin: [40, 22, 40, 0], columns: [
      { text: 'PortfolioScan · Portfolio report', fontSize: 7.5, color: MUT },
      { text: R.title, fontSize: 7.5, color: MUT, alignment: 'right' }] }),
    footer: (page, pages) => ({ margin: [40, 16, 40, 0], columns: [
      { text: `Generated by PortfolioScan on ${R.date}. Breakdowns are estimates built from public data.`, fontSize: 7, color: GREY },
      { text: `${page} / ${pages}`, fontSize: 7.5, color: MUT, alignment: 'right' }] }),
    content: out,
    pageBreakBefore: (node, after) => node.headlineLevel === 1 && after.length < 3,
  };
}

function initExport() {
  const box = document.getElementById('xp'), go = document.getElementById('xp-go'), st = document.getElementById('xp-st');
  const KEY = 'portfolioscan-export';
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) {}
  box.querySelectorAll('[data-sec]').forEach(c => { if (saved.sec && c.dataset.sec in saved.sec) c.checked = saved.sec[c.dataset.sec]; });
  const keep = saved.fmt && box.querySelector(`[name=xpf][value="${saved.fmt}"]`);
  if (keep) keep.checked = true;
  const fmtOf = () => box.querySelector('[name=xpf]:checked').value;
  const pick = () => Object.fromEntries([...box.querySelectorAll('[data-sec]')].map(c => [c.dataset.sec, c.checked]));
  const refresh = () => {
    go.textContent = { pdf: 'Download PDF report', xlsx: 'Download Excel workbook', csv: 'Download CSV' }[fmtOf()];
    const any = Object.values(pick()).some(Boolean);
    go.disabled = !any;
    st.textContent = any ? '' : 'Pick at least one section.';
  };
  box.addEventListener('change', refresh);
  refresh();
  go.onclick = async () => {
    const fmt = fmtOf(), sel = pick();
    try { localStorage.setItem(KEY, JSON.stringify({ fmt, sec: sel })); } catch (e) {}
    go.disabled = true;
    st.textContent = fmt === 'csv' ? '' : 'Preparing…';
    const base = 'PortfolioScan report ' + new Date().toISOString().slice(0, 10);
    try {
      if (fmt === 'pdf') { await xpLoad(XP_PDFMAKE); pdfMake.createPdf(xpPdf(sel)).download(base + '.pdf'); }
      else if (fmt === 'xlsx') { await xpLoad(XP_SHEETJS); xpXlsx(sel, base + '.xlsx'); }
      else xpCsv(sel, base + '.csv');
      st.textContent = '✓ Downloaded';
    } catch (e) {
      st.textContent = 'Export failed: ' + e.message;
    }
    go.disabled = false;
  };
}
""".replace("@PDFMAKE@", str(list(PDFMAKE)).replace("'", '"')).replace("@SHEETJS@", str(list(SHEETJS)).replace("'", '"'))
