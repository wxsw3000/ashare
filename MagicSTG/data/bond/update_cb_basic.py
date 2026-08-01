#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可转债基础信息同步脚本 (cb_basic)
从 AKShare (bond_zh_cov / bond_cb_jsl / bond_zh_cov_info) 拉取全量可转债列表及详细基础属性，写入/更新 TiDB 数据库
包含转股价自动兜底补全（涵盖已退市及历史转债）
"""

import sys
import os
import time
import pandas as pd

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


def format_code(code_str, market_prefix=True):
    """格式化代码逻辑，如 113008 -> 113008.SH 或 sh.113008"""
    code_str = str(code_str).strip()
    if not code_str:
        return ""
    if code_str.startswith('11') or code_str.startswith('13') or code_str.startswith('60') or code_str.startswith('68'):
        suffix = 'SH'
        prefix = 'sh'
    elif code_str.startswith('12') or code_str.startswith('00') or code_str.startswith('30'):
        suffix = 'SZ'
        prefix = 'sz'
    elif code_str.startswith('8') or code_str.startswith('4'):
        suffix = 'BJ'
        prefix = 'bj'
    else:
        suffix = 'SH'
        prefix = 'sh'
        
    num = ''.join(filter(str.isdigit, code_str))
    if market_prefix:
        return f"{prefix}.{num}"
    return f"{num}.{suffix}"


def parse_date(date_val):
    """把各种日期格式转换为 YYYY-MM-DD 字符串"""
    if pd.isna(date_val) or date_val is None or str(date_val).strip() in ('', 'NaT', 'None'):
        return None
    try:
        dt = pd.to_datetime(date_val)
        return dt.strftime('%Y-%m-%d')
    except Exception:
        return None


def fetch_and_update_cb_basic():
    print("=" * 60)
    print("🚀 开始更新可转债基础信息 (cb_basic)...")
    print("=" * 60)
    
    # 1. 拉取全量比价列表
    print("  [1/3] 正在从 AKShare 拉取可转债基本列表 (bond_zh_cov)...", flush=True)
    try:
        df_cov = ak.bond_zh_cov()
        print(f"  ✅ 成功获取 {len(df_cov)} 条可转债记录")
    except Exception as e:
        print(f"  ❌ 拉取 bond_zh_cov 失败: {e}")
        return

    # 2. 拉取集思录实时补充数据
    print("  [2/3] 正在从 AKShare 拉取集思录转债特征数据 (bond_cb_jsl)...", flush=True)
    jsl_dict = {}
    try:
        df_jsl = ak.bond_cb_jsl()
        for idx, row in df_jsl.iterrows():
            code = str(row['代码']).strip()
            jsl_dict[code] = {
                'curr_issue_size': float(row['剩余规模']) if '剩余规模' in row and pd.notna(row['剩余规模']) else None,
                'convert_price': float(row['转股价']) if '转股价' in row and pd.notna(row['转股价']) else None,
                'maturity_date': parse_date(row.get('到期时间'))
            }
        print(f"  ✅ 成功获取 {len(jsl_dict)} 条集思录辅助特征")
    except Exception as e:
        print(f"  ⚠️ 拉取集思录辅助数据失败（使用主表兜底）: {e}")

    # 3. 数据清洗与组装
    print("  [3/3] 数据清洗并写入/更新 TiDB 数据库...", flush=True)
    conn = get_connection()
    update_date = time.strftime('%Y-%m-%d')
    
    insert_sql = """
    INSERT INTO `cb_basic` (
        `code`, `name`, `stock_code`, `stock_name`, `convert_price`,
        `issue_size`, `curr_issue_size`, `list_date`, `delist_date`, `maturity_date`,
        `rating`, `update_date`
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        `name` = VALUES(`name`),
        `stock_code` = VALUES(`stock_code`),
        `stock_name` = VALUES(`stock_name`),
        `convert_price` = VALUES(`convert_price`),
        `issue_size` = VALUES(`issue_size`),
        `curr_issue_size` = VALUES(`curr_issue_size`),
        `list_date` = VALUES(`list_date`),
        `delist_date` = VALUES(`delist_date`),
        `maturity_date` = VALUES(`maturity_date`),
        `rating` = VALUES(`rating`),
        `update_date` = VALUES(`update_date`);
    """
    
    records = []
    missing_price_codes = []
    
    for idx, row in df_cov.iterrows():
        raw_code = str(row['债券代码']).strip()
        num_code = ''.join(filter(str.isdigit, raw_code))
        if not num_code or len(num_code) < 6:
            continue
            
        code = format_code(raw_code, market_prefix=True)
        name = str(row['债券简称']).strip() if pd.notna(row.get('债券简称')) else ''
        
        raw_stock_code = str(row['正股代码']).strip() if pd.notna(row.get('正股代码')) else ''
        stock_code = format_code(raw_stock_code, market_prefix=True) if raw_stock_code else None
        stock_name = str(row['正股简称']).strip() if pd.notna(row.get('正股简称')) else None
        
        convert_price = float(row['转股价']) if pd.notna(row.get('转股价')) else None
        issue_size = float(row['发行规模']) if pd.notna(row.get('发行规模')) else None
        list_date = parse_date(row.get('上市时间'))
        rating = str(row['信用评级']).strip() if pd.notna(row.get('信用评级')) else None
        
        # 尝试集思录补充数据
        curr_issue_size = None
        maturity_date = None
        if num_code in jsl_dict:
            jsl_item = jsl_dict[num_code]
            if jsl_item['curr_issue_size'] is not None:
                curr_issue_size = jsl_item['curr_issue_size']
            if jsl_item['convert_price'] is not None:
                convert_price = jsl_item['convert_price']
            if jsl_item['maturity_date'] is not None:
                maturity_date = jsl_item['maturity_date']
                
        if convert_price is None or convert_price <= 0:
            missing_price_codes.append((num_code, code))
            
        records.append([
            code, name, stock_code, stock_name, convert_price,
            issue_size, curr_issue_size, list_date, None, maturity_date,
            rating, update_date
        ])
        
    print(f"  📌 主表中仍有 {len(missing_price_codes)} 只转债缺失转股价，正在尝试从转债详情接口补全...", flush=True)
    
    # 批量兜底缺失转股价
    filled_count = 0
    for num_code, code in missing_price_codes:
        try:
            info_df = ak.bond_zh_cov_info(symbol=num_code)
            if info_df is not None and not info_df.empty:
                cp = None
                if 'TRANSFER_PRICE' in info_df.columns and pd.notna(info_df['TRANSFER_PRICE'].iloc[0]):
                    cp = float(info_df['TRANSFER_PRICE'].iloc[0])
                if (cp is None or cp <= 0) and 'INITIAL_TRANSFER_PRICE' in info_df.columns and pd.notna(info_df['INITIAL_TRANSFER_PRICE'].iloc[0]):
                    cp = float(info_df['INITIAL_TRANSFER_PRICE'].iloc[0])
                    
                if cp and cp > 0:
                    for rec in records:
                        if rec[0] == code:
                            rec[4] = cp
                            filled_count += 1
                            break
        except Exception:
            pass
            
    print(f"  ✅ 成功为 {filled_count} 只历史转债补全了转股价！")
    
    # 转换为 tuple 列表
    tuple_records = [tuple(r) for r in records]
        
    try:
        conn = ensure_connection_alive(conn)
        with conn.cursor() as cur:
            cur.executemany(insert_sql, tuple_records)
        conn.commit()
        print(f"🎉 成功更新 {len(tuple_records)} 条可转债基础信息至 cb_basic 表！")
    except Exception as e:
        conn.rollback()
        print(f"❌ 写入 cb_basic 失败: {e}")
        raise e
    finally:
        conn.close()


if __name__ == '__main__':
    fetch_and_update_cb_basic()
