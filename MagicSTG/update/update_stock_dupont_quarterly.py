#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_dupont_quarterly 季频杜邦指数数据更新脚本
从 stock_basic 获取股票列表（type='1'），从 Baostock 拉取杜邦指数数据
"""

import os
import sys
import time
import random
from datetime import datetime, timedelta
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

DUPONT_FIELDS = "code,pubDate,statDate,dupontROE,dupontAssetStoEquity,dupontAssetTurn,dupontPnitoni,dupontNitogr,dupontTaxBurden,dupontIntburden,dupontEbittogr"


def get_beijing_time():
    return datetime.utcnow() + timedelta(hours=8)


def get_connection():
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


def check_stock_basic_has_data(conn):
    """检查 stock_basic 表是否有数据"""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM stock_basic")
            count = cur.fetchone()[0]
            if count == 0:
                print("  [ERROR] stock_basic is empty!", flush=True)
                print("  [HINT] Please run update_stock_basic.py first!", flush=True)
                return False
            print(f"  [INFO] stock_basic has {count} records", flush=True)
            return True
    except Exception as e:
        print(f"  [ERROR] Failed to check stock_basic: {e}", flush=True)
        return False


def get_active_stocks_from_db(conn):
    """
    从 stock_basic 获取上市股票列表（type='1' 且 status='1'）
    返回: list of codes
    """
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


def get_dupont_latest_dates(conn, codes):
    """
    批量查询各股票在 dupont 表中的最新 stat_date
    返回: {'sh.600000': '2024-12-31', ...}
    """
    if not codes:
        return {}
    
    result = {}
    
    try:
        with conn.cursor() as cur:
            # 检查表是否存在
            cur.execute("SHOW TABLES LIKE 'stock_dupont_quarterly'")
            if cur.fetchone() is None:
                print("  [INFO] stock_dupont_quarterly table does not exist yet", flush=True)
                for code in codes:
                    result[code] = None
                return result
            
            # 检查表是否为空
            cur.execute("SELECT COUNT(*) FROM stock_dupont_quarterly")
            count = cur.fetchone()[0]
            if count == 0:
                print("  [INFO] stock_dupont_quarterly is empty, all stocks need full update", flush=True)
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
                FROM stock_dupont_quarterly 
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
        print(f"  [ERROR] Failed to query dupont latest dates: {e}", flush=True)
        for code in codes:
            result[code] = None
    
    return result


def fetch_dupont_data(code, year, quarter, max_retries=3):
    for attempt in range(max_retries):
        try:
            if not ensure_bs_login():
                time.sleep(2)
                continue
            
            rs = bs.query_dupont_data(code=code, year=year, quarter=quarter)
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


def safe_float(val, default=None):
    if val is None or val == "" or pd.isna(val):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def build_insert_sql(table_name, fields, batch_size):
    placeholders = ", ".join(["(" + ", ".join(["%s"] * len(fields)) + ")"] * batch_size)
    sql = f"""
    INSERT INTO `{table_name}` ({', '.join(['`' + f + '`' for f in fields])})
    VALUES {placeholders}
    ON DUPLICATE KEY UPDATE
        `pub_date` = VALUES(`pub_date`),
        `dupontROE` = VALUES(`dupontROE`),
        `dupontAssetStoEquity` = VALUES(`dupontAssetStoEquity`),
        `dupontAssetTurn` = VALUES(`dupontAssetTurn`),
        `dupontPnitoni` = VALUES(`dupontPnitoni`),
        `dupontNitogr` = VALUES(`dupontNitogr`),
        `dupontTaxBurden` = VALUES(`dupontTaxBurden`),
        `dupontIntburden` = VALUES(`dupontIntburden`),
        `dupontEbittogr` = VALUES(`dupontEbittogr`),
        `update_date` = VALUES(`update_date`);
    """
    return sql


def flush_db_buffer(conn, batch_data):
    if not batch_data:
        return conn
    
    fields = ['code', 'stat_date', 'pub_date', 'dupontROE', 'dupontAssetStoEquity',
              'dupontAssetTurn', 'dupontPnitoni', 'dupontNitogr', 'dupontTaxBurden',
              'dupontIntburden', 'dupontEbittogr', 'update_date']
    
    sql = build_insert_sql('stock_dupont_quarterly', fields, len(batch_data))
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
                conn = get_connection()
            else:
                raise e
    return conn


def parse_dupont_row(row, update_date):
    code = row[0]
    pub_date = row[1] if row[1] else None
    stat_date = row[2] if row[2] else None
    
    if not stat_date:
        return None
    
    return (
        code,
        stat_date,
        pub_date,
        safe_float(row[3]),  # dupontROE
        safe_float(row[4]),  # dupontAssetStoEquity
        safe_float(row[5]),  # dupontAssetTurn
        safe_float(row[6]),  # dupontPnitoni
        safe_float(row[7]),  # dupontNitogr
        safe_float(row[8]),  # dupontTaxBurden
        safe_float(row[9]),  # dupontIntburden
        safe_float(row[10]), # dupontEbittogr
        update_date
    )


def get_target_quarter():
    beijing_time = get_beijing_time()
    month = beijing_time.month
    year = beijing_time.year
    
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


def print_summary(total_stocks, updated_count, skip_count, fail_count, total_rows, target_year, target_quarter):
    print("=" * 70)
    print("📊 杜邦指数数据同步汇总")
    print(f"  [总股票数]          : {total_stocks}")
    print(f"  [已最新跳过]        : {skip_count}")
    print(f"  [成功更新]          : {updated_count}")
    print(f"  [写入行数]          : {total_rows}")
    print(f"  [失败]              : {fail_count}")
    print(f"  [目标季度]          : {target_year} Q{target_quarter}")
    print("=" * 70, flush=True)


def main():
    beijing_time = get_beijing_time()
    today_str = beijing_time.strftime('%Y-%m-%d')
    
    print("=" * 70)
    print("  [UPDATE] 季频杜邦指数数据同步 (stock_dupont_quarterly)")
    print(f"  [时间] {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70, flush=True)
    
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

    if not ensure_bs_login():
        print("Baostock login failed. Exiting.", flush=True)
        if conn:
            conn.close()
        return
    
    try:
        # ====== Step 1: 检查 stock_basic 是否有数据 ======
        print("\n[1] Checking stock_basic...", flush=True)
        if not check_stock_basic_has_data(conn):
            return
        
        # ====== Step 2: 从 stock_basic 获取上市股票 ======
        print("\n[2] Getting active stocks from stock_basic...", flush=True)
        all_stocks = get_active_stocks_from_db(conn)
        if not all_stocks:
            print("  [ERROR] No stocks found in stock_basic", flush=True)
            return
        
        # ====== Step 3: 查询各股票已有的最新数据 ======
        print(f"\n[3] Querying existing dupont data from database for {len(all_stocks)} stocks...", flush=True)
        dupont_status = get_dupont_latest_dates(conn, all_stocks)
        with_data_count = len([v for v in dupont_status.values() if v is not None])
        print(f"  [INFO] {with_data_count} stocks already have data", flush=True)
        
        # ====== Step 4: 确定目标季度 ======
        target_year, target_quarter = get_target_quarter()
        print(f"\n[4] Target quarter: {target_year} Q{target_quarter}", flush=True)
        print("-" * 70, flush=True)
        
        # ====== Step 5: 遍历股票更新 ======
        db_buffer = []
        db_buffer_limit = 500
        total_rows = 0
        updated_count = 0
        skip_count = 0
        fail_count = 0
        
        total_stocks = len(all_stocks)
        processed = 0
        
        for code in all_stocks:
            processed += 1
            last_date = dupont_status.get(code)
            
            if last_date:
                last_dt = pd.to_datetime(last_date)
                if last_dt.year > target_year or (last_dt.year == target_year and ((last_dt.month - 1) // 3 + 1) >= target_quarter):
                    skip_count += 1
                    if processed % 100 == 0:
                        print(f"  [进度] {processed}/{total_stocks} (跳过: {skip_count})", flush=True)
                    continue
            
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
                start_year = 2020
                start_quarter = 1
            
            if start_year > target_year or (start_year == target_year and start_quarter > target_quarter):
               