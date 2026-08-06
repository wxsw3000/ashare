# -*- coding: utf-8 -*-
"""
MagicSTG Core Database Writer
Handles persistence of recommendations, positions, and backtest results into TiDB.
"""

from datetime import datetime
import pandas as pd
from typing import List, Tuple, Dict, Any, Union

from MagicSTG.core.db import get_connection


def save_recommendations(strategy_name: str, buy_signals: List[Tuple], sell_signals: List[Tuple], signal_date: Union[pd.Timestamp, str, datetime]):
    """
    Saves recommendation signals into recommendations table.
    """
    if isinstance(signal_date, (pd.Timestamp, datetime)):
        signal_date_str = signal_date.strftime('%Y-%m-%d')
    else:
        signal_date_str = str(signal_date)

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM recommendations WHERE strategy = %s AND signal_date = %s",
            (strategy_name, signal_date_str)
        )

        inserted = 0

        for item in buy_signals:
            if len(item) >= 2:
                code = item[0]
                price = float(item[1])
                reason = '买入信号'

                if len(item) >= 3:
                    extra_info = f"PE: {item[2]:.2f}" if isinstance(item[2], (int, float)) else str(item[2])
                    reason = f"买入信号 ({extra_info})"

                cursor.execute("""
                    INSERT IGNORE INTO recommendations 
                    (strategy, stock_code, action, price, reason, signal_date)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (strategy_name, code, 'BUY', price, reason, signal_date_str))
                inserted += 1

        for item in sell_signals:
            if len(item) >= 2:
                code = item[0]
                price = float(item[1])
                reason = item[2] if len(item) >= 3 else '卖出信号'

                cursor.execute("""
                    INSERT IGNORE INTO recommendations 
                    (strategy, stock_code, action, price, reason, signal_date)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (strategy_name, code, 'SELL', price, str(reason), signal_date_str))
                inserted += 1

        conn.commit()
        print(f"  [DB] ✅ 保存 {inserted} 条推荐记录到数据库 (策略: {strategy_name}, 日期: {signal_date_str})")

    except Exception as e:
        print(f"  [DB] ❌ 保存推荐失败: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


def save_positions(strategy_name: str, positions_list: List[Any]):
    """
    Saves position holdings into positions table.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM positions WHERE strategy = %s", (strategy_name,))

        inserted = 0
        for pos in positions_list:
            if hasattr(pos, '__dict__'):
                code = pos.code
                buy_date = pos.buy_date.strftime('%Y-%m-%d') if hasattr(pos.buy_date, 'strftime') else str(pos.buy_date)
                buy_price = pos.buy_price
                shares = pos.shares
                cost_total = pos.cost_total
                current_price = getattr(pos, 'current_price', None)
                market_value = getattr(pos, 'market_value', None)
                pnl = getattr(pos, 'pnl', None)
                pnl_pct = getattr(pos, 'pnl_pct', None)
                status = getattr(pos, 'status', 'HOLDING')
                sell_date = getattr(pos, 'sell_date', None)
                if sell_date and hasattr(sell_date, 'strftime'):
                    sell_date = sell_date.strftime('%Y-%m-%d')
                sell_price = getattr(pos, 'sell_price', None)
            else:
                code = pos.get('code')
                buy_date = pos.get('buy_date')
                buy_price = pos.get('buy_price')
                shares = pos.get('shares')
                cost_total = pos.get('cost_total')
                current_price = pos.get('current_price')
                market_value = pos.get('market_value')
                pnl = pos.get('pnl')
                pnl_pct = pos.get('pnl_pct')
                status = pos.get('status', 'HOLDING')
                sell_date = pos.get('sell_date')
                sell_price = pos.get('sell_price')

            cursor.execute("""
                INSERT INTO positions 
                (strategy, stock_code, buy_date, buy_price, shares, cost_total, 
                 current_price, market_value, pnl, pnl_pct, status, sell_date, sell_price)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (strategy_name, code, buy_date, buy_price, shares, cost_total,
                  current_price, market_value, pnl, pnl_pct, status, sell_date, sell_price))
            inserted += 1

        conn.commit()
        print(f"  [DB] ✅ 保存 {inserted} 条持仓记录到数据库 (策略: {strategy_name})")

    except Exception as e:
        print(f"  [DB] ❌ 保存持仓失败: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


def save_backtest_result(strategy_name: str, result_data: Dict[str, Any]):
    """
    Saves backtest result summary into backtest_results and backtest_trades tables.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("ALTER TABLE backtest_results ADD COLUMN sharpe_ratio decimal(12, 6) DEFAULT 0.00;")
        except Exception:
            pass

        cursor.execute("""
            INSERT INTO backtest_results 
            (strategy, run_date, date_range_start, date_range_end, 
             initial_equity, final_equity, total_return, annual_return,
             max_drawdown, win_rate, total_buys, total_sells, total_fees, sharpe_ratio)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            strategy_name,
            result_data.get('run_date', datetime.now().strftime('%Y-%m-%d')),
            result_data.get('date_range_start'),
            result_data.get('date_range_end'),
            result_data.get('initial_equity'),
            result_data.get('final_equity'),
            result_data.get('total_return'),
            result_data.get('annual_return'),
            result_data.get('max_drawdown'),
            result_data.get('win_rate'),
            result_data.get('total_buys', 0),
            result_data.get('total_sells', 0),
            result_data.get('total_fees', 0),
            result_data.get('sharpe_ratio', 0.0)
        ))

        backtest_id = cursor.lastrowid

        trades = result_data.get('trades', [])
        for trade in trades:
            cursor.execute("""
                INSERT INTO backtest_trades 
                (backtest_id, trade_date, stock_code, action, price, shares, fee, pnl, pnl_pct, reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                backtest_id,
                trade.get('trade_date'),
                trade.get('stock_code'),
                trade.get('action'),
                trade.get('price'),
                trade.get('shares'),
                trade.get('fee', 0),
                trade.get('pnl'),
                trade.get('pnl_pct'),
                trade.get('reason')
            ))

        conn.commit()
        print(f"  [DB] ✅ 保存回测结果到数据库 (策略: {strategy_name}, ID: {backtest_id})")

    except Exception as e:
        print(f"  [DB] ❌ 保存回测结果失败: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()
