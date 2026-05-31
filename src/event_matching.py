"""Match Trump news / social posts to equity trades (trade-centric)."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from .disclosures import load_settings

DISCLOSURE_NEWS = re.compile(
    r"disclosure|278-?t|oge\b|periodic transaction|ptr\b|conflict of interest|"
    r"financial disclosure|sec filing|stock (trade|holding|portfolio)",
    re.I,
)

STOCK_KEYWORDS = re.compile(
    r"(stock market|stocks?|equit(y|ies)|portfolio|disclosure|278-?t|oge\b|"
    r"sec filing|financial disclosure|insider trad|wall street|nyse|nasdaq|"
    r"shareholders?|\betf\b|mutual fund|conflict of interest|"
    r"periodic transaction|traded shares|sold shares|bought shares|"
    r"truth social.*stock|tweet.*stock|post.*stock)",
    re.I,
)

TRUMP_CONTEXT = re.compile(r"\btrump\b|donald j\.? trump|realDonaldTrump|@potus", re.I)

TICKER_STOP = frozenset(
    {"A", "AI", "AM", "AN", "AS", "AT", "BE", "BY", "CEO", "CO", "DJT", "DO", "ETF", "EU", "EV",
     "FC", "FD", "FO", "FX", "GDP", "GO", "GT", "HE", "IF", "IN", "IP", "IQ", "IRS", "IS", "IT",
     "JP", "LLC", "LP", "MA", "ME", "MP", "MR", "MS", "NE", "NO", "OF", "OG", "OH", "OK", "ON",
     "OR", "PA", "PC", "PM", "PR", "PT", "RE", "RS", "RT", "SA", "SE", "SO", "TD", "THE", "TO",
     "TV", "UK", "UN", "UP", "US", "USA", "UTC", "VS", "WSJ", "XI"}
)

# Tickers that match common English when text is uppercased (strict rules apply).
SHORT_WORD_TICKERS = frozenset(
    {
        "A", "BE", "HE", "I", "IT", "MAN", "MO", "ON", "OR", "S", "T",
        "AM", "AN", "AS", "AT", "BY", "DO", "GO", "IF", "IN", "IS", "ME", "NO", "OF", "SO", "TO", "UP", "PM",
    }
)

AMBIGUOUS_TICKERS = frozenset({"NOW", "ARE", "J", "MAIN", "MA", "ON", "ALL", "KEY", "W", "IT", "AI", "GO"})

TICKER_FALSE_PHRASES: dict[str, list[re.Pattern]] = {
    "DASH": [re.compile(r"\bDASH\s+OF\b", re.I)],
    "MO": [re.compile(r"\bMO\s+BROOKS\b", re.I), re.compile(r"\bREP\.?\s*MO\b", re.I)],
    "PM": [
        re.compile(r"\bUK\s+PM\b", re.I),
        re.compile(r"\bPM\s+MODI\b", re.I),
        re.compile(r"\bPM\s+STARMER\b", re.I),
        re.compile(r"\bSPEAKS\s+TO\s+UK\s+PM\b", re.I),
    ],
}

STOCK_CONTEXT_RE = re.compile(
    r"\$|\(\s*(?:NYSE|NASDAQ|AMEX)\s*:\s*[A-Z]{1,5}\s*\)|"
    r"\b(?:stock|stocks|shares|equity|equities|ticker|etf|nyse|nasdaq)\b|"
    r"\b[A-Z]{1,5}\s+(?:stock|stocks|shares|inc\.?|corp\.?)\b",
    re.I,
)

TICKER_SPECIFIC_LINK_TYPES = {
    "ticker_mentioned",
    "social_near_trade",
    "social_near_disclosure",
}

# Legacy news link types (no longer created when match_trump_posts_only=true).
NEWS_LINK_TYPES = {
    "news_near_trade",
    "news_near_disclosure",
    "news_disclosure_general",
}

TICKER_LINK_TYPES = TICKER_SPECIFIC_LINK_TYPES

SOCIAL_PLATFORMS = {"truth_social", "post", "x_twitter"}
TRUMP_POST_PLATFORMS = SOCIAL_PLATFORMS
NEWS_PLATFORMS = {"google_news", "news"}


def _naive_ts(ts: pd.Timestamp | Any) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("US/Eastern").tz_localize(None)
    return t.normalize()


def _company_name_in_text(asset_name: str, text: str, *, min_len: int = 8) -> bool:
    name = str(asset_name or "").strip()
    if len(name) < min_len:
        return False
    blob = str(text or "")
    norm = re.sub(r"\s+", " ", name)
    if len(norm) >= min_len and norm.lower() in blob.lower():
        return True
    words = [w for w in re.sub(r"[^A-Za-z0-9& ]", " ", name).split() if len(w) >= 3]
    if len(words) >= 2:
        phrase = " ".join(words[:2])
        if len(phrase) >= 6 and phrase.lower() in blob.lower():
            return True
    return False


def _explicit_ticker_marker(t: str, upper: str) -> bool:
    if f"${t}" in upper:
        return True
    if re.search(rf"\(\s*{re.escape(t)}\s*\)", upper):
        return True
    if re.search(rf"\(\s*(?:NYSE|NASDAQ|AMEX)\s*:\s*{re.escape(t)}\s*\)", upper):
        return True
    if re.search(rf"\b{re.escape(t)}\s+(?:stock|stocks|shares)\b", upper):
        return True
    if re.search(rf"\b(?:stock|stocks|shares)\s+(?:in\s+)?{re.escape(t)}\b", upper):
        return True
    return False


def strict_ticker_in_event(
    ticker: str,
    text: str,
    tickers_mentioned: str = "",
    *,
    require_in_text: bool = False,
    platform: str = "",
    asset_name: str = "",
) -> bool:
    """True only when text credibly references this equity ticker (not English homographs)."""
    t = str(ticker).upper()
    blob = str(text or "")
    upper = blob.upper()
    if not t or t in {"NAN", "NONE"}:
        return False

    if f"${t}" in upper:
        return True
    if re.search(rf"\(\s*{re.escape(t)}\s*\)", upper):
        return True
    if re.search(rf"\(\s*(?:NYSE|NASDAQ|AMEX)\s*:\s*{re.escape(t)}\s*\)", upper):
        return True

    for pat in TICKER_FALSE_PHRASES.get(t, []):
        if pat.search(blob):
            return False

    is_social = str(platform) in SOCIAL_PLATFORMS
    has_company = _company_name_in_text(asset_name, blob)

    # Truth / social: company name or explicit $TICKER only.
    if is_social:
        return has_company or f"${t}" in upper

    if _explicit_ticker_marker(t, upper):
        return True

    if len(t) == 1:
        return False

    if t in SHORT_WORD_TICKERS or (len(t) <= 2 and t in TICKER_STOP):
        return has_company

    if t in AMBIGUOUS_TICKERS:
        return has_company

    if re.search(rf"\b{re.escape(t)}\b", upper):
        return True

    if require_in_text:
        return False
    mentioned = {x.strip().upper() for x in str(tickers_mentioned or "").split(",") if x.strip()}
    return t in mentioned


def build_ticker_lexicon(trades: pd.DataFrame) -> dict[str, set[str]]:
    lex: dict[str, set[str]] = {}
    if trades.empty or "ticker" not in trades.columns:
        return lex
    for _, row in trades.dropna(subset=["ticker"]).iterrows():
        ticker = str(row["ticker"]).upper()
        if not ticker or ticker in TICKER_STOP or len(ticker) > 5:
            continue
        aliases = lex.setdefault(ticker, {ticker})
        aliases.add(f"${ticker}")
        name = str(row.get("asset_name") or "")
        norm = re.sub(r"[^A-Z0-9& ]", " ", name.upper())
        norm = re.sub(r"\s+", " ", norm).strip()
        for tok in norm.split():
            if len(tok) >= 4 and tok not in {"INC", "CORP", "CLASS", "COMMON", "COM"}:
                aliases.add(tok)
        parts = [p for p in norm.split() if len(p) >= 3][:2]
        if parts:
            aliases.add(" ".join(parts))
    return lex


def mention_tickers(text: str, lexicon: dict[str, set[str]]) -> list[str]:
    if not text or not lexicon:
        return []
    upper = text.upper()
    found: set[str] = set()
    for m in re.finditer(r"\$([A-Z]{1,5})\b", upper):
        t = m.group(1)
        if t in lexicon:
            found.add(t)
    for tok in re.findall(r"\b[A-Z]{2,5}\b", upper):
        if tok in TICKER_STOP:
            continue
        if tok in lexicon:
            found.add(tok)
    if not found:
        for ticker, aliases in lexicon.items():
            for alias in aliases:
                if len(alias) <= 5:
                    continue
                if alias.upper() in upper:
                    found.add(ticker)
                    break
    return sorted(found)


def is_stock_related(text: str, platform: str = "", query: str = "") -> bool:
    blob = f"{text} {query}"
    if STOCK_KEYWORDS.search(blob):
        return True
    if platform in {"google_news", "news"} and TRUMP_CONTEXT.search(blob):
        if re.search(r"\b(stock|disclosure|portfolio|invest|equit|278|oge)\b", blob, re.I):
            return True
    if platform in {"truth_social", "x_twitter", "post"} and re.search(
        r"\b(market|tariff|economy|fed\b|interest rate|inflation)\b", blob, re.I
    ):
        return True
    return False


def enrich_events_with_tickers(events: pd.DataFrame, lexicon: dict[str, set[str]]) -> pd.DataFrame:
    if events.empty:
        return events
    out = events.copy()
    if "tickers_mentioned" not in out.columns:
        out["tickers_mentioned"] = ""
    out["tickers_mentioned"] = out.apply(
        lambda r: ",".join(
            sorted(
                set(
                    [t.strip() for t in str(r.get("tickers_mentioned", "")).split(",") if t.strip()]
                    + mention_tickers(str(r.get("text", "")), lexicon)
                )
            )
        ),
        axis=1,
    )
    if "stock_related" not in out.columns:
        out["stock_related"] = False
    out["stock_related"] = out.apply(
        lambda r: bool(r.get("stock_related"))
        or bool(r["tickers_mentioned"])
        or is_stock_related(str(r.get("text", "")), str(r.get("platform", "")), str(r.get("query", ""))),
        axis=1,
    )
    return out


def _in_window(ev_time: pd.Timestamp, anchor: pd.Timestamp, days: int) -> bool:
    if pd.isna(anchor):
        return False
    return abs((ev_time - anchor).days) <= days


def _is_social(ev: pd.Series) -> bool:
    return str(ev.get("event_type", "")) == "post" or str(ev.get("platform", "")) in SOCIAL_PLATFORMS


def _is_trump_post(ev: pd.Series) -> bool:
    """True for posts authored by Trump (Truth Social archive / RSS / manual)."""
    return _is_social(ev) and str(ev.get("platform", "")) in TRUMP_POST_PLATFORMS


def _is_news(ev: pd.Series) -> bool:
    return str(ev.get("event_type", "")) == "news" or str(ev.get("platform", "")) in NEWS_PLATFORMS


def _match_trump_posts_only(settings: dict[str, Any]) -> bool:
    return bool(settings.get("social", {}).get("match_trump_posts_only", True))


def align_events_to_trades(
    trades: pd.DataFrame,
    events: pd.DataFrame,
    settings: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Trade-centric: match each trade to Trump's own posts within ±N days of txn / disclosure."""
    settings = settings or load_settings()
    posts_only = _match_trump_posts_only(settings)
    mw = settings.get("social", {}).get("match_windows", {})
    trade_win = int(mw.get("trade_days", 30))
    disc_win = int(mw.get("disclosure_days", 30))

    if trades.empty or events.empty:
        return pd.DataFrame()

    tdf = trades.copy()
    tdf["transaction_date"] = pd.to_datetime(tdf["transaction_date"])
    tdf["disclosure_date"] = pd.to_datetime(tdf["disclosure_date"])

    ev = events.copy()
    ev["_ev_time"] = pd.to_datetime(ev["event_time"]).dt.tz_localize(None)
    if posts_only:
        ev = ev[ev.apply(_is_trump_post, axis=1)].copy()
    ev = ev.sort_values("_ev_time")

    if ev.empty:
        return pd.DataFrame()

    links: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(trade_id: str, event_id: str, link_type: str, event_time: pd.Timestamp, lag_days: int) -> None:
        key = (trade_id, event_id, link_type)
        if key in seen:
            return
        seen.add(key)
        links.append(
            {
                "trade_id": trade_id,
                "event_id": event_id,
                "link_type": link_type,
                "event_time": event_time,
                "lag_days": lag_days,
            }
        )

    global_lo = ev["_ev_time"].min() - pd.Timedelta(days=max(trade_win, disc_win))
    global_hi = ev["_ev_time"].max() + pd.Timedelta(days=max(trade_win, disc_win))

    for _, trade in tdf.iterrows():
        ticker = str(trade.get("ticker") or "").upper()
        if not ticker:
            continue
        tid = str(trade["trade_id"])
        asset_name = str(trade.get("asset_name") or "")
        txn = _naive_ts(trade["transaction_date"])
        disc = _naive_ts(trade["disclosure_date"]) if pd.notna(trade["disclosure_date"]) else pd.NaT

        if pd.notna(disc):
            add(tid, f"disc_{tid}", "disclosure_event", disc, int((disc - txn).days))

        lo = txn - pd.Timedelta(days=trade_win)
        hi = txn + pd.Timedelta(days=trade_win)
        if pd.notna(disc):
            lo = min(lo, disc - pd.Timedelta(days=disc_win))
            hi = max(hi, disc + pd.Timedelta(days=disc_win))
        lo = max(lo, global_lo)
        hi = min(hi, global_hi)

        candidates = ev[(ev["_ev_time"] >= lo) & (ev["_ev_time"] <= hi)]
        for _, erow in candidates.iterrows():
            ev_time = _naive_ts(erow["_ev_time"])
            ev_id = str(erow["event_id"])
            text = str(erow.get("text", ""))
            tickers_ev = str(erow.get("tickers_mentioned", ""))
            platform = str(erow.get("platform", ""))

            ticker_hit = strict_ticker_in_event(
                ticker,
                text,
                tickers_ev,
                require_in_text=True,
                platform=platform,
                asset_name=asset_name,
            )
            near_txn = _in_window(ev_time, txn, trade_win)
            near_disc = pd.notna(disc) and _in_window(ev_time, disc, disc_win)

            if not ticker_hit:
                continue

            if near_txn:
                add(tid, ev_id, "social_near_trade", ev_time, int((ev_time - txn).days))
            if near_disc:
                add(tid, ev_id, "social_near_disclosure", ev_time, int((ev_time - disc).days))
            add(tid, ev_id, "ticker_mentioned", ev_time, int((ev_time - txn).days))

    return pd.DataFrame(links)


def summarize_event_links(links: pd.DataFrame, events: pd.DataFrame, trades: pd.DataFrame) -> dict[str, Any]:
    if links.empty:
        return {
            "n_links": 0,
            "n_trades_with_media": 0,
            "n_trades_with_ticker_specific": 0,
            "by_link_type": {},
            "by_platform": {},
        }

    media = links[links["link_type"].isin(TICKER_SPECIFIC_LINK_TYPES)]
    ticker_specific = media
    merged = media.merge(events[["event_id", "platform", "text"]], on="event_id", how="left")

    by_platform_links = merged["platform"].value_counts().to_dict() if not merged.empty else {}
    by_platform_events = (
        merged.drop_duplicates("event_id")["platform"].value_counts().to_dict() if not merged.empty else {}
    )

    return {
        "n_links": int(len(links)),
        "n_media_links": int(len(media)),
        "n_events_loaded": int(len(events)),
        "n_unique_events_linked": int(media["event_id"].nunique()),
        "n_unique_events_ticker_specific": int(ticker_specific["event_id"].nunique()) if not ticker_specific.empty else 0,
        "n_trades_with_media": int(media["trade_id"].nunique()),
        "n_trades_with_ticker_specific": int(ticker_specific["trade_id"].nunique()) if not ticker_specific.empty else 0,
        "n_trades_total": int(len(trades)),
        "by_link_type": links["link_type"].value_counts().to_dict(),
        "by_platform_link_rows": by_platform_links,
        "by_platform_unique_events": by_platform_events,
        # Back-compat keys used in older report code
        "n_unique_events": int(media["event_id"].nunique()),
        "by_platform": by_platform_links,
        "sample_events": merged.drop_duplicates("event_id").head(8)[["platform", "text", "event_id"]].to_dict(orient="records"),
    }
