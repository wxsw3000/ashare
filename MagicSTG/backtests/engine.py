# -*- coding: utf-8 -*-
"""
MagicSTG Unified Core Backtest Engine
Provides a single source of truth for both CLI backtesting and Flask Web API backtesting.
Delegates asset portfolio management and trade execution directly to the unified PortfolioEngine
and PortfolioStrategyRegistry core.
"""

import gc
import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any

from MagicSTG.core.db import (
    load_all_data_db,
    load_all_stock_codes_db,
    get_trading_dates_db,
    load_cb_data_db
)
from MagicSTG.core.limit import check_limit_up
from MagicSTG.core.db_writer import save_backtest_result
from MagicSTG.strategies.registry import StrategyRegistry
from MagicSTG.execution.portfolio_strategies import PortfolioStrategyRegistry, is_convertible_bond
from MagicSTG.execution.portfolio_engine import calculate_performance_metrics


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
    Executes backtest loop with specified configuration.
    Delegates asset portfolio evaluation and trade execution to PortfolioStrategyRegistry.
    Supports both Stock and Convertible Bond strategies under a unified execution core.
    """
    is_cb_stg = (
        strategy_name == 'cb_double_low' or
        strategy_name.startswith('cb_') or
        config.get('category') == 'convertible_bond'
    )

    day_recs: Dict[str, Dict[str, List[dict]]] = {}
    price_map: Dict[Tuple[str, str], Dict[str, float]] = {}
    name_map: Dict[str, str] = {}
    backtest_dates: List[str] = []

    # =========================================================================
    # Step 1: Signal Selection & Data Loading Phase
    # =========================================================================
    if is_cb_stg:
        print(f"[BacktestEngine] Initializing Convertible Bond backtest for '{strategy_name}' ({start_date_str} ~ {end_date_str})...", flush=True)
        cb_data = load_cb_data_db(start_date=start_date_str, end_date=end_date_str)
        indicator_df = cb_data.get('cb_daily_indicator')
        basic_df = cb_data.get('cb_basic')

        if indicator_df is None or indicator_df.empty:
            return {'status': 'error', 'message': '未获取到可转债衍生指标数据，请确认 cb_daily_indicator 表已填载数据'}

        if basic_df is not None and not basic_df.empty:
            for _, row in basic_df.iterrows():
                code = str(row['code'])
                name = str(row['name'])
                name_map[code] = name

        indicator_df['date_str'] = indicator_df['date'].astype(str)
        backtest_dates = sorted(indicator_df['date_str'].unique().tolist())
        if not backtest_dates:
            return {'status': 'error', 'message': '所选时间段内没有可转债交易日期'}

        top_n = int(config.get('top_n') or config.get('portfolio_config', {}).get('max_holdings', 10))
        max_price = float(config.get('max_price', 130.0))
        cb_stg_cfg = dict(config)
        cb_stg_cfg.update({'top_n': top_n, 'max_price': max_price})
        selection_strategy = StrategyRegistry.get(strategy_name, cb_stg_cfg)

        # Generate CB signals and populate price_map for each trading date
        for d_str, sub_df in indicator_df.groupby('date_str', sort=True):
            for _, r in sub_df.iterrows():
                c_code = str(r['code'])
                c_price = float(r['cb_price']) if pd.notna(r['cb_price']) else 0.0
                if c_price > 0:
                    price_map[(c_code, d_str)] = {'open': c_price, 'close': c_price, 'high': c_price, 'low': c_price}

            if hasattr(selection_strategy, 'select_top_bonds'):
                top_bonds = selection_strategy.select_top_bonds(sub_df)
                buy_list = [
                    {
                        'code': str(r['code']),
                        'price': float(r['cb_price']),
                        'reason': f"双低入选 (价格:{float(r['cb_price']):.2f}, 双低值:{float(r.get('db_low_value', 0)):.2f})"
                    }
                    for _, r in top_bonds.iterrows()
                    if pd.notna(r.get('cb_price')) and float(r.get('cb_price')) > 0
                ]
                day_recs[d_str] = {'BUY': buy_list, 'SELL': []}
            elif hasattr(selection_strategy, 'get_signals'):
                buys, sells = selection_strategy.get_signals(sub_df, d_str)
                buy_list = [{'code': str(b[0]), 'price': float(b[1]), 'reason': f"双低轮动 (低值:{b[2]:.2f})"} for b in buys]
                sell_list = [{'code': str(s[0]), 'price': float(s[1]), 'reason': '离场轮动'} for s in sells]
                day_recs[d_str] = {'BUY': buy_list, 'SELL': sell_list}

    else:
        # Stock Strategy Signal Generation
        limit_to_csi300 = (universe == 'csi300')
        all_stock_codes = load_all_stock_codes_db(limit_to_csi300=limit_to_csi300)
        trading_dates = get_trading_dates_db(start_date_str, end_date_str)
        backtest_dates = [d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)[:10] for d in trading_dates]

        if not backtest_dates:
            print("  [WARN] No trading dates found in database for range.")
            return {'status': 'error', 'message': 'No trading dates found'}

        selection_strategy = StrategyRegistry.get(strategy_name, config)
        batch_size = 400
        batches = [all_stock_codes[i:i+batch_size] for i in range(0, len(all_stock_codes), batch_size)]
        print(f"[BacktestEngine] Chunked batch processing: {len(all_stock_codes)} stocks into {len(batches)} batches...", flush=True)

        if hasattr(selection_strategy, 'load_financial_data'):
            selection_strategy.load_financial_data(target_codes=all_stock_codes)

        daily_buy_cands: Dict[str, List[Tuple[str, float, float]]] = {d: [] for d in backtest_dates}
        daily_sell_sigs: Dict[str, List[Tuple[str, float]]] = {d: [] for d in backtest_dates}

        for idx, b_codes in enumerate(batches):
            b_data = load_all_data_db(start_date=start_date_str, end_date=end_date_str, target_codes=b_codes)
            if not b_data:
                continue

            b_processed = {}
            for code, df in b_data.items():
                if hasattr(selection_strategy, 'compute_indicators'):
                    b_processed[code] = selection_strategy.compute_indicators(df, code=code)
                else:
                    b_processed[code] = df

            for code, df in b_processed.items():
                c_code = str(code)
                # Store full K-lines into price_map
                for idx_row, r in df.iterrows():
                    row_dt = r['date'] if 'date' in r else idx_row
                    dt_s = row_dt.strftime('%Y-%m-%d') if hasattr(row_dt, 'strftime') else str(row_dt)[:10]
                    c_open = float(r['open']) if 'open' in r and not pd.isna(r['open']) else float(r['close'])
                    c_close = float(r['close'])
                    c_high = float(r['high']) if 'high' in r and not pd.isna(r['high']) else c_close
                    c_low = float(r['low']) if 'low' in r and not pd.isna(r['low']) else c_close
                    price_map[(c_code, dt_s)] = {'open': c_open, 'close': c_close, 'high': c_high, 'low': c_low}

                if 'buy_signal' in df.columns:
                    b_rows = df[df['buy_signal']]
                    for idx_row, row in b_rows.iterrows():
                        row_dt = row['date'] if 'date' in row else idx_row
                        dt_s = row_dt.strftime('%Y-%m-%d') if hasattr(row_dt, 'strftime') else str(row_dt)[:10]
                        if dt_s in daily_buy_cands:
                            c_price = float(row['close'])
                            if check_limit_up(df, idx_row, c_price):
                                continue
                            rank_val = float(row['rank_val']) if 'rank_val' in row and not pd.isna(row['rank_val']) else c_price
                            daily_buy_cands[dt_s].append((c_code, c_price, rank_val))

                if 'sell_signal' in df.columns:
                    s_rows = df[df['sell_signal']]
                    for idx_row, row in s_rows.iterrows():
                        row_dt = row['date'] if 'date' in row else idx_row
                        dt_s = row_dt.strftime('%Y-%m-%d') if hasattr(row_dt, 'strftime') else str(row_dt)[:10]
                        if dt_s in daily_sell_sigs:
                            s_price = float(row['close'])
                            daily_sell_sigs[dt_s].append((c_code, s_price))

            del b_data, b_processed
            gc.collect()

        reverse_sort = getattr(selection_strategy, 'reverse', False)
        top_n = int(config.get('top_n') or config.get('portfolio_config', {}).get('max_holdings', 10))

        for d_s in backtest_dates:
            cands = daily_buy_cands[d_s]
            if cands:
                cands.sort(key=lambda x: x[2] if len(x) > 2 and not pd.isna(x[2]) else -9999.0, reverse=reverse_sort)
                cands = cands[:top_n]
            buy_list = [{'code': c[0], 'price': c[1], 'reason': '选股因子建仓'} for c in cands]
            sell_list = [{'code': c[0], 'price': c[1], 'reason': '技术指标平仓'} for c in daily_sell_sigs[d_s]]
            day_recs[d_s] = {'BUY': buy_list, 'SELL': sell_list}

    # =========================================================================
    # Step 2: Portfolio Strategy & Asset Simulation (Unified Execution Core)
    # =========================================================================
    portfolio_stg = PortfolioStrategyRegistry.get(config)
    initial_capital = float(portfolio_stg.initial_capital)
    max_holdings = int(portfolio_stg.max_holdings)
    per_slot_cash = initial_capital / max_holdings

    active_positions: Dict[int, dict] = {}
    slot_cash: List[float] = [float(per_slot_cash)] * max_holdings
    pending_orders: List[dict] = []
    equity_history: List[dict] = []
    trade_log: List[dict] = []
    closed_trades: List[dict] = []
    total_fees = 0.0

    print(f"[BacktestEngine] Running portfolio simulation: Capital={initial_capital:,.2f}, MaxHoldings={max_holdings}, Strategy={portfolio_stg.name}...", flush=True)

    for d_str in backtest_dates:
        prev_active_slots = set(active_positions.keys())
        day_rec_items = day_recs.get(d_str, {'BUY': [], 'SELL': []})

        # Delegate daily position evaluation & risk control to Unified Portfolio Strategy
        active_positions, closed_today, slot_cash, pending_orders = portfolio_stg.evaluate_day(
            d_str=d_str,
            day_recs=day_rec_items,
            price_map=price_map,
            name_map=name_map,
            active_positions=active_positions,
            slot_cash=slot_cash,
            pending_orders=pending_orders
        )

        # Log newly opened positions (BUY trades)
        for slot_idx, pos in active_positions.items():
            if pos.get('buy_date') == d_str and slot_idx not in prev_active_slots:
                fee = float(pos.get('fee', 0.0))
                total_fees += fee
                trade_log.append({
                    'trade_date': d_str,
                    'stock_code': pos['stock_code'],
                    'stock_name': pos.get('stock_name', pos['stock_code']),
                    'action': 'BUY',
                    'price': round(float(pos['buy_price']), 2),
                    'shares': int(pos['shares']),
                    'amount': round(float(pos['cost_total']), 2),
                    'fee': round(fee, 2),
                    'pnl': 0.0,
                    'pnl_pct': 0.0,
                    'reason': pos.get('extra_data', '建仓')
                })

        # Log closed positions (SELL trades)
        for pos in closed_today:
            closed_trades.append(pos)
            fee = float(pos.get('fee', 0.0))
            total_fees += fee
            trade_log.append({
                'trade_date': d_str,
                'stock_code': pos['stock_code'],
                'stock_name': pos.get('stock_name', pos['stock_code']),
                'action': 'SELL',
                'price': round(float(pos['sell_price']), 2),
                'shares': int(pos['shares']),
                'amount': round(float(pos['shares'] * pos['sell_price']), 2),
                'fee': round(fee, 2),
                'pnl': round(float(pos.get('pnl', 0.0)), 2),
                'pnl_pct': round(float(pos.get('pnl_pct', 0.0)), 2),
                'reason': pos.get('extra_data', '平仓')
            })

        # Daily Equity Snapshot
        market_val = sum(float(pos.get('market_value', 0.0)) for pos in active_positions.values())
        total_cash = sum(slot_cash)
        total_equity = round(total_cash + market_val, 2)

        equity_history.append({
            'date': d_str,
            'equity': total_equity,
            'total_equity': total_equity,
            'cash': round(total_cash, 2),
            'positions_count': len(active_positions)
        })

    # =========================================================================
    # Step 3: Performance Metrics & Result Sanitization Phase
    # =========================================================================
    metrics = calculate_performance_metrics(equity_history, closed_trades, initial_capital)

    final_equity = equity_history[-1]['equity'] if equity_history else initial_capital
    tot_buys = len([t for t in trade_log if t['action'] == 'BUY'])
    tot_sells = len([t for t in trade_log if t['action'] == 'SELL'])

    result_summary = {
        'status': 'success',
        'strategy_name': strategy_name,
        'date_range_start': start_date_str,
        'date_range_end': end_date_str,
        'initial_equity': initial_capital,
        'final_equity': round(final_equity, 2),
        'total_return': round(metrics['cum_return'], 2),
        'annual_return': round(metrics['annual_return'], 2),
        'max_drawdown': round(metrics['max_drawdown'], 2),
        'sharpe_ratio': round(metrics['sharpe_ratio'], 2),
        'win_rate': round(metrics['win_rate'], 2) if metrics.get('win_rate') is not None else None,
        'total_buys': tot_buys,
        'total_sells': tot_sells,
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

    gc.collect()
    return result_summary
