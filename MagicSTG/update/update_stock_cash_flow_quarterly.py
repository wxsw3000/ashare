#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_cash_flow_quarterly 季频现金流量数据更新脚本
从 stock_basic 获取股票列表（type='1'），从 Baostock 拉取现金流量数据
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
    format_time,
    print_progress,
)

# ============================================================
# 配置
# ============================================================

CASH_FLOW_FIELDS = "code,pubDate,statDate,CAToAsset,NCAToAsset,tangibleAssetToAsset,ebitToInterest,CFOToOR,CFOToNP,CFOToGr"
START_YEAR = 2020


# ============================================================
# 数据库操作
# ============================================================

def get_active_stocks_from_db(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT code FROM stock_basic WHERE type = '1' AND status = '1'")
            rows = cur.fetchall()
            stocks = [row[0] for row in rows]
            print(f"  [DB] Found {len(stocks)} active stocks from stock_basic", flush=True)
            return stocks
    except Exception as e:
        print(f"  [ERROR] Failed to get stocks from stock_basic: {e}", flush=True)
        return []


def get_cash_flow_latest_dates(conn, codes):
    if not codes:
        return {}
    
    result = {}
    
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'stock_cash_flow_quarterly'")
            if cur.fetchone() is None:
                print("  [INFO] stock_cash_flow_quarterly table does not exist yet", flush=True)
                for code in codes:
                    result[code] = None
                return result
            
            cur.execute("SELECT COUNT(*) FROM stock_cash_flow_quarterly")
            count = cur.fetchone()[0]
            if count == 0:
                print("  [INFO] stock_cash_flow_quarterly is empty, all stocks need full update", flush=True)
                for code in codes:
                    result[code] = None
                return result
    except Exception as e:
        print(f"  [WARN] Table check failed: {e}", flush=True)
        for code in codes:
            result[code] = None
        return result
    
    try:
        with conn.cursor() as cur:
            placeholders = ','.join(['%s'] * len(codes))
            sql = f"""
                SELECT code, MAX(stat_date) 
                FROM stock_cash_flow_quarterly 
                WHERE code IN ({placeholders})
                GROUP BY code
            """
            cur.execute(sql, list(codes))
            rows = cur.fetchall()
            for row in rows:
                result[row[0]] = row[1].strftime('%Y-%m-%d') if row[1] else None
            
            for code in codes:
                if code not in result:
                    result[code] = None
    except Exception as e:
        print(f"  [ERROR] Failed to query cash_flow latest dates: {e}", flush=True)
        for code in codes:
            result[code] = None
    
    return result


def build_insert_sql(table_name, fields, batch_size):
    placeholders = ", ".join(["(" + ", ".join(["%s"] * len(fields)) + ")"] * batch_size)
    sql = f"""
    INSERT INTO `{table_name}` ({', '.join(['`' + f + '`' for f in fields])})
    VALUES {placeholders}
    ON DUPLICATE KEY UPDATE
        `pub_date` = VALUES(`pub_date`),
        `CAToAsset` = VALUES(`CAToAsset`),
        `NCAToAsset` = VALUES(`NCAToAsset`),
        `tangibleAssetToAsset` = VALUES(`tangibleAssetToAsset`),
        `ebitToInterest` = VALUES(`ebitToInterest`),
        `CFOToOR` = VALUES(`CFOToOR`),
        `CFOToNP` = VALUES(`CFOToNP`),
        `CFOToGr` = VALUES(`CFOToGr`),
        `update_date` = VALUES(`update_date`);
    """
    return sql


def flush_db_buffer(conn, batch_data):
    if not batch_data:
        return conn
    
    fields = ['code', 'stat_date', 'pub_date', 'CAToAsset', 'NCAToAsset',
              'tangibleAssetToAsset', 'ebitToInterest', 'CFOToOR', 'CFOToNP', 'CFOToGr',
              'update_date']
    
    sql = build_insert_sql('stock_cash_flow_quarterly', fields, len(batch_data))
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

def fetch_cash_flow_data(code, year, quarter, max_retries=3):
    for attempt in range(max_retries):
        try:
            if not ensure_bs_login():
                time.sleep(2)
                continue
            
            rs = bs.query_cash_flow_data(code=code, year=year, quarter=quarter)
            if rs.error_code != '0':
                if attempt < max_retries - 1:
                    time.sleep(random.uniform(1, 3))
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
                time.sleep(random.uniform(2, 5))
            else:
                return None, False
    return None, False


def parse_cash_flow_row(row, update_date):
    code = row[0]
    pub_date = row[1] if row[1] else None
    stat_date = row[2] if row[2] else None
    
    if not stat_date:
        return None
    
    return (
        code,
        stat_date,
        pub_date,
        safe_float(row[3]),
        safe_float(row[4]),
        safe_float(row[5]),
        safe_float(row[6]),
        safe_float(row[7]),
        safe_float(row[8]),
        safe_float(row[9]),
        update_date
    )


def get_target_quarter():
    beijing_time = get_beijing_time()
    month = beijing_time.month
    year = beijing_time.year
    
    if year < START_YEAR:
        return START_YEAR, 1
    
    if month <= 2:
        return (year - 1, 3)
    elif month <= 4:
        return (year - 1, 4)
    elif month <= 7:
        return (year, 1)
    elif month <= 10:
        return (year, 2)
    else:
        return (year, 3)


# ============================================================
# 核心更新逻辑
# ============================================================

def update_stock_data(conn, code, last_date, target_year, target_quarter, update_date, db_buffer, db_buffer_limit):
    total_rows = 0
    
    if last_date:
        last_dt = pd.to_datetime(last_date)
        last_year = last_dt.year
        last_quarter = (last_dt.month - 1) // 3 + 1
        if last_year > target_year or (last_year == target_year and last_quarter >= target_quarter):
            return 0
        
        start_year = max(last_dt.year, START_YEAR)
        start_quarter = (last_dt.month - 1) // 3 + 1
        if start_quarter == 4:
            start_year += 1
            start_quarter = 1
        else:
            start_quarter += 1
    else:
        start_year = START_YEAR
        start_quarter = 1
    
    if start_year > target_year or (start_year == target_year and start_quarter > target_quarter):
        return 0
    
    year = start_year
    quarter = start_quarter
    
    while year < target_year or (year == target_year and quarter <= target_quarter):
        data_list, ok = fetch_cash_flow_data(code, year, quarter)
        if not ok or data_list is None:
            return -1
        
        for row in data_list:
            db_row = parse_cash_flow_row(row, update_date)
            if db_row:
                db_buffer.append(db_row)
                total_rows += 1
            
            if len(db_buffer) >= db_buffer_limit:
                conn = flush_db_buffer(conn, db_buffer)
                conn.commit()
                db_buffer = []
        
        if quarter == 4:
            year += 1
            quarter = 1
        else:
            quarter += 1
    
    return total_rows


# ============================================================
# 主函数
# ============================================================

def main():
    beijing_time = get_beijing_time()
    today_str = beijing_time.strftime('%Y-%m-%d')
    
    print("=" * 70)
    print("  [UPDATE] 季频现金流量数据同步 (stock_cash_flow_quarterly)")
    print(f"  [时间] {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  [数据范围] {START_YEAR}年至今")
    print("=" * 70, flush=True)
    
    conn = get_connection_with_retry()
    print("[DB] Database connection established!", flush=True)
    
    try:
        print("\n[1] Getting active stocks from stock_basic...")
        all_stocks = get_active_stocks_from_db(conn)
        if not all_stocks:
            print("  [ERROR] No stocks found in stock_basic")
            return
        
        print(f"\n[2] Querying existing cash_flow data from database for {len(all_stocks)} stocks...")
        cash_flow_status = get_cash_flow_latest_dates(conn, all_stocks)
        with_data_count = len([v for v in cash_flow_status.values() if v is not None])
        print(f"  [INFO] {with_data_count} stocks already have data")
        
        target_year, target_quarter = get_target_quarter()
        print(f"\n[3] Target quarter: {target_year} Q{target_quarter}")
        print("-" * 70, flush=True)
        
        db_buffer = []
        db_buffer_limit = 150
        total_rows = 0
        updated_count = 0
        skip_count = 0
        fail_count = 0
        
        total_stocks = len(all_stocks)
        start_time = time.time()
        
        for idx, code in enumerate(all_stocks, 1):
            last_date = cash_flow_status.get(code)
            
            stock_start_time = time.time()
            if last_date:
                last_dt = pd.to_datetime(last_date)
                last_year = last_dt.year
                last_quarter = (last_dt.month - 1) // 3 + 1
                if last_year > target_year or (last_year == target_year and last_quarter >= target_quarter):
                    skip_count += 1
                    stock_elapsed = time.time() - stock_start_time
                    print(f"  {code} | 跳过 (已是最新) | 耗时: {stock_elapsed:.3f}s", flush=True)
                    
                    if idx % 100 == 0 or idx == 1 or idx == total_stocks:
                        print_progress(idx, total_stocks, start_time, "[进度] ")
                    continue
            
            rows = update_stock_data(conn, code, last_date, target_year, target_quarter, today_str, db_buffer, db_buffer_limit)
            stock_elapsed = time.time() - stock_start_time
            
            if rows == -1:
                fail_count += 1
                print(f"  {code} | 失败 | 耗时: {stock_elapsed:.3f}s", flush=True)
            elif rows > 0:
                updated_count += 1
                total_rows += rows
                if last_date:
                    last_dt = pd.to_datetime(last_date)
                    start_year = last_dt.year
                    start_quarter = (last_dt.month - 1) // 3 + 1
                    if start_quarter == 4:
                        start_year += 1
                        start_quarter = 1
                    else:
                        start_quarter += 1
                else:
                    start_year = START_YEAR
                    start_quarter = 1
                print(f"  {code} | 写入 {rows} 条数据 | {start_year}Q{start_quarter} ~ {target_year}Q{target_quarter} | 耗时: {stock_elapsed:.3f}s", flush=True)
            else:
                if last_date:
                    last_dt = pd.to_datetime(last_date)
                    start_year = last_dt.year
                    start_quarter = (last_dt.month - 1) // 3 + 1
                    if start_quarter == 4:
                        start_year += 1
                        start_quarter = 1
                    else:
                        start_quarter += 1
                else:
                    start_year = START_YEAR
                    start_quarter = 1
                print(f"  {code} | 无新数据 | {start_year}Q{start_quarter} ~ {target_year}Q{target_quarter} | 耗时: {stock_elapsed:.3f}s", flush=True)
            
            if idx % 100 == 0 or idx == 1 or idx == total_stocks:
                print_progress(idx, total_stocks, start_time, "[进度] ")
            
            if idx % 10 == 0:
                random_sleep(0.3, 0.8)
        
        if db_buffer:
            print("\nFlushing remaining data...")
            conn = flush_db_buffer(conn, db_buffer)
            conn.commit()
            db_buffer = []
        
        print("\n" + "=" * 70)
        print("📊 现金流量数据同步汇总")
        print(f"  [总股票数]          : {total_stocks}")
        print(f"  [已最新跳过]        : {skip_count}")
        print(f"  [成功更新]          : {updated_count}")
        print(f"  [写入行数]          : {total_rows}")
        print(f"  [失败]              : {fail_count}")
        print(f"  [目标季度]          : {target_year} Q{target_quarter}")
        print(f"  [起始年份]          : {START_YEAR}")
        print("=" * 70, flush=True)
        
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"\nFatal error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            bs.logout()
        except Exception:
            pass
        conn.close()


if __name__ == "__main__":
    main()