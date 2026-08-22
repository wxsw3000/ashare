# -*- coding: utf-8 -*-
"""
MagicSTG Equal-Slot Portfolio Strategy
Implements parameterized equal-slot partitioning, compounding mode, dynamic risk controls,
and configurable T+1 morning open or T close trade execution.
"""

import json
import math
from typing import Dict, List, Optional, Any, Tuple

from MagicSTG.execution.portfolio_strategies.base import (
    BasePortfolioStrategy,
    is_convertible_bond,
    calc_trade_cost_for_security
)


class EqualSlotPortfolioStrategy(BasePortfolioStrategy):
    """
    Equal-Slot Portfolio Management Strategy.
    
    Features:
    1. Divides total capital into N equal slots (max_holdings).
    2. Supports 'EQUAL_SLOT' (reset slot budget on sell) or 'COMPOUNDING' (reinvest sell proceeds).
    3. Configurable Hard Stop-Loss (e.g. -8.0%), Trailing Stop-Profit (e.g. -5.0%), and Max Holding Days.
    4. Configurable Execution Timing: 'T+1_OPEN' (no lookahead bias + slippage) or 'T_CLOSE' (legacy same-day).
    """

    def __init__(self, name: str = 'equal_slot', config: Optional[dict] = None):
        super().__init__(name, config)
        stg_cfg = self.config.get('portfolio_config') or self.config.get('strategy') or self.config
        self.allocation_mode = stg_cfg.get('allocation_mode', 'EQUAL_SLOT')  # 'EQUAL_SLOT' or 'COMPOUNDING'
        
        # Risk controls (negative float, e.g. -8.0 for -8% stop loss; None or 0 to disable)
        sl_val = stg_cfg.get('stop_loss_pct', -8.0)
        self.stop_loss_pct = float(sl_val) if sl_val is not None and float(sl_val) < 0 else None

        tp_val = stg_cfg.get('trailing_stop_pct', -5.0)
        self.trailing_stop_pct = float(tp_val) if tp_val is not None and float(tp_val) < 0 else None

        th_val = stg_cfg.get('max_holding_days', None)
        self.max_holding_days = int(th_val) if th_val is not None and int(th_val) > 0 else None

    def reset(self):
        pass

    def evaluate_day(
        self,
        d_str: str,
        day_recs: Dict[str, List[dict]],
        price_map: Dict[Tuple[str, str], Dict[str, float]],
        name_map: Dict[str, str],
        active_positions: Dict[int, dict],
        slot_cash: List[float],
        pending_orders: Optional[List[dict]] = None
    ) -> Tuple[Dict[int, dict], List[dict], List[float], List[dict]]:
        """
        Evaluates daily position management logic.
        """
        pending_orders = pending_orders or []
        closed_trades_today: List[dict] = []
        new_pending_orders: List[dict] = []
        per_slot_initial_cash = self.initial_capital / self.max_holdings

        # Helper to get price dict safely
        def get_prices(code: str) -> Dict[str, float]:
            return price_map.get((code, d_str), {})

        # --- Phase 1: Execute Pending Orders from Previous Day (For T+1 Morning Open Execution) ---
        if self.execution_timing == 'T+1_OPEN' and pending_orders:
            for p_order in pending_orders:
                action = p_order['action']
                code = p_order['code']
                p_dict = get_prices(code)
                open_p = p_dict.get('open', 0.0) or p_dict.get('close', 0.0) or p_order.get('price', 0.0)

                if open_p <= 0:
                    continue

                if action == 'SELL':
                    target_slot = None
                    for slot_idx, pos in active_positions.items():
                        if pos['stock_code'] == code:
                            target_slot = slot_idx
                            break

                    if target_slot is not None:
                        pos = active_positions[target_slot]
                        sell_price = round(open_p * (1.0 - self.slippage), 4)
                        fee, net_proceeds = calc_trade_cost_for_security(code, sell_price, pos['shares'], is_buy=False)
                        realized_pnl = net_proceeds - pos['cost_total']

                        if self.allocation_mode == 'COMPOUNDING':
                            slot_cash[target_slot] += net_proceeds
                        else:
                            slot_cash[target_slot] = per_slot_initial_cash

                        pos['status'] = 'SOLD'
                        pos['sell_date'] = d_str
                        pos['sell_price'] = sell_price
                        pos['fee'] = round(fee, 4)
                        pos['pnl'] = round(realized_pnl, 4)
                        pos['pnl_pct'] = round((realized_pnl / pos['cost_total']) * 100.0, 4) if pos['cost_total'] > 0 else 0.0
                        pos['extra_data'] = json.dumps({'exit_reason': p_order.get('reason', 'T+1开盘价卖出')})
                        closed_trades_today.append(pos)
                        del active_positions[target_slot]

                elif action == 'BUY':
                    # Allocate free slot
                    occupied_slots = set(active_positions.keys())
                    free_slots = [s for s in range(self.max_holdings) if s not in occupied_slots]

                    already_holding = any(p['stock_code'] == code for p in active_positions.values())
                    if free_slots and not already_holding:
                        slot_idx = free_slots[0]
                        budget = slot_cash[slot_idx]
                        buy_price = round(open_p * (1.0 + self.slippage), 4)

                        is_cb = is_convertible_bond(code)
                        if is_cb:
                            fee_rate = 0.00005
                            max_bonds = math.floor(budget / (buy_price * (1.0 + fee_rate)) / 10) * 10
                            shares = int(max_bonds)
                        else:
                            fee_rate = 0.00025
                            avail_budget = max(0.0, budget - 5.0)
                            max_shares = math.floor(avail_budget / (buy_price * (1.0 + fee_rate)) / 100) * 100
                            shares = int(max_shares)

                        min_lot = 10 if is_cb else 100
                        if shares >= min_lot:
                            fee, total_cost = calc_trade_cost_for_security(code, buy_price, shares, is_buy=True)
                            slot_cash[slot_idx] -= total_cost

                            stock_name = name_map.get(code, code)
                            pos = {
                                'strategy': self.name,
                                'stock_code': code,
                                'stock_name': stock_name,
                                'buy_date': d_str,
                                'buy_price': buy_price,
                                'highest_price': buy_price,
                                'slot_idx': slot_idx,
                                'shares': shares,
                                'cost_total': round(total_cost, 4),
                                'fee': round(fee, 4),
                                'current_price': buy_price,
                                'market_value': round(shares * buy_price, 4),
                                'pnl': 0.0,
                                'pnl_pct': 0.0,
                                'status': 'HOLDING',
                                'sell_date': None,
                                'sell_price': None,
                                'holding_days': 0,
                                'extra_data': json.dumps({'buy_reason': p_order.get('reason', 'T+1开盘价建仓')}, ensure_ascii=False)
                            }
                            active_positions[slot_idx] = pos

        # --- Phase 2: Update Active Positions Market Value & Check Risk Controls ---
        for slot_idx in list(active_positions.keys()):
            pos = active_positions[slot_idx]
            code = pos['stock_code']
            p_dict = get_prices(code)
            curr_price = p_dict.get('close', pos['current_price'])
            high_price = p_dict.get('high', curr_price) or curr_price

            if curr_price and curr_price > 0:
                pos['current_price'] = curr_price
                pos['highest_price'] = max(pos.get('highest_price', pos['buy_price']), high_price)
                pos['market_value'] = round(pos['shares'] * curr_price, 4)
                pos['pnl'] = round(pos['market_value'] - pos['cost_total'], 4)
                pos['pnl_pct'] = round((pos['pnl'] / pos['cost_total']) * 100.0, 4) if pos['cost_total'] > 0 else 0.0
                pos['holding_days'] = pos.get('holding_days', 0) + 1

            # Check Hard Stop Loss
            if self.stop_loss_pct is not None and pos['pnl_pct'] <= self.stop_loss_pct:
                sell_price = round(curr_price * (1.0 - self.slippage), 4) if curr_price > 0 else pos['buy_price']
                fee, net_proceeds = calc_trade_cost_for_security(code, sell_price, pos['shares'], is_buy=False)
                realized_pnl = net_proceeds - pos['cost_total']

                if self.allocation_mode == 'COMPOUNDING':
                    slot_cash[slot_idx] += net_proceeds
                else:
                    slot_cash[slot_idx] = per_slot_initial_cash

                pos['status'] = 'SOLD'
                pos['sell_date'] = d_str
                pos['sell_price'] = sell_price
                pos['fee'] = round(fee, 4)
                pos['pnl'] = round(realized_pnl, 4)
                pos['pnl_pct'] = round((realized_pnl / pos['cost_total']) * 100.0, 4) if pos['cost_total'] > 0 else 0.0
                pos['extra_data'] = json.dumps({'exit_reason': f"触发止损线 ({pos['pnl_pct']:.2f}% <= {self.stop_loss_pct:.2f}%)"}, ensure_ascii=False)
                closed_trades_today.append(pos)
                del active_positions[slot_idx]
                continue

            # Check Trailing Stop Profit
            if self.trailing_stop_pct is not None and pos['highest_price'] > pos['buy_price']:
                drawdown = ((curr_price - pos['highest_price']) / pos['highest_price']) * 100.0
                if drawdown <= self.trailing_stop_pct:
                    sell_price = round(curr_price * (1.0 - self.slippage), 4) if curr_price > 0 else pos['buy_price']
                    fee, net_proceeds = calc_trade_cost_for_security(code, sell_price, pos['shares'], is_buy=False)
                    realized_pnl = net_proceeds - pos['cost_total']

                    if self.allocation_mode == 'COMPOUNDING':
                        slot_cash[slot_idx] += net_proceeds
                    else:
                        slot_cash[slot_idx] = per_slot_initial_cash

                    pos['status'] = 'SOLD'
                    pos['sell_date'] = d_str
                    pos['sell_price'] = sell_price
                    pos['fee'] = round(fee, 4)
                    pos['pnl'] = round(realized_pnl, 4)
                    pos['pnl_pct'] = round((realized_pnl / pos['cost_total']) * 100.0, 4) if pos['cost_total'] > 0 else 0.0
                    pos['extra_data'] = json.dumps({'exit_reason': f"触发移动止盈 ({drawdown:.2f}% <= {self.trailing_stop_pct:.2f}%)"}, ensure_ascii=False)
                    closed_trades_today.append(pos)
                    del active_positions[slot_idx]
                    continue

            # Check Max Holding Days
            if self.max_holding_days is not None and pos['holding_days'] >= self.max_holding_days:
                sell_price = round(curr_price * (1.0 - self.slippage), 4) if curr_price > 0 else pos['buy_price']
                fee, net_proceeds = calc_trade_cost_for_security(code, sell_price, pos['shares'], is_buy=False)
                realized_pnl = net_proceeds - pos['cost_total']

                if self.allocation_mode == 'COMPOUNDING':
                    slot_cash[slot_idx] += net_proceeds
                else:
                    slot_cash[slot_idx] = per_slot_initial_cash

                pos['status'] = 'SOLD'
                pos['sell_date'] = d_str
                pos['sell_price'] = sell_price
                pos['fee'] = round(fee, 4)
                pos['pnl'] = round(realized_pnl, 4)
                pos['pnl_pct'] = round((realized_pnl / pos['cost_total']) * 100.0, 4) if pos['cost_total'] > 0 else 0.0
                pos['extra_data'] = json.dumps({'exit_reason': f"到达最大持仓天数 ({pos['holding_days']}天 >= {self.max_holding_days}天)"}, ensure_ascii=False)
                closed_trades_today.append(pos)
                del active_positions[slot_idx]
                continue

        # --- Phase 3: Process Today's Strategy Recommendation Signals ---
        sell_signals = day_recs.get('SELL', [])
        buy_signals = day_recs.get('BUY', [])

        if self.execution_timing == 'T+1_OPEN':
            # Queue signals to pending_orders for T+1 morning execution
            for sig in sell_signals:
                new_pending_orders.append({'action': 'SELL', 'code': sig['code'], 'reason': sig.get('reason', '策略卖出信号')})
            for sig in buy_signals:
                new_pending_orders.append({'action': 'BUY', 'code': sig['code'], 'reason': sig.get('reason', '策略买入信号')})

        else:
            # Legacy Same-Day T Close Execution
            for sell_sig in sell_signals:
                code = sell_sig['code']
                target_slot = None
                for slot_idx, pos in active_positions.items():
                    if pos['stock_code'] == code:
                        target_slot = slot_idx
                        break

                if target_slot is not None:
                    pos = active_positions[target_slot]
                    p_dict = get_prices(code)
                    sell_price = sell_sig['price'] if sell_sig['price'] > 0 else p_dict.get('close', pos['current_price'])
                    fee, net_proceeds = calc_trade_cost_for_security(code, sell_price, pos['shares'], is_buy=False)
                    realized_pnl = net_proceeds - pos['cost_total']

                    if self.allocation_mode == 'COMPOUNDING':
                        slot_cash[target_slot] += net_proceeds
                    else:
                        slot_cash[target_slot] = per_slot_initial_cash

                    pos['status'] = 'SOLD'
                    pos['sell_date'] = d_str
                    pos['sell_price'] = sell_price
                    pos['fee'] = round(fee, 4)
                    pos['pnl'] = round(realized_pnl, 4)
                    pos['pnl_pct'] = round((realized_pnl / pos['cost_total']) * 100.0, 4) if pos['cost_total'] > 0 else 0.0
                    pos['extra_data'] = json.dumps({'exit_reason': sell_sig.get('reason', '策略卖出信号')}, ensure_ascii=False)
                    closed_trades_today.append(pos)
                    del active_positions[target_slot]

            occupied_slots = set(active_positions.keys())
            free_slots = [s for s in range(self.max_holdings) if s not in occupied_slots]

            if free_slots and buy_signals:
                for buy_sig in buy_signals:
                    if not free_slots:
                        break

                    code = buy_sig['code']
                    already_holding = any(p['stock_code'] == code for p in active_positions.values())
                    if already_holding:
                        continue

                    slot_idx = free_slots.pop(0)
                    budget = slot_cash[slot_idx]
                    p_dict = get_prices(code)
                    buy_price = buy_sig['price'] if buy_sig['price'] > 0 else p_dict.get('close', 0.0)

                    if buy_price <= 0:
                        continue

                    is_cb = is_convertible_bond(code)
                    if is_cb:
                        fee_rate = 0.00005
                        max_bonds = math.floor(budget / (buy_price * (1.0 + fee_rate)) / 10) * 10
                        shares = int(max_bonds)
                    else:
                        fee_rate = 0.00025
                        avail_budget = max(0.0, budget - 5.0)
                        max_shares = math.floor(avail_budget / (buy_price * (1.0 + fee_rate)) / 100) * 100
                        shares = int(max_shares)

                    min_lot = 10 if is_cb else 100
                    if shares >= min_lot:
                        fee, total_cost = calc_trade_cost_for_security(code, buy_price, shares, is_buy=True)
                        slot_cash[slot_idx] -= total_cost

                        stock_name = name_map.get(code, code)
                        pos = {
                            'strategy': self.name,
                            'stock_code': code,
                            'stock_name': stock_name,
                            'buy_date': d_str,
                            'buy_price': buy_price,
                            'highest_price': buy_price,
                            'slot_idx': slot_idx,
                            'shares': shares,
                            'cost_total': round(total_cost, 4),
                            'fee': round(fee, 4),
                            'current_price': buy_price,
                            'market_value': round(shares * buy_price, 4),
                            'pnl': 0.0,
                            'pnl_pct': 0.0,
                            'status': 'HOLDING',
                            'sell_date': None,
                            'sell_price': None,
                            'holding_days': 0,
                            'extra_data': json.dumps({'buy_reason': buy_sig.get('reason', '策略买入建仓')}, ensure_ascii=False)
                        }
                        active_positions[slot_idx] = pos

        return active_positions, closed_trades_today, slot_cash, new_pending_orders
