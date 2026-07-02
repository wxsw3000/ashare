# ============ 持仓管理 ============
import os
import json
import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, asdict

# 获取项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class Position:
    """单只持仓"""
    code: str
    buy_date: str
    buy_price: float
    shares: int
    cost_total: float
    highest_price: float
    slot_idx: int = -1
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Position':
        return cls(**data)


class PositionManager:
    """持仓管理器"""
    
    def __init__(self, config: dict):
        self.config = config
        position_file = config['paths']['position_file']
        if not os.path.isabs(position_file):
            position_file = os.path.join(PROJECT_ROOT, position_file)
        self.position_file = position_file
        self.max_holdings = config['strategy']['max_holdings']
        self.per_stock_capital = config['strategy']['per_stock_capital']
        self.positions: List[Position] = []
        self.cash_remains: Dict[str, float] = {}
        self.cash_file = self.position_file.replace('.csv', '_cash.json')
        self.slot_cash = [float(self.per_stock_capital)] * self.max_holdings
        self.realized_pnl = 0.0
        self._load()
    
    def _load(self):
        """从 CSV 文件加载持仓"""
        self.positions = []
        self.cash_remains = {}
        self.realized_pnl = 0.0
        
        # 加载资金
        if self.config['strategy'].get('enable_compounding', False):
            if os.path.exists(self.cash_file):
                try:
                    with open(self.cash_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        self.slot_cash = [float(x) for x in data.get('slot_cash', [])]
                        if len(self.slot_cash) != self.max_holdings:
                            self.slot_cash = [float(self.per_stock_capital)] * self.max_holdings
                except Exception as e:
                    print(f"  ⚠️ 加载现金 JSON 失败: {e}，重置现金")
                    self.slot_cash = [float(self.per_stock_capital)] * self.max_holdings
            else:
                self.slot_cash = [float(self.per_stock_capital)] * self.max_holdings
        else:
            self.slot_cash = [float(self.per_stock_capital)] * self.max_holdings
            
        # 加载持仓股票
        if os.path.exists(self.position_file):
            try:
                df = pd.read_csv(self.position_file, encoding='utf-8')
                active_slots = set()
                for _, row in df.iterrows():
                    slot_idx = int(row.get('slot_idx', -1))
                    p = Position(
                        code=str(row['code']),
                        buy_date=str(row['buy_date']),
                        buy_price=float(row['buy_price']),
                        shares=int(row['shares']),
                        cost_total=float(row['cost_total']),
                        highest_price=float(row['highest_price']),
                        slot_idx=slot_idx
                    )
                    # 补齐缺少的 slot_idx
                    if p.slot_idx == -1:
                        p.slot_idx = min(set(range(self.max_holdings)) - active_slots)
                    active_slots.add(p.slot_idx)
                    self.positions.append(p)
                    self.cash_remains[p.code] = self.slot_cash[p.slot_idx]
                print(f"  📂 加载持仓: {len(self.positions)} 只 (CSV 格式)")
            except Exception as e:
                print(f"  ⚠️ 加载持仓 CSV 失败: {e}，将初始化为空")
                self.positions = []
                self.cash_remains = {}
        else:
            print("  📂 无历史持仓文件，初始化为空")
            self.positions = []
            self.cash_remains = {}
    
    def save(self):
        """保存持仓到 CSV 文件"""
        os.makedirs(os.path.dirname(self.position_file), exist_ok=True)
        try:
            data = [p.to_dict() for p in self.positions]
            df = pd.DataFrame(data, columns=['code', 'buy_date', 'buy_price', 'shares', 'cost_total', 'highest_price', 'slot_idx'])
            df.to_csv(self.position_file, index=False, encoding='utf-8')
            
            # 保存各分仓现金
            if self.config['strategy'].get('enable_compounding', False):
                with open(self.cash_file, 'w', encoding='utf-8') as f:
                    json.dump({'slot_cash': self.slot_cash}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  ⚠️ 保存持仓 CSV 失败: {e}")
    
    def get_positions(self) -> List[Position]:
        return self.positions
    
    def get_active_codes(self) -> List[str]:
        return [p.code for p in self.positions]
    
    def get_holding_count(self) -> int:
        return len(self.positions)
    
    def get_remaining_slots(self) -> int:
        return self.max_holdings - len(self.positions)
    
    def get_position(self, code: str) -> Optional[Position]:
        for p in self.positions:
            if p.code == code:
                return p
        return None
    
    def get_positions_market_value(self, current_prices: Dict[str, float] = None) -> float:
        if current_prices is None:
            current_prices = {}
        return sum(
            p.shares * current_prices.get(p.code, p.buy_price) 
            for p in self.positions
        )
        
    def get_cash_remain(self, code: str = None, current_prices: Dict[str, float] = None) -> float:
        if self.config['strategy'].get('enable_compounding', False):
            # 兼容复利模式下获取第一个空闲槽位资金
            active_slots = {p.slot_idx for p in self.positions}
            empty_slots = sorted(list(set(range(self.max_holdings)) - active_slots))
            if empty_slots:
                return self.slot_cash[empty_slots[0]]
            return 0.0
        else:
            if code is None:
                return sum(self.cash_remains.values()) + (self.max_holdings - len(self.positions)) * self.per_stock_capital
            if code not in self.cash_remains:
                return self.per_stock_capital
            return self.cash_remains[code]
    
    def add_position(self, code: str, price: float, shares: int, cost_total: float, slot_idx: int = -1) -> bool:
        if len(self.positions) >= self.max_holdings:
            print(f"  ⚠️ 已达最大持仓数 {self.max_holdings}，无法买入 {code}")
            return False
            
        if slot_idx == -1:
            active_slots = {p.slot_idx for p in self.positions}
            slot_idx = min(set(range(self.max_holdings)) - active_slots)
        
        position = Position(
            code=code,
            buy_date=datetime.now().strftime('%Y-%m-%d'),
            buy_price=price,
            shares=shares,
            cost_total=cost_total,
            highest_price=price,
            slot_idx=slot_idx
        )
        self.positions.append(position)
        
        # 扣减该槽位现金
        self.slot_cash[slot_idx] -= cost_total
        self.cash_remains[code] = self.slot_cash[slot_idx]
        self.save()
        return True
    
    def remove_position(self, code: str, sell_price: float = None, sell_fee: float = 0.0) -> Optional[Position]:
        for i, p in enumerate(self.positions):
            if p.code == code:
                removed = self.positions.pop(i)
                self.cash_remains.pop(code, None)
                slot_idx = removed.slot_idx
                
                # 增加该槽位现金并计入已实现盈亏 realized_pnl
                if self.config['strategy'].get('enable_compounding', False) and sell_price is not None:
                    proceeds = removed.shares * sell_price - sell_fee
                    self.slot_cash[slot_idx] += proceeds
                    pnl = proceeds - removed.cost_total
                    self.realized_pnl += pnl
                else:
                    # 非复利下，重置该槽位现金为初始分配额度 10,000 元
                    self.slot_cash[slot_idx] = float(self.per_stock_capital)
                    if sell_price is not None:
                        proceeds = removed.shares * sell_price - sell_fee
                        pnl = proceeds - removed.cost_total
                        self.realized_pnl += pnl
                        
                self.save()
                return removed
        return None
    
    def update_highest_price(self, code: str, price: float):
        p = self.get_position(code)
        if p and price > p.highest_price:
            p.highest_price = price
            self.save()
    
    def get_total_equity(self, current_prices: Dict[str, float] = None) -> float:
        total_market_value = self.get_positions_market_value(current_prices)
        if self.config['strategy'].get('enable_compounding', False):
            # 复利模式下，总权益 = 4个分仓的现金之和 + 总市值
            return sum(self.slot_cash) + total_market_value
        else:
            total_cash = sum(self.cash_remains.values()) + (self.max_holdings - len(self.positions)) * self.per_stock_capital
            return total_cash + total_market_value + self.realized_pnl
    
    def get_pnl(self, current_prices: Dict[str, float]) -> List[Dict]:
        result = []
        for p in self.positions:
            current_price = current_prices.get(p.code, p.buy_price)
            market_value = p.shares * current_price
            pnl = market_value - p.cost_total
            pnl_pct = (pnl / p.cost_total) * 100 if p.cost_total > 0 else 0
            result.append({
                'code': p.code,
                'buy_date': p.buy_date,
                'buy_price': p.buy_price,
                'shares': p.shares,
                'cost_total': p.cost_total,
                'current_price': current_price,
                'market_value': market_value,
                'pnl': round(pnl, 2),
                'pnl_pct': round(pnl_pct, 2),
                'highest_price': p.highest_price,
                'drawdown_from_high': round((current_price / p.highest_price - 1) * 100, 2) if p.highest_price > 0 else 0
            })
        return result