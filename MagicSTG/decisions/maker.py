# ============ 交易决策器 ============
import pandas as pd
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field

# 绝对导入
from positions.manager import PositionManager
from signals.generator_price import SignalGenerator
from utils.cost import calc_buy_cost, calc_sell_cost, calc_net_sell
from utils.limit import check_limit_up, check_limit_down


@dataclass
class TradeDecision:
    """交易决策"""
    buys: List[Dict] = field(default_factory=list)
    sells: List[Dict] = field(default_factory=list)
    holds: List[Dict] = field(default_factory=list)
    missed: List[Dict] = field(default_factory=list)
    
    def has_action(self) -> bool:
        return len(self.buys) > 0 or len(self.sells) > 0


class DecisionMaker:
    """交易决策器"""
    
    def __init__(self, config: dict, position_manager: PositionManager, signal_generator: SignalGenerator):
        self.config = config
        self.position_manager = position_manager
        self.signal_generator = signal_generator
        self.max_holdings = config['strategy']['max_holdings']
        self.per_stock_capital = config['strategy']['per_stock_capital']
        self.stop_loss = config['risk']['stop_loss']
        self.enable_stop_loss = config['risk']['enable_stop_loss']
    
    def make_decisions(
        self,
        all_data: Dict[str, pd.DataFrame],
        date: pd.Timestamp
    ) -> TradeDecision:
        """生成交易决策"""
        decision = TradeDecision()
        
        positions = self.position_manager.get_positions()
        holding_codes = [p.code for p in positions]
        
        current_prices = {}
        for p in positions:
            if p.code in all_data and date in all_data[p.code].index:
                current_prices[p.code] = all_data[p.code].loc[date, 'close']
            else:
                current_prices[p.code] = p.buy_price
        
        # ===== 1. 检查卖出 =====
        for pos in positions:
            code = pos.code
            if code not in all_data:
                continue
            
            df = all_data[code]
            if date not in df.index:
                continue
            
            row = df.loc[date]
            price = row['close']
            
            if pd.isna(price) or price <= 0:
                continue
            
            if check_limit_down(df, date, price):
                decision.holds.append({
                    'code': code,
                    'price': price,
                    'shares': pos.shares,
                    'reason': '跌停无法卖出'
                })
                continue
            
            # 止损检查
            if self.enable_stop_loss:
                pnl_pct = (price - pos.buy_price) / pos.buy_price * 100
                if pnl_pct <= self.stop_loss:
                    decision.sells.append({
                        'code': code,
                        'price': price,
                        'shares': pos.shares,
                        'reason': f'止损（亏损{pnl_pct:.2f}%）'
                    })
                    continue
            
            # 卖出信号
            if self.signal_generator.check_sell_signal(df, date):
                pnl_pct = (price - pos.buy_price) / pos.buy_price * 100 if pos.buy_price > 0 else 0
                decision.sells.append({
                    'code': code,
                    'price': price,
                    'shares': pos.shares,
                    'reason': f'卖出信号（盈亏{pnl_pct:.2f}%）'
                })
                continue
            
            decision.holds.append({
                'code': code,
                'price': price,
                'shares': pos.shares,
                'buy_price': pos.buy_price,
                'pnl_pct': round((price - pos.buy_price) / pos.buy_price * 100, 2) if pos.buy_price > 0 else 0
            })
        
        # ===== 2. 检查买入 =====
        exclude_codes = set(holding_codes)
        sold_codes = set()
        for sell in decision.sells:
            exclude_codes.add(sell['code'])
            sold_codes.add(sell['code'])
            
        # 复制槽位现金
        simulated_slot_cash = list(self.position_manager.slot_cash)
        
        # 释放已计划卖出股票对应的槽位资金
        for sell in decision.sells:
            pos = self.position_manager.get_position(sell['code'])
            if pos:
                sell_amount = pos.shares * sell['price']
                fee = calc_sell_cost(sell_amount)
                proceeds = sell_amount - fee
                if self.config['strategy'].get('enable_compounding', False):
                    simulated_slot_cash[pos.slot_idx] += proceeds
                else:
                    simulated_slot_cash[pos.slot_idx] = float(self.per_stock_capital)
                    
        # 找出当前空余槽位
        active_slots_after_sells = {
            p.slot_idx for p in positions if p.code not in sold_codes
        }
        empty_slots = sorted(list(set(range(self.max_holdings)) - active_slots_after_sells))
        
        remaining_slots = self.max_holdings - len(holding_codes) + len(decision.sells)
        
        if remaining_slots > 0:
            buy_candidates, _ = self.signal_generator.get_signals(
                all_data, 
                date, 
                exclude_codes=list(exclude_codes)
            )
            
            # 对齐原回测逻辑：在候选股排序和分配账户前，先剔除今天涨停的股票！
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
            
            strict_matching = self.config['strategy'].get('strict_matching', True)
            
            if strict_matching:
                # 严格模式：按原脚本逻辑，每个空闲槽位只尝试一次排在最前面的候选股，超支不降级也不重试其他股
                for slot_idx in list(empty_slots):
                    if len(buy_candidates) == 0:
                        break
                    
                    cand = buy_candidates.pop(0)
                    code = cand[0]
                    price = cand[1]
                    if code not in all_data:
                        continue
                        
                    available_cash = simulated_slot_cash[slot_idx] if self.config['strategy'].get('enable_compounding', False) else self.per_stock_capital
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
                # 智能模式：支持资金不足自动降低买入股数，且当前候选股完全买不起时重试后续其他候选股
                for slot_idx in list(empty_slots):
                    if len(buy_candidates) == 0:
                        break
                    
                    # 遍历候选股以寻找该槽位能买得起的第一个股票
                    bought_for_slot = False
                    for i_cand, cand in enumerate(buy_candidates):
                        code = cand[0]
                        price = cand[1]
                        if code not in all_data:
                            continue
                            
                        available_cash = simulated_slot_cash[slot_idx] if self.config['strategy'].get('enable_compounding', False) else self.per_stock_capital
                        max_shares = int(available_cash / price / 100) * 100
                        
                        if max_shares <= 0:
                            continue
                            
                        buy_amount = max_shares * price
                        cost_fee = calc_buy_cost(buy_amount)
                        total_cost = buy_amount + cost_fee
                        
                        # 如果超支，尝试降级
                        if total_cost > available_cash:
                            max_shares = int((available_cash - cost_fee) / price / 100) * 100
                            if max_shares <= 0:
                                continue
                            buy_amount = max_shares * price
                            cost_fee = calc_buy_cost(buy_amount)
                            total_cost = buy_amount + cost_fee
                            
                        # 成功买入
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
                        
                    if not bought_for_slot:
                        # 如果所有候选股都买不起，则记录第一个候选股为资金不足
                        cand = buy_candidates.pop(0)
                        code = cand[0]
                        price = cand[1]
                        decision.missed.append({
                            'code': code,
                            'price': price,
                            'reason': f'资金不足（需要{price*100:.0f}元）'
                        })
        
        return decision
    
    def execute_decisions(self, decisions: TradeDecision, confirm: bool = True) -> Dict:
        """执行交易决策"""
        result = {
            'executed_buys': [],
            'executed_sells': [],
            'skipped': []
        }
        
        if not decisions.has_action():
            print("  📭 今日无交易信号")
            return result
        
        print("\n" + "=" * 70)
        print("  📊 今日交易决策")
        print("=" * 70)
        
        if decisions.sells:
            print("\n  🔴 卖出建议:")
            for sell in decisions.sells:
                print(f"    {sell['code']} 价格:{sell['price']:.2f} 股数:{sell['shares']} 原因:{sell['reason']}")
        
        if decisions.buys:
            print("\n  🟢 买入建议:")
            for buy in decisions.buys:
                print(f"    {buy['code']} 价格:{buy['price']:.2f} 股数:{buy['shares']} 金额:{buy['amount']:.2f}")
        
        if decisions.holds:
            print("\n  ⏸️ 继续持有:")
            for hold in decisions.holds:
                print(f"    {hold['code']} 价格:{hold['price']:.2f} 盈亏:{hold.get('pnl_pct', 0):.2f}%")
        
        if decisions.missed:
            print("\n  ⚠️ 错失信号:")
            for miss in decisions.missed:
                print(f"    {miss['code']} 价格:{miss['price']:.2f} 原因:{miss['reason']}")
        
        print("=" * 70)
        
        if self.config['mode'].get('simulation', True):
            print("\n  🧪 模拟模式：请根据上述建议手动操作，然后更新持仓")
            return result
        
        if confirm:
            user_input = input("\n  是否执行上述交易？(y/n): ")
            if user_input.lower() != 'y':
                print("  ❌ 已取消执行")
                return result
        
        for sell in decisions.sells:
            # 计算手续费
            sell_amount = sell['shares'] * sell['price']
            fee = calc_sell_cost(sell_amount)
            pos = self.position_manager.remove_position(sell['code'], sell['price'], fee)
            if pos:
                result['executed_sells'].append({
                    'code': sell['code'],
                    'price': sell['price'],
                    'shares': sell['shares'],
                    'pnl': round((sell['price'] - pos.buy_price) * sell['shares'], 2)
                })
                print(f"  ✅ 卖出 {sell['code']} 成功")
        
        for buy in decisions.buys:
            if self.position_manager.add_position(
                buy['code'], 
                buy['price'], 
                buy['shares'], 
                buy['total']
            ):
                result['executed_buys'].append({
                    'code': buy['code'],
                    'price': buy['price'],
                    'shares': buy['shares'],
                    'total': buy['total']
                })
                print(f"  ✅ 买入 {buy['code']} 成功")
            else:
                result['skipped'].append({
                    'code': buy['code'],
                    'reason': '添加持仓失败'
                })
                print(f"  ❌ 买入 {buy['code']} 失败")
        
        return result