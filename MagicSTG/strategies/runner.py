# -*- coding: utf-8 -*-
"""
MagicSTG Strategy Recommendation Runner & Persistence Engine
盘后策略推荐生成与持久化引擎
数据更新完毕后调用，自动运行注册策略并落库至 recommendations 与 positions 表。
"""

import os
import sys
import pandas as pd
from datetime import datetime
from typing import Optional, Dict, Any

MAGICSTG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(MAGICSTG_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from MagicSTG.core.db import (
    get_connection,
    load_cb_data_db,
    load_all_data_db
)
from MagicSTG.core.db_writer import save_recommendations
from MagicSTG.strategies.registry import StrategyRegistry
from MagicSTG.db.utils import get_target_date, get_beijing_time


def update_strategy_checkpoint(strategy_name: str, target_date: str):
    """更新策略 Checkpoint"""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO strategy_checkpoints (strategy, last_check_date, updated_at)
            VALUES (%s, %s, NOW())
            ON DUPLICATE KEY UPDATE last_check_date = VALUES(last_check_date), updated_at = NOW()
        """, (strategy_name, target_date))
        conn.commit()
    except Exception as e:
        print(f"  [Checkpoint] ⚠️ 更新 checkpoint 失败: {e}", flush=True)
    finally:
        if conn:
            conn.close()


def run_cb_double_low_recommendation(target_date: str, strategy_id: str = 'cb_double_low', config: Optional[Dict[str, Any]] = None) -> bool:
    """运行可转债双低策略推荐并落库"""
    print(f"\n  [Strategy] 运行可转债双低策略推荐 ({strategy_id}, 日期: {target_date})...", flush=True)
    try:
        data = load_cb_data_db(start_date=target_date, end_date=target_date)
        indicator_df = data.get('cb_daily_indicator')

        if indicator_df is None or indicator_df.empty:
            print(f"  [Strategy] ⚠️ {target_date} 缺少可转债衍生指标数据，跳过双低策略信号计算。")
            return False

        strategy = StrategyRegistry.get('cb_double_low', config=config)
        buy_signals, sell_signals = strategy.generate_signals(
            date=pd.to_datetime(target_date),
            data={'cb_daily_indicator': indicator_df},
            current_holdings=[]
        )

        save_recommendations(strategy_id, buy_signals, sell_signals, target_date)
        update_strategy_checkpoint(strategy_id, target_date)
        return True

    except Exception as e:
        print(f"  [Strategy ERROR] ❌ 可转债双低策略计算失败: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return False


def run_dynamic_factor_recommendation(target_date: str, strategy_id: str = 'dynamic_factor', config: Optional[Dict[str, Any]] = None) -> bool:
    """运行动态多因子股票策略推荐并落库"""
    print(f"\n  [Strategy] 运行动态多因子股票策略推荐 ({strategy_id}, 日期: {target_date})...", flush=True)
    try:
        start_date = (pd.to_datetime(target_date) - pd.Timedelta(days=90)).strftime('%Y-%m-%d')
        limit_csi300 = (config and config.get('stock_scope') == 'csi300')
        stock_data = load_all_data_db(start_date=start_date, end_date=target_date, limit_to_csi300=limit_csi300)

        if not stock_data:
            print(f"  [Strategy] ⚠️ {target_date} 缺少股票数据，跳过多因子策略信号计算。")
            return False

        strategy = StrategyRegistry.get('dynamic_factor', config=config)
        buy_signals, sell_signals = strategy.generate_signals(
            date=pd.to_datetime(target_date),
            data=stock_data,
            exclude_codes=[]
        )

        save_recommendations(strategy_id, buy_signals, sell_signals, target_date)
        update_strategy_checkpoint(strategy_id, target_date)
        return True

    except Exception as e:
        print(f"  [Strategy ERROR] ❌ 动态多因子策略计算失败: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return False


def run_all_strategy_recommendations(target_date: Optional[str] = None):
    """
    运行所有激活策略并存储盘后推荐结果
    """
    if target_date is None:
        target_date = get_target_date()

    print("=" * 78)
    print(f"  🚀 开始生成盘后策略推荐信号 (行情目标日: {target_date})")
    print(f"  当前时间: {get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 78)

    # 1. 运行可转债双低策略
    run_cb_double_low_recommendation(target_date)

    # 2. 运行动态多因子策略
    run_dynamic_factor_recommendation(target_date)

    print("\n" + "=" * 78)
    print(f"  ✅ 盘后策略推荐信号生成完毕！(目标日: {target_date})")
    print("=" * 78)


if __name__ == '__main__':
    target_dt = sys.argv[1] if len(sys.argv) > 1 else None
    run_all_strategy_recommendations(target_dt)
