#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_basic 表更新脚本
从 Baostock 全量同步证券基本资料
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


def fetch_all_stock_basic():
    """
    从 Baostock 获取全量证券基本资料
    返回: list of dict
    """
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
            'type': row[4],      # 1:股票, 2:指数, 3:其它, 4:可转债, 5:ETF
            'status': row[5],    # 1:上市, 0:退市
        })
    
    print(f"[Baostock] Fetched {len(data_list)} securities", flush=True)
    return data_list


def get_db_existing_codes(conn):
    """
    查询数据库中已有的股票代码
    返回: set
    """
    with conn.cursor() as cur:
        cur.execute("SELECT code FROM stock_basic")
        rows = cur.fetchall()
        return {row[0] for row in rows}


def sync_stock_basic(conn, data_list):
    """
    全量同步 stock_basic 表
    使用 INSERT ... ON DUPLICATE KEY UPDATE
    """
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
    
    update_date = datetime.now().strftime('%Y-%m-%d')
    
    # 准备数据
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
    # 统计股票和指数
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
    
    # 从 Baostock 获取数据
    print("\n[1] Fetching stock basic data from Baostock...", flush=True)
    data_list = fetch_all_stock_basic()
    if not data_list:
        print("  [ERROR] No data fetched", flush=True)
        if conn:
            conn.close()
        return
    
    # 同步到数据库
    print("\n[2] Syncing to database...", flush=True)
    try:
        inserted = sync_stock_basic(conn, data_list)
        conn.commit()
        print(f"  [SUCCESS] {inserted} records inserted/updated", flush=True)
        
        # 打印汇总
        print_summary(data_list, inserted)
        
    except Exception as e:
        conn.rollback()
        print(f"  [ERROR] Failed to sync: {e}", flush=True)
    finally:
        try:
            bs.logout()
        except Exception:
            pass
        if conn:
            conn.close()


if __name__ == "__main__":
    main()