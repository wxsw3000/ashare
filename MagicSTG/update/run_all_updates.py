#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据更新主控脚本（智能判断版 + 断点续传）
自动根据当前日期判断该执行哪些脚本：
    - 每天：日K线 + 基础数据 + 宏观数据
    - 每周一：额外执行周K线
    - 每月1号：额外执行月K线
    - 1月/4月/7月/10月：额外执行财报数据（季频）
"""

import os
import sys
import subprocess
import time
import argparse
import re
from datetime import datetime

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import (
    get_connection,
    get_connection_with_retry,
    format_time,
    get_beijing_time,
)

# ============================================================
# 配置
# ============================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 表名与脚本名的映射（用于检查表是否为空）
SCRIPT_TABLE_MAP = {
    'update_stock_basic.py': 'stock_basic',
    'update_stock_industry.py': 'stock_industry',
    'update_index_kline_day.py': 'index_kline_day',
    'update_stock_kline_day.py': 'stock_kline_day',
    'update_stock_kline_weekly.py': 'stock_kline_weekly',
    'update_stock_kline_monthly.py': 'stock_kline_monthly',
    'update_stock_profit_quarterly.py': 'stock_profit_quarterly',
    'update_stock_operation_quarterly.py': 'stock_operation_quarterly',
    'update_stock_growth_quarterly.py': 'stock_growth_quarterly',
    'update_stock_balance_quarterly.py': 'stock_balance_quarterly',
    'update_stock_cash_flow_quarterly.py': 'stock_cash_flow_quarterly',
    'update_stock_dupont_quarterly.py': 'stock_dupont_quarterly',
    'update_stock_performance_express.py': 'stock_performance_express',
    'update_stock_forecast.py': 'stock_forecast',
    'update_macro_deposit_rate.py': 'macro_deposit_rate',
    'update_macro_loan_rate.py': 'macro_loan_rate',
    'update_macro_reserve_ratio.py': 'macro_reserve_ratio',
    'update_macro_money_supply_month.py': 'macro_money_supply_month',
    'update_macro_money_supply_year.py': 'macro_money_supply_year',
}

# 按频率分组
SCRIPT_GROUPS = {
    'daily': [
        'update_stock_basic.py',
        'update_stock_industry.py',
        'update_index_kline_day.py',
        'update_stock_kline_day.py',
        'update_macro_deposit_rate.py',
        'update_macro_loan_rate.py',
        'update_macro_reserve_ratio.py',
        'update_macro_money_supply_month.py',
        'update_macro_money_supply_year.py',
    ],
    'weekly': [
        'update_stock_kline_weekly.py',
    ],
    'monthly': [
        'update_stock_kline_monthly.py',
    ],
    'quarterly': [
        'update_stock_profit_quarterly.py',
        'update_stock_operation_quarterly.py',
        'update_stock_growth_quarterly.py',
        'update_stock_balance_quarterly.py',
        'update_stock_cash_flow_quarterly.py',
        'update_stock_dupont_quarterly.py',
        'update_stock_performance_express.py',
        'update_stock_forecast.py',
    ],
    'all': [
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
}

# 财报季月份
QUARTERLY_MONTHS = [1, 4, 7, 10]


# ============================================================
# 智能判断：根据日期决定执行哪些脚本
# ============================================================

def get_scripts_by_date():
    """
    根据当前日期自动判断应该执行哪些脚本
    返回: list of script names
    """
    beijing_time = get_beijing_time()
    weekday = beijing_time.weekday()  # 0=周一, 6=周日
    day = beijing_time.day
    month = beijing_time.month
    
    scripts = []
    
    # 1. 每日必跑：日K线 + 基础数据 + 宏观数据
    scripts.extend(SCRIPT_GROUPS['daily'])
    
    # 2. 每周一跑周K线
    if weekday == 0:  # 周一
        scripts.extend(SCRIPT_GROUPS['weekly'])
        print("  [SCHEDULE] 今天是周一，将执行周K线更新")
    
    # 3. 每月1号跑月K线
    if day == 1:
        scripts.extend(SCRIPT_GROUPS['monthly'])
        print("  [SCHEDULE] 今天是1号，将执行月K线更新")
    
    # 4. 季频：1月、4月、7月、10月跑财报数据
    if month in QUARTERLY_MONTHS:
        scripts.extend(SCRIPT_GROUPS['quarterly'])
        print(f"  [SCHEDULE] 当前是财报季（{month}月），将执行季频财报数据更新")
    
    # 去重（保持顺序）
    seen = set()
    result = []
    for s in scripts:
        if s not in seen:
            seen.add(s)
            result.append(s)
    
    return result


# ============================================================
# 数据库操作
# ============================================================

def check_table_empty(conn, table_name):
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(1) FROM {table_name}")
            count = cur.fetchone()[0]
            return count == 0
    except Exception:
        return True


def get_script_status(conn, task_date, script_name):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status, started_at, completed_at, error_msg
                FROM update_progress
                WHERE task_date = %s AND script_name = %s
            """, (task_date, script_name))
            row = cur.fetchone()
            if row:
                return {'status': row[0], 'started_at': row[1], 'completed_at': row[2], 'error_msg': row[3]}
            return None
    except Exception:
        return None


def init_daily_task(conn, task_date, scripts):
    with conn.cursor() as cur:
        for script in scripts:
            cur.execute("""
                INSERT INTO update_progress (task_date, script_name, status)
                VALUES (%s, %s, 'pending')
                ON DUPLICATE KEY UPDATE
                    status = IF(status IN ('pending', 'failed'), status, status)
            """, (task_date, script))
        conn.commit()


def mark_script_status(conn, task_date, script_name, status, error_msg=None):
    """
    更新脚本执行进度
    忽略传入的 conn 并开启临时连接，防止因长耗时子进程导致连接超时失效
    """
    temp_conn = get_connection_with_retry()
    try:
        with temp_conn.cursor() as cur:
            if status == 'running':
                cur.execute("""
                    UPDATE update_progress 
                    SET status = %s, started_at = %s 
                    WHERE task_date = %s AND script_name = %s
                """, (status, datetime.now(), task_date, script_name))
            elif status == 'pending':
                cur.execute("""
                    UPDATE update_progress 
                    SET status = %s, started_at = NULL, completed_at = NULL, error_msg = %s
                    WHERE task_date = %s AND script_name = %s
                """, (status, error_msg, task_date, script_name))
            elif status in ('success', 'failed'):
                cur.execute("""
                    UPDATE update_progress 
                    SET status = %s, completed_at = %s, error_msg = %s
                    WHERE task_date = %s AND script_name = %s
                """, (status, datetime.now(), error_msg, task_date, script_name))
            temp_conn.commit()
    except Exception as e:
        print(f"  [ERROR] Failed to mark script status for {script_name}: {e}", flush=True)
    finally:
        try:
            temp_conn.close()
        except Exception:
            pass


def get_pending_scripts(conn, task_date, scripts):
    pending = []
    with conn.cursor() as cur:
        placeholders = ','.join(['%s'] * len(scripts))
        cur.execute(f"""
            SELECT script_name FROM update_progress
            WHERE task_date = %s AND script_name IN ({placeholders})
            AND status IN ('pending', 'failed')
            ORDER BY id
        """, (task_date, *scripts))
        rows = cur.fetchall()
        pending = [row[0] for row in rows]
    return pending


def get_task_summary(conn, task_date, scripts):
    """
    获取任务汇总信息
    忽略传入的 conn 并开启临时连接，防止主连接超时失效
    """
    temp_conn = get_connection_with_retry()
    try:
        with temp_conn.cursor() as cur:
            placeholders = ','.join(['%s'] * len(scripts))
            cur.execute(f"""
                SELECT 
                    COUNT(1) AS total,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
                FROM update_progress
                WHERE task_date = %s AND script_name IN ({placeholders})
            """, (task_date, *scripts))
            row = cur.fetchone()
            return {
                'total': row[0] or 0,
                'pending': row[1] or 0,
                'running': row[2] or 0,
                'success': row[3] or 0,
                'failed': row[4] or 0,
            }
    finally:
        try:
            temp_conn.close()
        except Exception:
            pass


def reset_task(conn, task_date, scripts):
    with conn.cursor() as cur:
        placeholders = ','.join(['%s'] * len(scripts))
        cur.execute(f"""
            UPDATE update_progress 
            SET status = 'pending', started_at = NULL, completed_at = NULL, error_msg = NULL
            WHERE task_date = %s AND script_name IN ({placeholders})
            AND status IN ('running', 'failed')
        """, (task_date, *scripts))
        conn.commit()
        return cur.rowcount


def build_status_dict(scripts, conn, task_date):
    statuses = {}
    for script in scripts:
        status_info = get_script_status(conn, task_date, script)
        if status_info:
            statuses[script] = {
                'status': status_info['status'],
                'started_at': status_info['started_at'],
                'completed_at': status_info['completed_at'],
                'error_msg': status_info['error_msg'],
                'progress': (0, 0)
            }
        else:
            statuses[script] = {
                'status': 'pending',
                'started_at': None,
                'completed_at': None,
                'error_msg': None,
                'progress': (0, 0)
            }
    return statuses


# ============================================================
# 脚本执行
# ============================================================

def run_script_with_progress(script_name, task_date, conn):
    script_path = os.path.join(SCRIPT_DIR, script_name)
    
    if not os.path.exists(script_path):
        mark_script_status(conn, task_date, script_name, 'failed', f"脚本不存在: {script_path}")
        return False, f"脚本不存在: {script_path}", None, -1
    
    mark_script_status(conn, task_date, script_name, 'running')
    
    progress_info = {
        'current': 0,
        'total': 0,
        'last_update': time.time()
    }
    
    output_lines = []
    
    try:
        process = subprocess.Popen(
            [sys.executable, "-u", script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=os.environ.copy()
        )
        
        for line in process.stdout:
            line = line.strip()
            output_lines.append(line)
            
            if line:
                print(f"  {line}", flush=True)
            
            if 'PROGRESS:' in line:
                match = re.search(r'PROGRESS:\s*(\d+)\s*/\s*(\d+)', line)
                if match:
                    progress_info['current'] = int(match.group(1))
                    progress_info['total'] = int(match.group(2))
                    progress_info['last_update'] = time.time()
        
        process.wait()
        
        ret_code = process.returncode
        if ret_code == 0:
            mark_script_status(conn, task_date, script_name, 'success')
            return True, '\n'.join(output_lines), progress_info, ret_code
        elif ret_code == 2:
            # 优雅超时退出，状态改回 pending 以便重试
            mark_script_status(conn, task_date, script_name, 'pending', 'Reached runtime limit, partially completed')
            return True, '\n'.join(output_lines), progress_info, ret_code
        else:
            error_msg = f"退出码: {ret_code}"
            mark_script_status(conn, task_date, script_name, 'failed', error_msg)
            return False, '\n'.join(output_lines), progress_info, ret_code
            
    except Exception as e:
        error_msg = str(e)
        mark_script_status(conn, task_date, script_name, 'failed', error_msg[:500])
        return False, str(e), progress_info, -1


# ============================================================
# 显示函数
# ============================================================

def print_initial_status(task_date, scripts, statuses, start_time):
    elapsed = time.time() - start_time
    
    print("=" * 78)
    print("  📊 任务执行进度")
    print(f"  任务日期: {task_date}")
    print(f"  脚本总数: {len(scripts)}")
    print(f"  已运行: {format_time(elapsed)}")
    print("-" * 78)
    
    for idx, script_name in enumerate(scripts, 1):
        status_info = statuses.get(script_name, {'status': 'pending'})
        status = status_info.get('status', 'pending')
        
        if status == 'success':
            icon = "✅"
            status_text = "已完成"
        elif status == 'running':
            icon = "🔄"
            status_text = "执行中"
        elif status == 'failed':
            icon = "❌"
            status_text = "失败"
        else:
            icon = "⏳"
            status_text = "待执行"
        
        display_name = script_name[:35] + "..." if len(script_name) > 35 else script_name
        print(f"  [{idx:2d}/{len(scripts):2d}] {display_name}  {icon} {status_text}")
    
    print("=" * 78)
    print(flush=True)


def print_status_summary(scripts, statuses, start_time):
    elapsed = time.time() - start_time
    
    completed = sum(1 for s in statuses.values() if s.get('status') == 'success')
    running = sum(1 for s in statuses.values() if s.get('status') == 'running')
    failed = sum(1 for s in statuses.values() if s.get('status') == 'failed')
    total = len(scripts)
    
    running_scripts = [s[:30] for s, info in statuses.items() if info.get('status') == 'running']
    running_names = ", ".join(running_scripts[:3])
    if len(running_scripts) > 3:
        running_names += f" ... (+{len(running_scripts)-3}个)"
    
    print(f"[进度] 完成: {completed}/{total} | 执行中: {running} | 失败: {failed} | 已运行: {format_time(elapsed)}")
    if running_scripts:
        print(f"       正在执行: {running_names}", flush=True)


# ============================================================
# 主函数
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='数据更新主控脚本（智能判断版）')
    parser.add_argument('--reset', action='store_true',
                        help='重置当前任务（将 running/failed 重置为 pending）')
    parser.add_argument('--force', action='store_true',
                        help='强制执行所有脚本（忽略日期判断）')
    args = parser.parse_args()
    
    reset_flag = args.reset
    force_flag = args.force
    task_date = get_beijing_time().strftime('%Y-%m-%d')
    start_time = time.time()
    
    print("=" * 78)
    print("  📊 数据更新系统 - 智能判断版")
    print(f"  任务日期: {task_date}")
    print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 78)
    print()
    
    # 智能判断要执行的脚本
    if force_flag:
        scripts = SCRIPT_GROUPS['all']
        print("  [FORCE] 强制执行所有脚本")
    else:
        scripts = get_scripts_by_date()
        print(f"  [SCHEDULE] 智能判断完成，共 {len(scripts)} 个脚本")
    
    if not scripts:
        print("  [INFO] 今天没有需要执行的脚本")
        return 0
    
    # 显示脚本列表
    print("\n  📋 脚本列表:")
    for idx, script in enumerate(scripts, 1):
        print(f"     {idx:2d}. {script}")
    print()
    
    conn = None
    try:
        conn = get_connection_with_retry()
        print("  [DB] 数据库连接成功")
    except Exception as e:
        print(f"  [ERROR] 数据库连接失败: {e}")
        return 1
    
    try:
        # 检查哪些表为空，需要初始化
        print("  [CHECK] 检查数据表状态...")
        empty_tables = []
        for script in scripts:
            table_name = SCRIPT_TABLE_MAP.get(script)
            if table_name and check_table_empty(conn, table_name):
                empty_tables.append(script)
        
        if empty_tables:
            print(f"  [INIT] 检测到 {len(empty_tables)} 个表为空，将自动初始化:")
            for script in empty_tables:
                print(f"     - {script}")
        
        # 初始化任务记录
        init_daily_task(conn, task_date, scripts)
        
        if reset_flag:
            reset_count = reset_task(conn, task_date, scripts)
            print(f"  [RESET] 重置了 {reset_count} 个脚本状态")
        
        pending = get_pending_scripts(conn, task_date, scripts)
        for script in empty_tables:
            if script not in pending:
                pending.append(script)
                mark_script_status(conn, task_date, script, 'pending')
        
        if not pending:
            print("  [SUCCESS] 所有脚本已完成")
            return 0
        
        print(f"  [INFO] 待执行: {len(pending)} 个脚本")
        print("=" * 78)
        
        statuses = build_status_dict(scripts, conn, task_date)
        print_initial_status(task_date, scripts, statuses, start_time)
        
        early_exit = False
        for script_name in pending:
            statuses[script_name]['status'] = 'running'
            statuses[script_name]['started_at'] = datetime.now()
            
            print_status_summary(scripts, statuses, start_time)
            
            print(f"\n  ▶ 开始执行: {script_name}")
            print("-" * 50)
            
            success, output, progress, ret_code = run_script_with_progress(script_name, task_date, conn)
            
            if ret_code == 2:
                statuses[script_name]['status'] = 'pending'
                statuses[script_name]['completed_at'] = None
                early_exit = True
            else:
                statuses[script_name]['status'] = 'success' if success else 'failed'
                statuses[script_name]['completed_at'] = datetime.now()
                
            if progress and progress['total'] > 0:
                statuses[script_name]['progress'] = (progress['current'], progress['total'])
            
            print("-" * 50)
            if ret_code == 2:
                print(f"  ⚠️ {script_name} 达到时间限制优雅退出，已保存进度")
            elif success:
                print(f"  ✅ {script_name} 执行成功")
            else:
                print(f"  ❌ {script_name} 执行失败")
            
            print_status_summary(scripts, statuses, start_time)
            print()
            
            if early_exit:
                print("  [WARN] 达到运行时间上限，已保存断点，停止后续脚本的执行。")
                break
        
        summary = get_task_summary(conn, task_date, scripts)
        
        print("\n" + "=" * 78)
        print("  📊 执行完成")
        print(f"  总耗时: {format_time(time.time() - start_time)}")
        print("-" * 78)
        print(f"  ✅ 成功: {summary['success']}")
        print(f"  ❌ 失败: {summary['failed']}")
        print(f"  ⏳ 待执行: {summary['pending'] + summary['running']}")
        print("=" * 78)
        
        return 1 if summary['failed'] > 0 else 0
        
    except Exception as e:
        print(f"\n  [ERROR] 执行异常: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())