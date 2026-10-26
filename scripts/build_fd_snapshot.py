"""Rebuild data/fd_equities.csv.gz, the embedded FinanceDatabase snapshot the app reads.

The app only needs ticker, name, country and sector per ISIN, so this keeps a slim extract (~1 MB) instead of
downloading the full FinanceDatabase (~100 MB in memory) at runtime. Run it every few months to refresh:

    pip install financedatabase
    python scripts/build_fd_snapshot.py
"""
import os
from pathlib import Path

# The polars wheel financedatabase depends on refuses to start on some CPUs without this.
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import financedatabase as fd  # noqa: E402
import pandas as pd  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "fd_equities.csv.gz"
COLS = ["isin", "symbol", "name", "country", "sector"]


def main():
    df = fd.Equities().select().reset_index()
    # Prefer the primary listing (symbols without an exchange suffix) when an ISIN has many.
    df["_secondary"] = df["symbol"].str.contains(".", regex=False)
    df = df.sort_values(["isin", "_secondary"])

    with_isin = df[df["isin"].notna()].drop_duplicates("isin")
    # Rows without an ISIN still help the OpenFIGI fallback, which looks sectors up by company name.
    known = set(with_isin["name"].str.lower().str.strip())
    no_isin = df[df["isin"].isna() & df["sector"].notna() & df["name"].notna()].copy()
    no_isin["_k"] = no_isin["name"].str.lower().str.strip()
    no_isin = no_isin[~no_isin["_k"].isin(known)].drop_duplicates("_k")

    snapshot = pd.concat([with_isin[COLS], no_isin[COLS]], ignore_index=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    snapshot.to_csv(OUT, index=False, compression="gzip")
    print(f"wrote {OUT} · {len(with_isin):,} ISINs + {len(no_isin):,} name-only rows · "
          f"{OUT.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
