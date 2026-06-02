"""Net-long holdings snapshot and horizon returns for open FIFO lots."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .disclosures import load_settings
from .holdings import _sort_key
from .prices import price_on_date
from .trade_returns import trade_notional

HORIZONS = [1, 3, 5, 10, 20, 30]


@dataclass
class _OpenLot:
    ticker: str
    notional: float
    entry_date: pd.Timestamp
    entry_price: float


def _trading_calendar(
    price_cache: dict[str, pd.DataFrame],
    tickers: set[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> list[pd.Timestamp]:
    for ref in ("VOO", "SPY"):
        prices = price_cache.get(ref)
        if prices is not None and not prices.empty:
            idx = pd.to_datetime(prices.index).normalize()
            mask = (idx >= start) & (idx <= end)
            days = sorted(idx[mask].unique())
            if days:
                return list(days)

    days: set[pd.Timestamp] = set()
    for ticker in tickers:
        prices = price_cache.get(ticker)
        if prices is None or prices.empty:
            continue
        idx = pd.to_datetime(prices.index).normalize()
        mask = (idx >= start) & (idx <= end)
        days.update(idx[mask])
    return sorted(days)


def _close_on_date(prices: pd.DataFrame, date: pd.Timestamp) -> float | None:
    """Close on trading day, else next session; if past last quote use last close."""
    px = price_on_date(prices, date)
    if px is not None:
        return px
    idx = pd.to_datetime(prices.index).normalize().sort_values()
    date = pd.Timestamp(date).normalize()
    past = idx[idx <= date]
    if len(past) == 0:
        return None
    return float(prices.loc[past[-1], "Close"])

def compute_portfolio_daily_timeseries(
    trades: pd.DataFrame,
    price_cache: dict[str, pd.DataFrame],
    settings: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """
    Daily gross-long portfolio: FIFO cost/MTM exposure and mark-to-market PnL.

    For each trading day (EOD):
    - position_cost: sum of OGE amount_min on open lots
    - position_mtm: sum of lot_notional * (close / entry_close)
    - daily_pnl: change in (realized + unrealized) vs prior trading day
    - cum_pnl: cumulative daily_pnl
    """
    settings = settings or load_settings()
    end = pd.Timestamp(settings["oge"].get("analysis_end_date", "2026-05-30")).normalize()

    df = trades.copy()
    df = df[df["ticker"].notna() & df["action"].isin(["purchase", "sale"])].copy()
    if df.empty:
        return pd.DataFrame()

    df["transaction_date"] = pd.to_datetime(df["transaction_date"]).dt.normalize()
    df = df[df["transaction_date"] <= end].copy()
    df["_sort"] = df.apply(_sort_key, axis=1)
    df = df.sort_values("_sort").drop(columns="_sort")

    tickers = set(df["ticker"].astype(str).unique())
    start = df["transaction_date"].min()
    calendar = _trading_calendar(price_cache, tickers, start, end)
    if not calendar:
        return pd.DataFrame()

    by_day: dict[pd.Timestamp, list[pd.Series]] = {}
    for _, row in df.iterrows():
        d = pd.Timestamp(row["transaction_date"]).normalize()
        by_day.setdefault(d, []).append(row)

    queues: dict[str, deque[_OpenLot]] = {t: deque() for t in tickers}
    realized_pnl = 0.0
    prev_total_pnl = 0.0
    rows: list[dict[str, Any]] = []

    for day in calendar:
        for row in by_day.get(day, []):
            ticker = str(row["ticker"])
            prices = price_cache.get(ticker)
            if prices is None or prices.empty:
                continue
            notional = trade_notional(row)
            if pd.isna(notional) or notional <= 0:
                continue

            if row["action"] == "purchase":
                entry_price = _close_on_date(prices, day)
                if not entry_price or entry_price <= 0:
                    continue
                queues.setdefault(ticker, deque()).append(
                    _OpenLot(ticker, float(notional), day, float(entry_price))
                )
                continue

            q = queues.get(ticker)
            if not q:
                continue
            lot = q.popleft()
            exit_price = _close_on_date(prices, day)
            if exit_price and lot.entry_price > 0:
                realized_pnl += lot.notional * (exit_price / lot.entry_price - 1.0)

        position_cost = 0.0
        position_mtm = 0.0
        unrealized_pnl = 0.0
        n_open_lots = 0
        held_tickers: set[str] = set()

        for ticker, q in queues.items():
            prices = price_cache.get(ticker)
            if prices is None or prices.empty:
                continue
            close = _close_on_date(prices, day)
            if not close or close <= 0:
                continue
            for lot in q:
                if lot.entry_price <= 0:
                    continue
                position_cost += lot.notional
                mtm = lot.notional * close / lot.entry_price
                position_mtm += mtm
                unrealized_pnl += lot.notional * (close / lot.entry_price - 1.0)
                n_open_lots += 1
                held_tickers.add(ticker)

        total_pnl = realized_pnl + unrealized_pnl
        daily_pnl = total_pnl - prev_total_pnl
        prev_total_pnl = total_pnl

        rows.append(
            {
                "date": day.date(),
                "position_cost": position_cost,
                "position_mtm": position_mtm,
                "n_open_lots": n_open_lots,
                "n_tickers": len(held_tickers),
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized_pnl,
                "total_pnl": total_pnl,
                "daily_pnl": daily_pnl,
                "cum_pnl": total_pnl,
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values("date").reset_index(drop=True)


def compute_ticker_daily_pnl(
    trades: pd.DataFrame,
    price_cache: dict[str, pd.DataFrame],
    settings: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Per-ticker daily and cumulative PnL on the same FIFO calendar as portfolio_daily."""
    settings = settings or load_settings()
    end = pd.Timestamp(settings["oge"].get("analysis_end_date", "2026-05-30")).normalize()

    df = trades.copy()
    df = df[df["ticker"].notna() & df["action"].isin(["purchase", "sale"])].copy()
    if df.empty:
        return pd.DataFrame()

    df["transaction_date"] = pd.to_datetime(df["transaction_date"]).dt.normalize()
    df = df[df["transaction_date"] <= end].copy()
    df["_sort"] = df.apply(_sort_key, axis=1)
    df = df.sort_values("_sort").drop(columns="_sort")

    tickers = set(df["ticker"].astype(str).unique())
    start = df["transaction_date"].min()
    calendar = _trading_calendar(price_cache, tickers, start, end)
    if not calendar:
        return pd.DataFrame()

    by_day: dict[pd.Timestamp, list[pd.Series]] = {}
    for _, row in df.iterrows():
        d = pd.Timestamp(row["transaction_date"]).normalize()
        by_day.setdefault(d, []).append(row)

    queues: dict[str, deque[_OpenLot]] = {t: deque() for t in tickers}
    realized: dict[str, float] = {t: 0.0 for t in tickers}
    prev_total: dict[str, float] = {t: 0.0 for t in tickers}
    rows: list[dict[str, Any]] = []

    for day in calendar:
        for row in by_day.get(day, []):
            ticker = str(row["ticker"])
            prices = price_cache.get(ticker)
            if prices is None or prices.empty:
                continue
            notional = trade_notional(row)
            if pd.isna(notional) or notional <= 0:
                continue

            if row["action"] == "purchase":
                entry_price = _close_on_date(prices, day)
                if not entry_price or entry_price <= 0:
                    continue
                queues.setdefault(ticker, deque()).append(
                    _OpenLot(ticker, float(notional), day, float(entry_price))
                )
                continue

            q = queues.get(ticker)
            if not q:
                continue
            lot = q.popleft()
            exit_price = _close_on_date(prices, day)
            if exit_price and lot.entry_price > 0:
                realized[ticker] = realized.get(ticker, 0.0) + lot.notional * (
                    exit_price / lot.entry_price - 1.0
                )

        active = set(queues.keys()) | set(realized.keys())
        for ticker in active:
            prices = price_cache.get(ticker)
            unrealized = 0.0
            if prices is not None and not prices.empty:
                close = _close_on_date(prices, day)
                if close and close > 0:
                    for lot in queues.get(ticker, deque()):
                        if lot.entry_price > 0:
                            unrealized += lot.notional * (close / lot.entry_price - 1.0)
            total = realized.get(ticker, 0.0) + unrealized
            daily = total - prev_total.get(ticker, 0.0)
            prev_total[ticker] = total
            rows.append(
                {
                    "date": day.date(),
                    "ticker": ticker,
                    "daily_pnl": daily,
                    "cum_pnl": total,
                }
            )

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["date", "ticker"]).reset_index(drop=True)


def portfolio_daily_summary_records(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {}
    last = df.iloc[-1]
    peak = df.loc[df["position_mtm"].idxmax()] if df["position_mtm"].notna().any() else last
    return {
        "last_date": str(last["date"]),
        "position_cost_end": float(last["position_cost"]),
        "position_mtm_end": float(last["position_mtm"]),
        "cum_pnl_end": float(last["cum_pnl"]),
        "peak_mtm_date": str(peak["date"]),
        "peak_mtm": float(peak["position_mtm"]),
        "n_days": len(df),
    }


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
