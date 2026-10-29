"""Two looks at the whole portfolio, computed from the data the app already has:

- company exposure: how much of the portfolio sits in each company, adding the stocks held directly to the largest
  holdings of every fund (its top 10 on Yahoo Finance, or more from the update script's file);
- a health score out of 100, made of four rules that are each shown with the figure measured, the scale used and
  what would raise the score. Nothing is hidden: the thresholds below are the whole method.

No Streamlit here: app.py fetches the fund holdings and passes everything in.
"""
import collections
import re

# ── company exposure ─────────────────────────────────────────────────────────────────────────────────────────────

NOISE_SHARE = 0.003  # a name word found in more than this share of FinanceDatabase's company names ("inc", "corp"…)


def name_noise(company_names) -> set[str]:
    """The words so common at the end of company names that they say nothing about which company it is ("Inc",
    "Co Ltd", "Holdings", "Class A"…), learnt from FinanceDatabase's 45,000 names rather than listed by hand."""
    names = list(company_names)
    seen = collections.Counter(w for n in names for w in set(_words(n)))
    return {w for w, c in seen.items() if c / max(len(names), 1) > NOISE_SHARE}


def _words(name) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(name or "").lower().replace("&", " and "))


def company_key(name, noise: set[str]) -> tuple:
    """The words that identify a company: its name without the common words at its end ("Alphabet Inc Class A" and
    "Alphabet Inc." -> ("alphabet",)), nor a leading "the"."""
    w = _words(name)
    if w[:1] == ["the"]:
        w = w[1:]
    while len(w) > 1 and (w[-1] in noise or len(w[-1]) == 1):
        w.pop()
    return tuple(w)


def _same(a: tuple, b: tuple) -> bool:
    """Same company: same key, or one key starts the other and says enough on its own (two words, or one long word)."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return bool(short) and long_[:len(short)] == short and (len(short) > 1 or len(short[0]) >= 5)


def company_exposure(lines: list[dict], noise: set[str], top: int = 25) -> dict:
    """lines: one per position: {"v": value, "n": name, "direct": bool, "fund": fund name or None,
    "holdings": [(company name, weight 0-1)]} (a stock is its own single holding).
    -> {"rows": the `top` companies [{n, v, p, direct, funds: [[fund, value]]}], "covered": share of the funds' value
    whose holdings are known, "listed": share of the portfolio those known holdings account for}."""
    total = sum(x["v"] for x in lines) or 1.0
    groups = []  # [key, display names, value, direct value, {fund: value}]
    fund_v = known_v = listed_v = 0.0
    for x in lines:
        if not x["direct"]:
            fund_v += x["v"]
            if x["holdings"]:
                known_v += x["v"]
        for name, w in x["holdings"]:
            v = x["v"] * w
            listed_v += v
            key = company_key(name, noise)
            g = next((g for g in groups if _same(g[0], key)), None)
            if g is None:
                g = [key, collections.Counter(), 0.0, 0.0, collections.Counter()]
                groups.append(g)
            elif len(key) < len(g[0]):
                g[0] = key
            g[1][str(name)] += v
            g[2] += v
            if x["direct"]:
                g[3] += v
            else:
                g[4][x["fund"]] += v
    rows = []
    for key, names, v, direct, funds in sorted(groups, key=lambda g: -g[2])[:top]:
        # The plainest spelling: the shortest name seen ("Alphabet Inc." rather than "Alphabet Inc Class A").
        label = min(names, key=lambda n: (len(n), -names[n]))
        rows.append({"n": label, "v": round(v, 2), "p": round(100 * v / total, 3), "direct": round(direct, 2),
                     "funds": [[f, round(fv, 2)] for f, fv in funds.most_common()]})
    return {"rows": rows, "covered": round(100 * known_v / fund_v, 1) if fund_v else 100.0,
            "listed": round(100 * listed_v / total, 1)}


# ── health score ─────────────────────────────────────────────────────────────────────────────────────────────────
# Each rule gives 0 to 25 points, full points at or below `full`, none at or beyond `zero`, a straight line between.
RULES = {
    "geo": {"title": "Geographic spread", "full": 15, "zero": 75, "unit": "%",
            "measure": "Gap with the world index's country weights (active share)",
            "why": "A portfolio far from the world's mix bets on a few economies: one slowdown or currency move "
                   "weighs more."},
    "sector": {"title": "Sector balance", "full": 10, "zero": 60, "unit": "%",
               "measure": "Gap with the world index's sector weights (active share)",
               "why": "Sectors move in cycles; leaning heavily on a few makes the portfolio follow their ups and "
                      "downs."},
    "company": {"title": "Single-company risk", "full": 5, "zero": 25, "unit": "%",
                "measure": "Largest exposure to one company, through every stock and fund",
                "why": "One company's bad news should not be able to move the whole portfolio."},
    "cost": {"title": "Costs", "full": 0.25, "zero": 1.25, "unit": "% a year",
             "measure": "Fund fees (TER) over the whole portfolio, stocks counted at zero",
             "why": "Fees are taken every year whatever the market does, and compound over time."},
}
GRADES = [(80, "Well diversified"), (60, "Solid"), (40, "Concentrated"), (0, "Highly concentrated")]


def _points(value: float, full: float, zero: float) -> float:
    if value <= full:
        return 25.0
    if value >= zero:
        return 0.0
    return 25.0 * (zero - value) / (zero - full)


def _active_share(mine: dict, world: dict, neutral) -> tuple[float, str, float]:
    """Half the sum of the gaps between two breakdowns (real categories only, each rescaled to 100), and the largest
    gap: (active share in %, category, gap in points)."""
    a = {k: v for k, v in mine.items() if k not in neutral and v > 0}
    b = {k: v for k, v in world.items() if k not in neutral and v > 0}
    ta, tb = sum(a.values()) or 1, sum(b.values()) or 1
    gaps = {k: 100 * a.get(k, 0) / ta - 100 * b.get(k, 0) / tb for k in set(a) | set(b)}
    worst = max(gaps, key=lambda k: abs(gaps[k])) if gaps else ""
    return 0.5 * sum(abs(g) for g in gaps.values()), worst, gaps.get(worst, 0.0)


def health_score(countries: dict, sectors: dict, world: dict, companies: dict, fees: dict, neutral,
                 precision: float) -> dict:
    """countries / sectors: the portfolio's weights in %; world: {"name", "c", "s"} (empty when unknown);
    companies: company_exposure()'s result; fees: {"cost": % a year over the covered value, "covered": % of the
    portfolio with a known fee, "dearest": [name, ter] or None}; precision: % of the value with a known country and
    sector. -> {"score", "grade", "parts": [...], "confidence", "notes"}."""
    parts = []

    def part(key, value, shown, detail, tip):
        rule = RULES[key]
        pts = _points(value, rule["full"], rule["zero"])
        parts.append({"key": key, "title": rule["title"], "measure": rule["measure"], "why": rule["why"],
                      "value": shown, "detail": detail, "points": round(pts, 1),
                      "scale": f"25 points at {rule['full']:g}{rule['unit']} or less, 0 at {rule['zero']:g}"
                               f"{rule['unit']} or more, in proportion between",
                      "tip": tip if pts < 25 else ""})

    if world.get("c"):
        share, worst, gap = _active_share(countries, world["c"], neutral)
        part("geo", share, f"{share:.1f}%", f"Largest gap: {worst} {gap:+.1f} pt against {world['name']}.",
             f"Bringing {worst} closer to its world weight, for instance with a broad world ETF, would raise this.")
        share, worst, gap = _active_share(sectors, world["s"], neutral)
        part("sector", share, f"{share:.1f}%", f"Largest gap: {worst} {gap:+.1f} pt against {world['name']}.",
             f"A lighter tilt toward {worst}{' (overweight)' if gap > 0 else ' (underweight)'} would raise this.")
    top = companies["rows"][0] if companies["rows"] else None
    if top:
        via = len(top["funds"]) + (1 if top["direct"] else 0)
        part("company", top["p"], f"{top['p']:.1f}%",
             f"{top['n']}, held through {via} line{'s' if via > 1 else ''}.",
             f"Trimming the lines that hold {top['n']}, or adding unrelated ones, would raise this.")
    if fees["covered"] > 0:
        dear = fees["dearest"]
        part("cost", fees["cost"], f"{fees['cost']:.2f}% a year",
             f"Measured on the {fees['covered']:.0f}% of the portfolio with a known fee."
             + (f" Dearest line: {dear[0]} ({dear[1]:.2f}%)." if dear else ""),
             "Swapping the dearest funds for cheaper index ETFs on the same market would raise this.")

    score = round(sum(p["points"] for p in parts) * 100 / (25 * len(parts))) if parts else 0
    grade = next(label for floor, label in GRADES if score >= floor)
    coverage = {"precision": precision, "fees": fees["covered"], "companies": companies["covered"]}
    low = min(coverage.values())
    confidence = "High" if low >= 90 else "Medium" if low >= 60 else "Low"
    notes = [f"{len(parts)} of 4 rules could be measured, so the score is out of those."] if len(parts) < 4 else []
    return {"score": score, "grade": grade, "parts": parts, "confidence": confidence, "coverage": coverage,
            "notes": notes}
