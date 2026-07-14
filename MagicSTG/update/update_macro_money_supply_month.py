#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
macro_money_supply_month 货币供应量（月）数据更新脚本
从 Baostock 拉取货币供应量月度数据
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

START_DATE = "1990-01"


# ============================================================
# 数据库操作
# ============================================================

def get_existing_records(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT stat_year, stat_month FROM macro_money_supply_month")
            rows = cur.fetchall()
            return {(row[0], row[1]) for row in rows}
    except Exception as e:
        print(f"  [DB] Query existing records failed: {e}", flush=True)
        return set()


def build_insert_sql():
    sql = """
    INSERT INTO macro_money_supply_month (
        stat_year, stat_month, m0, m0_yoy, m0_mom, m1, m1_yoy, m1_mom, m2, m2_yoy, m2_mom
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        m0 = VALUES(m0),
        m0_yoy = VALUES(m0_yoy),
        m0_mom = VALUES(m0_mom),
        m1 = VALUES(m1),
        m1_yoy = VALUES(m1_yoy),
        m1_mom = VALUES(m1_mom),
        m2 = VALUES(m2),
        m2_yoy = VALUES(m2_yoy),
        m2_mom = VALUES(m2_mom);
    """
    return sql


def flush_db_buffer(conn, batch_data):
    if not batch_data:
        return conn
    
    sql = build_insert_sql()
    for attempt in range(1, 4):
        try:
            with conn.cursor() as cursor:
                cursor.executemany(sql, batch_data)
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

def fetch_money_supply_month(start_date, end_date, max_retries=3):
    for attempt in range(max_retries):
        try:
            if not ensure_bs_login():
                time.sleep(2)
                continue
            
            rs = bs.query_money_supply_data_month(start_date=start_date, end_date=end_date)
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


def parse_money_supply_month_row(row):
    stat_year = safe_int(row[0])
    stat_month = safe_int(row[1])
    
    if stat_year is None or stat_month is None:
        return None
    
    return (
        stat_year,
        stat_month,
        safe_float(row[2]),
        safe_float(row[3]),
        safe_float(row[4]),
        safe_float(row[5]),
        safe_float(row[6]),
        safe_float(row[7]),
        safe_float(row[8]),
        safe_float(row[9]),
        safe_float(row[10]),
    )


# ============================================================
# 主函数
# ============================================================

def main():
    beijing_time = get_beijing_time()
    end_date = beijing_time.strftime('%Y-%m')
    
    print("=" * 70)
    print("  [UPDATE] 货币供应量（月）数据同步 (macro_money_supply_month)")
    print(f"  [时间] {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  [查询范围] {START_DATE} ~ {end_date}")
    print("=" * 70, flush=True)
    
    conn = get_connection_with_retry()
    print("[DB] Database connection established!", flush=True)
    
    try:
        print("\n[1] Querying existing records from database...")
        existing_records = get_existing_records(conn)
        print(f"  [INFO] Found {len(existing_records)} existing records")
        
        print(f"\n[2] Fetching money supply (monthly) data from Baostock ({START_DATE} ~ {end_date})...")
        data_list, ok = fetch_money_supply_month(START_DATE, end_date)
        if not ok or data_list is None:
            print("  [ERROR] Failed to fetch data")
            return
        
        print(f"  [INFO] Fetched {len(data_list)} records")
        
        print("\n[3] Syncing to database...")
        db_buffer = []
        db_buffer_limit = 100
        inserted_count = 0
        
        for row in data_list:
            db_row = parse_money_supply_month_row(row)
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
        print("📊 货币供应量（月）数据同步汇总")
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
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()