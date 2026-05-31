# Trump_following — 完整审计观察日志

> **项目路径**：`/Users/haijiang/Desktop/Trump_following/`  
> **项目目的**：解析 Trump OGE Form 278-T 中的 **股票/ETF 交易**，计算披露滞后与 post-disclosure 收益，检验「跟单 / reveal alpha」，并（计划中）结合 Truth Social 与新闻。  
> **研究范围（应坚持）**：**普通股 + ETF**（可选含 REIT）；**不含**市政债、国债、公司债。  
> **最后更新**：2026-05-30（合并初审计 → 修复验收 → 股票专项，共四轮）

本文件汇总各轮审查中的**全部观察**（含已修复、仍开放、历史错误 run），供后续 agent 或人工接续。

---

## 状态图例

| 标记 | 含义 |
|------|------|
| ✅ **已解决** | 当前代码 + 最新 `reports/` / `data/processed/` 一致 |
| 🟡 **部分解决** | 有改进；局限、文档或口径问题仍在 |
| ❌ **未解决** | 仍缺实现或仍错误 |
| 📜 **历史** | 仅适用于修复前旧产物；勿再引用其数字 |

---

## 一、执行摘要（当前最新 run）

### 1.1 可信结论（股票/ETF 可交易样本）

| 维度 | 结论 |
|------|------|
| 债券混入 `trades_analysis` | **未发现**（0 条 bond；0 条债名+ticker） |
| 回测方法论 | **等权按披露日组合**；`portfolio_return_equal_weight` **+2.31%** |
| 勿用指标 | 对 2310 笔逐笔 `(1+r).cumprod()` ≈ **-99.97%**（📜 旧报告 -58.6% 同理错误） |
| 事件研究 | **2,628** 行 = **657** 个 `(ticker, disclosure_date)` × 4 窗口（已去重） |
| 新闻误链 | **0**（旧 run 曾 1043 条、1 个 event_id） |
| 样本校验 | DELL/NVDA/MSFT/AMZN/META（3/18 sale）**全部匹配** |

### 1.2 应使用的股票口径（勿照搬报告「3,903 笔」）

| 口径 | 笔数 | 说明 |
|------|------|------|
| `trades_raw` 总行数 | **3,903** | 含 **983** 行非股 `other`（债，无 ticker） |
| **可交易 股+ETF+REIT**（pipeline 默认） | **2,314** | `trades_analysis.csv` |
| **可交易 股+ETF**（不含 REIT） | **2,260** | |
| **推荐主样本**：bulk equity PDF + 2026 Q1 + 股/ETF | **2,194** | 634 tickers；见附录代码 |
| 来自 `trump_278t_2026_05_08_equity` | **2,310** / 2,314 | **99.8%** |
| 其他 filing 零星股票 | **4** | Aug 2025 + Jan 2026 |

### 1.3 仍开放（影响「完整研究」而非债污染）

- Truth Social：**0** 条有效帖 → 社交 alpha **未做**（❌）
- 无 ticker：**1,589 / 3,903** raw（40.7%）；股票类无 ticker 约 **606** 行（❌/🟡）
- Placebo、按披露日 `by_ticker` 聚合、10 步 notebook：**未实现**（❌）
- 报告文案：仍混用 3,903、5/12 vs 5/8、图片路径（🟡）

---

## 二、审计时间线

| 轮次 | 日期 | 内容 | 关键发现 |
|------|------|------|----------|
| **1** | 2026-05-30 | 初读代码 + 旧 outputs | 串联复利 -58.6%；事件研究 4168 行；新闻全链；1182 笔；ISTB；Truth 占位 |
| **2** | 2026-05-30 | 对照「已修复」源码 | **代码已改、产物未重跑**；`final_summary` 仍为 `total_return` |
| **3** | 2026-05-30 | 用户重跑 pipeline 后 | 2314 可交易；+2.31%；92.8% 解析率；8 图 + PDF |
| **4** | 2026-05-30 | **仅股票/ETF** | 债未进 analysis；主样本 2194；S1–S8 |

---

## 三、项目结构与数据流（观察）

```
OGE / White House 278-T PDFs
    → equity_disclosures.fetch_equity_disclosures()   # 7 份 catalog，6 份 ok
    → equity_trades.parse_all_equity_filings()        # BOND_PATTERNS 过滤；infer_ticker
    → ticker_resolver.enrich_trades_with_tickers()
    → trades_raw.csv / trades.parquet
    → filter_trades_with_ticker()                     # 仅「有 ticker」，不筛 asset_class
    → prices (yfinance) → compute_returns()         # 交易日 offset
    → align_events_to_trades() / event_study() / backtest_follow_strategy()
    → reports/ + data/processed/
```

**主要入口**：`scripts/run_pipeline.py`（STEP 1–11，含 `generate_report.py`、图表）。

**配置**：`config/settings.yaml`（`inauguration_date`、`disclosure_received_date`、`follow_delay_days` 等）。

---

## 四、股票/ETF 专项（第四轮，全部观察）

### 4.1 债券过滤是否有效

| 检查 | 结果 |
|------|------|
| `trades_analysis` 中 `asset_class == bond` | **0** |
| 名称匹配 `TREASURY|MUNI|REV RFDG|…` 且 **有 ticker** | **0** |
| 名称含 `PERP|NOTE DUE|…` 且在 analysis | **0** |
| `trades_raw` 中 `TREASURY` 提及 | **1** 行，**无 ticker** |

`asset_class=other`（**983** 行）样本：州债、市政、HWY REV 等 — **全部无 ticker**，未进入收益表。

`BOND_PATTERNS` 位于 `src/equity_trades.py`；`parse_equity_278t_pdf` 对 `bond` 类 `continue` 跳过。

### 4.2 `trades_raw` 资产分类（最新）

| asset_class | 行数 | 进入 analysis |
|-------------|------|----------------|
| equity | 2,771 | 2,225（有 ticker） |
| other | 983 | 0 |
| reit | 75 | 54 |
| etf | 74 | 35 |

### 4.3 按来源文件（可交易笔数）

| doc_id | analysis 笔数 | 备注 |
|--------|---------------|------|
| `trump_278t_2026_05_08_equity` | **2,310** | 113 页 Q1 bulk |
| `trump_278t_2025_08_19_a` | 3 | 2025 零星 |
| `trump_278t_2026_01_14` | 1 | 2025-12 一笔 |

`trump_278t_2026_04_23`：raw **21** 行（国债/优先债等），**0** ticker，未进 analysis。

### 4.4 解析率（股票 bulk）

| 指标 | 数值 |
|------|------|
| 表行 `table_rows_in_pdf` | 3,557 |
| 单文档 `equity_rows_in_doc` | 3,723 |
| 单文档 `parse_rate_vs_table` | **104.7%**（raw 提取重复，去重后以入库为准） |
| **聚合** `parse_rate_vs_table` | **92.8%**（跨 6 份 filing） |
| 聚合分子 `equity_rows_after_filter` | 3,903（含 other） |

`validate_parse.py` 目标 ≥90%：聚合口径下**应 PASS**。

### 4.5 股票子集收益（观察）

| 子集 | 笔数 | 备注 |
|------|------|------|
| 全表 analysis | 2,314 | post_1d 均值约 **-0.15%** |
| equity + etf only | 2,260 | 组合收益仍约 **+2.31%** |
| ETF only（35 笔） | 35 | 按披露日组合约 **-0.65%** |
| REIT（54 笔） | 54 | 约 **-0.07%** |
| Q1 bulk + equity/etf | 2,194 | median reveal lag **67** 天 |

### 4.6 股票专项问题表（S1–S8）

| ID | 严重度 | 观察 | 状态 |
|----|--------|------|------|
| **S1** | P1 | Pipeline 对 **所有** `report_type` 含 `278` 的 PDF 解析（含纯债 filing），`trades_raw` 显示 **3,903** 易误读为「3903 笔股票」 | 🟡 |
| **S2** | P1 | **606** 行 `equity/etf/reit` 无 ticker（约股票类 raw 的 **21%**） | ❌ |
| **S3** | P2 | **54** REIT 计入默认可交易集；严格「股+ETF」应 `isin(['equity','etf'])` | 🟡 |
| **S4** | P2 | **8** 条长 OCR 名在 analysis；**2** 条可疑 ticker（XOM、AVGO 乱码句） | 🟡 |
| **S5** | P2 | `PERP` 等在 raw 偶发标 `equity`（4 条 PERP），均无 ticker；`BOND_PATTERNS` 未含 PERP/PFD | 🟡 |
| **S6** | P3 | `FINAL_REPORT.md` 说明段仍写「2026-05-12」「3903 笔」 | 🟡 |
| **S7** | — | 主分析样本建议：**`trump_278t_2026_05_08_equity` + Q1 2026 + equity/etf** | 建议 |
| **S8** | — | 社交/新闻与个股 alpha 仍无效 | ❌ |

---

## 五、P0 — 结论可信度（逐项观察）

### P0-01 回测串联复利 — ✅ 已解决（📜 旧 run 错误）

**📜 历史（第一轮）**

- `backtest_follow_strategy` 对每笔 `net_return` 做 `cumprod`。
- `final_summary`：`total_return: -58.6%`；单笔均值约 **-0.05%** — 严重不一致。
- 含义：把上千笔独立交易当成连续满仓复利。

**现况（`src/backtest.py`）**

- 按 `disclosure_date` 等权日均；`portfolio_return_equal_weight`: **+2.31%**。
- `mean_return_per_trade`: **-0.30%**；`win_rate`: **44.0%**；`n_disclosure_days`: **3**。
- `bt` 列仍含 per-row `cum_return`（映射披露日组合），**勿**对 `net_return` 直接 `cumprod`。

---

### P0-02 事件研究按笔重复 — ✅ 已解决（📜 旧 4168 行）

**📜 历史**

- 每笔 trade 一条 event study → 约 **1040×4** 行；`ticker+event_date` 仅 **508** 组。

**现况**

- `event_study_universe()`：`drop_duplicates(['ticker','disclosure_date'])`。
- **2,628** = **657** 事件 × 4 窗口（0/1/5/20）。
- 仅 **1** ticker 跨 **2** 个披露日（绝大多数只在 2026-05-08 bulk 披露）。

**🟡 残留**

- `_market_model_ar` 仍用 **日历日** `event_date + Timedelta(days=w)`（见 P2-13），与 post-return 的**交易日**不一致。

---

### P0-03 新闻一条链全部交易 — ✅ 已解决（📜 旧 1043 链）

**📜 历史**

- `news_near_disclosure` **1043** 条，**1** 个 `event_id`。
- RSS 在 pipeline 运行日抓取；披露日附近头条（含 UFO 等）与每笔 trade 建链。
- `RELEVANT_NEWS` 不存在。

**现况**

- `RELEVANT_NEWS` 正则 + 需 ticker 命中；`seen_news` 去重。
- `news_near_disclosure`: **0**；`trade_event_links` 仅 `disclosure_event: 2314`。
- `data/raw/news/google_news_cache.json` 存在（可复现）。

---

### P0-04 Truth Social — ❌ 未解决

| 观察 | 详情 |
|------|------|
| `truth_social_manual.json` | `placeholder: true`，`/sample/` URL |
| `load_manual_social()` | 跳过 placeholder |
| `events.parquet` | **41** 条，**全部** `news` |
| `social_near_trade` | **0** |
| 交易窗 | 2025-01 ~ 2026-03；占位帖在 **2025** — 即使不跳过也无法对齐 Q1 2026 |
| README/PLAN | 仍写「结合 Truth Social」— **易误导** |

---

### P0-05 PDF 解析覆盖率 — 🟡 聚合 ✅；单文档仍异常

**📜 历史（第一轮）**

- 仅 **1,182** 行（≈32% of ~3642）；`FINAL_REPORT` 解析率 **0%**（字段缺失）。
- 全 PDF `parse_stats`：`parsed_rows` **3827** > 表行 **3557** → 率 **>100%**。
- `validate_parse` 针对表行 ≥90% 与「入库行数」脱节。

**现况**

- `parse_stats_all()`：用 `equity_rows_after_filter / table_rows` → **92.8%**。
- 总行 **3,903**（含 983 `other`）；可交易 **2,314**。
- 主 bulk：`equity_rows_in_doc` **3723**，`parse_rate` **104.7%**（raw 重复）。
- `per_document`：Aug 2025 等 bond-heavy PDF 也有少量 `equity_rows_in_doc`（108、50…），拉高分母/噪声。

---

## 六、P1 — 数据 / 时间 / 收益

### P1-01 披露日 — 🟡 多披露日已生效；收件日仍混

| 观察 | 详情 |
|------|------|
| 交易侧 `disclosure_date` | **2025-08-19**（3）、**2026-01-14**（1）、**2026-05-08**（2310） |
| `summary.disclosure_dates` | 另含 **2026-04-23**（该次无 ticker 进 analysis） |
| `enrich_equity_manifest` | PDF `OGE RECEIVED` 或 **filing_date** 回退 |
| equity bulk manifest | **2026-05-08**（非 config `disclosure_received_date: 2026-05-12`） |
| 📜 历史 | 全体硬编码 `2026-05-12`；manifest `disclosure_date: null` |
| 影响 | reveal lag、post-return、事件日差 **4 天** 即可能改变结论 |
| 报告 | `FINAL_REPORT` 说明仍写「5/12」 |

---

### P1-02 post_20d 空 — ✅ 已解决（📜 曾 100% 空）

- 📜 历史：披露 2026-05-12，run 日 2026-05-30，不足 20 日历日；window 20 AR 全 NaN。
- 现况：`return_post_disclosure_20d` 空值 **0.17%**（4 笔）；event window 20 有 **657** 条 AR。

---

### P1-03 日历日 vs 交易日 — ✅ 已解决（📜 `follow_delay_days` 未用）

- `price_on_trading_day_offset`；`follow_delay_days: 1` 用于 post 1d/5d/20d。
- `price_on_date` 仍用于 entry / disclosure 当日价。

---

### P1-04 `holding_days` 误标 — ✅ 已解决

- 📜 历史：`holding_days` == `reveal_lag_days`（100%）；PLAN 定义为 exit−entry。
- 现况：`compute_returns` **不输出** `holding_days`；新 CSV 无此列。

---

### P1-05 ISTB / IEMG — ✅ 已解决

- 📜 历史：`ISHARES CORE MSCI EMERGING ETF` → **ISTB**（债券 ETF）。
- 现况：`TICKER_MAP` / `MANUAL_ETF_MAP` → **IEMG**（两条 EMERGING 均为 IEMG）。

---

### P1-06 OCR 脏行 / 误匹配 — 🟡

| 观察 | 详情 |
|------|------|
| `ticker_resolver._is_ocr_garbage()` | 长文本、非 ASCII、字母比例低 → 不 fuzzy |
| `trades_raw` 名长 >80 | **101** 条 |
| 仍带 ticker 的乱码 | **2**（XOM、AVGO）；analysis 中长名 **8** 条 |
| 📜 历史 | txn #27 `Kum Sushi…` → **AAON**；6+ 条 garbage |

---

### P1-07 金额解析 — ✅ 已解决

- 📜 历史：**86~170** 笔 `amount_min < 1000`（如 VOO `0.001`）。
- 现况：`_parse_dollar_amount` 要求 ≥ **1000**；当前 **0** 笔违规。

---

### P1-08 Web 交叉验证 META — ✅ 已解决

- 📜 历史：期望 META sale **2026-02-10** → 未匹配（实际 sale **2026-03-18**）。
- 现况：`web_cross_check_samples` 期望 **2026-03-18**；**✅** 匹配。

---

### P1-09 时区 — ✅ 已解决

- 📜 历史：`tz-aware` vs `naive` 比较报错。
- 现况：`_naive_ts()` 统一 US/Eastern naive。

---

## 七、P2 — 实现 / 报告 / 工程

| ID | 观察 | 状态 |
|----|------|------|
| **P2-01** | Notebook 03 曾用 `strategy_return` → KeyError | ✅ 改为 `groupby('disclosure_date')['net_return'].mean().cumsum()` |
| **P2-02** | `run_pipeline` 未调 `generate_report` | ✅ STEP 11 `subprocess` 调用 |
| **P2-03** | `final_summary` 字段名不一致（`total_equity_rows_parsed` vs `total_rows_parsed`） | ✅ 新 run 用 `total_rows_parsed`、`disclosure_dates` |
| **P2-04** | `summarize_event_study` 的 `car` 实为 AR 求和 | ✅  renamed `sum_ar` |
| **P2-05** | `placebo_event_study()` 存在但未接入 pipeline/报告 | ❌ |
| **P2-06** | `by_ticker` 对同 ticker 多笔简单 `mean`，未按披露日去重 | ❌ |
| **P2-07** | Google News 每次 run 不同 | 🟡 已有 `google_news_cache.json` |
| **P2-08** | `.gitignore` 忽略 `data/raw/**/*.pdf` | ❌ 刻意；克隆需重新下载 |
| **P2-09** | PLAN 10 个 notebook；仅 3 个生成壳 | ❌ |
| **P2-10** | `OGE_INDEX_JSON` 未使用；用 `TRUMP_278T_FILINGS` 硬编码 | ❌ |
| **P2-11** | 无图表/PDF | ✅ **8** PNG + `FINAL_REPORT.pdf` |
| **P2-12** | Sharpe **-0.88**：per-trade `std` + 仅 **3** 个披露日 | 🟡 不宜作严肃风险指标 |
| **P2-13** | 事件研究 AR 用日历日窗口 | 🟡 与 post-return 交易日逻辑不一致 |

---

## 八、P3 — 文档与表述

| ID | 观察 | 状态 |
|----|------|------|
| **P3-01** | README 写解析率 ≥90% | ✅ 与聚合 92.8% 一致 |
| **P3-02** | 📜 报告写 0% 解析率却有 1182 笔 | ✅ 现 92.8% |
| **P3-03** | Truth Social 假 `/sample/` URL | ✅ placeholder 过滤 |
| **P3-04** | person 命名不一 | ✅ settings `Donald J Trump` |

---

## 九、其他观察（N1–N11 与杂项）

### N1 代码修复后产物未重跑 — ✅ 已消除（第二轮→第三轮）

- 第二轮：`portfolio_return_equal_weight` 不在 JSON；`event_study` 仍 4168；META 2/10。
- 第三轮：用户重跑后一致。

### N6 无 ticker 比例高

- raw **40.7%** 无 ticker；股票类无 ticker **606** — 制约覆盖率。

### N7 研究时间窗扩大

- 配置 `_filing_period`：**2025-01-20** ~ **2026-05-30**（非仅 Q1）。
- analysis：2026 Q1 约 **2298** 笔；2025 年仅 **16** 笔。
- 与早期 README「2026 Q1 only」表述不完全一致。

### N8 跨 filing 去重无 `doc_id`

- `dedupe_cols` 无 `doc_id`；`keep='first'` 按 `disclosure_date` 排序。
- 实测多 doc 重复：**0** — 风险仍在。

### N9 报告图片路径

- Markdown：`reports/figures/xxx.png`；文件位于 `reports/FINAL_REPORT.md` 旁 — 应用 `figures/xxx.png`。

### N10 债券 PDF 下载失败

- `trump_278t_2026_05_08_bond`：`URL not reachable`。
- manifest `status=error`；报告表「含股票」列对 bond 行易误读。

### N11 报告表 vs `likely_equity`

- 多份 filing `likely_equity: false` 仍被 parse（少量 equity 行）。
- Apr 2026：21 raw 行，多为 `other`（国债/优先债），**0** ticker。

### 杂项观察

|  topic | 观察 |
|--------|------|
| **GOLD** | raw 中 **12** 条含 GOLD；`GOLD TRUST` 在 `BOND_PATTERNS`；多为 equity/etf 类 gold 名 |
| **WFC / 优先债** | Apr filing 中 Wells Fargo **PERP** 等标 `other` 或 `equity`，均无 ticker |
| **yfinance NOW** | 2026-02 ServiceNow 价约 **$106**（yfinance 当前序列）；若与公众认知不符需另核数据源 |
| **`filter_trades_with_ticker`** | 只筛 ticker，**不**筛 `asset_class` — REIT 默认进入 |
| **White House PDF** | Aug 2025 等来自 whitehouse.gov；`cross_check` `is_oge_url` 为「至少 1 份 oge.gov」 |
| **parse 性能** | 全 PDF `parse_stats` 约 **4+ 分钟** |
| **事件研究样本** | 657 事件 ≈ 657 tickers — 每个 ticker 在当前数据集中大多只对应一次披露事件 |

---

## 十、📜 历史错误 run 数字（禁止再引用）

| 指标 | 旧值 |
|------|------|
| `trades_raw` / 解析 | 1,182 |
| 可交易 | 1,043 |
| 组合收益 | **-58.6%**（串联） |
| 事件研究行 | 4,168 |
| `news_near_disclosure` | 1,043 |
| `social_near_trade` | 0 |
| 披露日 | 全 **2026-05-12** |
| 解析率（报告） | **0%** |
| ISTB / amount / META 2/10 | 有问题 |

---

## 十一、已实施代码修复清单（供对照）

| 模块 | 修复内容 |
|------|----------|
| `backtest.py` | 等权组合回测；`event_study_universe`；`RELEVANT_NEWS`；`_naive_ts`；`price_on_trading_day_offset` |
| `prices.py` | `trading_day_on_or_after`、`price_on_trading_day_offset` |
| `equity_trades.py` | 金额 ≥1k；IEMG 映射；`parse_stats_all`；多 filing；去硬编码披露日 |
| `ticker_resolver.py` | `MANUAL_ETF_MAP`；`_is_ocr_garbage` |
| `events.py` | 跳过 placeholder；news cache |
| `equity_disclosures.py` | `TRUMP_278T_FILINGS` 多 PDF；manifest 披露日 |
| `run_pipeline.py` | META 3/18；`generate_report`；图表 STEP |
| `generate_report.py` | 组合收益文案；多披露日表 |
| `generate_notebooks.py` | 修正 backtest 绘图列 |

---

## 十二、数据文件快查（最新 run，2026-05-30）

| 文件 | 规模 | 备注 |
|------|------|------|
| `data/raw/disclosures/*.pdf` | 6 ok + 1 fail | bond 包失败 |
| `data/processed/manifest.csv` | 7 行 | |
| `trades_raw.csv` | 3,903 | 983 `other`；1,589 无 ticker |
| `trades_analysis.csv` | 2,314 | equity 2225 / reit 54 / etf 35 |
| `returns.parquet` | 2,314 | |
| `backtest.parquet` | 2,310 | |
| `event_study.parquet` | 2,628 | |
| `trade_event_links.parquet` | 2,314 | 仅 disclosure_event |
| `events.parquet` | 41 | news only |
| `final_summary.json` | — | `portfolio_return_equal_weight` 等 |
| `reports/FINAL_REPORT.md` | — | 生成 23:25 |
| `reports/FINAL_REPORT.pdf` | — | 存在 |
| `reports/figures/*.png` | 8 | |

---

## 十三、逐项状态总表

| ID | 状态 |
|----|------|
| P0-01 ~ P0-03, P0-05 | ✅ |
| P0-04 | 🟡 Truth archive ✅；strict 误匹配仍多（§18） |
| P1-01 | 🟡 |
| P1-02, P1-03, P1-04, P1-05, P1-07, P1-08, P1-09 | ✅ |
| P1-06 | 🟡 |
| P2-01 ~ P2-04, P2-07, P2-11 | ✅ |
| P2-05, P2-06, P2-08 ~ P2-10 | ❌ |
| P2-12, P2-13 | 🟡 |
| P3-01 ~ P3-04 | ✅ |
| S1 ~ S8 | 见第四节 |
| N1 | ✅ |
| N2, N3, N4, N6 ~ N11 | 🟡/❌ 见第九节 |
| R5-01 | 🟡 见 §16.2：口径改为 `amount_min`；旧报告/parquet 若仍用中点需重跑 |
| R5-02 ~ R5-05 | 🟡 见第十六节 |
| N7-01 ~ N7-08 | ❌/🟡 见 §18（新闻模块） |

---

## 十八、第七轮：新闻 / 社交媒体匹配验收（2026-05-31）

### 18.1 已改进 ✅

| 项 | 说明 |
|----|------|
| Truth Social 真实数据 | CNN `truth_archive.json` 缓存 ~18MB、**888** 条帖子（P0-04 **部分修复**） |
| Google News | 多 query RSS + 按 ticker `Trump {T} stock`；`events.parquet` **1345** 条 |
| 交易中心匹配 | `align_events_to_trades()`：交易日/披露日 ±30 天 |
| 媒体规律分析 | `media_patterns.py`：Top pairs、FIFO 买→发帖→卖、PnL 汇总 |
| 报告章节 | `FINAL_REPORT.md` §新闻 + §媒体匹配×收益 |

### 18.2 核心问题

#### N7-01 `news_disclosure_general` 批量 fan-out 夸大覆盖率（P1）

- **35** 篇「Trump 披露/278-T」类宏观新闻 × **2303** 笔同披露日交易 ≈ **80,530** 链接（占全部 88,607 的 **91%**）。
- 导致报告写「**2304/2314** 笔至少有 1 条新闻匹配」，但 **ticker 级**链接（去掉 `news_disclosure_general`）仅 **445/2314（19%）**。
- 该类型**不要求**标题出现 ticker，与报告文案「仅当明确提及 ticker」**不一致**。

#### N7-02 `strict_ticker_in_event` 误匹配仍严重（P0 — 媒体规律结论不可全信）

`media_patterns` 称已用「标题必须出现 ticker」的 strict 筛选，但 `\bTICKER\b` 对 **1–3 字母 ticker** 与 **Truth 宏观帖** 误报极多：

| Ticker | 误匹配来源（实测） | strict 链接数 |
|--------|-------------------|--------------|
| **S** | `U.S.`、`401K'S`、`WOMEN'S` 等 | 1582 |
| **A** | 英文冠词 **a**（"is **a** Great Man"） | 2185 |
| **HE** | 代词 **he**（"as **he** speaks"） | 625 |
| **BE** | 助动词 **be**（"will **be** a tremendous"） | 224 |
| **MAN** | 普通词 **Man** | 30 |
| **PM** | **UK PM**、**PM Modi**（非 Philip Morris） | 42 |
| **MO** | 人名 **Mo Brooks**（长帖末尾，标题截断不可见） | Top pairs 第 4 |
| **DASH** | "**Dash** of Malta"（非 DoorDash） | buy→卖样例 |

- **71.4%** 的 strict 链接来自 ticker 长度 ≤2；**66%** 来自 **Truth Social** 政治/关税帖，非股票帖。
- `TICKER_STOP` 在 `mention_tickers()` 用，但 **`strict_ticker_in_event()` 未用**，故 PM/BE/HE 等仍匹配。

#### N7-03 报告数字标签误导（P2）

| 报告写法 | 实际含义 |
|----------|----------|
| 「载入事件 **652** 条」 | 仅有链接的 **unique event_id**（`summarize_event_links.n_unique_events`），非 `events.parquet` 的 **1345** 条 |
| 「google_news: **83,260**」 | 链接行按 platform 计数（含 fan-out），非独立新闻篇数（实际 Google 事件 **457** 条） |

#### N7-04 Top 表 / 样例仍含噪声（P2）

- **Top tickers**：S(464)、A(374)、HE(176) 居首 — 主要为误匹配，非「媒体关注度」。
- **Top pairs**：PM 行重复 2 次；MSFT **sale** 按该笔 sale 的 +10d PnL 排序（逻辑 OK，易误读为「买入后新闻」）。
- **MO** Top pair：Truth 关税/背书帖，因正文 "Mo Brooks" 命中 **MO** ticker，标题截断 160 字**看不出**误匹配原因。

#### N7-05 相对可信的子集 ✅

- **FIFO 买→发帖→卖（15 对）**里部分样例 **可信**：META、AMD、JPM、FTNT 等为 Google News 标题含 `$T`/公司名。
- **DASH** 样例仍为误匹配（"Dash of Malta"）。
- 报告自身解读（「未见稳定 secret 建仓→Truth 喊单→卖出」）方向合理，但 **15 对 / 33 对** 的分母仍可能被 Truth 误匹配放大，宜人工 spot-check。

### 18.3 建议修复（按优先级）

1. **`strict_ticker_in_event` 加强**：1 字母 ticker 仅认 `$T` 或 `(T)`；2–3 字母加入 **ENGLISH_STOP**（a, be, he, man, pm 等）；PM 需 **stock context**（stock/shares/NYSE）或 `$PM`。
2. **Truth 帖**：默认仅当 `$TICKER` / 公司全名 / `(NASDAQ: XX)` 才链接；宏观帖不参与 ticker 级分析。
3. **报告指标拆分**：分别报 `news_disclosure_general` 与 **ticker-specific** 覆盖率（445/2314）；「载入事件」改为「events.parquet N 条 / 参与链接 M 条」。
4. **`top_trade_event_pairs`**：`drop_duplicates` 后按 `(ticker, event_id)` 去重；标题展示误匹配 snippet。
5. **P0-04 余项**：`truth_social_manual.json` 仍有 **1** 条 placeholder（影响极小）。

### 18.4 验收表

| 检查项 | 状态 |
|--------|------|
| Truth Social 真实 archive | ✅ |
| Google News RSS 抓取 | ✅ |
| 交易 ±30d / 披露 ±30d 窗口 | ✅ |
| ticker 级 strict 筛选有效 | ❌ N7-02 |
| 覆盖率数字不被 fan-out 夸大 | ❌ N7-01 |
| 买→发帖→卖 子集有可读案例 | 🟡 部分可信 |
| 报告表述与实现一致 | ❌ N7-01、N7-03 |

---

## 十四、建议后续（按优先级）

### 股票研究优先

0. **Notional 口径**：加权用 **`amount_min`**（见 §16.2）；重跑 pipeline 刷新 `reports/`、`FINAL_REPORT.md`（勿用 `(min+max)/2`）。
1. 分析默认过滤：`doc_id == trump_278t_2026_05_08_equity` + Q1 + `asset_class in (equity, etf)`（见附录）。
2. 报告数字改为 **2,314 / 2,194** 可交易，勿写 3,903「股票笔数」。
3. 扩展 `TICKER_MAP` / 手工表，压低 **606** 无 ticker。
4. `BOND_PATTERNS` 增加 `PERP`、`PFD`；`equity_trades.infer_ticker` 同步 garbage 检测。

### 研究完整性

5. Truth Social 真实数据或删除 PLAN 社交 alpha。→ **archive 已接入**；ticker 匹配规则见 §18.3。
6. 披露日：统一 **filing 5/8** vs **OGE received 5/12** 脚注。
7. Placebo、event study 交易日窗口、`by_ticker` 按披露日聚合。
8. 修复 `FINAL_REPORT` 图片路径与 bond 行表格表述。

### 工程

9. 可选：仅 parse `likely_equity_filing == True` 的 PDF。
10. 重试或镜像 `trump_278t_2026_05_08_bond.pdf`。

---

## 十五、附录：推荐股票样本过滤

```python
import pandas as pd

tr = pd.read_csv(
    "reports/trades_analysis.csv",
    parse_dates=["transaction_date", "disclosure_date"],
)

# 推荐：2026 Q1 bulk 股票/ETF（不含 REIT）
stocks = tr[
    tr["asset_class"].isin(["equity", "etf"])
    & (tr["doc_id"] == "trump_278t_2026_05_08_equity")
    & (tr["transaction_date"] >= "2026-01-06")
    & (tr["transaction_date"] <= "2026-03-30")
]
# 约 2,194 笔，634 tickers

# 若含 REIT：把 asset_class 改为 ["equity", "etf", "reit"] → 与全表 2,310 bulk 接近
```

---

## 十六、第五轮：双锚点收益 + Notional 加权 PnL（用户 comment 验收）

**验收日期**：2026-05-30  
**对照需求**（用户 comment）：

1. **Trump 交易是否赚钱**：锚点 = `transaction_date`（与披露日无关）； horizons = 1/3/5/10/20/30 **交易日**；卖出方向 `sign = -1`。
2. **能否 follow Trump**：锚点 = `disclosure_date`；同样 horizons 与 sign。
3. **不要组合复利收益**：每笔 `pnl_h = notional × signed_return`；整体 = `sum(pnl_h) / sum(notional)`（notional-weighted）。
4. **累计 PnL 图**：x = 日期，y = 按日汇总后的累计 PnL。

### 16.1 实现对照（代码层 ✅）

| 需求项 | 实现位置 | 结论 |
|--------|----------|------|
| 双锚点 | `src/trade_returns.py` → `run_both_analyses()`：`anchor_date` = transaction / disclosure | ✅ |
| 1–30 交易日价格 | `price_on_trading_day_offset()` + `compute_horizon_returns()` | ✅ |
| 买卖 sign | `direction_sign`：purchase +1，sale −1 | ✅ |
| NW 汇总 | `notional_weighted_summary()`：`sum(pnl)/sum(notional)` | ✅ 与手算一致 |
| Notional 口径 | **`notional = amount_min`**（OGE 区间下界）；不用区间中点 | ✅ 见 §16.2（用户确认，仅文档/实现约定） |
| 累计 PnL | `cumulative_pnl_by_date()`：按 `anchor_date` 日汇总 `pnl_*` 再 `cumsum` | ✅ 逻辑符合「按日期累加」 |
| Pipeline 输出 | `run_pipeline.py` STEP 7；`trump_timing_*` / `follow_disclosure_*` parquet & CSV | ✅ |
| 图表 | `10_trump_notional_returns`、`11_follow_notional_returns`、`12/13_*_cumulative_pnl` | ⚠️ 见 16.4 |

**未纳入主结论但仍存在**：`src/backtest.py` 披露日 +1d 等权组合、`FINAL_REPORT.md` 附录仍引用旧 backtest — 与用户「不要组合收益」并存，易误读（建议主文只保留 NW 两分析）。

### 16.2 Notional 口径：用 `amount_min`，不用区间中点

**用户确认（2026-05-30）**：每笔交易的 notional 取 OGE 披露区间的 **`amount_min`（下界）**，**不要**用 `(amount_min + amount_max) / 2`。

**第六轮（2026-05-31）已实现**：`trade_notional()` 优先 `amount_min`，且 `amount_min/max` 须 `< MAX_SANE_NOTIONAL`（1e9+1）；否则回退另一侧或 `NaN`。ORCL 坏 `amount_max` 行 notional = **100,001**；MA 坏 `amount_min` 行回退 **amount_max = 50,000**。

| 指标 | 旧报告（区间中点 + ORCL 坏 max） | **重跑后（`amount_min`）** |
|------|----------------------------------|----------------------------|
| Trump `total_notional` | ~$125T | **$148,906,275**（2288 笔） |
| Trump NW +1d | −2.33% | **−0.15%** |
| Trump NW +30d | +10.72% | **+1.28%** |
| Trump EW +1d | ~−0.22% | **−0.22%** |

`reports/trump_timing_summary.csv`、`FINAL_REPORT.md`、`final_summary.json` 与 parquet **已对齐**（见 §17）。

### 16.3 双分析数值快照（历史手算 2026-05-30）

> **最新数字以 §17.2 为准**（全 pipeline 重跑 2026-05-31）。下表仅保留第五轮对照；Follow +20/+30d 当时误写成全样本，实为 **4 笔**（价格窗口限制）。

### 16.4 次要问题

| ID | 问题 | 严重度 |
|----|------|--------|
| R5-01 | 旧报告用区间中点；**已约定改用 `amount_min`**；产物/parquet 需重跑对齐 | P1 | **✅ 第六轮已修并重跑** |
| R5-01b | MA 一笔 `amount_min` OCR 异常（与 ORCL 无关），宜核对 PDF | P2 | 仍待人工 |
| R5-02 | `reports/*_summary.csv` 与 `final_summary.json` 可能落后于 `data/processed/*.parquet` | P1 | **✅ 第六轮已重跑** |
| R5-03 | `plot_cumulative_pnl()` 只画 1/5/10/20/30d，**缺 +3d** | P2 | **✅ 第六轮已修** |
| R5-04 | 累计图 y 轴除以 1e6；若 `notional` 列仍为中点则尺度失真 | P1（随 `amount_min` 重跑缓解） | **✅ 第六轮已修** |
| R5-05 | 主报告仍突出旧 `portfolio_return_equal_weight` | P2 文档 | **✅ 第六轮已修** |

### 16.5 用户需求检查清单

- [x] Trump：按**交易日期**算 1/3/5/10/20/30 交易日收益，卖出 sign −1  
- [x] Follow：按**披露日期**算同样 horizons  
- [x] 整体收益 = Σ(notional×return) / Σ(notional)，非组合复利  
- [x] Notional = **`amount_min`**（不用区间中点）  
- [x] 累计 PnL 序列 + 图（按 anchor 日）  
- [x] **可发布结论**：重跑 pipeline，使 `reports/`、`FINAL_REPORT.md` 与 `amount_min` 一致  
- [x] 图表含 +3d（可选）

---

## Changelog

| 时间 | 变更 |
|------|------|
| 2026-05-30 | 初版审计（P0–P3） |
| 2026-05-30 | 补充全 PDF `parse_stats`（>100% / 慢） |
| 2026-05-30 | 第二轮：代码已修、产物未重跑（N1） |
| 2026-05-30 | 第三轮：重跑后验收；N6–N11 |
| 2026-05-30 | 第四轮：股票/ETF 专项 S1–S8 |
| 2026-05-30 | **合并为完整观察日志**：历史+现况+修复清单+总表+附录 |
| 2026-05-30 | **第五轮**：双锚点 NW 收益验收；R5-01 ORCL notional P0；报告陈旧 R5-02 |
| 2026-05-30 | §16.2：**notional 改用 `amount_min`**（用户确认）；更新参考 NW 表；弃用「排除 notional≥1e9」 |
| 2026-05-31 | **第六轮**：代码修复 + 全 pipeline 重跑；见 §17 |
| 2026-05-31 | **第七轮**：新闻/Truth 匹配验收；N7-01~08（fan-out、ticker 误匹配、报告标签） |

---

## 17. 第六轮验收（2026-05-31）

### 17.1 已修复（审计结论 ✅ → 已改代码并重跑）

| ID | 问题 | 修复 |
|----|------|------|
| R5-01 | NW 用区间中点 / ORCL 畸变 | `trade_notional()` 固定 **`amount_min`** + `MAX_SANE_NOTIONAL` 过滤 OCR 垃圾 |
| R5-02 | reports 与 parquet 不同步 | 全 pipeline 重跑；`FINAL_REPORT.md` / `final_summary.json` 已对齐 |
| R5-03 | 累计图缺 +3d | `plot_cumulative_pnl` horizons → `[1,3,5,10,20,30]` |
| R5-04 | y 轴固定 $M 失真 | 按 max\|PnL\| 自动选 $ / $K / $M |
| R5-05 | 主报告突出旧 +2.31% | NW 双锚点为主；旧等权回测移至附录 |
| P1-01 | bulk equity 披露日 5/8 vs 5/12 | catalog `disclosure_received_date: 2026-05-12` + OCR  tolerant `extract_oge_received_date` |
| P2-06 | `by_ticker` 未按披露日 dedupe | `drop_duplicates(ticker, disclosure_date)` 后再 groupby |
| P2-13 | event study 用日历日 | `_market_model_ar` 改为 **trading-day** offset |
| S5 | PERP/PFD 未过滤 | `BOND_PATTERNS` 增加 `PERP\|PFD\|PREFERRED` |

### 17.2 重跑后关键数字（2026-05-31 独立复核 ✅）

- 解析 **3,900** 行；可交易 **2,314**（equity+ETF **2,260**）；进入 horizon 收益 **2,288**（26 笔无锚点价/无 ticker 价）
- `total_notional`（各 horizon 一致）：**$148,906,275**
- 披露日：**2025-08-19, 2026-01-14, 2026-04-23, 2026-05-12**（bulk equity **2026-05-12**）
- 旧等权披露日回测（附录）：**+2.25%**

**Trump timing**（2288 笔，锚点 = 交易发生日）

| Horizon | NW | EW |
|---------|-----|-----|
| +1d | −0.15% | −0.22% |
| +3d | +0.16% | −0.32% |
| +5d | +0.00% | −0.57% |
| +10d | −0.15% | −0.40% |
| +20d | −0.08% | +0.38% |
| +30d | +1.28% | +2.04% |

**Follow disclosure**（锚点 = 披露日）

| Horizon | 有效笔数 | NW | EW |
|---------|---------|-----|-----|
| +1d | 2288 | +0.06% | −0.21% |
| +3d | 2288 | −0.15% | −0.27% |
| +5d | 2288 | −0.30% | −0.39% |
| +10d | 2288 | +0.68% | +1.11% |
| +20d | **4** | +3.46% | +3.15% |
| +30d | **4** | +0.31% | +2.37% |

- `trump_timing_summary.csv` ↔ parquet：`notional_weighted_return` **逐 horizon 一致**
- `trump_cumulative_pnl.csv` 末行 `cum_pnl_30d` = summary `total_pnl` **一致**

### 17.3 仍成立 / 未改（审计正确，非 bug）

| ID | 说明 |
|----|------|
| Follow +20d/+30d 仅 **4 笔** | **价格截止 ~2026-05-30**；5/12 bulk 披露尚不足 20/30 交易日 → 严格 horizon 返回空，样本只剩早期披露日几笔。**非代码错误**。 |
| R5-01b MA `amount_min` OCR | 仍宜人工核对 PDF；`MAX_SANE_NOTIONAL` 会跳过极端值 |
| S2 ~586 行无 ticker | 解析/OCR 限制；未在本轮扩展 |
| P0-04 Truth Social | CNN archive **888 帖**已接入；ticker strict 匹配仍误报（§18） |
| bond PDF 404 | `trump_278t_2026_05_08_bond` 下载失败；不影响 equity 分析 |
| S6 cross-check bond 误标含股票 | **已修** `bool(nan)` 导致误显示 ✅ |

### 17.4 方法论验收清单（重跑后）

- [x] 双锚点：交易发生日 vs 披露日
- [x] Horizons 1/3/5/10/20/30 **交易日**；卖出 sign −1
- [x] NW = Σ(notional×ret) / Σ(notional)；非组合复利
- [x] Notional = `amount_min` + OCR 上限过滤
- [x] 累计 PnL 按 anchor 日汇总 + 图（含 +3d）
- [x] `FINAL_REPORT.md` / CSV / JSON / parquet 数字一致
- [ ] Follow **+20d/+30d** 对 bulk（5/12 披露）需更长行情；当前 **勿解读** 为全样本结论
- [ ] MA 坏 `amount_min` 行已用 `amount_max` 回退，宜 PDF 核对

### 17.5 发布前可选

- [ ] 价格窗口延长后重算 Follow +20/+30d（bulk 样本）
- [ ] MA 单笔 OCR 人工修正
- [ ] Placebo / REIT 子样本敏感性
