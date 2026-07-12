#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据更新主控脚本（基于任务日期 + 断点续传）
每次运行自动检测当天任务，执行未完成的脚本，支持失败重试
"""

import os
import sys
import subprocess
import time
from datetime import datetime
import pymysql

# ============================================================
# 配置
# ============================================================

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "")
DB_SSL_CA = os.getenv("DB_SSL_CA", "")

# 所有脚本（按依赖顺序）
ALL_SCRIPTS = [
    'update_stock_basic.py',
    'update_stock_industry.py',
    'update_index_kline_day.py',
    'update_stock_kline_day.py',
    'update_stock_kline_weekly.py',
    'update_stock_kline_monthly.py',
    'update_stock_profit_quarterly.py',
    'update_stock_operation_quarterly.py',
    'update_stock_growth_quarterly.py',
    'update_stock_balance_quarterly.py',
    'update_stock_cash_flow_quarterly.py',
    'update_stock_dupont_quarterly.py',
    'update_stock_performance_express.py',
    'update_stock_forecast.py',
    'update_macro_deposit_rate.py',
    'update_macro_loan_rate.py',
    'update_macro_reserve_ratio.py',
    'update_macro_money_supply_month.py',
    'update_macro_money_supply_year.py',
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 单个脚本超时时间（秒），默认 1.5 小时
SCRIPT_TIMEOUT = 5400

# ============================================================
# 数据库连接
# ============================================================

def get_db_connection():
    """获取数据库连接"""
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
    
    # SSL 配置（如果提供了 CA 证书）
    if DB_SSL_CA and os.path.exists(DB_SSL_CA):
        conn_params["ssl"] = {"ca": DB_SSL_CA}
    
    return pymysql.connect(**conn_params)


def init_daily_task(conn):
    """
    初始化当天的任务记录
    如果今天还没有记录，插入所有脚本为 pending
    返回: (今日任务是否已存在, 待执行脚本列表)
    """
    today = datetime.now().strftime('%Y-%m-%d')
    
    with conn.cursor() as cur:
        # 查询今天是否有记录
        cur.execute(
            "SELECT COUNT(1) FROM update_progress WHERE task_date = %s",
            (today,)
        )
        count = cur.fetchone()[0]
        
        if count == 0:
            # 今天没有记录，初始化所有脚本为 pending
            print(f"  [INIT] 初始化今日任务: {today}")
            for script in ALL_SCRIPTS:
                cur.execute("""
                    INSERT INTO update_progress (task_date, script_name, status)
                    VALUES (%s, %s, 'pending')
                """, (today, script))
            conn.commit()
            return False, ALL_SCRIPTS.copy()
        else:
            # 查询今天未完成的脚本（pending 或 failed）
            cur.execute("""
                SELECT script_name FROM update_progress
                WHERE task_date = %s AND status IN ('pending', 'failed')
                ORDER BY id
            """, (today,))
            pending = [row[0] for row in cur.fetchall()]
            return True, pending


def mark_script_status(conn, script_name, status, error_msg=None):
    """标记脚本状态"""
    today = datetime.now().strftime('%Y-%m-%d')
    
    with conn.cursor() as cur:
        if status == 'running':
            cur.execute("""
                UPDATE update_progress 
                SET status = %s, started_at = %s 
                WHERE task_date = %s AND script_name = %s
            """, (status, datetime.now(), today, script_name))
        elif status in ('success', 'failed'):
            cur.execute("""
                UPDATE update_progress 
                SET status = %s, completed_at = %s, error_msg = %s
                WHERE task_date = %s AND script_name = %s
            """, (status, datetime.now(), error_msg, today, script_name))
        conn.commit()


def is_task_completed(conn):
    """检查今天的任务是否全部完成"""
    today = datetime.now().strftime('%Y-%m-%d')
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(1) FROM update_progress
            WHERE task_date = %s AND status IN ('pending', 'failed')
        """, (today,))
        return cur.fetchone()[0] == 0


def get_task_summary(conn):
    """获取今天任务的执行摘要"""
    today = datetime.now().strftime('%Y-%m-%d')
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 
                COUNT(1) AS total,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
            FROM update_progress
            WHERE task_date = %s
        """, (today,))
        row = cur.fetchone()
        return {
            'total': row[0],
            'pending': row[1],
            'running': row[2],
            'success': row[3],
            'failed': row[4],
        }


# ============================================================
# 脚本执行
# ============================================================

def run_single_script(script_name):
    """
    运行单个脚本
    返回: (success, output)
    """
    script_path = os.path.join(SCRIPT_DIR, script_name)
    
    if not os.path.exists(script_path):
        return False, f"脚本不存在: {script_path}"
    
    print(f"\n{'='*70}")
    print(f"  ▶ [{datetime.now().strftime('%H:%M:%S')}] 执行: {script_name}")
    print(f"{'='*70}")
    
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT
        )
        
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        
        if result.returncode == 0:
            print(f"  ✅ {script_name} 执行成功")
            return True, result.stdout
        else:
            print(f"  ❌ {script_name} 执行失败 (退出码: {result.returncode})")
            return False, result.stderr[:500]
            
    except subprocess.TimeoutExpired:
        print(f"  ❌ {script_name} 执行超时 (> {SCRIPT_TIMEOUT}s)")
        return False, f"执行超时 (> {SCRIPT_TIMEOUT}s)"
    except Exception as e:
        print(f"  ❌ {script_name} 执行异常: {e}")
        return False, str(e)


# ============================================================
# 主函数
# ============================================================

def main():
    start_time = time.time()
    
    print("=" * 70)
    print("  📊 数据更新系统 - 基于任务日期的断点续传")
    print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    # 数据库连接
    conn = None
    for attempt in range(1, 4):
        try:
            conn = get_db_connection()
            print("[DB] Database connection established!")
            break
        except Exception as e:
            print(f"Failed to connect (attempt {attempt}/3): {e}")
            if attempt < 3:
                time.sleep(2)
            else:
                print("Error: Could not establish database connection. Exiting.")
                return 1
    
    try:
        # 初始化当天任务
        print("\n[1] 检查今日任务状态...")
        existed, pending = init_daily_task(conn)
        
        if existed:
            print(f"  [INFO] 今日任务已存在，继续执行未完成的脚本")
        else:
            print(f"  [INFO] 今日任务已初始化，共 {len(pending)} 个脚本")
        
        # 显示任务摘要
        summary = get_task_summary(conn)
        print(f"\n  📋 今日任务状态:")
        print(f"     总脚本数: {summary['total']}")
        print(f"     ✅ 已完成: {summary['success']}")
        print(f"     ⏳ 待执行: {summary['pending']}")
        print(f"     🔄 执行中: {summary['running']}")
        print(f"     ❌ 失败: {summary['failed']}")
        
        # 如果没有待执行脚本，结束
        if not pending:
            print("\n✅ 所有脚本已完成，本次无需执行")
            return 0
        
        print(f"\n  📋 待执行脚本 ({len(pending)} 个):")
        for script in pending:
            print(f"     - {script}")
        
        # 执行脚本
        print("\n[2] 开始执行脚本...")
        success_count = 0
        fail_count = 0
        
        for script_name in pending:
            # 标记为 running
            mark_script_status(conn, script_name, 'running')
            
            # 执行脚本
            success, output = run_single_script(script_name)
            
            # 标记状态
            if success:
                mark_script_status(conn, script_name, 'success')
                success_count += 1
            else:
                mark_script_status(conn, script_name, 'failed', output)
                fail_count += 1
            
            # 检查 GitHub Actions 剩余时间（如果运行在 Actions 中）
            # 如果剩余时间不足 10 分钟，主动退出，避免被强制终止
            if os.environ.get('GITHUB_ACTIONS') == 'true':
                # 粗略估计剩余时间：每执行一个脚本大约 5 分钟
                # 这里不强制退出，让脚本自然结束
                pass
        
        # 最终检查
        completed = is_task_completed(conn)
        final_summary = get_task_summary(conn)
        
        print("\n" + "=" * 70)
        print("  📊 本轮执行总结")
        print(f"  结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  总耗时: {time.time() - start_time:.2f}s")
        print("-" * 70)
        print(f"  本轮成功: {success_count}")
        print(f"  本轮失败: {fail_count}")
        print(f"  今日总完成: {final_summary['success']}/{final_summary['total']}")
        
        if completed:
            print("\n  🎉 今日任务全部完成！")
        else:
            print(f"\n  ⏳ 今日任务未完成，剩余 {final_summary['pending'] + final_summary['failed']} 个脚本")
            print(f"  下次触发将继续执行")
        
        print("=" * 70)
        
        return 1 if fail_count > 0 and completed else 0
        
    except Exception as e:
        print(f"\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())