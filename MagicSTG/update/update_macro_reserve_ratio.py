#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
macro_reserve_ratio 存款准备金率数据更新脚本
从 Baostock 拉取存款准备金率数据
"""

import sys
import os
import time
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
)

# ============================================================
# 配置
# ============================================================

START_DATE = "1990-01-01"


# ============================================================
# 数据库操作
# ============================================================

def get_existing_dates(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pub_date FROM macro_reserve_ratio")
            rows = cur.fetchall()
            return {row[0].strftime('%Y-%m-%d') for row in rows}
    except Exception as e:
        print(f"  [DB] Query existing dates failed: {e}", flush=True)
        return set()


def build_insert_sql():
    sql = """
    INSERT INTO macro_reserve_ratio (
        pub_date, effective_date, big_before, big_after, medium_before, medium_after
    ) VALUES (%s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        effective_date = VALUES(effective_date),
        big_before = VALUES(big_before),
        big_after = VALUES(big_after),
        medium_before = VALUES(medium_before),
        medium_after = VALUES(medium_after);
    """
    return sql


def flush_db_buffer(conn, batch_data):
    if not batch_data:
        return conn
    
    sql = build_insert_sql()
    flat_args = []
    for record in batch_data:
        flat_args.extend(record)
    
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

def fetch_reserve_ratio(start_date, end_date, max_retries=3):
    for attempt in range(max_retries):
        try:
            if not ensure_bs_login():
                time.sleep(2)
                continue
            
            rs = bs.query_required_reserve_ratio_data(start_date=start_date, end_date=end_date)
            if rs.error_code != '0':
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                return None, False
            
            data_list = []
            while rs.next():
                data_list.append(rs.get_row_data())
            return data_list, True
            
        except Exception as e:
            print(f"  [WARN] Fetch error (attempt {attempt+1}/{max_retries}): {e}", flush=True)
            if attempt < max_retries - 1:
                time.sleep(3)
            else:
                return None, False
    return None, False


def parse_reserve_row(row):
    pub_date = row[0] if row[0] else None
    if not pub_date:
        return None
    
    return (
        pub_date,
        row[1] if row[1] else None,
        safe_float(row[2]),
        safe_float(row[3]),
        safe_float(row[4]),
        safe_float(row[5]),
    )


# ============================================================
# 主函数
# ============================================================

def main():
    beijing_time = get_beijing_time()
    end_date = beijing_time.strftime('%Y-%m-%d')
    
    print("=" * 70)
    print("  [UPDATE] 存款准备金率数据同步 (macro_reserve_ratio)")
    print(f"  [时间] {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  [查询范围] {START_DATE} ~ {end_date}")
    print("=" * 70, flush=True)
    
    conn = get_connection_with_retry()
    print("[DB] Database connection established!", flush=True)
    
    try:
        print("\n[1] Querying existing dates from database...")
        existing_dates = get_existing_dates(conn)
        print(f"  [INFO] Found {len(existing_dates)} existing records")
        
        print(f"\n[2] Fetching reserve ratio data from Baostock ({START_DATE} ~ {end_date})...")
        data_list, ok = fetch_reserve_ratio(START_DATE, end_date)
        if not ok or data_list is None:
            print("  [ERROR] Failed to fetch data")
            return
        
        print(f"  [INFO] Fetched {len(data_list)} records")
        
        print("\n[3] Syncing to database...")
        db_buffer = []
        db_buffer_limit = 100
        inserted_count = 0
        
        for row in data_list:
            db_row = parse_reserve_row(row)
            if db_row:
                db_buffer.append(db_row)
                inserted_count += 1
            
            if len(db_buffer) >= db_buffer_limit:
                conn = flush_db_buffer(conn, db_buffer)
                conn.commit()
                db_buffer = []
        
        if db_buffer:
            conn = flush_db_buffer(conn, db_buffer)
            conn.commit()
            db_buffer = []
        
        print("\n" + "=" * 70)
        print("📊 存款准备金率数据同步汇总")
        print(f"  [拉取记录数]        : {len(data_list)}")
        print(f"  [写入/更新记录数]   : {inserted_count}")
        print(f"  [查询范围]          : {START_DATE} ~ {end_date}")
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