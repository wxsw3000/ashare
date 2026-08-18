import os
import sys
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from MagicSTG.execution.portfolio_strategies import (
    BasePortfolioStrategy,
    EqualSlotPortfolioStrategy,
    PortfolioStrategyRegistry,
    is_convertible_bond,
    calc_trade_cost_for_security
)


class TestPortfolioStrategy(unittest.TestCase):

    def test_convertible_bond_checker(self):
        self.assertTrue(is_convertible_bond('sh.113008'))
        self.assertTrue(is_convertible_bond('sz.128001'))
        self.assertFalse(is_convertible_bond('sh.600000'))
        self.assertFalse(is_convertible_bond('sz.000001'))

    def test_trade_cost_calculation(self):
        # CB: 0.005% fee, no stamp tax, no min fee
        fee_buy, cost_buy = calc_trade_cost_for_security('sh.113008', price=100.0, shares_or_bonds=10, is_buy=True)
        self.assertEqual(round(fee_buy, 4), 0.05)
        self.assertEqual(round(cost_buy, 4), 1000.05)

        fee_sell, net_sell = calc_trade_cost_for_security('sh.113008', price=100.0, shares_or_bonds=10, is_buy=False)
        self.assertEqual(round(fee_sell, 4), 0.05)
        self.assertEqual(round(net_sell, 4), 999.95)

        # Stock: 0.025% commission (min 5 CNY rule) + 0.05% sell stamp tax
        fee_stock_buy, cost_stock_buy = calc_trade_cost_for_security('sh.600000', price=10.0, shares_or_bonds=1000, is_buy=True)
        self.assertEqual(fee_stock_buy, 5.0)  # Min 5 CNY rule
        self.assertEqual(cost_stock_buy, 10005.0)

    def test_registry_lookup(self):
        stg = PortfolioStrategyRegistry.get('equal_slot', {'strategy': {'initial_capital': 200000.0}})
        self.assertIsInstance(stg, EqualSlotPortfolioStrategy)
        self.assertEqual(stg.initial_capital, 200000.0)

        stg_fallback = PortfolioStrategyRegistry.get('non_existent_strategy')
        self.assertIsInstance(stg_fallback, EqualSlotPortfolioStrategy)

    def test_equal_slot_simulation_evaluation(self):
        stg = EqualSlotPortfolioStrategy(
            name='equal_slot_test',
            config={
                'strategy': {
                    'initial_capital': 100000.0,
                    'max_holdings': 5,
                    'stop_loss_pct': -8.0,
                    'trailing_stop_pct': -5.0,
                    'execution_timing': 'T_CLOSE'  # Same day close for test simplicity
                }
            }
        )

        active_positions = {}
        slot_cash = [20000.0] * 5
        price_map = {
            ('sh.600000', '2026-08-01'): {'close': 10.0, 'open': 10.0, 'high': 10.0, 'low': 10.0},
            ('sh.600000', '2026-08-02'): {'close': 9.0, 'open': 9.0, 'high': 9.0, 'low': 9.0},     # -10% stop loss
        }
        name_map = {'sh.600000': '浦发银行'}

        # Day 1: Buy signal
        day1_recs = {'BUY': [{'code': 'sh.600000', 'price': 10.0, 'reason': 'Test Buy'}], 'SELL': []}
        active_pos, closed_trades, updated_cash, pending = stg.evaluate_day(
            d_str='2026-08-01',
            day_recs=day1_recs,
            price_map=price_map,
            name_map=name_map,
            active_positions=active_positions,
            slot_cash=slot_cash
        )

        self.assertEqual(len(active_pos), 1)
        self.assertIn(0, active_pos)
        pos = active_pos[0]
        self.assertEqual(pos['stock_code'], 'sh.600000')
        self.assertEqual(pos['shares'], 1900)  # (20000-5) / (10 * 1.00025) = 1999 -> 1900 shares

        # Day 2: Price drops to 9.0 (-10%), triggers hard stop loss (-8%)
        day2_recs = {'BUY': [], 'SELL': []}
        active_pos, closed_trades, updated_cash, pending = stg.evaluate_day(
            d_str='2026-08-02',
            day_recs=day2_recs,
            price_map=price_map,
            name_map=name_map,
            active_positions=active_pos,
            slot_cash=updated_cash
        )

        self.assertEqual(len(active_pos), 0)
        self.assertEqual(len(closed_trades), 1)
        closed = closed_trades[0]
        self.assertEqual(closed['status'], 'SOLD')
        self.assertIn('触发止损线', closed['extra_data'])


if __name__ == '__main__':
    unittest.main()
