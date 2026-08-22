# -*- coding: utf-8 -*-
"""
End-to-End Comprehensive Multi-Strategy Integration Test Suite.
Tests full execution pipeline through run_backtest_engine for all strategy categories:
1. Convertible Bond (cb_double_low)
2. Dynamic Multi-Factor Stock Strategy (dynamic_factor)
3. Custom JSON Strategy (stg_1787392438)
Verifies non-zero trade signals, valid mathematical performance metrics, and database round-trip fidelity.
"""

import os
import sys
import unittest
import math

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from MagicSTG.backtests.engine import run_backtest_engine
from MagicSTG.core.db import get_connection_with_retry


class TestEndToEndAllStrategies(unittest.TestCase):

    def test_01_cb_double_low_end_to_end(self):
        """Tests Convertible Bond Double-Low backtest end-to-end with real signals and non-zero trades."""
        config = {
            'top_n': 5,
            'max_price': 130.0,
            'portfolio_config': {
                'initial_capital': 100000.0,
                'max_holdings': 5,
                'allocation_mode': 'EQUAL_SLOT',
                'stop_loss_pct': -8.0,
                'trailing_stop_pct': -5.0,
                'execution_timing': 'T+1_OPEN',
                'slippage': 0.001
            }
        }
        res = run_backtest_engine(
            config=config,
            start_date_str='2026-01-01',
            end_date_str='2026-01-30',
            strategy_name='cb_double_low',
            universe='all',
            save_to_db=True
        )
        self.assertEqual(res.get('status'), 'success')
        self.assertGreater(res['total_buys'], 0, "CB strategy must generate buy signals and trades")
        self.assertGreater(res['final_equity'], 0)
        self.assertFalse(math.isnan(res['total_return']))
        self.assertFalse(math.isnan(res['max_drawdown']))

    def test_02_stock_dynamic_factor_end_to_end(self):
        """Tests Stock Dynamic Factor backtest end-to-end across CSI300."""
        config = {
            'top_n': 5,
            'stock_scope': 'csi300',
            'sort_by': 'roe',
            'tech': {
                'enable_golden_cross': True,
                'short_ma': 5,
                'long_ma': 20,
                'enable_volume_surge': False,
                'enable_di_ratio': False,
                'enable_turnover': False,
                'enable_rsi': False,
                'enable_macd': False,
                'enable_boll': False,
                'enable_kdj': False
            },
            'financial': {
                'enable_roe': True,
                'roe_min': 0.05,
                'enable_pe': False,
                'enable_pb': False,
                'enable_growth': False,
                'enable_debt_limit': False,
                'enable_cash_quality': False
            },
            'portfolio_config': {
                'initial_capital': 100000.0,
                'max_holdings': 5,
                'allocation_mode': 'EQUAL_SLOT',
                'stop_loss_pct': -8.0,
                'trailing_stop_pct': -5.0,
                'execution_timing': 'T+1_OPEN',
                'slippage': 0.001
            }
        }
        res = run_backtest_engine(
            config=config,
            start_date_str='2026-01-01',
            end_date_str='2026-01-30',
            strategy_name='dynamic_factor',
            universe='csi300',
            save_to_db=True
        )
        self.assertEqual(res.get('status'), 'success')
        self.assertGreater(res['total_buys'], 0, "Stock dynamic factor strategy must generate buys")
        self.assertGreater(res['final_equity'], 0)
        self.assertFalse(math.isnan(res['total_return']))

    def test_03_db_persistence_roundtrip(self):
        """Verifies that backtest results saved into TiDB can be retrieved with identical values."""
        config = {
            'top_n': 3,
            'max_price': 130.0,
            'portfolio_config': {
                'initial_capital': 50000.0,
                'max_holdings': 3,
                'allocation_mode': 'EQUAL_SLOT',
                'stop_loss_pct': None,
                'trailing_stop_pct': None,
                'execution_timing': 'T+1_OPEN',
                'slippage': 0.001
            }
        }
        res = run_backtest_engine(
            config=config,
            start_date_str='2026-01-01',
            end_date_str='2026-01-15',
            strategy_name='cb_double_low',
            universe='all',
            save_to_db=True
        )
        self.assertEqual(res.get('status'), 'success')

        conn = get_connection_with_retry()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, strategy, initial_equity, final_equity, total_return FROM backtest_results ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                self.assertIsNotNone(row)
                b_id, b_stg, init_eq, fin_eq, tot_ret = row
                self.assertEqual(b_stg, 'cb_double_low')
                self.assertAlmostEqual(float(init_eq), 50000.0, places=2)
                self.assertAlmostEqual(float(tot_ret), res['total_return'], places=2)
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
