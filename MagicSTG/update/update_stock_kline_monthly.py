#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_kline_monthly A股月K线数据更新脚本（前复权）
从 stock_basic 获取股票列表（type='1'），从 Baostock 拉取前复权月K线数据
数据范围：2020年至今
"""

import sys
import os
import time
import random
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import baostock as bs
import pandas as pd

from db import (
    get_connection,
    get_connection_with_retry,
    safe_float,
    safe_int,
    safe_str,
    get_beijing_time,
    ensure_bs_login,
    random_sleep,
    get_target_date,
    format_time,
    print_progress,
)

# ============================================================
# 配置
# ============================================================

KLINE_FIELDS = "date,code,open,high,low,close,volume,amount,adjustflag,turn,pctChg"
START_DATE = "2020-01-01"


# ============================================================
# 数据库操作
# ============================================================

def get_active_stocks_from_db(conn):
    """从 stock_basic 获取上市股票列表（type='1' 且 status='1'）"""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT code, ipo_date FROM stock_basic WHERE type = '1' AND status = '1'")
            rows = cur.fetchall()
            stocks = []
            for row in rows:
                code = row[0]
                ipo_date = row[1].strftime('%Y-%m-%d') if row[1] else '2020-01-01'
                if ipo_date < '2020-01-01':
                    ipo_date = '2020-01-01'
                stocks.append({'code': code, 'ipo_date': ipo_date})
            print(f"  [DB] Found {len(stocks)} active stocks from stock_basic", flush=True)
            return stocks
    except Exception as e:
        print(f"  [ERROR] Failed to get stocks from stock_basic: {e}", flush=True)
        return []


def get_all_kline_latest_dates(conn, table_name="stock_kline_monthly"):
    """批量查询所有股票在指定表中的最新日期"""
    try:
        with conn.cursor() as cur:
            sql = f"SELECT code, MAX(date) FROM `{table_name}` GROUP BY code"
            cur.execute(sql)
            rows = cur.fetchall()
            latest_dates = {}
            for row in rows:
                if row[0] and row[1]:
                    latest_dates[row[0]] = row[1].strftime('%Y-%m-%d')
            print(f"  [DB] Loaded latest dates for {len(latest_dates)} stocks from {table_name}", flush=True)
            return latest_dates
    except Exception as e:
        print(f"  [ERROR] Failed to get latest dates from {table_name}: {e}", flush=True)
        return {}


def has_dividend_in_range(conn, code, start_date, end_date):
    """查询日K线表，判断某股票在指定日期范围内是否有 is_dividend = 1"""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(1) FROM stock_kline_day WHERE code = %s AND date >= %s AND date <= %s AND is_dividend = 1",
                (code, start_date, end_date)
            )
            row = cur.fetchone()
            return row[0] > 0
    except Exception as e:
        print(f"  [WARN] Failed to check dividend for {code}: {e}", flush=True)
        return False


def delete_stock_kline_data(conn, code):
    """删除某支股票的全部月K线数据"""
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM stock_kline_monthly WHERE code = %s", (code,))
            deleted = cur.rowcount
            print(f"  [DELETE] 删除了 {deleted} 条历史月K线数据", flush=True)
            return deleted
    except Exception as e:
        print(f"  [ERROR] Failed to delete data for {code}: {e}", flush=True)
        raise


def build_insert_sql(table_name, fields, batch_size):
    """构建批量插入 SQL"""
    placeholders = ", ".join(["(" + ", ".join(["%s"] * len(fields)) + ")"] * batch_size)
    sql = f"""
    INSERT INTO `{table_name}` ({', '.join(['`' + f + '`' for f in fields])})
    VALUES {placeholders}
    ON DUPLICATE KEY UPDATE
        `open` = VALUES(`open`),
        `high` = VALUES(`high`),
        `low` = VALUES(`low`),
        `close` = VALUES(`close`),
        `volume` = VALUES(`volume`),
        `amount` = VALUES(`amount`),
        `adjustflag` = VALUES(`adjustflag`),
        `turn` = VALUES(`turn`),
        `pctChg` = VALUES(`pctChg`),
        `update_date` = VALUES(`update_date`);
    """
    return sql


def flush_db_buffer(conn, batch_data, table_name="stock_kline_monthly"):
    """批量插入月K线数据"""
    if not batch_data:
        return conn
    
    fields = [
        'date', 'code', 'open', 'high', 'low', 'close',
        'volume', 'amount', 'adjustflag', 'turn', 'pctChg', 'update_date'
    ]
    
    sql = build_insert_sql(table_name, fields, len(batch_data))
    flat_args = [val for record in batch_data for val in record]
    
    for attempt in range(1, 4):
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, flat_args)
            return conn
        except Exception as e:
            print(f"  [DB ERROR] Bulk insert failed (attempt {attempt}/3): {e}", flush=True)
            if attempt < 3:
                time.sleep(2)
                try:
                    conn.close()
                except Exception:
                    pass
                conn = get_connection_with_retry()
            else:
                raise e
    return conn


# ============================================================
# Baostock 数据拉取
# ============================================================

def fetch_stock_kline(code, start_date, end_date, max_retries=3):
    """从 Baostock 查询月K线数据（前复权）"""
    for attempt in range(max_retries):
        try:
            if not ensure_bs_login():
                time.sleep(2)
                continue
            
            rs = bs.query_history_k_data_plus(
                code,
                KLINE_FIELDS,
                start_date=start_date,
                end_date=end_date,
                frequency="m",
                adjustflag="2"
            )
            if rs.error_code != '0':
                if attempt < max_retries - 1:
                    time.sleep(random.uniform(2, 4))
                    ensure_bs_login()
                    continue
                return None, False
            
            data_list = []
            while rs.next():
                data_list.append(rs.get_row_data())
            return data_list, True
            
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            print(f"  [WARN] Connection error: {e}, reconnecting...", flush=True)
            if attempt < max_retries - 1:
                ensure_bs_login()
                time.sleep(random.uniform(2, 4))
            else:
                return None, False
        except Exception as e:
            print(f"  [WARN] Fetch error (attempt {attempt+1}/{max_retries}): {e}", flush=True)
            if attempt < max_retries - 1:
                time.sleep(random.uniform(3, 6))
            else:
                return None, False
    return None, False


def parse_kline_row(row, code, update_date):
    """解析月K线数据行"""
    date_val = row[0]
    open_val = safe_float(row[2])
    high_val = safe_float(row[3])
    low_val = safe_float(row[4])
    close_val = safe_float(row[5])
    
    if open_val is None or high_val is None or low_val is None or close_val is None:
        return None
    
    return (
        date_val, code, open_val, high_val, low_val, close_val,
        safe_int(row[6], 0), safe_float(row[7], 0.0),
        safe_str(row[8], '2'), safe_float(row[9]), safe_float(row[10]),
        update_date
    )


def get_month_range(month_end_date):
    """根据某月的结束日期，计算该月的起始日期"""
    dt = pd.to_datetime(month_end_date)
    month_start = dt.replace(day=1)
    return month_start.strftime('%Y-%m-%d'), dt.strftime('%Y-%m-%d')


# ============================================================
# 核心更新逻辑
# ============================================================

def update_stock_data(conn, code, ipo_date, target_date, update_date, db_buffer, db_buffer_limit, latest_info):
    """更新单支股票的月K线数据"""
    total_rows = 0
    updated = 0
    skipped = 0
    failed = 0
    
    last_date = latest_info.get(code)
    
    if last_date and last_date >= target_date:
        return 0, 0, 1, 0, db_buffer
    
    if last_date:
        start_date = (pd.to_datetime(last_date) + timedelta(days=1)).strftime('%Y-%m-%d')
        # 除权区间判断从 start_date 开始，直至 target_date
        has_div = has_dividend_in_range(conn, code, start_date, target_date)
    else:
        start_date = ipo_date
        has_div = False
    
    if has_div:
        delete_stock_kline_data(conn, code)
        conn.commit()
        
        data_list, ok = fetch_stock_kline(code, ipo_date, target_date)
        if not ok or data_list is None:
            return 0, 0, 0, 1, db_buffer
        
        if len(data_list) == 0:
            return 0, 0, 1, 0, db_buffer
    else:
        data_list, ok = fetch_stock_kline(code, start_date, target_date)
        if not ok or data_list is None:
            return 0, 0, 0, 1, db_buffer
        
        if len(data_list) == 0:
            return 0, 0, 1, 0, db_buffer
    
    for row in data_list:
        db_row = parse_kline_row(row, code, update_date)
        if db_row:
            db_buffer.append(db_row)
            total_rows += 1
        
        if len(db_buffer) >= db_buffer_limit:
            conn = flush_db_buffer(conn, db_buffer)
            conn.commit()
            db_buffer = []
    
    return total_rows, 1, 0, 0, db_buffer


# ============================================================
# 主函数
# ============================================================

def main():
    import sys
    beijing_time = get_beijing_time()
    target_date = get_target_date()
    update_date = beijing_time.strftime('%Y-%m-%d')
    
    MAX_RUNTIME = float(os.environ.get('MAX_RUNTIME', 19000))
    
    print("=" * 70)
    print("  [UPDATE] A-share 月K线数据 (前复权 + 除权检测)")
    print(f"  [时间] {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  [数据范围] {START_DATE} 至今")
    print(f"  [目标日期] {target_date}")
    print(f"  [最大时长] {MAX_RUNTIME}秒")
    print("=" * 70, flush=True)
    
    conn = get_connection_with_retry()
    print("[DB] Database connection established!", flush=True)
    
    try:
        print("\n[1] Getting active stocks from stock_basic...")
        all_stocks = get_active_stocks_from_db(conn)
        if not all_stocks:
            print("  [ERROR] No stocks found in stock_basic")
            return
            
        print("\n[2] Loading all latest monthly K-line date info in bulk...")
        latest_info = get_all_kline_latest_dates(conn, "stock_kline_monthly")
        
        print(f"\n[3] Updating {len(all_stocks)} stocks (target: {target_date})...")
        print("-" * 70, flush=True)
        
        db_buffer = []
        db_buffer_limit = 150
        total_rows = 0
        updated_count = 0
        skip_count = 0
        fail_count = 0
        
        total_stocks = len(all_stocks)
        start_time = time.time()
        early_exit = False
        
        for idx, stock in enumerate(all_stocks, 1):
            elapsed = time.time() - start_time
            if elapsed > MAX_RUNTIME:
                print(f"\n  [WARN] Reached maximum runtime limit ({elapsed:.1f}s > {MAX_RUNTIME}s), exiting gracefully...", flush=True)
                early_exit = True
                break
                
            code = stock['code']
            ipo_date = stock['ipo_date']
            
            rows, updated, skipped, failed, db_buffer = update_stock_data(
                conn, code, ipo_date, target_date, update_date,
                db_buffer, db_buffer_limit, latest_info
            )
            
            total_rows += rows
            updated_count += updated
            skip_count += skipped
            fail_count += failed
            
            if idx % 10 == 0 or idx == 1 or idx == total_stocks:
                elapsed = time.time() - start_time
                avg_time = elapsed / idx if idx > 0 else 0
                remaining = avg_time * (total_stocks - idx)
                pct = (idx / total_stocks) * 100
                print(f"  PROGRESS: {idx}/{total_stocks} ({pct:.1f}%) "
                      f"已用: {format_time(elapsed)} 剩余: {format_time(remaining)} | "
                      f"更新: {updated_count} 跳过: {skip_count} 失败: {fail_count}", flush=True)
            
            if idx % 5 == 0:
                random_sleep(0.3, 0.8)
        
        if db_buffer:
            print("\nFlushing remaining data...")
            conn = flush_db_buffer(conn, db_buffer)
            conn.commit()
            db_buffer = []
        
        print("\n" + "=" * 70)
        print("📊 月K线数据同步汇总（前复权）")
        print(f"  [总股票数]          : {total_stocks}")
        print(f"  [已最新跳过]        : {skip_count}")
        print(f"  [成功更新]          : {updated_count}")
        print(f"  [写入行数]          : {total_rows}")
        print(f"  [失败]              : {fail_count}")
        print(f"  [目标日期]          : {target_date}")
        print(f"  [数据起始]          : {START_DATE}")
        print("=" * 70, flush=True)
        
        if early_exit:
            print("  [EXIT] Partially completed due to runtime limits. Exiting with status 2.", flush=True)
            sys.exit(2)
            
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        try:
            bs.logout()
        except Exception:
            pass
        conn.close()


if __name__ == "__main__":
    main()