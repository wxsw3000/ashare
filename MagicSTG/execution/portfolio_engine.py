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


def is_convertible_bond(code: str) -> bool:
    """Checks if security code is a convertible bond."""
    if not code:
        return False
    clean_code = str(code).lower()
    return clean_code.startswith('sh.11') or clean_code.startswith('sz.12') or clean_code.startswith('11') or clean_code.startswith('12')


def calc_trade_cost_for_security(code: str, price: float, shares_or_bonds: int, is_buy: bool) -> Tuple[float, float]:
    """
    Calculates friction fee and net amount for stocks or convertible bonds.
    Returns (fee, total_cost_or_net_proceeds).
    """
    gross_amount = price * shares_or_bonds
    if is_convertible_bond(code):
        # CB Commission: 0.005%, No min limit, 0% stamp tax
        fee = gross_amount * 0.00005
        if is_buy:
            return fee, gross_amount + fee
        else:
            return fee, gross_amount - fee
    else:
        # Stock: 0.025% commission (min 5 CNY rule) + 0.05% sell stamp tax
        if is_buy:
            fee = calc_buy_cost(gross_amount)
            return fee, gross_amount + fee
        else:
            fee = calc_sell_cost(gross_amount)
            return fee, gross_amount - fee


class PortfolioEngine:
    """
    Simulated Portfolio & Paper Trading Engine.
    Converts strategy recommendation signals (recommendations) into simulated holdings (positions)
    and generates portfolio equity curves (portfolio_daily_history).
    """

    def __init__(self, strategy_id: str, config: Optional[dict] = None):
        self.strategy_id = strategy_id
        cfg = config or {}
        stg_cfg = cfg.get('strategy', {})
        self.initial_capital = float(stg_cfg.get('initial_capital', 100000.0))
        self.max_holdings = int(stg_cfg.get('max_holdings', 5))
        self.allocation_mode = stg_cfg.get('allocation_mode', 'EQUAL_SLOT')  # 'EQUAL_SLOT' or 'COMPOUNDING'
        self.stop_loss_pct = float(stg_cfg.get('stop_loss_pct', -8.0))
        self.trailing_stop_pct = float(stg_cfg.get('trailing_stop_pct', -5.0))
        init_portfolio_db()

    def sync_portfolio_history(self) -> dict:
        """
        Synchronizes portfolio holdings and daily equity curve from recommendation signals in TiDB Cloud.
        """
        conn = get_connection_with_retry()
        try:
            # 1. Fetch all recommendation dates for this strategy
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT signal_date FROM recommendations WHERE strategy = %s ORDER BY signal_date ASC",
                    (self.strategy_id,)
                )
                dates = [r[0].strftime('%Y-%m-%d') if hasattr(r[0], 'strftime') else str(r[0]) for r in cur.fetchall()]

            if not dates:
                print(f"[PortfolioEngine] No recommendations found for strategy: {self.strategy_id}")
                return {'status': 'empty', 'message': f'No recommendations found for {self.strategy_id}'}

            # 2. Fetch security code names map
            name_map = {}
            with conn.cursor() as cur:
                cur.execute("SELECT code, code_name FROM stock_basic")
                for r in cur.fetchall():
                    name_map[r[0]] = r[1]
                cur.execute("SELECT code, name FROM cb_basic")
                for r in cur.fetchall():
                    name_map[r[0]] = r[1]

            # 3. Fetch all recommendations for strategy
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

            # 4. Fetch daily close prices for all referenced codes
            price_map: Dict[Tuple[str, str], float] = {}
            if all_codes:
                codes_tuple = tuple(all_codes)
                min_date = dates[0]
                max_date = dates[-1]

                with conn.cursor() as cur:
                    if len(codes_tuple) == 1:
                        cur.execute(
                            "SELECT code, date, close FROM stock_kline_day WHERE code = %s AND date >= %s AND date <= %s",
                            (codes_tuple[0], min_date, max_date)
                        )
                    else:
                        cur.execute(
                            f"SELECT code, date, close FROM stock_kline_day WHERE code IN %s AND date >= %s AND date <= %s",
                            (codes_tuple, min_date, max_date)
                        )
                    for r in cur.fetchall():
                        d_str = r[1].strftime('%Y-%m-%d') if hasattr(r[1], 'strftime') else str(r[1])
                        price_map[(r[0], d_str)] = float(r[2])

                    if len(codes_tuple) == 1:
                        cur.execute(
                            "SELECT code, date, close FROM cb_kline_day WHERE code = %s AND date >= %s AND date <= %s",
                            (codes_tuple[0], min_date, max_date)
                        )
                    else:
                        cur.execute(
                            f"SELECT code, date, close FROM cb_kline_day WHERE code IN %s AND date >= %s AND date <= %s",
                            (codes_tuple, min_date, max_date)
                        )
                    for r in cur.fetchall():
                        d_str = r[1].strftime('%Y-%m-%d') if hasattr(r[1], 'strftime') else str(r[1])
                        price_map[(r[0], d_str)] = float(r[2])

            # 5. Initialize simulation state
            per_slot_cash = self.initial_capital / self.max_holdings
            slot_cash = [per_slot_cash] * self.max_holdings
            active_positions: Dict[int, dict] = {}  # slot_idx -> position dict
            all_positions: List[dict] = []
            daily_history: List[dict] = []
            prev_total_equity = self.initial_capital

            # 6. Run daily simulation loop
            for d_str in dates:
                day_recs = rec_by_date.get(d_str, {'BUY': [], 'SELL': []})

                # --- Phase A: Update Active Positions Market Value & Check Risk Controls ---
                for slot_idx in list(active_positions.keys()):
                    pos = active_positions[slot_idx]
                    code = pos['stock_code']
                    curr_price = price_map.get((code, d_str), pos['current_price'])

                    if curr_price and curr_price > 0:
                        pos['current_price'] = curr_price
                        pos['highest_price'] = max(pos['highest_price'], curr_price)
                        pos['market_value'] = round(pos['shares'] * curr_price, 4)
                        pos['pnl'] = round(pos['market_value'] - pos['cost_total'], 4)
                        pos['pnl_pct'] = round((pos['pnl'] / pos['cost_total']) * 100.0, 4) if pos['cost_total'] > 0 else 0.0

                    # Check Hard Stop-Loss
                    if self.stop_loss_pct < 0 and pos['pnl_pct'] <= self.stop_loss_pct:
                        # Auto SELL on Stop Loss
                        sell_price = curr_price if curr_price > 0 else pos['buy_price']
                        fee, net_proceeds = calc_trade_cost_for_security(code, sell_price, pos['shares'], is_buy=False)
                        realized_pnl = net_proceeds - pos['cost_total']

                        if self.allocation_mode == 'COMPOUNDING':
                            slot_cash[slot_idx] += net_proceeds
                        else:
                            slot_cash[slot_idx] = per_slot_cash

                        pos['status'] = 'SOLD'
                        pos['sell_date'] = d_str
                        pos['sell_price'] = sell_price
                        pos['pnl'] = round(realized_pnl, 4)
                        pos['pnl_pct'] = round((realized_pnl / pos['cost_total']) * 100.0, 4) if pos['cost_total'] > 0 else 0.0
                        pos['extra_data'] = json.dumps({'exit_reason': f"触发止损线 ({pos['pnl_pct']:.2f}% <= {self.stop_loss_pct:.2f}%)"})
                        all_positions.append(pos)
                        del active_positions[slot_idx]
                        continue

                    # Check Trailing Stop-Profit
                    if self.trailing_stop_pct < 0 and pos['highest_price'] > pos['buy_price']:
                        drawdown = ((curr_price - pos['highest_price']) / pos['highest_price']) * 100.0
                        if drawdown <= self.trailing_stop_pct:
                            sell_price = curr_price if curr_price > 0 else pos['buy_price']
                            fee, net_proceeds = calc_trade_cost_for_security(code, sell_price, pos['shares'], is_buy=False)
                            realized_pnl = net_proceeds - pos['cost_total']

                            if self.allocation_mode == 'COMPOUNDING':
                                slot_cash[slot_idx] += net_proceeds
                            else:
                                slot_cash[slot_idx] = per_slot_cash

                            pos['status'] = 'SOLD'
                            pos['sell_date'] = d_str
                            pos['sell_price'] = sell_price
                            pos['pnl'] = round(realized_pnl, 4)
                            pos['pnl_pct'] = round((realized_pnl / pos['cost_total']) * 100.0, 4) if pos['cost_total'] > 0 else 0.0
                            pos['extra_data'] = json.dumps({'exit_reason': f"触发移动止盈 ({drawdown:.2f}% <= {self.trailing_stop_pct:.2f}%)"})
                            all_positions.append(pos)
                            del active_positions[slot_idx]
                            continue

                # --- Phase B: Execute Strategy SELL Signals ---
                sell_signals = day_recs.get('SELL', [])
                for sell_sig in sell_signals:
                    code = sell_sig['code']
                    # Find slot holding this code
                    target_slot = None
                    for slot_idx, pos in active_positions.items():
                        if pos['stock_code'] == code:
                            target_slot = slot_idx
                            break

                    if target_slot is not None:
                        pos = active_positions[target_slot]
                        sell_price = sell_sig['price'] if sell_sig['price'] > 0 else pos['current_price']
                        fee, net_proceeds = calc_trade_cost_for_security(code, sell_price, pos['shares'], is_buy=False)
                        realized_pnl = net_proceeds - pos['cost_total']

                        if self.allocation_mode == 'COMPOUNDING':
                            slot_cash[target_slot] += net_proceeds
                        else:
                            slot_cash[target_slot] = per_slot_cash

                        pos['status'] = 'SOLD'
                        pos['sell_date'] = d_str
                        pos['sell_price'] = sell_price
                        pos['pnl'] = round(realized_pnl, 4)
                        pos['pnl_pct'] = round((realized_pnl / pos['cost_total']) * 100.0, 4) if pos['cost_total'] > 0 else 0.0
                        pos['extra_data'] = json.dumps({'exit_reason': sell_sig.get('reason', '策略卖出信号')})
                        all_positions.append(pos)
                        del active_positions[target_slot]

                # --- Phase C: Execute Strategy BUY Signals ---
                buy_signals = day_recs.get('BUY', [])
                occupied_slots = set(active_positions.keys())
                free_slots = [s for s in range(self.max_holdings) if s not in occupied_slots]

                if free_slots and buy_signals:
                    for buy_sig in buy_signals:
                        if not free_slots:
                            break

                        code = buy_sig['code']
                        # Check if already holding code
                        already_holding = any(p['stock_code'] == code for p in active_positions.values())
                        if already_holding:
                            continue

                        slot_idx = free_slots.pop(0)
                        budget = slot_cash[slot_idx]
                        buy_price = buy_sig['price']

                        if not buy_price or buy_price <= 0:
                            # try fetch close price
                            buy_price = price_map.get((code, d_str), 0.0)

                        if buy_price <= 0:
                            continue

                        # Calculate shares/bonds based on lot constraints
                        is_cb = is_convertible_bond(code)
                        if is_cb:
                            # 10 bonds per lot (100 CNY par value)
                            # gross budget approx = budget
                            fee_rate = 0.00005
                            max_bonds = math.floor(budget / (buy_price * (1.0 + fee_rate)) / 10) * 10
                            shares = int(max_bonds)
                        else:
                            # Stock: 100 shares per lot
                            # Budget minus 5 CNY min fee guard
                            fee_rate = 0.00025
                            avail_budget = max(0.0, budget - 5.0)
                            max_shares = math.floor(avail_budget / (buy_price * (1.0 + fee_rate)) / 100) * 100
                            shares = int(max_shares)

                        min_lot = 10 if is_cb else 100
                        if shares < min_lot:
                            # Insufficient budget for 1 hand
                            continue

                        fee, total_cost = calc_trade_cost_for_security(code, buy_price, shares, is_buy=True)
                        slot_cash[slot_idx] -= total_cost

                        stock_name = name_map.get(code, code)
                        pos = {
                            'strategy': self.strategy_id,
                            'stock_code': code,
                            'stock_name': stock_name,
                            'buy_date': d_str,
                            'buy_price': buy_price,
                            'highest_price': buy_price,
                            'slot_idx': slot_idx,
                            'shares': shares,
                            'cost_total': round(total_cost, 4),
                            'current_price': buy_price,
                            'market_value': round(shares * buy_price, 4),
                            'pnl': 0.0,
                            'pnl_pct': 0.0,
                            'status': 'HOLDING',
                            'sell_date': None,
                            'sell_price': None,
                            'extra_data': json.dumps({'buy_reason': buy_sig.get('reason', '策略买入建仓')})
                        }
                        active_positions[slot_idx] = pos

                # --- Phase D: Save Daily Portfolio Snapshot ---
                total_market_val = sum(p['market_value'] for p in active_positions.values())
                total_cash = sum(slot_cash)
                total_equity = total_market_val + total_cash
                daily_pnl = total_equity - prev_total_equity
                cum_pnl = total_equity - self.initial_capital
                cum_return = (cum_pnl / self.initial_capital) * 100.0
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
