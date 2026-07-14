#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据更新主控脚本（状态面板版 + 断点续传 + 频率控制）
支持 --mode 参数：daily / weekly / monthly / quarterly / all
自动检测空表并执行初始化
"""

import os
import sys
import subprocess
import time
import argparse
import re
from datetime import datetime, timedelta

# 添加父目录到路径，以便导入 db 模块
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

# 所有脚本列表（用于依赖检查）
ALL_SCRIPTS = SCRIPT_GROUPS['all']


# ============================================================
# 数据库操作函数
# ============================================================

def check_table_empty(conn, table_name):
    """检查表是否为空"""
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(1) FROM {table_name}")
            count = cur.fetchone()[0]
            return count == 0
    except Exception:
        # 表不存在或查询失败，视为空
        return True


def get_script_status(conn, task_date, script_name):
    """获取脚本在今天的执行状态"""
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
    """初始化当天的任务记录"""
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
    """标记脚本状态"""
    with conn.cursor() as cur:
        if status == 'running':
            cur.execute("""
                UPDATE update_progress 
                SET status = %s, started_at = %s 
                WHERE task_date = %s AND script_name = %s
            """, (status, datetime.now(), task_date, script_name))
        elif status in ('success', 'failed'):
            cur.execute("""
                UPDATE update_progress 
                SET status = %s, completed_at = %s, error_msg = %s
                WHERE task_date = %s AND script_name = %s
            """, (status, datetime.now(), error_msg, task_date, script_name))
        conn.commit()


def get_pending_scripts(conn, task_date, scripts):
    """获取未完成的脚本"""
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
    """获取任务摘要"""
    with conn.cursor() as cur:
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


def reset_task(conn, task_date, scripts):
    """重置任务（将 running 和 failed 重置为 pending）"""
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


# ============================================================
# 脚本执行（状态面板版）
# ============================================================

def run_script_with_progress(script_name, task_date, conn):
    """
    运行单个脚本，实时捕获进度输出
    返回: (success, output, progress_info)
    """
    script_path = os.path.join(SCRIPT_DIR, script_name)
    
    if not os.path.exists(script_path):
        mark_script_status(conn, task_date, script_name, 'failed', f"脚本不存在: {script_path}")
        return False, f"脚本不存在: {script_path}", None
    
    # 标记为 running
    mark_script_status(conn, task_date, script_name, 'running')
    
    progress_info = {
        'current': 0,
        'total': 0,
        'last_update': time.time()
    }
    
    start_time = time.time()
    output_lines = []
    
    try:
        # 使用 subprocess.Popen 逐行读取输出
        process = subprocess.Popen(
            [sys.executable, script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=os.environ.copy()
        )
        
        # 逐行读取并解析进度
        for line in process.stdout:
            line = line.strip()
            output_lines.append(line)
            
            # 解析 PROGRESS: 标记
            if 'PROGRESS:' in line:
                match = re.search(r'PROGRESS:\s*(\d+)\s*/\s*(\d+)', line)
                if match:
                    progress_info['current'] = int(match.group(1))
                    progress_info['total'] = int(match.group(2))
                    progress_info['last_update'] = time.time()
        
        process.wait()
        elapsed = time.time() - start_time
        
        if process.returncode == 0:
            mark_script_status(conn, task_date, script_name, 'success')
            return True, '\n'.join(output_lines), progress_info
        else:
            error_msg = f"退出码: {process.returncode}"
            mark_script_status(conn, task_date, script_name, 'failed', error_msg)
            return False, '\n'.join(output_lines), progress_info
            
    except Exception as e:
        error_msg = str(e)
        mark_script_status(conn, task_date, script_name, 'failed', error_msg[:500])
        return False, str(e), progress_info


# ============================================================
# 状态面板显示
# ============================================================

def print_status_panel(task_date, mode, scripts, script_statuses, start_time):
    """
    打印状态面板
    script_statuses: {script_name: {'status': 'pending/running/success/failed', 'started_at': datetime, 'completed_at': datetime, 'progress': (current, total), 'error_msg': str}}
    """
    elapsed = time.time() - start_time
    
    # 清屏（移动光标到顶部）
    print("\033[H\033[J", end="")
    
    print("=" * 78)
    print("  📊 任务执行进度")
    print(f"  当前模式: {mode}")
    print(f"  任务日期: {task_date}")
    print(f"  已运行: {format_time(elapsed)}")
    print("-" * 78)
    
    # 统计已完成数
    completed = sum(1 for s in script_statuses.values() if s.get('status') == 'success')
    total = len(scripts)
    running_count = sum(1 for s in script_statuses.values() if s.get('status') == 'running')
    
    # 标题行
    print(f"  进度: {completed}/{total} 完成, {running_count} 执行中")
    print("-" * 78)
    
    for idx, script_name in enumerate(scripts, 1):
        status_info = script_statuses.get(script_name, {'status': 'pending'})
        status = status_info.get('status', 'pending')
        
        # 状态图标
        if status == 'success':
            icon = "✅"
        elif status == 'running':
            icon = "🔄"
        elif status == 'failed':
            icon = "❌"
        else:
            icon = "⏳"
        
        # 状态文字
        if status == 'success':
            status_text = "已完成"
            elapsed_text = f"耗时: {format_time((status_info.get('completed_at') - status_info.get('started_at')).total_seconds()) if status_info.get('started_at') and status_info.get('completed_at') else 'N/A'}"
        elif status == 'running':
            started = status_info.get('started_at')
            if started:
                run_time = time.time() - started.timestamp()
                elapsed_text = f"已用: {format_time(run_time)}"
            else:
                elapsed_text = "已用: N/A"
        elif status == 'failed':
            elapsed_text = f"错误: {status_info.get('error_msg', '未知错误')[:30]}"
        else:
            elapsed_text = "等待执行"
        
        # 进度信息
        progress = status_info.get('progress', (0, 0))
        if progress[1] > 0:
            pct = (progress[0] / progress[1]) * 100
            progress_text = f"进度: {pct:.1f}% ({progress[0]}/{progress[1]})"
        else:
            progress_text = ""
        
        # 估算剩余时间（仅对 running 状态）
        if status == 'running' and progress[1] > 0 and progress[0] > 0:
            started = status_info.get('started_at')
            if started:
                run_time = time.time() - started.timestamp()
                avg_time = run_time / progress[0]
                remaining = avg_time * (progress[1] - progress[0])
                progress_text += f" 剩余: {format_time(remaining)}"
        
        # 截断过长的脚本名
        display_name = script_name[:35] + "..." if len(script_name) > 35 else script_name
        
        print(f"  [{idx:2d}/{total:2d}] {display_name}")
        print(f"     {icon} {status_text}  {elapsed_text}")
        if progress_text:
            print(f"     {progress_text}")
        print("-" * 78)


def build_status_dict(scripts, conn, task_date):
    """构建脚本状态字典"""
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
# 主函数
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='数据更新主控脚本')
    parser.add_argument('--mode', type=str, default='daily',
                        choices=['daily', 'weekly', 'monthly', 'quarterly', 'all'],
                        help='更新模式')
    parser.add_argument('--reset', action='store_true',
                        help='重置当前任务（将 running/failed 重置为 pending）')
    args = parser.parse_args()
    
    mode = args.mode
    reset_flag = args.reset
    task_date = get_beijing_time().strftime('%Y-%m-%d')
    start_time = time.time()
    
    print("=" * 78)
    print("  📊 数据更新系统 - 状态面板版")
    print(f"  模式: {mode}")
    print(f"  任务日期: {task_date}")
    print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 78)
    print()
    
    # 获取要执行的脚本列表
    scripts = SCRIPT_GROUPS.get(mode, [])
    if not scripts:
        print(f"  [ERROR] 未知模式: {mode}")
        return 1
    
    print(f"  [INFO] 模式 '{mode}' 包含 {len(scripts)} 个脚本")
    
    # 连接数据库
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
        
        # 如果设置了 reset 标志，重置所有 running/failed 状态
        if reset_flag:
            reset_count = reset_task(conn, task_date, scripts)
            print(f"  [RESET] 重置了 {reset_count} 个脚本状态")
        
        # 获取待执行脚本
        pending = get_pending_scripts(conn, task_date, scripts)
        # 如果有空表，确保它们在待执行列表中
        for script in empty_tables:
            if script not in pending:
                pending.append(script)
                # 确保状态为 pending
                mark_script_status(conn, task_date, script, 'pending')
        
        if not pending:
            print("  [SUCCESS] 所有脚本已完成")
            return 0
        
        print(f"  [INFO] 待执行: {len(pending)} 个脚本")
        print("=" * 78)
        
        # 构建状态字典
        statuses = build_status_dict(scripts, conn, task_date)
        
        # 打印初始状态面板
        print_status_panel(task_date, mode, scripts, statuses, start_time)
        
        # 逐个执行待执行脚本
        executed_count = 0
        for script_name in pending:
            # 更新状态
            statuses[script_name]['status'] = 'running'
            statuses[script_name]['started_at'] = datetime.now()
            
            # 刷新面板
            print_status_panel(task_date, mode, scripts, statuses, start_time)
            
            # 执行脚本
            success, output, progress = run_script_with_progress(script_name, task_date, conn)
            
            # 更新状态信息
            statuses[script_name]['status'] = 'success' if success else 'failed'
            statuses[script_name]['completed_at'] = datetime.now()
            if progress and progress['total'] > 0:
                statuses[script_name]['progress'] = (progress['current'], progress['total'])
            
            # 刷新面板
            print_status_panel(task_date, mode, scripts, statuses, start_time)
            
            executed_count += 1
            
            # 输出脚本的详细日志（缩进显示）
            if output:
                print(f"\n  详细日志 ({script_name}):")
                for line in output.split('\n')[:20]:  # 只显示前20行
                    if line.strip():
                        print(f"     {line}")
                if len(output.split('\n')) > 20:
                    print(f"     ... (共 {len(output.split('\n'))} 行)")
                print()
        
        # 最终统计
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