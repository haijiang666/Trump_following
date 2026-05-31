"""Notional-weighted returns: Trump timing vs follow-on-disclosure."""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd

from .disclosures import load_settings
from .prices import price_on_date, price_on_trading_day_offset

HORIZONS = [1, 3, 5, 10, 20, 30]
AnchorCol = Literal["transaction_date", "disclosure_date"]


def _naive_ts(ts: pd.Timestamp | Any) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("US/Eastern").tz_localize(None)
    return t.normalize()


# OGE max bracket floor is $100,000,001; reject obvious OCR garbage above $1B
MAX_SANE_NOTIONAL = 1_000_000_001


def trade_notional(row: pd.Series) -> float:
    """OGE bracket lower bound (amount_min); skip OCR-inflated values."""
    lo = row.get("amount_min")
    if pd.notna(lo):
        v = float(lo)
        if 0 < v < MAX_SANE_NOTIONAL:
            return v
    hi = row.get("amount_max")
    if pd.notna(hi):
        v = float(hi)
        if 0 < v < MAX_SANE_NOTIONAL:
            return v
    return np.nan


def _action_sign(action: str) -> float:
    return 1.0 if action == "purchase" else -1.0


def compute_horizon_returns(
    trades: pd.DataFrame,
    price_cache: dict[str, pd.DataFrame],
    anchor_col: AnchorCol,
    horizons: list[int] | None = None,
) -> pd.DataFrame:
    """Per-trade signed returns and PnL at multiple trading-day horizons from anchor date."""
    horizons = horizons or HORIZONS
    rows: list[dict[str, Any]] = []

    for _, trade in trades.iterrows():
        ticker = trade.get("ticker")
        if not ticker or ticker not in price_cache:
            continue
        anchor = trade.get(anchor_col)
        if pd.isna(anchor):
            continue

        prices = price_cache[ticker]
        anchor_dt = _naive_ts(anchor)
        p0 = price_on_date(prices, anchor_dt)
        if not p0 or p0 <= 0:
            continue

        sign = _action_sign(str(trade["action"]))
        notional = trade_notional(trade)
        if pd.isna(notional) or notional <= 0:
            continue

        row: dict[str, Any] = {
            **trade.to_dict(),
            "anchor_date": anchor_dt,
            "anchor_type": anchor_col,
            "notional": notional,
            "anchor_price": p0,
            "action_sign": sign,
        }

        for h in horizons:
            ph = price_on_trading_day_offset(prices, anchor_dt, h)
            if ph is None or ph <= 0:
                row[f"ret_{h}d"] = np.nan
                row[f"pnl_{h}d"] = np.nan
                continue
            raw = (ph - p0) / p0
            signed = sign * raw
            row[f"ret_{h}d"] = signed
            row[f"pnl_{h}d"] = notional * signed

        rows.append(row)

    return pd.DataFrame(rows)


def notional_weighted_summary(df: pd.DataFrame, horizons: list[int] | None = None) -> pd.DataFrame:
    """Sum(PnL) / Sum(notional) per horizon."""
    horizons = horizons or HORIZONS
    rows = []
    total_notional = df["notional"].sum()
    for h in horizons:
        pnl_col = f"pnl_{h}d"
        ret_col = f"ret_{h}d"
        valid = df[df[pnl_col].notna()]
        pnl_sum = valid[pnl_col].sum()
        notional_sum = valid["notional"].sum()
        rows.append(
            {
                "horizon_days": h,
                "n_trades": len(valid),
                "total_notional": notional_sum,
                "total_pnl": pnl_sum,
                "notional_weighted_return": pnl_sum / notional_sum if notional_sum > 0 else np.nan,
                "equal_weight_mean_return": valid[ret_col].mean() if len(valid) else np.nan,
            }
        )
    out = pd.DataFrame(rows)
    out["pct_of_all_notional"] = out["total_notional"] / total_notional if total_notional > 0 else np.nan
    return out


def cumulative_pnl_by_date(
    df: pd.DataFrame,
    horizons: list[int] | None = None,
    date_col: str = "anchor_date",
) -> pd.DataFrame:
    """Daily summed PnL then cumulative sum per horizon."""
    horizons = horizons or HORIZONS
    base = df[[date_col, "notional"] + [f"pnl_{h}d" for h in horizons]].copy()
    base[date_col] = pd.to_datetime(base[date_col]).dt.normalize()
    daily = base.groupby(date_col).sum(numeric_only=True).sort_index()

    out = daily[["notional"]].rename(columns={"notional": "daily_notional"})
    for h in horizons:
        col = f"pnl_{h}d"
        if col in daily.columns:
            out[f"daily_pnl_{h}d"] = daily[col]
            out[f"cum_pnl_{h}d"] = daily[col].cumsum()
    return out.reset_index().rename(columns={date_col: "date"})


def _lot_notional(buy_min: float | None, sell_min: float | None) -> float:
    vals = []
    for v in (buy_min, sell_min):
        if pd.notna(v) and 0 < float(v) < MAX_SANE_NOTIONAL:
            vals.append(float(v))
    if not vals:
        return np.nan
    return min(vals)


def compute_realized_fifo(
    matched_lots: pd.DataFrame,
    trades: pd.DataFrame,
    price_cache: dict[str, pd.DataFrame],
    settings: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Realized PnL on FIFO matched buy→sell lots using market entry/exit closes.
    Open buys marked to analysis_end_date (unrealized).
    """
    settings = settings or load_settings()
    end = pd.Timestamp(settings["oge"].get("analysis_end_date", "2026-05-30"))
    trade_idx = trades.set_index("trade_id") if "trade_id" in trades.columns else trades

    rows: list[dict[str, Any]] = []
    for _, lot in matched_lots.iterrows():
        status = lot.get("match_status")
        ticker = str(lot["ticker"])
        if ticker not in price_cache:
            continue
        prices = price_cache[ticker]

        if status == "matched":
            buy_d = _naive_ts(lot["buy_date"])
            sell_d = _naive_ts(lot["sell_date"])
            p_entry = price_on_date(prices, buy_d)
            p_exit = price_on_date(prices, sell_d)
            if not p_entry or not p_exit or p_entry <= 0:
                continue
            notional = _lot_notional(lot.get("buy_amount_min"), lot.get("sell_amount_min"))
            if pd.isna(notional):
                continue
            ret = (p_exit - p_entry) / p_entry
            rows.append(
                {
                    "ticker": ticker,
                    "status": "realized",
                    "entry_date": buy_d.date(),
                    "exit_date": sell_d.date(),
                    "holding_days": int(lot["holding_days"]),
                    "entry_price": p_entry,
                    "exit_price": p_exit,
                    "notional": notional,
                    "return_pct": ret,
                    "pnl": notional * ret,
                    "buy_trade_id": lot.get("buy_trade_id"),
                    "sell_trade_id": lot.get("sell_trade_id"),
                }
            )
        elif status == "open":
            buy_d = _naive_ts(lot["buy_date"])
            p_entry = price_on_date(prices, buy_d)
            p_mtm = price_on_date(prices, end)
            if not p_entry or not p_mtm or p_entry <= 0:
                continue
            notional = _lot_notional(lot.get("buy_amount_min"), None)
            if pd.isna(notional):
                continue
            ret = (p_mtm - p_entry) / p_entry
            rows.append(
                {
                    "ticker": ticker,
                    "status": "open_unrealized",
                    "entry_date": buy_d.date(),
                    "exit_date": end.date(),
                    "holding_days": int((end - buy_d).days),
                    "entry_price": p_entry,
                    "exit_price": p_mtm,
                    "notional": notional,
                    "return_pct": ret,
                    "pnl": notional * ret,
                    "buy_trade_id": lot.get("buy_trade_id"),
                    "sell_trade_id": None,
                }
            )

    detail = pd.DataFrame(rows)
    realized = detail[detail["status"] == "realized"] if not detail.empty else detail
    open_u = detail[detail["status"] == "open_unrealized"] if not detail.empty else detail

    def _agg(sub: pd.DataFrame) -> dict[str, Any]:
        if sub.empty:
            return {"n_lots": 0, "total_notional": 0.0, "total_pnl": 0.0, "nw_return": np.nan}
        n = len(sub)
        notional = sub["notional"].sum()
        pnl = sub["pnl"].sum()
        return {
            "n_lots": n,
            "total_notional": float(notional),
            "total_pnl": float(pnl),
            "nw_return": float(pnl / notional) if notional > 0 else np.nan,
            "median_return_pct": float(sub["return_pct"].median()),
            "median_holding_days": float(sub["holding_days"].median()) if "holding_days" in sub else None,
        }

    summary = {
        "realized": _agg(realized),
        "open_unrealized": _agg(open_u),
        "mark_date": str(end.date()),
        "n_prior_sells": int((matched_lots["match_status"] == "prior_position").sum()) if len(matched_lots) else 0,
    }
    if not realized.empty:
        summary["realized"]["win_rate"] = float((realized["pnl"] > 0).mean())
    return detail, summary


def run_both_analyses(
    trades: pd.DataFrame,
    price_cache: dict[str, pd.DataFrame],
    horizons: list[int] | None = None,
    matched_lots: pd.DataFrame | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    horizons = horizons or HORIZONS
    settings = settings or load_settings()
    trump = compute_horizon_returns(trades, price_cache, "transaction_date", horizons)
    follow = compute_horizon_returns(trades, price_cache, "disclosure_date", horizons)

    trump_buy = trump[trump["action"] == "purchase"] if not trump.empty else trump
    trump_sell = trump[trump["action"] == "sale"] if not trump.empty else trump
    follow_buy = follow[follow["action"] == "purchase"] if not follow.empty else follow
    follow_sell = follow[follow["action"] == "sale"] if not follow.empty else follow

    realized_detail = pd.DataFrame()
    realized_summary: dict[str, Any] = {}
    if matched_lots is not None and not matched_lots.empty:
        realized_detail, realized_summary = compute_realized_fifo(matched_lots, trades, price_cache, settings)

    return {
        "trump_timing": trump,
        "follow_disclosure": follow,
        "follow_disclosure_buy": follow_buy,
        "follow_disclosure_sell": follow_sell,
        "trump_timing_buy": trump_buy,
        "trump_timing_sell": trump_sell,
        "trump_summary": notional_weighted_summary(trump, horizons),
        "trump_buy_summary": notional_weighted_summary(trump_buy, horizons),
        "trump_sell_summary": notional_weighted_summary(trump_sell, horizons),
        "follow_summary": notional_weighted_summary(follow, horizons),
        "follow_buy_summary": notional_weighted_summary(follow_buy, horizons),
        "follow_sell_summary": notional_weighted_summary(follow_sell, horizons),
        "trump_cumulative": cumulative_pnl_by_date(trump, horizons),
        "follow_cumulative": cumulative_pnl_by_date(follow, horizons),
        "follow_buy_cumulative": cumulative_pnl_by_date(follow_buy, horizons),
        "follow_sell_cumulative": cumulative_pnl_by_date(follow_sell, horizons),
        "realized_lots": realized_detail,
        "realized_summary": realized_summary,
    }
