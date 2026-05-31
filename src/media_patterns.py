"""Analyze news / social matches vs trade timing and PnL."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .disclosures import load_settings, project_root
from .event_matching import (
    AMBIGUOUS_TICKERS,
    SHORT_WORD_TICKERS,
    TICKER_LINK_TYPES,
    TRUMP_POST_PLATFORMS,
    strict_ticker_in_event,
)

MEDIA_PLATFORMS = TRUMP_POST_PLATFORMS


def _load_frames(root: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    root = root or project_root()
    proc = root / "data" / "processed"
    links = pd.read_parquet(proc / "trade_event_links.parquet")
    events = pd.read_parquet(proc / "events.parquet")
    trump = pd.read_parquet(proc / "trump_timing_returns.parquet")
    follow = pd.read_parquet(proc / "follow_disclosure_returns.parquet")
    return links, events, trump, follow


def _ticker_name_map(returns: pd.DataFrame) -> dict[str, str]:
    if returns.empty or "ticker" not in returns.columns:
        return {}
    cols = ["ticker", "asset_name"] if "asset_name" in returns.columns else ["ticker"]
    if "asset_name" not in cols:
        return {}
    sub = returns.dropna(subset=["ticker"]).drop_duplicates("ticker")
    return {str(r["ticker"]).upper(): str(r["asset_name"]) for _, r in sub.iterrows()}


def _load_realized_map(root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    path = root / "reports" / "realized_fifo_lots.csv"
    if not path.exists():
        return {}
    rdf = pd.read_csv(path)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for _, r in rdf.iterrows():
        key = (str(r.get("buy_trade_id", "")), str(r.get("sell_trade_id", "")))
        out[key] = {
            "realized_pnl": r.get("pnl"),
            "realized_return_pct": r.get("return_pct"),
            "realized_notional": r.get("notional"),
        }
    return out


def _media_links(links: pd.DataFrame) -> pd.DataFrame:
    out = links[~links["event_id"].astype(str).str.startswith("disc_")].copy()
    return out[out["link_type"].isin(TICKER_LINK_TYPES)]


def _enriched_links(links: pd.DataFrame, events: pd.DataFrame, returns: pd.DataFrame, pnl_col: str) -> pd.DataFrame:
    m = _media_links(links)
    ret_cols = ["trade_id", "ticker", "action", "transaction_date", "disclosure_date", "notional", pnl_col]
    if "asset_name" in returns.columns:
        ret_cols.append("asset_name")
    if f"ret_{pnl_col.split('_')[1]}" in returns.columns:
        ret_cols.append(f"ret_{pnl_col.split('_')[1]}")
    m = m.merge(returns[ret_cols], on="trade_id", how="inner")
    m = m.merge(events, on="event_id", how="inner", suffixes=("", "_ev"))
    m["event_time"] = pd.to_datetime(m["event_time"]).dt.tz_localize(None)
    m["transaction_date"] = pd.to_datetime(m["transaction_date"])
    m["disclosure_date"] = pd.to_datetime(m["disclosure_date"])
    m["days_event_minus_txn"] = (m["event_time"] - m["transaction_date"]).dt.days
    return m


def _row_strict(row: pd.Series) -> bool:
    return strict_ticker_in_event(
        str(row["ticker"]),
        str(row.get("text", "") or row.get("headline", "")),
        str(row.get("tickers_mentioned", "")),
        require_in_text=True,
        platform=str(row.get("platform", "")),
        asset_name=str(row.get("asset_name", "")),
    )


def _quality_media_row(row: pd.Series, trade_win: int = 30) -> bool:
    if not _row_strict(row):
        return False
    if row.get("platform") == "truth_social" and row.get("link_type") == "social_near_trade":
        days = row.get("days_event_minus_txn")
        if pd.notna(days) and abs(int(days)) > trade_win:
            return False
    return True


def _strict_filter(m: pd.DataFrame, trade_win: int = 30) -> pd.DataFrame:
    if m.empty:
        return m
    m = m[m.apply(_row_strict, axis=1)].copy()
    if m.empty:
        return m
    return m[m.apply(lambda r: _quality_media_row(r, trade_win), axis=1)].copy()


def top_trade_event_pairs(
    links: pd.DataFrame,
    events: pd.DataFrame,
    returns: pd.DataFrame,
    pnl_col: str = "pnl_10d",
    top_n: int = 20,
    trade_win: int = 30,
) -> pd.DataFrame:
    """Best (trade, Trump post) pairs by trade notional with strict ticker mention."""
    m = _strict_filter(_enriched_links(links, events, returns, pnl_col), trade_win)
    m = m.sort_values("notional", ascending=False).drop_duplicates(subset=["ticker", "event_id"])
    cols = [
        "ticker",
        "action",
        "transaction_date",
        "event_time",
        "platform",
        "link_type",
        "days_event_minus_txn",
        pnl_col,
        "notional",
        "text",
        "url",
        "query",
    ]
    out = m[cols].head(top_n).copy()
    out["transaction_date"] = pd.to_datetime(out["transaction_date"]).dt.date
    out["event_time"] = pd.to_datetime(out["event_time"]).dt.date
    out.rename(columns={pnl_col: "trump_pnl_10d", "text": "headline"}, inplace=True)
    out["headline"] = out["headline"].astype(str).str[:160]
    return out


def top_events_by_pnl(
    links: pd.DataFrame,
    events: pd.DataFrame,
    returns: pd.DataFrame,
    pnl_col: str = "pnl_10d",
    top_n: int = 15,
    trade_win: int = 30,
) -> pd.DataFrame:
    m = _strict_filter(_enriched_links(links, events, returns, pnl_col), trade_win)
    m = m[m[pnl_col].notna()].drop_duplicates(subset=["event_id", "trade_id"])

    agg = (
        m.groupby("event_id", as_index=False)
        .agg(
            platform=("platform", "first"),
            event_time=("event_time", "first"),
            text=("text", "first"),
            url=("url", "first"),
            query=("query", "first"),
            n_trades=("trade_id", "nunique"),
            tickers=("ticker", lambda s: ", ".join(sorted(set(s.astype(str)))[:8])),
            total_pnl=(pnl_col, "sum"),
            avg_pnl=(pnl_col, "mean"),
            total_notional=("notional", "sum"),
        )
        .sort_values("total_pnl", ascending=False)
    )
    agg["nw_return"] = agg["total_pnl"] / agg["total_notional"].replace(0, np.nan)
    return agg.head(top_n)


def bottom_events_by_pnl(
    links: pd.DataFrame,
    events: pd.DataFrame,
    returns: pd.DataFrame,
    pnl_col: str = "pnl_10d",
    top_n: int = 10,
    trade_win: int = 30,
) -> pd.DataFrame:
    m = _strict_filter(_enriched_links(links, events, returns, pnl_col), trade_win)
    m = m[m[pnl_col].notna()].drop_duplicates(subset=["event_id", "trade_id"])
    agg = (
        m.groupby("event_id", as_index=False)
        .agg(
            platform=("platform", "first"),
            event_time=("event_time", "first"),
            text=("text", "first"),
            n_trades=("trade_id", "nunique"),
            tickers=("ticker", lambda s: ", ".join(sorted(set(s.astype(str)))[:8])),
            total_pnl=(pnl_col, "sum"),
        )
        .sort_values("total_pnl", ascending=True)
    )
    return agg.head(top_n)


def top_tickers_by_media_matches(
    links: pd.DataFrame,
    events: pd.DataFrame,
    returns: pd.DataFrame,
    top_n: int = 20,
    trade_win: int = 30,
) -> pd.DataFrame:
    m = _strict_filter(_enriched_links(links, events, returns, "pnl_10d"), trade_win)
    m = m[~m["ticker"].astype(str).str.upper().isin(SHORT_WORD_TICKERS | AMBIGUOUS_TICKERS)]
    m = m.drop_duplicates(subset=["event_id", "trade_id", "ticker"])

    dedupe_col = "trade_id" if "trade_id" in returns.columns else "ticker"
    if "trade_id" in returns.columns:
        ticker_notional = (
            returns.drop_duplicates(subset=[dedupe_col])
            .groupby("ticker", as_index=False)
            .agg(trump_total_notional=("notional", "sum"), n_all_trades=("trade_id", "nunique"))
        )
    else:
        ticker_notional = (
            returns.drop_duplicates(subset=[dedupe_col])
            .groupby("ticker", as_index=False)
            .agg(trump_total_notional=("notional", "sum"))
        )

    media_stats = (
        m.groupby("ticker", as_index=False)
        .agg(
            n_event_trade_pairs=("event_id", "count"),
            n_unique_events=("event_id", "nunique"),
            n_trades=("trade_id", "nunique"),
            n_buys=("action", lambda s: (s == "purchase").sum()),
            n_sells=("action", lambda s: (s == "sale").sum()),
        )
    )

    media_trades = m.drop_duplicates(subset=["trade_id", "ticker"])
    pnl_by_ticker = (
        media_trades.groupby("ticker", as_index=False)
        .agg(trump_pnl_10d=("pnl_10d", "sum"), matched_notional=("notional", "sum"))
    )

    by_ticker = media_stats.merge(ticker_notional, on="ticker", how="inner").merge(pnl_by_ticker, on="ticker", how="left")
    by_ticker["trump_nw_return_10d"] = by_ticker["trump_pnl_10d"] / by_ticker["matched_notional"].replace(0, np.nan)
    by_ticker = by_ticker.sort_values("trump_total_notional", ascending=False)
    return by_ticker.head(top_n)


def _posts_mentioning_ticker(media: pd.DataFrame, ticker: str, asset_name: str = "") -> pd.DataFrame:
    t = str(ticker).upper()

    def _hit(row: pd.Series) -> bool:
        return strict_ticker_in_event(
            t,
            str(row.get("text", "")),
            str(row.get("tickers_mentioned", "")),
            require_in_text=True,
            platform=str(row.get("platform", "")),
            asset_name=asset_name,
        )

    return media[media.apply(_hit, axis=1)]


def detect_buy_post_sell_patterns(
    links: pd.DataFrame,
    events: pd.DataFrame,
    returns: pd.DataFrame,
    matched_lots_path: Path | None = None,
    match_days: int = 30,
) -> pd.DataFrame:
    root = project_root()
    lots_path = matched_lots_path or (root / "reports" / "matched_lots.csv")
    if not lots_path.exists():
        return pd.DataFrame()

    lots = pd.read_csv(lots_path)
    lots["buy_date"] = pd.to_datetime(lots["buy_date"])
    lots["sell_date"] = pd.to_datetime(lots["sell_date"])

    realized_map = _load_realized_map(root)
    name_map = _ticker_name_map(returns)

    media = events[events["platform"].isin(MEDIA_PLATFORMS)].copy()
    media["event_time"] = pd.to_datetime(media["event_time"]).dt.tz_localize(None)

    rows: list[dict[str, Any]] = []
    for _, lot in lots.iterrows():
        ticker = str(lot["ticker"])
        asset_name = name_map.get(ticker.upper(), "")
        buy_d = lot["buy_date"]
        sell_d = lot["sell_date"]
        hold_days = (sell_d - buy_d).days
        buy_tid = str(lot.get("buy_trade_id", ""))
        sell_tid = str(lot.get("sell_trade_id", ""))

        ev_ticker = _posts_mentioning_ticker(media, ticker, asset_name)

        between = ev_ticker[(ev_ticker["event_time"] >= buy_d) & (ev_ticker["event_time"] <= sell_d)]
        after_buy = ev_ticker[
            (ev_ticker["event_time"] >= buy_d) & (ev_ticker["event_time"] <= buy_d + pd.Timedelta(days=match_days))
        ]
        before_buy = ev_ticker[
            (ev_ticker["event_time"] < buy_d) & (ev_ticker["event_time"] >= buy_d - pd.Timedelta(days=match_days))
        ]

        if len(between) == 0 and len(after_buy) == 0 and len(before_buy) == 0:
            continue

        buy_ret = returns[(returns["ticker"] == ticker) & (returns["trade_id"] == buy_tid)]
        trump_pnl = float(buy_ret["pnl_10d"].iloc[0]) if len(buy_ret) and buy_ret["pnl_10d"].notna().any() else np.nan
        trump_ret = float(buy_ret["ret_10d"].iloc[0]) if len(buy_ret) and buy_ret["ret_10d"].notna().any() else np.nan

        realized = realized_map.get((buy_tid, sell_tid), {})

        sample_post = ""
        sample_time = pd.NaT
        sample_platform = ""
        pattern = "none"
        if len(between) > 0:
            pattern = "buy→post→sell (during hold)"
            sample_post = str(between.iloc[0]["text"])[:200]
            sample_time = between.iloc[0]["event_time"]
            sample_platform = str(between.iloc[0].get("platform", ""))
        elif len(after_buy) > 0:
            pattern = f"buy→post within {match_days}d (no sell yet in lot)"
            sample_post = str(after_buy.iloc[0]["text"])[:200]
            sample_time = after_buy.iloc[0]["event_time"]
            sample_platform = str(after_buy.iloc[0].get("platform", ""))
        elif len(before_buy) > 0:
            pattern = f"post→buy within {match_days}d"
            sample_post = str(before_buy.iloc[0]["text"])[:200]
            sample_time = before_buy.iloc[0]["event_time"]
            sample_platform = str(before_buy.iloc[0].get("platform", ""))

        rows.append(
            {
                "ticker": ticker,
                "buy_date": buy_d.date(),
                "sell_date": sell_d.date(),
                "holding_days": hold_days,
                "pattern": pattern,
                "n_posts_during_hold": len(between),
                "n_posts_after_buy_30d": len(after_buy),
                "n_posts_before_buy_30d": len(before_buy),
                "post_date": sample_time.date() if pd.notna(sample_time) else None,
                "days_post_minus_buy": (sample_time - buy_d).days if pd.notna(sample_time) else None,
                "sample_post": sample_post,
                "sample_platform": sample_platform,
                "buy_trump_ret_10d": trump_ret,
                "buy_trump_pnl_10d": trump_pnl,
                "realized_return_pct": realized.get("realized_return_pct"),
                "realized_pnl": realized.get("realized_pnl"),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    def _valid_sample(r: pd.Series) -> bool:
        return strict_ticker_in_event(
            str(r["ticker"]),
            str(r["sample_post"]),
            "",
            require_in_text=True,
            platform=str(r.get("sample_platform", "")),
            asset_name=name_map.get(str(r["ticker"]).upper(), ""),
        )

    out = out[out.apply(_valid_sample, axis=1)]
    if out.empty:
        return out
    priority = {
        "buy→post→sell (during hold)": 0,
        f"buy→post within {match_days}d (no sell yet in lot)": 1,
        f"post→buy within {match_days}d": 2,
    }
    out["_pri"] = out["pattern"].map(priority).fillna(9)
    return out.sort_values(["_pri", "buy_trump_pnl_10d"], ascending=[True, False]).drop(columns="_pri")


def build_media_timeline_top_tickers(
    links: pd.DataFrame,
    events: pd.DataFrame,
    returns: pd.DataFrame,
    all_lots: pd.DataFrame | None = None,
    top_n: int = 3,
    trade_win: int = 30,
) -> list[dict[str, Any]]:
    """Top-N matched tickers by trade notional, with buy/post/sell/hold timeline data."""
    m = _strict_filter(_enriched_links(links, events, returns, "pnl_10d"), trade_win)
    if m.empty:
        return []

    top_tickers = (
        m.groupby("ticker", as_index=False)["notional"]
        .max()
        .sort_values("notional", ascending=False)
        .head(top_n)["ticker"]
        .tolist()
    )

    settings = load_settings()
    end = pd.Timestamp(settings["oge"].get("analysis_end_date", "2026-05-30")).normalize()
    name_map = _ticker_name_map(returns)
    media = events[events["platform"].isin(MEDIA_PLATFORMS)].copy()
    media["event_time"] = pd.to_datetime(media["event_time"]).dt.tz_localize(None)

    timelines: list[dict[str, Any]] = []
    returns = returns.copy()
    returns["transaction_date"] = pd.to_datetime(returns["transaction_date"])

    for ticker in top_tickers:
        t = str(ticker).upper()
        trades = returns[returns["ticker"].astype(str).str.upper() == t].drop_duplicates("trade_id")
        buys = trades[trades["action"] == "purchase"].sort_values("transaction_date")
        sells = trades[trades["action"] == "sale"].sort_values("transaction_date")

        buy_pts = [
            {"date": pd.Timestamp(r["transaction_date"]).normalize(), "notional": float(r["notional"])}
            for _, r in buys.iterrows()
        ]
        sell_pts = [
            {"date": pd.Timestamp(r["transaction_date"]).normalize(), "notional": float(r["notional"])}
            for _, r in sells.iterrows()
        ]

        posts = _posts_mentioning_ticker(media, t, name_map.get(t, ""))
        post_pts = [
            {"date": pd.Timestamp(r["event_time"]).normalize(), "text": str(r.get("text", ""))[:80]}
            for _, r in posts.sort_values("event_time").iterrows()
        ]

        still_open = False
        hold_end = end
        if all_lots is not None and not all_lots.empty:
            tl = all_lots[all_lots["ticker"].astype(str).str.upper() == t]
            still_open = bool((tl["match_status"] == "open").any())
            matched = tl[tl["match_status"] == "matched"]
            if not matched.empty and pd.notna(matched["sell_date"].max()):
                hold_end = max(hold_end, pd.Timestamp(matched["sell_date"].max()).normalize())
            if still_open:
                hold_end = end
            elif not sells.empty:
                hold_end = pd.Timestamp(sells["transaction_date"].max()).normalize()

        first_buy = buy_pts[0]["date"] if buy_pts else None
        timelines.append(
            {
                "ticker": t,
                "max_trade_notional": float(m.loc[m["ticker"] == ticker, "notional"].max()),
                "buys": buy_pts,
                "sells": sell_pts,
                "posts": post_pts,
                "first_buy": first_buy,
                "hold_end": hold_end,
                "still_open": still_open,
                "status": "仍持有" if still_open or (len(buys) > len(sells)) else "已卖出",
            }
        )

    return timelines


def pattern_summary(patterns: pd.DataFrame, match_days: int = 30) -> dict[str, Any]:
    if patterns.empty:
        return {}
    during = patterns[patterns["pattern"] == "buy→post→sell (during hold)"]
    post_before = patterns[patterns["pattern"] == f"post→buy within {match_days}d"]
    post_after = patterns[patterns["pattern"].str.contains("buy→post", na=False)]
    return {
        "match_days": match_days,
        "n_lots_with_any_media": int(len(patterns)),
        "n_buy_post_sell_during_hold": int(len(during)),
        "n_post_before_buy_30d": int(len(post_before)),
        "n_post_after_buy_30d": int(len(post_after)),
        "median_days_post_minus_buy_during_hold": float(during["days_post_minus_buy"].median()) if len(during) else None,
        "mean_buy_ret_10d_during_hold": float(during["buy_trump_ret_10d"].mean()) if len(during) and during["buy_trump_ret_10d"].notna().any() else None,
        "mean_buy_pnl_10d_during_hold": float(during["buy_trump_pnl_10d"].mean()) if len(during) and during["buy_trump_pnl_10d"].notna().any() else None,
        "mean_realized_return_during_hold": float(during["realized_return_pct"].mean()) if len(during) and during["realized_return_pct"].notna().any() else None,
        "mean_realized_pnl_during_hold": float(during["realized_pnl"].mean()) if len(during) and during["realized_pnl"].notna().any() else None,
    }


def run_media_pattern_analysis(root: Path | None = None) -> dict[str, Any]:
    root = root or project_root()
    reports = root / "reports"
    reports.mkdir(exist_ok=True)

    settings = load_settings()
    mw = settings.get("social", {}).get("match_windows", {})
    trade_win = int(mw.get("trade_days", 30))

    links, events, trump, follow = _load_frames(root)

    top_pairs = top_trade_event_pairs(links, events, trump, "pnl_10d", 20, trade_win)
    top_ev = top_events_by_pnl(links, events, trump, "pnl_10d", 15, trade_win)
    bot_ev = bottom_events_by_pnl(links, events, trump, "pnl_10d", 8, trade_win)
    top_tix = top_tickers_by_media_matches(links, events, trump, 20, trade_win)
    patterns = detect_buy_post_sell_patterns(links, events, trump, match_days=trade_win)
    psum = pattern_summary(patterns, trade_win)

    lots_path = root / "data" / "processed" / "matched_lots.parquet"
    all_lots = pd.read_parquet(lots_path) if lots_path.exists() else pd.DataFrame()
    timelines = build_media_timeline_top_tickers(links, events, trump, all_lots, top_n=3, trade_win=trade_win)

    top_pairs.to_csv(reports / "media_top_trade_event_pairs.csv", index=False)

    if not patterns.empty and patterns["buy_trump_pnl_10d"].notna().any():
        during = patterns[patterns["pattern"] == "buy→post→sell (during hold)"]
        other = patterns[patterns["pattern"] != "buy→post→sell (during hold)"]
        psum["mean_buy_pnl_10d_during_hold"] = float(during["buy_trump_pnl_10d"].mean()) if len(during) else None
        psum["mean_buy_pnl_10d_other_patterns"] = float(other["buy_trump_pnl_10d"].mean()) if len(other) else None

    top_ev.to_csv(reports / "media_top_events_by_pnl.csv", index=False)
    bot_ev.to_csv(reports / "media_bottom_events_by_pnl.csv", index=False)
    top_tix.to_csv(reports / "media_top_tickers_by_matches.csv", index=False)
    patterns.to_csv(reports / "media_buy_post_sell_patterns.csv", index=False)

    return {
        "top_trade_event_pairs": top_pairs.to_dict(orient="records"),
        "top_events_by_pnl": top_ev.to_dict(orient="records"),
        "bottom_events_by_pnl": bot_ev.to_dict(orient="records"),
        "top_tickers_by_matches": top_tix.to_dict(orient="records"),
        "buy_post_sell_patterns": patterns.head(25).to_dict(orient="records"),
        "pattern_summary": psum,
        "media_timelines": timelines,
    }
