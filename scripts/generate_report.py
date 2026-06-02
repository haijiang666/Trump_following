#!/usr/bin/env python3
"""Generate markdown + PDF final report with visualizations."""

from __future__ import annotations

import base64
import json
import re
import socket
from datetime import datetime
from html import escape
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages

ROOT = Path(__file__).resolve().parents[1]


def _load_media_analysis(summary: dict) -> dict:
    if summary.get("media_pattern_analysis"):
        return summary["media_pattern_analysis"]
    reports = ROOT / "reports"
    pairs_p = reports / "media_top_trade_event_pairs.csv"
    if not pairs_p.exists():
        try:
            from src.media_patterns import run_media_pattern_analysis
            return run_media_pattern_analysis()
        except Exception:
            return {}
    import pandas as pd

    def _read(name: str) -> list:
        p = reports / name
        return pd.read_csv(p).to_dict(orient="records") if p.exists() else []

    ps = {}
    ps_path = reports / "media_buy_post_sell_patterns.csv"
    if ps_path.exists():
        pdf = pd.read_csv(ps_path)
        during = pdf[pdf["pattern"] == "buy→post→sell (during hold)"]
        ps = {
            "n_lots_with_any_media": len(pdf),
            "n_buy_post_sell_during_hold": len(during),
            "n_post_before_buy_30d": int((pdf["pattern"].str.startswith("post→buy")).sum()),
            "median_days_post_minus_buy_during_hold": float(during["days_post_minus_buy"].median()) if len(during) else None,
        }
    return {
        "top_trade_event_pairs": _read("media_top_trade_event_pairs.csv"),
        "top_tickers_by_matches": _read("media_top_tickers_by_matches.csv"),
        "buy_post_sell_patterns": _read("media_buy_post_sell_patterns.csv")[:25],
        "pattern_summary": ps,
    }


def _media_pattern_section(mpa: dict, fig: callable | None = None) -> list[str]:
    if not mpa:
        return []
    ps = mpa.get("pattern_summary") or {}
    match_days = ps.get("match_days", 30)
    n_during = ps.get("n_buy_post_sell_during_hold", "—")
    med_lag = ps.get("median_days_post_minus_buy_during_hold", "—")
    mean_ret = ps.get("mean_buy_ret_10d_during_hold")
    mean_pnl = ps.get("mean_buy_pnl_10d_during_hold")
    mean_real_ret = ps.get("mean_realized_return_during_hold")
    mean_real_pnl = ps.get("mean_realized_pnl_during_hold")
    ret_s = f"{mean_ret*100:.2f}%" if mean_ret is not None and mean_ret == mean_ret else "—"
    pnl_s = f"${mean_pnl:,.0f}" if mean_pnl is not None and mean_pnl == mean_pnl else "—"
    real_ret_s = f"{mean_real_ret*100:.2f}%" if mean_real_ret is not None and mean_real_ret == mean_real_ret else "—"
    real_pnl_s = f"${mean_real_pnl:,.0f}" if mean_real_pnl is not None and mean_real_pnl == mean_real_pnl else "—"
    lines = [
        "",
        "## Trump 发帖 × 收益 / 行为规律",
        "",
        "以下仅用 **Trump 本人 Truth Social 帖文中明确出现 ticker** 的 strict 匹配（宏观帖、英文 homograph 如 A/S/HE 已过滤）。",
        "Trump PnL = 该笔 **买入** 交易 timing +10 交易日名义 PnL；已实现 PnL = FIFO 配对 lot 的 entry→exit 收益。",
        "Top 表按 `(ticker, event)` 去重；**sale** 行的 PnL 来自该笔卖出交易自身 timing，非买入。",
        "",
        "### 是否存在「先买 → 发帖 → 卖」？",
        "",
        f"- FIFO 配对中 **{ps.get('n_lots_with_any_media', '—')}** 对持仓期间有 **ticker 级别 Trump 发帖** 提及",
        f"- 其中 **{n_during}** 对：**买入 → 持仓期内 Trump 发帖 → 卖出**",
        f"- **{ps.get('n_post_before_buy_30d', '—')}** 对：Trump 发帖在买入前 {match_days} 天内",
        f"- 持仓期内发帖相对买入日中位 lag: **{med_lag}** 天",
        f"- 上述「买→发帖→卖」样例：买入 +10d 平均收益 **{ret_s}**（PnL **{pnl_s}**）；已实现平均 **{real_ret_s}**（PnL **{real_pnl_s}**）",
        "",
        "**解读**：在 **帖文必须出现 ticker** 的严格筛选下：",
        f"- **{n_during}** 对 FIFO 持仓满足「买→持仓期内 Trump 点名 ticker→卖」，中位发帖 lag **约 {med_lag} 天**；",
        "- 第三方新闻报道（Google News 等）**已排除**，不参与本分析；",
        "- 未见稳定的「secret 建仓 → Truth 点名该 ticker → 数日内卖出」链条；",
        "- Truth **宏观帖**（通胀、美元等）若未点名 ticker，已从本分析剔除。",
        "",
        "### Top 交易×Trump 发帖（按交易名义金额排序）",
        "",
        "| Ticker | 动作 | 交易日 | 名义($) | 发帖日 | Δ天 | +10d PnL | 平台 | 帖文 |",
        "|--------|------|--------|--------:|--------|----:|---------:|------|------|",
    ]
    for p in (mpa.get("top_trade_event_pairs") or [])[:12]:
        notional = p.get("notional", 0)
        try:
            notional_s = f"{float(notional):,.0f}"
        except (TypeError, ValueError):
            notional_s = "—"
        lines.append(
            f"| {p.get('ticker', '?')} | {p.get('action', '?')} | {p.get('transaction_date', '?')} | "
            f"{notional_s} | {p.get('event_time', '?')} | {p.get('days_event_minus_txn', '?')} | "
            f"{p.get('trump_pnl_10d', 0):,.0f} | {p.get('platform', '?')} | "
            f"{str(p.get('headline', ''))[:70]} |"
        )
    lines += [
        "",
        "完整列表: `reports/media_top_trade_event_pairs.csv`",
        "",
        "### Top3 匹配 ticker 时间线（买 → 发帖 → 卖 / 仍持有）",
        "",
        "下图展示 **匹配名义金额最大的 3 只 ticker**：绿色▼=买入、蓝色◆=Trump Truth 发帖、红色▲=卖出；灰条=持仓区间，右端标注是否仍持有。",
        "",
    ]
    if fig:
        lines += fig("16_media_match_timelines")
    lines += [
        "### Trump 发帖匹配的 Top Ticker（按 Trump 名义金额排序）",
        "",
        "| Ticker | Trump名义($) | 独立事件 | 交易笔数 | 买/卖链接 | Trump NW +10d |",
        "|--------|------------:|---------:|---------:|----------:|--------------:|",
    ]
    for t in (mpa.get("top_tickers_by_matches") or [])[:12]:
        nw = t.get("trump_nw_return_10d")
        nw_s = f"{nw*100:.2f}%" if nw is not None and nw == nw else "—"
        notional = t.get("trump_total_notional", t.get("notional", 0))
        try:
            notional_s = f"{float(notional):,.0f}"
        except (TypeError, ValueError):
            notional_s = "—"
        lines.append(
            f"| {t.get('ticker', '?')} | {notional_s} | {t.get('n_unique_events', 0)} | {t.get('n_trades', 0)} | "
            f"{t.get('n_buys', 0)}/{t.get('n_sells', 0)} | {nw_s} |"
        )
    lines += [
        "",
        "### 「买→发帖→卖」样例（ticker 必须在帖中出现）",
        "",
        "| Ticker | 买 | 卖 | 持仓d | 发帖日 | 发帖-买(d) | 买+10d收益 | 买+10d PnL | 已实现收益 | 已实现 PnL | 样例 |",
        "|--------|----|----|------:|--------|----------:|-----------:|----------:|-----------:|-----------:|------|",
    ]
    for p in (mpa.get("buy_post_sell_patterns") or [])[:10]:
        if p.get("pattern") != "buy→post→sell (during hold)":
            continue
        ret = p.get("buy_trump_ret_10d")
        ret_cell = f"{ret*100:.2f}%" if ret is not None and ret == ret else "—"
        pnl = p.get("buy_trump_pnl_10d")
        pnl_cell = f"${pnl:,.0f}" if pnl is not None and pnl == pnl else "—"
        rret = p.get("realized_return_pct")
        rret_cell = f"{rret*100:.2f}%" if rret is not None and rret == rret else "—"
        rpnl = p.get("realized_pnl")
        rpnl_cell = f"${rpnl:,.0f}" if rpnl is not None and rpnl == rpnl else "—"
        lines.append(
            f"| {p.get('ticker')} | {p.get('buy_date')} | {p.get('sell_date')} | {p.get('holding_days')} | "
            f"{p.get('post_date', '—')} | {p.get('days_post_minus_buy', '—')} | "
            f"{ret_cell} | {pnl_cell} | {rret_cell} | {rpnl_cell} | "
            f"{str(p.get('sample_post', ''))[:50]} |"
        )
    lines += ["", "明细: `reports/media_buy_post_sell_patterns.csv`", ""]
    return lines


MAX_SANE_NOTIONAL = 1_000_000_001
EQUITY_ETF_CLASSES = {"equity", "etf"}


def _safe_notional(row: pd.Series) -> float:
    lo = row.get("amount_min")
    if pd.notna(lo):
        v = float(lo)
        if 0 < v < MAX_SANE_NOTIONAL:
            return v
    return 0.0


def _fmt_usd(x: float) -> str:
    if x >= 1e9:
        return f"${x / 1e9:.2f}B"
    if x >= 1e6:
        return f"${x / 1e6:.1f}M"
    if x >= 1e3:
        return f"${x / 1e3:.0f}K"
    if x > 0:
        return f"${x:,.0f}"
    return "—"


def _filing_content_label(n_equity_etf: int, n_bond_other: int, file_ok: bool) -> str:
    if not file_ok:
        return "未下载"
    if n_equity_etf >= 100:
        return "股票/ETF 批量"
    if n_equity_etf > 0 and n_bond_other > n_equity_etf:
        return "以债券为主（含少量误解析）"
    if n_equity_etf > 0:
        return "少量股票/ETF"
    return "市政/公司债"


_FIGURE_CAPTIONS: dict[str, str] = {
    "01_monthly_volume": "交易时间线（按日/周）：名义金额为主；柱顶标注 Top3 公司 buy/sell 名义",
    "02_reveal_lag": "披露滞后（交易日 → 披露日）",
    "03_top_tickers": "Trump 名义金额 Top Ticker（amount_min 合计）",
    "04_buy_sell": "买入 vs 卖出：笔数与名义金额双饼图",
    "05_post_returns": "Legacy：披露后收益分布",
    "16_media_match_timelines": "Top3 匹配 ticker：买入 / Trump 发帖 / 卖出或仍持有",
    "17_open_holdings": "当前净多头 Top10：名义 + 买入后 horizon 收益",
    "18_portfolio_timeseries": "组合持仓规模与累计 PnL 随时间变化（FIFO 日度）",
    "06_backtest_cum": "Legacy：等权披露日回测累计收益",
    "07_event_study": "事件研究：披露日 abnormal return",
    "08_disclosure_timeline": "披露日批次：披露名义总额 + 笔数",
    "09_holding_days": "FIFO 持仓天数分布",
    "10_trump_notional_returns": "Trump timing：名义加权 horizon 收益",
    "11_follow_notional_returns": "Follow 披露日：名义加权 horizon 收益",
    "12_trump_cumulative_pnl": "Trump timing：累计 PnL（按交易日）",
    "13_follow_cumulative_pnl": "Follow 披露日：累计 PnL",
    "14_follow_buy_vs_sell": "Follow：买入 vs 卖出 NW 收益对比",
    "15_trump_buy_vs_sell": "Trump timing：买入 vs 卖出 NW 收益对比",
}


def _figure_lines(chart_paths: list[Path], stem: str, embedded: set[str]) -> list[str]:
    """Inline HTML figure block (renders in MD + HTML reports)."""
    available = {p.stem: p for p in chart_paths if p.suffix.lower() == ".png"}
    if stem not in available or stem in embedded:
        return []
    embedded.add(stem)
    cap = _FIGURE_CAPTIONS.get(stem, stem.replace("_", " "))
    fname = available[stem].name
    return [
        "",
        f'<figure class="report-fig">',
        f'<img src="figures/{fname}" alt="{cap}">',
        f"<figcaption>{cap}</figcaption>",
        "</figure>",
        "",
    ]


def _trade_action_summary_lines() -> list[str]:
    """Buy/sell count + notional table for the data overview section."""
    path = ROOT / "data" / "processed" / "trump_timing_returns.parquet"
    if not path.exists():
        return []
    df = pd.read_parquet(path)
    df = df[df["action"].isin(["purchase", "sale"])].dropna(subset=["notional"])
    if df.empty:
        return []
    lines = [
        "",
        "**交易规模（Trump timing；名义 = OGE `amount_min`，金额优先于笔数）**",
        "",
        "| 方向 | 笔数 | 名义下限合计 | 占总额比例 |",
        "|------|-----:|-------------:|-----------:|",
    ]
    total_n = float(df["notional"].sum())
    total_c = len(df)
    for action, label in [("purchase", "买入"), ("sale", "卖出")]:
        sub = df[df["action"] == action]
        n = len(sub)
        notional = float(sub["notional"].sum())
        pct = notional / total_n * 100 if total_n > 0 else 0
        lines.append(f"| {label} | {n:,} | {_fmt_usd(notional)} | {pct:.1f}% |")
    lines.append(f"| **合计** | **{total_c:,}** | **{_fmt_usd(total_n)}** | 100% |")
    lines.append("")
    return lines


def _portfolio_daily_section(summary: dict) -> list[str]:
    meta = summary.get("portfolio_daily") or {}
    if not meta:
        path = ROOT / "reports" / "portfolio_daily.csv"
        if path.exists():
            df = pd.read_csv(path)
            if not df.empty:
                last = df.iloc[-1]
                meta = {
                    "last_date": str(last["date"]),
                    "position_mtm_end": float(last["position_mtm"]),
                    "cum_pnl_end": float(last["cum_pnl"]),
                    "peak_mtm": float(df["position_mtm"].max()),
                }
    if not meta:
        return []

    return [
        "",
        "## Trump 组合持仓与 PnL 时间序列",
        "",
        "按 **FIFO 净多头** 重建每个交易日的 EOD 持仓：",
        "- **持仓规模**：未平仓买入的 OGE `amount_min` 合计（成本）及按收盘价 mark-to-market 的市值；",
        "- **每日 PnL**：各仍持有标的的日度价格变动 × 对应名义仓位，卖出日记入已实现收益；",
        "- **累计 PnL**：全部交易日 daily PnL 的 running sum（整组合曲线）。",
        "",
        f"- 样本交易日: **{meta.get('n_days', '—')}** 天",
        f"- 截止 **{meta.get('last_date', '—')}**：MTM 持仓 **{_fmt_usd(meta.get('position_mtm_end', 0))}**，"
        f"累计 PnL **{_fmt_usd(meta.get('cum_pnl_end', 0))}**",
        f"- 持仓 MTM 峰值: **{_fmt_usd(meta.get('peak_mtm', 0))}**（{meta.get('peak_mtm_date', '—')}）",
        "",
        "明细: `reports/portfolio_daily.csv`",
        "",
    ]


def _open_holdings_section(summary: dict) -> list[str]:
    """Net-long open FIFO lots — top 10 with horizon returns."""
    rows = summary.get("open_holdings") or []
    if not rows:
        path = ROOT / "reports" / "open_holdings_top10.csv"
        if path.exists():
            rows = pd.read_csv(path).to_dict(orient="records")
    if not rows:
        return []

    lines = [
        "",
        "## Trump 当前净多头持仓（Top 10）",
        "",
        "基于 **FIFO 未配对买入**（`match_status=open`），按净名义金额排序。",
        "Horizon 收益以 **最早一笔未平买入** 的交易日为 anchor（Trump timing）。",
        "截至分析截止日仍标注 **仍持有**；明细见 `reports/open_holdings_top10.csv`。",
        "",
        "| Ticker | 净名义($) | 未平笔数 | 最早买入 | 最近买入 | 持有天 | 状态 | +1d | +5d | +10d | +20d | +30d |",
        "|--------|----------:|---------:|----------|----------|------:|------|-----:|-----:|------:|------:|------:|",
    ]
    for r in rows[:10]:
        def _pct(key: str) -> str:
            v = r.get(key)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return "—"
            return f"{float(v)*100:.2f}%"

        lines.append(
            f"| {r.get('ticker', '?')} | {float(r.get('net_notional', 0)):,.0f} | "
            f"{r.get('n_open_lots', r.get('n_open_buys', '—'))} | "
            f"{r.get('first_buy_date', '—')} | {r.get('latest_buy_date', '—')} | "
            f"{r.get('days_held', '—')} | {r.get('status', '仍持有')} | "
            f"{_pct('ret_1d')} | {_pct('ret_5d')} | {_pct('ret_10d')} | {_pct('ret_20d')} | {_pct('ret_30d')} |"
        )
    lines.append("")
    return lines


def _filing_stats(trades: pd.DataFrame) -> dict[str, dict]:
    """Per-filing parse stats for the report table."""
    stats: dict[str, dict] = {}
    if trades.empty or "doc_id" not in trades.columns:
        return stats
    for doc_id, g in trades.groupby("doc_id", sort=False):
        is_eq = g["ticker"].notna() & g["asset_class"].isin(EQUITY_ETF_CLASSES)
        eq = g[is_eq]
        other = g[~is_eq]
        stats[str(doc_id)] = {
            "n_parsed": int(len(g)),
            "n_equity_etf": int(len(eq)),
            "n_bond_other": int(len(other)),
            "n_tickers": int(eq["ticker"].nunique()) if len(eq) else 0,
            "notional_equity_etf": float(eq.apply(_safe_notional, axis=1).sum()),
            "notional_all": float(g.apply(_safe_notional, axis=1).sum()),
            "notional_bond_other": float(other.apply(_safe_notional, axis=1).sum()),
        }
    return stats


def _md_report(
    summary: dict,
    xcheck: dict,
    web: list,
    by_ticker: pd.DataFrame,
    manifest: pd.DataFrame,
    chart_paths: list[Path],
    filing_stats: dict[str, dict],
) -> str:
    n = summary.get("total_rows_parsed", 0)
    n_trad = summary.get("tradable_with_ticker", 0)
    n_eq = summary.get("tradable_equity_etf", n_trad)
    table_rate = (summary.get("parse_rate_vs_table") or 0) * 100
    dr = summary.get("date_range", ["?", "?"])
    disc_dates = summary.get("disclosure_dates", [])

    tot_tickers = summary.get("unique_tickers", 0)
    tot_notional_eq = sum(s["notional_equity_etf"] for s in filing_stats.values())
    tot_notional_all = sum(s["notional_all"] for s in filing_stats.values())

    embedded: set[str] = set()

    def fig(stem: str) -> list[str]:
        return _figure_lines(chart_paths, stem, embedded)

    lines = [
        "# Trump 股票/ETF 交易分析报告",
        "",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} · OGE Form 278-T · 第二任期上任以来",
        "",
        "## 数据范围",
        "",
        f"- **分析区间**: {dr[0]} → {dr[1]}（交易发生日）",
        f"- **278-T 文件数**: {summary.get('n_filings', 1)} 份（有效 {xcheck.get('n_valid_documents', '—')}）",
        f"- **披露日**: {', '.join(disc_dates) if disc_dates else '—'}",
        f"- **股票/ETF 可交易笔数**: **{n_eq:,}**（**{tot_tickers}** 只 ticker）",
        f"- **股票/ETF 名义下限合计**: **{_fmt_usd(tot_notional_eq)}**（OGE `amount_min` 求和）",
        f"- **全部解析行**: **{n:,}**（含债券等；全文件名义合计约 **{_fmt_usd(tot_notional_all)}**）",
        f"- **表格解析率**: **{table_rate:.1f}%**",
        "",
    ]
    lines += _trade_action_summary_lines()
    lines += fig("08_disclosure_timeline")
    lines += fig("01_monthly_volume")
    lines += fig("04_buy_sell")
    lines += _open_holdings_section(summary)
    lines += fig("17_open_holdings")
    lines += _portfolio_daily_section(summary)
    lines += fig("18_portfolio_timeseries")
    lines += [
        "",
        "## Cross-Check",
        "",
        f"- OGE 官方来源: {'✅' if xcheck.get('is_oge_url') else '⚠️ 含 White House 镜像'}",
        f"- Form 278-T 检测: {'✅' if xcheck.get('all_valid') else '❌'}",
        "",
        "### 已纳入文件（逐份统计）",
        "",
        "名义金额 = 各笔 OGE 区间**下限**（`amount_min`）相加；债券 filing 中「股票/ETF」多为 0 或 OCR 误映射。",
        "",
        "| doc_id | 页数 | 披露日 | 解析笔数 | 股票/ETF笔数 | ticker数 | 股票名义($) | 全文件名义($) | 内容 |",
        "|--------|-----:|--------|--------:|-------------:|---------:|------------:|--------------:|------|",
    ]
    for d in xcheck.get("documents", []):
        doc_id = str(d.get("doc_id", "?"))
        fs = filing_stats.get(doc_id, {})
        pages = d.get("pages")
        pages_s = "—" if pages is None or (isinstance(pages, float) and pd.isna(pages)) else str(int(pages))
        file_ok = bool(d.get("file_exists"))

        def _cell(key: str, fmt=None):
            if not file_ok:
                return "—"
            val = fs.get(key, 0)
            return fmt(val) if fmt else val

        n_eq_doc = fs.get("n_equity_etf", 0) if file_ok else 0
        n_other = fs.get("n_bond_other", 0) if file_ok else 0
        lines.append(
            f"| {doc_id} | {pages_s} | {d.get('disclosure_date', '—')} | "
            f"{_cell('n_parsed')} | {_cell('n_equity_etf')} | {_cell('n_tickers')} | "
            f"{_cell('notional_equity_etf', _fmt_usd)} | {_cell('notional_all', _fmt_usd)} | "
            f"{_filing_content_label(n_eq_doc, n_other, file_ok)} |"
        )

    lines += [
        "",
        "## 样本验证",
        "",
        "| Ticker | 动作 | 日期 | 匹配 |",
        "|--------|------|------|------|",
    ]
    for s in web:
        lines.append(f"| {s['ticker']} | {s['action']} | {s['date']} | {'✅' if s['found_in_parse'] else '❌'} |")

    em = summary.get("event_matching") or {}
    if em:
        by_lt = em.get("by_link_type") or {}
        by_plat_events = em.get("by_platform_unique_events") or {}
        n_loaded = em.get("n_events_loaded", "—")
        n_linked = em.get("n_unique_events_linked", em.get("n_unique_events", "—"))
        n_ts = em.get("n_trades_with_ticker_specific", "—")
        n_any = em.get("n_trades_with_media", 0)
        n_total = em.get("n_trades_total", "—")
        lines += [
            "",
            "## Trump 本人发帖匹配",
            "",
            "来源：**仅 Truth Social**（CNN 归档 + trumpstruth.org RSS + 手动 JSON）。",
            "**不含** Google News 等第三方报道——只有 Trump **自己发的帖** 才能反映主动点名/炒作某公司的意图。",
            "规则：以 **交易为中心**，对每笔交易在交易日 ±30 天、披露日 ±30 天内检索 Trump 帖文。",
            "**Ticker 级链接**：帖文须 **明确提及 ticker**（`$T` 或公司全名）；未点名 ticker 的宏观帖不参与匹配。",
            "",
            f"- 载入 Trump 帖文（`events.parquet`）: **{n_loaded}** 条",
            f"- 参与匹配的独立帖文: **{n_linked}** 条（去重）",
            f"- 匹配链接总行数: **{em.get('n_media_links', em.get('n_links', 0)):,}**（含披露锚点 **{by_lt.get('disclosure_event', 0):,}**）",
            f"- 至少 1 条 **Trump 发帖** 匹配的交易: **{n_any:,}** / {n_total}",
            f"- 至少 1 条 **ticker 级** 发帖匹配的交易: **{n_ts:,}** / {n_total}",
            "",
            "**按链接类型**",
            "",
        ]
        for k, v in sorted(by_lt.items(), key=lambda x: -x[1]):
            lines.append(f"- `{k}`: {v:,}")
        if by_plat_events:
            lines += ["", "**按平台（独立事件数，非链接行数）**", ""]
            for k, v in sorted(by_plat_events.items(), key=lambda x: -x[1]):
                lines.append(f"- {k}: {v:,}")
        samples = em.get("sample_events") or []
        if samples:
            lines += ["", "**样例帖文**", ""]
            for s in samples[:6]:
                plat = s.get("platform", "?")
                txt = str(s.get("text", ""))[:120]
                lines.append(f"- [{plat}] {txt}")

    mpa = _load_media_analysis(summary)
    lines += _media_pattern_section(mpa, fig)

    hs = summary.get("holding_stats") or {}
    holdings_path = ROOT / "reports" / "holdings_by_ticker.csv"
    holdings_top = ""
    if holdings_path.exists():
        hdf = pd.read_csv(holdings_path).dropna(subset=["avg_holding_days"])
        trump_path = ROOT / "data" / "processed" / "trump_timing_returns.parquet"
        if trump_path.exists():
            tr = pd.read_parquet(trump_path)
            if "notional" in tr.columns:
                tnot = tr.drop_duplicates("trade_id").groupby("ticker")["notional"].sum()
                hdf = hdf.merge(tnot.rename("trump_total_notional"), on="ticker", how="left")
                hdf = hdf.sort_values("trump_total_notional", ascending=False, na_position="last")
            else:
                hdf = hdf.sort_values("n_matched_pairs", ascending=False)
        else:
            hdf = hdf.sort_values("n_matched_pairs", ascending=False)
        hdf = hdf.head(10)
        if not hdf.empty:
            holdings_top = hdf.to_string(index=False)

    lines += [
        "",
        "## 持仓时间（FIFO 买→卖配对）",
        "",
        f"- 成功配对: **{hs.get('n_matched_pairs', '—')}** 对，涉及 **{hs.get('n_tickers_with_pairs', '—')}** 个 ticker",
        f"- 持仓中位: **{hs.get('median_holding_days', 0):.0f}** 天，均值: **{hs.get('mean_holding_days', 0):.0f}** 天",
        "- 规则: 同 ticker 按日期排序，**先进先出**；无对应买入的卖出标为 `prior_position`；未卖出买入标为 `open`",
        "- 明细: `reports/matched_lots.csv`（每笔买-卖对），`reports/holdings_by_ticker.csv`（每 ticker 平均持仓）",
        "",
    ]
    if holdings_top:
        lines += ["### Top tickers（按 Trump 名义金额）", "", "```", holdings_top, "```", ""]
    lines += fig("09_holding_days")

    ra = summary.get("return_analysis") or {}
    trump_rows = ra.get("trump_timing") or []
    trump_buy_rows = ra.get("trump_timing_buy") or []
    trump_sell_rows = ra.get("trump_timing_sell") or []
    follow_rows = ra.get("follow_disclosure") or []
    follow_buy_rows = ra.get("follow_disclosure_buy") or []
    follow_sell_rows = ra.get("follow_disclosure_sell") or []
    realized = ra.get("realized_fifo") or {}

    def _ret_table(rows: list) -> str:
        if not rows:
            return "（暂无数据）"
        hdr = "| 窗口(交易日) | 笔数 | 总名义($) | 总PnL($) | **名义加权收益率** |"
        sep = "|---|---:|---:|---:|---:|"
        body = []
        for r in rows:
            nw = r["notional_weighted_return"]
            nw_s = f"**{nw*100:.2f}%**" if nw is not None and nw == nw else "—"
            body.append(
                f"| +{r['horizon_days']}d | {r['n_trades']} | {r['total_notional']:,.0f} | "
                f"{r['total_pnl']:,.0f} | {nw_s} |"
            )
        return "\n".join([hdr, sep] + body)

    lines += [
        "",
        "## 1. Trump 自身交易 timing（锚点 = **交易发生日**）",
        "",
        "- **事件研究**：每笔交易当日为锚点，看 +h 交易日价格；买入 sign=+1，卖出 sign=−1",
        "- **已实现（realized）**：FIFO 配对 **entry=买入日收盘**、**exit=卖出日收盘**（见 §1d）",
        "- notional = OGE `amount_min`；窗口: **1, 3, 5, 10, 20, 30** 个交易日",
        "",
        "### 1a. 合计（买 + 卖）",
        "",
        _ret_table(trump_rows),
        "",
    ]
    lines += fig("10_trump_notional_returns")
    lines += [
        "### 1b. Trump 买（仅 `purchase`）",
        "",
        _ret_table(trump_buy_rows),
        "",
        "### 1c. Trump 卖（仅 `sale`，按做空计 sign=−1）",
        "",
        _ret_table(trump_sell_rows),
        "",
    ]
    lines += fig("15_trump_buy_vs_sell")
    lines += fig("12_trump_cumulative_pnl")

    def _realized_section(rs: dict) -> list[str]:
        if not rs:
            return []
        r = rs.get("realized") or {}
        o = rs.get("open_unrealized") or {}
        mark = rs.get("mark_date", "—")
        lines = [
            "### 1d. 已实现收益（FIFO entry → exit）",
            "",
            "假设：**entry** = 买入日收盘价，**exit** = 卖出日收盘价；名义 = min(买/卖 `amount_min`)。",
            "未平仓买入按 **mark-to-market** 至 " + str(mark) + "（未实现）。",
            "",
            "| 类型 | 配对/笔数 | 名义($) | 总 PnL($) | **NW 收益率** | 中位持仓(天) | 胜率 |",
            "|------|----------:|--------:|----------:|--------------:|-------------:|-----:|",
        ]

        def _row(label: str, block: dict) -> None:
            if not block or not block.get("n_lots"):
                return
            nw = block.get("nw_return")
            nw_s = f"**{nw*100:.2f}%**" if nw is not None and nw == nw else "—"
            wr = block.get("win_rate")
            wr_s = f"{wr*100:.1f}%" if wr is not None and wr == wr else "—"
            med = block.get("median_holding_days")
            med_s = f"{med:.0f}" if med is not None and med == med else "—"
            lines.append(
                f"| {label} | {block.get('n_lots', 0)} | {block.get('total_notional', 0):,.0f} | "
                f"{block.get('total_pnl', 0):,.0f} | {nw_s} | {med_s} | {wr_s} |"
            )

        _row("已实现（买→卖）", r)
        _row("未平仓（MTM）", o)
        if rs.get("n_prior_sells"):
            lines.append("")
            lines.append(f"- 无对应买入的卖出（`prior_position`）: **{rs['n_prior_sells']}** 笔 — 未计入 realized")
        lines += ["", "明细: `reports/realized_fifo_lots.csv`", ""]
        return lines

    lines += _realized_section(realized)
    lines += [
        "## 2. Follow Trump（锚点 = **OGE 披露日**）",
        "",
        "- 披露日跟单，方向与 Trump 一致：**买入 = 做多**，**卖出 = 做空**",
        "- 下面先给 **合计**，再拆 **Follow 买** / **Follow 卖**",
        "",
        "### 2a. 合计（买 + 卖）",
        "",
        _ret_table(follow_rows),
        "",
    ]
    lines += fig("11_follow_notional_returns")
    lines += [
        "### 2b. Follow 买（仅 `purchase`，做多）",
        "",
        _ret_table(follow_buy_rows),
        "",
        "### 2c. Follow 卖（仅 `sale`，做空）",
        "",
        _ret_table(follow_sell_rows),
        "",
    ]
    lines += fig("14_follow_buy_vs_sell")
    lines += fig("13_follow_cumulative_pnl")
    lines += [
        "## Top Tickers（按 Trump 名义金额 `amount_min` 合计）",
        "",
        "```",
        by_ticker.head(15).to_string(index=False),
        "```",
        "",
    ]
    lines += fig("03_top_tickers")

    for p in chart_paths:
        if p.stem not in embedded:
            lines += _figure_lines(chart_paths, p.stem, embedded)

    lines += [
        "",
        "## 说明",
        "",
        "- 上任以来 **6 份** 278-T 中，**股票/ETF 批量披露**集中在 `trump_278t_2026_05_08_equity`（2026-05-12 收到）；其余多为市政/公司债 periodic report。",
        "- 名义金额 = OGE 披露区间**下限**相加，非精确成交价；单笔可能落在 $1,001–$15,000 至 $50M+ 等 bracket。",
        "- `return_post_disclosure_20d` 在披露日距数据截止不足 20 交易日时为空。",
        "",
        "完整数据: `reports/trades_analysis.csv`",
        "",
    ]
    return "\n".join(lines)


def _pdf_report(
    summary: dict,
    xcheck: dict,
    chart_paths: list[Path],
    out_path: Path,
    filing_stats: dict[str, dict],
) -> None:
    bt = summary.get("backtest_metrics", {})
    dr = summary.get("date_range", ["?", "?"])
    tot_notional_eq = sum(s["notional_equity_etf"] for s in filing_stats.values())
    text_lines = [
        "Trump Stock/ETF Trade Analysis Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"Period: {dr[0]} to {dr[1]}",
        f"Equity+ETF trades: {summary.get('tradable_equity_etf', 0):,} | "
        f"Tickers: {summary.get('unique_tickers', 0)} | Notional (min): {_fmt_usd(tot_notional_eq)}",
        f"Parsed rows (all): {summary.get('total_rows_parsed', 0):,}",
        f"Parse rate: {(summary.get('parse_rate_vs_table') or 0)*100:.1f}%",
        "",
        "Return analysis: notional-weighted (amount_min), dual-anchor (see FINAL_REPORT.md)",
    ]
    ra = summary.get("return_analysis") or {}
    trump_rows = ra.get("trump_timing") or []
    follow_rows = ra.get("follow_disclosure") or []
    if trump_rows:
        top = trump_rows[0] if trump_rows else {}
        text_lines.append(
            f"Trump NW +1d: {top.get('notional_weighted_return', 0):.2%} ({top.get('n_trades', 0)} trades)"
        )
    hs = summary.get("holding_stats") or {}
    if hs:
        text_lines += [
            "",
            f"FIFO holding: {hs.get('n_matched_pairs', 0)} pairs, "
            f"median {hs.get('median_holding_days', 0):.0f}d, mean {hs.get('mean_holding_days', 0):.0f}d",
        ]
    text_lines += ["", "Documents:"]
    for d in xcheck.get("documents", []):
        doc_id = str(d.get("doc_id", "?"))
        fs = filing_stats.get(doc_id, {})
        file_ok = bool(d.get("file_exists"))
        extra = ""
        if file_ok and fs:
            extra = (
                f", eq {fs.get('n_equity_etf', 0)} trades / {fs.get('n_tickers', 0)} tickers"
                f", {_fmt_usd(fs.get('notional_equity_etf', 0))}"
            )
        text_lines.append(
            f"  - {doc_id}: {d.get('pages')}pp, disclosed {d.get('disclosure_date')}{extra}"
        )

    with PdfPages(out_path) as pdf:
        fig = plt.figure(figsize=(8.5, 11))
        ax = fig.add_subplot(111)
        ax.axis("off")
        ax.text(0.02, 0.98, "\n".join(text_lines), va="top", fontsize=10, family="monospace", transform=ax.transAxes)
        pdf.savefig(fig)
        plt.close(fig)

        for chart in chart_paths:
            if not chart.exists():
                continue
            img = plt.imread(chart)
            fig, ax = plt.subplots(figsize=(11, 8.5))
            ax.imshow(img)
            ax.axis("off")
            ax.set_title(chart.stem.replace("_", " ").title(), fontsize=12, pad=10)
            pdf.savefig(fig)
            plt.close(fig)


_HTML_STYLES = """
:root {
  color-scheme: light;
  --bg: #f4f5f7;
  --paper: #ffffff;
  --text: #1a1a2e;
  --muted: #5c6370;
  --border: #e2e6ed;
  --accent: #1e4d8c;
  --code-bg: #f1f3f6;
  --nav-h: 3rem;
}
* { box-sizing: border-box; }
html {
  -webkit-text-size-adjust: 100%;
  text-size-adjust: 100%;
  scroll-behavior: smooth;
}
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB",
    "Microsoft YaHei", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 16px;
  line-height: 1.65;
  color: var(--text);
  background: var(--bg);
  padding-bottom: env(safe-area-inset-bottom, 0);
}
.page {
  max-width: 980px;
  margin: 0 auto;
  padding: 2rem 1.25rem 4rem;
}
article {
  background: var(--paper);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 2rem 2.25rem;
  box-shadow: 0 2px 12px rgba(0,0,0,.04);
}
.mobile-nav {
  position: sticky;
  top: 0;
  z-index: 100;
  background: rgba(255,255,255,.96);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--border);
  padding: 0.5rem 0.75rem;
  margin: -2rem -2.25rem 1.25rem;
  border-radius: 10px 10px 0 0;
}
.mobile-nav .nav-title {
  font-size: 0.85rem;
  color: var(--muted);
  margin: 0 0 0.35rem;
}
.mobile-nav .nav-toggle {
  width: 100%;
  padding: 0.55rem 0.75rem;
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--accent);
  background: #eef2f8;
  border: 1px solid var(--border);
  border-radius: 8px;
  cursor: pointer;
  touch-action: manipulation;
}
.mobile-nav .nav-links {
  display: none;
  flex-direction: column;
  gap: 0.35rem;
  margin-top: 0.5rem;
  max-height: 50vh;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}
.mobile-nav.is-open .nav-links { display: flex; }
.mobile-nav .nav-links a {
  display: block;
  padding: 0.45rem 0.6rem;
  font-size: 0.88rem;
  color: var(--text);
  text-decoration: none;
  border-radius: 6px;
  background: #fafbfc;
  border: 1px solid var(--border);
}
.mobile-nav .nav-links a:active { background: #eef2f8; }
h1 { font-size: 1.85rem; margin-top: 0; border-bottom: 2px solid var(--accent); padding-bottom: .5rem; word-break: break-word; }
h2 { font-size: 1.35rem; margin-top: 2.2rem; color: var(--accent); scroll-margin-top: calc(var(--nav-h) + 1rem); word-break: break-word; }
h3 { font-size: 1.1rem; margin-top: 1.6rem; word-break: break-word; }
blockquote {
  margin: 1rem 0;
  padding: .75rem 1rem;
  border-left: 4px solid var(--accent);
  background: #f0f4fa;
  color: var(--muted);
  font-size: 0.92rem;
}
p, ul, ol { margin: .75rem 0; word-break: break-word; }
ul, ol { padding-left: 1.4rem; }
li { margin: .25rem 0; }
a { color: var(--accent); }
.table-wrap {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  margin: 1rem 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
}
.table-wrap::after {
  content: "← 左右滑动查看 →";
  display: none;
  text-align: center;
  font-size: 0.72rem;
  color: var(--muted);
  padding: 0.25rem;
}
table {
  width: max-content;
  min-width: 100%;
  border-collapse: collapse;
  margin: 0;
  font-size: .88rem;
}
th, td {
  border: 1px solid var(--border);
  padding: .45rem .6rem;
  text-align: left;
  white-space: nowrap;
}
th { background: #eef2f8; font-weight: 600; position: sticky; top: 0; }
tr:nth-child(even) td { background: #fafbfc; }
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: .88em;
  background: var(--code-bg);
  padding: .12em .35em;
  border-radius: 4px;
  word-break: break-all;
}
pre {
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.75rem;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  font-size: .78rem;
  line-height: 1.45;
}
pre code { background: none; padding: 0; white-space: pre; }
img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 1.25rem auto;
  border: 1px solid var(--border);
  border-radius: 6px;
}
figure.report-fig {
  margin: 1.5rem 0;
  padding: 0.85rem 0.5rem 1rem;
  background: #fafbfc;
  border: 1px solid var(--border);
  border-radius: 8px;
  text-align: center;
}
figure.report-fig img {
  margin: 0 auto;
  border: none;
  border-radius: 4px;
  max-width: 100%;
  width: 100%;
  height: auto;
}
figure.report-fig figcaption {
  margin-top: 0.65rem;
  font-size: 0.85rem;
  color: var(--muted);
  line-height: 1.4;
  white-space: normal;
  word-break: break-word;
}
strong { font-weight: 650; }
.footer-tip {
  margin-top: 2rem;
  padding: 0.75rem 1rem;
  font-size: 0.8rem;
  color: var(--muted);
  background: #f0f4fa;
  border-radius: 8px;
  line-height: 1.5;
}
@media (max-width: 768px) {
  body { font-size: 15px; }
  .page { padding: 0.5rem 0 2.5rem; max-width: 100%; }
  article {
    padding: 1rem 0.85rem 1.5rem;
    border-radius: 0;
    border-left: none;
    border-right: none;
    box-shadow: none;
  }
  .mobile-nav {
    margin: -1rem -0.85rem 1rem;
    border-radius: 0;
    padding-top: max(0.5rem, env(safe-area-inset-top));
  }
  h1 { font-size: 1.35rem; line-height: 1.35; }
  h2 { font-size: 1.12rem; margin-top: 1.6rem; }
  h3 { font-size: 1rem; }
  .table-wrap::after { display: block; }
  .table-wrap th, .table-wrap td { padding: 0.32rem 0.42rem; font-size: 0.74rem; }
  figure.report-fig { padding: 0.45rem 0.25rem 0.65rem; margin: 1rem 0; }
  figure.report-fig figcaption { font-size: 0.78rem; }
  pre { font-size: 0.72rem; padding: 0.55rem; }
}
@media print {
  body { background: white; }
  .page { padding: 0; max-width: none; }
  article { border: none; box-shadow: none; padding: 0; }
  .mobile-nav, .footer-tip { display: none; }
  img { page-break-inside: avoid; }
}
"""

_HTML_MOBILE_SCRIPT = """
(function () {
  var nav = document.querySelector('.mobile-nav');
  var btn = document.querySelector('.nav-toggle');
  if (!nav || !btn) return;
  btn.addEventListener('click', function () {
    var open = nav.classList.toggle('is-open');
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    btn.textContent = open ? '收起目录 ▴' : '展开目录 ▾';
  });
  document.querySelectorAll('.nav-links a').forEach(function (a) {
    a.addEventListener('click', function () {
      nav.classList.remove('is-open');
      btn.setAttribute('aria-expanded', 'false');
      btn.textContent = '展开目录 ▾';
    });
  });
})();
"""


def _wrap_tables(html: str) -> str:
    return re.sub(r"<table>", r'<div class="table-wrap"><table>', html).replace("</table>", "</table></div>")


def _add_section_ids_and_toc(html: str) -> tuple[str, str]:
    toc_items: list[tuple[str, str]] = []
    counter = 0

    def _repl(match: re.Match) -> str:
        nonlocal counter
        counter += 1
        inner = match.group(1)
        plain = re.sub(r"<[^>]+>", "", inner).strip()
        sid = f"sec-{counter}"
        toc_items.append((sid, plain))
        return f'<h2 id="{sid}">{inner}</h2>'

    html = re.sub(r"<h2>(.*?)</h2>", _repl, html, flags=re.DOTALL)
    links = "".join(f'<a href="#{sid}">{escape(title)}</a>' for sid, title in toc_items)
    return html, links


def _embed_figure_src(html: str, figures_dir: Path, *, compress: bool = True) -> str:
    def _repl(match: re.Match) -> str:
        fname = match.group(1)
        path = figures_dir / fname
        if not path.exists():
            return match.group(0)
        if compress:
            from io import BytesIO

            from PIL import Image

            with Image.open(path) as img:
                max_w = 900
                if img.width > max_w:
                    ratio = max_w / img.width
                    img = img.resize((max_w, int(img.height * ratio)), Image.Resampling.LANCZOS)
                buf = BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=72, optimize=True)
                payload = buf.getvalue()
            b64 = base64.b64encode(payload).decode("ascii")
            return f'src="data:image/jpeg;base64,{b64}"'
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'src="data:image/png;base64,{b64}"'

    return re.sub(r'src="figures/([^"]+\.png)"', _repl, html)


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _html_report(
    md_text: str,
    out_path: Path,
    *,
    title: str = "Trump 股票/ETF 交易分析报告",
    figures_dir: Path | None = None,
    standalone: bool = False,
    port_hint: int = 8765,
) -> None:
    import markdown

    body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
        output_format="html5",
    )
    body = _wrap_tables(body)
    body, toc_links = _add_section_ids_and_toc(body)

    if standalone and figures_dir:
        body = _embed_figure_src(body, figures_dir)

    nav_html = ""
    if toc_links:
        nav_html = f"""
    <nav class="mobile-nav" aria-label="报告目录">
      <p class="nav-title">Trump 交易分析报告 · 手机版</p>
      <button type="button" class="nav-toggle" aria-expanded="false">展开目录 ▾</button>
      <div class="nav-links">{toc_links}</div>
    </nav>"""

    tip = (
        "手机/微信：若 GitHub 链接打不开，请用 Safari/Chrome 打开 "
        "<code>cdn.jsdelivr.net/gh/haijiang666/Trump_following@main/docs/index.html</code>，"
        "或把 <code>reports/FINAL_REPORT.mobile.html</code> 作为文件发到微信。"
        if standalone
        else f"局域网预览：电脑与手机同一 WiFi 时，运行 "
        f"<code>python scripts/serve_report.py</code>，手机浏览器访问 "
        f"<code>http://{_lan_ip()}:{port_hint}/</code>，可复制链接到微信。"
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
  <meta name="format-detection" content="telephone=no"/>
  <meta name="apple-mobile-web-app-capable" content="yes"/>
  <meta name="apple-mobile-web-app-status-bar-style" content="default"/>
  <meta name="description" content="Trump 第二任期股票/ETF 交易分析报告（手机可读版）"/>
  <title>{escape(title)}</title>
  <style>{_HTML_STYLES}</style>
</head>
<body>
  <div class="page">
    <article>
{nav_html}
      {body}
      <div class="footer-tip">{tip}</div>
    </article>
  </div>
  <script>{_HTML_MOBILE_SCRIPT}</script>
</body>
</html>
"""
    out_path.write_text(html, encoding="utf-8")


def main() -> None:
    reports = ROOT / "reports"
    figures = reports / "figures"
    summary = json.loads((reports / "final_summary.json").read_text())
    xcheck = json.loads((reports / "cross_check_manifest.json").read_text())
    web = json.loads((reports / "web_cross_check.json").read_text())
    by_ticker = pd.read_csv(reports / "summary_by_ticker.csv")
    manifest = pd.read_csv(ROOT / "data/processed/manifest.csv") if (ROOT / "data/processed/manifest.csv").exists() else pd.DataFrame()
    trades_path = reports / "trades_raw.csv"
    trades = pd.read_csv(trades_path) if trades_path.exists() else pd.DataFrame()
    filing_stats = _filing_stats(trades)

    chart_paths = sorted(figures.glob("*.png")) if figures.exists() else []

    md = _md_report(summary, xcheck, web, by_ticker, manifest, chart_paths, filing_stats)
    md_path = reports / "FINAL_REPORT.md"
    md_path.write_text(md)
    print(f"Wrote {md_path}")

    pdf_path = reports / "FINAL_REPORT.pdf"
    _pdf_report(summary, xcheck, chart_paths, pdf_path, filing_stats)
    print(f"Wrote {pdf_path}")

    html_path = reports / "FINAL_REPORT.html"
    mobile_path = reports / "FINAL_REPORT.mobile.html"
    _html_report(md, html_path, figures_dir=figures, standalone=False)
    _html_report(md, mobile_path, figures_dir=figures, standalone=True)
    print(f"Wrote {html_path}")
    print(f"Wrote {mobile_path} (单文件，适合上传后在微信打开)")

    pages_index = reports.parent / "docs" / "index.html"
    pages_index.parent.mkdir(exist_ok=True)
    pages_index.write_text(mobile_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Wrote {pages_index} (GitHub Pages → https://haijiang666.github.io/Trump_following/)")


if __name__ == "__main__":
    main()
