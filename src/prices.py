"""Fetch and cache price data via yfinance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from .disclosures import load_settings, project_root


def normalize_yf_ticker(ticker: str) -> str:
    return ticker.replace(".", "-").replace("/", "-")


def fetch_prices(
    ticker: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    settings: dict[str, Any] | None = None,
) -> pd.DataFrame:
    settings = settings or load_settings()
    root = project_root()
    cache_dir = root / settings["paths"]["prices"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    yf_ticker = normalize_yf_ticker(ticker)
    cache_path = cache_dir / f"{yf_ticker.replace('/', '_')}.parquet"

    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    if pd.isna(start) or pd.isna(end):
        return pd.DataFrame()
    if start > end:
        start, end = end - pd.Timedelta(days=30), end

    if cache_path.exists():
        cached = pd.read_parquet(cache_path)
        cached.index = pd.to_datetime(cached.index)
        c_start, c_end = cached.index.min(), cached.index.max()
        if c_start <= start and c_end >= end:
            return cached.loc[start:end]

    data = yf.download(
        yf_ticker,
        start=start - pd.Timedelta(days=5),
        end=end + pd.Timedelta(days=5),
        progress=False,
        auto_adjust=True,
    )
    if data.empty:
        return pd.DataFrame()

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    data.index = pd.to_datetime(data.index).tz_localize(None)
    data["ticker"] = ticker
    data["delisted"] = len(data) < 5

    if cache_path.exists():
        cached = pd.read_parquet(cache_path)
        cached.index = pd.to_datetime(cached.index)
        data = pd.concat([cached, data])
        data = data[~data.index.duplicated(keep="last")].sort_index()

    data.to_parquet(cache_path)
    return data.loc[start:end] if not data.empty else data


def fetch_prices_for_trades(trades: pd.DataFrame, settings: dict[str, Any] | None = None) -> dict[str, pd.DataFrame]:
    settings = settings or load_settings()
    pre = settings["price"]["pre_window_days"]
    post = settings["price"]["post_window_days"]
    result: dict[str, pd.DataFrame] = {}

    tickers = trades["ticker"].dropna().unique()
    for ticker in tickers:
        sub = trades[trades["ticker"] == ticker]
        start = sub["transaction_date"].min() - pd.Timedelta(days=pre)
        disc_max = sub["disclosure_date"].max()
        txn_max = sub["transaction_date"].max()
        anchor_max = max(d for d in [disc_max, txn_max] if pd.notna(d))
        # +60 calendar days covers 30 trading-day horizons after latest anchor
        end = anchor_max + pd.Timedelta(days=max(post, 60))
        result[ticker] = fetch_prices(ticker, start, end, settings)
    return result


def price_on_date(prices: pd.DataFrame, date: pd.Timestamp, col: str = "Close") -> float | None:
    if prices.empty or pd.isna(date):
        return None
    date = pd.Timestamp(date).normalize()
    idx = prices.index
    if date in idx:
        return float(prices.loc[date, col])
    future = idx[idx >= date]
    if len(future) == 0:
        return None
    return float(prices.loc[future[0], col])


def trading_day_on_or_after(prices: pd.DataFrame, date: pd.Timestamp) -> pd.Timestamp | None:
    if prices.empty or pd.isna(date):
        return None
    date = pd.Timestamp(date).normalize()
    idx = prices.index.sort_values()
    future = idx[idx >= date]
    return future[0] if len(future) else None


def price_on_trading_day_offset(
    prices: pd.DataFrame,
    date: pd.Timestamp,
    offset_days: int,
    col: str = "Close",
) -> float | None:
    """Price at close of the Nth trading day on/after `date` (offset_days=0 → event day)."""
    if prices.empty or pd.isna(date):
        return None
    idx = prices.index.sort_values()
    start = trading_day_on_or_after(prices, date)
    if start is None:
        return None
    pos = idx.get_loc(start)
    target_pos = pos + offset_days
    if target_pos >= len(idx):
        return None
    return float(prices.iloc[target_pos][col])
