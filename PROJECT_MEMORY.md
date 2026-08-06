# MagicSTG Quantitative System - Project Memory & Handoff
*Last Updated: 2026-08-02 00:08 (Local Time)*

This document serves as a persistent context handoff for MagicSTG. It ensures future development sessions or different AI agents can pick up the work instantly without confusion.

---

## 1. Project Overview & Architecture
MagicSTG is a quantitative stock selection, recommendation, and backtesting dashboard for A-shares.
* **Backend**: Flask (Python) running on **Render** (Cloud Hosting).
* **Database**: **TiDB Cloud** (Distributed MySQL-compatible).
* **Automated Job**: **GitHub Actions** runs daily data pull and strategy runners after market close to update recommendation tables.

---

## 2. TiDB Database Schemas
The database `asharedb` contains both raw data tables and system tables created for the dashboard:

### Core Data Tables (Pre-existing)
* **`stock_kline_day`**: Daily K-line table. Columns: `code` (`sh.600000`), `date`, `open`, `high`, `low`, `close`, `volume`, `peTTM` (used for PE strategy calculations).
* **`stock_profit_quarterly`**: Profitability stats. Contains `roe_avg` (average ROE) and `pub_date` (actual disclosure date, used to prevent future-data bias).
* **`stock_growth_quarterly`**: Growth stats. Contains `YOYNI` (Net Income YoY growth).
* **`stock_balance_quarterly`**: Balance sheet stats. Contains `liabilityToAsset` (Debt ratio).
* **`stock_cash_flow_quarterly`**: Cash flow stats. Contains `CFOToNP` (Operating Cash Flow / Net Profit ratio).

### Dashboard System Tables (Created on 2026-07-16)
* **`strategy_checkpoints`**: Saves strategy scan progress to database (avoids ephemeral container loss on Render).
  * Fields: `strategy` (PK), `last_check_date`, `updated_at`.
* **`recommendations`**: Stores daily strategy recommendations.
  * Fields: `id` (PK), `strategy`, `stock_code`, `action` (`BUY`/`SELL`), `price`, `reason`, `signal_date` (Unique Index: `strategy` + `stock_code` + `signal_date`), `factor_data` (JSON, extensible), `created_at`.
* **`positions`**: Stores simulated/active holdings for each strategy.
  * Fields: `id` (PK), `strategy`, `stock_code`, `buy_date`, `buy_price`, `shares`, `cost_total`, `current_price`, `market_value`, `pnl`, `pnl_pct`, `status` (`HOLDING`/`SOLD`), `sell_date`, `sell_price`, `extra_data` (JSON).

### Convertible Bond Data Tables (Created on 2026-07-30 & Unified on 2026-07-31)
* **`cb_basic`**: Convertible bond master info table (`code` e.g. `sh.113008`, `name`, `stock_code` e.g. `sh.600000`, `stock_name`, `convert_price`, `issue_size`, `curr_issue_size`, `list_date`, `delist_date`, `maturity_date`, `rating`).
* **`cb_kline_day`**: Convertible bond daily K-line table (`code` e.g. `sh.113008`, `date`, `open`, `high`, `low`, `close`, `volume`, `amount`, `pctChg`, `turn`).
* **`cb_daily_indicator`**: Convertible bond daily specialized metrics (`code` e.g. `sh.113008`, `date`, `cb_price`, `stock_code` e.g. `sh.600000`, `stock_price`, `convert_price`, `convert_value`, `convert_premium_rate`, `db_low_value`).

---

## 3. Modular Architecture & Key Components

The codebase has been refactored into a highly modular, plugin-based architecture for high scalability:

* **`MagicSTG/config/`**:
  * `settings.py`: Centralized environment variable loading (`.env`), SSL CA path resolution (`isrgrootx1.pem`), and YAML configuration loader.
* **`MagicSTG/core/`**:
  * `db.py`: Primary database connection, heartbeat pinging, data loaders (`load_all_data_db`, `load_roe_data_db`, `load_cb_data_db`), and strategy checkpoints.
  * `db_writer.py`: Persistence engine for recommendations, positions, and backtest results into TiDB Cloud.
  * `cost.py` & `limit.py`: Transaction fee calculations and price limit-up/down checking.
* **`MagicSTG/strategies/`**:
  * `base.py`: Abstract `BaseStrategy` base class defining strategy contract (`generate_signals`).
  * `dynamic_factor.py`: Plugin-based multi-factor dynamic strategy implementation (`DynamicFactorStrategy`).
  * `cb_double_low.py`: Convertible bond Double-Low strategy implementation (`CBDoubleLowStrategy`).
  * `registry.py`: Strategy factory and registry (`StrategyRegistry.get(name)`).
  * `legacy/`: Archived single-factor legacy strategies (`pe_strategy`, `price_strategy`, `roe_strategy`).
* **`MagicSTG/execution/`**:
  * `position_manager.py`: Holding position manager (`PositionManager`, `InMemoryPositionManager`).
  * `decisions.py`: Trade decision maker engine (`DecisionMaker`, `TradeDecision`).
* **`MagicSTG/data/`**:
  * `stock/`, `macro/`, `index/`, `bond/`: Categorized data collection modules.
  * `runner.py`: Unified daily data update controller.
* **`MagicSTG/web/server.py`**:
  * Flask REST API & Web Dashboard server deployed on Render (`gunicorn --timeout 120 MagicSTG.web.server:app`).


---

## 4. Production Deployment Notes (Render)
1. **SSL Certificate**: The SSL certificate `MagicSTG/dbconfig/isrgrootx1.pem` must be pushed to Git. `utils/db.py` reads it using relative pathing from `PROJECT_ROOT`.
2. **Start Command**: Must increase Gunicorn timeout to prevent worker kills during 2.5-year daily range queries.
   * Start Command: `gunicorn --timeout 120 MagicSTG.web.server:app`
3. **Environment Variables on Render**: Ensure `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_PORT`, `DB_SSL_CA` are configured in Render dashboard.

---

## 5. 每日数据更新逻辑与数据库状态跟踪机制

### 5.1 自动化更新架构与调度策略
* **最佳触发时间**：推荐每日 **18:30 / 18:31（北京时间）** 触发。对应 GitHub Actions Cron 表达式为 `31 10 * * *` (UTC)。
  * **依据**：Baostock 与 AKShare 盘后数据在 18:00 ~ 18:30 全市场准备就绪。18:30 以后运行能保证数据的绝对完整与稳定。
* **目标交易日定位 (`get_target_date`)**：
  * 使用时区感知的 `datetime.now(timezone(timedelta(hours=8)))`，排除所有宿主环境的时区计算偏差。
  * **北京时间 < 18:30:00** 运行：`target_date` 自动定位为 **昨天**（上一交易日）。
  * **北京时间 ≥ 18:30:00** 运行：`target_date` 自动定位为 **当天**（当天收盘日）。

### 5.2 任务归档与断点续传机制 (`task_date`)
* **核心归档原则**：主控脚本 `run_all_updates.py` 的任务归档日期 `task_date` **严格绑定 `get_target_date()`**（即行情目标日），而非启动脚本时的挂钟日历时间。
* **跨日/凌晨运行无冲突保障**：
  * 若在次日凌晨（如 24 日 01:00）手动运行，`get_target_date()` 判定当前属于 18:30 前，归档 `task_date` 为 **23 日**，更新并标记 23 日完成。
  * 到了 24 日 18:31 盘后，定时任务触发，`get_target_date()` 判定归档 `task_date` 为 **24 日**。系统检查发现 24 日未完成，**仍会正常触发并拉取 24 日当天的最新收盘数据**。

### 5.3 数据库状态跟踪表 (`update_progress`)
主控脚本通过 TiDB 中的 `update_progress` 系统表管理增量更新状态与断点续传：
* **表结构与字段**：
  * `task_date` (DATE): 任务对应的行情目标日（如 `2026-07-23`）。
  * `script_name` (VARCHAR): 对应的更新脚本名称（如 `update_stock_kline_day.py`）。
  * `status` (VARCHAR): 执行状态 (`pending` | `running` | `success` | `failed`)。
  * `started_at`, `completed_at` (DATETIME): 执行起始与完成时间。
  * `error_msg` (TEXT): 错误追踪日志。
* **状态流转规则**：
  1. **`pending`**：待执行。
  2. **`running`**：执行中。若进程意外崩溃断电，下次启动时 `auto_reset_running_task` 会自动将其重置为 `pending` 恢复断点续传。
  3. **`success`**：执行成功。同一天再次运行脚本时，系统查询到 `success` 标记会自动跳过重复执行。
  4. **`failed`**：失败。可通过手动重置修复。

### 5.4 脚本健壮性与数据清洗保护
1. **数据库连接心跳 (`ensure_connection_alive`)**：
   在所有更新脚本（日K、周K、月K、可转债）的游标操作与缓冲池刷新前加入心跳自动重连，彻底避免因网络拉取耗时引发的 TiDB 空闲连接断开错误 `(0, '')`。
2. **月K线/周K线未完结快照自动覆盖**：
   对于月K线，增量起点自动对齐至 `last_date` 所在月份 1 号，并在写入前自动清理该月份已有记录。无论月中何时运行，月末/下月更新时均能无缝清理旧月中快照（如 7-22），替换为完整的月末完结K线（如 7-31），杜绝主键重复与数据两份残留。

### 5.5 GitHub Actions 手动控制扩展
通过 `.github/workflows/update_ashare_data.yml` 中的 `workflow_dispatch` 暴露了两个手动重置/强制开关：
* **`force`** (boolean): 忽略数据库中的 `success` 状态标记，强行重新拉取并重新执行所有脚本。
* **`reset`** (boolean): 重置当天的 `update_progress` 进度状态。

---

## 6. Convertible Bond Data (2026-07-31)
* **Completed Progress (2026-07-31)**:
  * **Database Architecture**: Created `cb_basic`, `cb_kline_day`, and `cb_daily_indicator` in TiDB Cloud (`init_cb_tables.py`).
  * **Code Format Unification**: Fully migrated all convertible bond tables (`cb_basic`, `cb_kline_day`, `cb_daily_indicator`) to Baostock standard format (`sh.113008` / `sh.600000`), enabling native zero-overhead indexed SQL joins with `stock_kline_day`.
  * **Master Info (`cb_basic`)**: Fully loaded 1,044 convertible bonds (1,016 active records updated to `sh.113008`). Auto-filled转股价 for 702 historical/delisted bonds via AKShare detail API (`update_cb_basic.py`).
  * **Daily K-Lines (`cb_kline_day`)**: Fully loaded and updated 662,130 daily K-line records spanning 2020-01-01 to present in `sh.113008` format (`update_cb_kline_day.py`).
  * **Daily Metrics (`cb_daily_indicator`)**: Refactored `update_cb_daily_indicator.py` with SQL index optimization (`st.code = b.stock_code AND cb.date = st.date`). Fully calculated and updated **656,354** records spanning **2020-01-02 to 2026-07-30** (100% completed).

---

## 7. Session Memory & Strategy Development (2026-08-01 ~ 2026-08-02)

* **Completed Accomplishments**:
  1. **Data Pipeline Fixes & GitHub Actions Integration**:
     - Fixed `format_time` in `MagicSTG/data/runner.py` to handle elapsed seconds (`float`/`int`).
     - Fixed `.gitignore` path matching rule (`data/` -> `/data/`) to track all update scripts in `MagicSTG/data/`.
     - Fixed global import path `from MagicSTG.db import ...` across all 19 update scripts (`stock/`, `macro/`, `index/`).
     - Enhanced `update_cb_kline_day.py` to filter out non-mainstream Beijing Stock Exchange bond codes (`bj.`) and handle AKShare `KeyError` gracefully.
     - Backfilled `update_progress` status in TiDB Cloud for historical tracking.
  2. **Convertible Bond Double-Low Strategy (`CBDoubleLowStrategy`)**:
     - Implemented `CBDoubleLowStrategy` in `MagicSTG/strategies/cb_double_low.py` (Double-Low Value = Bond Price + Convert Premium Rate; Filters: Price <= 130, Premium Rate range, Top N ranking).
     - Registered `'cb_double_low'` and `'cb_double_low_strategy'` in `StrategyRegistry` (`MagicSTG/strategies/registry.py`).
  3. **Convertible Bond Backtesting & Workflow Isolation**:
     - Added `load_cb_data_db()` to `MagicSTG/core/db.py` for loading `cb_daily_indicator` and `cb_basic`.
     - Developed `MagicSTG/run_cb_backtest.py` backtest engine script.
     - Executed 2022-2026 historical backtest (1,107 trading days, 50.6k records): Total Return **+20.86%**, Annualized **+4.24%**, Max Drawdown **-26.94%**, Win Rate **51.0%**, Final Capital **120,862.33 CNY**.
     - Created dedicated GitHub Actions workflow `.github/workflows/run_backtest.yml` ("策略回测"), completely separating strategy backtests from daily data pipeline (`update_ashare_data.yml`).
  4. **Architecture Consensus on Forced Redemption (强赎风控)**:
     - Confirmed NO NEED to create redundant database fields for forced redemption. Forced redemption warning will be dynamically calculated via a 30-day rolling window (`stock_price >= 1.3 * convert_price` for >= 15 days) directly in strategy/backtest code and real-time API filters.

* **Next Steps**:
  * Integrate Convertible Bond Double-Low Strategy into Web Dashboard (`MagicSTG/web/server.py`) and daily post-market recommendation generator.
  * Optimize strategy parameters (e.g. testing `max_price=120.0` or `115.0` to minimize drawdown).

---

## 8. Session Summary & Memory (2026-08-02)

* **Architecture Refactoring & Streamlining**:
  * Updated `.github/workflows/update_ashare_data.yml` to call `python -m MagicSTG.data.runner $CMD_ARGS` directly from project root.
  * Moved root design documents (`Python API文档.txt`, `表设计文档.txt`) into [`docs/`](file:///E:/ashare/docs/).
  * Removed obsolete zero-import forwarding folders (`MagicSTG/utils`, `MagicSTG/signals`, `MagicSTG/positions`, `MagicSTG/decisions`).
  * Preserved core data pipeline modules (`MagicSTG/db/`, `MagicSTG/data/`) ensuring 100% data update pipeline safety.

* **Automated Post-Market Strategy Execution & Web Integration**:
  * Developed [`MagicSTG/strategies/runner.py`](file:///E:/ashare/MagicSTG/strategies/runner.py) to automatically execute `cb_double_low` and `dynamic_factor` strategy recommendations and save to TiDB Cloud.
  * Appended strategy recommendation execution as the final step in [`MagicSTG/data/runner.py`](file:///E:/ashare/MagicSTG/data/runner.py)—triggering **only after 100% of daily market & indicator data updates finish successfully** (handling 2~5h data pull windows cleanly).
  * Executed test run for `2026-07-30`; generated and saved 10 Double-Low Convertible Bond BUY recommendations and 119 Multi-factor BUY/SELL recommendations to `recommendations` table in TiDB Cloud.
  * Added `cb_double_low` and `dynamic_factor` strategy tabs to Flask Web Dashboard ([`index.html`](file:///E:/ashare/MagicSTG/web/templates/index.html)), updated default strategy in [`server.py`](file:///E:/ashare/MagicSTG/web/server.py), and verified `/api/recommendations?strategy=cb_double_low` returns status 200 with live recommendation data.

* **Strategy Signal Refinement & Top N Quota Enforcement**:
  * Refactored legacy strategy generators ([`pe_strategy.py`](file:///E:/ashare/MagicSTG/strategies/legacy/pe_strategy.py), [`roe_strategy.py`](file:///E:/ashare/MagicSTG/strategies/legacy/roe_strategy.py), [`price_strategy.py`](file:///E:/ashare/MagicSTG/strategies/legacy/price_strategy.py)) to enforce strict `top_n` quotas (default Top 10).
  * `PE`: Sorts buy candidates ascending by PE (cheapest first) and returns Top 10.
  * `ROE`: Sorts buy candidates descending by ROE (highest quality first) and returns Top 10.
  * `Price`: Sorts buy candidates ascending by price and returns Top 10.
  * Guarantees that traders receive high-conviction, actionable recommendations (max 10 signals per strategy) rather than noisy unconstrained list dumps.

* **GitHub Repository Synchronization**:
  * All refactored code, docs, and strategy runner updates committed and pushed to `main` branch (`https://github.com/wxsw3000/ashare.git`).
  * Render web deployment automatically triggered.

* **Next Steps**:
  * Integrate custom user strategy configuration saving & run history querying into Flask Web UI.
  * Further optimize strategy parameters (e.g. testing `max_price=120.0` or `115.0` to minimize drawdown).

---

## 9. Session Summary & Memory (2026-08-03)

* **Signal Scheduling & Recommendations Clarification**:
  * Verified and documented post-market signal calculation timing: GitHub Actions triggers daily at 18:31 CST (`update_ashare_data.yml`), updates market data, and executes `MagicSTG/strategies/runner.py`.
  * Resolved user query regarding historical signal dates (7-15 and 7-28 legacy test data).

* **Signal Generation & Database Backfill (2026-07-31)**:
  * Executed `python -m MagicSTG.strategies.runner 2026-07-31` to populate recommendations for trade date 2026-07-31.
  * Generated and saved **10** `cb_double_low` recommendations and **176** `dynamic_factor` recommendations into TiDB Cloud `recommendations` table.
  * Confirmed Web API `/api/recommendations` retrieves and displays the 2026-07-31 latest signals cleanly.

* **Next Steps**:
  * Test deployed Web UI on Render.
  * Optimize strategy parameters (e.g. testing `max_price=120.0` or `115.0` to minimize drawdown).

---

## 10. Session Summary & Memory (2026-08-04) - Quant System v1.0 Implementation

* **Database Architecture & Strategy Table Initialization**:
  * Created `custom_strategies` table in TiDB Cloud (`MagicSTG/core/init_v1_tables.py`).
  * Populated 5 standard default strategies into TiDB Cloud: `cb_double_low` (可转债双低), `dynamic_factor` (动态多因子), `pe_strategy` (低估值PE), `roe_strategy` (高ROE盈利), `price_strategy` (低股价).

* **Backend REST APIs for Strategy Management & Recommendations**:
  * Implemented `/api/strategies` (GET, POST, PUT, DELETE) for custom strategy CRUD.
  * Implemented `/api/strategies/<id>/run` for target-date strategy execution with "already run today" prevention alert (`already_run: true`).
  * Enhanced `/api/recommendations` to perform SQL JOINs with `cb_basic` and `cb_daily_indicator`, providing enriched multi-dimensional fields (转债代码, 名称, 价格, 转股价值, 纯债价值, YTM, 正股代码, 正股名称, 正股价格).

* **Web UI Redesign & Mobile Device Responsiveness (v1.0 Mindmap Alignment)**:
  * Redesigned [`index.html`](file:///E:/ashare/MagicSTG/web/templates/index.html) into 3 main navigation tabs:
    1. **策略管理** (Strategy Management): Table list, create/edit modal, delete, run with prevention alert, backtest log execution modal with 1-click copy log button.
    2. **回测记录** (Backtest Records): Historical performance metrics table & interactive equity chart / trade details viewer.
    3. **策略推荐结果** (Strategy Recommendations): Master-Detail下钻交互架构。第一层为“行情时间 + 策略”执行履历卡片列表（显示行情日期、策略名称、买卖信号数量、生成时间）；点击任意卡片后平滑下钻进入第二层“推荐明细表格”（包含完整 9 大维度可转债数据/股票数据，并支持一键返回履历列表）。
  * **可视化因子表单建构器 (Visual Factor Builder Form)**: Embedded comprehensive multi-column factor controls inside the Create/Edit Strategy Modal—including MA short/long, volume surge factor, DMI (PDI/NDI) threshold, ROE min %, PE range [min, max], Profit growth YoY %, Debt limit %, Cash ratio (CFO/NP), primary factor sorting rules (Price, ROE, PE, Growth, DB Low Value), and stock/CB universe selections. Saved directly into TiDB Cloud `custom_strategies.factors_config` JSON.

---

## 11. Session Summary & Memory (2026-08-05) - Factor Decoupling & Performance Optimization

* **Convertible Bond Factor Decoupling & Stock Linkage Implementation**:
  * **Dynamic Form Switching (`index.html`)**: Splitted strategy modal factor builder into `#factorPanelStock` and `#factorPanelCB`. Automatically toggles dynamic factor panels based on strategy category (`stock` vs `convertible_bond`).
  * **Convertible Bond Dedicated Factors**: Exposed price limit (`max_price`), convert premium range (`min_convert_premium`, `max_convert_premium`), pure bond premium limit (`max_pure_bond_premium`), minimum YTM (`min_ytm`), and flexible bond primary sorting rules (`db_low_value`, `cb_price`, `convert_premium_rate`, `ytm`).
  * **Underlying Stock Linkage (`stock_linkage`)**: Added optional stock linkage controls (Stock ROE, Stock PE range, Stock MA trend) for convertible bond strategies, enabling dual bond-stock quantitative filtering.

* **Backtest Engine Dual-Channel Routing & Performance Optimization**:
  * **CB Backtest Integration (`engine.py`)**: Routed convertible bond strategy backtests in `run_backtest_engine` to `run_cb_backtest`, returning unified equity curve & trade log JSON formats (eliminated HTML 500 error).
  * **Binary Search Financial Lookup (`dynamic_factor.py`)**: Optimized quarterly financial report loading by filtering target universe stocks (`target_codes`) and replacing $O(N)$ pandas boolean filtering with $O(\log N)$ `np.searchsorted` binary search (compressed lookup times from 120s to milliseconds).
  * **Runner & Web API Synchronization (`runner.py`, `server.py`)**: Updated strategy execution endpoints to pass user custom factor configurations (`factors_config`) to strategy instances.
  * **Web UI Feedback & Date Range Guard (`index.html`)**: Added button loading spinner state (`⏳ 计算中...`) for strategy execution and set default backtest start date to `2025-01-01` to guarantee high-performance response times without browser network timeouts.

* **Memory Downcasting & Explicit Garbage Collection (Fix Render OOM)**:
  * **Numeric Downcasting (`db.py`, `dynamic_factor.py`)**: Downcasted all K-line prices, volumes, valuation metrics, and quarterly financial ratios (`roe_avg`, `YOYNI`, `liabilityToAsset`, `CFOToNP`, `cb_price`, etc.) from `float64`/`int64` to `float32`/`int32`, cutting DataFrame RAM consumption by 50%.
  * **Explicit Garbage Collection (`db.py`, `dynamic_factor.py`, `engine.py`, `server.py`)**: Inserted explicit `del` operations on intermediate temporary DataFrames and invoked `gc.collect()` in data loaders, backtest engine completion, and Flask Web API `finally` blocks to instantly release freed memory on Render.

* **JSON Serialization Fix & Instant Interactive Backtest UX**:
  * **NumPy Type Sanitization (`engine.py`, `server.py`)**: Added recursive `sanitize_json` to convert `np.float32`, `np.float64`, `np.int32`, and `pd.Timestamp` to standard Python types, eliminating `TypeError: Object of type float32 is not JSON serializable`.
  * **Instant-Open Modal & Pre-Calculation Date Picker (`index.html`)**: Redesigned backtest modal so clicking 【回测】 opens the modal instantly with default rolling 1-year date range, allowing the user to select/confirm start and end dates *before* initiating backtest execution.

---

## 12. Session Summary & Memory (2026-08-06) - Strategy Run & Recommendation Pipeline Fixes

* **Fix Missing `json` Import in `server.py`**:
  * **Issue**: Clicking "策略 运行" in Web UI triggered `❌ 策略运行失败: 运行失败: name 'json' is not defined` because `run_strategy` route in `MagicSTG/web/server.py` attempted to call `json.loads(factors_cfg)` without `import json` in module scope.
  * **Fix**: Added top-level `import json` to [`MagicSTG/web/server.py`](file:///E:/ashare/MagicSTG/web/server.py). Verified module imports and loads cleanly.

* **Fix "未生成有效推荐数据" Data Pipeline Filter Bug (`db.py` & `dynamic_factor.py`)**:
  * **Root Cause 1 (Min Rows Filter)**: In [`MagicSTG/core/db.py`](file:///E:/ashare/MagicSTG/core/db.py#L244), `load_all_data_db` had `if len(group) < 200: continue`. When calculating daily recommendations, the strategy runner requests a 90-day window (~60 trading days per stock). Because 60 < 200, `load_all_data_db` filtered out 100% of all stocks, resulting in `0 stocks data` and empty recommendations.
  * **Root Cause 2 (Tech Filter Defaults)**: `DynamicFactorStrategy` defaulted `enable_golden_cross` / `enable_volume_surge` to `True` even for fundamental-only strategies without technical configs, forcing single-day cross requirements.
  * **Fix**: Lowered row threshold in `load_all_data_db` from `200` to `20` (requiring only ~1 month for MA20), and made technical filter defaults (`enable_golden_cross`, etc.) default to `False` unless technical configs are present in strategy factors. Committed and pushed to `main` branch ([`b8adc58`](https://github.com/wxsw3000/ashare/commit/b8adc58)).

---

## 13. Session Summary & Memory (2026-08-06) - Strategy Management UI/UX Redesign

* **Auto-generated Unique Strategy ID (`strategy_id`)**:
  * Auto-generates format `stg_<timestamp>` (e.g. `stg_1722934823`) during strategy creation in both frontend [`index.html`](file:///E:/ashare/MagicSTG/web/templates/index.html) and backend [`server.py`](file:///E:/ashare/MagicSTG/web/server.py). Set `strategy_id` input field to read-only mode so users never need to manually invent or type IDs.

* **Responsive Strategy Card Grid & Detail Modal Architecture**:
  * Replaced strategy table in Strategy Management tab with responsive 3-column `.stg-card-grid` (adapts cleanly to 1 column on mobile devices).
  * Removed direct "运行" (Run) and "回测" (Backtest) action buttons from outer strategy list. Action execution now strictly requires entering the **Strategy Detail Modal** (`#strategyDetailModal`).

* **Single Task Concurrency Lock & Unified Terminal Console Modal**:
  * Implemented global execution lock `globalTaskRunning`. Attempts to start multiple concurrent strategy runs/backtests are guarded with a warning alert.
  * Unified Strategy Run and Backtest execution into `#executionLogModal`. Shows terminal-style real-time logs, date controls, and quick navigation buttons to view generated recommendation results or backtest equity charts.
  * **Auto-fill Latest Available Trade Date**: Added `/api/latest-date` endpoint in [`server.py`](file:///E:/ashare/MagicSTG/web/server.py) and frontend auto-fill in [`index.html`](file:///E:/ashare/MagicSTG/web/templates/index.html) so the execution modal automatically defaults to the latest trade date with market data in TiDB Cloud (preventing 0-record runs caused by running today's date before 18:31 CST post-market data sync). Committed and pushed to `main` ([`4a516de`](https://github.com/wxsw3000/ashare/commit/4a516de)).
  * **Performance Fix (All-Stock Universe Timeout)**: In [`MagicSTG/core/db.py`](file:///E:/ashare/MagicSTG/core/db.py), `load_all_data_db` was executing 53 sequential batch SQL queries when `stock_scope = 'all'` (~35-40s), exceeding Render's 30s reverse proxy timeout and returning 504 HTML error pages (`Unexpected token '<'`). Optimized `load_all_data_db` to execute a single vector query (`WHERE date >= %s AND date <= %s`), bringing query time down to ~12s. Committed and pushed to `main` ([`15b13ba`](https://github.com/wxsw3000/ashare/commit/15b13ba)).

---

## 14. Session Summary & Memory (2026-08-06) - Fix GitHub Actions Workflow `NameError: name 'List' is not defined`

* **Issue**: GitHub Actions `update_ashare_data.yml` failed during `python -m MagicSTG.data.runner` with:
  `NameError: name 'List' is not defined. Did you mean: 'list'?` in [`MagicSTG/core/db.py`](file:///E:/ashare/MagicSTG/core/db.py#L60).
* **Root Cause**: `List` type annotation was used in [`MagicSTG/core/db.py`](file:///E:/ashare/MagicSTG/core/db.py) and [`MagicSTG/strategies/registry.py`](file:///E:/ashare/MagicSTG/strategies/registry.py) without importing `List` from `typing`.
* **Fix**: Added `List` to `from typing import ...` in both [`MagicSTG/core/db.py`](file:///E:/ashare/MagicSTG/core/db.py) and [`MagicSTG/strategies/registry.py`](file:///E:/ashare/MagicSTG/strategies/registry.py). Ran AST scanner across the entire codebase to confirm zero remaining missing typing imports. Tested `python -m MagicSTG.data.runner --help` successfully.

---

## 15. Session Summary & Memory (2026-08-06) - Backtest Records UI Redesign, Sharpe Ratio & Deletion API

* **Database Capabilities & Cascading Backtest Deletion**:
  * Verified database schema: `backtest_results` has primary key `id`, and `backtest_trades` links to it via `backtest_id`.
  * Added `DELETE /api/backtests/<int:backtest_id>` REST endpoint in [`MagicSTG/web/server.py`](file:///E:/ashare/MagicSTG/web/server.py). Automatically deletes all corresponding records from both `backtest_trades` and `backtest_results` in TiDB Cloud.
  * Added `sharpe_ratio` column auto-migration to `backtest_results` in [`MagicSTG/core/db_writer.py`](file:///E:/ashare/MagicSTG/core/db_writer.py) and [`MagicSTG/web/server.py`](file:///E:/ashare/MagicSTG/web/server.py).

* **Sharpe Ratio Calculation**:
  * Updated [`MagicSTG/backtests/engine.py`](file:///E:/ashare/MagicSTG/backtests/engine.py) (`run_backtest_engine`) to calculate the annualized Sharpe Ratio for both stock and convertible bond strategies (assuming 2.0% risk-free rate).
  * Exposed `sharpe_ratio` field in `/api/backtests` GET response.

* **Backtest Records Card Layout & Modal Sub-Window Redesign**:
  * Redesigned `#sectionBacktests` in [`index.html`](file:///E:/ashare/MagicSTG/web/templates/index.html) from table layout to a responsive card grid (`#backtestCardGrid`).
  * Displayed **Strategy Name** (`strategy_name`) as the primary card title and Strategy ID as a secondary badge tag.
  * Embedded **Sharpe Ratio** (夏普比率) as a core metric alongside total return, annual return, max drawdown, and win rate.
  * Implemented 1-click **删除 (Delete)** button on each card with interactive confirmation modal.
  * Replaced bottom inline detail view with a dedicated **回测详细报告子界面弹窗 (`#backtestDetailModal`)**, displaying metrics summary and historical trade execution logs in a clean modal overlay.

---

## 16. Session Summary & Memory (2026-08-06) - Strategy Recommendation Results Deletion Feature

* **Deletion REST API**:
  * Added `DELETE /api/recommendations` endpoint in [`MagicSTG/web/server.py`](file:///E:/ashare/MagicSTG/web/server.py).
  * Accepts `strategy` and `date` parameters, executing `DELETE FROM recommendations WHERE strategy = %s AND signal_date = %s` in TiDB Cloud.

* **UI Integration (`index.html`)**:
  * Added **删除 (Delete)** button on each 策略执行历史履历卡片 in `#recRunsMasterContainer` (`deleteRecommendationRunAction` with `event.stopPropagation()`).
  * Added **删除该日推荐记录** button in `#recRunDetailContainer` header bar (`deleteCurrentRecRunFromDetail`).
  * Triggers 1-click confirmation alert and updates Web UI dynamically upon successful deletion.

---

## 17. Session Summary & Memory (2026-08-06) - Fix RAM Leak in Legacy & Dynamic Strategies Indicator Caching

* **Root Cause Analysis (Render OOM on `price_strategy`)**:
  * Legacy strategies (`price_strategy`, `pe_strategy`, `roe_strategy`) and `DynamicFactorStrategy` stored computed `df.copy()` objects indefinitely inside `self._indicator_cache` indexed by `id(df)`.
  * When executing strategies with `stock_scope: 'all'` (scanning 5,300 A-share stocks), 5,300 full DataFrames were permanently pinned in Python instance memory.
  * In `runner.py`, `stock_data` and strategy instances were not freed after recommendation signal generation, causing Render's 512MB RAM worker limit to be breached.

* **Fixes Implemented**:
  * Added `clear_cache()` to [`MagicSTG/strategies/dynamic_factor.py`](file:///E:/ashare/MagicSTG/strategies/dynamic_factor.py), [`price_strategy.py`](file:///E:/ashare/MagicSTG/strategies/legacy/price_strategy.py), [`pe_strategy.py`](file:///E:/ashare/MagicSTG/strategies/legacy/pe_strategy.py), and [`roe_strategy.py`](file:///E:/ashare/MagicSTG/strategies/legacy/roe_strategy.py).
  * Wrapped `get_signals` processing loops in `try-finally` blocks to guarantee `self.clear_cache()` and `gc.collect()` run immediately after signal generation.
  * Added explicit `strategy.clear_cache()`, `del stock_data`, and `gc.collect()` in `run_dynamic_factor_recommendation` in [`MagicSTG/strategies/runner.py`](file:///E:/ashare/MagicSTG/strategies/runner.py) and Flask `run_strategy` route in [`MagicSTG/web/server.py`](file:///E:/ashare/MagicSTG/web/server.py).
