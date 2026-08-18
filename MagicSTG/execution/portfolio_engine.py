# -*- coding: utf-8 -*-
"""
MagicSTG Portfolio & Paper Trading Engine
Handles simulated portfolio management, order execution, friction costs, stop-loss/profit rules, and daily equity tracking into TiDB Cloud.
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
    Ensures that portfolio_daily_history table exists and positions table has all required columns.
    """
    conn = get_connection_with_retry()
    try:
        with conn.cursor() as cursor:
            # 1. Create portfolio_daily_history table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_daily_history (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    strategy VARCHAR(50) NOT NULL,
                    date DATE NOT NULL,
                    total_equity DECIMAL(20, 4) NOT NULL,
                    cash DECIMAL(20, 4) NOT NULL,
                    market_value DECIMAL(20, 4) NOT NULL,
                    daily_pnl DECIMAL(20, 4) NOT NULL DEFAULT 0.0,
                    cum_pnl DECIMAL(20, 4) NOT NULL DEFAULT 0.0,
                    cum_return DECIMAL(12, 6) NOT NULL DEFAULT 0.0,
                    holding_count INT NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_strategy_date (strategy, date),
                    INDEX idx_strategy_date (strategy, date)
                )
            """)

            # 2. Check and add columns to positions table if missing
            cursor.execute("DESCRIBE positions")
            cols = [r[0] for r in cursor.fetchall()]
            
            if 'stock_name' not in cols:
                cursor.execute("ALTER TABLE positions ADD COLUMN stock_name VARCHAR(100) DEFAULT NULL AFTER stock_code")
            if 'highest_price' not in cols:
                cursor.execute("ALTER TABLE positions ADD COLUMN highest_price DECIMAL(12,4) DEFAULT NULL AFTER buy_price")
            if 'slot_idx' not in cols:
                cursor.execute("ALTER TABLE positions ADD COLUMN slot_idx INT DEFAULT -1 AFTER highest_price")

        conn.commit()
    except Exception as e:
        print(f"[PortfolioEngine] DB Schema init notice: {e}")
    finally:
        conn.close()


class PortfolioEngine:
    """
    Simulated Portfolio & Paper Trading Engine.
    Orchestrates paper trading simulations by pairing strategy recommendation signals (recommendations)
    with plugin-based portfolio management strategies (PortfolioStrategyRegistry).
    """

    def __init__(self, strategy_id: str, config: Optional[dict] = None):
        self.strategy_id = strategy_id
        self.config = config or {}
        init_portfolio_db()
        self.portfolio_strategy = PortfolioStrategyRegistry.get(self.config)
        self.initial_capital = self.portfolio_strategy.initial_capital

    def sync_portfolio_history(self) -> dict:
        """
        Synchronizes portfolio holdings and daily equity curve from recommendation signals in TiDB Cloud.
        """
        conn = get_connection_with_retry()
        try:
            # 1. Load portfolio management strategy instance from registry
            stg_cfg = self.config
            if not stg_cfg:
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT factors_config FROM custom_strategies WHERE strategy_id = %s", (self.strategy_id,))
                        row = cur.fetchone()
                        if row and row[0]:
                            stg_cfg = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                except Exception as e:
                    print(f"[PortfolioEngine] Notice: Could not query custom_strategies for config: {e}")
                stg_cfg = stg_cfg or {}

            portfolio_strategy = PortfolioStrategyRegistry.get(stg_cfg)

            # 2. Fetch all recommendation dates for this strategy
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT signal_date FROM recommendations WHERE strategy = %s ORDER BY signal_date ASC",
                    (self.strategy_id,)
                )
                dates = [r[0].strftime('%Y-%m-%d') if hasattr(r[0], 'strftime') else str(r[0]) for r in cur.fetchall()]

            if not dates:
                print(f"[PortfolioEngine] No recommendations found for strategy: {self.strategy_id}")
                return {'status': 'empty', 'message': f'No recommendations found for {self.strategy_id}'}

            # 3. Fetch security code names map
            name_map = {}
            with conn.cursor() as cur:
                cur.execute("SELECT code, code_name FROM stock_basic")
                for r in cur.fetchall():
                    name_map[r[0]] = r[1]
                cur.execute("SELECT code, name FROM cb_basic")
                for r in cur.fetchall():
                    name_map[r[0]] = r[1]

            # 4. Fetch all recommendations for strategy
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT stock_code, action, price, reason, signal_date, factor_data FROM recommendations WHERE strategy = %s ORDER BY signal_date ASC, id ASC",
                    (self.strategy_id,)
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

            # 5. Fetch daily price records (close, open, high, low) for all referenced codes
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

            # 6. Initialize simulation state using portfolio strategy instance
            per_slot_cash = portfolio_strategy.initial_capital / portfolio_strategy.max_holdings
            slot_cash = [per_slot_cash] * portfolio_strategy.max_holdings
            active_positions: Dict[int, dict] = {}  # slot_idx -> position dict
            all_positions: List[dict] = []
            daily_history: List[dict] = []
            pending_orders: List[dict] = []
            prev_total_equity = portfolio_strategy.initial_capital

            # 7. Run daily simulation loop via portfolio_strategy plugin
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

                all_positions.extend(closed_trades_today)

                # Save Daily Portfolio Snapshot
                total_market_val = sum(p['market_value'] for p in active_positions.values())
                total_cash = sum(slot_cash)
                total_equity = total_market_val + total_cash
                daily_pnl = total_equity - prev_total_equity
                cum_pnl = total_equity - portfolio_strategy.initial_capital
                cum_return = (cum_pnl / portfolio_strategy.initial_capital) * 100.0
                prev_total_equity = total_equity

                daily_history.append({
                    'strategy': self.strategy_id,
                    'date': d_str,
                    'total_equity': round(total_equity, 4),
                    'cash': round(total_cash, 4),
                    'market_value': round(total_market_val, 4),
                    'daily_pnl': round(daily_pnl, 4),
                    'cum_pnl': round(cum_pnl, 4),
                    'cum_return': round(cum_return, 4),
                    'holding_count': len(active_positions)
                })

            # Add remaining active positions to all_positions
            for pos in active_positions.values():
                all_positions.append(pos)

            # 7. Persist to TiDB Cloud
            with conn.cursor() as cur:
                # Clear and insert positions
                cur.execute("DELETE FROM positions WHERE strategy = %s", (self.strategy_id,))
                for p in all_positions:
                    cur.execute("""
                        INSERT INTO positions 
                        (strategy, stock_code, stock_name, buy_date, buy_price, highest_price, slot_idx, shares, cost_total, current_price, market_value, pnl, pnl_pct, status, sell_date, sell_price, extra_data)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        p['strategy'], p['stock_code'], p.get('stock_name'), p['buy_date'], p['buy_price'],
                        p.get('highest_price'), p.get('slot_idx', -1), p['shares'], p['cost_total'],
                        p['current_price'], p['market_value'], p['pnl'], p['pnl_pct'], p['status'],
                        p['sell_date'], p['sell_price'], p.get('extra_data')
                    ))

                # Clear and insert portfolio_daily_history
                cur.execute("DELETE FROM portfolio_daily_history WHERE strategy = %s", (self.strategy_id,))
                for h in daily_history:
                    cur.execute("""
                        INSERT INTO portfolio_daily_history
                        (strategy, date, total_equity, cash, market_value, daily_pnl, cum_pnl, cum_return, holding_count)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        h['strategy'], h['date'], h['total_equity'], h['cash'], h['market_value'],
                        h['daily_pnl'], h['cum_pnl'], h['cum_return'], h['holding_count']
                    ))

            conn.commit()
            print(f"[PortfolioEngine] ✅ Successfully synced {len(daily_history)} days portfolio history for {self.strategy_id}")

            return {
                'status': 'success',
                'days_count': len(daily_history),
                'active_holdings': len(active_positions),
                'total_trades': len(all_positions),
                'latest_equity': daily_history[-1]['total_equity'] if daily_history else self.initial_capital,
                'latest_return': daily_history[-1]['cum_return'] if daily_history else 0.0
            }

        except Exception as e:
            print(f"[PortfolioEngine] ❌ Error syncing portfolio history: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    def get_portfolio_summary(self) -> dict:
        """
        Retrieves portfolio summary, active positions, trade history, and equity curve for Web UI.
        """
        conn = get_connection_with_retry()
        try:
            with conn.cursor() as cur:
                # 1. Get latest equity snapshot
                cur.execute(
                    "SELECT date, total_equity, cash, market_value, daily_pnl, cum_pnl, cum_return, holding_count FROM portfolio_daily_history WHERE strategy = %s ORDER BY date DESC LIMIT 1",
                    (self.strategy_id,)
                )
                latest_row = cur.fetchone()

                if not latest_row:
                    # Sync if empty
                    self.sync_portfolio_history()
                    cur.execute(
                        "SELECT date, total_equity, cash, market_value, daily_pnl, cum_pnl, cum_return, holding_count FROM portfolio_daily_history WHERE strategy = %s ORDER BY date DESC LIMIT 1",
                        (self.strategy_id,)
                    )
                    latest_row = cur.fetchone()

                if latest_row:
                    summary = {
                        'strategy': self.strategy_id,
                        'latest_date': latest_row[0].strftime('%Y-%m-%d') if hasattr(latest_row[0], 'strftime') else str(latest_row[0]),
                        'total_equity': float(latest_row[1]),
                        'cash': float(latest_row[2]),
                        'market_value': float(latest_row[3]),
                        'daily_pnl': float(latest_row[4]),
                        'cum_pnl': float(latest_row[5]),
                        'cum_return': float(latest_row[6]),
                        'holding_count': int(latest_row[7]),
                        'initial_capital': self.initial_capital
                    }
                else:
                    summary = {
                        'strategy': self.strategy_id,
                        'latest_date': datetime.now().strftime('%Y-%m-%d'),
                        'total_equity': self.initial_capital,
                        'cash': self.initial_capital,
                        'market_value': 0.0,
                        'daily_pnl': 0.0,
                        'cum_pnl': 0.0,
                        'cum_return': 0.0,
                        'holding_count': 0,
                        'initial_capital': self.initial_capital
                    }

                # 2. Get active positions
                cur.execute("""
                    SELECT stock_code, stock_name, buy_date, buy_price, highest_price, shares, cost_total, current_price, market_value, pnl, pnl_pct, slot_idx, extra_data
                    FROM positions
                    WHERE strategy = %s AND status = 'HOLDING'
                    ORDER BY buy_date DESC, id DESC
                """, (self.strategy_id,))
                active_rows = cur.fetchall()

                active_positions = []
                for r in active_rows:
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

                # 3. Get closed trades
                cur.execute("""
                    SELECT stock_code, stock_name, buy_date, buy_price, sell_date, sell_price, shares, cost_total, pnl, pnl_pct, extra_data
                    FROM positions
                    WHERE strategy = %s AND status = 'SOLD'
                    ORDER BY sell_date DESC, id DESC
                """, (self.strategy_id,))
                sold_rows = cur.fetchall()

                closed_trades = []
                for r in sold_rows:
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

                # 4. Get equity curve history
                cur.execute("""
                    SELECT date, total_equity, cash, market_value, daily_pnl, cum_pnl, cum_return, holding_count
                    FROM portfolio_daily_history
                    WHERE strategy = %s
                    ORDER BY date ASC
                """, (self.strategy_id,))
                hist_rows = cur.fetchall()

                equity_curve = []
                for r in hist_rows:
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

                summary['active_positions'] = active_positions
                summary['closed_trades'] = closed_trades
                summary['equity_curve'] = equity_curve
                return summary

        finally:
            conn.close()


def run_portfolio_sync_all_active():
    """
    Synchronizes portfolio holdings for all active workflow strategies.
    Can be called daily by GitHub Actions runner or runner.py.
    """
    conn = get_connection_with_retry()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT strategy_id FROM custom_strategies WHERE is_active = 1")
            strategies = [r[0] for r in cur.fetchall()]
        
        if not strategies:
            # Fallback to distinct recommendation strategies
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT strategy FROM recommendations")
                strategies = [r[0] for r in cur.fetchall()]

        print(f"[PortfolioEngine] Syncing portfolios for active strategies: {strategies}")
        for stg_id in strategies:
            engine = PortfolioEngine(stg_id)
            engine.sync_portfolio_history()

    finally:
        conn.close()


if __name__ == '__main__':
    # Standalone runner test
    print("Testing PortfolioEngine...")
    engine = PortfolioEngine('cb_double_low')
    res = engine.sync_portfolio_history()
    print("Sync result:", res)
    summary = engine.get_portfolio_summary()
    print("Summary latest total equity:", summary.get('total_equity'), "Holdings count:", len(summary.get('active_positions', [])))
