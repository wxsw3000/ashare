#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可转债日线 K 线同步脚本 (cb_kline_day)
从数据库 cb_basic 读取全部转债列表，调用 AKShare (bond_zh_hs_cov_daily) 拉取 2020 年至今的日线 K 线
支持全量初始化与每日增量更新
"""

import sys
import os
import time
import random
import pandas as pd
from datetime import datetime, timedelta

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
curr = SCRIPT_DIR
while curr and os.path.dirname(curr) != curr:
    if os.path.exists(os.path.join(curr, 'MagicSTG')):
        if curr not in sys.path:
            sys.path.insert(0, curr)
        break
    curr = os.path.dirname(curr)

import akshare as ak
from MagicSTG.core.db import get_connection, ensure_connection_alive

START_DATE = "2020-01-01"


def get_symbol_from_code(code_str):
    """把 sh.113008 或 113008.SH 转化为 akshare 需要的 sh113008"""
    code_str = str(code_str).strip()
    if not code_str:
        return ""
    parts = code_str.split('.')
    if len(parts) == 2:
        if parts[0].isalpha():  # sh.113008
            return f"{parts[0].lower()}{parts[1]}"
        else:  # 113008.SH
            return f"{parts[1].lower()}{parts[0]}"
    num = ''.join(filter(str.isdigit, code_str))
    if num.startswith('12'):
        return f"sz{num}"
    return f"sh{num}"


def get_all_cb_stocks_from_db(conn):
    """从 cb_basic 获取转债列表"""
    try:
        conn.ping(reconnect=True)
        with conn.cursor() as cur:
            cur.execute("SELECT code, list_date FROM cb_basic ORDER BY code ASC")
            rows = cur.fetchall()
            stocks = []
            for row in rows:
                code = row[0]
                list_date = row[1].strftime('%Y-%m-%d') if row[1] else START_DATE
                if list_date < START_DATE:
                    list_date = START_DATE
                stocks.append({'code': code, 'list_date': list_date})
            return stocks
    except Exception as e:
        print(f"❌ 从 cb_basic 获取转债列表失败: {e}")
        return []


def get_latest_dates_from_db(conn):
    """获取 cb_kline_day 表中每只转债的最新日期"""
    try:
        conn.ping(reconnect=True)
        with conn.cursor() as cur:
            cur.execute("SELECT code, MAX(date) FROM cb_kline_day GROUP BY code")
            rows = cur.fetchall()
            latest_map = {}
            for row in rows:
                if row[0] and row[1]:
                    latest_map[row[0]] = row[1].strftime('%Y-%m-%d')
            return latest_map
    except Exception as e:
        print(f"⚠️ 获取最新 K 线日期失败: {e}")
        return {}


def flush_db_buffer(conn, batch_data):
    """批量写入 cb_kline_day 表"""
    if not batch_data:
        return conn
        
    sql = """
    INSERT INTO `cb_kline_day` (
        `code`, `date`, `open`, `high`, `low`, `close`, `volume`, `amount`, `pctChg`, `turn`, `update_date`
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        `open` = VALUES(`open`),
        `high` = VALUES(`high`),
        `low` = VALUES(`low`),
        `close` = VALUES(`close`),
        `volume` = VALUES(`volume`),
        `amount` = VALUES(`amount`),
        `pctChg` = VALUES(`pctChg`),
        `turn` = VALUES(`turn`),
        `update_date` = VALUES(`update_date`);
    """
    
    for attempt in range(1, 4):
        try:
            conn.ping(reconnect=True)
            with conn.cursor() as cur:
                cur.executemany(sql, batch_data)
            conn.commit()
            return conn
        except Exception as e:
            print(f"  [DB ERROR] 批量插入失败 ({attempt}/3): {e}")
            time.sleep(2)
    return conn


def update_cb_kline_day():
    print("=" * 60)
    print("🚀 开始同步可转债日线 K 线数据 (cb_kline_day)...")
    print(f"  [起始日期]: {START_DATE}")
    print("=" * 60)
    
    conn = get_connection()
    try:
        stocks = get_all_cb_stocks_from_db(conn)
        if not stocks:
            print("  ❌ 未查找到任何转债信息，请先运行 update_cb_basic.py")
            return
            
        latest_map = get_latest_dates_from_db(conn)
        print(f"  ✅ 发现 {len(stocks)} 只可转债，已知已入库 {len(latest_map)} 只的最新日期")
        
        update_date = time.strftime('%Y-%m-%d')
        total_rows = 0
        success_count = 0
        skip_count = 0
        fail_count = 0
        
        batch_buffer = []
        batch_limit = 2000
        
        for idx, stock in enumerate(stocks, 1):
            code = stock['code']
            symbol = get_symbol_from_code(code)
            
            last_date = latest_map.get(code)
            
            # 判断抓取区间
            if last_date:
                # 增量
                start_fetch = (pd.to_datetime(last_date) + timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                start_fetch = stock['list_date']
                
            if start_fetch > update_date:
                skip_count += 1
                continue
                
            if code.startswith('bj.'):
                skip_count += 1
                continue

            try:
                df = ak.bond_zh_hs_cov_daily(symbol=symbol)
                if df is None or df.empty:
                    skip_count += 1
                    continue
                    
                df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
                df = df[df['date'] >= start_fetch]
                
                if df.empty:
                    skip_count += 1
                    continue
                    
                df = df.sort_values('date')
                
                # 计算涨跌幅
                df['pctChg'] = df['close'].pct_change() * 100
                df['pctChg'] = df['pctChg'].fillna(0.0)
                
                for _, row in df.iterrows():
                    open_val = float(row['open']) if pd.notna(row.get('open')) else None
                    high_val = float(row['high']) if pd.notna(row.get('high')) else None
                    low_val = float(row['low']) if pd.notna(row.get('low')) else None
                    close_val = float(row['close']) if pd.notna(row.get('close')) else None
                    vol_val = int(row['volume']) if pd.notna(row.get('volume')) else 0
                    pct_val = float(row['pctChg']) if pd.notna(row.get('pctChg')) else 0.0
                    
                    batch_buffer.append((
                        code, row['date'], open_val, high_val, low_val, close_val,
                        vol_val, None, pct_val, None, update_date
                    ))
                    total_rows += 1
                    
                success_count += 1
                
                if len(batch_buffer) >= batch_limit:
                    conn = flush_db_buffer(conn, batch_buffer)
                    batch_buffer = []
                    
            except KeyError:
                skip_count += 1
            except Exception as e:
                fail_count += 1
                print(f"  ⚠️ 处理 {code} ({symbol}) 异常: {e}")
                
            if idx % 50 == 0 or idx == len(stocks):
                print(f"  PROGRESS: {idx}/{len(stocks)} ({(idx/len(stocks))*100:.1f}%) | 成功: {success_count} 跳过: {skip_count} 失败: {fail_count} | 写入行数: {total_rows}", flush=True)
                
            if idx % 10 == 0:
                time.sleep(random.uniform(0.1, 0.3))
                
        if batch_buffer:
            conn = flush_db_buffer(conn, batch_buffer)
            batch_buffer = []
            
        print("\n" + "=" * 60)
        print(f"🎉 可转债日 K 线同步完毕！总结: 成功={success_count}, 跳过={skip_count}, 失败={fail_count}, 总行数={total_rows}")
        print("=" * 60)
        
    finally:
        conn.close()


if __name__ == '__main__':
    update_cb_kline_day()
