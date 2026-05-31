"""Resolve company names from OGE 278-T to stock tickers."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd
import requests

from .disclosures import project_root

SYMBOL_URLS = [
    "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
]

# Explicit ETF / name overrides (checked before fuzzy match)
MANUAL_ETF_MAP = {
    "ISHARES CORE MSCI EMERGING MARKETS": "IEMG",
    "ISHARES CORE MSCI EMERGING": "IEMG",
    "ISHARES MSCI EMERGING": "IEMG",
    "ISHARES CORE S&P 500": "IVV",
    "VANGUARD S&P 500 ETF": "VOO",
    "STATE STREET SPDR S&P 500": "SPY",
}

SUFFIXES = re.compile(
    r"\b(UNSOLICITED|CLASS [ABC]|CL [ABC]|COM NEW|COM USD|COMMON STOCK|"
    r"COM|NEW|INC|CORP|CORPORATION|PLC|LTD|CO|SHS|REIT|LP|HOLDINGS)\b",
    re.I,
)


def _is_ocr_garbage(name: str) -> bool:
    """Skip fuzzy match on heavily corrupted OCR lines."""
    if len(name) < 6:
        return True
    letters = sum(c.isalpha() for c in name)
    if letters / max(len(name), 1) < 0.55:
        return True
    if len(name) > 100:
        return True
    if re.search(r"[^\x00-\x7F]", name):
        return True
    return False


def _normalize_name(name: str) -> str:
    s = name.upper()
    s = SUFFIXES.sub(" ", s)
    s = re.sub(r"[^A-Z0-9& ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


@lru_cache(maxsize=1)
def load_symbol_table() -> pd.DataFrame:
    cache = project_root() / "data" / "manual" / "symbol_directory.csv"
    if cache.exists() and cache.stat().st_size > 1000:
        return pd.read_csv(cache)

    rows: list[dict[str, str]] = []
    for url in SYMBOL_URLS:
        try:
            text = requests.get(url, timeout=30).text
        except Exception:
            continue
        lines = text.strip().splitlines()[1:]
        for line in lines:
            parts = line.split("|")
            if len(parts) < 2:
                continue
            sym, name = parts[0].strip(), parts[1].strip()
            if sym and name and sym != "Symbol":
                rows.append({"ticker": sym, "name": name, "norm": _normalize_name(name)})

    df = pd.DataFrame(rows).drop_duplicates(subset=["ticker"])
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)
    return df


def resolve_ticker(asset_name: str, manual_map: dict[str, str] | None = None) -> str | None:
    manual_map = manual_map or {}
    upper = asset_name.upper()
    if _is_ocr_garbage(asset_name):
        return None

    for k, v in {**MANUAL_ETF_MAP, **manual_map}.items():
        if k in upper:
            return v

    norm = _normalize_name(asset_name)
    if not norm or len(norm.split()) < 1:
        return None

    table = load_symbol_table()
    hit = table[table["norm"] == norm]
    if len(hit) == 1:
        return hit.iloc[0]["ticker"]

    tokens = norm.split()
    if len(tokens) >= 2:
        two = " ".join(tokens[:2])
        cand = table[table["norm"].str.contains(re.escape(two), regex=True, na=False)]
        if len(cand) == 1:
            return cand.iloc[0]["ticker"]

    if len(tokens[0]) >= 4:
        cand = table[table["norm"].str.startswith(tokens[0])]
        if len(cand) == 1:
            return cand.iloc[0]["ticker"]

    return None


def enrich_trades_with_tickers(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for i, row in out[out["ticker"].isna()].iterrows():
        if row.get("asset_class") in ("bond", "other"):
            continue
        t = resolve_ticker(str(row["asset_name"]))
        if t:
            out.at[i, "ticker"] = t
            if str(row.get("asset_class", "")).endswith("_unmapped"):
                out.at[i, "asset_class"] = str(row["asset_class"]).replace("_unmapped", "")
    return out
