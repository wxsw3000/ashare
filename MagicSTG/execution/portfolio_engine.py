# -*- coding: utf-8 -*-
"""
MagicSTG Task-Oriented Portfolio & Paper Trading Engine
Handles user-created paper trading tasks (portfolio_tasks), simulated portfolio management,
order execution, friction costs, stop-loss/profit rules, trade log histories,
performance metrics (Cum Return, Annual Return, Sharpe Ratio, Max Drawdown, Win Rate),
and cascading deletion into TiDB Cloud.
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import json
import math
import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional, Any, Tuple

from MagicSTG.db import (
    get_connection_with_retry,
    ensure_connection_alive,
    safe_float,
    safe_int,
    safe_str
)
from MagicSTG.core.cost import calc_buy_cost, calc_sell_cost, calc_net_sell
from MagicSTG.execution.portfolio_strategies import PortfolioStrategyRegistry, is_convertible_bond, calc_trade_cost_for_security


def init_portfolio_db():
    """
    Ensures that portfolio_tasks, portfolio_daily_history, positions, and portfolio_trade_logs tables exist
    with correct schemas and columns in TiDB Cloud.
    """
    conn = get_connection_with_retry()
    try:
        with conn.cursor() as cursor:
            # 1. Create portfolio_tasks table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_tasks (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    task_id VARCHAR(50) UNIQUE NOT NULL,
                    task_name VARCHAR(100) NOT NULL,
                    strategy_code VARCHAR(50) NOT NULL,
                    initial_capital DECIMAL(20, 4) NOT NULL DEFAULT 100000.0,
                    current_equity DECIMAL(20, 4) NOT NULL DEFAULT 100000.0,
                    cash DECIMAL(20, 4) NOT NULL DEFAULT 100000.0,
                    market_value DECIMAL(20, 4) NOT NULL DEFAULT 0.0,
                    cum_pnl DECIMAL(20, 4) NOT NULL DEFAULT 0.0,
                    cum_return DECIMAL(12, 6) NOT NULL DEFAULT 0.0,
                    annual_return DECIMAL(12, 6) NOT NULL DEFAULT 0.0,
                    sharpe_ratio DECIMAL(12, 6) NOT NULL DEFAULT 0.0,
                    max_drawdown DECIMAL(12, 6) NOT NULL DEFAULT 0.0,
                    win_rate DECIMAL(12, 6) NOT NULL DEFAULT 0.0,
                    holding_count INT NOT NULL DEFAULT 0,
                    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
                    portfolio_config JSON DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_status (status),
                    INDEX idx_strategy (strategy_code)
                )
            """)

            # Add start_date column to portfolio_tasks if missing
            cursor.execute("DESCRIBE portfolio_tasks")
            pt_cols = [r[0] for r in cursor.fetchall()]
            if 'start_date' not in pt_cols:
                cursor.execute("ALTER TABLE portfolio_tasks ADD COLUMN start_date VARCHAR(20) DEFAULT NULL AFTER status")

            # 2. Check and add columns to positions table
            cursor.execute("DESCRIBE positions")
            cols = [r[0] for r in cursor.fetchall()]
            
            if 'stock_name' not in cols:
                cursor.execute("ALTER TABLE positions ADD COLUMN stock_name VARCHAR(100) DEFAULT NULL AFTER stock_code")
            if 'highest_price' not in cols:
                cursor.execute("ALTER TABLE positions ADD COLUMN highest_price DECIMAL(12,4) DEFAULT NULL AFTER buy_price")
            if 'slot_idx' not in cols:
                cursor.execute("ALTER TABLE positions ADD COLUMN slot_idx INT DEFAULT -1 AFTER highest_price")
            if 'task_id' not in cols:
                cursor.execute("ALTER TABLE positions ADD COLUMN task_id VARCHAR(50) DEFAULT NULL AFTER strategy")
                cursor.execute("ALTER TABLE positions ADD INDEX idx_task_id (task_id)")

            # 3. Check and add columns to portfolio_daily_history table
            cursor.execute("DESCRIBE portfolio_daily_history")
            cols_hist = [r[0] for r in cursor.fetchall()]
            if 'task_id' not in cols_hist:
                cursor.execute("ALTER TABLE portfolio_daily_history ADD COLUMN task_id VARCHAR(50) DEFAULT NULL AFTER strategy")
                cursor.execute("ALTER TABLE portfolio_daily_history ADD INDEX idx_task_date (task_id, date)")

            # Drop old uk_strategy_date unique index if present and create uk_task_date
            try:
                cursor.execute("ALTER TABLE portfolio_daily_history DROP INDEX uk_strategy_date")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE portfolio_daily_history ADD UNIQUE KEY uk_task_date (task_id, date)")
            except Exception:
                pass

            # 4. Create portfolio_trade_logs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_trade_logs (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    task_id VARCHAR(50) NOT NULL,
                    strategy VARCHAR(50) NOT NULL,
                    trade_date DATE NOT NULL,
                    action VARCHAR(10) NOT NULL,
                    stock_code VARCHAR(30) NOT NULL,
                    stock_name VARCHAR(100),
                    price DECIMAL(12, 4) NOT NULL,
                    shares INT NOT NULL,
                    cost_total DECIMAL(20, 4) NOT NULL,
                    pnl DECIMAL(20, 4) DEFAULT 0.0,
                    pnl_pct DECIMAL(12, 6) DEFAULT 0.0,
                    reason VARCHAR(255),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_task_date (task_id, trade_date)
                )
            """)

        conn.commit()
    except Exception as e:
        print(f"[PortfolioEngine] DB Schema init notice: {e}")
    finally:
        conn.close()


def calculate_performance_metrics(
    daily_history: List[dict],
    closed_trades: List[dict],
    initial_capital: float = 100000.0,
    start_date_str: Optional[str] = None,
    end_date_str: Optional[str] = None
) -> dict:
    """
    Calculates quantitative performance indicators:
    - Cumulative Return (%)
    - Annualized Return (%) (Using full calendar duration including non-trading days/holidays)
    - Sharpe Ratio
    - Max Drawdown (%)
    - Win Rate (%)
    """
    if not daily_history:
        return {
            'cum_return': 0.0,
            'annual_return': 0.0,
            'sharpe_ratio': 0.0,
            'max_drawdown': 0.0,
            'win_rate': 0.0
        }

    latest_equity = float(daily_history[-1].get('total_equity') if daily_history[-1].get('total_equity') is not None else daily_history[-1].get('equity', 0.0))
    cum_pnl = latest_equity - initial_capital
    cum_return = (cum_pnl / initial_capital) * 100.0 if initial_capital > 0 else 0.0

    # 1. Annualized Return (Strict 365 calendar days compound rate)
    n_days = len(daily_history)
    if n_days > 1:
        try:
            d0_str = start_date_str or daily_history[0]['date']
            d1_str = end_date_str or daily_history[-1]['date']
            d0 = datetime.strptime(str(d0_str)[:10], '%Y-%m-%d')
            d1 = datetime.strptime(str(d1_str)[:10], '%Y-%m-%d')
            cal_days = (d1 - d0).days
            # Only compute compound annualized return if running duration >= 7 calendar days to prevent short-term noise distortion
            if cal_days >= 7 and latest_equity > 0:
                annual_return = (((latest_equity / initial_capital) ** (365.0 / cal_days)) - 1.0) * 100.0
            else:
                annual_return = cum_return
        except Exception:
            annual_return = cum_return
    else:
        annual_return = cum_return

    # 2. Max Drawdown
    def _get_eq(item_dict):
        v = item_dict.get('total_equity')
        return float(v) if v is not None else float(item_dict.get('equity', 0.0))

    peak = initial_capital
    max_dd = 0.0
    for h in daily_history:
        eq = _get_eq(h)
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak * 100.0
            if dd > max_dd:
                max_dd = dd

    # 3. Sharpe Ratio (annualized, 1.5% risk free rate)
    sharpe_ratio = 0.0
    daily_returns = []
    for i in range(1, len(daily_history)):
        prev_eq = _get_eq(daily_history[i-1])
        curr_eq = _get_eq(daily_history[i])
        if prev_eq > 0:
            daily_returns.append((curr_eq - prev_eq) / prev_eq)

    if len(daily_returns) > 1:
        mean_ret = sum(daily_returns) / len(daily_returns)
        rf_daily = 0.015 / 252.0
        variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        std_ret = math.sqrt(variance)
        if std_ret > 1e-7:
            sharpe_ratio = (mean_ret - rf_daily) / std_ret * math.sqrt(252.0)

    # 4. Win Rate (returns None when no trades have been closed yet)
    win_rate = None
    if closed_trades:
        win_count = sum(1 for t in closed_trades if (t.get('pnl') or 0) > 0)
        win_rate = round((win_count / len(closed_trades)) * 100.0, 4)

    return {
        'cum_return': round(cum_return, 4),
        'annual_return': round(annual_return, 4),
        'sharpe_ratio': round(sharpe_ratio, 4),
        'max_drawdown': round(max_dd, 4),
        'win_rate': win_rate
    }


class PortfolioEngine:
    """
    Task-Oriented Simulated Portfolio & Paper Trading Engine.
    Manages user-created paper trading tasks (portfolio_tasks), runs daily executions,
    calculates performance metrics, and supports cascading task deletion.
    """

    def __init__(self, strategy_id: Optional[str] = None, config: Optional[dict] = None):
        self.strategy_id = strategy_id or 'cb_double_low'
        self.config = config or {}
        init_portfolio_db()

    @staticmethod
    def create_task(task_name: str, strategy_code: str, initial_capital: float = 100000.0, portfolio_config: Optional[dict] = None, start_date: Optional[str] = None) -> dict:
        """
        Creates a new portfolio trading instance starting from start_date.
        Inherits default portfolio_config from strategy if not provided.
        """
        init_portfolio_db()
        if not start_date:
            start_date = datetime.now().strftime('%Y-%m-%d')

        task_id = f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{strategy_code}"
        conn = get_connection_with_retry()
        try:
            # Inherit default portfolio_config from strategy if not explicitly passed
            effective_cfg = portfolio_config or {}
            with conn.cursor() as cur:
                cur.execute("SELECT factors_config FROM custom_strategies WHERE strategy_id = %s", (strategy_code,))
                row = cur.fetchone()
                if row and row[0]:
                    f_cfg = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                    if isinstance(f_cfg, dict) and 'portfolio_config' in f_cfg:
                        default_p_cfg = f_cfg['portfolio_config']
                        if isinstance(default_p_cfg, dict):
                            merged = default_p_cfg.copy()
                            merged.update(effective_cfg)
                            effective_cfg = merged

            cfg_json = json.dumps(effective_cfg) if effective_cfg else None

            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO portfolio_tasks
                    (task_id, task_name, strategy_code, initial_capital, current_equity, cash, market_value, status, start_date, portfolio_config)
                    VALUES (%s, %s, %s, %s, %s, %s, 0.0, 'ACTIVE', %s, %s)
                """, (task_id, task_name, strategy_code, initial_capital, initial_capital, initial_capital, start_date, cfg_json))
            conn.commit()

            # Sync initial run for task
            engine = PortfolioEngine(strategy_id=strategy_code, config=effective_cfg)
            engine.sync_task(task_id)

            return PortfolioEngine.get_task_detail(task_id)
        finally:
            conn.close()

    @staticmethod
    def delete_task(task_id: str) -> bool:
        """
        Cascading delete for a portfolio task: deletes positions, portfolio_daily_history, portfolio_trade_logs, and portfolio_tasks record.
        """
        conn = get_connection_with_retry()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM positions WHERE task_id = %s", (task_id,))
                cur.execute("DELETE FROM portfolio_daily_history WHERE task_id = %s", (task_id,))
                cur.execute("DELETE FROM portfolio_trade_logs WHERE task_id = %s", (task_id,))
                cur.execute("DELETE FROM portfolio_tasks WHERE task_id = %s", (task_id,))
            conn.commit()
            print(f"[PortfolioEngine] ✅ Cascading deleted portfolio task: {task_id}")
            return True
        except Exception as e:
            if conn:
                conn.rollback()
            print(f"[PortfolioEngine] ❌ Error deleting task {task_id}: {e}")
            raise
        finally:
            conn.close()

    @staticmethod
    def list_tasks() -> List[dict]:
        """
        Lists all active portfolio tasks with strategy names and performance summary.
        """
        init_portfolio_db()
        conn = get_connection_with_retry()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT t.id, t.task_id, t.task_name, t.strategy_code, COALESCE(s.name, t.strategy_code) AS strategy_name,
                           t.initial_capital, t.current_equity, t.cash, t.market_value, t.cum_pnl, t.cum_return,
                           t.annual_return, t.sharpe_ratio, t.max_drawdown, t.win_rate, t.holding_count, t.status,
                           t.portfolio_config, t.created_at, t.updated_at, t.start_date
                    FROM portfolio_tasks t
                    LEFT JOIN custom_strategies s ON (t.strategy_code = s.strategy_id OR t.strategy_code = CAST(s.id AS CHAR))
                    WHERE t.status != 'DELETED'
                    ORDER BY t.id DESC
                """)
                rows = cur.fetchall()

            tasks = []
            for r in rows:
                p_cfg = r[17]
                if isinstance(p_cfg, str):
                    try:
                        p_cfg = json.loads(p_cfg) if p_cfg else {}
                    except Exception:
                        p_cfg = {}
                elif not isinstance(p_cfg, dict):
                    p_cfg = {}

                tasks.append({
                    'id': r[0],
                    'task_id': r[1],
                    'task_name': r[2],
                    'strategy_code': r[3],
                    'strategy_name': r[4],
                    'initial_capital': float(r[5]) if r[5] is not None else 0.0,
                    'current_equity': float(r[6]) if r[6] is not None else 0.0,
                    'cash': float(r[7]) if r[7] is not None else 0.0,
                    'market_value': float(r[8]) if r[8] is not None else 0.0,
                    'cum_pnl': float(r[9]) if r[9] is not None else 0.0,
                    'cum_return': float(r[10]) if r[10] is not None else 0.0,
                    'annual_return': float(r[11]) if r[11] is not None else 0.0,
                    'sharpe_ratio': float(r[12]) if r[12] is not None else 0.0,
                    'max_drawdown': float(r[13]) if r[13] is not None else 0.0,
                    'win_rate': float(r[14]) if r[14] is not None else None,
                    'holding_count': int(r[15]) if r[15] is not None else 0,
                    'status': r[16],
                    'portfolio_config': p_cfg,
                    'created_at': r[18].strftime('%Y-%m-%d %H:%M') if hasattr(r[18], 'strftime') else str(r[18] or ''),
                    'updated_at': r[19].strftime('%Y-%m-%d %H:%M') if hasattr(r[19], 'strftime') else str(r[19] or ''),
                    'start_date': str(r[20]) if r[20] else None
                })
            return tasks
        finally:
            conn.close()

    @staticmethod
    def get_task_detail(task_id: str) -> dict:
        """
        Retrieves complete task performance, active positions, trade logs, and daily equity curve.
        """
        conn = get_connection_with_retry()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT t.task_id, t.task_name, t.strategy_code, COALESCE(s.name, t.strategy_code) AS strategy_name,
                           t.initial_capital, t.current_equity, t.cash, t.market_value, t.cum_pnl, t.cum_return,
                           t.annual_return, t.sharpe_ratio, t.max_drawdown, t.win_rate, t.holding_count, t.status,
                           t.portfolio_config, t.created_at, t.updated_at, t.start_date
                    FROM portfolio_tasks t
                    LEFT JOIN custom_strategies s ON (t.strategy_code = s.strategy_id OR t.strategy_code = CAST(s.id AS CHAR))
                    WHERE t.task_id = %s
                """, (task_id,))
                row = cur.fetchone()

                if not row:
                    return {'status': 'error', 'message': f'Task not found: {task_id}'}

                task_summary = {
                    'task_id': row[0],
                    'task_name': row[1],
                    'strategy_code': row[2],
                    'strategy_name': row[3],
                    'initial_capital': float(row[4]),
                    'current_equity': float(row[5]),
                    'cash': float(row[6]),
                    'market_value': float(row[7]),
                    'cum_pnl': float(row[8]),
                    'cum_return': float(row[9]),
                    'annual_return': float(row[10]),
                    'sharpe_ratio': float(row[11]),
                    'max_drawdown': float(row[12]),
                    'win_rate': float(row[13]),
                    'holding_count': int(row[14]),
                    'status': row[15],
                    'portfolio_config': json.loads(row[16]) if row[16] else {},
                    'created_at': row[17].strftime('%Y-%m-%d %H:%M') if hasattr(row[17], 'strftime') else str(row[17]),
                    'updated_at': row[18].strftime('%Y-%m-%d %H:%M') if hasattr(row[18], 'strftime') else str(row[18]),
                    'start_date': str(row[19]) if row[19] else None
                }

                # Active positions
                cur.execute("""
                    SELECT stock_code, stock_name, buy_date, buy_price, highest_price, shares, cost_total, current_price, market_value, pnl, pnl_pct, slot_idx, extra_data
                    FROM positions
                    WHERE task_id = %s AND status = 'HOLDING'
                    ORDER BY buy_date DESC, id DESC
                """, (task_id,))
                active_positions = []
                for r in cur.fetchall():
                    active_positions.append({
                        'stock_code': r[0],
                        'stock_name': r[1] or r[0],
                        'buy_date': r[2].strftime('%Y-%m-%d') if hasattr(r[2], 'strftime') else str(r[2]),
                        'buy_price': float(r[3]) if r[3] else 0.0,
                        'highest_price': float(r[4]) if r[4] else float(r[3] or 0.0),
                        'shares': int(r[5]),
                        'cost_total': float(r[6]) if r[6] else 0.0,
                        'current_price': float(r[7]) if r[7] else 0.0,
                        'market_value': float(r[8]) if r[8] else 0.0,
                        'pnl': float(r[9]) if r[9] else 0.0,
                        'pnl_pct': float(r[10]) if r[10] else 0.0,
                        'slot_idx': int(r[11]) if r[11] is not None else -1,
                        'extra_data': json.loads(r[12]) if r[12] else {}
                    })

                # Closed trades
                cur.execute("""
                    SELECT stock_code, stock_name, buy_date, buy_price, sell_date, sell_price, shares, cost_total, pnl, pnl_pct, extra_data
                    FROM positions
                    WHERE task_id = %s AND status = 'SOLD'
                    ORDER BY sell_date DESC, id DESC
                """, (task_id,))
                closed_trades = []
                for r in cur.fetchall():
                    closed_trades.append({
                        'stock_code': r[0],
                        'stock_name': r[1] or r[0],
                        'buy_date': r[2].strftime('%Y-%m-%d') if hasattr(r[2], 'strftime') else str(r[2]),
                        'buy_price': float(r[3]) if r[3] else 0.0,
                        'sell_date': r[4].strftime('%Y-%m-%d') if hasattr(r[4], 'strftime') else str(r[4]),
                        'sell_price': float(r[5]) if r[5] else 0.0,
                        'shares': int(r[6]),
                        'cost_total': float(r[7]) if r[7] else 0.0,
                        'pnl': float(r[8]) if r[8] else 0.0,
                        'pnl_pct': float(r[9]) if r[9] else 0.0,
                        'extra_data': json.loads(r[10]) if r[10] else {}
                    })

                # Trade execution logs
                cur.execute("""
                    SELECT trade_date, action, stock_code, stock_name, price, shares, cost_total, pnl, pnl_pct, reason
                    FROM portfolio_trade_logs
                    WHERE task_id = %s
                    ORDER BY trade_date DESC, id DESC
                    LIMIT 200
                """, (task_id,))
                trade_logs = []
                for r in cur.fetchall():
                    trade_logs.append({
                        'trade_date': r[0].strftime('%Y-%m-%d') if hasattr(r[0], 'strftime') else str(r[0]),
                        'action': r[1],
                        'stock_code': r[2],
                        'stock_name': r[3] or r[2],
                        'price': float(r[4]) if r[4] else 0.0,
                        'shares': int(r[5]) if r[5] else 0,
                        'cost_total': float(r[6]) if r[6] else 0.0,
                        'pnl': float(r[7]) if r[7] is not None else 0.0,
                        'pnl_pct': float(r[8]) if r[8] is not None else 0.0,
                        'reason': r[9] or ''
                    })

                # Equity curve history
                cur.execute("""
                    SELECT date, total_equity, cash, market_value, daily_pnl, cum_pnl, cum_return, holding_count
                    FROM portfolio_daily_history
                    WHERE task_id = %s
                    ORDER BY date ASC
                """, (task_id,))
                equity_curve = []
                for r in cur.fetchall():
                    equity_curve.append({
                        'date': r[0].strftime('%Y-%m-%d') if hasattr(r[0], 'strftime') else str(r[0]),
                        'total_equity': float(r[1]),
                        'cash': float(r[2]),
                        'market_value': float(r[3]),
                        'daily_pnl': float(r[4]),
                        'cum_pnl': float(r[5]),
                        'cum_return': float(r[6]),
                        'holding_count': int(r[7])
                    })

                if len(closed_trades) == 0:
                    task_summary['win_rate'] = None

                task_summary['active_positions'] = active_positions
                task_summary['closed_trades'] = closed_trades
                task_summary['trade_logs'] = trade_logs
                task_summary['equity_curve'] = equity_curve
                return task_summary
        finally:
            conn.close()

    def sync_task(self, task_id: str) -> dict:
        """
        Synchronizes paper trading execution and daily equity curve for a specific task_id.
        """
        conn = get_connection_with_retry()
        try:
            # 1. Fetch task configuration
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT task_id, task_name, strategy_code, initial_capital, portfolio_config, start_date
                    FROM portfolio_tasks WHERE task_id = %s
                """, (task_id,))
                t_row = cur.fetchone()

            if not t_row:
                raise ValueError(f"Task not found: {task_id}")

            t_id, t_name, stg_code, initial_capital, p_cfg_raw, start_date = t_row
            initial_capital = float(initial_capital)
            start_date_str = str(start_date) if start_date else None
            stg_cfg = json.loads(p_cfg_raw) if p_cfg_raw and isinstance(p_cfg_raw, str) else (p_cfg_raw or {})

            if not stg_cfg:
                with conn.cursor() as cur:
                    cur.execute("SELECT factors_config FROM custom_strategies WHERE strategy_id = %s", (stg_code,))
                    row = cur.fetchone()
                    if row and row[0]:
                        stg_cfg = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            stg_cfg = stg_cfg or {}
            stg_cfg['initial_capital'] = initial_capital

            portfolio_strategy = PortfolioStrategyRegistry.get(stg_cfg)

            # 2. Fetch recommendation dates for strategy_code starting from start_date
            with conn.cursor() as cur:
                if start_date_str:
                    cur.execute(
                        "SELECT DISTINCT signal_date FROM recommendations WHERE strategy = %s AND signal_date >= %s ORDER BY signal_date ASC",
                        (stg_code, start_date_str)
                    )
                else:
                    cur.execute(
                        "SELECT DISTINCT signal_date FROM recommendations WHERE strategy = %s ORDER BY signal_date ASC",
                        (stg_code,)
                    )
                dates = [r[0].strftime('%Y-%m-%d') if hasattr(r[0], 'strftime') else str(r[0]) for r in cur.fetchall()]

            if not dates and start_date_str:
                # Fallback: if no signals exist >= start_date_str, fetch all signals and take latest available dates
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT DISTINCT signal_date FROM recommendations WHERE strategy = %s ORDER BY signal_date ASC",
                        (stg_code,)
                    )
                    all_d = [r[0].strftime('%Y-%m-%d') if hasattr(r[0], 'strftime') else str(r[0]) for r in cur.fetchall()]
                    if all_d:
                        dates = [all_d[-1]]

            if not dates:
                print(f"[PortfolioEngine] No recommendations found for strategy: {stg_code}")
                return {'status': 'empty', 'message': f'No recommendations found for {stg_code}'}

            # 3. Security names map
            name_map = {}
            with conn.cursor() as cur:
                cur.execute("SELECT code, code_name FROM stock_basic")
                for r in cur.fetchall():
                    name_map[r[0]] = r[1]
                cur.execute("SELECT code, name FROM cb_basic")
                for r in cur.fetchall():
                    name_map[r[0]] = r[1]

            # 4. Fetch recommendations
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT stock_code, action, price, reason, signal_date, factor_data FROM recommendations WHERE strategy = %s ORDER BY signal_date ASC, id ASC",
                    (stg_code,)
                )
                recs = cur.fetchall()

            rec_by_date: Dict[str, Dict[str, List[dict]]] = {}
            all_codes = set()
            for r in recs:
                code, action, price, reason, sdate, factor_data = r
                sdate_str = sdate.strftime('%Y-%m-%d') if hasattr(sdate, 'strftime') else str(sdate)
                all_codes.add(code)
                if sdate_str not in rec_by_date:
                    rec_by_date[sdate_str] = {'BUY': [], 'SELL': []}
                rec_by_date[sdate_str][action].append({
                    'code': code,
                    'price': float(price) if price else 0.0,
                    'reason': reason,
                    'factor_data': factor_data
                })

            # 5. Price map
            price_map: Dict[Tuple[str, str], Dict[str, float]] = {}
            if all_codes:
                codes_tuple = tuple(all_codes)
                min_date = dates[0]
                max_date = dates[-1]

                with conn.cursor() as cur:
                    if len(codes_tuple) == 1:
                        cur.execute(
                            "SELECT code, date, close, open, high, low FROM stock_kline_day WHERE code = %s AND date >= %s AND date <= %s",
                            (codes_tuple[0], min_date, max_date)
                        )
                    else:
                        cur.execute(
                            f"SELECT code, date, close, open, high, low FROM stock_kline_day WHERE code IN %s AND date >= %s AND date <= %s",
                            (codes_tuple, min_date, max_date)
                        )
                    for r in cur.fetchall():
                        d_str = r[1].strftime('%Y-%m-%d') if hasattr(r[1], 'strftime') else str(r[1])
                        price_map[(r[0], d_str)] = {
                            'close': float(r[2]),
                            'open': float(r[3]) if r[3] is not None else float(r[2]),
                            'high': float(r[4]) if r[4] is not None else float(r[2]),
                            'low': float(r[5]) if r[5] is not None else float(r[2]),
                        }

                    if len(codes_tuple) == 1:
                        cur.execute(
                            "SELECT code, date, close, open, high, low FROM cb_kline_day WHERE code = %s AND date >= %s AND date <= %s",
                            (codes_tuple[0], min_date, max_date)
                        )
                    else:
                        cur.execute(
                            f"SELECT code, date, close, open, high, low FROM cb_kline_day WHERE code IN %s AND date >= %s AND date <= %s",
                            (codes_tuple, min_date, max_date)
                        )
                    for r in cur.fetchall():
                        d_str = r[1].strftime('%Y-%m-%d') if hasattr(r[1], 'strftime') else str(r[1])
                        price_map[(r[0], d_str)] = {
                            'close': float(r[2]),
                            'open': float(r[3]) if r[3] is not None else float(r[2]),
                            'high': float(r[4]) if r[4] is not None else float(r[2]),
                            'low': float(r[5]) if r[5] is not None else float(r[2]),
                        }

            # 6. Run daily simulation
            per_slot_cash = initial_capital / portfolio_strategy.max_holdings
            slot_cash = [per_slot_cash] * portfolio_strategy.max_holdings
            active_positions: Dict[int, dict] = {}
            all_positions: List[dict] = []
            daily_history: List[dict] = []
            trade_logs: List[dict] = []
            pending_orders: List[dict] = []
            prev_total_equity = initial_capital

            for d_str in dates:
                day_recs = rec_by_date.get(d_str, {'BUY': [], 'SELL': []})

                active_positions, closed_trades_today, slot_cash, pending_orders = portfolio_strategy.evaluate_day(
                    d_str=d_str,
                    day_recs=day_recs,
                    price_map=price_map,
                    name_map=name_map,
                    active_positions=active_positions,
                    slot_cash=slot_cash,
                    pending_orders=pending_orders
                )

                # Record trade logs for closed trades today
                for c_trade in closed_trades_today:
                    all_positions.append(c_trade)
                    extra_d = c_trade.get('extra_data')
                    if isinstance(extra_d, str):
                        try:
                            extra_d = json.loads(extra_d)
                        except Exception:
                            extra_d = {}
                    elif not isinstance(extra_d, dict):
                        extra_d = {}

                    trade_logs.append({
                        'task_id': task_id,
                        'strategy': stg_code,
                        'trade_date': c_trade.get('sell_date', d_str),
                        'action': 'SELL',
                        'stock_code': c_trade['stock_code'],
                        'stock_name': c_trade.get('stock_name') or name_map.get(c_trade['stock_code'], c_trade['stock_code']),
                        'price': c_trade.get('sell_price', 0.0),
                        'shares': c_trade.get('shares', 0),
                        'cost_total': c_trade.get('cost_total', 0.0),
                        'pnl': c_trade.get('pnl', 0.0),
                        'pnl_pct': c_trade.get('pnl_pct', 0.0),
                        'reason': extra_d.get('sell_reason') or 'REBALANCE_SELL'
                    })

                # Check newly added holding positions today to log buy actions
                for slot_idx, pos in active_positions.items():
                    if pos.get('buy_date') == d_str:
                        # Ensure we haven't already logged this buy action
                        already_logged = any(l['action'] == 'BUY' and l['stock_code'] == pos['stock_code'] and l['trade_date'] == d_str for l in trade_logs)
                        if not already_logged:
                            trade_logs.append({
                                'task_id': task_id,
                                'strategy': stg_code,
                                'trade_date': d_str,
                                'action': 'BUY',
                                'stock_code': pos['stock_code'],
                                'stock_name': pos.get('stock_name') or name_map.get(pos['stock_code'], pos['stock_code']),
                                'price': pos['buy_price'],
                                'shares': pos['shares'],
                                'cost_total': pos['cost_total'],
                                'pnl': 0.0,
                                'pnl_pct': 0.0,
                                'reason': 'SIGNAL_BUY'
                            })

                total_market_val = sum(p['market_value'] for p in active_positions.values())
                total_cash = sum(slot_cash)
                total_equity = total_market_val + total_cash
                daily_pnl = total_equity - prev_total_equity
                cum_pnl = total_equity - initial_capital
                cum_return = (cum_pnl / initial_capital) * 100.0 if initial_capital > 0 else 0.0
                prev_total_equity = total_equity

                daily_history.append({
                    'task_id': task_id,
                    'strategy': stg_code,
                    'date': d_str,
                    'total_equity': round(total_equity, 4),
                    'cash': round(total_cash, 4),
                    'market_value': round(total_market_val, 4),
                    'daily_pnl': round(daily_pnl, 4),
                    'cum_pnl': round(cum_pnl, 4),
                    'cum_return': round(cum_return, 4),
                    'holding_count': len(active_positions)
                })

            for pos in active_positions.values():
                all_positions.append(pos)

            # 7. Calculate Quantitative Performance Metrics
            metrics = calculate_performance_metrics(
                daily_history,
                [p for p in all_positions if p.get('status') == 'SOLD'],
                initial_capital,
                start_date_str=start_date_str
            )

            latest_equity = daily_history[-1]['total_equity'] if daily_history else initial_capital
            latest_cash = daily_history[-1]['cash'] if daily_history else initial_capital
            latest_mv = daily_history[-1]['market_value'] if daily_history else 0.0
            cum_pnl = latest_equity - initial_capital
            holding_count = len(active_positions)

            # 8. Persist to TiDB Cloud
            with conn.cursor() as cur:
                # Clear and insert positions for task_id
                cur.execute("DELETE FROM positions WHERE task_id = %s", (task_id,))
                for p in all_positions:
                    cur.execute("""
                        INSERT INTO positions 
                        (task_id, strategy, stock_code, stock_name, buy_date, buy_price, highest_price, slot_idx, shares, cost_total, current_price, market_value, pnl, pnl_pct, status, sell_date, sell_price, extra_data)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        task_id, stg_code, p['stock_code'], p.get('stock_name'), p['buy_date'], p['buy_price'],
                        p.get('highest_price'), p.get('slot_idx', -1), p['shares'], p['cost_total'],
                        p['current_price'], p['market_value'], p['pnl'], p['pnl_pct'], p['status'],
                        p['sell_date'], p['sell_price'], json.dumps(p.get('extra_data')) if isinstance(p.get('extra_data'), dict) else p.get('extra_data')
                    ))

                # Clear and insert portfolio_daily_history for task_id
                cur.execute("DELETE FROM portfolio_daily_history WHERE task_id = %s", (task_id,))
                for h in daily_history:
                    cur.execute("""
                        INSERT INTO portfolio_daily_history
                        (task_id, strategy, date, total_equity, cash, market_value, daily_pnl, cum_pnl, cum_return, holding_count)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        task_id, stg_code, h['date'], h['total_equity'], h['cash'], h['market_value'],
                        h['daily_pnl'], h['cum_pnl'], h['cum_return'], h['holding_count']
                    ))

                # Clear and insert portfolio_trade_logs for task_id
                cur.execute("DELETE FROM portfolio_trade_logs WHERE task_id = %s", (task_id,))
                for log in trade_logs:
                    cur.execute("""
                        INSERT INTO portfolio_trade_logs
                        (task_id, strategy, trade_date, action, stock_code, stock_name, price, shares, cost_total, pnl, pnl_pct, reason)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        task_id, stg_code, log['trade_date'], log['action'], log['stock_code'],
                        log['stock_name'], log['price'], log['shares'], log['cost_total'],
                        log['pnl'], log['pnl_pct'], log['reason']
                    ))

                # Update portfolio_tasks summary record
                cur.execute("""
                    UPDATE portfolio_tasks
                    SET current_equity = %s, cash = %s, market_value = %s, cum_pnl = %s,
                        cum_return = %s, annual_return = %s, sharpe_ratio = %s, max_drawdown = %s, win_rate = %s,
                        holding_count = %s, updated_at = NOW()
                    WHERE task_id = %s
                """, (
                    latest_equity, latest_cash, latest_mv, cum_pnl,
                    metrics['cum_return'], metrics['annual_return'], metrics['sharpe_ratio'],
                    metrics['max_drawdown'], (metrics['win_rate'] if metrics['win_rate'] is not None else 0.0), holding_count, task_id
                ))

            conn.commit()
            print(f"[PortfolioEngine] ✅ Successfully synced task '{task_id}' with {len(daily_history)} days history, {len(trade_logs)} trade logs.")

            return {
                'status': 'success',
                'task_id': task_id,
                'days_count': len(daily_history),
                'active_holdings': len(active_positions),
                'total_trades': len(all_positions),
                'latest_equity': latest_equity,
                'metrics': metrics
            }

        except Exception as e:
            print(f"[PortfolioEngine] ❌ Error syncing task {task_id}: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    def sync_portfolio_history() -> dict:
        """
        Legacy fallback method for single strategy sync. Finds or creates a task for self.strategy_id and syncs it.
        """
        conn = get_connection_with_retry()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT task_id FROM portfolio_tasks WHERE strategy_code = %s AND status = 'ACTIVE' LIMIT 1", (self.strategy_id,))
                row = cur.fetchone()
            if row:
                return self.sync_task(row[0])
            else:
                task_detail = self.create_task(
                    task_name=f"{self.strategy_id} 策略实盘任务",
                    strategy_code=self.strategy_id,
                    initial_capital=self.initial_capital
                )
                return {'status': 'success', 'task_id': task_detail.get('task_id')}
        finally:
            conn.close()


def run_portfolio_sync_all_active():
    """
    Synchronizes portfolio paper trading for all active tasks.
    Can be called daily by GitHub Actions runner or runner.py after market close.
    """
    init_portfolio_db()
    conn = get_connection_with_retry()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT task_id FROM portfolio_tasks WHERE status = 'ACTIVE'")
            tasks = [r[0] for r in cur.fetchall()]

        if not tasks:
            # List tasks will auto-populate default tasks if empty
            all_t = PortfolioEngine.list_tasks()
            tasks = [t['task_id'] for t in all_t if t['status'] == 'ACTIVE']

        print(f"[PortfolioEngine] Syncing all active tasks: {tasks}")
        engine = PortfolioEngine()
        for t_id in tasks:
            try:
                engine.sync_task(t_id)
            except Exception as e:
                print(f"[PortfolioEngine] Error syncing task {t_id}: {e}")
    finally:
        conn.close()


if __name__ == '__main__':
    print("Testing PortfolioEngine Task System...")
    init_portfolio_db()
    tasks = PortfolioEngine.list_tasks()
    print(f"Found {len(tasks)} tasks.")
    if tasks:
        t0 = tasks[0]
        print(f"First task details: {t0['task_name']} ({t0['task_id']}) -> Cum Return: {t0['cum_return']}%, Sharpe: {t0['sharpe_ratio']}")
