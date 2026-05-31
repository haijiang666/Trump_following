"""FIFO lot matching for buy/sell holding periods."""

from __future__ import annotations

from collections import deque
from typing import Any

import pandas as pd


def _sort_key(row: pd.Series) -> tuple:
    action_rank = 0 if row["action"] == "purchase" else 1
    txn = row.get("txn_num")
    txn = int(txn) if pd.notna(txn) else 0
    return (pd.Timestamp(row["transaction_date"]), action_rank, txn, str(row.get("trade_id", "")))


def fifo_match_trades(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    FIFO match purchases to sales per ticker.

    Returns
    -------
    matched_lots : matched buy→sell pairs only
    ticker_summary : per-ticker stats
    trade_holding : holding_days on matched sales
    all_lots : matched + open + prior_position rows
    """
    df = trades.copy()
    df = df[df["ticker"].notna() & df["action"].isin(["purchase", "sale"])].copy()
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])

    lot_rows: list[dict[str, Any]] = []
    trade_holding: dict[str, float | None] = {}

    for ticker, grp in df.groupby("ticker", sort=False):
        ordered = grp.copy()
        ordered["_sort"] = ordered.apply(_sort_key, axis=1)
        ordered = ordered.sort_values("_sort").drop(columns="_sort")

        buy_queue: deque = deque()

        for _, row in ordered.iterrows():
            tid = str(row["trade_id"])
            if row["action"] == "purchase":
                buy_queue.append(row)
                trade_holding[tid] = None
                continue

            sell_date = pd.Timestamp(row["transaction_date"]).normalize()
            if buy_queue:
                buy = buy_queue.popleft()
                buy_date = pd.Timestamp(buy["transaction_date"]).normalize()
                days = int((sell_date - buy_date).days)
                lot_rows.append(
                    {
                        "ticker": ticker,
                        "buy_trade_id": buy["trade_id"],
                        "sell_trade_id": tid,
                        "buy_date": buy_date,
                        "sell_date": sell_date,
                        "holding_days": days,
                        "buy_amount_min": buy.get("amount_min"),
                        "sell_amount_min": row.get("amount_min"),
                        "match_status": "matched",
                    }
                )
                trade_holding[tid] = float(days)
            else:
                lot_rows.append(
                    {
                        "ticker": ticker,
                        "buy_trade_id": None,
                        "sell_trade_id": tid,
                        "buy_date": pd.NaT,
                        "sell_date": sell_date,
                        "holding_days": None,
                        "buy_amount_min": None,
                        "sell_amount_min": row.get("amount_min"),
                        "match_status": "prior_position",
                    }
                )
                trade_holding[tid] = None

        for buy in buy_queue:
            lot_rows.append(
                {
                    "ticker": ticker,
                    "buy_trade_id": buy["trade_id"],
                    "sell_trade_id": None,
                    "buy_date": pd.Timestamp(buy["transaction_date"]).normalize(),
                    "sell_date": pd.NaT,
                    "holding_days": None,
                    "buy_amount_min": buy.get("amount_min"),
                    "sell_amount_min": None,
                    "match_status": "open",
                }
            )

    all_lots = pd.DataFrame(lot_rows)
    if all_lots.empty:
        return all_lots, pd.DataFrame(), pd.DataFrame(columns=["trade_id", "holding_days"]), all_lots

    matched_lots = all_lots[all_lots["match_status"] == "matched"].reset_index(drop=True)

    summary_rows = []
    for ticker, sub in all_lots.groupby("ticker"):
        matched_sub = sub[sub["match_status"] == "matched"]
        hd = matched_sub["holding_days"].dropna()
        summary_rows.append(
            {
                "ticker": ticker,
                "n_matched_pairs": len(matched_sub),
                "n_open_buys": int((sub["match_status"] == "open").sum()),
                "n_prior_sells": int((sub["match_status"] == "prior_position").sum()),
                "avg_holding_days": float(hd.mean()) if len(hd) else None,
                "median_holding_days": float(hd.median()) if len(hd) else None,
                "min_holding_days": float(hd.min()) if len(hd) else None,
                "max_holding_days": float(hd.max()) if len(hd) else None,
            }
        )
    ticker_summary = pd.DataFrame(summary_rows).sort_values("n_matched_pairs", ascending=False)

    trade_holding_df = pd.DataFrame(
        [{"trade_id": k, "fifo_holding_days": v} for k, v in trade_holding.items()]
    )
    return matched_lots, ticker_summary, trade_holding_df, all_lots


def attach_holding_to_trades(trades: pd.DataFrame, trade_holding: pd.DataFrame) -> pd.DataFrame:
    """Add fifo_holding_days to trades (populated on matched sales)."""
    return trades.merge(trade_holding, on="trade_id", how="left")


def holding_summary_stats(matched_lots: pd.DataFrame) -> dict[str, float | int | None]:
    if matched_lots.empty:
        return {}
    hd = matched_lots["holding_days"].dropna()
    return {
        "n_matched_pairs": len(matched_lots),
        "median_holding_days": float(hd.median()),
        "mean_holding_days": float(hd.mean()),
        "n_tickers_with_pairs": int(matched_lots["ticker"].nunique()),
    }
