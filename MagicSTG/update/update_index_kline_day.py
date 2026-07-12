#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
index_kline_day 指数日线数据更新脚本
从 stock_basic 获取所有指数（type='2'），然后从 Baostock 拉取日K线数据
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

# 指数查询字段
INDEX_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,pctChg"


def get_beijing_time():
    return datetime.utcnow() + timedelta(hours=8)


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


def get_all_indices_from_db(conn):
    """
    从 stock_basic 获取所有指数（type='2' 且 status='1' 上市状态）
    返回: list of code
    """
    try:
        with conn.cursor() as cur:
            # 从 stock_basic 查询所有指数，只取上市状态的
            cur.execute("SELECT code FROM stock_basic WHERE type = '2' AND status = '1'")
            rows = cur.fetchall()
            indices = [row[0] for row in rows]
            print(f"  [DB] Found {len(indices)} indices from stock_basic", flush=True)
            return indices
    except Exception as e:
        print(f"  [ERROR] Failed to get indices from stock_basic: {e}", flush=True)
        return []


def get_index_latest_dates(conn, codes):
    """
    查询各指数在 index_kline_day 表中的最新日期
    返回: {'sh.000001': '2026-07-10', ...}
    """
    if not codes:
        return {}
    
    result = {}
    try:
        # 检查表是否存在
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'index_kline_day'")
            if cur.fetchone() is None:
                print("  [INFO] index_kline_day table does not exist yet", flush=True)
                for code in codes:
                    result[code] = None
                return result
            
            # 检查表是否为空
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
    
    # 正常查询各指数最新日期
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
            
            # 没有数据的指数设为 None
            for code in codes:
                if code not in result:
                    result[code] = None
    except Exception as e:
        print(f"  [ERROR] Failed to query index latest dates: {e}", flush=True)
        for code in codes:
            result[code] = None
    
    return result


def fetch_index_kline(code, start_date, end_date, max_retries=3):
    """
    从 Baostock 查询指数日K线数据
    返回: (data_list, success)
    """
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


def safe_int(val, default=0):
    if val is None or val == "" or pd.isna(val):
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


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
                conn = get_connection()
            else:
                raise e
    return conn


def parse_index_row(row, code, update_date):
    """
    解析 Baostock 返回的指数数据行
    字段顺序: date, code, open, high, low, close, preclose, volume, amount, pctChg
    """
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


def print_summary(total_indices, updated_count, skip_count, fail_count, total_rows, target_date):
    """打印汇总信息"""
    print("=" * 70)
    print("📊 指数数据同步汇总")
    print(f"  [总指数数]          : {total_indices}")
    print(f"  [已最新跳过]        : {skip_count}")
    print(f"  [成功更新]          : {updated_count}")
    print(f"  [写入行数]          : {total_rows}")
    print(f"  [失败]              : {fail_count}")
    print(f"  [目标日期]          : {target_date}")
    print("=" * 70, flush=True)


def main():
    beijing_time = get_beijing_time()
    today_str = beijing_time.strftime('%Y-%m-%d')
    current_hour = beijing_time.hour
    
    print("=" * 70)
    print("  [UPDATE] 指数日线数据增量同步 (index_kline_day)")
    print(f"  [北京时间] {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    if current_hour >= 18:
        target_date = today_str
        print(f"  ⏰ 当前时间 {current_hour:02d}:00 >= 18:00，拉取截止到 {target_date}")
    else:
        target_date = (beijing_time - timedelta(days=1)).strftime('%Y-%m-%d')
        print(f"  ⏰ 当前时间 {current_hour:02d}:00 < 18:00，拉取截止到 {target_date}")
    
    update_date = today_str
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
        # Step 1: 从 stock_basic 获取所有指数
        print("\n[1] Getting all indices from stock_basic...", flush=True)
        all_indices = get_all_indices_from_db(conn)
        if not all_indices:
            print("  [ERROR] No indices found in stock_basic", flush=True)
            print("  [HINT] Please run update_stock_basic.py first to populate stock_basic", flush=True)
            return
        print(f"  [INFO] Total indices: {len(all_indices)}", flush=True)
        
        # Step 2: 查询各指数在数据库中的最新日期
        print("\n[2] Querying existing index data from database...", flush=True)
        index_status = get_index_latest_dates(conn, all_indices)
        with_data_count = len([v for v in index_status.values() if v is not None])
        print(f"  [INFO] {with_data_count} indices already have data", flush=True)
        
        # Step 3: 更新
        print(f"\n[3] Updating indices (target: {target_date})...", flush=True)
        print("-" * 70, flush=True)
        
        db_buffer = []
        db_buffer_limit = 500
        total_rows = 0
        updated_count = 0
        skip_count = 0
        fail_count = 0
        
        total_indices = len(all_indices)
        processed = 0
        
        for code in all_indices:
            processed += 1
            last_date = index_status.get(code)
            
            # 判断是否需要更新
            if last_date and last_date >= target_date:
                skip_count += 1
                if processed % 50 == 0:
                    print(f"  [进度] {processed}/{total_indices} (跳过: {skip_count})", flush=True)
                continue
            
            # 确定起始日期
            if last_date:
                start_date = (pd.to_datetime(last_date) + timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                start_date = "2000-01-01"
            
            if processed % 10 == 0 or processed == 1:
                print(f"  [{processed}/{total_indices}] {code} ({start_date} -> {target_date}) ...", end=" ", flush=True)
            
            data_list, ok = fetch_index_kline(code, start_date, target_date)
            if not ok or data_list is None:
                print(f"[FAIL] {code}", flush=True)
                fail_count += 1
                continue
            
            if len(data_list) == 0:
                if processed % 10 == 0:
                    print("[SKIP] 无数据", flush=True)
                continue
            
            valid_rows = 0
            for row in data_list:
                db_row = parse_index_row(row, code, update_date)
                if db_row:
                    db_buffer.append(db_row)
                    valid_rows += 1
            
            if len(db_buffer) >= db_buffer_limit:
                conn = flush_db_buffer(conn, db_buffer)
                conn.commit()
                db_buffer = []
            
            updated_count += 1
            total_rows += valid_rows
            if processed % 10 == 0:
                print(f"[SUCCESS] {valid_rows} 行", flush=True)
            
            # 控制请求频率
            if processed % 5 == 0:
                time.sleep(random.uniform(0.2, 0.5))
        
        # 刷新剩余数据
        if db_buffer:
            print("\nFlushing remaining data...", flush=True)
            conn = flush_db_buffer(conn, db_buffer)
            conn.commit()
            db_buffer = []
        
        # 打印汇总
        print_summary(total_indices, updated_count, skip_count, fail_count, total_rows, target_date)
        
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