"""Fetch social posts and news events."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import feedparser
import pandas as pd
import requests

from .disclosures import load_settings, project_root
from .event_matching import build_ticker_lexicon, enrich_events_with_tickers, is_stock_related


def _event_id(text: str, event_time: str) -> str:
    return hashlib.md5(f"{event_time}|{text[:80]}".encode()).hexdigest()[:12]


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def load_manual_social(settings: dict[str, Any] | None = None) -> pd.DataFrame:
    """Load Truth Social posts from manual JSON. Skips placeholder/sample entries."""
    settings = settings or load_settings()
    manual_path = project_root() / settings["paths"]["raw_social"] / "truth_social_manual.json"
    if not manual_path.exists():
        return pd.DataFrame()

    posts = json.loads(manual_path.read_text())
    rows = []
    for p in posts:
        if p.get("placeholder") or "/sample/" in str(p.get("url", "")):
            continue
        text = p["text"]
        rows.append(
            {
                "event_id": _event_id(text, p["event_time"]),
                "event_time": pd.to_datetime(p["event_time"], utc=True).tz_convert(settings["timezone"]),
                "platform": "truth_social",
                "text": text,
                "url": p.get("url", ""),
                "tickers_mentioned": p.get("tickers_mentioned", ""),
                "event_type": "post",
                "query": "",
                "stock_related": is_stock_related(text, "truth_social"),
            }
        )
    return pd.DataFrame(rows)


def fetch_truth_social_archive(settings: dict[str, Any] | None = None) -> pd.DataFrame:
    """Load Trump's Truth Social posts from CNN archive (cached locally)."""
    settings = settings or load_settings()
    social_cfg = settings.get("social", {})
    url = social_cfg.get("truth_archive_url", "https://ix.cnn.io/data/truth-social/truth_archive.json")
    cache_path = project_root() / settings["paths"]["raw_social"] / "truth_archive_cache.json"
    inaug = pd.Timestamp(settings["oge"]["inauguration_date"], tz=settings["timezone"])
    end = pd.Timestamp(settings["oge"].get("analysis_end_date", "2026-05-30"), tz=settings["timezone"])

    if cache_path.exists() and cache_path.stat().st_size > 1_000_000:
        posts = json.loads(cache_path.read_text())
    else:
        try:
            resp = requests.get(url, timeout=120, headers={"User-Agent": "TrumpFollowingResearch/1.0"})
            resp.raise_for_status()
            posts = resp.json()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(resp.text)
        except Exception:
            return pd.DataFrame()

    rows = []
    for p in posts:
        try:
            dt = _parse_dt(p["created_at"])
        except Exception:
            continue
        ts = pd.Timestamp(dt).tz_convert(settings["timezone"])
        if ts < inaug or ts > end + pd.Timedelta(days=1):
            continue
        text = re.sub(r"<[^>]+>", " ", str(p.get("content") or ""))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        rows.append(
            {
                "event_time": ts,
                "platform": "truth_social",
                "text": text[:2000],
                "url": p.get("url", ""),
                "tickers_mentioned": "",
                "event_type": "post",
                "query": "",
                "stock_related": is_stock_related(text, "truth_social"),
            }
        )

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["event_id"] = df.apply(lambda r: _event_id(r["text"], str(r["event_time"])), axis=1)
    return df.drop_duplicates(subset=["event_id"]).sort_values("event_time").reset_index(drop=True)


def fetch_truth_social_rss(settings: dict[str, Any] | None = None, max_items: int = 100) -> pd.DataFrame:
    """Supplement archive with latest posts from trumpstruth.org RSS."""
    settings = settings or load_settings()
    social_cfg = settings.get("social", {})
    base = social_cfg.get("truth_rss_url", "https://trumpstruth.org/feed")
    inaug = settings["oge"]["inauguration_date"]
    end = settings["oge"].get("analysis_end_date", "2026-05-30")
    url = f"{base}?start_date={inaug}&end_date={end}"
    feed = feedparser.parse(url)
    rows = []
    for entry in feed.entries[:max_items]:
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        if published:
            dt = datetime(*published[:6], tzinfo=timezone.utc)
        else:
            continue
        text = entry.get("title", "") or entry.get("summary", "")
        rows.append(
            {
                "event_time": pd.Timestamp(dt).tz_convert(settings["timezone"]),
                "platform": "truth_social",
                "text": text,
                "url": entry.get("link", ""),
                "tickers_mentioned": "",
                "event_type": "post",
                "query": "",
                "stock_related": is_stock_related(text, "truth_social"),
            }
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["event_id"] = df.apply(lambda r: _event_id(r["text"], str(r["event_time"])), axis=1)
    return df


def fetch_google_news(query: str, max_items: int = 20) -> list[dict[str, Any]]:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        resp = requests.get(url, timeout=12, headers={"User-Agent": "TrumpFollowingResearch/1.0"})
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception:
        return []
    items = []
    for entry in feed.entries[:max_items]:
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        if published:
            dt = datetime(*published[:6], tzinfo=timezone.utc)
        else:
            dt = datetime.now(timezone.utc)
        text = entry.get("title", "")
        items.append(
            {
                "event_time": dt,
                "platform": "google_news",
                "text": text,
                "url": entry.get("link", ""),
                "tickers_mentioned": "",
                "event_type": "news",
                "query": query,
                "stock_related": is_stock_related(text, "google_news", query),
            }
        )
    return items


def _merge_news_cache(cache_path: Path, new_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing: list[dict[str, Any]] = []
    if cache_path.exists():
        existing = json.loads(cache_path.read_text())
    by_id: dict[str, dict[str, Any]] = {}
    for row in existing + new_rows:
        eid = row.get("event_id") or _event_id(row.get("text", ""), str(row.get("event_time", "")))
        row["event_id"] = eid
        by_id[eid] = row
    return list(by_id.values())


def fetch_ticker_news(trades: pd.DataFrame, settings: dict[str, Any], top_n: int = 25) -> list[dict[str, Any]]:
    """Google News for top tickers by trade count (one query per ticker)."""
    if trades.empty or "ticker" not in trades.columns:
        return []
    top = trades["ticker"].value_counts().head(top_n).index.tolist()
    rows: list[dict[str, Any]] = []
    per_query = int(settings.get("social", {}).get("ticker_news_max_items", 8))
    for ticker in top:
        if not ticker or pd.isna(ticker):
            continue
        try:
            rows.extend(fetch_google_news(f"Trump {ticker} stock", max_items=per_query))
        except Exception:
            continue
    return rows


def fetch_all_events(
    settings: dict[str, Any] | None = None,
    trades: pd.DataFrame | None = None,
    refresh_news: bool = True,
) -> pd.DataFrame:
    settings = settings or load_settings()
    root = project_root()
    cache = root / settings["paths"]["raw_news"] / "google_news_cache.json"
    social_cfg = settings.get("social", {})
    posts_only = bool(social_cfg.get("match_trump_posts_only", True))

    frames: list[pd.DataFrame] = []

    manual = load_manual_social(settings)
    if not manual.empty:
        frames.append(manual)

    truth = fetch_truth_social_archive(settings)
    if not truth.empty:
        frames.append(truth)

    rss = fetch_truth_social_rss(settings)
    if not rss.empty:
        frames.append(rss)

    if not posts_only:
        news_rows: list[dict[str, Any]] = []
        if cache.exists() and not refresh_news:
            news_rows = json.loads(cache.read_text())
        else:
            existing = json.loads(cache.read_text()) if cache.exists() else []
            queries = social_cfg.get("news_rss_queries", [])
            fresh: list[dict[str, Any]] = []
            for query in queries:
                try:
                    fresh.extend(fetch_google_news(query, max_items=int(social_cfg.get("news_max_items", 20))))
                except Exception:
                    continue
            if trades is not None and not trades.empty:
                fresh.extend(fetch_ticker_news(trades, settings, top_n=int(social_cfg.get("ticker_news_top_n", 12))))
            news_rows = _merge_news_cache(cache, existing + fresh)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(news_rows, indent=2, default=str))

        news_df = pd.DataFrame(news_rows) if news_rows else pd.DataFrame()
        if not news_df.empty:
            news_df["event_time"] = pd.to_datetime(news_df["event_time"], utc=True).dt.tz_convert(settings["timezone"])
            if "stock_related" not in news_df.columns:
                news_df["stock_related"] = news_df.apply(
                    lambda r: is_stock_related(str(r.get("text", "")), str(r.get("platform", "")), str(r.get("query", ""))),
                    axis=1,
                )
            frames.append(news_df)

    if not frames:
        return pd.DataFrame()

    events = pd.concat(frames, ignore_index=True)
    events = events.drop_duplicates(subset=["event_id"]).sort_values("event_time").reset_index(drop=True)

    if not events.empty and posts_only:
        events = events[events["platform"].isin({"truth_social", "post", "x_twitter"})].reset_index(drop=True)
    elif not events.empty:
        is_news = events["event_type"].eq("news") | events["platform"].eq("google_news")
        is_truth = events["platform"].eq("truth_social")
        events = events[is_news | (~is_truth) | events["stock_related"].fillna(False)].reset_index(drop=True)

    if trades is not None and not trades.empty:
        lexicon = build_ticker_lexicon(trades)
        events = enrich_events_with_tickers(events, lexicon)

    if not events.empty and not posts_only:
        is_news = events["event_type"].eq("news") | events["platform"].eq("google_news")
        keep = is_news | events["stock_related"].fillna(False) | events["tickers_mentioned"].astype(str).str.len().gt(0)
        events = events[keep].reset_index(drop=True)

    out = root / settings["paths"]["processed"] / "events.parquet"
    events.to_parquet(out, index=False)
    return events
