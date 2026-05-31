"""Returns computation, event study, and follow-strategy backtest."""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from .disclosures import load_settings
from .event_matching import align_events_to_trades
from .prices import fetch_prices, price_on_date, price_on_trading_day_offset, trading_day_on_or_after

RELEVANT_NEWS = re.compile(
    r"disclosure|278|stock|trade|trading|portfolio|equity|sec filing|financial disclosure",
    re.I,
)


def _naive_ts(ts: pd.Timestamp | Any) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("US/Eastern").tz_localize(None)
    return t.normalize()


def compute_returns(trades: pd.DataFrame, price_cache: dict[str, pd.DataFrame], settings: dict[str, Any] | None = None) -> pd.DataFrame:
    settings = settings or load_settings()
    follow_td = int(settings["backtest"].get("follow_delay_days", 1))
    post_windows = {"1d": follow_td, "5d": 5, "20d": 20}
    rows = []

    for _, trade in trades.iterrows():
        ticker = trade.get("ticker")
        if not ticker or ticker not in price_cache:
            continue
        prices = price_cache[ticker]
        txn = _naive_ts(trade["transaction_date"])
        disc = _naive_ts(trade["disclosure_date"]) if pd.notna(trade.get("disclosure_date")) else txn + pd.Timedelta(days=45)

        entry_px = price_on_date(prices, txn)
        disc_px = price_on_date(prices, disc)
        post = {k: price_on_trading_day_offset(prices, disc, n) for k, n in post_windows.items()}

        def ret(a, b):
            if a and b and a > 0:
                return (b - a) / a
            return np.nan

        sign = 1 if trade["action"] == "purchase" else -1
        reveal_lag = (disc - txn).days if pd.notna(disc) else np.nan

        rows.append(
            {
                **trade.to_dict(),
                "entry_price": entry_px,
                "disclosure_price": disc_px,
                "reveal_lag_days": reveal_lag,
                "return_to_disclosure": sign * ret(entry_px, disc_px),
                "return_post_disclosure_1d": sign * ret(disc_px, post["1d"]),
                "return_post_disclosure_5d": sign * ret(disc_px, post["5d"]),
                "return_post_disclosure_20d": sign * ret(disc_px, post["20d"]),
            }
        )

    return pd.DataFrame(rows)


def event_study_universe(returns_df: pd.DataFrame) -> pd.DataFrame:
    """One row per (ticker, disclosure_date) — avoid inflating sample with duplicate trades."""
    if returns_df.empty:
        return returns_df
    return returns_df.drop_duplicates(subset=["ticker", "disclosure_date"]).reset_index(drop=True)


def _trading_day_at_offset(prices: pd.DataFrame, anchor: pd.Timestamp, offset: int) -> pd.Timestamp | None:
    anchor = _naive_ts(anchor)
    start = trading_day_on_or_after(prices, anchor)
    if start is None:
        return None
    idx = prices.index.sort_values()
    pos = idx.get_loc(start) + offset
    if pos >= len(idx):
        return None
    return idx[pos]


def _market_model_ar(prices: pd.DataFrame, market: pd.DataFrame, event_date: pd.Timestamp, est: tuple[int, int], window: int) -> float | None:
    event_date = _naive_ts(event_date)
    ev_day = _trading_day_at_offset(prices, event_date, window)
    if ev_day is None:
        return None

    def daily_ret(df):
        return df["Close"].pct_change().dropna()

    rp = daily_ret(prices)
    rm = daily_ret(market)
    common = rp.index.intersection(rm.index)
    rp, rm = rp.loc[common], rm.loc[common]

    est_end_day = _trading_day_at_offset(prices, event_date, est[1])
    est_start_day = _trading_day_at_offset(prices, event_date, est[0])
    if est_start_day is None or est_end_day is None:
        return None
    est_idx = (rp.index >= est_start_day) & (rp.index <= est_end_day)
    if est_idx.sum() < 20:
        return None

    y = rp.loc[est_idx].values
    x = rm.loc[est_idx].values
    x = np.column_stack([np.ones(len(x)), x])
    beta = np.linalg.lstsq(x, y, rcond=None)[0]

    if ev_day not in rp.index or ev_day not in rm.index:
        return None

    expected = beta[0] + beta[1] * rm.loc[ev_day]
    actual = rp.loc[ev_day]
    return float(actual - expected)


def event_study(returns_df: pd.DataFrame, event_col: str = "disclosure_date", settings: dict[str, Any] | None = None) -> pd.DataFrame:
    settings = settings or load_settings()
    universe = event_study_universe(returns_df)
    if universe.empty or event_col not in universe.columns:
        return pd.DataFrame()

    est = settings["event_study"]["estimation_window"]
    windows = settings["event_study"]["event_windows"]
    bench = settings["price"]["benchmark_ticker"]

    start = universe["transaction_date"].min() - pd.Timedelta(days=200)
    disc = universe["disclosure_date"].dropna()
    if disc.empty:
        return pd.DataFrame()
    end = disc.max() + pd.Timedelta(days=30)
    market = fetch_prices(bench, start, end, settings)

    rows = []
    for _, row in universe.iterrows():
        ticker = row.get("ticker")
        if not ticker:
            continue
        prices = fetch_prices(ticker, start, end, settings)
        if prices.empty:
            continue
        event_date = _naive_ts(row[event_col]) if pd.notna(row.get(event_col)) else None
        if event_date is None:
            continue

        for w in windows:
            ar = _market_model_ar(prices, market, event_date, tuple(est), w)
            rows.append(
                {
                    "trade_id": row["trade_id"],
                    "ticker": ticker,
                    "event_date": event_date,
                    "event_window_day": w,
                    "abnormal_return": ar,
                    "study_type": "reveal" if event_col == "disclosure_date" else "social",
                }
            )

    return pd.DataFrame(rows)


def summarize_event_study(es_df: pd.DataFrame) -> pd.DataFrame:
    if es_df.empty:
        return pd.DataFrame()

    def pval(x):
        x = x.dropna()
        if len(x) <= 2:
            return np.nan
        return stats.ttest_1samp(x, 0).pvalue

    summary = es_df.groupby("event_window_day")["abnormal_return"].agg(["mean", "std", "count", pval])
    summary = summary.rename(columns={"pval": "p_value"})
    summary["sum_ar"] = summary["mean"] * summary["count"]
    return summary


def placebo_event_study(returns_df: pd.DataFrame, n_sim: int = 100, settings: dict[str, Any] | None = None) -> float:
    settings = settings or load_settings()
    real = event_study(returns_df, settings=settings)
    if real.empty:
        return np.nan

    placebo_means = []
    rng = np.random.default_rng(42)
    universe = event_study_universe(returns_df)
    for _ in range(n_sim):
        shuffled = universe.copy()
        dates = shuffled["disclosure_date"].dropna().values.copy()
        rng.shuffle(dates)
        mask = shuffled["disclosure_date"].notna()
        shuffled.loc[mask, "disclosure_date"] = dates[: mask.sum()]
        p = event_study(shuffled, settings=settings)
        if not p.empty:
            placebo_means.append(p["abnormal_return"].mean())

    return float(np.mean(placebo_means)) if placebo_means else np.nan


def backtest_follow_strategy(returns_df: pd.DataFrame, settings: dict[str, Any] | None = None) -> pd.DataFrame:
    """Per-trade follow returns; cum_return is equal-weight portfolio by disclosure date (not chained per trade)."""
    settings = settings or load_settings()
    cost = (settings["backtest"]["commission_bps"] + settings["backtest"]["slippage_bps"]) / 10000

    rows = []
    for _, row in returns_df.iterrows():
        ret = row.get("return_post_disclosure_1d")
        if pd.isna(ret):
            continue
        rows.append(
            {
                "trade_id": row["trade_id"],
                "ticker": row["ticker"],
                "action": row["action"],
                "disclosure_date": _naive_ts(row.get("disclosure_date")),
                "gross_return": ret,
                "net_return": ret - cost,
                "strategy": "reveal_follow",
            }
        )

    bt = pd.DataFrame(rows)
    if bt.empty:
        return bt

    daily = bt.groupby("disclosure_date")["net_return"].mean().sort_index()
    bt["cum_return"] = np.nan
    if len(daily):
        cum = (1 + daily).cumprod() - 1
        bt["cum_return"] = bt["disclosure_date"].map(cum)
    return bt


def backtest_metrics(bt: pd.DataFrame) -> dict[str, float]:
    if bt.empty:
        return {}
    r = bt["net_return"]
    daily = bt.groupby("disclosure_date")["net_return"].mean()
    port_cum = float((1 + daily).prod() - 1) if len(daily) else 0.0
    sharpe = r.mean() / r.std() * np.sqrt(252 / max(len(daily), 1)) if r.std() > 0 else 0
    if len(daily) > 1:
        port_curve = (1 + daily).cumprod()
        dd = float((port_curve / port_curve.cummax() - 1).min())
    else:
        dd = 0.0
    return {
        "mean_return_per_trade": float(r.mean()),
        "median_return_per_trade": float(r.median()),
        "portfolio_return_equal_weight": port_cum,
        "sharpe": float(sharpe),
        "max_drawdown": dd,
        "n_trades": len(bt),
        "n_disclosure_days": int(bt["disclosure_date"].nunique()),
        "win_rate": float((r > 0).mean()),
    }
