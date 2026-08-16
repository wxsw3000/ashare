# -*- coding: utf-8 -*-
"""
MagicSTG Position Manager & Backtest Portfolio Tracking Engine
"""

import os
import json
import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, asdict

from MagicSTG.config import MAGICSTG_DIR, PROJECT_ROOT


@dataclass
class Position:
    """Single stock position model."""
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
    """Manages holding positions, cash allocation, and portfolio backtests."""

    def __init__(self, config: dict):
        self.config = config
        position_file = config.get('paths', {}).get('position_file', 'positions/position.csv')
        if not os.path.isabs(position_file):
            position_file = os.path.join(MAGICSTG_DIR, position_file)
        self.position_file = position_file
        self.max_holdings = config.get('strategy', {}).get('max_holdings', 5)
        self.per_stock_capital = config.get('strategy', {}).get('per_stock_capital', 20000.0)
        self.positions: List[Position] = []
        self.cash_remains: Dict[str, float] = {}
        self.cash_file = self.position_file.replace('.csv', '_cash.json')
        self.slot_cash = [float(self.per_stock_capital)] * self.max_holdings
        self.realized_pnl = 0.0
        self._load()

    def _load(self):
        """Loads positions and cash state from local files if available."""
        self.positions = []
        self.cash_remains = {}
        self.realized_pnl = 0.0

        if self.config.get('strategy', {}).get('enable_compounding', False):
            if os.path.exists(self.cash_file):
                try:
                    with open(self.cash_file, 'r', encoding='utf-8') as f:
                        cash_data = json.load(f)
                        if isinstance(cash_data, list) and len(cash_data) == self.max_holdings:
                            self.slot_cash = [float(x) for x in cash_data]
                except Exception as e:
                    print(f"  [WARN] Failed to load cash state file: {e}")

        if os.path.exists(self.position_file):
            try:
                df = pd.read_csv(self.position_file)
                for _, row in df.iterrows():
                    slot_idx = int(row.get('slot_idx', -1)) if 'slot_idx' in row and not pd.isna(row['slot_idx']) else -1
                    pos = Position(
                        code=str(row['code']),
                        buy_date=str(row['buy_date']),
                        buy_price=float(row['buy_price']),
                        shares=int(row['shares']),
                        cost_total=float(row['cost_total']),
                        highest_price=float(row.get('highest_price', row['buy_price'])),
                        slot_idx=slot_idx
                    )
                    self.positions.append(pos)
            except Exception as e:
                print(f"  [WARN] Failed to load position file {self.position_file}: {e}")

    def save(self):
        """Saves current positions and cash states."""
        os.makedirs(os.path.dirname(self.position_file), exist_ok=True)
        data = [p.to_dict() for p in self.positions]
        df = pd.DataFrame(data)
        df.to_csv(self.position_file, index=False)

        if self.config.get('strategy', {}).get('enable_compounding', False):
            with open(self.cash_file, 'w', encoding='utf-8') as f:
                json.dump(self.slot_cash, f)

    def get_positions(self) -> List[Position]:
        return self.positions

    def get_position(self, code: str) -> Optional[Position]:
        for p in self.positions:
            if p.code == code:
                return p
        return None

    def add_position(self, code: str, buy_price: float, shares: int, cost_total: float, slot_idx: int = -1) -> bool:
        if len(self.positions) >= self.max_holdings:
            return False
        buy_date = datetime.now().strftime('%Y-%m-%d')
        pos = Position(
            code=code,
            buy_date=buy_date,
            buy_price=buy_price,
            shares=shares,
            cost_total=cost_total,
            highest_price=buy_price,
            slot_idx=slot_idx
        )
        self.positions.append(pos)
        if slot_idx >= 0 and slot_idx < self.max_holdings:
            self.slot_cash[slot_idx] -= cost_total
            if self.slot_cash[slot_idx] < 0:
                self.slot_cash[slot_idx] = 0.0
        self.save()
        return True

    def remove_position(self, code: str, sell_price: float, fee: float = 0.0) -> Optional[Position]:
        pos = self.get_position(code)
        if pos:
            self.positions.remove(pos)
            proceeds = pos.shares * sell_price - fee
            pnl = proceeds - pos.cost_total
            self.realized_pnl += pnl
            if pos.slot_idx >= 0 and pos.slot_idx < self.max_holdings:
                if self.config.get('strategy', {}).get('enable_compounding', False):
                    self.slot_cash[pos.slot_idx] += proceeds
                else:
                    self.slot_cash[pos.slot_idx] = float(self.per_stock_capital)
            self.save()
            return pos
        return None

    def get_total_equity(self, current_prices: Dict[str, float]) -> float:
        market_value = 0.0
        for p in self.positions:
            price = current_prices.get(p.code, p.buy_price)
            market_value += p.shares * price
        total_cash = sum(self.slot_cash)
        return market_value + total_cash

    def get_pnl(self, current_prices: Dict[str, float]) -> List[Dict[str, Any]]:
        result = []
        for p in self.positions:
            curr_price = current_prices.get(p.code, p.buy_price)
            mkt_val = p.shares * curr_price
            pnl = mkt_val - p.cost_total
            pnl_pct = (pnl / p.cost_total) * 100 if p.cost_total > 0 else 0.0
            result.append({
                'code': p.code,
                'buy_date': p.buy_date,
                'buy_price': p.buy_price,
                'current_price': curr_price,
                'shares': p.shares,
                'market_value': round(mkt_val, 2),
                'pnl': round(pnl, 2),
                'pnl_pct': round(pnl_pct, 2)
            })
        return result


class InMemoryPositionManager(PositionManager):
    """In-memory PositionManager for dynamic web backtests without file I/O overhead."""

    def __init__(self, config: dict):
        self.config = config
        self.max_holdings = config.get('strategy', {}).get('max_holdings', 5)
        self.per_stock_capital = config.get('strategy', {}).get('per_stock_capital', 20000.0)
        self.positions: List[Position] = []
        self.cash_remains: Dict[str, float] = {}
        self.slot_cash = [float(self.per_stock_capital)] * self.max_holdings
        self.realized_pnl = 0.0

    def _load(self):
        pass

    def save(self):
        pass
