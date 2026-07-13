#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据更新主控脚本（基于任务日期 + 断点续传 + 连接保活修复）
每次更新进度时独立建立数据库连接，避免长连接空闲超时
"""

import os
import sys
import subprocess
import time
from datetime import datetime
import pymysql

# ============================================================
# 获取脚本所在目录
# ============================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# 加载 .env 文件（本地开发时使用）
# ============================================================

try:
    from dotenv import load_dotenv
    ENV_PATH = os.path.join(SCRIPT_DIR, '.env')
    if os.path.exists(ENV_PATH):
        load_dotenv(ENV_PATH)
        print(f"[ENV] Loaded .env from: {ENV_PATH}")
except ImportError:
    pass

# ============================================================
# 数据库配置
# ============================================================

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT") or 3306)
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "")
DB_SSL_CA = os.getenv("DB_SSL_CA", "")

# ============================================================
# 所有脚本（按依赖顺序）
# ============================================================

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

# 为不同脚本设置不同的超时时间（秒）
SCRIPT_TIMEOUT_MAP = {
    'update_index_kline_day.py': 10800,      # 3小时
    'update_stock_kline_day.py': 10800,      # 3小时
    'update_stock_profit_quarterly.py': 7200, # 2小时
    'update_stock_operation_quarterly.py': 7200,
    'update_stock_growth_quarterly.py': 7200,
    'update_stock_balance_quarterly.py': 7200,
    'update_stock_cash_flow_quarterly.py': 7200,
    'update_stock_dupont_quarterly.py': 7200,
    'update_stock_performance_express.py': 3600,
    'update_stock_forecast.py': 3600,
    'update_stock_kline_weekly.py': 3600,
    'update_stock_kline_monthly.py': 3600,
    'update_macro_deposit_rate.py': 600,
    'update_macro_loan_rate.py': 600,
    'update_macro_reserve_ratio.py': 600,
    'update_macro_money_supply_month.py': 600,
    'update_macro_money_supply_year.py': 600,
}

# 默认超时时间（秒）
DEFAULT_TIMEOUT = 5400  # 1.5小时

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
    
    if DB_SSL_CA and os.path.exists(DB_SSL_CA):
        conn_params["ssl"] = {"ca": DB_SSL_CA}
    
    return pymysql.connect(**conn_params)


def get_script_timeout(script_name):
    """获取脚本的超时时间"""
    return SCRIPT_TIMEOUT_MAP.get(script_name, DEFAULT_TIMEOUT)


# ============================================================
# 进度管理函数（每次独立连接）
# ============================================================

def init_daily_task():
    """
    初始化当天的任务记录
    如果今天还没有记录，插入所有脚本为 pending
    返回: (existed, pending_list)
    """
    today = datetime.now().strftime('%Y-%m-%d')
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(1) FROM update_progress WHERE task_date = %s",
                (today,)
            )
            count = cur.fetchone()[0]
            
            if count == 0:
                print(f"  [INIT] 初始化今日任务: {today}")
                for script in ALL_SCRIPTS:
                    cur.execute("""
                        INSERT INTO update_progress (task_date, script_name, status)
                        VALUES (%s, %s, 'pending')
                    """, (today, script))
                conn.commit()
                return False, ALL_SCRIPTS.copy()
            else:
                cur.execute("""
                    SELECT script_name FROM update_progress
                    WHERE task_date = %s AND status IN ('pending', 'failed')
                    ORDER BY id
                """, (today,))
                pending = [row[0] for row in cur.fetchall()]
                return True, pending
    finally:
        if conn:
            conn.close()


def mark_script_status(script_name, status, error_msg=None):
    """
    标记脚本状态（每次独立连接）
    """
    today = datetime.now().strftime('%Y-%m-%d')
    conn = None
    try:
        conn = get_db_connection()
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
    except Exception as e:
        print(f"  [WARN] mark_script_status({script_name}, {status}) 失败: {e}")
        raise
    finally:
        if conn:
            conn.close()


def is_task_completed():
    """检查今天的任务是否全部完成"""
    today = datetime.now().strftime('%Y-%m-%d')
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(1) FROM update_progress
                WHERE task_date = %s AND status IN ('pending', 'failed')
            """, (today,))
            return cur.fetchone()[0] == 0
    finally:
        if conn:
            conn.close()


def get_task_summary():
    """获取今天任务的执行摘要"""
    today = datetime.now().strftime('%Y-%m-%d')
    conn = None
    try:
        conn = get_db_connection()
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
    finally:
        if conn:
            conn.close()


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
    
    timeout = get_script_timeout(script_name)
    
    print(f"\n{'='*70}")
    print(f"  ▶ [{datetime.now().strftime('%H:%M:%S')}] 执行: {script_name}")
    print(f"  ⏱️  超时限制: {timeout // 60} 分钟")
    print(f"{'='*70}")
    
    try:
        # 使用与当前脚本相同的 Python 解释器，传递环境变量
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy()
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
        print(f"  ❌ {script_name} 执行超时 (> {timeout}s)")
        return False, f"执行超时 (> {timeout}s)"
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
    print(f"  脚本目录: {SCRIPT_DIR}")
    print("=" * 70)
    
    try:
        # 初始化当天任务
        print("\n[1] 检查今日任务状态...")
        existed, pending = init_daily_task()
        
        summary = get_task_summary()
        print(f"\n  📋 今日任务状态:")
        print(f"     总脚本数: {summary['total']}")
        print(f"     ✅ 已完成: {summary['success']}")
        print(f"     ⏳ 待执行: {summary['pending']}")
        print(f"     🔄 执行中: {summary['running']}")
        print(f"     ❌ 失败: {summary['failed']}")
        
        if not pending:
            print("\n✅ 所有脚本已完成，本次无需执行")
            return 0
        
        print(f"\n  📋 待执行脚本 ({len(pending)} 个):")
        for script in pending:
            timeout = get_script_timeout(script)
            print(f"     - {script} (超时: {timeout // 60} 分钟)")
        
        print("\n[2] 开始执行脚本...")
        success_count = 0
        fail_count = 0
        
        for script_name in pending:
            # 标记为 running（独立连接）
            try:
                mark_script_status(script_name, 'running')
            except Exception as e:
                print(f"  [ERROR] 无法标记 {script_name} 为 running: {e}")
                # 尝试继续执行，但可能进度表会不一致
                # 这里我们选择继续，因为子脚本会自己写入数据
            
            # 执行脚本
            success, output = run_single_script(script_name)
            
            # 标记状态（独立连接）
            try:
                if success:
                    mark_script_status(script_name, 'success')
                    success_count += 1
                else:
                    mark_script_status(script_name, 'failed', output)
                    fail_count += 1
            except Exception as e:
                print(f"  [ERROR] 无法标记 {script_name} 状态: {e}")
                if success:
                    success_count += 1
                else:
                    fail_count += 1
        
        # 最终检查
        completed = is_task_completed()
        final_summary = get_task_summary()
        
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
            remaining = final_summary['pending'] + final_summary['failed']
            print(f"\n  ⏳ 今日任务未完成，剩余 {remaining} 个脚本")
            print(f"  下次触发将继续执行")
        print("=" * 70)
        
        return 1 if fail_count > 0 and completed else 0
        
    except Exception as e:
        print(f"\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())