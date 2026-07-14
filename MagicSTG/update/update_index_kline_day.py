#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
index_kline_day 指数日线数据更新脚本
从 stock_basic 获取所有指数（type='2'），从 Baostock 拉取日K线数据
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

INDEX_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,pctChg"
START_DATE = "2020-01-01"


# ============================================================
# 数据库操作
# ============================================================

def get_all_indices_from_db(conn):
    """从 stock_basic 获取所有指数（type='2' 且 status='1'）"""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT code FROM stock_basic WHERE type = '2' AND status = '1'")
            rows = cur.fetchall()
            indices = [row[0] for row in rows]
            print(f"  [DB] Found {len(indices)} indices from stock_basic", flush=True)
            return indices
    except Exception as e:
        print(f"  [ERROR] Failed to get indices from stock_basic: {e}", flush=True)
        return []


def get_index_latest_dates(conn, codes):
    """查询各指数在 index_kline_day 表中的最新日期"""
    if not codes:
        return {}
    
    result = {}
    
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'index_kline_day'")
            if cur.fetchone() is None:
                print("  [INFO] index_kline_day table does not exist yet", flush=True)
                for code in codes:
                    result[code] = None
                return result
            
            cur.execute("SELECT COUNT(*) FROM index_kline_day")
            count = cur.fetchone()[0]
            if count == 0:
                print("  [INFO] index_kline_day is empty, all indices need full update", flush=True)
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
                SELECT code, MAX(date) 
                FROM index_kline_day 
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
        print(f"  [ERROR] Failed to query index latest dates: {e}", flush=True)
        for code in codes:
            result[code] = None
    
    return result


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
        `preclose` = VALUES(`preclose`),
        `volume` = VALUES(`volume`),
        `amount` = VALUES(`amount`),
        `pctChg` = VALUES(`pctChg`),
        `update_date` = VALUES(`update_date`);
    """
    return sql


def flush_db_buffer(conn, batch_data):
    """批量插入指数数据"""
    if not batch_data:
        return conn
    
    fields = ['date', 'code', 'open', 'high', 'low', 'close', 'preclose', 
              'volume', 'amount', 'pctChg', 'update_date']
    
    sql = build_insert_sql('index_kline_day', fields, len(batch_data))
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

def fetch_index_kline(code, start_date, end_date, max_retries=3):
    """从 Baostock 查询指数日K线数据"""
    for attempt in range(max_retries):
        try:
            if not ensure_bs_login():
                time.sleep(2)
                continue
            
            rs = bs.query_history_k_data_plus(
                code,
                INDEX_FIELDS,
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="3"
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


def parse_index_row(row, code, update_date):
    """解析指数日K线数据行"""
    date_val = row[0]
    open_val = safe_float(row[2])
    high_val = safe_float(row[3])
    low_val = safe_float(row[4])
    close_val = safe_float(row[5])
    preclose_val = safe_float(row[6])
    
    if open_val is None or high_val is None or low_val is None or close_val is None:
        return None
    
    return (
        date_val,
        code,
        open_val,
        high_val,
        low_val,
        close_val,
        preclose_val,
        safe_int(row[7], 0),
        safe_float(row[8], 0.0),
        safe_float(row[9]),
        update_date
    )


# ============================================================
# 核心更新逻辑
# ============================================================

def update_index_data(conn, code, last_date, target_date, update_date, db_buffer, db_buffer_limit):
    """更新单只指数的日K线数据"""
    total_rows = 0
    
    if last_date and last_date >= target_date:
        return 0
    
    if last_date:
        start_date = (pd.to_datetime(last_date) + timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        start_date = START_DATE
    
    data_list, ok = fetch_index_kline(code, start_date, target_date)
    if not ok or data_list is None:
        return -1
    
    if len(data_list) == 0:
        return 0
    
    for row in data_list:
        db_row = parse_index_row(row, code, update_date)
        if db_row:
            db_buffer.append(db_row)
            total_rows += 1
        
        if len(db_buffer) >= db_buffer_limit:
            conn = flush_db_buffer(conn, db_buffer)
            conn.commit()
            db_buffer = []
    
    return total_rows


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
    print("  [UPDATE] 指数日线数据增量同步 (index_kline_day)")
    print(f"  [时间] {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  [数据范围] {START_DATE} 至今")
    print(f"  [目标日期] {target_date}")
    print(f"  [最大时长] {MAX_RUNTIME}秒")
    print("=" * 70, flush=True)
    
    conn = get_connection_with_retry()
    print("[DB] Database connection established!", flush=True)
    
    try:
        print("\n[1] Getting all indices from stock_basic...")
        all_indices = get_all_indices_from_db(conn)
        if not all_indices:
            print("  [ERROR] No indices found in stock_basic")
            return
        
        print(f"  [INFO] Total indices: {len(all_indices)}")
        
        print("\n[2] Querying existing index data from database...")
        index_status = get_index_latest_dates(conn, all_indices)
        with_data_count = len([v for v in index_status.values() if v is not None])
        print(f"  [INFO] {with_data_count} indices already have data")
        
        print(f"\n[3] Updating indices (target: {target_date})...")
        print("-" * 70, flush=True)
        
        db_buffer = []
        db_buffer_limit = 150
        total_rows = 0
        updated_count = 0
        skip_count = 0
        fail_count = 0
        
        total_indices = len(all_indices)
        start_time = time.time()
        early_exit = False
        
        for idx, code in enumerate(all_indices, 1):
            elapsed = time.time() - start_time
            if elapsed > MAX_RUNTIME:
                print(f"\n  [WARN] Reached maximum runtime limit ({elapsed:.1f}s > {MAX_RUNTIME}s), exiting gracefully...", flush=True)
                early_exit = True
                break
                
            last_date = index_status.get(code)
            
            if last_date and last_date >= target_date:
                skip_count += 1
                if idx % 10 == 0 or idx == 1 or idx == total_indices:
                    elapsed = time.time() - start_time
                    avg_time = elapsed / idx if idx > 0 else 0
                    remaining = avg_time * (total_indices - idx)
                    pct = (idx / total_indices) * 100
                    print(f"  PROGRESS: {idx}/{total_indices} ({pct:.1f}%) "
                          f"已用: {format_time(elapsed)} 剩余: {format_time(remaining)} | "
                          f"更新: {updated_count} 跳过: {skip_count} 失败: {fail_count}", flush=True)
                continue
            
            rows = update_index_data(conn, code, last_date, target_date, update_date, db_buffer, db_buffer_limit)
            
            if rows == -1:
                fail_count += 1
                print(f"  [FAIL] {code}", flush=True)
            elif rows > 0:
                updated_count += 1
                total_rows += rows
                if idx % 10 == 0 or idx == 1 or idx == total_indices:
                    elapsed = time.time() - start_time
                    avg_time = elapsed / idx if idx > 0 else 0
                    remaining = avg_time * (total_indices - idx)
                    pct = (idx / total_indices) * 100
                    print(f"  PROGRESS: {idx}/{total_indices} ({pct:.1f}%) "
                          f"已用: {format_time(elapsed)} 剩余: {format_time(remaining)} | "
                          f"更新: {updated_count} 跳过: {skip_count} 失败: {fail_count}", flush=True)
            else:
                if idx % 10 == 0 or idx == 1 or idx == total_indices:
                    elapsed = time.time() - start_time
                    avg_time = elapsed / idx if idx > 0 else 0
                    remaining = avg_time * (total_indices - idx)
                    pct = (idx / total_indices) * 100
                    print(f"  PROGRESS: {idx}/{total_indices} ({pct:.1f}%) "
                          f"已用: {format_time(elapsed)} 剩余: {format_time(remaining)} | "
                          f"更新: {updated_count} 跳过: {skip_count} 失败: {fail_count}", flush=True)
            
            if idx % 5 == 0:
                random_sleep(0.2, 0.5)
        
        if db_buffer:
            print("\nFlushing remaining data...")
            conn = flush_db_buffer(conn, db_buffer)
            conn.commit()
            db_buffer = []
        
        print("\n" + "=" * 70)
        print("📊 指数数据同步汇总")
        print(f"  [总指数数]          : {total_indices}")
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