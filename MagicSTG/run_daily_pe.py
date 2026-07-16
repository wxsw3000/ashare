#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PE优先策略 - 每日交易决策
"""

import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
from datetime import datetime
import yaml
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from positions.manager import PositionManager
from signals.generator_pe import SignalGeneratorPE
from decisions.maker import DecisionMaker
from reports.daily_report import DailyReport


def load_config():
    config_path = os.path.join(PROJECT_ROOT, 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_all_data(data_dir: str):
    from utils.db import load_all_data_db
    return load_all_data_db(limit_days=250)


def get_last_check_date(strategy_name: str):
    from utils.db import get_last_check_date_db
    return get_last_check_date_db(strategy_name)


def save_checkpoint(strategy_name: str, date: pd.Timestamp):
    from utils.db import save_checkpoint_db
    save_checkpoint_db(strategy_name, date)


def main():
    STRATEGY_NAME = "PE"
    print("=" * 70)
    print(f"  🚀 PE优先策略 - 每日交易系统")
    print(f"  ⏰ 运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    config = load_config()
    config['strategy']['name'] = "PE优先策略"
    config['paths']['position_file'] = config['paths']['position_file'].replace('position.csv', 'position_pe.csv')
    
    print("\n[1] 加载配置...")
    print(f"  ✅ 配置加载完成")
    print(f"    最大持仓: {config['strategy']['max_holdings']} 只")
    print(f"    每只资金: {config['strategy']['per_stock_capital']:.0f} 元")
    print(f"    PE范围: {config['strategy'].get('pe_min', 0)} ~ {config['strategy'].get('pe_max', 500)}")

    print("\n[2] 加载数据...")
    data_dir = config['paths']['data_dir']
    all_data = load_all_data(data_dir)
    if len(all_data) == 0:
        print("  ❌ 没有加载到任何数据")
        return

    all_dates = sorted(set().union(*[set(df.index) for df in all_data.values()]))
    latest_date = all_dates[-1]
    print(f"  ✅ 加载 {len(all_data)} 只股票")
    print(f"  📅 最新交易日: {latest_date.strftime('%Y-%m-%d')}")

    last_check = get_last_check_date(STRATEGY_NAME)
    if last_check is None:
        check_dates = all_dates[-5:]
        print(f"  📌 首次运行，检查最近5个交易日")
    else:
        check_dates = [d for d in all_dates if d > last_check]
        print(f"  📌 上次检查: {last_check.strftime('%Y-%m-%d')}")

    print("\n[3] 初始化...")
    position_manager = PositionManager(config)
    signal_generator = SignalGeneratorPE(config)
    decision_maker = DecisionMaker(config, position_manager, signal_generator)

    all_buy_signals = {}
    all_sell_signals = {}

    if not check_dates:
        print("  ✅ 没有新日期需要扫描历史信号")
    else:
        print(f"  📌 本次检查: {check_dates[0].strftime('%Y-%m-%d')} ~ {check_dates[-1].strftime('%Y-%m-%d')}")
        print(f"\n[4] 检查 {len(check_dates)} 个交易日的信号...")

        for date in check_dates:
            # 直接调用信号生成器获取原始信号（不经过DecisionMaker过滤）
            buys, sells = signal_generator.get_signals(all_data, date, exclude_codes=[])
            if buys:
                all_buy_signals[date] = buys
            if sells:
                all_sell_signals[date] = sells

        if all_buy_signals:
            print(f"\n  🟢 PE优先买入信号:")
            for date, buys in all_buy_signals.items():
                print(f"    📅 {date.strftime('%Y-%m-%d')}: {len(buys)} 只")
                for code, price, pe in buys[:5]:
                    print(f"       {code} 价格:{price:.2f} PE:{pe:.2f}")
                if len(buys) > 5:
                    print(f"       ... 还有 {len(buys)-5} 只")
        else:
            print(f"\n  📭 没有发现买入信号")

        if all_sell_signals:
            print(f"\n  🔴 PE优先卖出信号:")
            for date, sells in all_sell_signals.items():
                print(f"    📅 {date.strftime('%Y-%m-%d')}: {len(sells)} 只")
                for code, price in sells[:5]:
                    print(f"       {code} 价格:{price:.2f}")
            if len(sells) > 5:
                print(f"       ... 还有 {len(sells)-5} 只")
        else:
            print(f"\n  📭 没有发现卖出信号")

    # 生成交易决策与持仓报告并执行交易决策
    print(f"\n[5] 生成交易决策与持仓报告...")
    decisions = decision_maker.make_decisions(all_data, latest_date)
    current_prices = {}
    for pos in position_manager.get_positions():
        code = pos.code
        if code in all_data and latest_date in all_data[code].index:
            current_prices[code] = all_data[code].loc[latest_date, 'close']
    
    report = DailyReport(config, position_manager)
    report_text = report.generate(latest_date, decisions, current_prices)
    print(report_text)
    
    # 执行交易决策
    decision_maker.execute_decisions(decisions, confirm=not config['mode'].get('simulation', True))

    # ========== 新增：保存数据到数据库 ==========
    print("\n[6] 保存数据到数据库...")
    try:
        from utils.db_writer import save_recommendations, save_positions
        
        # 收集买入信号
        buy_signals = []
        for date, buys in all_buy_signals.items():
            for item in buys:
                # 格式: (code, price, pe)
                if len(item) >= 2:
                    buy_signals.append(item)
        
        # 收集卖出信号
        sell_signals = []
        for date, sells in all_sell_signals.items():
            for item in sells:
                if len(item) >= 2:
                    sell_signals.append(item)
        
        # 保存推荐信号
        if buy_signals or sell_signals:
            save_recommendations('pe', buy_signals, sell_signals, latest_date)
        else:
            print(f"  [DB] 📭 没有新的推荐信号需要保存")
        
        # 保存持仓
        positions = position_manager.get_positions()
        if positions:
            save_positions('pe', positions)
        else:
            print(f"  [DB] 📭 当前策略无持仓")
            
    except ImportError as e:
        print(f"  [DB] ⚠️ db_writer 模块未找到: {e}")
        print(f"  [DB] 数据未写入数据库，请检查 utils/db_writer.py 是否存在")
    except Exception as e:
        print(f"  [DB] ❌ 保存数据到数据库失败: {e}")
    # ========== 数据库保存结束 ==========

    save_checkpoint(STRATEGY_NAME, latest_date)
    print(f"\n✅ 已保存检查点: {latest_date.strftime('%Y-%m-%d')}")


if __name__ == "__main__":
    main()