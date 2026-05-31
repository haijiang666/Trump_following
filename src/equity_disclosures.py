"""Discover and download Trump OGE 278-T filings (equity focus)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pdfplumber
import requests

from .disclosures import download_pdf, ensure_dirs, extract_oge_received_date, load_settings, project_root

OGE_INDEX_JSON = "https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index?ReadViewEntries&OutputFormat=JSON&Count=500"

# All known Trump 278-T filings since second-term inauguration (Jan 20, 2025).
# Earlier filings are mostly municipal bonds; equity bulk is May 2026 Q1 report.
TRUMP_278T_FILINGS = [
    {
        "doc_id": "trump_278t_2025_08_19_a",
        "url": "https://www.whitehouse.gov/wp-content/uploads/2025/08/President-Donald-J.-Trump-Periodic-Transaction-Report-8.12.25-1.pdf",
        "report_type": "periodic_278t",
        "filing_date": "2025-08-12",
        "notes": "Aug 2025 PTR part 1 (mostly munis)",
    },
    {
        "doc_id": "trump_278t_2025_08_19_b",
        "url": "https://www.whitehouse.gov/wp-content/uploads/2025/08/President-Donald-J.-Trump-Periodic-Transaction-Report-8.12.25-2.pdf",
        "report_type": "periodic_278t",
        "filing_date": "2025-08-12",
        "notes": "Aug 2025 PTR part 2 (bonds)",
    },
    {
        "doc_id": "trump_278t_2025_11_17",
        "url": "https://www.whitehouse.gov/wp-content/uploads/2025/11/President-Donald-J.-Trump-Periodic-Transaction-Report-11.14.25.pdf",
        "report_type": "periodic_278t",
        "filing_date": "2025-11-14",
        "notes": "Nov 2025 PTR (bonds)",
    },
    {
        "doc_id": "trump_278t_2026_01_14",
        "url": "https://www.whitehouse.gov/wp-content/uploads/2026/01/President-Donald-J.-Trump-Periodic-Transaction-Report-1.14.2026-.pdf",
        "report_type": "periodic_278t",
        "filing_date": "2026-01-14",
        "notes": "Jan 2026 PTR (bonds)",
    },
    {
        "doc_id": "trump_278t_2026_04_23",
        "url": "https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/CD75555856A7D2E485258DE4002DD4A0/$FILE/Donald-J-Trump-4.20.2026-278T.pdf",
        "report_type": "periodic_278t",
        "filing_date": "2026-04-20",
        "notes": "Apr 2026 PTR (bonds)",
    },
    {
        "doc_id": "trump_278t_2026_05_08_bond",
        "url": "https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/405E4EC4E27BE8D185258DF7002DD1C0/$FILE/Trump,%20Donald%20J.-05.08.2026-278T(1).pdf",
        "report_type": "periodic_278t",
        "filing_date": "2026-05-08",
        "notes": "May 2026 PTR part 1 (bonds)",
    },
    {
        "doc_id": "trump_278t_2026_05_08_equity",
        "url": "https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/405E4EC4E27BE8D185258DF7002DD1C0/$FILE/Trump,%20Donald%20J.-05.08.2026-278T(2).pdf",
        "report_type": "periodic_278t_equity",
        "filing_date": "2026-05-08",
        "disclosure_received_date": "2026-05-12",
        "period": "2026-Q1",
        "notes": "May 2026 PTR part 2 — 113pp Q1 equity/ETF bulk (~3642 trades)",
    },
]


def _normalize_received(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if m:
        mo, da, yr = m.groups()
        return f"{yr}-{int(mo):02d}-{int(da):02d}"
    return None


def classify_pdf(pdf_path: Path) -> dict[str, Any]:
    """Heuristic: equity filing vs bond filing based on page count and keywords."""
    with pdfplumber.open(pdf_path) as pdf:
        pages = len(pdf.pages)
        sample = "\n".join((pdf.pages[i].extract_text() or "") for i in range(min(8, pages)))
    upper = sample.upper()
    equity_hits = sum(upper.count(k) for k in ["NVIDIA", "MICROSOFT CORP", "ETF", "CLASS A", "AMAZON", "APPLE INC", "INC COM"])
    bond_hits = sum(upper.count(k) for k in ["REV RFDG", "CNTY", "SCH DIST", " B/E ", "YTM", "MUNI"])
    return {
        "pages": pages,
        "equity_keyword_hits": equity_hits,
        "bond_keyword_hits": bond_hits,
        "likely_equity_filing": pages > 20 and equity_hits > bond_hits,
    }


def probe_url(url: str, timeout: int = 15) -> bool:
    try:
        r = requests.head(url, headers={"User-Agent": "TrumpFollowingResearch/1.0"}, timeout=timeout, allow_redirects=True)
        if r.status_code == 200:
            return True
        r = requests.get(url, headers={"User-Agent": "TrumpFollowingResearch/1.0"}, timeout=timeout, stream=True)
        return r.status_code == 200
    except Exception:
        return False


def discover_trump_filings(settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return catalog of Trump 278-T filings since inauguration."""
    settings = settings or load_settings()
    cfg = settings.get("oge", {}).get("filings", [])
    docs = cfg if cfg else TRUMP_278T_FILINGS
    since = pd.Timestamp(settings.get("oge", {}).get("inauguration_date", "2025-01-20"))
    out = []
    for doc in docs:
        fd = doc.get("filing_date")
        if fd and pd.Timestamp(fd) < since - pd.Timedelta(days=30):
            continue
        out.append(doc)
    return out


def fetch_equity_disclosures(settings: dict[str, Any] | None = None) -> pd.DataFrame:
    """Download all known Trump 278-T PDFs since inauguration."""
    settings = settings or load_settings()
    paths = ensure_dirs(settings)
    person = settings["person"]
    docs = discover_trump_filings(settings)
    rows: list[dict[str, Any]] = []

    for doc in docs:
        doc_id = doc["doc_id"]
        url = doc["url"]
        dest = paths["raw_disclosures"] / f"{doc_id}.pdf"
        try:
            if not dest.exists() or dest.stat().st_size < 1000:
                if not probe_url(url):
                    raise RuntimeError(f"URL not reachable: {url}")
            download_pdf(url, dest)
            status = "ok"
            file_size = dest.stat().st_size
            meta = classify_pdf(dest)
        except Exception as exc:
            status = f"error: {exc}"
            file_size = dest.stat().st_size if dest.exists() else 0
            meta = classify_pdf(dest) if dest.exists() and file_size > 1000 else {}

        rows.append(
            {
                "doc_id": doc_id,
                "url": url,
                "local_path": str(dest),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "person": f"{person['first_name']} {person['last_name']}",
                "report_type": doc.get("report_type", "periodic_278t"),
                "filing_date": doc.get("filing_date"),
                "period": doc.get("period", ""),
                "notes": doc.get("notes", ""),
                "status": status,
                "file_size_bytes": file_size,
                **meta,
            }
        )

    manifest = pd.DataFrame(rows)
    manifest.to_csv(paths["processed"] / "manifest.csv", index=False)
    return manifest


def enrich_equity_manifest(manifest: pd.DataFrame, settings: dict[str, Any] | None = None) -> pd.DataFrame:
    settings = settings or load_settings()
    paths = ensure_dirs(settings)
    out = manifest.copy()
    dates = []
    for _, row in out.iterrows():
        p = Path(row["local_path"])
        doc_id = row.get("doc_id", "")
        cfg_date = None
        for doc in discover_trump_filings(settings):
            if doc.get("doc_id") == doc_id and doc.get("disclosure_received_date"):
                cfg_date = doc["disclosure_received_date"]
                break
        if cfg_date:
            dates.append(str(cfg_date)[:10])
            continue
        if p.exists() and str(row.get("status", "")).startswith("ok"):
            raw = extract_oge_received_date(p)
            iso = _normalize_received(raw)
            if not iso and row.get("filing_date"):
                iso = str(row["filing_date"])[:10]
            dates.append(iso)
        else:
            dates.append(row.get("filing_date"))
    out["disclosure_date"] = dates
    out.to_csv(paths["processed"] / "manifest.csv", index=False)
    return out


def cross_check_manifest(manifest: pd.DataFrame) -> dict[str, Any]:
    """Validate files are official OGE Form 278-T disclosures."""
    oge_urls = sum(1 for u in manifest["url"] if "oge.gov" in str(u))
    checks = {
        "is_oge_url": oge_urls >= 1,
        "n_oge_documents": int(oge_urls),
        "n_total_documents": len(manifest),
        "form_type": "OGE Form 278-T (Periodic Transaction Report)",
        "legal_basis": "5 U.S.C. app. § 13104(l); transactions >$1,000 within 30-45 days",
        "documents": [],
    }
    for _, row in manifest.iterrows():
        p = Path(row["local_path"])
        ok = p.exists() and str(row.get("status", "")).startswith("ok")
        form_ok = False
        if ok:
            with pdfplumber.open(p) as pdf:
                head = pdf.pages[0].extract_text() or ""
            form_ok = "278-T" in head or "278-T" in head.replace("278�", "278-T")
        le = row.get("likely_equity_filing", False)
        likely_equity = bool(le) if pd.notna(le) else False
        checks["documents"].append(
            {
                "doc_id": row["doc_id"],
                "pages": row.get("pages"),
                "filing_date": row.get("filing_date"),
                "disclosure_date": row.get("disclosure_date"),
                "file_exists": ok,
                "form_278t_detected": form_ok,
                "likely_equity": likely_equity and ok,
                "status": row.get("status"),
            }
        )
    ok_docs = [d for d in checks["documents"] if d["file_exists"] and d["form_278t_detected"]]
    checks["all_valid"] = len(ok_docs) > 0
    checks["n_valid_documents"] = len(ok_docs)
    return checks
