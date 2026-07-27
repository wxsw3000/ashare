# -*- coding: utf-8 -*-
"""
MagicSTG Unified CLI Backtest Runner
Upgraded CLI backtest entrypoint supporting dynamic multi-factor & legacy strategies.
"""

import os
import sys
import argparse
from typing import Optional

# Ensure project root is in sys.path
MAGICSTG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(MAGICSTG_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from MagicSTG.config import load_yaml_config
from MagicSTG.backtests.engine import run_backtest_engine


def main(args_list: Optional[list] = None):
    parser = argparse.ArgumentParser(description="MagicSTG Quantitative Strategy CLI Backtester")
    parser.add_argument("--strategy", type=str, default="dynamic_factor", help="Strategy name: dynamic_factor, price, pe, roe")
    parser.add_argument("--start", type=str, default="2024-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default="2026-06-30", help="End date YYYY-MM-DD")
    parser.add_argument("--universe", type=str, default="all", choices=["all", "csi300"], help="Stock universe: all or csi300")
    parser.add_argument("--save-db", action="store_true", help="Save backtest results to TiDB database")

    args = parser.parse_args(args_list)

    config = load_yaml_config()
    # Apply strategy specific overrides
    stg_name = args.strategy.lower()
    if stg_name in ['price', 'pe', 'roe']:
        config['strategy']['name'] = f"{stg_name.upper()}优先策略"

    print("=" * 70)
    print("  [*] MagicSTG CLI 回测引擎启动")
    print(f"  [*] 策略名称: {args.strategy}")
    print(f"  [*] 回测区间: {args.start} ~ {args.end}")
    print(f"  [*] 股票池:   {args.universe.upper()}")
    print("=" * 70)

    res = run_backtest_engine(
        config=config,
        start_date_str=args.start,
        end_date_str=args.end,
        strategy_name=args.strategy,
        universe=args.universe,
        save_to_db=args.save_db
    )

    if res.get('status') == 'error':
        print(f"\n[ERROR] 回测失败: {res.get('message')}")
        return

    print("\n" + "=" * 70)
    print("  [RESULT] 策略回测统计结果")
    print("=" * 70)
    print(f"  初始资金:     {res['initial_equity']:>14,.2f} 元")
    print(f"  期末总资产:   {res['final_equity']:>14,.2f} 元")
    print(f"  累计收益率:   {res['total_return']:>14.2f} %")
    print(f"  年化收益率:   {res['annual_return']:>14.2f} %")
    print(f"  最大回撤:     {res['max_drawdown']:>14.2f} %")
    print(f"  胜率:         {res['win_rate']:>14.2f} % ({res['win_rate']*res['total_sells']/100:.0f}胜 / {res['total_sells']}笔平仓)")
    print(f"  总交易笔数:   {res['total_buys'] + res['total_sells']:>14} 笔 (买入:{res['total_buys']}, 卖出:{res['total_sells']})")
    print(f"  手续费总计:   {res['total_fees']:>14,.2f} 元")
    print("=" * 70)


if __name__ == '__main__':
    main()
