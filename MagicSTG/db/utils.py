#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公共工具函数模块
所有模块（更新脚本、策略脚本）统一从这里获取工具函数
"""

import random
import time
import sys
from datetime import datetime, timedelta
import pandas as pd

# 避免 Windows 控制台下打印 Emoji/中文 出现 GBK 编码错误
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


# ============================================================
# 类型转换工具
# ============================================================

def safe_int(val, default=0):
    """安全转换为整数"""
    if val is None or val == "" or pd.isna(val):
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def safe_float(val, default=None):
    """安全转换为浮点数"""
    if val is None or val == "" or pd.isna(val):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_str(val, default=None):
    """安全转换为字符串"""
    if val is None or val == "" or pd.isna(val):
        return default
    return str(val)


def safe_date(val, default=None):
    """安全转换为日期"""
    if val is None or val == "" or pd.isna(val):
        return default
    try:
        if isinstance(val, datetime):
            return val.date()
        if isinstance(val, str):
            return datetime.strptime(val, '%Y-%m-%d').date()
        return val
    except (ValueError, TypeError):
        return default


# ============================================================
# 时间工具
# ============================================================

def get_beijing_time():
    """获取当前北京时间 (UTC+8)"""
    return datetime.utcnow() + timedelta(hours=8)


def format_time(seconds):
    """格式化时间显示"""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    else:
        return f"{seconds/3600:.1f}h"


def get_trading_dates(start_date, end_date):
    """
    获取指定范围内的交易日列表
    注意：这里只是日期范围，实际交易日需要从数据库查询
    """
    start = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')
    dates = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # 周一到周五
            dates.append(current.strftime('%Y-%m-%d'))
        current += timedelta(days=1)
    return dates


def random_sleep(min_sec=0.3, max_sec=0.8):
    """随机延迟，避免请求过于频繁"""
    time.sleep(random.uniform(min_sec, max_sec))


# ============================================================
# Baostock 相关工具
# ============================================================

_LAST_LOGIN_CHECK_TIME = 0

def ensure_bs_login(force=False):
    """确保 Baostock 已登录"""
    global _LAST_LOGIN_CHECK_TIME
    import baostock as bs
    import time
    
    current_time = time.time()
    # 如果在最近300秒内已成功检查，且不强制校验，则直接返回在线
    if not force and (current_time - _LAST_LOGIN_CHECK_TIME < 300):
        return True
        
    try:
        rs = bs.query_stock_basic(code="sh.600000")
        if rs.error_code == '0':
            _LAST_LOGIN_CHECK_TIME = current_time
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
    print("[Baostock] Login successful", flush=True)
    _LAST_LOGIN_CHECK_TIME = time.time()
    return True


def get_target_date():
    """
    根据当前时间确定目标拉取日期
    18:00 之后拉取当天，之前拉取昨天
    返回: date_str
    """
    beijing_time = get_beijing_time()
    today_str = beijing_time.strftime('%Y-%m-%d')
    current_hour = beijing_time.hour
    
    if current_hour >= 18:
        return today_str
    else:
        return (beijing_time - timedelta(days=1)).strftime('%Y-%m-%d')


# ============================================================
# 进度输出工具
# ============================================================

def print_progress(current, total, start_time=None, prefix=""):
    """
    输出进度信息
    """
    if start_time is None:
        start_time = time.time()
    
    elapsed = time.time() - start_time
    pct = (current / total) * 100 if total > 0 else 0
    
    if current > 0 and total > 0:
        avg_time = elapsed / current
        remaining = avg_time * (total - current)
        remaining_str = format_time(remaining)
    else:
        remaining_str = "计算中..."
    
    print(f"  {prefix}PROGRESS: {current}/{total} ({pct:.1f}%) "
          f"已用: {format_time(elapsed)} 剩余: {remaining_str}", flush=True)