#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库公共模块
提供统一的数据库连接和工具函数
所有模块（更新脚本、策略脚本）统一从这里导入
"""

from .db_config import (
    get_connection,
    get_connection_with_retry,
    get_config,
    get_connection_info,
    execute_query,
    execute_many,
)

from .utils import (
    # 类型转换
    safe_int,
    safe_float,
    safe_str,
    safe_date,
    # 时间工具
    get_beijing_time,
    format_time,
    get_trading_dates,
    random_sleep,
    # Baostock 工具
    ensure_bs_login,
    get_target_date,
    # 进度输出
    print_progress,
)

# 导出所有公共接口
__all__ = [
    # 数据库连接
    'get_connection',
    'get_connection_with_retry',
    'get_config',
    'get_connection_info',
    'execute_query',
    'execute_many',
    # 类型转换
    'safe_int',
    'safe_float',
    'safe_str',
    'safe_date',
    # 时间工具
    'get_beijing_time',
    'format_time',
    'get_trading_dates',
    'random_sleep',
    # Baostock 工具
    'ensure_bs_login',
    'get_target_date',
    # 进度输出
    'print_progress',
]