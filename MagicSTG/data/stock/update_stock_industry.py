#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_industry 证券行业信息更新脚本
从 Baostock 拉取行业分类信息
"""

import sys
import os
import time

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


def fetch_stock_industry(date=None):
    """从 Baostock 获取行业分类数据"""
    if not ensure_bs_login():
        return []
    
    rs = bs.query_stock_industry(date=date)
    if rs.error_code != '0':
        print(f"[Baostock] query_stock_industry failed: {rs.error_msg}", flush=True)
        return []
    
    data_list = []
    while rs.next():
        row = rs.get_row_data()
        data_list.append({
            'code': row[1],
            'code_name': row[2],
            'industry': row[3],
            'industry_classification': row[4],
            'update_date': row[0],
        })
    
    print(f"[Baostock] Fetched {len(data_list)} industry records", flush=True)
    return data_list


def sync_stock_industry(conn, data_list):
    """全量同步 stock_industry 表"""
    if not data_list:
        return 0
    
    sql = """
    INSERT INTO stock_industry (code, code_name, industry, industry_classification, update_date)
    VALUES (%s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        code_name = VALUES(code_name),
        industry = VALUES(industry),
        industry_classification = VALUES(industry_classification),
        update_date = VALUES(update_date)
    """
    
    values = []
    for item in data_list:
        values.append((
            item['code'],
            item['code_name'],
            item['industry'],
            item['industry_classification'],
            item['update_date'],
        ))
    
    with conn.cursor() as cur:
        cur.executemany(sql, values)
    
    return len(values)


def get_stock_count_by_industry(conn):
    """统计各行业的股票数量"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 
                industry,
                COUNT(*) as stock_count
            FROM stock_industry
            WHERE industry IS NOT NULL AND industry != ''
            GROUP BY industry
            ORDER BY stock_count DESC
        """)
        rows = cur.fetchall()
        return rows


def print_summary(data_list, inserted_count, conn):
    """打印汇总信息"""
    with_industry = [d for d in data_list if d['industry'] and d['industry'] != '']
    without_industry = [d for d in data_list if not d['industry'] or d['industry'] == '']
    
    classification_counts = {}
    for d in data_list:
        cls = d['industry_classification'] or '未分类'
        classification_counts[cls] = classification_counts.get(cls, 0) + 1
    
    print("=" * 70)
    print("📊 stock_industry 同步汇总")
    print(f"  [总记录数]          : {len(data_list)}")
    print(f"  [有行业分类]        : {len(with_industry)}")
    print(f"  [无行业分类]        : {len(without_industry)}")
    print(f"  [写入/更新记录数]   : {inserted_count}")
    print()
    print("  行业分类标准分布:")
    for cls, count in classification_counts.items():
        print(f"    - {cls}: {count}")
    print()
    
    top_industries = get_stock_count_by_industry(conn)
    print("  行业股票数量 TOP 10:")
    for i, (industry, count) in enumerate(top_industries[:10], 1):
        print(f"    {i:2d}. {industry}: {count} 只")
    print("=" * 70, flush=True)


def main():
    print("=" * 70)
    print("  [UPDATE] stock_industry 行业分类同步")
    beijing_time = get_beijing_time()
    print(f"  [时间] {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70, flush=True)
    
    conn = get_connection_with_retry()
    print("[DB] Database connection established!", flush=True)
    
    try:
        print("\n[1] Fetching industry data from Baostock (latest)...")
        data_list = fetch_stock_industry(date=None)
        if not data_list:
            print("  [ERROR] No data fetched")
            return
        
        print("\n[2] Syncing to database...")
        inserted = sync_stock_industry(conn, data_list)
        conn.commit()
        print(f"  [SUCCESS] {inserted} records inserted/updated")
        
        print_summary(data_list, inserted, conn)
        
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