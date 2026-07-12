#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_kline_day A股日K线数据更新脚本（前复权 + 除权自动检测与标记）
从 stock_basic 获取股票列表（type='1'），从 Baostock 拉取前复权日K线数据
核心逻辑：
    1. 增量更新时，检测新数据 preclose 是否等于本地前一天 close
    2. 如果不相等 → 标记 is_dividend = 1，全量重建该股票
    3. 周/月K线通过查询 is_dividend 来判断是否需要全量重建
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

# 日K线查询字段（包含 preclose 用于除权检测）
KLINE_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,"
    "adjustflag,tradestatus,isST,turn,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM"
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
    返回: list of dict with code and ipo_date
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT code, ipo_date FROM stock_basic WHERE type = '1' AND status = '1'")
            rows = cur.fetchall()
            stocks = []
            for row in rows:
                code = row[0]
                ipo_date = row[1].strftime('%Y-%m-%d') if row[1] else '2000-01-01'
                stocks.append({'code': code, 'ipo_date': ipo_date})
            print(f"  [DB] Found {len(stocks)} active stocks from stock_basic", flush=True)
            return stocks
    except Exception as e:
        print(f"  [ERROR] Failed to get stocks from stock_basic: {e}", flush=True)
        return []


def get_kline_latest_date(conn, code):
    """
    查询某支股票在 stock_kline_day 表中的最新日期
    返回: 日期字符串 或 None
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(date) FROM stock_kline_day WHERE code = %s",
                (code,)
            )
            row = cur.fetchone()
            if row and row[0]:
                return row[0].strftime('%Y-%m-%d')
            return None
    except Exception as e:
        print(f"  [ERROR] Failed to query latest date for {code}: {e}", flush=True)
        return None


def get_previous_close(conn, code, date):
    """
    查询某支股票在指定日期前一天的 close 价格
    返回: float 或 None
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT close FROM stock_kline_day WHERE code = %s AND date < %s ORDER BY date DESC LIMIT 1",
                (code, date)
            )
            row = cur.fetchone()
            if row and row[0]:
                return float(row[0])
            return None
    except Exception as e:
        print(f"  [ERROR] Failed to query previous close for {code} on {date}: {e}", flush=True)
        return None


def has_dividend_in_range(conn, code, start_date, end_date):
    """
    查询某股票在指定日期范围内是否有除权记录 (is_dividend = 1)
    供周/月K线调用
    返回: True/False
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(1) FROM stock_kline_day WHERE code = %s AND date >= %s AND date <= %s AND is_dividend = 1",
                (code, start_date, end_date)
            )
            row = cur.fetchone()
            return row[0] > 0
    except Exception as e:
        print(f"  [WARN] Failed to check dividend for {code}: {e}", flush=True)
        return False


def delete_stock_kline_data(conn, code):
    """
    删除某支股票的全部日K线数据
    返回: 删除的行数
    """
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM stock_kline_day WHERE code = %s", (code,))
            deleted = cur.rowcount
            print(f"  [DELETE] 删除了 {deleted} 条历史数据", flush=True)
            return deleted
    except Exception as e:
        print(f"  [ERROR] Failed to delete data for {code}: {e}", flush=True)
        raise


def fetch_stock_kline(code, start_date, end_date, max_retries=3):
    """
    从 Baostock 查询日K线数据（前复权）
    返回: (data_list, success)
    """
    for attempt in range(max_retries):
        try:
            if not ensure_bs_login():
                time.sleep(2)
                continue
            
            rs = bs.query_history_k_data_plus(
                code,
                KLINE_FIELDS,
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="2"  # 前复权
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
        `open` = VALUES(`open`),
        `high` = VALUES(`high`),
        `low` = VALUES(`low`),
        `close` = VALUES(`close`),
        `preclose` = VALUES(`preclose`),
        `volume` = VALUES(`volume`),
        `amount` = VALUES(`amount`),
        `adjustflag` = VALUES(`adjustflag`),
        `tradestatus` = VALUES(`tradestatus`),
        `isST` = VALUES(`isST`),
        `turn` = VALUES(`turn`),
        `pctChg` = VALUES(`pctChg`),
        `peTTM` = VALUES(`peTTM`),
        `pbMRQ` = VALUES(`pbMRQ`),
        `psTTM` = VALUES(`psTTM`),
        `pcfNcfTTM` = VALUES(`pcfNcfTTM`),
        `is_dividend` = VALUES(`is_dividend`),
        `update_date` = VALUES(`update_date`);
    """
    return sql


def flush_db_buffer(conn, batch_data, table_name="stock_kline_day"):
    if not batch_data:
        return conn
    
    fields = [
        'date', 'code', 'open', 'high', 'low', 'close', 'preclose',
        'volume', 'amount', 'adjustflag', 'tradestatus', 'isST',
        'turn', 'pctChg', 'peTTM', 'pbMRQ', 'psTTM', 'pcfNcfTTM',
        'is_dividend', 'update_date'
    ]
    
    sql = build_insert_sql(table_name, fields, len(batch_data))
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


def parse_kline_row(row, code, update_date, is_dividend):
    """
    解析 Baostock 返回的日K线数据行
    字段顺序: date,code,open,high,low,close,preclose,volume,amount,
              adjustflag,tradestatus,isST,turn,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM
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
        date_val, code, open_val, high_val, low_val, close_val, preclose_val,
        safe_int(row[7], 0), safe_float(row[8], 0.0),
        safe_str(row[9], '3'), safe_str(row[10], '1'), safe_str(row[11], '0'),
        safe_float(row[12]), safe_float(row[13]),
        safe_float(row[14]), safe_float(row[15]), safe_float(row[16]), safe_float(row[17]),
        is_dividend,  # 新增：除权标记
        update_date
    )


def update_stock_data(conn, code, ipo_date, target_date, update_date, db_buffer, db_buffer_limit):
    """
    更新单支股票的日K线数据
    返回: (total_rows, updated, skipped, failed, db_buffer, dividend_detected)
    """
    total_rows = 0
    updated = 0
    skipped = 0
    failed = 0
    dividend_detected = False
    
    # 查询本地最新日期
    last_date = get_kline_latest_date(conn, code)
    
    # 如果本地有数据且已是最新，跳过
    if last_date and last_date >= target_date:
        return 0, 0, 1, 0, db_buffer, False
    
    # 确定拉取起始日期
    if last_date:
        start_date = (pd.to_datetime(last_date) + timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        start_date = ipo_date
    
    print(f"  {code} ({start_date} -> {target_date}) ...", end=" ", flush=True)
    
    # 拉取数据
    data_list, ok = fetch_stock_kline(code, start_date, target_date)
    if not ok or data_list is None:
        print("[FAIL] 拉取失败", flush=True)
        return 0, 0, 0, 1, db_buffer, False
    
    if len(data_list) == 0:
        print("[SKIP] 无新数据", flush=True)
        return 0, 0, 1, 0, db_buffer, False
    
    # ========== 核心：检测除权除息 ==========
    # 如果本地有历史数据，检查增量数据第一天的 preclose
    # 如果 preclose != 本地前一天的 close，说明发生了除权
    dividend_date = None
    if last_date:
        first_row = data_list[0]
        first_date = first_row[0]
        first_preclose = safe_float(first_row[6])
        
        prev_close = get_previous_close(conn, code, first_date)
        if prev_close is not None and first_preclose is not None:
            # 使用阈值 0.001 避免浮点精度误差
            if abs(first_preclose - prev_close) > 0.001:
                dividend_detected = True
                dividend_date = first_date
                print(f"[DIVIDEND] 检测到除权! {first_date} preclose={first_preclose:.4f}, prev_close={prev_close:.4f}", flush=True)
    
    # 如果检测到除权除息，全量重建
    if dividend_detected:
        print(f"  全量重建 {code} ...", end=" ", flush=True)
        # 删除全部历史数据
        delete_stock_kline_data(conn, code)
        conn.commit()
        
        # 从上市日期开始全量拉取
        data_list, ok = fetch_stock_kline(code, ipo_date, target_date)
        if not ok or data_list is None:
            print("[FAIL] 全量重建失败", flush=True)
            return 0, 0, 0, 1, db_buffer, False
        
        if len(data_list) == 0:
            print("[SKIP] 全量重建无数据", flush=True)
            return 0, 0, 1, 0, db_buffer, False
        
        # 全量重建时，只有 dividend_date 这一天标记为 is_dividend = 1
        # 其他所有日期标记为 0
        is_dividend = 0
    else:
        # 正常增量更新，所有新增数据 is_dividend = 0
        is_dividend = 0
    
    # 解析数据行，标记除权日
    valid_rows = 0
    for row in data_list:
        row_date = row[0]
        # 如果是全量重建，只有除权日标记为 1
        if dividend_detected and row_date == dividend_date:
            db_row = parse_kline_row(row, code, update_date, 1)
        else:
            db_row = parse_kline_row(row, code, update_date, 0)
        
        if db_row:
            db_buffer.append(db_row)
            valid_rows += 1
        
        if len(db_buffer) >= db_buffer_limit:
            conn = flush_db_buffer(conn, db_buffer)
            conn.commit()
            db_buffer = []
    
    if dividend_detected:
        print(f"[REBUILD] {valid_rows} 行 (除权日: {dividend_date})", flush=True)
    else:
        print(f"[SUCCESS] {valid_rows} 行", flush=True)
    
    return valid_rows, 1, 0, 0, db_buffer, dividend_detected


def print_summary(total_stocks, updated_count, skip_count, fail_count, 
                  total_rows, dividend_count, target_date):
    print("=" * 70)
    print("📊 日K线数据同步汇总（前复权 + 除权检测与标记）")
    print(f"  [总股票数]          : {total_stocks}")
    print(f"  [已最新跳过]        : {skip_count}")
    print(f"  [成功更新]          : {updated_count}")
    print(f"  [写入行数]          : {total_rows}")
    print(f"  [检测到除权]        : {dividend_count}")
    print(f"  [失败]              : {fail_count}")
    print(f"  [目标日期]          : {target_date}")
    print("=" * 70, flush=True)


def main():
    beijing_time = get_beijing_time()
    today_str = beijing_time.strftime('%Y-%m-%d')
    current_hour = beijing_time.hour
    current_minute = beijing_time.minute
    
    print("=" * 70)
    print("  [UPDATE] A-share 日K线数据 (前复权 + 除权检测与标记)")
    print(f"  [北京时间] {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    if current_hour >= 18:
        target_date = today_str
        print(f"  ⏰ 当前时间 {current_hour:02d}:{current_minute:02d} >= 18:00，拉取截止到 {target_date}")
    else:
        target_date = (beijing_time - timedelta(days=1)).strftime('%Y-%m-%d')
        print(f"  ⏰ 当前时间 {current_hour:02d}:{current_minute:02d} < 18:00，拉取截止到 {target_date}")
    
    update_date = today_str
    print("=" * 70, flush=True)
    
    # 数据库连接
    conn = None
    for attempt in range(1, 6):
        try:
            conn = get_connection()
            print("[DB] Database connection established!", flush=True)
            break
        except Exception as e:
            print(f"Failed to connect (attempt {attempt}/5): {e}", flush=True)
            if attempt < 5:
                time.sleep(3)
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
        
        # Step 2: 从 stock_basic 获取上市股票列表
        print("\n[2] Getting active stocks from stock_basic...", flush=True)
        all_stocks = get_active_stocks_from_db(conn)
        if not all_stocks:
            print("  [ERROR] No stocks found in stock_basic", flush=True)
            return
        
        # Step 3: 遍历更新
        print(f"\n[3] Updating {len(all_stocks)} stocks (target: {target_date})...", flush=True)
        print("-" * 70, flush=True)
        
        db_buffer = []
        db_buffer_limit = 500
        total_rows = 0
        updated_count = 0
        skip_count = 0
        fail_count = 0
        dividend_count = 0
        
        total_stocks = len(all_stocks)
        processed = 0
        
        for stock in all_stocks:
            processed += 1
            code = stock['code']
            ipo_date = stock['ipo_date']
            
            rows, updated, skipped, failed, db_buffer, dividend_detected = update_stock_data(
                conn, code, ipo_date, target_date, update_date,
                db_buffer, db_buffer_limit
            )
            
            total_rows += rows
            updated_count += updated
            skip_count += skipped
            fail_count += failed
            if dividend_detected:
                dividend_count += 1
            
            if processed % 50 == 0:
                print(f"  [进度] {processed}/{total_stocks} (更新: {updated_count}, 跳过: {skip_count}, 除权: {dividend_count}, 失败: {fail_count})", flush=True)
            
            if processed % 5 == 0:
                time.sleep(random.uniform(0.3, 0.8))
        
        # 刷新剩余数据
        if db_buffer:
            print("\nFlushing remaining data...", flush=True)
            conn = flush_db_buffer(conn, db_buffer)
            conn.commit()
            db_buffer = []
        
        print_summary(total_stocks, updated_count, skip_count, fail_count, 
                     total_rows, dividend_count, target_date)
        
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