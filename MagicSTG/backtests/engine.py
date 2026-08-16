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

from MagicSTG.core.db import load_all_data_db, load_all_stock_codes_db, get_trading_dates_db
from MagicSTG.core.cost import calc_buy_cost, calc_sell_cost
from MagicSTG.core.limit import check_limit_up
from MagicSTG.core.db_writer import save_backtest_result
from MagicSTG.strategies.registry import StrategyRegistry
from MagicSTG.execution.position_manager import InMemoryPositionManager
def sanitize_json(obj):
    if isinstance(obj, dict):
        return {k: sanitize_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [sanitize_json(v) for v in obj]
    elif isinstance(obj, (np.floating, float)):
        val = float(obj)
        return 0.0 if np.isnan(val) or np.isinf(val) else val
    elif isinstance(obj, (np.integer, int)):
        return int(obj)
    elif isinstance(obj, (pd.Timestamp, datetime)):
        return obj.strftime('%Y-%m-%d')
    elif isinstance(obj, np.ndarray):
        return [sanitize_json(v) for v in obj]
    return obj


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
                'sharpe_ratio': round(float(cb_res.get('sharpe_ratio', 0.0)), 2),
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
        all_stock_codes = load_all_stock_codes_db(limit_to_csi300=limit_to_csi300)
        backtest_dates = get_trading_dates_db(start_date_str, end_date_str)

        if not backtest_dates:
            return {'status': 'error', 'message': '所选时间段内没有交易日期'}

        strategy = StrategyRegistry.get(strategy_name, config)

        batch_size = 400
        batches = [all_stock_codes[i:i+batch_size] for i in range(0, len(all_stock_codes), batch_size)]
        print(f"[BacktestEngine] Chunked batch processing: {len(all_stock_codes)} stocks into {len(batches)} batches across {len(backtest_dates)} dates...", flush=True)

        if hasattr(strategy, 'load_financial_data'):
            strategy.load_financial_data(target_codes=all_stock_codes)

        daily_buy_candidates = {d: [] for d in backtest_dates}
        daily_sell_signals = {d: [] for d in backtest_dates}
        candidate_kline_pool = {}

        for idx, b_codes in enumerate(batches):
            print(f"  [BacktestEngine] Batch {idx+1}/{len(batches)} ({len(b_codes)} stocks)...", flush=True)
            b_data = load_all_data_db(start_date=start_date_str, end_date=end_date_str, target_codes=b_codes)
            if not b_data:
                continue

            b_processed = {}
            for code, df in b_data.items():
                if hasattr(strategy, 'compute_indicators'):
                    b_processed[code] = strategy.compute_indicators(df, code=code)
                else:
                    b_processed[code] = df

            for code, df in b_processed.items():
                c_code = str(code)
                if 'buy_signal' in df.columns:
                    b_rows = df[df['buy_signal']]
                    for dt, row in b_rows.iterrows():
                        if dt in daily_buy_candidates:
                            c_price = float(row['close'])
                            if check_limit_up(df, dt, c_price):
                                continue
                            rank_val = float(row['rank_val']) if 'rank_val' in row and not pd.isna(row['rank_val']) else c_price
                            daily_buy_candidates[dt].append((c_code, c_price, rank_val))
                            df_copy = df.copy()
                            candidate_kline_pool[c_code] = df_copy
                            candidate_kline_pool[c_code.replace('.', '_')] = df_copy
                            candidate_kline_pool[c_code.replace('_', '.')] = df_copy

                if 'sell_signal' in df.columns:
                    s_rows = df[df['sell_signal']]
                    for dt, row in s_rows.iterrows():
                        if dt in daily_sell_signals:
                            s_price = float(row['close'])
                            daily_sell_signals[dt].append((c_code, s_price))
                            df_copy = df.copy()
                            candidate_kline_pool[c_code] = df_copy
                            candidate_kline_pool[c_code.replace('.', '_')] = df_copy
                            candidate_kline_pool[c_code.replace('_', '.')] = df_copy

            del b_data, b_processed
            gc.collect()

        reverse_sort = getattr(strategy, 'reverse', False)
        for d in backtest_dates:
            cands = daily_buy_candidates[d]
            if cands:
                cands.sort(key=lambda x: x[2] if len(x) > 2 and not pd.isna(x[2]) else -9999.0, reverse=reverse_sort)

    else:
        # Backward compatibility if all_data passed directly
        strategy = StrategyRegistry.get(strategy_name, config)
        processed_data = {}
        candidate_kline_pool = {}
        for code, df in all_data.items():
            if hasattr(strategy, 'compute_indicators'):
                processed_data[code] = strategy.compute_indicators(df)
            else:
                processed_data[code] = df

        start_date = pd.Timestamp(start_date_str)
        end_date = pd.Timestamp(end_date_str)
        all_dates = sorted(set().union(*[set(df.index) for df in all_data.values()]))
        backtest_dates = [d for d in all_dates if start_date <= d <= end_date]
        if not backtest_dates:
            return {'status': 'error', 'message': '所选时间段内没有交易日期'}

        daily_buy_candidates = {d: [] for d in backtest_dates}
        daily_sell_signals = {d: [] for d in backtest_dates}
        for d in backtest_dates:
            if hasattr(strategy, 'get_signals'):
                buys, sells = strategy.get_signals(processed_data, d)
                if buys:
                    for cand in buys:
                        c_code, c_price = cand[0], cand[1]
                        if c_code in processed_data and check_limit_up(processed_data[c_code], d, c_price):
                            continue
                  # Initialize Position Manager & Simulation Loop (Strict T+1 Next-Day Open Execution Framework)
    position_manager = InMemoryPositionManager(config)
    max_holdings = config.get('strategy', {}).get('max_holdings', 5)
    per_stock_capital = config.get('strategy', {}).get('per_stock_capital', 20000.0)
    initial_equity = max_holdings * per_stock_capital

    position_manager.slot_cash = [float(per_stock_capital)] * max_holdings
    position_manager.positions = []
    position_manager.realized_pnl = 0.0

    trade_log = []
    equity_history = []
    holding_data = {}

    slippage_rate = float(config.get('execution', {}).get('slippage', 0.001)) # 0.1% 滑点成本

    pending_sells = [] # 待在当前交易日开盘执行的卖单 [(code, sell_reason)]
    pending_buys = []  # 待在当前交易日开盘执行的买单 [(code, rank_val)]

    for date_idx, date in enumerate(backtest_dates):
        # 预加载当前交易日已有持仓及候选股票的 K 线数据
        active_codes = [p.code for p in position_manager.get_positions()]
        missing_codes = [c for c in active_codes if c not in holding_data]
        if missing_codes:
            if all_data is not None:
                for c in missing_codes:
                    if c in all_data:
                        holding_data[c] = processed_data[c]
            else:
                for c in missing_codes:
                    c_std = c.replace('_', '.')
                    c_raw = c.replace('.', '_')
                    if c in candidate_kline_pool:
                        holding_data[c] = candidate_kline_pool[c]
                    elif c_std in candidate_kline_pool:
                        holding_data[c] = candidate_kline_pool[c_std]
                    elif c_raw in candidate_kline_pool:
                        holding_data[c] = candidate_kline_pool[c_raw]
                    else:
                        fresh_h_data = load_all_data_db(start_date=start_date_str, end_date=end_date_str, target_codes=[c])
                        for code_h, df in fresh_h_data.items():
                            holding_data[c] = strategy.compute_indicators(df, code=c) if hasattr(strategy, 'compute_indicators') else df

        # =========================================================================
        # 1. 严格在 T+1 日【开盘时】(Open Price) 执行上一交易日盘后产生的卖出指令
        # =========================================================================
        unexecuted_sells = []
        for sell_item in pending_sells:
            code = sell_item['code']
            reason = sell_item['reason']
            pos = position_manager.get_position(code)
            if not pos:
                continue

            if code in holding_data and date in holding_data[code].index:
                df_k = holding_data[code]
                row_k = df_k.loc[date]
                open_price = float(row_k['open'])
                high_price = float(row_k['high'])
                low_price = float(row_k['low'])

                # 检查是否为一字跌停 (若全天一字跌停，开盘无法卖出平仓，保留至下一个交易日继续尝试)
                if open_price <= 0 or (open_price == low_price == high_price and 'close' in row_k and check_limit_up(df_k, date, open_price)):
                    unexecuted_sells.append(sell_item)
                    continue

                # 扣除卖出滑点成本 (根据滑点向下修正卖出价格)
                exec_sell_price = round(open_price * (1.0 - slippage_rate), 2)
                sell_amount = pos.shares * exec_sell_price
                fee = calc_sell_cost(sell_amount)
                net_sell = sell_amount - fee
                pnl = net_sell - pos.cost_total
                pnl_pct = (pnl / pos.cost_total * 100) if pos.cost_total > 0 else 0.0

                position_manager.remove_position(code, exec_sell_price, fee)

                trade_log.append({
                    'trade_date': date.strftime('%Y-%m-%d'),
                    'stock_code': code,
                    'action': 'SELL',
                    'price': exec_sell_price,
                    'shares': pos.shares,
                    'amount': round(sell_amount, 2),
                    'fee': round(fee, 2),
                    'pnl': round(pnl, 2),
                    'pnl_pct': round(pnl_pct, 2),
                    'reason': f"[T+1开盘成交] {reason}"
                })
            else:
                unexecuted_sells.append(sell_item)

        # 挂单未成交的卖单保留到下一交易日
        pending_sells = unexecuted_sells

        # =========================================================================
        # 2. 严格在 T+1 日【开盘时】(Open Price) 执行上一交易日盘后产生的买入指令
        # =========================================================================
        holding_codes = [p.code for p in position_manager.get_positions()]
        exclude_codes = set(holding_codes)
        active_slots_after_sells = {p.slot_idx for p in position_manager.get_positions()}
        empty_slots = sorted(list(set(range(max_holdings)) - active_slots_after_sells))

        for buy_item in pending_buys:
            if not empty_slots:
                break
            code = buy_item['code']
            if code in exclude_codes:
                continue

            # 获取该股票在买入日的 K 线数据
            df_k = None
            if code in candidate_kline_pool:
                df_k = candidate_kline_pool[code]
            elif code.replace('_', '.') in candidate_kline_pool:
                df_k = candidate_kline_pool[code.replace('_', '.')]
            elif code in holding_data:
                df_k = holding_data[code]
            else:
                fresh_b = load_all_data_db(start_date=start_date_str, end_date=end_date_str, target_codes=[code])
                if fresh_b and code in fresh_b:
                    df_k = fresh_b[code]
                    candidate_kline_pool[code] = df_k

            if df_k is None or date not in df_k.index:
                continue

            row_k = df_k.loc[date]
            open_price = float(row_k['open'])
            high_price = float(row_k['high'])
            low_price = float(row_k['low'])

            if open_price <= 0:
                continue

            # 检查是否为一字涨停 (若全天一字涨停，开盘无法挂单买入，买单失效)
            if open_price == high_price == low_price and check_limit_up(df_k, date, open_price):
                continue

            # 增加买入滑点成本 (根据滑点向上修正买入价格)
            exec_buy_price = round(open_price * (1.0 + slippage_rate), 2)

            slot_idx = empty_slots.pop(0)
            available_cash = position_manager.slot_cash[slot_idx] if config.get('strategy', {}).get('enable_compounding', False) else per_stock_capital
            max_shares = int(available_cash / exec_buy_price / 100) * 100

            if max_shares <= 0:
                empty_slots.append(slot_idx)
                empty_slots.sort()
                continue

            buy_amount = max_shares * exec_buy_price
            cost_fee = calc_buy_cost(buy_amount)
            total_cost = buy_amount + cost_fee

            if total_cost > available_cash:
                empty_slots.append(slot_idx)
                empty_slots.sort()
                continue

            position_manager.add_position(code, exec_buy_price, max_shares, total_cost, slot_idx)
            holding_data[code] = df_k
            exclude_codes.add(code)

            trade_log.append({
                'trade_date': date.strftime('%Y-%m-%d'),
                'stock_code': code,
                'action': 'BUY',
                'price': exec_buy_price,
                'shares': max_shares,
                'amount': round(buy_amount, 2),
                'fee': round(cost_fee, 2),
                'pnl': 0.0,
                'pnl_pct': 0.0,
                'reason': '[T+1开盘成交] 满足选股因子'
            })

        # 清空已处理的买单
        pending_buys = []

        # =========================================================================
        # 3. 今日 (T日) 盘后：更新最高价、生成卖出信号与选股买入信号（供 T+1 日开盘执行）
        # =========================================================================
        # 3.1 更新最高价与卖出判断
        next_pending_sells = []
        for pos in position_manager.get_positions():
            code = pos.code
            if code in holding_data and date in holding_data[code].index:
                df_k = holding_data[code]
                high_price = float(df_k.loc[date, 'high'])
                close_price = float(df_k.loc[date, 'close'])
                if not pd.isna(high_price) and hasattr(pos, 'highest_price'):
                    pos.highest_price = max(getattr(pos, 'highest_price', pos.buy_price), high_price)

                pnl_pct = (close_price - pos.buy_price) / pos.buy_price if pos.buy_price > 0 else 0.0
                sell_reason = None

                if config.get('risk', {}).get('enable_stop_loss', False) and pnl_pct <= config.get('risk', {}).get('stop_loss', -0.08):
                    sell_reason = f"止损触发 (盘后浮亏: {pnl_pct*100:.2f}%)"
                elif strategy.check_sell_signal(df_k, date):
                    sell_reason = "技术指标卖出信号"

                if sell_reason:
                    # 避免重复加入卖单
                    if not any(s['code'] == code for s in pending_sells) and not any(s['code'] == code for s in next_pending_sells):
                        next_pending_sells.append({'code': code, 'reason': sell_reason})

        pending_sells.extend(next_pending_sells)

        # 3.2 今日盘后生成新的买入候选信号
        current_holding_count = len(position_manager.get_positions())
        if current_holding_count < max_holdings:
            cands = daily_buy_candidates.get(date, [])
            for cand in cands:
                cand_code = cand[0]
                if cand_code not in set(p.code for p in position_manager.get_positions()):
                    if not any(b['code'] == cand_code for b in pending_buys):
                        pending_buys.append({'code': cand_code, 'rank_val': cand[2] if len(cand) > 2 else 0.0})

        # =========================================================================
        # 4. 今日 (T日) 盘后：以今日收盘价 Close 结算最新资产组合净值
        # =========================================================================
        current_prices = {}
        for pos in position_manager.get_positions():
            code = pos.code
            if code in holding_data and date in holding_data[code].index:
                current_prices[code] = float(holding_data[code].loc[date, 'close'])
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

    eq_df['daily_return'] = eq_df['equity'].pct_change().fillna(0.0)
    daily_rf = 0.02 / 252
    excess_returns = eq_df['daily_return'] - daily_rf
    std_val = excess_returns.std()
    if pd.isna(std_val) or std_val <= 0 or np.isnan(std_val):
        sharpe_ratio = 0.0
    else:
        raw_sharpe = float((excess_returns.mean() / std_val) * np.sqrt(252))
        if np.isnan(raw_sharpe) or np.isinf(raw_sharpe):
            sharpe_ratio = 0.0
        else:
            sharpe_ratio = float(np.clip(raw_sharpe, -99.99, 99.99))

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
        'sharpe_ratio': round(sharpe_ratio, 2),
        'win_rate': round(win_rate, 2),
        'total_buys': len(buys),
        'total_sells': len(sells),
        'total_fees': round(total_fees, 2),
        'equity_history': equity_history,
        'trades': trade_log
    }

    result_summary = sanitize_json(result_summary)

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
