"""Net-long holdings snapshot and horizon returns for open FIFO lots."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .disclosures import load_settings

HORIZONS = [1, 3, 5, 10, 20, 30]


def compute_open_holdings_top_n(
    trump: pd.DataFrame,
    all_lots: pd.DataFrame,
    price_cache: dict | None = None,
    top_n: int = 10,
) -> pd.DataFrame:
    """
    Top-N net-long tickers from open FIFO lots with horizon returns since earliest open buy.
    """
    del price_cache  # reserved for future MTM column
    if all_lots.empty or trump.empty:
        return pd.DataFrame()

    open_lots = all_lots[all_lots["match_status"] == "open"].copy()
    if open_lots.empty:
        return pd.DataFrame()

    open_lots["buy_date"] = pd.to_datetime(open_lots["buy_date"])
    trump = trump.copy()
    trump["transaction_date"] = pd.to_datetime(trump["transaction_date"])

    rows: list[dict[str, Any]] = []
    settings = load_settings()
    end = pd.Timestamp(settings["oge"].get("analysis_end_date", "2026-05-30")).normalize()

    for ticker, grp in open_lots.groupby("ticker", sort=False):
        buy_ids = grp["buy_trade_id"].astype(str).tolist()
        buy_trades = trump[trump["trade_id"].astype(str).isin(buy_ids)].copy()
        if buy_trades.empty:
            continue

        net_notional = float(buy_trades["notional"].sum())
        if net_notional <= 0:
            continue

        earliest = buy_trades.loc[buy_trades["transaction_date"].idxmin()]
        latest = buy_trades.loc[buy_trades["transaction_date"].idxmax()]
        entry = pd.Timestamp(earliest["transaction_date"]).normalize()
        days_held = int((end - entry).days)

        row: dict[str, Any] = {
            "ticker": ticker,
            "net_notional": net_notional,
            "n_open_lots": len(grp),
            "n_open_buys": len(buy_trades),
            "first_buy_date": entry.date(),
            "latest_buy_date": pd.Timestamp(latest["transaction_date"]).date(),
            "days_held": days_held,
            "status": "仍持有",
        }

        for h in HORIZONS:
            rc, pc = f"ret_{h}d", f"pnl_{h}d"
            if rc in earliest.index and pd.notna(earliest.get(rc)):
                row[rc] = float(earliest[rc])
            if pc in earliest.index and pd.notna(earliest.get(pc)):
                row[pc] = float(earliest[pc])

        rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values("net_notional", ascending=False).head(top_n).reset_index(drop=True)


def open_holdings_summary_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    recs = df.to_dict(orient="records")
    for r in recs:
        for k, v in list(r.items()):
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return recs
