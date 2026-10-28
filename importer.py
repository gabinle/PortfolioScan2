"""Read broker / insurer exports (CSV or Excel, any layout) into ISIN + value positions.

No Streamlit here: app.py shows what `detect` guessed in a confirmation dialog and lets the user change it.

    tables = read_tables(name, data)          # one per Excel sheet, or the parsed CSV
    guess = detect(tables[0].raw)             # header row + code / value / name columns
    positions, ignored = extract(tables[0].raw, guess)
"""
import csv
import io
import re
from dataclasses import dataclass

import pandas as pd

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
SEPARATORS = {",": "comma  ,", ";": "semicolon  ;", "\t": "tab", "|": "pipe  |"}

# Header words that point at the position value, and at numeric columns that are not it.
VALUE_WORDS = ["montant en eur", "somme en compte", "valorisation", "market value", "valeur", "value", "montant",
               "amount", "total", "encours", "position", "solde", "balance", "eur"]
NOT_VALUE_WORDS = ["quantit", "quantity", "nombre", "nbre", "parts", "shares", "units", "cours", "cotation", "price",
                   "prix", "clôture", "cloture", "close", "pru", "revient", "cost", "perf", "%", "plus", "moins",
                   "gain", "loss", "date", "ticker", "frais", "fee", "variation", "change", "rate", "taux"]
ACCOUNT_WORDS = ["contrat", "contract", "compte", "account", "portefeuille", "portfolio", "enveloppe", "placement",
                 "wallet", "dépôt", "depot"]
NAME_WORDS = ["produit", "nom", "name", "libell", "support", "description", "security", "instrument", "titre",
              "valeur"]


@dataclass
class Table:
    label: str              # "Sheet1", or the file name for a CSV
    raw: pd.DataFrame       # every cell as text, no header applied
    sep: str | None = None  # CSV separator used, None for Excel


# ── numbers and codes ────────────────────────────────────────────────────────────────────────────────────────────

def parse_number(x) -> float | None:
    """'3 561,18 €', '1,234.56', '1.234,56', '- 111,61', 9367.3 -> float; None for anything else."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return None if pd.isna(x) else float(x)
    s = re.sub(r"[\s  '€$£]|EUR|USD|CHF|GBP", "", str(x), flags=re.I)
    if not s or "%" in s or not re.fullmatch(r"[-+]?[\d.,]+", s):
        return None
    if "," in s and "." in s:  # the last separator is the decimal one
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif s.count(",") == 1:
        s = s.replace(",", ".")
    elif s.count(",") > 1 or s.count(".") > 1:  # thousands separators only
        s = s.replace(",", "").replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def special_code(code: str, name: str = "") -> str | None:
    """Pseudo-code for a cash / euro fund / SCPI line, from its own code (SCPI00004799, SUR000001730, FONDSEURNG,
    NETIS075, CASH) or, when it has none, from its name ("CASH & CASH FUND", "Fonds en euros …", "SCPI …")."""
    c = str(code or "").strip().upper()
    if re.search(r"\s", c):  # codes have no spaces: "Fonds Actions" is a category, not a code
        c = ""
    if c.startswith("SCPI"):
        return c
    if c.startswith(("CASH", "LIQUIDIT", "ESPECE", "ESPÈCE")):
        return "CASH"
    if re.match(r"^(FONDS|SUR\d|NETIS|EURO)", c):  # no real ISIN starts with SU, and FONDS/EURO are not codes
        return c
    n = str(name or "").strip().lower()
    if re.search(r"\bcash\b|liquidit|espèces|especes", n):
        return "CASH"
    if re.search(r"fonds? (en )?euros?\b|fonds euro", n):
        return "FONDSEURO-" + re.sub(r"[^A-Z0-9]", "", n.upper())[:20]
    if n.startswith("scpi"):
        return "SCPI-" + re.sub(r"[^A-Z0-9]", "", n.upper())[4:24]
    return None


def is_code(x) -> bool:
    c = str(x or "").strip().upper()
    return bool(ISIN_RE.match(c)) or special_code(c) is not None


# ── reading ──────────────────────────────────────────────────────────────────────────────────────────────────────

def is_excel(data: bytes) -> bool:
    return data[:2] == b"PK" or data[:4] == b"\xd0\xcf\x11\xe0"  # .xlsx is a zip, .xls an OLE file


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def sniff_separator(text: str) -> str:
    sample = "\n".join(text.splitlines()[:50])
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(SEPARATORS)).delimiter
    except csv.Error:
        counts = {s: sample.count(s) for s in SEPARATORS}
        return max(counts, key=counts.get) if max(counts.values()) else ","


def parse_text(text: str, sep: str) -> pd.DataFrame:
    rows = list(csv.reader(io.StringIO(text), delimiter=sep))
    width = max((len(r) for r in rows), default=0)
    fixed = []
    for r in rows:
        # Degiro wraps some whole lines in quotes ("CASH,,,,EUR,""9367,32"",…"): one cell that is itself a row.
        if len(r) == 1 and width > 1 and sep in r[0]:
            r = next(csv.reader([r[0]], delimiter=sep))
        fixed.append(r + [""] * (width - len(r)))
    raw = pd.DataFrame(fixed, dtype=str).fillna("")
    return raw.loc[~(raw.apply(lambda c: c.str.strip()) == "").all(axis=1)].reset_index(drop=True)


def read_tables(name: str, data: bytes, sep: str | None = None) -> list[Table]:
    """Every table in the file: one per non-empty Excel sheet, or the CSV parsed with `sep` (guessed if None)."""
    if is_excel(data):
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=str)
        out = []
        for label, raw in sheets.items():
            raw = raw.fillna("").astype(str)
            raw = raw.loc[~(raw.apply(lambda c: c.str.strip()) == "").all(axis=1)]
            raw = raw.loc[:, ~(raw.apply(lambda c: c.str.strip()) == "").all(axis=0)]
            if not raw.empty:
                out.append(Table(str(label), raw.reset_index(drop=True).T.reset_index(drop=True).T))
        return out
    text = decode(data)
    sep = sep or sniff_separator(text)
    return [Table(name, parse_text(text, sep), sep)]


# ── detection ────────────────────────────────────────────────────────────────────────────────────────────────────

def _score_header(cell: str, good: list[str], bad: list[str]) -> int:
    h = cell.strip().lower()
    if not h:
        return 0
    for i, w in enumerate(good):
        if w in h and not any(b in h for b in bad):
            return len(good) - i  # earlier words in the list are more specific
    return -5 if any(b in h for b in bad) else 0


def detect(raw: pd.DataFrame) -> dict:
    """Best guess of {"header": row index or None, "code": col, "value": col, "name": col or None}."""
    ncols = raw.shape[1]
    codes = raw.apply(lambda c: c.map(is_code))
    isins = raw.apply(lambda c: c.str.strip().str.upper().str.match(ISIN_RE))
    score = codes.sum() + isins.sum()  # real ISINs count double
    code_col = int(score.idxmax()) if codes.values.any() else 0
    hits = codes[code_col]
    first = int(hits.idxmax()) if hits.any() else 0
    # Header: the nearest all-text row above the first code (a cash line may sit in between, as in Degiro).
    header = None
    for r in range(first - 1, -1, -1):
        cand = raw.iloc[r]
        filled = [v for v in cand if v.strip()]
        if len(filled) >= 2 and not any(parse_number(v) is not None or is_code(v) for v in filled):
            header = r
            break
    heads = raw.iloc[header] if header is not None else pd.Series([""] * ncols)
    body = raw.iloc[(header + 1) if header is not None else 0:]
    body = body[body.apply(lambda r: any(is_code(v) for v in r), axis=1)] if hits.any() else body

    best, value_col = None, None
    for c in range(ncols):
        if c == code_col:
            continue
        nums = body[c].map(parse_number)
        share = nums.notna().mean() if len(body) else 0
        if share < 0.5:
            continue
        # Header words first; then the column with the largest total (values dwarf prices and quantities).
        score = (_score_header(heads[c], VALUE_WORDS, NOT_VALUE_WORDS), share >= 0.9, nums.abs().sum())
        if best is None or score > best:
            best, value_col = score, c

    name_col, best_name = None, None
    for c in range(ncols):
        if c in (code_col, value_col):
            continue
        cells = body[c].str.strip()
        texty = ((cells != "") & cells.map(lambda v: parse_number(v) is None and not is_code(v))).mean()
        if texty < 0.5:
            continue
        # A name-like header, then the most varied column: "Nom du support" beats the contract column "Produit".
        score = (_score_header(heads[c], NAME_WORDS, []) > 0, cells.nunique(), cells.str.len().mean())
        if best_name is None or score > best_name:
            best_name, name_col = score, c
    # Account: a column whose header says so, with few distinct values; the most detailed one wins
    # (Linxea: "N° Contrat" splits the lines finer than "Placement").
    account_col, best_acc = None, None
    for c in range(ncols):
        if c in (code_col, value_col, name_col) or _score_header(heads[c], ACCOUNT_WORDS, []) <= 0:
            continue
        cells = body[c].str.strip()
        n = cells[cells != ""].nunique()
        if 1 <= n <= 30 and (best_acc is None or n > best_acc):
            best_acc, account_col = n, c
    # Account label: a column that is constant within each account and tells them apart better than a number
    # (Linxea: "Produit" turns 940310942 into "Linxea Spirit PER · 940310942").
    label_col, best_label = None, None
    if account_col is not None:
        acct = body[account_col].str.strip()
        for c in range(ncols):
            if c in (code_col, value_col, name_col, account_col):
                continue
            cells = body[c].str.strip()
            if (cells == "").any() or cells.map(lambda v: parse_number(v) is not None).any():
                continue
            if cells.groupby(acct).nunique().max() == 1 and cells.nunique() > 1:
                if best_label is None or cells.nunique() > best_label:
                    best_label, label_col = cells.nunique(), c
    return {"header": header, "code": code_col, "value": value_col if value_col is not None else min(1, ncols - 1),
            "name": name_col, "account": account_col, "account_label": label_col}


def column_labels(raw: pd.DataFrame, header: int | None) -> list[str]:
    """'A · ISIN', 'B · Montant en EUR', … (letter plus the header text, if any)."""
    out = []
    for c in range(raw.shape[1]):
        letter = ""
        n = c
        while True:
            letter = chr(65 + n % 26) + letter
            n = n // 26 - 1
            if n < 0:
                break
        head = str(raw.iat[header, c]).strip() if header is not None else ""
        out.append(f"{letter} · {head}" if head else letter)
    return out


# ── extraction ───────────────────────────────────────────────────────────────────────────────────────────────────

def extract(raw: pd.DataFrame, m: dict, account: str = "") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Positions (isin, value, name, kind, account) and the ignored rows (with a reason), per the mapping `m`.
    `account` names the lines whose account column is empty, or all of them when there is no such column."""
    body = raw.iloc[(m["header"] + 1) if m["header"] is not None else 0:]
    keep, ignored = [], []
    for i, r in body.iterrows():
        code = str(r[m["code"]]).strip().upper()
        name = str(r[m["name"]]).strip() if m.get("name") is not None else ""
        acct = (str(r[m["account"]]).strip() if m.get("account") is not None else "") or account or "Portfolio"
        if m.get("account") is not None and m.get("account_label") is not None and str(r[m["account_label"]]).strip():
            acct = f"{str(r[m['account_label']]).strip()} · {acct}"
        value = parse_number(r[m["value"]])
        # The name only classifies lines without a real ISIN (a money-market ETF called "… Cash" stays an ETF).
        special = special_code(code) or (None if ISIN_RE.match(code) else special_code("", name))
        isin = code if ISIN_RE.match(code) and not special else special
        line = " | ".join(v for v in r.astype(str).str.strip() if v)[:120]
        if not isin:
            ignored.append({"row": i + 1, "content": line, "reason": "no ISIN or recognised code"})
        elif value is None:
            ignored.append({"row": i + 1, "content": line, "reason": "no numeric value"})
        elif value == 0:
            ignored.append({"row": i + 1, "content": line, "reason": "value is 0"})
        else:
            kind = ("cash" if isin == "CASH" else "SCPI" if isin.startswith("SCPI") else
                    "euro fund" if special else "ISIN")
            keep.append({"isin": isin, "value": value, "name": name, "kind": kind, "account": acct})
    return (pd.DataFrame(keep, columns=["isin", "value", "name", "kind", "account"]),
            pd.DataFrame(ignored, columns=["row", "content", "reason"]))
