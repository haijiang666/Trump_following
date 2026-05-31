"""Equity-only trade parsing from OGE 278-T filings."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pdfplumber

from .disclosures import load_settings, project_root

# Corporate name fragments -> ticker (longest match first)
TICKER_MAP = {
    "DELL TECHNOLOGIES": "DELL",
    "NVIDIA CORP": "NVDA",
    "NVIDIA": "NVDA",
    "MICROSOFT CORP": "MSFT",
    "APPLE INC": "AAPL",
    "AMAZON.COM": "AMZN",
    "AMAZON COM": "AMZN",
    "META PLATFORMS": "META",
    "ALPHABET INC": "GOOGL",
    "GOOGLE": "GOOGL",
    "BERKSHIRE HATHAWAY CLASS B": "BRK-B",
    "BERKSHIRE HATHAWAY CLASS A": "BRK-A",
    "TESLA INC": "TSLA",
    "BOEING CO": "BA",
    "BOEING COMPANY": "BA",
    "BOEING": "BA",
    "JPMORGAN": "JPM",
    "VISA INC": "V",
    "PALANTIR": "PLTR",
    "INTEL CORP": "INTC",
    "ADVANCED MICRO DEVICES": "AMD",
    "BROADCOM": "AVGO",
    "ORACLE CORP": "ORCL",
    "SERVICENOW": "NOW",
    "ADOBE INC": "ADBE",
    "WORKDAY": "WDAY",
    "SYNOPSYS": "SNPS",
    "CADENCE DESIGN": "CDNS",
    "SUPER MICRO COMPUTER": "SMCI",
    "JABIL INC": "JBL",
    "TRUMP MEDIA": "DJT",
    "NETFLIX INC": "NFLX",
    "COSTCO WHSL": "COST",
    "DISNEY WALT": "DIS",
    "EXXON MOBIL": "XOM",
    "CHEVRON CORP": "CVX",
    "COINBASE GLOBAL": "COIN",
    "VANGUARD S&P 500 ETF": "VOO",
    "STATE STREET SPDR S&P 500": "SPY",
    "SPDR S&P 500": "SPY",
    "ISHARES CORE S&P 500": "IVV",
    "ISHARES CORE MSCI EMERGING MARKETS": "IEMG",
    "ISHARES CORE MSCI EMERGING": "IEMG",
    "ISHARES MSCI EMERGING": "IEMG",
    "COMCAST CORP": "CMCSA",
    "PTC INC": "PTC",
    "PTCINC": "PTC",
    "ACCENTURE PLC": "ACN",
    "DATAOOG": "DDOG",
    "DATADOG": "DDOG",
    "AMPHENOL CORP": "APH",
    "ZEBRA TECHNOLOGIES": "ZBRA",
    "MICROCHIP TECHNOLOGY": "MCHP",
    "CHIPOTLE MEXICAN GRILL": "CMG",
    "PAYCHEX INC": "PAYX",
    "DOORDASH": "DASH",
    "LINDE PLC": "LIN",
    "CHURCH & DWIGHT": "CHD",
    "DIGITAL RLTY TR": "DLR",
    "EDWARDS LIFESCIENCES": "EW",
    "APPLIED MATERIALS": "AMAT",
    "STRYKER CORP": "SYK",
    "LENNOX INTL": "LII",
    "CME GROUP": "CME",
    "ILLINOIS TOOL WORKS": "ITW",
    "ROPER TECHNOLOGIES": "ROP",
    "EMCOR GROUP": "EME",
    "LAM RESEARCH": "LRCX",
    "FORTINET": "FTNT",
    "PHILIP MORRIS": "PM",
    "UBER TECHNOLOGIES": "UBER",
    "SALESFORCE": "CRM",
    "HOME DEPOT": "HD",
    "CARVANA": "CVNA",
    "WASTE MGMT": "WM",
    "INTERCONTINENTALEXCHANGE": "ICE",
    "HONEYWELL": "HON",
    "ZOETIS": "ZTS",
    "NVR INC": "NVR",
    "PEPSICO": "PEP",
    "KEURIG DR PEPPER": "KDP",
    "WILLIAMS COS": "WMB",
    "PUBLIC SERVICE ENTERPRISE": "PEG",
    "COPART": "CPRT",
    "DUOLINGO": "DUOL",
    "MONGODB": "MDB",
    "BLACKROCK": "BLK",
    "NORFOLK SOUTHN": "NSC",
    "EQUINIX": "EQIX",
    "GOLDMAN SACHS": "GS",
}

# Exclude bonds, money market, treasury/bond ETFs
BOND_PATTERNS = re.compile(
    r"REV\s+RFDG|RFDG|B/?E\s|PCT\s|CNTY|SCH\s+DIST|MUNI|HLTH\s+FAC|"
    r"NTS\d|REGS\s+DUE|NOTES\s+DUE|SENIOR\s+NOTES|"
    r"YTM\s*=|TO\s+MATURITY|TO\s+PAR\s+CALL|"
    r"DISCRETIONARY\s+ORDER\s+YIELD|FC\d{6}|DTD\d|"
    r"UNSOLICITED\s+ORDER\s+YIELD|CORPORATE\s+BOND|"
    r"TREASURY|T-BILL|T-NOTE|"
    r"\d+\.\d+%\s*MS|\d+\.\d+%\s*JJ|"
    r"AUTH\s+REV|SALES\s+TAX\s+REV|WTR\s+|SWR\s+REV|"
    r"PROM\s+NT|IMPT\s+SER|GOVERNMENT\s+MONEY\s+FUND|"
    r"MONEY\s+FUND|TREASURY\s+BOND|TREASURY\s+BND|"
    r"INTERNATIONAL\s+TRSRY|INTERNATIONAL\s+TREASURY|"
    r"US\s+TREASURY\s+BOND|GOLD\s+TRUST|"
    r"\bPERP\b|\bPFD\b|PREFERRED\s+STOCK|PREFERRED\s+SEC",
    re.I,
)

ETF_PATTERNS = re.compile(r"\bETF\b|ISHARES|VANGUARD|INVESCO|STATE STREET", re.I)

NOTIFY_WORDS = re.compile(r"\b(Yes|No|Yos|Vos|Yea|Yoo|Yoa|Yo■)\b", re.I)

AMOUNT_TAIL = re.compile(
    r"(\$[\d,\.\s]+[-–—•]\s*\$?[\d,\.\s]+|\$[\d,\.\s]+[-–—•][\s\$[\d,\.\s]+|"
    r"\$?\d[\d,\.\s]*[-–—•]\s*\$?[\d,\.\s]+|[Ss]\d[\d,\.\s]*[-–—•]\s*\$?[\d,\.\s]+|"
    r"\$\d[\d,\.\s]*•\s*\$[\d,\.\s]+)",
    re.I,
)

DATE_IN_LINE = re.compile(
    r"(\d{1,2}\s*[/lI]\s*\d{1,2}\s*[/lI]\s*\d{4}|\d{1,2}/\d{4,6}|\d{1,2}/\d{1,2}\d{4}|\d{6,8})"
)

ACTION_TOKEN = re.compile(
    r"\b(purch\w*|ourch\w*|lourch\w*|DUrch\w*|IPUrch\w*|curch\w*|pun:\w*|"
    r"ourcl\w*|lourc\w*|lourt\w*|sale|salo|solo|sold|1110|aalo|ulo|exchange)\b",
    re.I,
)

def _filing_period(settings: dict[str, Any] | None = None) -> tuple[pd.Timestamp, pd.Timestamp]:
    settings = settings or load_settings()
    oge = settings.get("oge", {})
    lo = pd.Timestamp(oge.get("inauguration_date", "2025-01-20"))
    hi = pd.Timestamp(oge.get("analysis_end_date", "2026-05-30"))
    return lo, hi


def normalize_ocr_text(text: str) -> str:
    if not text:
        return ""
    t = text.replace("•", "-").replace("S$", "$")
    t = re.sub(r"(?<=\d)[lI](?=\d)", "/", t)
    t = re.sub(r"(\d)[lI](\d)", r"\1/\2", t)
    return t


def _date_score(ts: pd.Timestamp, settings: dict[str, Any] | None = None) -> int:
    lo, hi = _filing_period(settings)
    if lo <= ts <= hi:
        return 10
    if ts.year in (2025, 2026):
        return 3
    return 0


def _apply_year_fix(ts: pd.Timestamp) -> pd.Timestamp:
    if ts.year in (2027, 2028) and ts.month <= 3:
        ts = ts.replace(year=2026)
    return ts


def generate_date_candidates(raw: str) -> list[str]:
    raw = normalize_ocr_text(raw.strip())
    cands: list[str] = []

    for m in re.finditer(r"(\d{1,2})\s*[/lI]\s*(\d{1,2})\s*[/lI]\s*(\d{4})", raw):
        cands.append(f"{int(m.group(1))}/{int(m.group(2))}/{m.group(3)}")

    s = re.sub(r"\s+", "", raw)
    s = s.replace(".", "/")

    # 3/512026 -> 3/5/2026
    m = re.match(r"^(\d{1,2})/(\d{5,6})$", s)
    if m:
        rest = m.group(2)
        cands.append(f"{int(m.group(1))}/{int(rest[0])}/{rest[-4:]}")
        if len(rest) >= 6:
            cands.append(f"{int(m.group(1))}/{int(rest[:2])}/{rest[-4:]}")

    for pat in [
        r"^(\d{1,2})/(\d{1,2})(\d{4})$",
        r"^(\d{1,2})/(\d)(\d{4})$",
        r"^(\d{2,3})/(\d{4})$",
        r"^(\d{1,2})/(\d{3,4})(\d{4})$",
    ]:
        m = re.match(pat, s)
        if not m:
            continue
        g = m.groups()
        if len(g) == 3:
            cands.append(f"{int(g[0])}/{int(g[1])}/{g[2]}")
        elif len(g) == 2 and len(g[0]) == 3:
            cands.append(f"{int(g[0][0])}/{int(g[0][1:])}/{g[1]}")
        elif len(g) == 2 and len(g[0]) == 2:
            cands.append(f"{int(g[0][0])}/{int(g[0][1])}/{g[1]}")

    digits = re.sub(r"\D", "", raw)
    if len(digits) in (6, 7, 8, 9):
        splits = {
            6: [(1, 1, 4), (1, 2, 3), (2, 2, 2)],
            7: [(1, 2, 4), (2, 2, 3), (1, 1, 5)],
            8: [(2, 2, 4), (1, 2, 5), (1, 1, 6), (2, 1, 5)],
            9: [(2, 2, 5), (1, 2, 6), (1, 1, 7), (2, 1, 6)],
        }
        for a, b, c in splits.get(len(digits), []):
            if a + b + c != len(digits):
                continue
            mm, dd, yy = int(digits[:a]), int(digits[a : a + b]), digits[a + b :]
            if 1 <= mm <= 12 and 1 <= dd <= 31 and len(yy) == 4:
                cands.append(f"{mm}/{dd}/{yy}")
        if len(digits) == 9:
            for i, ch in enumerate(digits):
                if ch not in "912":
                    continue
                sub = digits[:i] + digits[i + 1 :]
                if len(sub) == 8:
                    mm, dd, yy = int(sub[0]), int(sub[1:3]), sub[3:]
                    if 1 <= mm <= 12 and 1 <= dd <= 31:
                        cands.append(f"{mm}/{dd}/{yy}")
        # Drop one spurious OCR digit and retry 7/8-char splits
        if len(digits) in (8, 9):
            for i in range(len(digits)):
                sub = digits[:i] + digits[i + 1 :]
                for a, b, c in [(1, 2, 4), (2, 2, 3), (1, 1, 5)]:
                    if len(sub) != a + b + c:
                        continue
                    mm, dd, yy = int(sub[:a]), int(sub[a : a + b]), sub[a + b :]
                    if 1 <= mm <= 12 and 1 <= dd <= 31 and len(yy) == 4:
                        cands.append(f"{mm}/{dd}/{yy}")

    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def fix_ocr_date(raw: str) -> pd.Timestamp | None:
    """Fix common OCR date errors; prefer dates inside Q1 2026 filing window."""
    best: tuple[int, pd.Timestamp] | None = None
    for cand in generate_date_candidates(raw):
        ts = pd.to_datetime(cand, errors="coerce")
        if pd.isna(ts):
            continue
        if ts.month > 12 or ts.day > 31:
            continue
        ts = _apply_year_fix(ts)
        score = _date_score(ts)
        if best is None or score > best[0]:
            best = (score, ts)
    return best[1] if best and best[0] > 0 else (best[1] if best else None)


def normalize_action(action: str) -> str:
    a = re.sub(r"[^a-z]", "", action.lower())
    if a in ("1110",) or "sale" in a or "salo" in a or "solo" in a or "sold" in a or a in ("aalo", "ulo"):
        return "sale"
    if "cnio" in a or "nioo" in a or a.endswith("nio"):
        return "sale"
    if "exchange" in a:
        return "exchange"
    if "rch" in a or "hase" in a or "hose" in a:
        return "purchase"
    return "purchase"


def infer_action_from_text(text: str) -> str | None:
    clean = re.sub(r"[^a-zA-Z:]", " ", text.lower())
    if re.search(r"\b(sale|salo|solo|sold|1110|aalo|ulo)\b", clean):
        return "sale"
    if re.search(r"(urch|rchase|rchas|rch\w*|hase|hose|pun|cnio)", clean):
        return "purchase"
    if "exchange" in clean:
        return "exchange"
    return None


def _parse_dollar_amount(raw: str) -> float | None:
    """Parse one side of an OGE dollar range (handles OCR thousand dots)."""
    s = re.sub(r"[^\d.]", "", raw.replace(" ", ""))
    if not s:
        return None
    if s.count(".") >= 2:
        s = s.replace(".", "")
    elif s.count(".") == 1:
        left, right = s.split(".")
        if len(right) == 3 and len(left) <= 3:
            s = left + right
    try:
        v = float(s.replace(",", ""))
        return v if v >= 1000 else None  # OGE ranges start at $1,001
    except ValueError:
        return None


def parse_amount_range(text: str) -> tuple[float | None, float | None]:
    cleaned = normalize_ocr_text(text).replace("•", "-")
    cleaned = re.sub(r"^[Ss](?=\d)", "$", cleaned)
    parts = re.split(r"[-–—]", cleaned, maxsplit=1)
    if len(parts) != 2:
        return None, None
    lo = _parse_dollar_amount(parts[0])
    hi = _parse_dollar_amount(parts[1])
    return lo, hi


def strip_action_from_desc(desc: str) -> str:
    desc = re.sub(r"^(YES|NO|UNSOLICITED)\s*", "", desc, flags=re.I).strip()
    desc = ACTION_TOKEN.sub("", desc).strip()
    desc = re.sub(r"\s+", " ", desc)
    return desc


def is_bond_description(desc: str) -> bool:
    return bool(BOND_PATTERNS.search(desc.upper()))


def infer_ticker(desc: str) -> tuple[str | None, str]:
    """Return (ticker, asset_class)."""
    upper = desc.upper()
    if is_bond_description(desc):
        return None, "bond"

    for name, ticker in sorted(TICKER_MAP.items(), key=lambda x: -len(x[0])):
        if name in upper:
            if ETF_PATTERNS.search(upper) or "ETF" in name:
                return ticker, "etf"
            if "REIT" in upper:
                return ticker, "reit"
            return ticker, "equity"

    if ETF_PATTERNS.search(upper) or " ETF" in upper:
        return None, "etf_unmapped"

    if re.search(r"\b(INC|CORP|PLC|CLASS [ABC]|COM USD|COMMON STOCK|COM NEW)\b", upper):
        if "REIT" in upper:
            return None, "reit_unmapped"
        return None, "equity_unmapped"

    return None, "other"


def _find_amount_in_row(row: list[Any]) -> str:
    for cell in reversed(row):
        if cell and re.search(r"[\d$]", str(cell)):
            text = normalize_ocr_text(str(cell))
            if re.search(r"\d{3}", text) and ("$" in text or re.search(r"\d,\d", text) or "•" in text or "-" in text):
                return text
    return ""


def parse_table_row(row: list[Any]) -> dict[str, Any] | None:
    """Parse a 278-T table row from pdfplumber extract_tables()."""
    if not row or len(row) < 4:
        return None
    c0 = str(row[0] or "").strip()
    if not re.fullmatch(r"\d{1,5}", c0):
        return None

    txn_num = int(c0)
    desc_cell = normalize_ocr_text(str(row[1] or "")).strip()
    action_cell = normalize_ocr_text(str(row[2] or "")).strip() if len(row) > 2 and row[2] else ""
    date_cell = normalize_ocr_text(str(row[3] or "")).strip() if len(row) > 3 else ""
    amount_cell = _find_amount_in_row(row)

    if not date_cell:
        return None

    txn_date = fix_ocr_date(date_cell)
    if txn_date is None or pd.isna(txn_date):
        return None

    if action_cell:
        action = normalize_action(action_cell)
        desc = strip_action_from_desc(desc_cell)
    else:
        combined = desc_cell
        action = infer_action_from_text(combined)
        if not action:
            return None
        desc = strip_action_from_desc(combined)

    if len(desc) < 2:
        return None

    amount_min, amount_max = parse_amount_range(amount_cell) if amount_cell else (None, None)
    return {
        "txn_num": txn_num,
        "asset_name": desc,
        "action": normalize_action(action),
        "transaction_date": txn_date,
        "amount_min": amount_min,
        "amount_max": amount_max,
    }


def parse_line_transaction(line: str) -> dict[str, Any] | None:
    """Fallback: parse a single 278-T transaction line (OCR-tolerant)."""
    line = normalize_ocr_text(line.strip())
    m_num = re.match(r"^(\d{1,5})\s+(.+)$", line)
    if not m_num:
        return None

    txn_num = int(m_num.group(1))
    rest = m_num.group(2)

    amt_m = AMOUNT_TAIL.search(rest)
    if not amt_m:
        return None
    amount_str = amt_m.group(1)
    rest = rest[: amt_m.start()].strip()

    notify_m = NOTIFY_WORDS.search(rest)
    if notify_m:
        rest = rest[: notify_m.start()].strip()

    norm_rest = re.sub(r"\s*\.\s*", ".", rest)
    norm_rest = re.sub(r"(\d)\s+(\d)", r"\1\2", norm_rest)
    dates = DATE_IN_LINE.findall(norm_rest)
    if not dates:
        return None
    txn_date = fix_ocr_date(dates[-1])
    if txn_date is None or pd.isna(txn_date):
        return None
    date_pos = norm_rest.rfind(dates[-1])
    before_date = rest[:date_pos] if date_pos >= 0 else rest

    action = infer_action_from_text(before_date)
    if not action:
        return None

    desc = strip_action_from_desc(before_date)
    if len(desc) < 2:
        return None

    amount_min, amount_max = parse_amount_range(amount_str)
    return {
        "txn_num": txn_num,
        "asset_name": desc,
        "action": normalize_action(action),
        "transaction_date": txn_date,
        "amount_min": amount_min,
        "amount_max": amount_max,
    }


def extract_raw_transactions(pdf_path: Path) -> list[dict[str, Any]]:
    """Extract all transactions via table parsing + line fallback."""
    parsed: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def add(parsed_row: dict[str, Any]) -> None:
        key = (
            f"{parsed_row['txn_num']}|{parsed_row['transaction_date'].date()}|"
            f"{parsed_row['action']}|{parsed_row.get('amount_min')}|"
            f"{parsed_row['asset_name'][:30].upper()}"
        )
        if key in seen_keys:
            return
        seen_keys.add(key)
        parsed.append(parsed_row)

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for row in table:
                    row_parsed = parse_table_row(row)
                    if row_parsed:
                        add(row_parsed)

        table_keys = set(seen_keys)
        for page in pdf.pages:
            for line in (page.extract_text() or "").splitlines():
                line_parsed = parse_line_transaction(line)
                if not line_parsed:
                    continue
                key = (
                    f"{line_parsed['txn_num']}|{line_parsed['transaction_date'].date()}|"
                    f"{line_parsed['action']}|{line_parsed.get('amount_min')}|"
                    f"{line_parsed['asset_name'][:30].upper()}"
                )
                if key in table_keys:
                    continue
                add(line_parsed)

    return parsed


def parse_equity_278t_pdf(
    pdf_path: Path,
    doc_id: str,
    disclosure_date: str | None,
    person: str,
) -> list[dict[str, Any]]:
    raw = extract_raw_transactions(pdf_path)
    trades: list[dict[str, Any]] = []

    for parsed in raw:
        txn_num = parsed["txn_num"]
        desc = parsed["asset_name"]
        ticker, asset_class = infer_ticker(desc)
        if asset_class.startswith("bond"):
            continue

        trade_key = (
            f"{person}|{doc_id}|{txn_num}|{desc[:50]}|{parsed['action']}|{parsed['transaction_date'].date()}"
        )
        trades.append(
            {
                "trade_id": hashlib.md5(trade_key.encode()).hexdigest()[:12],
                "person": person,
                "doc_id": doc_id,
                "txn_num": txn_num,
                "asset_name": desc,
                "action": parsed["action"],
                "transaction_date": parsed["transaction_date"],
                "disclosure_date": pd.to_datetime(disclosure_date, errors="coerce") if disclosure_date else pd.NaT,
                "amount_min": parsed["amount_min"],
                "amount_max": parsed["amount_max"],
                "asset_class": asset_class.replace("_unmapped", ""),
                "ticker": ticker,
                "source_doc": pdf_path.name,
            }
        )

    return trades


def parse_all_equity_filings(manifest: pd.DataFrame, settings: dict[str, Any] | None = None) -> pd.DataFrame:
    settings = settings or load_settings()
    person = f"{settings['person']['first_name']} {settings['person']['last_name']}"
    all_trades: list[dict[str, Any]] = []
    lo, hi = _filing_period(settings)

    ptr = manifest[manifest["report_type"].str.contains("278", na=False)]
    ptr = ptr.drop_duplicates(subset=["doc_id"])
    for _, row in ptr.iterrows():
        pdf_path = Path(row["local_path"])
        if not pdf_path.exists() or not str(row.get("status", "")).startswith("ok"):
            continue
        disc_date = row.get("disclosure_date") or row.get("filing_date")
        trades = parse_equity_278t_pdf(
            pdf_path,
            doc_id=row["doc_id"],
            disclosure_date=str(disc_date)[:10] if pd.notna(disc_date) and disc_date else None,
            person=person,
        )
        all_trades.extend(trades)

    if not all_trades:
        return pd.DataFrame()

    df = pd.DataFrame(all_trades)
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df["disclosure_date"] = pd.to_datetime(df["disclosure_date"])

    def fix_txn_date(ts: pd.Timestamp) -> pd.Timestamp:
        if pd.isna(ts):
            return ts
        if ts.year in (2027, 2028) and ts.month <= 3:
            ts = ts.replace(year=2026)
        if ts.year == 2024 and ts.month >= 6:
            ts = ts.replace(year=2025)
        return ts

    df["transaction_date"] = df["transaction_date"].apply(fix_txn_date)
    df = df[(df["transaction_date"] >= lo) & (df["transaction_date"] <= hi)].reset_index(drop=True)

    dedupe_cols = ["person", "ticker", "action", "transaction_date", "amount_min", "amount_max", "asset_name"]
    df = df.sort_values("disclosure_date").drop_duplicates(
        subset=[c for c in dedupe_cols if c in df.columns], keep="first"
    )
    return df.reset_index(drop=True)


def parse_stats_all(manifest: pd.DataFrame, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Aggregate parse coverage across all filings."""
    settings = settings or load_settings()
    per_doc = []
    total_table = 0
    total_equity = 0
    ok_manifest = manifest[manifest["status"].astype(str).str.startswith("ok")]
    trades = parse_all_equity_filings(ok_manifest, settings)
    for _, row in ok_manifest.iterrows():
        p = Path(row["local_path"])
        if not p.exists():
            continue
        st = parse_stats(p, settings, manifest_row=row.to_dict())
        per_doc.append({"doc_id": row["doc_id"], **st})
        total_table += st["table_rows_in_pdf"]
        total_equity += st["equity_rows_in_doc"]
    return {
        "n_filings": len(per_doc),
        "per_document": per_doc,
        "table_rows_in_pdf": total_table,
        "equity_rows_after_filter": len(trades),
        "parse_rate_vs_table": len(trades) / total_table if total_table else 0,
    }


def parse_stats(
    pdf_path: Path,
    settings: dict[str, Any] | None = None,
    manifest_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return parse coverage stats for a single PDF."""
    settings = settings or load_settings()
    manifest_row = manifest_row or {
        "doc_id": pdf_path.stem,
        "local_path": str(pdf_path),
        "status": "ok",
        "report_type": "periodic_278t",
        "disclosure_date": settings.get("oge", {}).get("disclosure_received_date"),
    }
    raw = extract_raw_transactions(pdf_path)
    lo, hi = _filing_period(settings)
    in_period = sum(1 for p in raw if lo <= p["transaction_date"] <= hi)
    single = parse_all_equity_filings(pd.DataFrame([manifest_row]), settings)
    doc_equity = single[single["doc_id"] == manifest_row["doc_id"]] if "doc_id" in single.columns else single
    with pdfplumber.open(pdf_path) as pdf:
        table_rows = sum(
            1
            for page in pdf.pages
            for table in (page.extract_tables() or [])
            for row in table
            if row and row[0] and re.fullmatch(r"\d{1,5}", str(row[0]).strip())
        )
    equity_n = len(doc_equity)
    return {
        "table_rows_in_pdf": table_rows,
        "parsed_raw_rows": len(raw),
        "equity_rows_in_doc": equity_n,
        "in_filing_period": in_period,
        "parse_rate_vs_table": equity_n / table_rows if table_rows else 0,
    }


def save_equity_trades(df: pd.DataFrame, settings: dict[str, Any] | None = None) -> Path:
    settings = settings or load_settings()
    out = project_root() / settings["paths"]["processed"] / "trades.parquet"
    df.to_parquet(out, index=False)
    return out


def filter_trades_with_ticker(df: pd.DataFrame, asset_classes: list[str] | None = None) -> pd.DataFrame:
    """Filter rows with ticker; optional asset_class whitelist (e.g. equity, etf)."""
    out = df[df["ticker"].notna()].copy()
    if asset_classes and "asset_class" in out.columns:
        out = out[out["asset_class"].isin(asset_classes)]
    return out
