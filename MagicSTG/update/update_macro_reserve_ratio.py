#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
macro_reserve_ratio 存款准备金率数据更新脚本
从 Baostock 拉取存款准备金率数据
"""

import os
import sys
import time
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


def get_existing_dates(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pub_date FROM macro_reserve_ratio")
            rows = cur.fetchall()
            return {row[0].strftime('%Y-%m-%d') for row in rows}
    except Exception as e:
        print(f"  [DB] Query existing dates failed: {e}", flush=True)
        return set()


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


def safe_float(val, default=None):
    if val is None or val == "" or pd.isna(val):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def flush_db_buffer(conn, batch_data):
    if not batch_data:
        return conn
    
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
                conn = get_connection()
            else:
                raise e
    return conn


def parse_reserve_row(row):
    """
    字段顺序: pubDate, effectiveDate, bigInstitutionsRatioPre, bigInstitutionsRatioAfter,
              mediumInstitutionsRatioPre, mediumInstitutionsRatioAfter
    """
    pub_date = row[0] if row[0] else None
    if not pub_date:
        return None
    
    return (
        pub_date,
        row[1] if row[1] else None,  # effective_date
        safe_float(row[2]),           # big_before
        safe_float(row[3]),           # big_after
        safe_float(row[4]),           # medium_before
        safe_float(row[5]),           # medium_after
    )


def print_summary(total_fetched, inserted_count, start_date, end_date):
    print("=" * 70)
    print("📊 存款准备金率数据同步汇总")
    print(f"  [拉取记录数]        : {total_fetched}")
    print(f"  [写入/更新记录数]   : {inserted_count}")
    print(f"  [查询范围]          : {start_date} ~ {end_date}")
    print("=" * 70, flush=True)


def main():
    beijing_time = datetime.utcnow() + timedelta(hours=8)
    today_str = beijing_time.strftime('%Y-%m-%d')
    current_hour = beijing_time.hour
    
    print("=" * 70)
    print("  [UPDATE] 存款准备金率数据同步 (macro_reserve_ratio)")
    print(f"  [时间] {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    if current_hour >= 18:
        end_date = today_str
    else:
        end_date = (beijing_time - timedelta(days=1)).strftime('%Y-%m-%d')
    
    start_date = "1990-01-01"
    print(f"  [查询范围] {start_date} ~ {end_date}")
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
        print("\n[1] Querying existing dates from database...", flush=True)
        existing_dates = get_existing_dates(conn)
        print(f"  [INFO] Found {len(existing_dates)} existing records", flush=True)
        
        print(f"\n[2] Fetching reserve ratio data from Baostock ({start_date} ~ {end_date})...", flush=True)
        data_list, ok = fetch_reserve_ratio(start_date, end_date)
        if not ok or data_list is None:
            print("  [ERROR] Failed to fetch data", flush=True)
            return
        
        print(f"  [INFO] Fetched {len(data_list)} records", flush=True)
        
        print("\n[3] Syncing to database...", flush=True)
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
        
        print_summary(len(data_list), inserted_count, start_date, end_date)
        
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"\nFatal error: {e}", flush=True)
        import traceback
        traceback.print_exc()
    finally:
        try:
            bs.logout()
        except Exception:
            pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()