# MagicSTG Quantitative System - Project Memory & Handoff
*Last Updated: 2026-07-31 17:33 (Local Time)*

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
  * `db.py`: Primary database connection, heartbeat pinging, data loaders (`load_all_data_db`, `load_roe_data_db`), and strategy checkpoints.
  * `db_writer.py`: Persistence engine for recommendations, positions, and backtest results into TiDB Cloud.
  * `cost.py` & `limit.py`: Transaction fee calculations and price limit-up/down checking.
* **`MagicSTG/strategies/`**:
  * `base.py`: Abstract `BaseStrategy` base class defining strategy contract (`generate_signals`).
  * `dynamic_factor.py`: Plugin-based multi-factor dynamic strategy implementation (`DynamicFactorStrategy`).
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

## 6. Convertible Bond Data & Next Steps (2026-07-31)
* **Completed Progress Today (2026-07-31)**:
  * **Database Architecture**: Created `cb_basic`, `cb_kline_day`, and `cb_daily_indicator` in TiDB Cloud (`init_cb_tables.py`).
  * **Code Format Unification**: Fully migrated all convertible bond tables (`cb_basic`, `cb_kline_day`, `cb_daily_indicator`) to Baostock standard format (`sh.113008` / `sh.600000`), enabling native zero-overhead indexed SQL joins with `stock_kline_day`.
  * **Master Info (`cb_basic`)**: Fully loaded 1,044 convertible bonds (1,016 active records updated to `sh.113008`). Auto-filled转股价 for 702 historical/delisted bonds via AKShare detail API (`update_cb_basic.py`).
  * **Daily K-Lines (`cb_kline_day`)**: Fully loaded and updated 662,130 daily K-line records spanning 2020-01-01 to present in `sh.113008` format (`update_cb_kline_day.py`).
  * **Daily Metrics (`cb_daily_indicator`)**: Refactored `update_cb_daily_indicator.py` with SQL index optimization (`st.code = b.stock_code AND cb.date = st.date`). Fully calculated and updated **656,354** records spanning **2020-01-02 to 2026-07-30** (100% completed).
  * **Daily Automation Pipeline**: Registered `update_cb_basic.py`, `update_cb_kline_day.py`, and `update_cb_daily_indicator.py` into `MagicSTG/data/runner.py` under `daily` group for automated daily post-market execution.
  * **Directory Execution Support**: Added dynamic project root resolution in all bond scripts to support direct execution from any subdirectory (`MagicSTG/data/bond/` or `E:\ashare`).
  * **Time Cutoff Alignment**: Aligned exact date boundary to **18:30:00 (Beijing Time)** across `runner.py` and `utils.py`.
* **Next Steps**:
  * Implement Convertible Bond Strategy (e.g. Double-Low 双低策略, Low-Premium Rate 策略).
  * Integrate Convertible Bond backtesting into `MagicSTG/backtests/engine.py` and Web Dashboard.

