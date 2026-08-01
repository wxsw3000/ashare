#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MagicSTG Convertible Bond Strategy Backtest Engine
可转债双低策略专用回测驱动引擎
模拟历史持仓定期轮动，计算年化收益、最大回撤、夏普比率等核心指标。
"""

import sys
import os
import argparse
import pandas as pd
import numpy as np
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

from MagicSTG.core.db import load_cb_data_db
from MagicSTG.strategies.registry import StrategyRegistry


def run_cb_backtest(
    start_date: str = "2022-01-01",
    end_date: str = "2026-07-30",
    initial_capital: float = 100000.0,
    top_n: int = 10,
    max_price: float = 130.0,
    rebalance_days: int = 5
) -> dict:
    print("=" * 70)
    print("🚀 开始可转债双低策略历史回测引擎...")
    print(f"  [回测区间]: {start_date} ~ {end_date}")
    print(f"  [初始资金]: {initial_capital:,.2f} 元 | [持仓数量]: {top_n} 只 | [轮动周期]: {rebalance_days} 天")
    print(f"  [价格上限]: {max_price:.1f} 元")
    print("=" * 70)

    # 1. 从 TiDB 加载可转债衍生指标数据
    data = load_cb_data_db(start_date=start_date, end_date=end_date)
    indicator_df = data.get('cb_daily_indicator')
    if indicator_df is None or indicator_df.empty:
        print("❌ 未获取到可转债衍生指标数据，请先确认 cb_daily_indicator 表已装载数据。")
        return {}

    # 日期转换
    indicator_df['date'] = pd.to_datetime(indicator_df['date'])
    trading_dates = sorted(indicator_df['date'].unique())

    if not trading_dates:
        print("❌ 没有找到有效的交易日期。")
        return {}

    print(f"  ✅ 成功获取 {len(trading_dates)} 个交易日的数据，开始进行逐日回测模拟...", flush=True)

    # 2. 实例化策略
    strategy_config = {
        'top_n': top_n,
        'max_price': max_price,
        'rebalance_days': rebalance_days
    }
    strategy = StrategyRegistry.get('cb_double_low', strategy_config)

    # 3. 模拟盘口与资产组合状态
    current_cash = initial_capital
    holdings = {}  # {code: {'shares': int, 'cost_price': float, 'curr_price': float}}
    portfolio_history = []
    trade_logs = []

    day_counter = 0

    for current_date in trading_dates:
        date_str = current_date.strftime('%Y-%m-%d')
        today_df = indicator_df[indicator_df['date'] == current_date]

        if today_df.empty:
            continue

        price_map = {}
        for _, row in today_df.iterrows():
            code = str(row['code'])
            price = float(row['cb_price']) if pd.notna(row['cb_price']) else None
            if price and price > 0:
                price_map[code] = price

        # 更新当前已有持仓的市值
        current_holdings_value = 0.0
        active_codes = list(holdings.keys())
        for code in active_codes:
            if code in price_map:
                holdings[code]['curr_price'] = price_map[code]
            curr_price = holdings[code]['curr_price']
            current_holdings_value += holdings[code]['shares'] * curr_price

        total_asset = current_cash + current_holdings_value

        # 判断是否到达轮动调仓日
        is_rebalance_day = (day_counter % rebalance_days == 0) or (len(holdings) == 0)

        if is_rebalance_day:
            current_holding_codes = list(holdings.keys())
            buy_signals, sell_signals = strategy.generate_signals(
                date=current_date,
                data={'cb_daily_indicator': today_df},
                current_holdings=current_holding_codes
            )

            # 1) 执行卖出离场
            for code, price, reason in sell_signals:
                if code in holdings:
                    shares = holdings[code]['shares']
                    sell_price = price if price > 0 else holdings[code]['curr_price']
                    income = shares * sell_price * (1 - 0.00005)  # 扣除万0.5佣金
                    current_cash += income
                    pnl = (sell_price - holdings[code]['cost_price']) * shares
                    trade_logs.append({
                        'date': date_str,
                        'action': 'SELL',
                        'code': code,
                        'price': sell_price,
                        'shares': shares,
                        'amount': income,
                        'pnl': pnl,
                        'reason': reason
                    })
                    del holdings[code]

            #  recalculate total_asset after sell
            current_holdings_val_after_sell = sum(h['shares'] * h['curr_price'] for h in holdings.values())
            total_asset = current_cash + current_holdings_val_after_sell

            # 2) 执行买入建仓
            if buy_signals:
                # 均分可用资金买入入榜的转债
                target_allocation = total_asset / top_n
                for code, price, reason in buy_signals:
                    if price <= 0:
                        continue
                    available_buy_cash = min(current_cash, target_allocation)
                    if available_buy_cash >= price * 10:  # 至少买10张 (手)
                        shares = int(available_buy_cash // (price * (1 + 0.00005)))
                        if shares > 0:
                            cost = shares * price * (1 + 0.00005)
                            current_cash -= cost
                            holdings[code] = {
                                'shares': shares,
                                'cost_price': price,
                                'curr_price': price
                            }
                            trade_logs.append({
                                'date': date_str,
                                'action': 'BUY',
                                'code': code,
                                'price': price,
                                'shares': shares,
                                'amount': cost,
                                'pnl': 0.0,
                                'reason': reason
                            })

        # 重新计算最新组合总资产
        current_holdings_value = sum(h['shares'] * h['curr_price'] for h in holdings.values())
        total_asset = current_cash + current_holdings_value

        portfolio_history.append({
            'date': current_date,
            'cash': current_cash,
            'holdings_val': current_holdings_value,
            'total_asset': total_asset,
            'num_holdings': len(holdings)
        })

        day_counter += 1

    # 4. 汇总计算分析回测指标
    perf_df = pd.DataFrame(portfolio_history)
    if perf_df.empty:
        print("❌ 回测过程未生成有效权益曲线。")
        return {}

    perf_df['daily_return'] = perf_df['total_asset'].pct_change().fillna(0.0)
    perf_df['cum_return'] = perf_df['total_asset'] / initial_capital - 1.0

    # 动态最大回撤计算
    perf_df['peak'] = perf_df['total_asset'].cummax()
    perf_df['drawdown'] = (perf_df['total_asset'] - perf_df['peak']) / perf_df['peak']

    total_return = perf_df['cum_return'].iloc[-1]
    num_years = max((trading_dates[-1] - trading_dates[0]).days / 365.25, 0.1)
    annual_return = (1 + total_return) ** (1 / num_years) - 1.0
    max_drawdown = perf_df['drawdown'].min()

    # 夏普比率计算 (假设无风险利率 2.0%)
    daily_rf = 0.02 / 252
    excess_returns = perf_df['daily_return'] - daily_rf
    sharpe_ratio = (excess_returns.mean() / excess_returns.std() * np.sqrt(252)) if excess_returns.std() > 0 else 0.0

    # 交易统计
    trade_df = pd.DataFrame(trade_logs)
    sell_trades = trade_df[trade_df['action'] == 'SELL'] if not trade_df.empty else pd.DataFrame()
    win_trades = sell_trades[sell_trades['pnl'] > 0] if not sell_trades.empty else pd.DataFrame()
    win_rate = (len(win_trades) / len(sell_trades) * 100) if not sell_trades.empty else 0.0

    print("\n" + "=" * 70)
    print("📊 可转债双低策略 - 回测性能报告")
    print("=" * 70)
    print(f"  累计收益率 (Total Return)   : {total_return * 100:+.2f}%")
    print(f"  年化收益率 (Annual Return)  : {annual_return * 100:+.2f}%")
    print(f"  最大回撤 (Max Drawdown)    : {max_drawdown * 100:.2f}%")
    print(f"  夏普比率 (Sharpe Ratio)    : {sharpe_ratio:.2f}")
    print(f"  胜率 (Win Rate)            : {win_rate:.1f}% ({len(win_trades)}胜 / {len(sell_trades)}平卖)")
    print(f"  期末总资产 (Final Value)    : {perf_df['total_asset'].iloc[-1]:,.2f} 元")
    print("=" * 70)

    return {
        'total_return': total_return,
        'annual_return': annual_return,
        'max_drawdown': max_drawdown,
        'sharpe_ratio': sharpe_ratio,
        'win_rate': win_rate,
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
