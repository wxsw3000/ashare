#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
资金流向与市场情绪增量更新脚本 (update_money_flow.py)
拉取北向资金净流入 (stock_hsgt_fund_flow_summary_em) 与 沪深两融余额数据 (stock_margin_sse/szse)，
写入 TiDB Cloud 数据库中 `market_money_flow` 系统表。
"""

import sys
import os
import time
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

def safe_float(val, default=None):
    if pd.isna(val) or val is None or str(val).strip() in ('', 'None', 'NaN'):
        return default
    try:
        return float(val)
    except Exception:
        return default


def init_money_flow_table(conn):
    """初始化资金流向与两融情绪表`market_money_flow`"""
    sql = """
    CREATE TABLE IF NOT EXISTS `market_money_flow` (
        `date` DATE NOT NULL COMMENT '交易日期',
        `north_net_inflow` DOUBLE DEFAULT NULL COMMENT '北向资金净流入(万元)',
        `margin_balance` DOUBLE DEFAULT NULL COMMENT '两融余额(元)',
        `margin_buy` DOUBLE DEFAULT NULL COMMENT '融资买入额(元)',
        `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (`date`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='市场资金流向与情绪指标表';
    """
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def fetch_and_save_money_flow():
    print("=" * 60)
    print("🚀 开始增量更新北向资金与市场两融余额数据...")
    print("=" * 60)

    conn = get_connection()
    try:
        conn = ensure_connection_alive(conn)
        init_money_flow_table(conn)

        records_map = {}

        # 1. 获取北向资金数据
        try:
            print("  [AKShare] 拉取北向资金流向历史...", flush=True)
            df_north = ak.stock_hsgt_fund_flow_summary_em()
            if df_north is not None and not df_north.empty:
                # 第一列通常为日期
                date_col = df_north.columns[0]
                for _, row in df_north.iterrows():
                    d_str = str(row[date_col]).strip()
                    if not d_str or d_str == 'None':
                        continue
                    try:
                        d_val = pd.to_datetime(d_str).strftime('%Y-%m-%d')
                    except Exception:
                        continue

                    # 尝试寻找净流入列
                    net_inflow = None
                    for c in df_north.columns:
                        if '净买额' in c or '净流入' in c or '资金' in c:
                            val = safe_float(row[c])
                            if val is not None:
                                net_inflow = val
                                break

                    if d_val not in records_map:
                        records_map[d_val] = {'north': net_inflow, 'margin_bal': None, 'margin_buy': None}
                    else:
                        records_map[d_val]['north'] = net_inflow
        except Exception as e:
            print(f"  [WARN] 无法拉取北向资金数据: {e}", flush=True)

        # 2. 获取沪深两融余额数据 (近 30 天)
        end_date_str = datetime.now().strftime('%Y%m%d')
        start_date_str = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
        try:
            print(f"  [AKShare] 拉取两融余额数据 ({start_date_str} ~ {end_date_str})...", flush=True)
            df_margin_sh = ak.stock_margin_sse(start_date=start_date_str, end_date=end_date_str)
            if df_margin_sh is not None and not df_margin_sh.empty:
                d_col = df_margin_sh.columns[0]
                for _, row in df_margin_sh.iterrows():
                    d_str = str(row[d_col]).strip()
                    try:
                        d_val = pd.to_datetime(d_str).strftime('%Y-%m-%d')
                    except Exception:
                        continue

                    bal = safe_float(row.iloc[1]) if len(row) > 1 else None
                    buy = safe_float(row.iloc[2]) if len(row) > 2 else None

                    if d_val not in records_map:
                        records_map[d_val] = {'north': None, 'margin_bal': bal, 'margin_buy': buy}
                    else:
                        if bal is not None:
                            records_map[d_val]['margin_bal'] = (records_map[d_val]['margin_bal'] or 0) + bal
                        if buy is not None:
                            records_map[d_val]['margin_buy'] = (records_map[d_val]['margin_buy'] or 0) + buy
        except Exception as e:
            print(f"  [WARN] 无法拉取两融余额数据: {e}", flush=True)

        # 3. 写入 TiDB 数据库
        if records_map:
            sql = """
            INSERT INTO `market_money_flow` (`date`, `north_net_inflow`, `margin_balance`, `margin_buy`)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                `north_net_inflow` = COALESCE(VALUES(`north_net_inflow`), `north_net_inflow`),
                `margin_balance` = COALESCE(VALUES(`margin_balance`), `margin_balance`),
                `margin_buy` = COALESCE(VALUES(`margin_buy`), `margin_buy`);
            """
            batch_data = []
            for d_val, data in records_map.items():
                batch_data.append((d_val, data['north'], data['margin_bal'], data['margin_buy']))

            with conn.cursor() as cur:
                cur.executemany(sql, batch_data)
            conn.commit()
            print(f"  [DB SUCCESS] 成功写入/更新了 {len(batch_data)} 条市场资金情绪数据！", flush=True)
        else:
            print("  ⚠️ 未获取到有效数据。", flush=True)

    finally:
        conn.close()


if __name__ == "__main__":
    fetch_and_save_money_flow()
