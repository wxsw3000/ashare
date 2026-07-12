#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_forecast 季频业绩预告数据更新脚本
从 stock_basic 获取股票列表（type='1'），从 Baostock 拉取业绩预告数据
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
    else:
        print("[ENV] No .env file found, using system environment variables", flush=True)
except ImportError:
    print("[ENV] python-dotenv not installed, using system environment variables", flush=True)

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "")
DB_SSL_CA = os.getenv("DB_SSL_CA", "")

# 业绩预告查询字段
FORECAST_FIELDS = (
    "code,profitForcastExpPubDate,profitForcastExpStatDate,"
    "profitForcastType,profitForcastAbstract,"
    "profitForcastChgPctUp,profitForcastChgPctDwn"
)


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
    
    print("[Baostock] Session expired or not logged in, re-logging...", flush=True)
    try:
        bs.logout()
    except Exception:
        pass
    time.sleep(1)
    lg = bs.login()
    if lg.error_code != '0':
        print(f"[Baostock] Login failed: {lg.error_msg}", flush=True)
        return False
    print("[Baostock] Re-login successful", flush=True)
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


def get_forecast_latest_dates(conn, codes):
    """
    批量查询各股票在 forecast 表中的最新 stat_date
    返回: {'sh.600000': '2024-12-31', ...}
    """
    if not codes:
        return {}
    
    result = {}
    
    try:
        with conn.cursor() as cur:
            # 检查表是否存在
            cur.execute("SHOW TABLES LIKE 'stock_forecast'")
            if cur.fetchone() is None:
                print("  [INFO] stock_forecast table does not exist yet", flush=True)
                for code in codes:
                    result[code] = None
                return result
            
            # 检查表是否为空
            cur.execute("SELECT COUNT(*) FROM stock_forecast")
            count = cur.fetchone()[0]
            if count == 0:
                print("  [INFO] stock_forecast is empty, all stocks need full update", flush=True)
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
                FROM stock_forecast 
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
        print(f"  [ERROR] Failed to query forecast latest dates: {e}", flush=True)
        for code in codes:
            result[code] = None
    
    return result


def fetch_forecast_data(code, start_date, end_date, max_retries=3):
    """
    从 Baostock 查询业绩预告数据
    使用 start_date 和 end_date 按发布日期范围查询
    返回: (data_list, success)
    """
    for attempt in range(max_retries):
        try:
            if not ensure_bs_login():
                time.sleep(2)
                continue
            
            rs = bs.query_forecast_report(
                code=code,
                start_date=start_date,
                end_date=end_date
            )
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


def safe_str(val, default=None):
    if val is None or val == "" or pd.isna(val):
        return default
    return str(val)


def build_insert_sql(table_name, fields, batch_size):
    placeholders = ", ".join(["(" + ", ".join(["%s"] * len(fields)) + ")"] * batch_size)
    sql = f"""
    INSERT INTO `{table_name}` ({', '.join(['`' + f + '`' for f in fields])})
    VALUES {placeholders}
    ON DUPLICATE KEY UPDATE
        `pub_date` = VALUES(`pub_date`),
        `forecast_type` = VALUES(`forecast_type`),
        `abstract` = VALUES(`abstract`),
        `chg_pct_up` = VALUES(`chg_pct_up`),
        `chg_pct_dwn` = VALUES(`chg_pct_dwn`);
    """
    return sql


def flush_db_buffer(conn, batch_data):
    if not batch_data:
        return conn
    
    fields = [
        'code', 'stat_date', 'pub_date',
        'forecast_type', 'abstract', 'chg_pct_up', 'chg_pct_dwn'
    ]
    
    sql = build_insert_sql('stock_forecast', fields, len(batch_data))
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


def parse_forecast_row(row, update_date):
    """
    解析 Baostock 返回的业绩预告数据行
    字段顺序: code, profitForcastExpPubDate, profitForcastExpStatDate,
              profitForcastType, profitForcastAbstract,
              profitForcastChgPctUp, profitForcastChgPctDwn
    """
    code = row[0]
    pub_date = row[1] if row[1] else None
    stat_date = row[2] if row[2] else None
    forecast_type = safe_str(row[3])
    abstract = safe_str(row[4])
    
    if not stat_date:
        return None
    
    return (
        code,                              # code
        stat_date,                         # stat_date
        pub_date,                          # pub_date
        forecast_type,                     # forecast_type
        abstract,                          # abstract
        safe_float(row[5]),                # chg_pct_up
        safe_float(row[6]),                # chg_pct_dwn
    )


def get_date_range():
    """
    确定查询日期范围
    业绩预告按发布日期查询，从 2003-01-01 到目标日期
    """
    beijing_time = get_beijing_time()
    today_str = beijing_time.strftime('%Y-%m-%d')
    current_hour = beijing_time.hour
    
    if current_hour >= 18:
        end_date = today_str
    else:
        end_date = (beijing_time - timedelta(days=1)).strftime('%Y-%m-%d')
    
    # 业绩预告从 2003 年开始有数据
    start_date = "2003-01-01"
    
    return start_date, end_date


def print_summary(total_stocks, updated_count, skip_count, fail_count, total_rows, start_date, end_date):
    print("=" * 70)
    print("📊 业绩预告数据同步汇总")
    print(f"  [总股票数]          : {total_stocks}")
    print(f"  [已最新跳过]        : {skip_count}")
    print(f"  [成功更新]          : {updated_count}")
    print(f"  [写入行数]          : {total_rows}")
    print(f"  [失败]              : {fail_count}")
    print(f"  [查询范围]          : {start_date} ~ {end_date}")
    print("=" * 70, flush=True)


def main():
    beijing_time = get_beijing_time()
    today_str = beijing_time.strftime('%Y-%m-%d')
    
    print("=" * 70)
    print("  [UPDATE] 季频业绩预告数据同步 (stock_forecast)")
    print(f"  [时间] {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70, flush=True)
    
    # 获取日期范围
    start_date, end_date = get_date_range()
    print(f"  [查询范围] {start_date} ~ {end_date}")
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

    if not ensure_bs_login():
        print("Baostock login failed. Exiting.", flush=True)
        if conn:
            conn.close()
        return
    
    try:
        # Step 1: 检查 stock_basic 是否有数据
        print("\n[1] Checking stock_basic...", flush=True)
        if not check_stock_basic_has_data(conn):
            return
        
        # Step 2: 从 stock_basic 获取上市股票
        print("\n[2] Getting active stocks from stock_basic...", flush=True)
        all_stocks = get_active_stocks_from_db(conn)
        if not all_stocks:
            print("  [ERROR] No stocks found in stock_basic", flush=True)
            return
        
        # Step 3: 查询各股票已有的最新数据
        print(f"\n[3] Querying existing forecast data from database for {len(all_stocks)} stocks...", flush=True)
        forecast_status = get_forecast_latest_dates(conn, all_stocks)
        with_data_count = len([v for v in forecast_status.values() if v is not None])
        print(f"  [INFO] {with_data_count} stocks already have data", flush=True)
        
        print(f"\n[4] Updating stocks (查询范围: {start_date} ~ {end_date})...", flush=True)
        print("-" * 70, flush=True)
        
        # Step 4: 遍历股票更新
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
            last_date = forecast_status.get(code)
            
            # 判断是否需要更新
            if last_date:
                last_year = pd.to_datetime(last_date).year
                current_year = pd.to_datetime(end_date).year
                if last_year >= current_year - 1:
                    skip_count += 1
                    if processed % 100 == 0:
                        print(f"  [进度] {processed}/{total_stocks} (跳过: {skip_count})", flush=True)
                    continue
            
            if processed % 50 == 0:
                print(f"  [{processed}/{total_stocks}] {code} ...", end=" ", flush=True)
            
            data_list, ok = fetch_forecast_data(code, start_date, end_date)
            if not ok or data_list is None:
                print(f"[FAIL] 拉取失败", flush=True)
                fail_count += 1
                continue
            
            if len(data_list) == 0:
                if processed % 50 == 0:
                    print("[SKIP] 无数据", flush=True)
                continue
            
            valid_rows = 0
            for row in data_list:
                db_row = parse_forecast_row(row, today_str)
                if db_row:
                    db_buffer.append(db_row)
                    valid_rows += 1
            
            if len(db_buffer) >= db_buffer_limit:
                conn = flush_db_buffer(conn, db_buffer)
                conn.commit()
                db_buffer = []
            
            if valid_rows > 0:
                updated_count += 1
                total_rows += valid_rows
                if processed % 50 == 0:
                    print(f"[SUCCESS] {valid_rows} 行", flush=True)
            
            if processed % 10 == 0:
                time.sleep(random.uniform(0.3, 0.8))
        
        # 刷新剩余数据
        if db_buffer:
            print("\nFlushing remaining data...", flush=True)
            conn = flush_db_buffer(conn, db_buffer)
            conn.commit()
            db_buffer = []
        
        print_summary(total_stocks, updated_count, skip_count, fail_count, total_rows, start_date, end_date)
        
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