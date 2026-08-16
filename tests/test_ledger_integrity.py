# -*- coding: utf-8 -*-
"""
MagicSTG Ledger Integrity Automated Unit Test
Verifies capital conservation:
1. Cash deduction on position buy
2. Cash credit & realized PnL on position sell
3. Total equity accounting precision
"""

import unittest
import sys
import os

# Align project root
MAGICSTG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MAGICSTG_DIR not in sys.path:
    sys.path.insert(0, MAGICSTG_DIR)

from MagicSTG.execution.position_manager import InMemoryPositionManager

class TestLedgerIntegrity(unittest.TestCase):

    def setUp(self):
        self.config = {
            'strategy': {
                'max_holdings': 5,
                'per_stock_capital': 20000.0,
                'enable_compounding': True
            }
        }
        self.pm = InMemoryPositionManager(self.config)

    def test_initial_balance(self):
        """测试初始资金配置准确性"""
        total_cash = sum(self.pm.slot_cash)
        self.assertEqual(total_cash, 100000.0)
        self.assertEqual(len(self.pm.get_positions()), 0)

    def test_buy_cash_deduction(self):
        """测试建仓买入时现金严格扣减与资产平衡"""
        buy_price = 10.0
        shares = 1000
        cost_total = 10000.0  # 假设总花费10000
        slot_idx = 0

        # 执行买入
        success = self.pm.add_position('sh.600000', buy_price, shares, cost_total, slot_idx)
        self.assertTrue(success)

        # 槽位现金必须严格从 20,000 减少到 10,000
        self.assertEqual(self.pm.slot_cash[0], 10000.0)
        self.assertEqual(sum(self.pm.slot_cash), 90000.0)

        # 盘后按买入价结算，总资产必须完全等于初始 100,000
        current_prices = {'sh.600000': 10.0}
        total_equity = self.pm.get_total_equity(current_prices)
        self.assertEqual(total_equity, 100000.0)

    def test_sell_profit_accumulation(self):
        """测试平仓卖出时现金回收与真实盈亏计算"""
        # 1. 先买入
        self.pm.add_position('sh.600000', 10.0, 1000, 10000.0, slot_idx=0)
        
        # 2. 卖出 (价格 15.0，总回收 15,000，扣规费 50 = 净回收 14,950)
        sell_price = 15.0
        fee = 50.0
        pos = self.pm.remove_position('sh.600000', sell_price, fee)
        
        self.assertIsNotNone(pos)
        # 0号槽位现金应从 10,000 增加净回收 14,950 变为 24,950
        self.assertEqual(self.pm.slot_cash[0], 24950.0)
        
        # 实现盈亏应为 14,950 - 10,000 = 4,950
        self.assertEqual(self.pm.realized_pnl, 4950.0)
        
        # 空仓时总资产必须精密等于 104,950.0
        current_prices = {}
        total_equity = self.pm.get_total_equity(current_prices)
        self.assertEqual(total_equity, 104950.0)

if __name__ == '__main__':
    unittest.main()
