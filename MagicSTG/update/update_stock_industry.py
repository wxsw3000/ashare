#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_industry 表更新脚本
从 Baostock 同步行业分类信息
更新频率：每周一更新（Baostock 每周一更新行业分类）
"""

import os
import sys
import time
from datetime import datetime
import baostock as bs
import pandas as pd
import pymysql

try:
    from dotenv import load_dotenv
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ENV_PATH = os.path.join(PROJECT_ROOT, 'dbconfig', '.env')
    if os.path.exists(ENV_PATH):
        load_dotenv(ENV_PATH)
        print(f"[ENV] Loaded .env from: {ENV_PATH}", flush=True)
except ImportError:
    pass

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "")
DB_SSL_CA = os.getenv("DB_SSL_CA", "")


def get_connection():
    """建立数据库连接"""
    is_github_actions = os.environ.get('GITHUB_ACTIONS') == 'true'
    
    if is_github_actions:
        ssl_ca = "/etc/ssl/cert.pem"
    else:
        ssl_ca = DB_SSL_CA
        if ssl_ca and not os.path.exists(ssl_ca):
            filename = os.path.basename(ssl_ca)
            for path_candidate in [
                os.path.join(PROJECT_ROOT, 'dbconfig', filename),
                os.path.join(PROJECT_ROOT, filename),
            ]:
                if os.path.exists(path_candidate):
                    ssl_ca = path_candidate
                    break
        if ssl_ca and os.path.exists(ssl_ca):
            print(f"[SSL] Using CA: {ssl_ca}", flush=True)
        else:
            ssl_ca = "/etc/ssl/cert.pem" if os.path.exists("/etc/ssl/cert.pem") else None
    
    conn_params = {
        "host": DB_HOST,
        "port": DB_PORT,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "database": DB_NAME,
        "charset": "utf8mb4",
        "autocommit": False,
        "connect_timeout": 15,
        "read_timeout": 60,
    }
    
    if ssl_ca and os.path.exists(ssl_ca):
        conn_params["ssl"] = {"ca": ssl_ca, "verify_cert": True, "verify_identity": True}
    else:
        conn_params["ssl"] = {"verify_cert": False, "verify_identity": False}
    
    return pymysql.connect(**conn_params)


def ensure_bs_login():
    """确保 Baostock 已登录"""
    try:
        rs = bs.query_stock_basic()
        if rs.error_code == '0':
            return True
    except Exception:
        pass
    
    try:
        bs.logout()
    except Exception:
        pass
    time.sleep(1)
    lg = bs.login()
    if lg.error_code != '0':
        print(f"[Baostock] Login failed: {lg.error_msg}", flush=True)
        return False
    print("[Baostock] Login successful", flush=True)
    return True


def fetch_stock_industry(date=None):
    """
    从 Baostock 获取行业分类数据
    参数:
        date: 查询日期，格式 YYYY-MM-DD，为空时获取最新数据
    返回: list of dict
    """
    if not ensure_bs_login():
        return []
    
    rs = bs.query_stock_industry(date=date)
    if rs.error_code != '0':
        print(f"[Baostock] query_stock_industry failed: {rs.error_msg}", flush=True)
        return []
    
    data_list = []
    while rs.next():
        row = rs.get_row_data()
        # row 顺序: updateDate, code, code_name, industry, industryClassification
        data_list.append({
            'code': row[1],
            'code_name': row[2],
            'industry': row[3],
            'industry_classification': row[4],
            'update_date': row[0],
        })
    
    print(f"[Baostock] Fetched {len(data_list)} industry records", flush=True)
    return data_list


def get_db_existing_codes(conn):
    """
    查询数据库中已有的行业记录代码
    返回: set
    """
    with conn.cursor() as cur:
        cur.execute("SELECT code FROM stock_industry")
        rows = cur.fetchall()
        return {row[0] for row in rows}


def sync_stock_industry(conn, data_list):
    """
    全量同步 stock_industry 表
    使用 INSERT ... ON DUPLICATE KEY UPDATE
    """
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
    # 统计有行业分类的股票数
    with_industry = [d for d in data_list if d['industry'] and d['industry'] != '']
    without_industry = [d for d in data_list if not d['industry'] or d['industry'] == '']
    
    # 行业分类标准统计
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
    
    # 显示行业股票数量 TOP 10
    top_industries = get_stock_count_by_industry(conn)
    print("  行业股票数量 TOP 10:")
    for i, (industry, count) in enumerate(top_industries[:10], 1):
        print(f"    {i:2d}. {industry}: {count} 只")
    
    print("=" * 70, flush=True)


def main():
    print("=" * 70)
    print("  [UPDATE] stock_industry 行业分类同步")
    print(f"  [时间] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70, flush=True)
    
    # 数据库连接
    conn = None
    for attempt in range(1, 4):
        try:
            conn = get_connection()
            print("[DB] Database connection established!", flush=True)
            break
        except Exception as e:
            print(f"Failed to connect (attempt {attempt}/3): {e}", flush=True)
            if attempt < 3:
                time.sleep(2)
            else:
                print("Error: Could not establish database connection. Exiting.", flush=True)
                return
    
    # 从 Baostock 获取数据（使用最新日期）
    print("\n[1] Fetching industry data from Baostock (latest)...", flush=True)
    data_list = fetch_stock_industry(date=None)
    if not data_list:
        print("  [ERROR] No data fetched", flush=True)
        if conn:
            conn.close()
        return
    
    # 同步到数据库
    print("\n[2] Syncing to database...", flush=True)
    try:
        inserted = sync_stock_industry(conn, data_list)
        conn.commit()
        print(f"  [SUCCESS] {inserted} records inserted/updated", flush=True)
        
        # 打印汇总
        print_summary(data_list, inserted, conn)
        
    except Exception as e:
        conn.rollback()
        print(f"  [ERROR] Failed to sync: {e}", flush=True)
        import traceback
        traceback.print_exc()
    finally:
        try:
            bs.logout()
        except Exception:
            pass
        if conn:
            conn.close()


if __name__ == "__main__":
    main()