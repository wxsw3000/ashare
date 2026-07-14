#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_kline_day A股日K线数据更新脚本（前复权 + 除权自动检测与标记）
从 stock_basic 获取股票列表（type='1'），从 Baostock 拉取前复权日K线数据
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

KLINE_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,"
    "adjustflag,tradestatus,isST,turn,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM"
)
START_DATE = "2020-01-01"


# ============================================================
# 数据库操作
# ============================================================

def get_active_stocks_from_db(conn):
    """从 stock_basic 获取上市股票列表（type='1' 且 status='1'）"""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT code, ipo_date FROM stock_basic WHERE type = '1' AND status = '1'")
            rows = cur.fetchall()
            stocks = []
            for row in rows:
                code = row[0]
                ipo_date = row[1].strftime('%Y-%m-%d') if row[1] else '2020-01-01'
                if ipo_date < '2020-01-01':
                    ipo_date = '2020-01-01'
                stocks.append({'code': code, 'ipo_date': ipo_date})
            print(f"  [DB] Found {len(stocks)} active stocks from stock_basic", flush=True)
            return stocks
    except Exception as e:
        print(f"  [ERROR] Failed to get stocks from stock_basic: {e}", flush=True)
        return []


def get_all_kline_latest_info(conn):
    """批量查询所有股票在 stock_kline_day 表中的最新日期和对应的收盘价"""
    try:
        with conn.cursor() as cur:
            sql = """
            SELECT code, date, close 
            FROM stock_kline_day 
            WHERE (code, date) IN (
                SELECT code, MAX(date) 
                FROM stock_kline_day 
                GROUP BY code
            )
            """
            cur.execute(sql)
            rows = cur.fetchall()
            latest_info = {}
            for row in rows:
                code = row[0]
                latest_date = row[1].strftime('%Y-%m-%d') if row[1] else None
                close_price = float(row[2]) if row[2] is not None else None
                latest_info[code] = (latest_date, close_price)
            print(f"  [DB] Loaded latest K-line dates/closes for {len(latest_info)} stocks", flush=True)
            return latest_info
    except Exception as e:
        print(f"  [ERROR] Failed to get latest K-line info: {e}", flush=True)
        return {}


def delete_stock_kline_data(conn, code):
    """删除某支股票的全部日K线数据"""
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM stock_kline_day WHERE code = %s", (code,))
            deleted = cur.rowcount
            print(f"  [DELETE] 删除了 {deleted} 条历史数据", flush=True)
            return deleted
    except Exception as e:
        print(f"  [ERROR] Failed to delete data for {code}: {e}", flush=True)
        raise


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
    """批量插入日K线数据"""
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
                conn = get_connection_with_retry()
            else:
                raise e
    return conn


# ============================================================
# Baostock 数据拉取
# ============================================================

def fetch_stock_kline(code, start_date, end_date, max_retries=3):
    """从 Baostock 查询日K线数据（前复权）"""
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
                adjustflag="2"
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


def parse_kline_row(row, code, update_date, is_dividend):
    """解析日K线数据行"""
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
        is_dividend,
        update_date
    )


# ============================================================
# 核心更新逻辑
# ============================================================

def update_stock_data(conn, code, ipo_date, target_date, update_date, db_buffer, db_buffer_limit, latest_info):
    """
    更新单支股票的日K线数据
    返回: (total_rows, updated, skipped, failed, db_buffer, dividend_detected)
    """
    total_rows = 0
    dividend_detected = False
    
    last_date, prev_close = latest_info.get(code, (None, None))
    
    if last_date and last_date >= target_date:
        return 0, 0, 1, 0, db_buffer, False
    
    if last_date:
        start_date = (pd.to_datetime(last_date) + timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        start_date = ipo_date
    
    data_list, ok = fetch_stock_kline(code, start_date, target_date)
    if not ok or data_list is None:
        return 0, 0, 0, 1, db_buffer, False
    
    if len(data_list) == 0:
        return 0, 0, 1, 0, db_buffer, False
    
    # 检测除权事件：判断第一天的 preclose 是否与本地最大日期的 close 不一致
    if last_date:
        first_row = data_list[0]
        first_preclose = safe_float(first_row[6])
        if prev_close is not None and first_preclose is not None:
            if abs(first_preclose - prev_close) > 0.001:
                dividend_detected = True
    
    if dividend_detected:
        delete_stock_kline_data(conn, code)
        conn.commit()
        
        # 重新获取 IPO 至今的全部行情
        data_list, ok = fetch_stock_kline(code, ipo_date, target_date)
        if not ok or data_list is None:
            return 0, 0, 0, 1, db_buffer, False
        
        if len(data_list) == 0:
            return 0, 0, 1, 0, db_buffer, False
            
    # 查找所有的除权日（相邻行的 preclose 与上一行 close 不一致即为除权日）
    dividend_dates = set()
    for i in range(1, len(data_list)):
        prev_close_price = safe_float(data_list[i-1][5])
        curr_preclose_price = safe_float(data_list[i][6])
        if prev_close_price is not None and curr_preclose_price is not None:
            if abs(curr_preclose_price - prev_close_price) > 0.001:
                dividend_dates.add(data_list[i][0])
                
    # 若在增量第一天发生了除权，且第一天本身也是除权日
    if dividend_detected and last_date:
        first_row = data_list[0]
        first_preclose = safe_float(first_row[6])
        if prev_close is not None and first_preclose is not None and abs(first_preclose - prev_close) > 0.001:
            dividend_dates.add(first_row[0])
    
    for row in data_list:
        row_date = row[0]
        is_div = 1 if row_date in dividend_dates else 0
        db_row = parse_kline_row(row, code, update_date, is_div)
        
        if db_row:
            db_buffer.append(db_row)
            total_rows += 1
        
        if len(db_buffer) >= db_buffer_limit:
            conn = flush_db_buffer(conn, db_buffer)
            conn.commit()
            db_buffer = []
    
    return total_rows, 1, 0, 0, db_buffer, dividend_detected


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
    print(f"  [数据起始]          : {START_DATE}")
    print("=" * 70, flush=True)


def main():
    import sys
    beijing_time = get_beijing_time()
    target_date = get_target_date()
    update_date = beijing_time.strftime('%Y-%m-%d')
    
    # 优雅超时的最大执行时长设定，这里支持通过环境变量控制，默认设定为 19000 秒 (约 5.28 小时)
    MAX_RUNTIME = float(os.environ.get('MAX_RUNTIME', 19000))
    
    print("=" * 70)
    print("  [UPDATE] A-share 日K线数据 (前复权 + 除权检测与标记)")
    print(f"  [时间] {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  [数据范围] {START_DATE} 至今")
    print(f"  [目标日期] {target_date}")
    print(f"  [最大时长] {MAX_RUNTIME}秒")
    print("=" * 70, flush=True)
    
    conn = get_connection_with_retry()
    print("[DB] Database connection established!", flush=True)
    
    try:
        print("\n[1] Getting active stocks from stock_basic...")
        all_stocks = get_active_stocks_from_db(conn)
        if not all_stocks:
            print("  [ERROR] No stocks found in stock_basic")
            return
        
        print("\n[2] Loading all latest K-line date & close info in bulk...")
        latest_info = get_all_kline_latest_info(conn)
        
        print(f"\n[3] Updating {len(all_stocks)} stocks (target: {target_date})...")
        print("-" * 70, flush=True)
        
        db_buffer = []
        # 按用户建议，将写入频率调整为 150 条以优化 IO 并在意外超时发生时保留更多进度
        db_buffer_limit = 150
        total_rows = 0
        updated_count = 0
        skip_count = 0
        fail_count = 0
        dividend_count = 0
        
        total_stocks = len(all_stocks)
        start_time = time.time()
        early_exit = False
        
        for idx, stock in enumerate(all_stocks, 1):
            # 优雅超时判断
            elapsed = time.time() - start_time
            if elapsed > MAX_RUNTIME:
                print(f"\n  [WARN] Reached maximum runtime limit ({elapsed:.1f}s > {MAX_RUNTIME}s), exiting gracefully...", flush=True)
                early_exit = True
                break
                
            code = stock['code']
            ipo_date = stock['ipo_date']
            
            rows, updated, skipped, failed, db_buffer, dividend_detected = update_stock_data(
                conn, code, ipo_date, target_date, update_date,
                db_buffer, db_buffer_limit, latest_info
            )
            
            total_rows += rows
            updated_count += updated
            skip_count += skipped
            fail_count += failed
            if dividend_detected:
                dividend_count += 1
            
            # 每 10 只输出一次包含 PROGRESS 格式的进度
            if idx % 10 == 0 or idx == 1 or idx == total_stocks:
                elapsed = time.time() - start_time
                avg_time = elapsed / idx if idx > 0 else 0
                remaining = avg_time * (total_stocks - idx)
                pct = (idx / total_stocks) * 100
                print(f"  PROGRESS: {idx}/{total_stocks} ({pct:.1f}%) "
                      f"已用: {format_time(elapsed)} 剩余: {format_time(remaining)} | "
                      f"更新: {updated_count} 跳过: {skip_count} 失败: {fail_count}", flush=True)
            
            if idx % 5 == 0:
                random_sleep(0.3, 0.8)
        
        if db_buffer:
            print("\nFlushing remaining data...")
            conn = flush_db_buffer(conn, db_buffer)
            conn.commit()
            db_buffer = []
        
        print_summary(total_stocks, updated_count, skip_count, fail_count, 
                     total_rows, dividend_count, target_date)
                     
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