# -*- coding: utf-8 -*-
"""
MagicSTG Unified Core Backtest Engine
Provides a single source of truth for both CLI backtesting and Flask Web API backtesting.
"""

import gc
import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any

from MagicSTG.core.db import load_all_data_db
from MagicSTG.core.cost import calc_buy_cost, calc_sell_cost
from MagicSTG.core.limit import check_limit_up
from MagicSTG.core.db_writer import save_backtest_result
from MagicSTG.strategies.registry import StrategyRegistry
from MagicSTG.execution.position_manager import InMemoryPositionManager
from MagicSTG.execution.decisions import DecisionMaker


def run_backtest_engine(
    config: Dict[str, Any],
    start_date_str: str,
    end_date_str: str,
    strategy_name: str = "dynamic_factor",
    universe: str = "all",
    save_to_db: bool = False,
    all_data: Optional[Dict[str, pd.DataFrame]] = None
) -> Dict[str, Any]:
    """
    Executes backtest loop with specified configuration. Supports both Stock and CB strategies.
    """
    # 区分可转债策略与股票策略
    if strategy_name == 'cb_double_low' or strategy_name.startswith('cb_') or config.get('category') == 'convertible_bond':
        try:
            from MagicSTG.run_cb_backtest import run_cb_backtest

            top_n = int(config.get('top_n', 10))
            max_price = float(config.get('max_price', 130.0))

            cb_res = run_cb_backtest(
                start_date=start_date_str,
                end_date=end_date_str,
                initial_capital=100000.0,
                top_n=top_n,
                max_price=max_price,
                rebalance_days=5
            )

            if not cb_res or 'perf_df' not in cb_res or cb_res['perf_df'].empty:
                return {'status': 'error', 'message': '可转债策略计算未生成有效回测数据'}

            perf_df = cb_res['perf_df']
            trade_df = cb_res.get('trade_df', pd.DataFrame())

            equity_history = []
            for _, row in perf_df.iterrows():
                dt_str = row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])
                equity_history.append({
                    'date': dt_str,
                    'equity': round(float(row['total_asset']), 2),
                    'cash': round(float(row['cash']), 2),
                    'positions_count': int(row['num_holdings'])
                })

            trade_log = []
            if not trade_df.empty:
                for _, row in trade_df.iterrows():
                    trade_log.append({
                        'trade_date': str(row['date']),
                        'stock_code': str(row['code']),
                        'action': str(row['action']),
                        'price': round(float(row['price']), 2),
                        'shares': int(row['shares']),
                        'amount': round(float(row['amount']), 2),
                        'fee': 0.0,
                        'pnl': round(float(row.get('pnl', 0.0)), 2),
                        'pnl_pct': 0.0,
                        'reason': str(row['reason'])
                    })

            initial_eq = 100000.0
            final_eq = float(perf_df['total_asset'].iloc[-1])
            tot_ret = float(cb_res.get('total_return', 0.0)) * 100
            ann_ret = float(cb_res.get('annual_return', 0.0)) * 100
            max_dd = float(cb_res.get('max_drawdown', 0.0)) * 100

            result_summary = {
                'status': 'success',
                'strategy_name': strategy_name,
                'date_range_start': start_date_str,
                'date_range_end': end_date_str,
                'initial_equity': initial_eq,
                'final_equity': round(final_eq, 2),
                'total_return': round(tot_ret, 2),
                'annual_return': round(ann_ret, 2),
                'max_drawdown': round(max_dd, 2),
                'win_rate': round(float(cb_res.get('win_rate', 0.0)), 2),
                'total_buys': len([t for t in trade_log if t['action'] == 'BUY']),
                'total_sells': len([t for t in trade_log if t['action'] == 'SELL']),
                'total_fees': 0.0,
                'equity_history': equity_history,
                'trades': trade_log
            }

            if save_to_db:
                try:
                    save_backtest_result(strategy_name, result_summary)
                except Exception as e:
                    print(f"  [BacktestEngine] ⚠️ Persistent DB saving failed: {e}", flush=True)

            return result_summary
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {'status': 'error', 'message': f'可转债策略回测执行失败: {str(e)}'}
    if all_data is None:
        limit_to_csi300 = (universe == 'csi300')
        print(f"[BacktestEngine] Loading data for range {start_date_str} ~ {end_date_str} (universe={universe})...", flush=True)
        all_data = load_all_data_db(start_date=start_date_str, end_date=end_date_str, limit_to_csi300=limit_to_csi300)

    if not all_data:
        return {'status': 'error', 'message': '未加载到任何 K 线数据'}

    # Instantiate Strategy
    strategy = StrategyRegistry.get(strategy_name, config)

    # Pre-compute technical indicators
    processed_data = {}
    for code, df in all_data.items():
        if hasattr(strategy, 'compute_indicators'):
            processed_data[code] = strategy.compute_indicators(df)
        else:
            processed_data[code] = df

    # Prepare trading dates
    start_date = pd.Timestamp(start_date_str)
    end_date = pd.Timestamp(end_date_str)
    all_dates = sorted(set().union(*[set(df.index) for df in all_data.values()]))
    backtest_dates = [d for d in all_dates if start_date <= d <= end_date]

    if not backtest_dates:
        return {'status': 'error', 'message': '所选时间段内没有交易日期'}

    # Initialize Position Manager & Decision Maker
    position_manager = InMemoryPositionManager(config)
    max_holdings = config.get('strategy', {}).get('max_holdings', 5)
    per_stock_capital = config.get('strategy', {}).get('per_stock_capital', 20000.0)
    initial_equity = max_holdings * per_stock_capital

    position_manager.slot_cash = [float(per_stock_capital)] * max_holdings
    position_manager.positions = []
    position_manager.realized_pnl = 0.0

    decision_maker = DecisionMaker(config, position_manager, strategy)

    trade_log = []
    equity_history = []

    for date in backtest_dates:
        # 1. Update highest price for holdings if applicable
        for pos in position_manager.get_positions():
            code = pos.code
            df = processed_data[code]
            if date in df.index:
                high_price = df.loc[date, 'high']
                if not pd.isna(high_price) and hasattr(pos, 'highest_price'):
                    pos.highest_price = max(getattr(pos, 'highest_price', pos.buy_price), high_price)

        # 2. Make decisions
        decisions = decision_maker.make_decisions(all_data, date)

        # 3. Execute Sell decisions
        for sell in decisions.sells:
            code = sell['code']
            pos = position_manager.get_position(code)
            if not pos:
                continue
            sell_amount = sell['shares'] * sell['price']
            fee = calc_sell_cost(sell_amount)
            net_sell = sell_amount - fee
            pnl = net_sell - pos.cost_total
            pnl_pct = (pnl / pos.cost_total * 100) if pos.cost_total > 0 else 0.0

            position_manager.remove_position(code, sell['price'], fee)

            trade_log.append({
                'trade_date': date.strftime('%Y-%m-%d'),
                'stock_code': code,
                'action': 'SELL',
                'price': sell['price'],
                'shares': sell['shares'],
                'amount': round(sell_amount, 2),
                'fee': round(fee, 2),
                'pnl': round(pnl, 2),
                'pnl_pct': round(pnl_pct, 2),
                'reason': sell['reason']
            })

        # 4. Execute Buy decisions
        for buy in decisions.buys:
            code = buy['code']
            position_manager.add_position(code, buy['price'], buy['shares'], buy['total'], buy.get('slot_idx', -1))

            trade_log.append({
                'trade_date': date.strftime('%Y-%m-%d'),
                'stock_code': code,
                'action': 'BUY',
                'price': buy['price'],
                'shares': buy['shares'],
                'amount': buy['amount'],
                'fee': buy['fee'],
                'pnl': 0.0,
                'pnl_pct': 0.0,
                'reason': '买入信号'
            })

        # 5. Calculate daily equity
        current_prices = {}
        for pos in position_manager.get_positions():
            code = pos.code
            df = processed_data[code]
            if date in df.index:
                current_prices[code] = df.loc[date, 'close']
            else:
                current_prices[code] = pos.buy_price

        total_equity = position_manager.get_total_equity(current_prices)
        equity_history.append({
            'date': date.strftime('%Y-%m-%d'),
            'equity': round(total_equity, 2),
            'cash': round(sum(position_manager.slot_cash), 2),
            'positions_count': len(position_manager.get_positions())
        })

    # Performance Metrics
    eq_df = pd.DataFrame(equity_history)
    eq_df['cummax'] = eq_df['equity'].cummax()
    eq_df['drawdown'] = (eq_df['equity'] - eq_df['cummax']) / eq_df['cummax']

    final_equity = eq_df['equity'].iloc[-1]
    total_return = (final_equity - initial_equity) / initial_equity * 100
    max_drawdown = eq_df['drawdown'].min() * 100

    trading_days = len(backtest_dates)
    annual_return = ((1 + total_return / 100) ** (252.0 / max(trading_days, 1)) - 1) * 100 if trading_days > 0 else 0.0

    sells = [t for t in trade_log if t['action'] == 'SELL']
    buys = [t for t in trade_log if t['action'] == 'BUY']
    wins = [t for t in sells if t['pnl'] > 0]
    win_rate = (len(wins) / len(sells) * 100) if sells else 0.0
    total_fees = sum(t['fee'] for t in trade_log)

    result_summary = {
        'status': 'success',
        'strategy_name': strategy_name,
        'date_range_start': start_date_str,
        'date_range_end': end_date_str,
        'initial_equity': initial_equity,
        'final_equity': round(final_equity, 2),
        'total_return': round(total_return, 2),
        'annual_return': round(annual_return, 2),
        'max_drawdown': round(max_drawdown, 2),
        'win_rate': round(win_rate, 2),
        'total_buys': len(buys),
        'total_sells': len(sells),
        'total_fees': round(total_fees, 2),
        'equity_history': equity_history,
        'trades': trade_log
    }

    if save_to_db:
        try:
            save_backtest_result(strategy_name, result_summary)
        except Exception as e:
            print(f"  [BacktestEngine] ⚠️ Persistent DB saving failed: {e}", flush=True)

    if 'all_data' in locals():
        del all_data
    if 'processed_data' in locals():
        del processed_data
    if 'eq_df' in locals():
        del eq_df
    gc.collect()

    return result_summary
