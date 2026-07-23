# MagicSTG Quantitative System - Project Memory & Handoff
*Last Updated: 2026-07-23 23:48 (Local Time)*

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
* **`stock_kline_day`**: Daily K-line table. Columns: `code`, `date`, `open`, `high`, `low`, `close`, `volume`, `peTTM` (used for PE strategy calculations).
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

---

## 3. Key Files & Roles
* **`MagicSTG/web/server.py`**:
  * Flask routing and REST API server.
  * Endpoint `/api/recommendations`: Queries recommendations and dates.
  * Endpoint `/api/backtest`: Executes the dynamic multi-factor backtest loop in memory using `InMemoryPositionManager` (overrides file load to avoid Unicode errors and file conflicts).
* **`MagicSTG/web/templates/index.html`**:
  * Dual-tab UI (Tab 1: Real-time Recommendations, Tab 2: Custom Backtester).
  * Contains a client-side signal filter dropdown (All, Buy-only, Sell-only) and dynamic stats indicator.
  * Renders assets curve using Chart.js.
* **`MagicSTG/signals/generator_dynamic.py`**:
  * Implements `SignalGeneratorDynamic` class. Calculates MAs, Volume Surge, and DMI ratios on the fly, and merges them with quarterly fundamental reports (ROE, Growth, Debt, CFO quality) aligned to `pub_date` for bias-free backtesting.
* **`MagicSTG/utils/db.py`**:
  * Establishes SSL connection to TiDB via `isrgrootx1.pem` certificate.
  * Implements `load_all_data_db` (supports optimized `ORDER BY date ASC` SQL sorting to prevent Pandas memory sorting overhead).
  * Implements database-backed checkpoints (`get_last_check_date_db`, `save_checkpoint_db`).
* **`MagicSTG/utils/db_writer.py`**:
  * Handles writing daily signals into database. Modified to use `INSERT IGNORE` to bypass duplicate entries when multiple signals are registered on the same day.
* **`run_daily_price.py`, `run_daily_pe.py`, `run_daily_roe.py`**:
  * Standard strategy runners. Fully migrated to database-backed checkpoints and SSL-based TiDB writing.

---

## 4. Production Deployment Notes (Render)
1. **SSL Certificate**: The SSL certificate `MagicSTG/dbconfig/isrgrootx1.pem` must be pushed to Git. `utils/db.py` reads it using relative pathing from `PROJECT_ROOT`.
2. **Start Command**: Must increase Gunicorn timeout to prevent worker kills during 2.5-year daily range queries.
   * Start Command: `gunicorn --timeout 120 MagicSTG.web.server:app`
3. **Environment Variables on Render**: Ensure `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_PORT`, `DB_SSL_CA` are configured in Render dashboard.

---

## 5. 每日数据更新逻辑与数据库状态跟踪机制

### 5.1 自动化更新架构与调度策略
* **最佳触发时间**：推荐每日 **18:31（北京时间）** 触发。对应 GitHub Actions Cron 表达式为 `31 10 * * *` (UTC)。
  * **依据**：Baostock 每日盘后于 17:30 开始清洗推送，到 18:00 ~ 18:30 全市场 5000+ 股票的日K线、前复权价格及市盈率等指标 100% 准备就绪。18:31 运行能保证数据的绝对完整与稳定。
* **目标交易日定位 (`get_target_date`)**：
  * 使用时区感知的 `datetime.now(timezone(timedelta(hours=8)))`，排除所有宿主环境（Windows/Linux/Docker）的时区计算偏差。
  * **北京时间 < 18:00** 运行：`target_date` 自动定位为 **昨天**（上一交易日）。
  * **北京时间 ≥ 18:00** 运行：`target_date` 自动定位为 **当天**（当天收盘日）。

### 5.2 任务归档与断点续传机制 (`task_date`)
* **核心归档原则**：主控脚本 `run_all_updates.py` 的任务归档日期 `task_date` **严格绑定 `get_target_date()`**（即行情目标日），而非启动脚本时的挂钟日历时间。
* **跨日/凌晨运行无冲突保障**：
  * 若在次日凌晨（如 24 日 01:00）手动运行，`get_target_date()` 判定当前属于 18:00 前，归档 `task_date` 为 **23 日**，更新并标记 23 日完成。
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
1. **数据库连接心跳 (`conn.ping(reconnect=True)`)**：
   在所有更新脚本（日K、周K、月K）的游标操作与缓冲池刷新 (`flush_db_buffer`) 前加入心跳自动重连，彻底避免因 Baostock 网络拉取耗时引发的 TiDB 空闲连接断开错误 `(0, '')`。
2. **月K线/周K线未完结快照自动覆盖**：
   对于月K线，增量起点自动对齐至 `last_date` 所在月份 1 号，并在写入前自动清理该月份已有记录。无论月中何时运行，月末/下月更新时均能无缝清理旧月中快照（如 7-22），替换为完整的月末完结K线（如 7-31），杜绝主键重复与数据两份残留。

### 5.5 GitHub Actions 手动控制扩展
通过 `.github/workflows/update_ashare_data.yml` 中的 `workflow_dispatch` 暴露了两个手动重置/强制开关：
* **`force`** (boolean): 忽略数据库中的 `success` 状态标记，强行重新拉取并重新执行所有脚本。
* **`reset`** (boolean): 重置当天的 `update_progress` 进度状态。

---

## 6. Next Steps
* Integrate automated notifications (e.g., Telegram/WeChat webhook) when daily runners generate a `BUY`/`SELL` recommendation.
* Support custom portfolio weight rebalancing in backtest configuration.
