#!/usr/bin/env python3
"""Generate analysis notebooks."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NB_DIR = ROOT / "notebooks"


def nb(cells: list[dict]) -> dict:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
        "cells": cells,
    }


def md(source: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": source.splitlines(keepends=True)}


def code(source: str) -> dict:
    return {
        "cell_type": "code",
        "metadata": {},
        "source": source.splitlines(keepends=True),
        "outputs": [],
        "execution_count": None,
    }


NOTEBOOKS = {
    "01_download_and_verify.ipynb": [
        md("# 01 — 下载与 Cross-Check\n\nOGE 278-T 股票/ETF 披露（不含债券）。"),
        code("import json\nfrom pathlib import Path\nimport pandas as pd\n\nROOT = Path('..').resolve()\npd.read_csv(ROOT / 'data/processed/manifest.csv')"),
        code("xcheck = json.loads((ROOT / 'reports/cross_check_manifest.json').read_text())\nxcheck"),
        code("pd.DataFrame(json.loads((ROOT / 'reports/web_cross_check.json').read_text()))"),
    ],
    "02_parse_trades.ipynb": [
        md("# 02 — 解析股票交易"),
        code("import pandas as pd\nfrom pathlib import Path\n\nROOT = Path('..').resolve()\ntrades = pd.read_parquet(ROOT / 'data/processed/trades.parquet')\ntrades.head(10)"),
        code("trades.groupby('action').size()"),
        code("trades[trades.asset_name.str.contains('DELL', case=False, na=False)]"),
    ],
    "03_returns_and_alpha.ipynb": [
        md("# 03 — 收益与 Alpha"),
        code("import pandas as pd\nfrom pathlib import Path\n\nROOT = Path('..').resolve()\ndf = pd.read_csv(ROOT / 'reports/trades_analysis.csv', parse_dates=['transaction_date','disclosure_date'])\ndf['reveal_lag_days'].describe()"),
        code("pd.read_parquet(ROOT / 'data/processed/event_study.parquet').groupby('event_window_day')['abnormal_return'].mean()"),
        code("bt = pd.read_parquet(ROOT / 'data/processed/backtest.parquet')\nbt.groupby('disclosure_date')['net_return'].mean().cumsum().plot()"),
    ],
}


def main() -> None:
    NB_DIR.mkdir(exist_ok=True)
    for name, cells in NOTEBOOKS.items():
        path = NB_DIR / name
        path.write_text(json.dumps(nb(cells), indent=1))
        print("Wrote", path)


if __name__ == "__main__":
    main()
