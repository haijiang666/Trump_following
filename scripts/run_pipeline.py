#!/usr/bin/env python3
"""Trump stock trade analysis pipeline (OGE 278-T, equities only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.backtest import (
    align_events_to_trades,
    backtest_follow_strategy,
    backtest_metrics,
    compute_returns,
    event_study,
    summarize_event_study,
)
from src.disclosures import load_settings, project_root
from src.equity_disclosures import cross_check_manifest, enrich_equity_manifest, fetch_equity_disclosures
from src.equity_trades import (
    filter_trades_with_ticker,
    parse_all_equity_filings,
    parse_stats_all,
    save_equity_trades,
)
from src.ticker_resolver import enrich_trades_with_tickers
from src.media_patterns import run_media_pattern_analysis
from src.event_matching import summarize_event_links
from src.events import fetch_all_events
from src.prices import fetch_prices_for_trades
from src.holdings import attach_holding_to_trades, fifo_match_trades, holding_summary_stats
from src.trade_returns import run_both_analyses
from src.portfolio_snapshot import (
    compute_open_holdings_top_n,
    compute_portfolio_daily_timeseries,
    open_holdings_summary_records,
    portfolio_daily_summary_records,
)
from src.visualizations import generate_all_charts


def web_cross_check_samples(trades: pd.DataFrame) -> list[dict]:
    expected = [
        {"ticker": "DELL", "action": "purchase", "date": "2026-02-10", "amount_min": 1_000_001, "source": "OGE 278-T + press"},
        {"ticker": "NVDA", "action": "purchase", "date": "2026-02-10", "source": "OGE 278-T + MarketWatch"},
        {"ticker": "MSFT", "action": "sale", "date": "2026-02-10", "amount_min": 5_000_001, "source": "OGE 278-T + press"},
        {"ticker": "AMZN", "action": "sale", "date": "2026-02-10", "source": "OGE 278-T + press"},
        {"ticker": "META", "action": "sale", "date": "2026-03-18", "source": "OGE 278-T + press"},
    ]
    results = []
    t = trades.copy()
    t["transaction_date"] = pd.to_datetime(t["transaction_date"])
    for exp in expected:
        mask = t["ticker"] == exp["ticker"]
        mask &= t["action"] == exp["action"]
        mask &= t["transaction_date"] == pd.Timestamp(exp["date"])
        if "amount_min" in exp:
            mask &= t["amount_min"] >= exp["amount_min"]
        matched = t[mask]
        results.append(
            {
                **exp,
                "found_in_parse": len(matched) > 0,
                "match_count": len(matched),
                "sample_asset": matched["asset_name"].iloc[0] if len(matched) else None,
            }
        )
    return results


def main() -> int:
    settings = load_settings()
    proc = project_root() / settings["paths"]["processed"]
    reports = project_root() / "reports"
    figures = reports / "figures"
    reports.mkdir(exist_ok=True)

    print("=" * 60)
    print("STEP 1: Download all Trump 278-T filings since inauguration")
    manifest = fetch_equity_disclosures(settings)
    manifest = enrich_equity_manifest(manifest, settings)
    ok = manifest[manifest["status"].astype(str).str.startswith("ok")]
    print(ok[["doc_id", "status", "pages", "likely_equity_filing", "disclosure_date"]].to_string())

    print("\nSTEP 2: Cross-check — official OGE Form 278-T?")
    xcheck = cross_check_manifest(manifest)
    print(json.dumps({k: v for k, v in xcheck.items() if k != "documents"}, indent=2, default=str))
    (reports / "cross_check_manifest.json").write_text(json.dumps(xcheck, indent=2, default=str))

    print("\nSTEP 3: Parse stock/ETF trades (all filings)")
    pstats = parse_stats_all(ok, settings)
    print(f"  Filings: {pstats['n_filings']}, table rows: {pstats['table_rows_in_pdf']}")
    print(f"  Parse rate: {pstats['parse_rate_vs_table']*100:.1f}%")
    trades = parse_all_equity_filings(ok, settings)
    trades = enrich_trades_with_tickers(trades)
    print(f"  Parsed: {len(trades)} equity rows, tickers: {trades['ticker'].notna().sum()}")
    save_equity_trades(trades, settings)
    trades.to_csv(reports / "trades_raw.csv", index=False)

    print("\nSTEP 4: Web cross-check (sample trades)")
    samples = web_cross_check_samples(trades)
    for s in samples:
        status = "OK" if s["found_in_parse"] else "MISSING"
        print(f"  [{status}] {s['ticker']} {s['action']} {s['date']}")
    (reports / "web_cross_check.json").write_text(json.dumps(samples, indent=2))

    tradable = filter_trades_with_ticker(trades)

    print("\nSTEP 5: FIFO holding periods (buy→sell matching)")
    matched_lots, holdings_by_ticker, trade_holding, all_lots = fifo_match_trades(tradable)
    hstats = holding_summary_stats(matched_lots)
    print(f"  Matched pairs: {hstats.get('n_matched_pairs', 0)}, median hold {hstats.get('median_holding_days', 0):.0f}d")
    matched_lots.to_csv(reports / "matched_lots.csv", index=False)
    all_lots.to_csv(reports / "all_lots.csv", index=False)
    holdings_by_ticker.to_csv(reports / "holdings_by_ticker.csv", index=False)
    all_lots.to_parquet(proc / "matched_lots.parquet", index=False)

    print(f"\nSTEP 6: Prices ({tradable['ticker'].nunique()} tickers)")
    price_cache = fetch_prices_for_trades(tradable, settings)

    print("\nSTEP 7: Horizon returns (Trump timing vs Follow)")
    ret_analysis = run_both_analyses(tradable, price_cache, matched_lots=all_lots, settings=settings)
    ret_analysis["trump_timing"].to_parquet(proc / "trump_timing_returns.parquet", index=False)
    ret_analysis["follow_disclosure"].to_parquet(proc / "follow_disclosure_returns.parquet", index=False)
    ret_analysis["trump_summary"].to_csv(reports / "trump_timing_summary.csv", index=False)
    ret_analysis["trump_buy_summary"].to_csv(reports / "trump_timing_buy_summary.csv", index=False)
    ret_analysis["trump_sell_summary"].to_csv(reports / "trump_timing_sell_summary.csv", index=False)
    ret_analysis["follow_summary"].to_csv(reports / "follow_disclosure_summary.csv", index=False)
    ret_analysis["follow_buy_summary"].to_csv(reports / "follow_disclosure_buy_summary.csv", index=False)
    ret_analysis["follow_sell_summary"].to_csv(reports / "follow_disclosure_sell_summary.csv", index=False)
    ret_analysis["trump_cumulative"].to_csv(reports / "trump_cumulative_pnl.csv", index=False)
    ret_analysis["follow_cumulative"].to_csv(reports / "follow_cumulative_pnl.csv", index=False)
    ret_analysis["follow_buy_cumulative"].to_csv(reports / "follow_buy_cumulative_pnl.csv", index=False)
    ret_analysis["follow_sell_cumulative"].to_csv(reports / "follow_sell_cumulative_pnl.csv", index=False)
    if not ret_analysis["realized_lots"].empty:
        ret_analysis["realized_lots"].to_csv(reports / "realized_fifo_lots.csv", index=False)
    print("  Trump timing (notional-weighted):")
    print(ret_analysis["trump_summary"][["horizon_days", "notional_weighted_return", "n_trades"]].to_string(index=False))
    print("  Trump timing — BUY only:")
    print(ret_analysis["trump_buy_summary"][["horizon_days", "notional_weighted_return", "n_trades"]].to_string(index=False))
    print("  Trump timing — SELL only (short):")
    print(ret_analysis["trump_sell_summary"][["horizon_days", "notional_weighted_return", "n_trades"]].to_string(index=False))
    rs = ret_analysis.get("realized_summary") or {}
    if rs.get("realized"):
        r = rs["realized"]
        print(f"  Realized FIFO: {r.get('n_lots', 0)} lots, NW return {r.get('nw_return', 0):.2%}, PnL ${r.get('total_pnl', 0):,.0f}")
    print("  Follow disclosure — ALL:")
    print(ret_analysis["follow_summary"][["horizon_days", "notional_weighted_return", "n_trades"]].to_string(index=False))
    print("  Follow disclosure — BUY only:")
    print(ret_analysis["follow_buy_summary"][["horizon_days", "notional_weighted_return", "n_trades"]].to_string(index=False))
    print("  Follow disclosure — SELL only (short):")
    print(ret_analysis["follow_sell_summary"][["horizon_days", "notional_weighted_return", "n_trades"]].to_string(index=False))

    print("\nSTEP 8: Legacy returns + holdings columns")
    returns_df = compute_returns(tradable, price_cache, settings)
    returns_df = attach_holding_to_trades(returns_df, trade_holding)
    returns_df.to_parquet(proc / "returns.parquet", index=False)
    returns_df.to_csv(reports / "trades_analysis.csv", index=False)
    print(f"  {len(returns_df)} trades, median reveal lag {returns_df['reveal_lag_days'].median():.0f}d")

    print("\nSTEP 9: Trump posts (Truth Social only)")
    events = fetch_all_events(settings, trades=tradable, refresh_news=False)
    links = align_events_to_trades(tradable, events, settings)
    links.to_parquet(proc / "trade_event_links.parquet", index=False)
    evt_summary = summarize_event_links(links, events, tradable)
    print(f"  Trump posts loaded: {len(events)} ({events['platform'].value_counts().to_dict() if len(events) else {}})")
    print(f"  Stock-related posts: {int(events['stock_related'].sum()) if len(events) and 'stock_related' in events.columns else 0}")
    print(f"  Post links: {evt_summary.get('n_links', 0)} ({evt_summary.get('by_link_type', {})})")
    print(f"  Trades with Trump post match: {evt_summary.get('n_trades_with_media', 0)} / {evt_summary.get('n_trades_total', 0)}")

    print("\nSTEP 9b: Media pattern analysis (PnL / buy→post→sell)")
    media_analysis = run_media_pattern_analysis()
    ps = media_analysis.get("pattern_summary", {})
    print(f"  FIFO lots with ticker-specific media: {ps.get('n_lots_with_any_media', 0)}")
    print(f"  buy→post→sell during hold: {ps.get('n_buy_post_sell_during_hold', 0)}")

    print("\nSTEP 10: Event study")
    es = event_study(returns_df, settings=settings)
    print(summarize_event_study(es))
    es.to_parquet(proc / "event_study.parquet", index=False)

    print("\nSTEP 11: Backtest (legacy equal-weight by disclosure day)")
    bt = backtest_follow_strategy(returns_df, settings)
    metrics = backtest_metrics(bt)
    print(metrics)
    bt.to_parquet(proc / "backtest.parquet", index=False)

    trump_for_rank = ret_analysis["trump_timing"].merge(
        returns_df[["trade_id", "return_post_disclosure_1d", "return_post_disclosure_5d"]],
        on="trade_id",
        how="left",
    )
    by_ticker = (
        trump_for_rank.drop_duplicates(subset=["trade_id"])
        .groupby("ticker")
        .agg(
            trades=("trade_id", "count"),
            buys=("action", lambda x: (x == "purchase").sum()),
            sales=("action", lambda x: (x == "sale").sum()),
            total_notional=("notional", "sum"),
            avg_post_5d=("return_post_disclosure_5d", "mean"),
            avg_post_1d=("return_post_disclosure_1d", "mean"),
        )
        .sort_values("total_notional", ascending=False)
    )
    by_ticker.to_csv(reports / "summary_by_ticker.csv")

    disc_dates = sorted(trades["disclosure_date"].dropna().astype(str).unique().tolist())
    n_raw = len(trades)
    n_tradable = len(tradable)
    n_equity_etf = len(filter_trades_with_ticker(trades, asset_classes=["equity", "etf"]))
    summary = {
        "total_rows_parsed": n_raw,
        "tradable_with_ticker": n_tradable,
        "tradable_equity_etf": n_equity_etf,
        "unique_tickers": int(tradable["ticker"].nunique()),
        "date_range": [str(trades["transaction_date"].min().date()), str(trades["transaction_date"].max().date())],
        "disclosure_dates": disc_dates,
        "n_filings": int(pstats["n_filings"]),
        "median_reveal_lag_days": float(returns_df["reveal_lag_days"].median()) if len(returns_df) else None,
        "holding_stats": hstats,
        "parse_rate_vs_table": pstats["parse_rate_vs_table"],
        "per_document_stats": pstats.get("per_document", []),
        "web_cross_check": samples,
        "backtest_metrics": metrics,
        "return_analysis": {
            "trump_timing": ret_analysis["trump_summary"].to_dict(orient="records"),
            "trump_timing_buy": ret_analysis["trump_buy_summary"].to_dict(orient="records"),
            "trump_timing_sell": ret_analysis["trump_sell_summary"].to_dict(orient="records"),
            "follow_disclosure": ret_analysis["follow_summary"].to_dict(orient="records"),
            "follow_disclosure_buy": ret_analysis["follow_buy_summary"].to_dict(orient="records"),
            "follow_disclosure_sell": ret_analysis["follow_sell_summary"].to_dict(orient="records"),
            "realized_fifo": ret_analysis.get("realized_summary") or {},
        },
        "event_matching": evt_summary,
        "media_pattern_analysis": media_analysis,
    }
    print("\nSTEP 11: Visualizations")
    open_holdings = compute_open_holdings_top_n(ret_analysis["trump_timing"], all_lots, top_n=10)
    open_holdings.to_csv(reports / "open_holdings_top10.csv", index=False)
    summary["open_holdings"] = open_holdings_summary_records(open_holdings)
    portfolio_daily = compute_portfolio_daily_timeseries(tradable, price_cache, settings)
    portfolio_daily.to_csv(reports / "portfolio_daily.csv", index=False)
    summary["portfolio_daily"] = portfolio_daily_summary_records(portfolio_daily)
    (reports / "final_summary.json").write_text(json.dumps(summary, indent=2, default=str))

    chart_paths = generate_all_charts(
        trades,
        returns_df,
        bt,
        es,
        figures,
        matched_lots,
        holdings_by_ticker,
        ret_analysis,
        media_timelines=media_analysis.get("media_timelines"),
        open_holdings=open_holdings,
        portfolio_daily=portfolio_daily,
    )
    for p in chart_paths:
        print(f"  {p.name}")

    print("\nSTEP 12: Generate report (MD + PDF)")
    import subprocess

    subprocess.run([sys.executable, str(ROOT / "scripts/generate_report.py")], check=True)
    print("\nDone → reports/FINAL_REPORT.md + reports/FINAL_REPORT.pdf + reports/FINAL_REPORT.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
