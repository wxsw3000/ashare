#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据库写入工具 - 将策略结果写入 TiDB
"""

import os
import sys
import pymysql
from datetime import datetime
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from utils.db import get_db_connection


def save_recommendations(strategy_name, buy_signals, sell_signals, signal_date):
    """
    保存推荐信号到 recommendations 表
    
    参数:
        strategy_name: str, 'price' 或 'pe'
        buy_signals: list of tuples, [(code, price, ...), ...]
        sell_signals: list of tuples, [(code, price, ...), ...]
        signal_date: pd.Timestamp 或 str, 信号日期
    """
    if isinstance(signal_date, pd.Timestamp):
        signal_date = signal_date.strftime('%Y-%m-%d')
    elif isinstance(signal_date, datetime):
        signal_date = signal_date.strftime('%Y-%m-%d')
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 先删除该日期该策略的旧数据（避免重复）
        cursor.execute(
            "DELETE FROM recommendations WHERE strategy = %s AND signal_date = %s",
            (strategy_name, signal_date)
        )
        
        inserted = 0
        
        # 插入买入信号
        for item in buy_signals:
            # 不同策略的信号格式不同，统一处理
            if len(item) >= 2:
                code = item[0]
                price = float(item[1])
                reason = '买入信号'
                
                # 如果有第三个元素，可能是 PE 或其他指标
                if len(item) >= 3:
                    extra_info = f"PE: {item[2]:.2f}" if isinstance(item[2], (int, float)) else str(item[2])
                    reason = f"买入信号 ({extra_info})"
                
                cursor.execute("""
                    INSERT IGNORE INTO recommendations 
                    (strategy, stock_code, action, price, reason, signal_date)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (strategy_name, code, 'BUY', price, reason, signal_date))
                inserted += 1
        
        # 插入卖出信号
        for item in sell_signals:
            if len(item) >= 2:
                code = item[0]
                price = float(item[1])
                
                cursor.execute("""
                    INSERT IGNORE INTO recommendations 
                    (strategy, stock_code, action, price, reason, signal_date)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (strategy_name, code, 'SELL', price, '卖出信号', signal_date))
                inserted += 1
        
        conn.commit()
        print(f"  [DB] ✅ 保存 {inserted} 条推荐信号到数据库 (策略: {strategy_name}, 日期: {signal_date})")
        
    except Exception as e:
        print(f"  [DB] ❌ 保存推荐信号失败: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


def save_positions(strategy_name, positions):
    """
    保存持仓数据到 positions 表
    
    参数:
        strategy_name: str, 'price' 或 'pe'
        positions: list of Position 对象或 dict 列表
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 先删除该策略的旧持仓
        cursor.execute("DELETE FROM positions WHERE strategy = %s", (strategy_name,))
        
        inserted = 0
        for pos in positions:
            # 支持 Position 对象或 dict
            if hasattr(pos, 'code'):
                # Position 对象
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
                # dict
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


def save_backtest_result(strategy_name, result_data):
    """
    保存回测结果到 backtest_results 表
    
    参数:
        strategy_name: str, 'price' 或 'pe'
        result_data: dict, 包含回测汇总数据
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO backtest_results 
            (strategy, run_date, date_range_start, date_range_end, 
             initial_equity, final_equity, total_return, annual_return,
             max_drawdown, win_rate, total_buys, total_sells, total_fees)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            result_data.get('total_fees', 0)
        ))
        
        backtest_id = cursor.lastrowid
        
        # 保存成交明细到 backtest_trades
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