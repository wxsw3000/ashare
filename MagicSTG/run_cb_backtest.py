#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MagicSTG Convertible Bond Strategy Backtest Engine
可转债双低策略专用回测驱动引擎 (Delegates directly to Unified Backtest Engine)
"""

import sys
import os
import argparse
import pandas as pd
from datetime import datetime

# 自动定位项目根目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = SCRIPT_DIR
while PROJECT_ROOT and os.path.dirname(PROJECT_ROOT) != PROJECT_ROOT:
    if os.path.exists(os.path.join(PROJECT_ROOT, 'MagicSTG')):
        if PROJECT_ROOT not in sys.path:
            sys.path.insert(0, PROJECT_ROOT)
        break
    PROJECT_ROOT = os.path.dirname(PROJECT_ROOT)

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from MagicSTG.backtests.engine import run_backtest_engine


def run_cb_backtest(
    start_date: str = "2022-01-01",
    end_date: str = "2026-07-30",
    initial_capital: float = 100000.0,
    top_n: int = 10,
    max_price: float = 130.0,
    rebalance_days: int = 5
) -> dict:
    print("=" * 70)
    print("🚀 开始可转债双低策略历史回测引擎 (统一引擎核心)...")
    print(f"  [回测区间]: {start_date} ~ {end_date}")
    print(f"  [初始资金]: {initial_capital:,.2f} 元 | [持仓数量]: {top_n} 只 | [轮动周期]: {rebalance_days} 天")
    print(f"  [价格上限]: {max_price:.1f} 元")
    print("=" * 70)

    config = {
        'category': 'convertible_bond',
        'top_n': top_n,
        'max_price': max_price,
        'rebalance_days': rebalance_days,
        'portfolio_config': {
            'initial_capital': initial_capital,
            'max_holdings': top_n
        }
    }

    res = run_backtest_engine(
        config=config,
        start_date_str=start_date,
        end_date_str=end_date,
        strategy_name='cb_double_low',
        save_to_db=True
    )

    if not res or res.get('status') == 'error':
        print(f"❌ 可转债回测失败: {res.get('message') if res else '未知错误'}")
        return {}

    equity_hist = res.get('equity_history', [])
    trades_list = res.get('trades', [])

    perf_df = pd.DataFrame(equity_hist)
    if not perf_df.empty:
        perf_df.rename(columns={'equity': 'total_asset', 'positions_count': 'num_holdings'}, inplace=True)
        if 'date' in perf_df.columns:
            perf_df['date'] = pd.to_datetime(perf_df['date'])

    trade_df = pd.DataFrame(trades_list)
    if not trade_df.empty and 'trade_date' in trade_df.columns:
        trade_df.rename(columns={'trade_date': 'date', 'stock_code': 'code'}, inplace=True)

    print("\n" + "=" * 70)
    print("📊 可转债双低策略 - 回测性能报告 (统一 Portfolio Engine 核心)")
    print("=" * 70)
    print(f"  累计收益率 (Total Return)   : {res.get('total_return', 0.0):+.2f}%")
    print(f"  年化收益率 (Annual Return)  : {res.get('annual_return', 0.0):+.2f}%")
    print(f"  最大回撤 (Max Drawdown)    : {res.get('max_drawdown', 0.0):.2f}%")
    print(f"  夏普比率 (Sharpe Ratio)    : {res.get('sharpe_ratio', 0.0):.2f}")
    print(f"  胜率 (Win Rate)            : {res.get('win_rate', 0.0):.1f}% ({res.get('total_sells', 0)} 笔卖出平仓)")
    print(f"  期末总资产 (Final Value)    : {res.get('final_equity', 0.0):,.2f} 元")
    print("=" * 70)

    return {
        'total_return': res.get('total_return', 0.0) / 100.0,
        'annual_return': res.get('annual_return', 0.0) / 100.0,
        'max_drawdown': res.get('max_drawdown', 0.0) / 100.0,
        'sharpe_ratio': res.get('sharpe_ratio', 0.0),
        'win_rate': res.get('win_rate', 0.0),
        'perf_df': perf_df,
        'trade_df': trade_df
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='可转债双低策略回测引擎')
    parser.add_argument('--start', type=str, default='2022-01-01', help='回测起始日期')
    parser.add_argument('--end', type=str, default='2026-07-30', help='回测结束日期')
    parser.add_argument('--capital', type=float, default=100000.0, help='初始资金')
    parser.add_argument('--top', type=int, default=10, help='持仓转债数量')
    parser.add_argument('--max-price', type=float, default=130.0, help='最高价格限制')
    parser.add_argument('--rebalance', type=int, default=5, help='轮动间隔天数')

    args = parser.parse_args()

    run_cb_backtest(
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        top_n=args.top,
        max_price=args.max_price,
        rebalance_days=args.rebalance
    )
