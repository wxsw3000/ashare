#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_basic 证券基本资料更新脚本
从 Baostock 拉取全量证券基本资料
数据范围：全量
"""

import sys
import os
import time

# 添加父目录到路径，以便导入 db 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import baostock as bs
import pandas as pd

from db import (
    get_connection,
    get_connection_with_retry,
    safe_float,
    safe_str,
    safe_int,
    get_beijing_time,
    ensure_bs_login,
    random_sleep,
    print_progress,
)


def fetch_all_stock_basic():
    """从 Baostock 获取全量证券基本资料"""
    if not ensure_bs_login():
        return []
    
    rs = bs.query_stock_basic()
    if rs.error_code != '0':
        print(f"[Baostock] query_stock_basic failed: {rs.error_msg}", flush=True)
        return []
    
    data_list = []
    while rs.next():
        row = rs.get_row_data()
        data_list.append({
            'code': row[0],
            'code_name': row[1],
            'ipo_date': row[2] if row[2] else None,
            'out_date': row[3] if row[3] else None,
            'type': row[4],
            'status': row[5],
        })
    
    print(f"[Baostock] Fetched {len(data_list)} securities", flush=True)
    return data_list


def sync_stock_basic(conn, data_list):
    """全量同步 stock_basic 表"""
    if not data_list:
        return 0
    
    sql = """
    INSERT INTO stock_basic (code, code_name, ipo_date, out_date, type, status, update_date)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        code_name = VALUES(code_name),
        ipo_date = VALUES(ipo_date),
        out_date = VALUES(out_date),
        type = VALUES(type),
        status = VALUES(status),
        update_date = VALUES(update_date)
    """
    
    update_date = get_beijing_time().strftime('%Y-%m-%d')
    
    values = []
    for item in data_list:
        values.append((
            item['code'],
            item['code_name'],
            item['ipo_date'],
            item['out_date'],
            item['type'],
            item['status'],
            update_date
        ))
    
    with conn.cursor() as cur:
        cur.executemany(sql, values)
    
    return len(values)


def print_summary(data_list, inserted_count):
    """打印汇总信息"""
    stocks = [d for d in data_list if d['type'] == '1']
    indices = [d for d in data_list if d['type'] == '2']
    active_stocks = [d for d in stocks if d['status'] == '1']
    inactive_stocks = [d for d in stocks if d['status'] == '0']
    
    print("=" * 70)
    print("📊 stock_basic 同步汇总")
    print(f"  [总证券数]          : {len(data_list)}")
    print(f"  [股票]              : {len(stocks)} (上市: {len(active_stocks)}, 退市: {len(inactive_stocks)})")
    print(f"  [指数]              : {len(indices)}")
    print(f"  [其他/可转债/ETF]   : {len(data_list) - len(stocks) - len(indices)}")
    print(f"  [写入/更新记录数]   : {inserted_count}")
    print("=" * 70, flush=True)


def main():
    print("=" * 70)
    print("  [UPDATE] stock_basic 全量同步")
    beijing_time = get_beijing_time()
    print(f"  [时间] {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70, flush=True)
    
    # 使用公共模块获取连接
    conn = get_connection_with_retry()
    print("[DB] Database connection established!", flush=True)
    
    try:
        print("\n[1] Fetching stock basic data from Baostock...")
        data_list = fetch_all_stock_basic()
        if not data_list:
            print("  [ERROR] No data fetched")
            return
        
        print("\n[2] Syncing to database...")
        inserted = sync_stock_basic(conn, data_list)
        conn.commit()
        print(f"  [SUCCESS] {inserted} records inserted/updated")
        
        print_summary(data_list, inserted)
        
    except Exception as e:
        conn.rollback()
        print(f"  [ERROR] Failed to sync: {e}")
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