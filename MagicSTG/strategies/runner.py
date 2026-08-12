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
    ensure_connection_alive,
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
    """运行动态多因子股票策略推荐并落库（分批加载与计算，低内存开销）"""
    import gc
    print(f"\n  [Strategy] 运行动态多因子股票策略推荐 ({strategy_id}, 日期: {target_date})...", flush=True)
    strategy = None
    try:
        start_date = (pd.to_datetime(target_date) - pd.Timedelta(days=90)).strftime('%Y-%m-%d')
        limit_csi300 = (config and config.get('stock_scope') == 'csi300')

        from MagicSTG.core.db import load_all_stock_codes_db
        all_stock_codes = load_all_stock_codes_db(limit_to_csi300=limit_csi300)
        if not all_stock_codes:
            print(f"  [Strategy] ⚠️ {target_date} 查无股票代码表，跳过策略推荐。")
            return False

        strategy = StrategyRegistry.get('dynamic_factor', config=config)

        # 提前一次性预加载财报数据索引（轻量级）
        if hasattr(strategy, 'load_financial_data'):
            strategy.load_financial_data(target_codes=all_stock_codes)

        batch_size = 300
        batches = [all_stock_codes[i:i+batch_size] for i in range(0, len(all_stock_codes), batch_size)]
        print(f"  [Strategy] 启动分批加载与计算: {len(all_stock_codes)} 支股票划分为 {len(batches)} 个批次...", flush=True)

        all_buy_candidates = []
        all_sell_signals = []

        for idx, b_codes in enumerate(batches):
            b_data = load_all_data_db(start_date=start_date, end_date=target_date, target_codes=b_codes)
            if not b_data:
                continue

            batch_buys, batch_sells = strategy.generate_signals(
                date=pd.to_datetime(target_date),
                data=b_data,
                exclude_codes=[]
            )
            if batch_buys:
                all_buy_candidates.extend(batch_buys)
            if batch_sells:
                all_sell_signals.extend(batch_sells)

            del b_data
            gc.collect()

        # 针对全局批次筛选出的符合条件候选标的，按主因子进行排序并截取 Top N 推荐
        all_buy_candidates.sort(key=lambda x: x[2] if not pd.isna(x[2]) else -9999.0, reverse=strategy.reverse)
        top_n = config.get('top_n', 10) if config else 10
        if top_n and len(all_buy_candidates) > top_n:
            all_buy_candidates = all_buy_candidates[:top_n]

        print(f"  [Strategy] 所有批次计算完成！生成 {len(all_buy_candidates)} 条买入信号, {len(all_sell_signals)} 条卖出信号。", flush=True)
        save_recommendations(strategy_id, all_buy_candidates, all_sell_signals, target_date)
        update_strategy_checkpoint(strategy_id, target_date)
        return True

    except Exception as e:
        print(f"  [Strategy ERROR] ❌ 动态多因子策略计算失败: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return False
    finally:
        if strategy and hasattr(strategy, 'clear_cache'):
            strategy.clear_cache()
        gc.collect()



def run_single_strategy_recommendation(strategy_id: str, target_date: str, force: bool = False) -> bool:
    """运行单个具体策略推荐，并在 update_progress 表记录状态"""
    import json
    print(f"\n[Runner] 启动策略推荐计算: {strategy_id}, 日期: {target_date}, force: {force}", flush=True)
    conn = None
    task_name = f"strategy_{strategy_id}"
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # 更新 update_progress 任务为 running
        cursor.execute("""
            INSERT INTO update_progress (task_date, script_name, status, started_at, error_msg)
            VALUES (%s, %s, 'running', NOW(), NULL)
            ON DUPLICATE KEY UPDATE status = 'running', started_at = NOW(), error_msg = NULL
        """, (target_date, task_name))
        conn.commit()

        # 读取策略配置
        stg_code = strategy_id
        parsed_cfg = {}
        category = 'stock'
        
        cursor.execute("SELECT strategy_id, name, category, factors_config FROM custom_strategies WHERE strategy_id = %s OR id = %s", (strategy_id, int(strategy_id) if strategy_id.isdigit() else -1))
        row = cursor.fetchone()

        if row:
            stg_code, stg_name, category, factors_cfg = row[0], row[1], row[2], row[3]
            parsed_cfg = json.loads(factors_cfg) if isinstance(factors_cfg, str) else (factors_cfg or {})
        elif strategy_id == 'cb_double_low':
            category = 'convertible_bond'
        else:
            category = 'stock'

        success = False
        if category == 'convertible_bond' or stg_code == 'cb_double_low':
            success = run_cb_double_low_recommendation(target_date, strategy_id=stg_code, config=parsed_cfg)
        else:
            success = run_dynamic_factor_recommendation(target_date, strategy_id=stg_code, config=parsed_cfg)

        if success:
            try:
                from MagicSTG.execution.portfolio_engine import PortfolioEngine
                engine = PortfolioEngine(stg_code, config={'strategy': parsed_cfg})
                engine.sync_portfolio_history()
            except Exception as pe_err:
                print(f"  [Portfolio ⚠️] 策略 {stg_code} 模拟持仓更新失败: {pe_err}", flush=True)

        conn = ensure_connection_alive(conn)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE update_progress
            SET status = %s, completed_at = NOW()
            WHERE task_date = %s AND script_name = %s
        """, ('success' if success else 'failed', target_date, task_name))
        conn.commit()
        return success

    except Exception as e:
        print(f"[Runner ERROR] ❌ 策略 {strategy_id} 执行失败: {e}", flush=True)
        if conn:
            try:
                conn = ensure_connection_alive(conn)
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE update_progress
                    SET status = 'failed', completed_at = NOW(), error_msg = %s
                    WHERE task_date = %s AND script_name = %s
                """, (str(e), target_date, task_name))
                conn.commit()
            except Exception:
                pass
        return False
    finally:
        if conn:
            conn.close()


from MagicSTG.core.email_notifier import send_daily_summary_email


def run_all_strategy_recommendations(target_date: Optional[str] = None):
    """
    运行所有 is_active = 1 的工作流策略并存储盘后推荐结果，最后发送邮件日报
    """
    if target_date is None:
        target_date = get_target_date()

    print("=" * 78)
    print(f"  🚀 开始生成盘后工作流激活策略推荐信号 (行情目标日: {target_date})")
    print(f"  当前时间: {get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 78, flush=True)

    # 1. 从 TiDB 查询所有处于 is_active = 1 的工作流激活策略
    active_strategies = []
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT strategy_id, name, category, factors_config
                FROM custom_strategies
                WHERE is_active = 1 OR is_active IS NULL
                ORDER BY id ASC
            """)
            rows = cursor.fetchall()
            import json
            for r in rows:
                cfg = json.loads(r[3]) if isinstance(r[3], str) else (r[3] or {})
                active_strategies.append({
                    'strategy_id': r[0],
                    'name': r[1],
                    'category': r[2],
                    'config': cfg
                })
    except Exception as e:
        print(f"  [Runner ⚠️] 读取工作流激活策略失败: {e}", flush=True)
    finally:
        if conn:
            conn.close()

    if not active_strategies:
        print("  [Runner ℹ️] 尚无在工作流中激活的策略，降级运行默认双低与多因子策略...")
        run_cb_double_low_recommendation(target_date)
        run_dynamic_factor_recommendation(target_date)
    else:
        print(f"  [Runner 📌] 共检测到 {len(active_strategies)} 个激活策略，依次执行选股扫描...", flush=True)
        for stg in active_strategies:
            stg_id = stg['strategy_id']
            category = stg['category']
            cfg = stg['config']
            print(f"\n  ▶ 正在执行激活策略: {stg['name']} ({stg_id})...", flush=True)

            if category == 'convertible_bond' or stg_id == 'cb_double_low':
                run_cb_double_low_recommendation(target_date, strategy_id=stg_id, config=cfg)
            else:
                run_dynamic_factor_recommendation(target_date, strategy_id=stg_id, config=cfg)

            # 同步模拟持仓与收益率
            try:
                from MagicSTG.execution.portfolio_engine import PortfolioEngine
                pe = PortfolioEngine(stg_id, config={'strategy': cfg})
                pe.sync_portfolio_history()
            except Exception as pe_err:
                print(f"  [Portfolio ⚠️] {stg_id} 模拟持仓更新失败: {pe_err}", flush=True)

    print("\n" + "=" * 78)
    print(f"  ✅ 盘后激活策略推荐信号与模拟持仓同步完毕！(目标日: {target_date})")
    print("=" * 78, flush=True)

    # 发送盘后选股推荐日报邮件
    send_daily_summary_email(target_date)



if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="MagicSTG Strategy Recommendation Runner")
    parser.add_argument('--strategy', type=str, default='all', help="Strategy ID (e.g. pe_strategy, cb_double_low, all)")
    parser.add_argument('--date', type=str, default=None, help="Target date YYYY-MM-DD")
    parser.add_argument('--force', action='store_true', help="Force re-run")

    args, unknown = parser.parse_known_args()

    # Position argument backward compatibility
    if not args.date and len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
        args.date = sys.argv[1]

    target_dt = args.date or get_target_date()

    if args.strategy == 'all':
        run_all_strategy_recommendations(target_dt)
    else:
        run_single_strategy_recommendation(args.strategy, target_dt, force=args.force)

