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

## 10. Strategy Signal Refinement & Top N Quota Enforcement (2026-08-02)

* **Top N Recommendation Limit Enforcement**:
  * Refactored legacy strategy generators ([`pe_strategy.py`](file:///E:/ashare/MagicSTG/strategies/legacy/pe_strategy.py), [`roe_strategy.py`](file:///E:/ashare/MagicSTG/strategies/legacy/roe_strategy.py), [`price_strategy.py`](file:///E:/ashare/MagicSTG/strategies/legacy/price_strategy.py)) to enforce strict `top_n` quotas (default Top 10).
  * `PE`: Sorts buy candidates ascending by PE (cheapest first) and returns Top 10.
  * `ROE`: Sorts buy candidates descending by ROE (highest quality first) and returns Top 10.
  * `Price`: Sorts buy candidates ascending by price and returns Top 10.
  * Guarantees that traders receive high-conviction, actionable recommendations (max 10 signals per strategy) rather than noisy unconstrained list dumps.




