# MagicSTG Quantitative System - Project Memory & Handoff
*Last Updated: 2026-07-16 23:53 (Local Time)*

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

## 5. Next Steps
* Integrate automated notifications (e.g., Telegram/WeChat webhook) when daily runners generate a `BUY`/`SELL` recommendation.
* Support custom portfolio weight rebalancing in backtest configuration.
