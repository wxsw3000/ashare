# -*- coding: utf-8 -*-
"""
MagicSTG Trade Decision Maker & Order Execution Engine
"""

import pandas as pd
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field

from MagicSTG.execution.position_manager import PositionManager
from MagicSTG.core.cost import calc_buy_cost, calc_sell_cost
from MagicSTG.core.limit import check_limit_up


@dataclass
class TradeDecision:
    """Trade decision output data model."""
    buys: List[Dict] = field(default_factory=list)
    sells: List[Dict] = field(default_factory=list)
    holds: List[Dict] = field(default_factory=list)
    missed: List[Dict] = field(default_factory=list)

    def has_action(self) -> bool:
        return len(self.buys) > 0 or len(self.sells) > 0


class DecisionMaker:
    """Engine that generates and executes trading decisions based on signals."""

    def __init__(self, config: dict, position_manager: PositionManager, signal_generator: Any):
        self.config = config
        self.position_manager = position_manager
        self.signal_generator = signal_generator
        self.max_holdings = config.get('strategy', {}).get('max_holdings', 5)
        self.per_stock_capital = config.get('strategy', {}).get('per_stock_capital', 20000.0)
        self.stop_loss = config.get('risk', {}).get('stop_loss', -0.08)
        self.enable_stop_loss = config.get('risk', {}).get('enable_stop_loss', False)

    def make_decisions(
        self,
        all_data: Dict[str, pd.DataFrame],
        date: pd.Timestamp
    ) -> TradeDecision:
        """Generates buy/sell decisions for a specified trading date."""
        decision = TradeDecision()

        positions = self.position_manager.get_positions()
        holding_codes = [p.code for p in positions]

        current_prices = {}
        for p in positions:
            if p.code in all_data and date in all_data[p.code].index:
                current_prices[p.code] = all_data[p.code].loc[date, 'close']
            else:
                current_prices[p.code] = p.buy_price

        # Check sell signals and stop loss
        for pos in positions:
            code = pos.code
            if code not in all_data:
                continue

            df = all_data[code]
            if date not in df.index:
                continue

            price = current_prices[code]
            pnl_pct = (price - pos.buy_price) / pos.buy_price

            sell_reason = None

            if self.enable_stop_loss and pnl_pct <= self.stop_loss:
                sell_reason = f"止损触发 (盈亏: {pnl_pct*100:.2f}%)"
            elif self.signal_generator.check_sell_signal(df, date):
                sell_reason = "技术指标卖出信号"

            if sell_reason:
                decision.sells.append({
                    'code': code,
                    'price': price,
                    'shares': pos.shares,
                    'reason': sell_reason
                })
            else:
                decision.holds.append({
                    'code': code,
                    'price': price,
                    'pnl_pct': round(pnl_pct * 100, 2)
                })

        # Calculate buy candidates
        sold_codes = {s['code'] for s in decision.sells}
        exclude_codes = set(holding_codes) - sold_codes

        simulated_slot_cash = list(self.position_manager.slot_cash)

        for sell in decision.sells:
            pos = self.position_manager.get_position(sell['code'])
            if pos and pos.slot_idx >= 0 and pos.slot_idx < self.max_holdings:
                sell_amount = pos.shares * sell['price']
                fee = calc_sell_cost(sell_amount)
                proceeds = sell_amount - fee
                if self.config.get('strategy', {}).get('enable_compounding', False):
                    simulated_slot_cash[pos.slot_idx] += proceeds
                else:
                    simulated_slot_cash[pos.slot_idx] = float(self.per_stock_capital)

        active_slots_after_sells = {
            p.slot_idx for p in positions if p.code not in sold_codes
        }
        empty_slots = sorted(list(set(range(self.max_holdings)) - active_slots_after_sells))

        remaining_slots = self.max_holdings - len(holding_codes) + len(decision.sells)

        if remaining_slots > 0:
            if hasattr(self.signal_generator, 'generate_signals'):
                buy_candidates, _ = self.signal_generator.generate_signals(date, all_data)
            else:
                buy_candidates, _ = self.signal_generator.get_signals(
                    all_data,
                    date,
                    exclude_codes=list(exclude_codes)
                )

            valid_candidates = []
            for cand in buy_candidates:
                code = cand[0]
                price = cand[1]
                if code in all_data and check_limit_up(all_data[code], date, price):
                    decision.missed.append({
                        'code': code,
                        'price': price,
                        'reason': '涨停无法买入'
                    })
                else:
                    valid_candidates.append((code, price))
            buy_candidates = valid_candidates

            strict_matching = self.config.get('strategy', {}).get('strict_matching', True)

            for slot_idx in list(empty_slots):
                if len(buy_candidates) == 0:
                    break

                if strict_matching:
                    cand = buy_candidates.pop(0)
                    code = cand[0]
                    price = cand[1]
                    if code not in all_data:
                        continue

                    available_cash = simulated_slot_cash[slot_idx] if self.config.get('strategy', {}).get('enable_compounding', False) else self.per_stock_capital
                    max_shares = int(available_cash / price / 100) * 100

                    if max_shares <= 0:
                        decision.missed.append({
                            'code': code,
                            'price': price,
                            'reason': f'资金不足（需要{price*100:.0f}元）'
                        })
                        continue

                    buy_amount = max_shares * price
                    cost_fee = calc_buy_cost(buy_amount)
                    total_cost = buy_amount + cost_fee

                    if total_cost > available_cash:
                        decision.missed.append({
                            'code': code,
                            'price': price,
                            'reason': f'资金不足（需要{price*100:.0f}元）'
                        })
                        continue

                    empty_slots.remove(slot_idx)
                    decision.buys.append({
                        'code': code,
                        'price': price,
                        'shares': max_shares,
                        'amount': round(buy_amount, 2),
                        'fee': round(cost_fee, 2),
                        'total': round(total_cost, 2),
                        'slot_idx': slot_idx
                    })
                    simulated_slot_cash[slot_idx] -= total_cost
                else:
                    bought_for_slot = False
                    for i_cand, cand in enumerate(buy_candidates):
                        code = cand[0]
                        price = cand[1]
                        if code not in all_data:
                            continue

                        available_cash = simulated_slot_cash[slot_idx] if self.config.get('strategy', {}).get('enable_compounding', False) else self.per_stock_capital
                        max_shares = int(available_cash / price / 100) * 100

                        if max_shares <= 0:
                            continue

                        buy_amount = max_shares * price
                        cost_fee = calc_buy_cost(buy_amount)
                        total_cost = buy_amount + cost_fee

                        if total_cost > available_cash:
                            max_shares = int((available_cash - cost_fee) / price / 100) * 100
                            if max_shares <= 0:
                                continue
                            buy_amount = max_shares * price
                            cost_fee = calc_buy_cost(buy_amount)
                            total_cost = buy_amount + cost_fee

                        empty_slots.remove(slot_idx)
                        decision.buys.append({
                            'code': code,
                            'price': price,
                            'shares': max_shares,
                            'amount': round(buy_amount, 2),
                            'fee': round(cost_fee, 2),
                            'total': round(total_cost, 2),
                            'slot_idx': slot_idx
                        })
                        simulated_slot_cash[slot_idx] -= total_cost
                        buy_candidates.pop(i_cand)
                        bought_for_slot = True
                        break

                    if not bought_for_slot and buy_candidates:
                        cand = buy_candidates.pop(0)
                        code = cand[0]
                        price = cand[1]
                        decision.missed.append({
                            'code': code,
                            'price': price,
                            'reason': f'资金不足（需要{price*100:.0f}元）'
                        })

        return decision

    def execute_decisions(self, decision: TradeDecision, confirm: bool = False):
        """Executes buy/sell decisions when confirm is True."""
        if not confirm:
            return

        for sell in decision.sells:
            code = sell['code']
            price = sell['price']
            self.position_manager.remove_position(code, sell_price=price)
            print(f"  [EXECUTE] 卖出执行: {code} 价格:{price:.2f}")

        for buy in decision.buys:
            code = buy['code']
            price = buy['price']
            shares = buy['shares']
            total = buy.get('total', shares * price)
            slot_idx = buy.get('slot_idx', -1)
            self.position_manager.add_position(code, buy_price=price, shares=shares, cost_total=total, slot_idx=slot_idx)
            print(f"  [EXECUTE] 买入执行: {code} 价格:{price:.2f} 股数:{shares}")

