"""Generate analysis charts for Trump equity trade reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from .trade_returns import trade_notional

sns.set_theme(style="whitegrid", palette="muted")
plt.rcParams.update({"figure.dpi": 120, "savefig.dpi": 150, "font.size": 10})

_ACTION_COLORS = {"purchase": "#2ecc71", "sale": "#e74c3c", "exchange": "#f39c12"}
_ACTION_LABELS = {"purchase": "Buy", "sale": "Sell", "exchange": "Exchange"}


def _save(fig: plt.Figure, out_dir: Path, name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.png"
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _fmt_notional_short(x: float) -> str:
    if pd.isna(x) or x <= 0:
        return "$0"
    if x >= 1e6:
        return f"${x / 1e6:.2f}M"
    if x >= 1e3:
        return f"${x / 1e3:.0f}K"
    return f"${x:,.0f}"


def _mpl_label(s: str) -> str:
    """Escape $ so matplotlib renders dollar amounts literally (not as mathtext)."""
    return s.replace("$", r"\$")


def _with_notional(trades: pd.DataFrame, trump_df: pd.DataFrame | None = None) -> pd.DataFrame:
    df = trades.copy()
    if trump_df is not None and "notional" in trump_df.columns and "trade_id" in trump_df.columns:
        nmap = trump_df.drop_duplicates("trade_id").set_index("trade_id")["notional"]
        if "trade_id" in df.columns:
            df["notional"] = df["trade_id"].map(nmap)
        else:
            df["notional"] = df.apply(trade_notional, axis=1)
    elif "notional" not in df.columns:
        df["notional"] = df.apply(trade_notional, axis=1)
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df["disclosure_date"] = pd.to_datetime(df["disclosure_date"])
    return df.dropna(subset=["notional"])


def _pick_granularity(dates: pd.Series) -> tuple[str, str]:
    span = int((dates.max() - dates.min()).days)
    if span <= 45:
        return "D", "day"
    if span <= 120:
        return "W", "week"
    return "W", "week"


def _period_start(dates: pd.Series, granularity: str) -> pd.Series:
    if granularity == "D":
        return dates.dt.normalize()
    if granularity == "W":
        return dates.dt.to_period("W").apply(lambda p: p.start_time)
    return dates.dt.to_period("M").apply(lambda p: p.start_time)


def _top_ticker_lines(sub: pd.DataFrame, top_n: int = 3) -> list[str]:
    """Top-N tickers in a period by total notional, with buy/sell breakdown."""
    sub = sub[sub["ticker"].notna()].copy()
    if sub.empty:
        return []
    rows = []
    for ticker, g in sub.groupby("ticker", sort=False):
        buy = float(g.loc[g["action"] == "purchase", "notional"].sum())
        sell = float(g.loc[g["action"] == "sale", "notional"].sum())
        total = buy + sell
        if total <= 0:
            continue
        parts: list[str] = []
        if buy > 0:
            parts.append(f"buy {_fmt_notional_short(buy)}")
        if sell > 0:
            parts.append(f"sell {_fmt_notional_short(sell)}")
        rows.append((total, f"{ticker} {' '.join(parts)}"))
    rows.sort(key=lambda x: x[0], reverse=True)
    return [line for _, line in rows[:top_n]]


def _annotate_top_tickers(
    ax,
    df: pd.DataFrame,
    periods: list,
    x_positions: np.ndarray,
    heights: np.ndarray,
    top_n: int = 3,
    min_notional: float = 0.0,
    ymax: float = 1.0,
) -> None:
    """Place top-ticker labels on each bar with dark text on white box."""
    bbox = dict(boxstyle="round,pad=0.35", facecolor="white", edgecolor="#7f8c8d", alpha=0.97, linewidth=0.8)
    for period, xpos, h in zip(periods, x_positions, heights):
        if h < min_notional:
            continue
        sub = df[df["_period"] == period]
        lines = _top_ticker_lines(sub, top_n=top_n)
        if not lines:
            continue
        text = _mpl_label("\n".join(lines))
        ax.text(
            xpos,
            h + ymax * 0.006,
            text,
            ha="center",
            va="bottom",
            fontsize=9.5,
            fontweight="bold",
            color="#1a1a1a",
            linespacing=1.15,
            bbox=bbox,
            zorder=10,
            clip_on=False,
        )


def plot_trade_volume_monthly(
    trades: pd.DataFrame,
    out_dir: Path,
    trump_df: pd.DataFrame | None = None,
) -> Path:
    """Timeline of trade count + notional (day/week); top-3 trades labeled per bar."""
    df = _with_notional(trades, trump_df)
    df = df[df["action"].isin(["purchase", "sale"])].copy()
    gran, gran_label = _pick_granularity(df["transaction_date"])
    df["_period"] = _period_start(df["transaction_date"], gran)
    periods = sorted(df["_period"].unique())
    x = np.arange(len(periods))
    width = 0.72

    buy_n = []
    sell_n = []
    buy_not = []
    sell_not = []
    for p in periods:
        sub = df[df["_period"] == p]
        buy = sub[sub["action"] == "purchase"]
        sell = sub[sub["action"] == "sale"]
        buy_n.append(len(buy))
        sell_n.append(len(sell))
        buy_not.append(buy["notional"].sum())
        sell_not.append(sell["notional"].sum())

    total_notional = df["notional"].sum()
    total_trades = len(df)
    counts = np.array(buy_n) + np.array(sell_n)
    totals_not = np.array(buy_not) + np.array(sell_not)
    ymax = float(totals_not.max()) if len(totals_not) else 1.0

    fig_w = max(18.0, len(periods) * 0.52)
    fig, ax1 = plt.subplots(figsize=(fig_w, 8))
    ax2 = ax1.twinx()
    ax1.bar(x, buy_not, width, label=f"Buy notional ({sum(buy_n):,} trades)", color=_ACTION_COLORS["purchase"], alpha=0.92)
    ax1.bar(
        x,
        sell_not,
        width,
        bottom=buy_not,
        label=f"Sell notional ({sum(sell_n):,} trades)",
        color=_ACTION_COLORS["sale"],
        alpha=0.92,
    )
    ax2.plot(x, counts, color="#34495e", marker="o", lw=1.5, ms=3, label="Trade count", zorder=5)

    _annotate_top_tickers(
        ax1,
        df,
        periods,
        x,
        totals_not,
        top_n=3,
        min_notional=max(200_000.0, ymax * 0.03),
        ymax=ymax,
    )

    ax1.set_xticks(x)
    ax1.set_xticklabels([pd.Timestamp(p).strftime("%Y-%m-%d") for p in periods], rotation=45, ha="right")
    ax1.set_ylabel("Notional ($) — primary", fontweight="bold")
    ax2.set_ylabel("Trade count")
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: _fmt_notional_short(v)))
    ax1.set_title(
        _mpl_label(
            f"Trade Volume by {gran_label.title()} · Total {_fmt_notional_short(total_notional)} notional · {total_trades:,} trades"
        )
    )
    ax1.set_xlabel(f"Period start ({gran_label}) · on-bar labels: top-3 tickers (buy/sell notional)")
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8)
    ax1.set_ylim(0, ymax * 1.10)
    fig.subplots_adjust(top=0.92, bottom=0.14, right=0.92)
    return _save(fig, out_dir, "01_monthly_volume")


def plot_reveal_lag(returns_df: pd.DataFrame, out_dir: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4))
    lag = returns_df["reveal_lag_days"].dropna()
    ax.hist(lag, bins=40, color="#3498db", edgecolor="white")
    ax.axvline(lag.median(), color="#e67e22", ls="--", label=f"Median {lag.median():.0f}d")
    ax.set_title("Reveal Lag: Transaction → OGE Disclosure")
    ax.set_xlabel("Days")
    ax.set_ylabel("Trades")
    ax.legend()
    return _save(fig, out_dir, "02_reveal_lag")


def plot_top_tickers(
    trades: pd.DataFrame,
    out_dir: Path,
    n: int = 15,
    returns_df: pd.DataFrame | None = None,
    prefix: str = "03_top_tickers",
    title: str | None = None,
) -> Path:
    if returns_df is not None and "notional" in returns_df.columns and "ticker" in returns_df.columns:
        dedupe_col = "trade_id" if "trade_id" in returns_df.columns else "ticker"
        top = (
            returns_df.drop_duplicates(subset=[dedupe_col])
            .groupby("ticker")["notional"]
            .sum()
            .nlargest(n)
        )
        chart_title = title or f"Top {n} tickers by Trump timing notional (amount_min)"
        xlabel = "Notional ($)"
    else:
        df = trades[trades["ticker"].notna()].copy()
        df["notional"] = df.apply(trade_notional, axis=1)
        top = df.groupby("ticker")["notional"].sum().nlargest(n)
        chart_title = title or f"Top {n} tickers by raw amount_min (all parsed rows)"
        xlabel = "Notional ($)"
    fig, ax = plt.subplots(figsize=(8, 5))
    top.sort_values().plot(kind="barh", ax=ax, color="#9b59b6")
    ax.set_title(_mpl_label(chart_title))
    ax.set_xlabel(xlabel)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x/1e6:.1f}M" if x >= 1e6 else f"${x/1e3:.0f}K"))
    return _save(fig, out_dir, prefix)


def plot_buy_sell(trades: pd.DataFrame, out_dir: Path, trump_df: pd.DataFrame | None = None) -> Path:
    df = _with_notional(trades, trump_df)
    df = df[df["action"].isin(["purchase", "sale"])].copy()
    actions = ["purchase", "sale"]

    count = df.groupby("action").size().reindex(actions, fill_value=0)
    notional = df.groupby("action")["notional"].sum().reindex(actions, fill_value=0)
    total_n = notional.sum()
    total_c = count.sum()

    fig, axes = plt.subplots(1, 2, figsize=(6.8, 3.0))
    colors = [_ACTION_COLORS[a] for a in actions]
    labels = [_ACTION_LABELS[a] for a in actions]

    def _pie(ax, values, title: str) -> None:
        vals = values.values.astype(float)
        if vals.sum() <= 0:
            ax.text(0.5, 0.5, "No data", ha="center", va="center")
            ax.axis("off")
            return

        def _autopct(pct: float) -> str:
            val = pct / 100.0 * vals.sum()
            if title.startswith("Notional"):
                return _mpl_label(f"{pct:.1f}%\n{_fmt_notional_short(val)}")
            return f"{pct:.1f}%\n{int(round(val)):,}"

        ax.pie(vals, labels=labels, autopct=_autopct, colors=colors, startangle=90, textprops={"fontsize": 8})
        ax.set_title(_mpl_label(title), fontsize=10, pad=8)

    _pie(axes[0], count, f"By count ({total_c:,} trades)")
    _pie(axes[1], notional, f"By notional ({_fmt_notional_short(total_n)})")
    fig.suptitle(
        _mpl_label(
            "Buy vs Sell · "
            + " · ".join(
                f"{_ACTION_LABELS[a]}: {int(count[a]):,} trades / {_fmt_notional_short(notional[a])}"
                for a in actions
            )
        ),
        fontsize=9,
        y=1.02,
    )
    return _save(fig, out_dir, "04_buy_sell")


def plot_post_returns(returns_df: pd.DataFrame, out_dir: Path) -> Path:
    cols = ["return_post_disclosure_1d", "return_post_disclosure_5d"]
    data = returns_df[cols].dropna(how="all")
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for ax, col in zip(axes, cols):
        s = data[col].dropna()
        if len(s):
            ax.hist(s * 100, bins=40, color="#1abc9c", edgecolor="white")
            ax.axvline(s.mean() * 100, color="#c0392b", ls="--", label=f"Mean {s.mean():.2%}")
        ax.set_title(col.replace("return_post_disclosure_", "Post-disclosure +") + "d")
        ax.set_xlabel("Return (%)")
        ax.legend()
    fig.suptitle("Post-Disclosure Returns (direction-adjusted)")
    return _save(fig, out_dir, "05_post_returns")


def plot_backtest_cum(bt: pd.DataFrame, out_dir: Path) -> Path:
    if bt.empty:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.text(0.5, 0.5, "No backtest data", ha="center", va="center")
        return _save(fig, out_dir, "06_backtest_cum")
    daily = bt.groupby("disclosure_date")["net_return"].mean().sort_index()
    cum = (1 + daily).cumprod() - 1
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(cum.index, cum.values * 100, marker="o", color="#2980b9")
    ax.axhline(0, color="gray", lw=0.8)
    ax.set_title("Follow Strategy — Equal-Weight by Disclosure Date")
    ax.set_xlabel("Disclosure Date")
    ax.set_ylabel("Cumulative Return (%)")
    plt.xticks(rotation=45, ha="right")
    return _save(fig, out_dir, "06_backtest_cum")


def plot_event_study(es_df: pd.DataFrame, out_dir: Path) -> Path:
    if es_df.empty:
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.text(0.5, 0.5, "No event study data", ha="center", va="center")
        return _save(fig, out_dir, "07_event_study")
    summary = es_df.groupby("event_window_day")["abnormal_return"].agg(["mean", "count"])
    fig, ax = plt.subplots(figsize=(7, 4))
    x = summary.index.astype(str)
    y = summary["mean"] * 100
    colors = ["#27ae60" if v >= 0 else "#c0392b" for v in y]
    ax.bar(x, y, color=colors)
    ax.axhline(0, color="gray", lw=0.8)
    ax.set_title("Event Study — Mean Abnormal Return by Window")
    ax.set_xlabel("Event Window (trading days)")
    ax.set_ylabel("AR (%)")
    for i, (xi, yi, n) in enumerate(zip(x, y, summary["count"])):
        ax.text(i, yi, f"n={int(n)}", ha="center", va="bottom" if yi >= 0 else "top", fontsize=8)
    return _save(fig, out_dir, "07_event_study")


def plot_disclosure_timeline(trades: pd.DataFrame, out_dir: Path, trump_df: pd.DataFrame | None = None) -> Path:
    df = _with_notional(trades, trump_df)
    df = df[df["action"].isin(["purchase", "sale"])].copy()
    df = df.dropna(subset=["disclosure_date"])

    by_disc = (
        df.groupby("disclosure_date")
        .agg(trades=("notional", "size"), notional=("notional", "sum"))
        .sort_index()
    )
    total_notional = df["notional"].sum()
    total_trades = len(df)

    x = np.arange(len(by_disc))
    labels = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in by_disc.index]

    fig, ax1 = plt.subplots(figsize=(10, 5))
    ax2 = ax1.twinx()
    bars = ax1.bar(x, by_disc["notional"], width=0.65, color="#8e44ad", alpha=0.9, label="Disclosed notional")
    ax2.plot(x, by_disc["trades"], color="#2c3e50", marker="D", lw=2, ms=6, label="Trade count")

    for i, (_, row) in enumerate(by_disc.iterrows()):
        ax1.text(
            i,
            row["notional"] + by_disc["notional"].max() * 0.02,
            _mpl_label(f"{_fmt_notional_short(row['notional'])}\n({int(row['trades']):,} trades)"),
            ha="center",
            va="bottom",
            fontsize=9,
            fontweight="bold",
        )

    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=30, ha="right")
    ax1.set_ylabel("Disclosed notional ($) — primary", fontweight="bold")
    ax2.set_ylabel("Trade count")
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: _fmt_notional_short(v)))
    ax1.set_title(
        _mpl_label(
            f"Trades by OGE Disclosure Date · Total {_fmt_notional_short(total_notional)} · {total_trades:,} trades"
        )
    )
    ax1.set_xlabel("Disclosure date")
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=8)
    ax1.set_ylim(0, by_disc["notional"].max() * 1.22 if len(by_disc) else 1)
    return _save(fig, out_dir, "08_disclosure_timeline")


def plot_cumulative_pnl(cum_df: pd.DataFrame, title: str, out_dir: Path, prefix: str) -> Path:
    horizons = [1, 3, 5, 10, 20, 30]
    fig, ax = plt.subplots(figsize=(11, 5))
    colors = plt.cm.tab10(np.linspace(0, 1, len(horizons)))
    max_abs = 0.0
    series_data = []
    for h, c in zip(horizons, colors):
        col = f"cum_pnl_{h}d"
        if col not in cum_df.columns:
            continue
        sub = cum_df.dropna(subset=[col])
        if sub.empty:
            continue
        vals = sub[col].values
        max_abs = max(max_abs, float(np.nanmax(np.abs(vals))))
        series_data.append((h, c, sub, vals))
    scale, ylab = (1e6, "Cumulative PnL ($M)") if max_abs >= 5e5 else (1e3, "Cumulative PnL ($K)")
    if max_abs < 5e3:
        scale, ylab = (1.0, "Cumulative PnL ($)")
    for h, c, sub, vals in series_data:
        ax.plot(sub["date"], vals / scale, label=f"+{h}td", color=c, lw=1.8)
    ax.axhline(0, color="gray", lw=0.8)
    ax.set_title(title)
    ax.set_xlabel("Date")
    ax.set_ylabel(ylab)
    ax.legend(title="Horizon")
    plt.xticks(rotation=45, ha="right")
    return _save(fig, out_dir, prefix)


def plot_buy_sell_bars(
    buy_summary: pd.DataFrame,
    sell_summary: pd.DataFrame,
    title: str,
    out_dir: Path,
    prefix: str,
) -> Path:
    fig, ax = plt.subplots(figsize=(10, 4.5))
    horizons = buy_summary["horizon_days"].astype(str) + "d"
    x = np.arange(len(horizons))
    w = 0.35
    buy_y = buy_summary["notional_weighted_return"].values * 100
    sell_y = sell_summary["notional_weighted_return"].values * 100
    ax.bar(x - w / 2, buy_y, w, label="BUY (long)", color="#27ae60")
    ax.bar(x + w / 2, sell_y, w, label="SELL (short)", color="#c0392b")
    ax.axhline(0, color="gray", lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(horizons)
    ax.set_title(title)
    ax.set_xlabel("Trading days after anchor")
    ax.set_ylabel("Notional-weighted return (%)")
    ax.legend()
    return _save(fig, out_dir, prefix)


def plot_follow_buy_sell_bars(
    buy_summary: pd.DataFrame,
    sell_summary: pd.DataFrame,
    out_dir: Path,
    prefix: str = "14_follow_buy_vs_sell",
) -> Path:
    return plot_buy_sell_bars(
        buy_summary,
        sell_summary,
        "Follow Trump — Buy vs Sell (notional-weighted, anchor = disclosure date)",
        out_dir,
        prefix,
    )


def plot_notional_weighted_bars(summary: pd.DataFrame, title: str, out_dir: Path, prefix: str) -> Path:
    fig, ax = plt.subplots(figsize=(9, 4))
    x = summary["horizon_days"].astype(str) + "d"
    y = summary["notional_weighted_return"] * 100
    colors = ["#27ae60" if v >= 0 else "#c0392b" for v in y]
    ax.bar(x, y, color=colors)
    ax.axhline(0, color="gray", lw=0.8)
    ax.set_title(title)
    ax.set_xlabel("Trading days after anchor")
    ax.set_ylabel("Notional-weighted return (%)")
    return _save(fig, out_dir, prefix)


def plot_holding_days(matched_lots: pd.DataFrame, ticker_summary: pd.DataFrame, out_dir: Path) -> Path | None:
    if matched_lots.empty:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    hd = matched_lots["holding_days"].dropna()
    axes[0].hist(hd, bins=50, color="#e67e22", edgecolor="white")
    axes[0].axvline(hd.median(), color="#2c3e50", ls="--", label=f"Median {hd.median():.0f}d")
    axes[0].set_title("FIFO Holding Period (matched lots)")
    axes[0].set_xlabel("Days (sell − buy)")
    axes[0].legend()

    top = ticker_summary.nlargest(12, "n_matched_pairs").dropna(subset=["avg_holding_days"])
    if not top.empty:
        top.sort_values("avg_holding_days").plot(
            kind="barh", y="avg_holding_days", ax=axes[1], color="#16a085", legend=False
        )
        axes[1].set_title("Avg Holding Days by Ticker (top pairs)")
        axes[1].set_xlabel("Days")
    fig.suptitle("FIFO Lot-Matched Holding Periods")
    return _save(fig, out_dir, "09_holding_days")


def plot_media_match_timelines(timelines: list[dict], out_dir: Path) -> Path | None:
    """Swimlane: buy / Trump post / sell-or-hold for top matched tickers."""
    if not timelines:
        return None

    n = len(timelines)
    fig, axes = plt.subplots(n, 1, figsize=(14, 3.2 * n), squeeze=False)
    buy_c, sell_c, post_c, hold_c = "#2ecc71", "#e74c3c", "#3498db", "#ecf0f1"

    for ax, tl in zip(axes.flat, timelines):
        ticker = tl["ticker"]
        t0 = tl.get("first_buy")
        t1 = tl.get("hold_end")
        if t0 is None:
            ax.set_visible(False)
            continue
        t0 = pd.Timestamp(t0)
        t1 = pd.Timestamp(t1)
        ax.axhspan(0.35, 0.65, xmin=0, xmax=1, color=hold_c, alpha=0.5, zorder=0)
        if t1 > t0:
            ax.barh(0.5, (t1 - t0).days, left=0, height=0.18, color="#bdc3c7", alpha=0.6, zorder=1)

        def _x(d: pd.Timestamp) -> float:
            return (pd.Timestamp(d) - t0).days

        span = max((t1 - t0).days, 1)
        for b in tl.get("buys", []):
            x = _x(b["date"])
            ax.scatter(x, 0.72, marker="v", s=120, color=buy_c, zorder=3)
            ax.text(x, 0.78, _mpl_label(f"Buy {_fmt_notional_short(b['notional'])}"), ha="center", va="bottom", fontsize=8, fontweight="bold")

        for s in tl.get("sells", []):
            x = _x(s["date"])
            ax.scatter(x, 0.28, marker="^", s=120, color=sell_c, zorder=3)
            ax.text(x, 0.22, _mpl_label(f"Sell {_fmt_notional_short(s['notional'])}"), ha="center", va="top", fontsize=8, fontweight="bold")

        for i, p in enumerate(tl.get("posts", [])):
            x = _x(p["date"])
            y = 0.5 + (0.12 if i % 2 == 0 else -0.12)
            ax.scatter(x, y, marker="D", s=70, color=post_c, zorder=4)
            ax.annotate(
                "Truth",
                (x, y),
                textcoords="offset points",
                xytext=(0, 10 if i % 2 == 0 else -12),
                ha="center",
                fontsize=7,
                color=post_c,
            )

        end_label = "Open" if tl.get("still_open") else "Closed"
        ax.text(span * 0.98, 0.5, end_label, ha="right", va="center", fontsize=9, fontweight="bold", color="#2c3e50")

        ax.set_xlim(-2, span + 5)
        ax.set_ylim(0, 1)
        ax.set_yticks([])
        ax.set_xlabel("Days from first buy")
        ax.set_title(
            _mpl_label(f"{ticker} · max matched trade {_fmt_notional_short(tl.get('max_trade_notional', 0))} · {end_label}"),
            fontsize=11,
            loc="left",
        )
        ax.grid(axis="x", alpha=0.3)

    fig.suptitle("Top 3 Matched Tickers — Buy / Trump Post / Sell or Hold", fontsize=12, y=1.01)
    fig.subplots_adjust(hspace=0.55)
    return _save(fig, out_dir, "16_media_match_timelines")


def plot_open_holdings_snapshot(holdings: pd.DataFrame, out_dir: Path) -> Path | None:
    """Net-long open holdings: notional bars + horizon return heatmap-style table."""
    if holdings.empty:
        return None

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5), gridspec_kw={"width_ratios": [1.1, 1.4]})
    df = holdings.sort_values("net_notional", ascending=True)

    axes[0].barh(df["ticker"], df["net_notional"] / 1e6, color="#8e44ad", alpha=0.9)
    axes[0].set_xlabel("Net notional ($M)")
    axes[0].set_title("Open Holdings Top 10 (FIFO unmatched buys)")
    for i, (_, r) in enumerate(df.iterrows()):
        axes[0].text(r["net_notional"] / 1e6 + 0.02, i, f"${r['net_notional']/1e6:.2f}M", va="center", fontsize=8)

    horizons = [1, 3, 5, 10, 20, 30]
    ret_cols = [f"ret_{h}d" for h in horizons if f"ret_{h}d" in df.columns]
    if ret_cols:
        mat = df.set_index("ticker")[ret_cols].astype(float) * 100
        mat.columns = [c.replace("ret_", "+").replace("d", "d") for c in mat.columns]
        im = axes[1].imshow(mat.values, aspect="auto", cmap="RdYlGn", vmin=-5, vmax=5)
        axes[1].set_xticks(range(len(mat.columns)))
        axes[1].set_xticklabels(mat.columns, rotation=45, ha="right")
        axes[1].set_yticks(range(len(mat.index)))
        axes[1].set_yticklabels(mat.index)
        for i in range(mat.shape[0]):
            for j in range(mat.shape[1]):
                v = mat.iloc[i, j]
                if pd.notna(v):
                    axes[1].text(j, i, f"{v:.1f}%", ha="center", va="center", fontsize=8, color="#222")
        axes[1].set_title("Return since earliest open buy (txn anchor)")
        fig.colorbar(im, ax=axes[1], fraction=0.046, label="Return %")
    else:
        axes[1].axis("off")

    fig.suptitle("Trump Current Net-Long Portfolio Snapshot", fontsize=12)
    return _save(fig, out_dir, "17_open_holdings")


def _pnl_scale(pnl: pd.Series) -> tuple[float, str]:
    mx = float(pnl.abs().max()) if len(pnl) else 0.0
    if mx >= 5e5:
        return 1e6, "$M"
    if mx >= 5e3:
        return 1e3, "$K"
    return 1.0, "$"


def plot_daily_accumulated_pnl(
    daily: pd.DataFrame,
    out_dir: Path,
    title: str,
    prefix: str = "20_daily_accumulated_pnl",
) -> Path | None:
    if daily.empty or "cum_pnl" not in daily.columns:
        return None
    df = daily.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    scale, unit = _pnl_scale(df["cum_pnl"])
    fig, ax = plt.subplots(figsize=(12, 5))
    y = df["cum_pnl"] / scale
    ax.plot(df["date"], y, color="#1e4d8c", lw=2.2, label="Accumulated PnL")
    ax.fill_between(df["date"], 0, y, where=df["cum_pnl"] >= 0, alpha=0.12, color="#2ecc71")
    ax.fill_between(df["date"], 0, y, where=df["cum_pnl"] < 0, alpha=0.12, color="#e74c3c")
    ax.axhline(0, color="gray", lw=0.8)
    ax.set_title(_mpl_label(title))
    ax.set_xlabel("Date")
    ax.set_ylabel(f"Accumulated PnL ({unit})")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    plt.xticks(rotation=45, ha="right")
    return _save(fig, out_dir, prefix)


def _monthly_top3_other_rows(
    ticker_daily: pd.DataFrame,
    start: str | pd.Timestamp = "2024-01-01",
) -> pd.DataFrame:
    td = ticker_daily.copy()
    td["date"] = pd.to_datetime(td["date"])
    td = td[td["date"] >= pd.Timestamp(start).normalize()].copy()
    if td.empty:
        return pd.DataFrame()
    td["month"] = td["date"].dt.to_period("M").dt.to_timestamp()
    monthly = td.groupby(["month", "ticker"], as_index=False)["daily_pnl"].sum()
    records: list[dict[str, Any]] = []
    for month, g in monthly.groupby("month", sort=True):
        g = g[g["daily_pnl"].notna()].copy()
        if g.empty:
            continue
        g = g.assign(_abs=g["daily_pnl"].abs()).sort_values("_abs", ascending=False)
        top = g.head(3)
        other = float(g["daily_pnl"].sum() - top["daily_pnl"].sum())
        vals = top["daily_pnl"].tolist()
        names = top["ticker"].astype(str).tolist()
        while len(vals) < 3:
            vals.append(0.0)
            names.append("")
        records.append(
            {
                "month": month,
                "pnl_1": vals[0],
                "pnl_2": vals[1],
                "pnl_3": vals[2],
                "other": other,
                "ticker_1": names[0],
                "ticker_2": names[1],
                "ticker_3": names[2],
            }
        )
    return pd.DataFrame(records)


def plot_monthly_pnl_top3_bars(
    ticker_daily: pd.DataFrame,
    out_dir: Path,
    title: str,
    prefix: str = "21_monthly_pnl_top3_bars",
    start: str | pd.Timestamp = "2024-01-01",
) -> Path | None:
    wide = _monthly_top3_other_rows(ticker_daily, start=start)
    if wide.empty:
        return None
    for legacy_name in ("21_daily_pnl_top3_stack.png", "21_weekly_pnl_top3_bars.png"):
        legacy = out_dir / legacy_name
        if legacy.exists():
            try:
                legacy.unlink()
            except OSError:
                pass
    wide = wide.sort_values("month")
    scale, unit = _pnl_scale(
        pd.concat([wide["pnl_1"], wide["pnl_2"], wide["pnl_3"], wide["other"]], ignore_index=True)
    )
    n = len(wide)
    fig_w = max(10, min(22, n * 0.55))
    fig, ax = plt.subplots(figsize=(fig_w, 5.5))
    x = np.arange(n)
    bar_w = 0.72
    b1 = wide["pnl_1"] / scale
    b2 = wide["pnl_2"] / scale
    b3 = wide["pnl_3"] / scale
    bo = wide["other"] / scale
    colors = ["#2980b9", "#27ae60", "#e67e22", "#95a5a6"]
    ax.bar(x, b1, bar_w, label="Top 1", color=colors[0])
    ax.bar(x, b2, bar_w, bottom=b1, label="Top 2", color=colors[1])
    ax.bar(x, b3, bar_w, bottom=b1 + b2, label="Top 3", color=colors[2])
    ax.bar(x, bo, bar_w, bottom=b1 + b2 + b3, label="Other", color=colors[3])
    ax.axhline(0, color="gray", lw=0.8)
    labels = pd.to_datetime(wide["month"]).dt.strftime("%Y-%m")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_title(_mpl_label(title))
    ax.set_xlabel("Month")
    ax.set_ylabel(f"Monthly PnL ({unit})")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, axis="y", alpha=0.25)
    fig.tight_layout()
    return _save(fig, out_dir, prefix)


def plot_portfolio_daily_timeseries(daily: pd.DataFrame, out_dir: Path) -> Path | None:
    """Gross-long FIFO portfolio: MTM exposure and cumulative PnL over time."""
    if daily.empty:
        return None

    df = daily.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios": [1.1, 1]})

    ax0 = axes[0]
    cost_m = df["position_cost"] / 1e6
    mtm_m = df["position_mtm"] / 1e6
    ax0.fill_between(df["date"], 0, mtm_m, alpha=0.18, color="#8e44ad")
    ax0.plot(df["date"], mtm_m, color="#8e44ad", lw=2.0, label="MTM value")
    ax0.plot(df["date"], cost_m, color="#566573", lw=1.4, ls="--", label="Cost basis (amount_min)")
    ax0.set_ylabel("Position size ($M)")
    ax0.set_title("Gross-long portfolio size (FIFO, EOD)")
    ax0.legend(loc="upper left", fontsize=9)
    ax0.grid(True, alpha=0.3)

    ax1 = axes[1]
    pnl = df["cum_pnl"]
    scale = 1e6 if pnl.abs().max() >= 5e5 else 1e3
    ylab = "Cumulative PnL ($M)" if scale == 1e6 else "Cumulative PnL ($K)"
    ax1.plot(df["date"], pnl / scale, color="#1e4d8c", lw=2.0, label="Cumulative PnL")
    ax1.fill_between(
        df["date"],
        0,
        pnl / scale,
        where=pnl >= 0,
        alpha=0.15,
        color="#2ecc71",
        interpolate=True,
    )
    ax1.fill_between(
        df["date"],
        0,
        pnl / scale,
        where=pnl < 0,
        alpha=0.15,
        color="#e74c3c",
        interpolate=True,
    )
    ax1.axhline(0, color="gray", lw=0.8)
    ax1.set_ylabel(ylab)
    ax1.set_xlabel("Date")
    ax1.set_title("Portfolio cumulative PnL (sum of daily MTM changes)")
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(True, alpha=0.3)

    for ax in axes:
        for label in ax.get_xticklabels():
            label.set_rotation(45)
            label.set_ha("right")

    fig.suptitle("Trump Portfolio — Position Size & Cumulative PnL", fontsize=12, y=1.01)
    fig.tight_layout()
    return _save(fig, out_dir, "18_portfolio_timeseries")


def generate_all_charts(
    trades: pd.DataFrame,
    returns_df: pd.DataFrame,
    bt: pd.DataFrame,
    es_df: pd.DataFrame,
    out_dir: Path,
    matched_lots: pd.DataFrame | None = None,
    ticker_summary: pd.DataFrame | None = None,
    return_analysis: dict | None = None,
    media_timelines: list | None = None,
    open_holdings: pd.DataFrame | None = None,
    portfolio_daily: pd.DataFrame | None = None,
    ticker_daily_pnl: pd.DataFrame | None = None,
    pnl_daily_for_accum: pd.DataFrame | None = None,
) -> list[Path]:
    trump_df = return_analysis.get("trump_timing") if return_analysis else None
    paths = [
        plot_trade_volume_monthly(trades, out_dir, trump_df=trump_df),
        plot_reveal_lag(returns_df, out_dir),
        plot_top_tickers(trades, out_dir, returns_df=trump_df),
        plot_buy_sell(trades, out_dir, trump_df=trump_df),
    ]
    if open_holdings is not None and not open_holdings.empty:
        oh = plot_open_holdings_snapshot(open_holdings, out_dir)
        if oh:
            paths.append(oh)
    if portfolio_daily is not None and not portfolio_daily.empty:
        pt = plot_portfolio_daily_timeseries(portfolio_daily, out_dir)
        if pt:
            paths.append(pt)
    accum_src = pnl_daily_for_accum if pnl_daily_for_accum is not None and not pnl_daily_for_accum.empty else portfolio_daily
    if accum_src is not None and not accum_src.empty:
        acc = plot_daily_accumulated_pnl(
            accum_src,
            out_dir,
            "Trump portfolio — daily accumulated PnL (FIFO MTM through analysis end)",
        )
        if acc:
            paths.append(acc)
    if ticker_daily_pnl is not None and not ticker_daily_pnl.empty:
        mo = plot_monthly_pnl_top3_bars(
            ticker_daily_pnl,
            out_dir,
            "Monthly PnL (2024→) — top 3 tickers by |PnL| + Other (FIFO daily sum)",
        )
        if mo:
            paths.append(mo)
    paths += [
        plot_post_returns(returns_df, out_dir),
        plot_backtest_cum(bt, out_dir),
        plot_event_study(es_df, out_dir),
        plot_disclosure_timeline(trades, out_dir, trump_df=trump_df),
    ]
    if media_timelines:
        mt = plot_media_match_timelines(media_timelines, out_dir)
        if mt:
            paths.append(mt)
    if matched_lots is not None and ticker_summary is not None:
        h = plot_holding_days(matched_lots, ticker_summary, out_dir)
        if h:
            paths.append(h)
    if return_analysis:
        paths.append(
            plot_notional_weighted_bars(
                return_analysis["trump_summary"],
                "Trump Timing — Notional-Weighted Return (by txn date)",
                out_dir,
                "10_trump_notional_returns",
            )
        )
        if "trump_buy_summary" in return_analysis and "trump_sell_summary" in return_analysis:
            paths.append(
                plot_buy_sell_bars(
                    return_analysis["trump_buy_summary"],
                    return_analysis["trump_sell_summary"],
                    "Trump Timing — Buy vs Sell (anchor = transaction date)",
                    out_dir,
                    "15_trump_buy_vs_sell",
                )
            )
        paths.append(
            plot_notional_weighted_bars(
                return_analysis["follow_summary"],
                "Follow Trump — Notional-Weighted Return (by disclosure date)",
                out_dir,
                "11_follow_notional_returns",
            )
        )
        paths.append(
            plot_cumulative_pnl(
                return_analysis["trump_cumulative"],
                "Trump Timing — Cumulative PnL (anchor = transaction date)",
                out_dir,
                "12_trump_cumulative_pnl",
            )
        )
        paths.append(
            plot_cumulative_pnl(
                return_analysis["follow_cumulative"],
                "Follow Trump — Cumulative PnL (anchor = disclosure date)",
                out_dir,
                "13_follow_cumulative_pnl",
            )
        )
        if "follow_buy_summary" in return_analysis and "follow_sell_summary" in return_analysis:
            paths.append(
                plot_follow_buy_sell_bars(
                    return_analysis["follow_buy_summary"],
                    return_analysis["follow_sell_summary"],
                    out_dir,
                )
            )
    return paths
