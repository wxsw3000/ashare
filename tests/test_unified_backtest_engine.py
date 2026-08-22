# -*- coding: utf-8 -*-
"""
Unit tests for the Unified Backtest Engine (MagicSTG.backtests.engine)
Verifies that run_backtest_engine uses PortfolioStrategyRegistry and PortfolioEngine core.
"""

import os
import sys
import unittest
import pandas as pd
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from MagicSTG.backtests.engine import run_backtest_engine, sanitize_json


class TestUnifiedBacktestEngine(unittest.TestCase):

    def test_sanitize_json_types(self):
        raw = {
            'str': 'hello',
            'float': 100.25,
            'nan': float('nan'),
            'inf': float('inf'),
            'date': datetime(2026, 8, 22),
            'list': [1, 2, float('nan')]
        }
        clean = sanitize_json(raw)
        self.assertEqual(clean['nan'], 0.0)
        self.assertEqual(clean['inf'], 0.0)
        self.assertEqual(clean['date'], '2026-08-22')
        self.assertEqual(clean['list'], [1, 2, 0.0])

    def test_stock_backtest_engine_execution(self):
        config = {
            'top_n': 5,
            'portfolio_config': {
                'initial_capital': 100000.0,
                'max_holdings': 5,
                'execution_timing': 'T+1_OPEN',
                'stop_loss_pct': -8.0,
                'trailing_stop_pct': -5.0
            }
        }
        res = run_backtest_engine(
            config=config,
            start_date_str="2026-01-01",
            end_date_str="2026-01-10",
            strategy_name="dynamic_factor",
            universe="csi300",
            save_to_db=False
        )

        self.assertEqual(res.get('status'), 'success')
        self.assertIn('equity_history', res)
        self.assertIn('trades', res)
        self.assertIn('sharpe_ratio', res)
        self.assertIn('win_rate', res)
        self.assertEqual(res['initial_equity'], 100000.0)

    def test_cb_backtest_engine_execution(self):
        config = {
            'category': 'convertible_bond',
            'top_n': 5,
            'max_price': 130.0,
            'portfolio_config': {
                'initial_capital': 100000.0,
                'max_holdings': 5
            }
        }
        res = run_backtest_engine(
            config=config,
            start_date_str="2026-01-01",
            end_date_str="2026-01-10",
            strategy_name="cb_double_low",
            save_to_db=False
        )

        self.assertEqual(res.get('status'), 'success')
        self.assertIn('equity_history', res)
        self.assertIn('trades', res)
        self.assertEqual(res['initial_equity'], 100000.0)


if __name__ == '__main__':
    unittest.main()
