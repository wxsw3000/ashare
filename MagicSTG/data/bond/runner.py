#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可转债数据同步统一主控入口 (MagicSTG/data/bond/runner.py)
顺序执行：
  1. 基础信息 (update_cb_basic.py)
  2. 日线 K 线 (update_cb_kline_day.py)
  3. 衍生指标 (update_cb_daily_indicator.py)
"""

import sys
import os

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from MagicSTG.data.bond.init_cb_tables import init_tables
from MagicSTG.data.bond.update_cb_basic import fetch_and_update_cb_basic
from MagicSTG.data.bond.update_cb_kline_day import update_cb_kline_day
from MagicSTG.data.bond.update_cb_daily_indicator import update_cb_daily_indicator


def run_all_bond_updates():
    print("=" * 70)
    print("  🚀 MagicSTG - 可转债全量/增量数据更新系统")
    print("=" * 70)
    
    # 0. 确认建表
    init_tables()
    
    # 1. 基础信息
    fetch_and_update_cb_basic()
    
    # 2. 日线 K 线
    update_cb_kline_day()
    
    # 3. 衍生指标
    update_cb_daily_indicator()
    
    print("\n" + "=" * 70)
    print("  ✅ 可转债全部数据拉取与衍生指标计算完成！")
    print("=" * 70)


if __name__ == '__main__':
    run_all_bond_updates()
