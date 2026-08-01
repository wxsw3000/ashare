#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可转债每日衍生指标同步脚本 (cb_daily_indicator)
分批关联 cb_kline_day、stock_kline_day 与 cb_basic，高效计算：
  - 转股价值 (平价) = (100 / 转股价) * 正股收盘价
  - 转股溢价率 (%) = (转债收盘价 - 转股价值) / 转股价值 * 100
  - 双低值 = 转债收盘价 + 转股溢价率
写入/更新 cb_daily_indicator 表
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

from MagicSTG.core.db import get_connection, ensure_connection_alive


def update_cb_daily_indicator():
    print("=" * 60)
    print("🚀 开始计算并更新可转债每日衍生指标 (cb_daily_indicator)...")
    print("=" * 60)
    
    conn = get_connection()
    try:
        conn = ensure_connection_alive(conn)
        update_date = time.strftime('%Y-%m-%d')
        
        # 1. 获取包含 K 线的所有不重复交易日期
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT date FROM cb_kline_day ORDER BY date ASC")
            rows = cur.fetchall()
            dates = [r[0].strftime('%Y-%m-%d') for r in rows if r[0]]
            
        print(f"  [DB] 找到 {len(dates)} 个包含了转债 K 线的交易日期，开始分批按月计算...", flush=True)
        
        if not dates:
            print("  ⚠️ cb_kline_day 表中暂无日期数据")
            return

        # 2. 按月份分组批量执行，避免一次性大 JOIN 占用 TiDB 超额内存
        # 将日期按 YYYY-MM 分组
        date_series = pd.to_datetime(dates)
        months = date_series.strftime('%Y-%m').unique()
        
        calc_sql = """
        INSERT INTO `cb_daily_indicator` (
            `code`, `date`, `cb_price`, `stock_code`, `stock_price`,
            `convert_price`, `convert_value`, `convert_premium_rate`,
            `db_low_value`, `update_date`
        )
        SELECT 
            cb.`code`,
            cb.`date`,
            cb.`close` AS cb_price,
            b.`stock_code`,
            st.`close` AS stock_price,
            b.`convert_price`,
            IF(b.`convert_price` > 0, ROUND((100.0 / b.`convert_price`) * st.`close`, 4), NULL) AS convert_value,
            IF(b.`convert_price` > 0 AND st.`close` > 0, 
               ROUND(((cb.`close` - ((100.0 / b.`convert_price`) * st.`close`)) / ((100.0 / b.`convert_price`) * st.`close`)) * 100.0, 4), 
               NULL
            ) AS convert_premium_rate,
            IF(b.`convert_price` > 0 AND st.`close` > 0, 
               ROUND(cb.`close` + (((cb.`close` - ((100.0 / b.`convert_price`) * st.`close`)) / ((100.0 / b.`convert_price`) * st.`close`)) * 100.0), 4), 
               NULL
            ) AS db_low_value,
            %s AS update_date
        FROM `cb_kline_day` cb
        JOIN `cb_basic` b ON cb.`code` = b.`code`
        JOIN `stock_kline_day` st ON st.`code` = b.`stock_code` AND cb.`date` = st.`date`
        WHERE cb.`date` >= %s AND cb.`date` <= %s
        ON DUPLICATE KEY UPDATE
            `cb_price` = VALUES(`cb_price`),
            `stock_code` = VALUES(`stock_code`),
            `stock_price` = VALUES(`stock_price`),
            `convert_price` = VALUES(`convert_price`),
            `convert_value` = VALUES(`convert_value`),
            `convert_premium_rate` = VALUES(`convert_premium_rate`),
            `db_low_value` = VALUES(`db_low_value`),
            `update_date` = VALUES(`update_date`);
        """
        
        total_affected = 0
        start_time = time.time()
        
        for idx, month_str in enumerate(months, 1):
            m_dates = date_series[date_series.strftime('%Y-%m') == month_str]
            min_d = m_dates.min().strftime('%Y-%m-%d')
            max_d = m_dates.max().strftime('%Y-%m-%d')
            
            for attempt in range(1, 4):
                try:
                    conn = ensure_connection_alive(conn)
                    with conn.cursor() as cur:
                        affected = cur.execute(calc_sql, (update_date, min_d, max_d))
                        total_affected += affected
                    conn.commit()
                    break
                except Exception as ex:
                    print(f"  ⚠️ 处理 {month_str} ({min_d} ~ {max_d}) 失败 (尝试 {attempt}/3): {ex}")
                    time.sleep(2)
                    
            if idx % 12 == 0 or idx == len(months):
                print(f"  PROGRESS: {idx}/{len(months)} 月份 ({(idx/len(months))*100:.1f}%) | 已更新衍生指标记录: {total_affected}", flush=True)
                
        elapsed = time.time() - start_time
        print(f"\n🎉 可转债衍生指标计算完毕！共生成/更新 {total_affected} 条包含双低、溢价率、转股价值的数据（总耗时 {elapsed:.2f}s）")
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ 计算可转债衍生指标失败: {e}")
        raise e
    finally:
        if conn:
            conn.close()


if __name__ == '__main__':
    update_cb_daily_indicator()
