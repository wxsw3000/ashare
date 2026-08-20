# -*- coding: utf-8 -*-
"""
Unit tests for Task-Oriented Portfolio Engine in MagicSTG.
Verifies task creation, performance metrics calculation, trade log tracking, and cascading deletion.
"""

import os
import sys
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from MagicSTG.execution.portfolio_engine import (
    PortfolioEngine,
    calculate_performance_metrics,
    init_portfolio_db
)
from MagicSTG.db import get_connection_with_retry


class TestPortfolioTasks(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_portfolio_db()

    def test_01_metrics_calculation(self):
        """Tests quantitative metrics math (Return, Sharpe, Drawdown, Win Rate)."""
        daily_history = [
            {'date': '2026-01-01', 'total_equity': 100000.0},
            {'date': '2026-01-02', 'total_equity': 102000.0},
            {'date': '2026-01-03', 'total_equity': 101000.0},
            {'date': '2026-01-04', 'total_equity': 105000.0},
        ]
        closed_trades = [
            {'pnl': 1000.0},
            {'pnl': -500.0},
            {'pnl': 2000.0}
        ]
        metrics = calculate_performance_metrics(daily_history, closed_trades, initial_capital=100000.0)
        
        self.assertEqual(metrics['cum_return'], 5.0)
        self.assertGreater(metrics['win_rate'], 66.0)
        self.assertGreaterEqual(metrics['max_drawdown'], 0.98)
        self.assertIsNotNone(metrics['sharpe_ratio'])

    def test_02_task_lifecycle_and_cascading_delete(self):
        """Tests full task creation, query, sync, and cascading deletion."""
        task_name = "单元测试实盘任务"
        strategy_code = "cb_double_low"
        initial_capital = 150000.0

        # 1. Create task
        task_detail = PortfolioEngine.create_task(
            task_name=task_name,
            strategy_code=strategy_code,
            initial_capital=initial_capital
        )
        task_id = task_detail.get('task_id')
        self.assertIsNotNone(task_id)
        self.assertTrue(task_id.startswith("task_"))

        # 2. Query task detail
        queried = PortfolioEngine.get_task_detail(task_id)
        self.assertEqual(queried['task_name'], task_name)
        self.assertEqual(queried['initial_capital'], initial_capital)
        self.assertIn('active_positions', queried)
        self.assertIn('trade_logs', queried)

        # 3. Verify task appears in task list
        all_tasks = PortfolioEngine.list_tasks()
        task_ids = [t['task_id'] for t in all_tasks]
        self.assertIn(task_id, task_ids)

        # 4. Cascading deletion
        success = PortfolioEngine.delete_task(task_id)
        self.assertTrue(success)

        # 5. Verify database wiped for this task_id
        conn = get_connection_with_retry()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM portfolio_tasks WHERE task_id = %s", (task_id,))
                self.assertEqual(cur.fetchone()[0], 0)

                cur.execute("SELECT COUNT(*) FROM positions WHERE task_id = %s", (task_id,))
                self.assertEqual(cur.fetchone()[0], 0)

                cur.execute("SELECT COUNT(*) FROM portfolio_daily_history WHERE task_id = %s", (task_id,))
                self.assertEqual(cur.fetchone()[0], 0)

                cur.execute("SELECT COUNT(*) FROM portfolio_trade_logs WHERE task_id = %s", (task_id,))
                self.assertEqual(cur.fetchone()[0], 0)
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
