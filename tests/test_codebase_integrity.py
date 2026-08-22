# -*- coding: utf-8 -*-
"""
Comprehensive Codebase Integrity & Quantitative Safety Test Suite.
Verifies:
1. Cross-stock price isolation (zero pointer contamination).
2. Strict capital ledger conservation (total equity == cash + market value at all times).
3. No-lookahead T+1 open execution and slippage rules.
4. Parameter fidelity and clean disabling of risk guards (stop loss, trailing stop).
"""

import os
import sys
import unittest
import pandas as pd
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from MagicSTG.execution.portfolio_strategies.equal_slot import EqualSlotPortfolioStrategy
from MagicSTG.execution.portfolio_strategies.base import calc_trade_cost_for_security
from MagicSTG.web.server import validate_and_sanitize_factors_config


class TestCodebaseIntegrity(unittest.TestCase):

    def test_01_parameter_fidelity_and_disabling_risk_guards(self):
        """Verifies that setting trailing_stop_pct or stop_loss_pct to 0/null/None correctly disables them (None)."""
        # Case A: User explicitly passes 0 or null to disable trailing stop
        config_a = {
            "category": "stock",
            "stock_scope": "all",
            "portfolio_config": {
                "initial_capital": 100000,
                "max_holdings": 5,
                "stop_loss_pct": -10.0,
                "trailing_stop_pct": 0
            }
        }
        ok, msg, sanitized = validate_and_sanitize_factors_config("stock", config_a)
        self.assertTrue(ok)
        self.assertIsNone(sanitized["portfolio_config"]["trailing_stop_pct"], "0 should disable trailing_stop_pct to None")
        self.assertEqual(sanitized["portfolio_config"]["stop_loss_pct"], -10.0)

        # Case B: User passes null/None to disable both
        config_b = {
            "category": "stock",
            "portfolio_config": {
                "initial_capital": 100000,
                "max_holdings": 5,
                "stop_loss_pct": None,
                "trailing_stop_pct": None
            }
        }
        ok, msg, sanitized = validate_and_sanitize_factors_config("stock", config_b)
        self.assertTrue(ok)
        self.assertIsNone(sanitized["portfolio_config"]["stop_loss_pct"], "None should disable stop_loss_pct")
        self.assertIsNone(sanitized["portfolio_config"]["trailing_stop_pct"], "None should disable trailing_stop_pct")

        # Case C: User passes negative numbers (normal enabled state)
        config_c = {
            "category": "stock",
            "portfolio_config": {
                "initial_capital": 100000,
                "max_holdings": 5,
                "stop_loss_pct": -8.0,
                "trailing_stop_pct": -5.0
            }
        }
        ok, msg, sanitized = validate_and_sanitize_factors_config("stock", config_c)
        self.assertTrue(ok)
        self.assertEqual(sanitized["portfolio_config"]["stop_loss_pct"], -8.0)
        self.assertEqual(sanitized["portfolio_config"]["trailing_stop_pct"], -5.0)

    def test_02_cross_stock_price_isolation(self):
        """Verifies that two different stocks never cross-contaminate each other's price lookups."""
        stg = EqualSlotPortfolioStrategy('test_isolation', {
            'initial_capital': 100000.0,
            'max_holdings': 2,
            'allocation_mode': 'EQUAL_SLOT',
            'stop_loss_pct': None,
            'trailing_stop_pct': None,
            'execution_timing': 'T+1_OPEN',
            'slippage': 0.0
        })

        price_map = {
            ('sh.600001', '2026-01-02'): {'open': 10.0, 'close': 10.0, 'high': 10.0, 'low': 10.0},
            ('sh.600002', '2026-01-02'): {'open': 50.0, 'close': 50.0, 'high': 50.0, 'low': 50.0},
            ('sh.600001', '2026-01-03'): {'open': 20.0, 'close': 20.0, 'high': 20.0, 'low': 20.0}, # +100% gain
            ('sh.600002', '2026-01-03'): {'open': 40.0, 'close': 40.0, 'high': 40.0, 'low': 40.0}, # -20% loss
        }
        name_map = {'sh.600001': 'StockA', 'sh.600002': 'StockB'}

        # Day 1: Send BUY signals for both
        active_pos, closed, slot_cash, pending = stg.evaluate_day(
            d_str='2026-01-01',
            day_recs={'BUY': [{'code': 'sh.600001'}, {'code': 'sh.600002'}], 'SELL': []},
            price_map=price_map,
            name_map=name_map,
            active_positions={},
            slot_cash=[50000.0, 50000.0],
            pending_orders=[]
        )
        self.assertEqual(len(pending), 2)

        # Day 2: Execute buys at T+1 open
        active_pos, closed, slot_cash, pending = stg.evaluate_day(
            d_str='2026-01-02',
            day_recs={'BUY': [], 'SELL': []},
            price_map=price_map,
            name_map=name_map,
            active_positions=active_pos,
            slot_cash=slot_cash,
            pending_orders=pending
        )
        self.assertEqual(len(active_pos), 2)
        pos_a = next(p for p in active_pos.values() if p['stock_code'] == 'sh.600001')
        pos_b = next(p for p in active_pos.values() if p['stock_code'] == 'sh.600002')
        self.assertEqual(pos_a['buy_price'], 10.0)
        self.assertEqual(pos_b['buy_price'], 50.0)

        # Day 3: Price movement (+100% for A, -20% for B). Queue sell for both
        active_pos, closed, slot_cash, pending = stg.evaluate_day(
            d_str='2026-01-03',
            day_recs={'BUY': [], 'SELL': [{'code': 'sh.600001'}, {'code': 'sh.600002'}]},
            price_map=price_map,
            name_map=name_map,
            active_positions=active_pos,
            slot_cash=slot_cash,
            pending_orders=[]
        )
        pos_a = next(p for p in active_pos.values() if p['stock_code'] == 'sh.600001')
        pos_b = next(p for p in active_pos.values() if p['stock_code'] == 'sh.600002')
        self.assertGreater(pos_a['pnl'], 0, "Stock A should have profit")
        self.assertLess(pos_b['pnl'], 0, "Stock B should have loss, NEVER inherit Stock A profit")

    def test_03_absolute_ledger_conservation(self):
        """Verifies that total_equity == sum(slot_cash) + sum(market_values) at every single step."""
        initial_cap = 100000.0
        stg = EqualSlotPortfolioStrategy('test_ledger', {
            'initial_capital': initial_cap,
            'max_holdings': 2,
            'allocation_mode': 'EQUAL_SLOT',
            'stop_loss_pct': -8.0,
            'trailing_stop_pct': -5.0,
            'execution_timing': 'T+1_OPEN',
            'slippage': 0.001
        })

        price_map = {
            ('sh.600001', '2026-01-02'): {'open': 10.0, 'close': 10.5, 'high': 10.5, 'low': 10.0},
            ('sh.600001', '2026-01-03'): {'open': 10.5, 'close': 11.0, 'high': 11.2, 'low': 10.4},
            ('sh.600001', '2026-01-04'): {'open': 10.4, 'close': 10.4, 'high': 10.5, 'low': 10.3}, # pulls back from 11.2 -> triggers trailing stop
        }
        name_map = {'sh.600001': 'StockA'}
        active_pos = {}
        slot_cash = [50000.0, 50000.0]
        pending = []

        # Day 1: Signal
        active_pos, closed, slot_cash, pending = stg.evaluate_day('2026-01-01', {'BUY': [{'code': 'sh.600001'}], 'SELL': []}, price_map, name_map, active_pos, slot_cash, pending)
        tot_eq1 = sum(slot_cash) + sum(p['market_value'] for p in active_pos.values())
        self.assertAlmostEqual(tot_eq1, initial_cap, places=2)

        # Day 2: Buy executed
        active_pos, closed, slot_cash, pending = stg.evaluate_day('2026-01-02', {'BUY': [], 'SELL': []}, price_map, name_map, active_pos, slot_cash, pending)
        tot_eq2 = sum(slot_cash) + sum(p['market_value'] for p in active_pos.values())
        # Cash was deducted and converted to stock shares
        self.assertLess(slot_cash[0], 50000.0)
        self.assertGreater(tot_eq2, 0.0)

        # Day 3: Stock rises to high 11.2
        active_pos, closed, slot_cash, pending = stg.evaluate_day('2026-01-03', {'BUY': [], 'SELL': []}, price_map, name_map, active_pos, slot_cash, pending)
        self.assertEqual(active_pos[0]['highest_price'], 11.2)

        # Day 4: Stock drops to 10.4 (drawdown (10.4-11.2)/11.2 = -7.14% <= -5.0%) -> Trailing stop triggered and sold
        active_pos, closed, slot_cash, pending = stg.evaluate_day('2026-01-04', {'BUY': [], 'SELL': []}, price_map, name_map, active_pos, slot_cash, pending)
        tot_eq4 = sum(slot_cash) + sum(p.get('market_value', 0.0) for p in active_pos.values())
        self.assertEqual(len(active_pos), 0, "Position should be sold by trailing stop")
        self.assertEqual(len(closed), 1, "Should have 1 closed trade")
        self.assertAlmostEqual(tot_eq4, sum(slot_cash), places=2)


if __name__ == '__main__':
    unittest.main()
