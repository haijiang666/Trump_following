#!/usr/bin/env python3
"""Validate PDF parse coverage (target >= 90% on equity bulk filing)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.disclosures import load_settings
from src.equity_disclosures import enrich_equity_manifest, fetch_equity_disclosures
from src.equity_trades import parse_stats, parse_stats_all


def main() -> int:
    settings = load_settings()
    manifest = enrich_equity_manifest(fetch_equity_disclosures(settings), settings)
    ok = manifest[manifest["status"].astype(str).str.startswith("ok")]

    stats = parse_stats_all(ok, settings)
    rate = stats["parse_rate_vs_table"] * 100
    print(f"Filings: {stats['n_filings']}")
    print(f"Equity rows: {stats['equity_rows_after_filter']} / {stats['table_rows_in_pdf']} table rows ({rate:.1f}%)")

    equity_pdf = ROOT / "data/raw/disclosures/trump_278t_2026_05_08_equity.pdf"
    if equity_pdf.exists():
        row = ok[ok["doc_id"] == "trump_278t_2026_05_08_equity"]
        if len(row):
            eq_stats = parse_stats(equity_pdf, settings, manifest_row=row.iloc[0].to_dict())
            eq_rate = eq_stats["parse_rate_vs_table"] * 100
            print(f"Main equity PDF: {eq_stats['equity_rows_in_doc']} / {eq_stats['table_rows_in_pdf']} ({eq_rate:.1f}%)")
            print("PASS" if eq_rate >= 90 else "FAIL")
            return 0 if eq_rate >= 90 else 1

    print("PASS" if rate >= 90 else "FAIL")
    return 0 if rate >= 90 else 1


if __name__ == "__main__":
    raise SystemExit(main())
