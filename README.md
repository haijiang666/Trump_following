# Trump Following — Stock Trade Alpha Research

分析 Donald Trump **OGE Form 278-T 股票/ETF 交易**（entry、exit、reveal lag、收益），并结合 Truth Social / 新闻检验 reveal alpha 与社交信号。

**范围**：Donald Trump 第二任期（2025-01-20 起）全部 OGE Form 278-T 中的**股票/ETF**交易；债券已过滤。

## 快速开始

```bash
cd Trump_following
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/validate_parse.py    # 验证 PDF 解析率 ≥90%
python scripts/run_pipeline.py      # 完整分析 pipeline
python scripts/generate_report.py     # 生成 reports/FINAL_REPORT.md
jupyter lab notebooks/              # 分步 notebook 01→03
```

## 项目结构

```
Trump_following/
├── Trump_analysis_PLAN.md      # 研究计划
├── config/settings.yaml        # 配置（OGE URL、回测参数等）
├── data/
│   ├── raw/disclosures/        # OGE PDF（仅 equity 278-T）
│   ├── raw/social/             # Truth Social 手动/RSS
│   └── processed/              # trades.parquet, returns, prices/
├── notebooks/
│   ├── 01_download_and_verify.ipynb
│   ├── 02_parse_trades.ipynb
│   └── 03_returns_and_alpha.ipynb
├── reports/
│   ├── FINAL_REPORT.md
│   ├── trades_analysis.csv     # 逐笔 entry/exit/收益
│   └── final_summary.json
├── scripts/
│   ├── run_pipeline.py
│   ├── generate_report.py
│   ├── validate_parse.py
│   └── generate_notebooks.py
└── src/
    ├── equity_trades.py        # PDF 表格解析（≥90% 覆盖率）
    ├── equity_disclosures.py   # OGE 下载与 cross-check
    ├── ticker_resolver.py      # 公司名 → ticker
    ├── prices.py               # yfinance 价格
    ├── events.py               # Truth Social + 新闻
    └── backtest.py             # 收益、event study、回测
```

## 数据来源

- [OGE Form 278-T](https://extapps2.oge.gov/) — 总统 periodic transaction report
- yfinance — 股票价格
- Google News RSS — 新闻事件

## Disclaimer

Research use only. Not investment advice.
