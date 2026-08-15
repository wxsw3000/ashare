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
    parser.add_argument("--strategy", type=str, default="dynamic_factor", help="Strategy name/ID")
    parser.add_argument("--start", type=str, default="2024-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default="2026-06-30", help="End date YYYY-MM-DD")
    parser.add_argument("--universe", type=str, default="all", choices=["all", "csi300"], help="Stock universe: all or csi300")
    parser.add_argument("--capital", type=float, default=100000.0, help="Initial capital in RMB")
    parser.add_argument("--top-n", type=int, default=10, help="Top N holdings count")
    parser.add_argument("--save-db", action="store_true", default=True, help="Save backtest results to TiDB database")

    args = parser.parse_args(args_list)

    config = load_yaml_config()
    stg_name = args.strategy

    # 如果是自定义策略 ID，尝试从 TiDB 读取并加载其因素配置
    try:
        from MagicSTG.core.db import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT factors_config FROM custom_strategies WHERE strategy_id = %s OR id = %s", (stg_name, stg_name))
        row = cursor.fetchone()
        if row and row[0]:
            import json
            custom_cfg = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            if isinstance(custom_cfg, dict):
                config.update(custom_cfg)
        conn.close()
    except Exception as db_err:
        print(f"  [*] 读取 TiDB 策略因子配置提示: {db_err}")

    print("=" * 70)
    print("  [*] MagicSTG CLI 回测引擎启动 (GitHub Actions Node)")
    print(f"  [*] 策略代码: {args.strategy}")
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
