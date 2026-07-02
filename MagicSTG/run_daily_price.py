#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
每日交易决策执行脚本 - 价格优先策略
使用方法: python run_daily_price.py
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
from signals.generator_price import SignalGenerator
from decisions.maker import DecisionMaker
from reports.daily_report import DailyReport


def load_config():
    config_path = os.path.join(PROJECT_ROOT, 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_all_data(data_dir: str):
    from utils.db import load_all_data_db
    return load_all_data_db(limit_days=250)



def get_all_dates(all_data: dict) -> list:
    all_dates = set()
    for code, df in all_data.items():
        all_dates.update(df.index.tolist())
    return sorted(all_dates)


def get_last_check_date():
    checkpoint_file = os.path.join(PROJECT_ROOT, 'last_check_price.json')
    if os.path.exists(checkpoint_file):
        with open(checkpoint_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return pd.Timestamp(data['last_date'])
    return None


def save_checkpoint(date: pd.Timestamp):
    checkpoint_file = os.path.join(PROJECT_ROOT, 'last_check_price.json')
    with open(checkpoint_file, 'w', encoding='utf-8') as f:
        json.dump({'last_date': date.strftime('%Y-%m-%d')}, f)


def main():
    print("=" * 70)
    print("  🚀 价格优先策略 - 每日交易系统")
    print(f"  ⏰ 运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    config = load_config()
    config['strategy']['name'] = "价格优先策略"
    config['paths']['position_file'] = config['paths']['position_file'].replace('position.csv', 'position_price.csv')
    
    print("\n[1] 加载配置...")
    print(f"  ✅ 配置加载完成")
    print(f"    最大持仓: {config['strategy']['max_holdings']} 只")
    print(f"    每只资金: {config['strategy']['per_stock_capital']:.0f} 元")
    
    print("\n[2] 加载数据...")
    data_dir = config['paths']['data_dir']
    all_data = load_all_data(data_dir)
    if len(all_data) == 0:
        print("  ❌ 没有加载到任何数据")
        return
    
    all_dates = get_all_dates(all_data)
    latest_date = all_dates[-1]
    print(f"  ✅ 加载 {len(all_data)} 只股票")
    last_check = get_last_check_date()
    if last_check is None:
        check_dates = all_dates[-10:]  # 首次运行检查最近10个交易日
        print(f"  📌 首次运行，检查最近10个交易日")
    else:
        check_dates = [d for d in all_dates if d > last_check]
        print(f"  📌 上次检查: {last_check.strftime('%Y-%m-%d')}")
    
    print("\n[3] 初始化持仓...")
    position_manager = PositionManager(config)
    
    signal_generator = SignalGenerator(config)
    decision_maker = DecisionMaker(config, position_manager, signal_generator)
    
    buy_candidates_by_date = {}
    sell_signals_by_date = {}
    
    if not check_dates:
        print("  ✅ 没有新日期需要扫描历史信号")
    else:
        print(f"  📌 本次检查: {check_dates[0].strftime('%Y-%m-%d')} ~ {check_dates[-1].strftime('%Y-%m-%d')}")
        print(f"\n[4] 检查 {len(check_dates)} 个交易日的信号...")
        
        # 先获取所有买入候选（不依赖持仓状态）
        buy_candidates_by_date = {}
        sell_signals_by_date = {}
        
        for date in check_dates:
            # 直接调用信号生成器获取原始信号（不经过DecisionMaker过滤）
            buys, sells = signal_generator.get_signals(all_data, date, exclude_codes=[])
            if buys:
                buy_candidates_by_date[date] = buys
            if sells:
                sell_signals_by_date[date] = sells
        
        # 打印结果
        if buy_candidates_by_date:
            print(f"\n  🟢 发现买入信号:")
            for date, buys in buy_candidates_by_date.items():
                print(f"    📅 {date.strftime('%Y-%m-%d')}: {len(buys)} 只")
                for code, price in buys[:5]:  # 只显示前5只
                    print(f"       {code} 价格:{price:.2f}")
                if len(buys) > 5:
                    print(f"       ... 还有 {len(buys)-5} 只")
        else:
            print(f"\n  📭 检查期间没有发现买入信号")
        
        if sell_signals_by_date:
            print(f"\n  🔴 发现卖出信号:")
            for date, sells in sell_signals_by_date.items():
                print(f"    📅 {date.strftime('%Y-%m-%d')}: {len(sells)} 只")
                for code, price in sells[:5]:
                    print(f"       {code} 价格:{price:.2f}")
    
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
    
    # 执行交易决策（若非模拟模式，可选择自动更新持仓）
    decision_maker.execute_decisions(decisions, confirm=not config['mode'].get('simulation', True))
    
    save_checkpoint(latest_date)
    print(f"\n✅ 已保存检查点: {latest_date.strftime('%Y-%m-%d')}")
    
    if not buy_candidates_by_date and not position_manager.get_positions():
        print("\n📭 当前空仓且无买入信号，明日无需操作")
 

if __name__ == "__main__":
    main()
