#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
交互式持仓登记与更新脚本
"""

import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import yaml

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from positions.manager import PositionManager
from utils.cost import calc_buy_cost

def load_config():
    config_path = os.path.join(PROJECT_ROOT, 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def show_positions(position_manager: PositionManager):
    print("\n" + "=" * 60)
    print("  📋 当前持仓明细")
    print("=" * 60)
    positions = position_manager.get_positions()
    if positions:
        print(f"{'代码':<12} {'买入日期':<12} {'买入价':<10} {'持股数':<10} {'初始成本':<12}")
        print("-" * 60)
        for p in positions:
            print(f"{p.code:<12} {p.buy_date:<12} {p.buy_price:<10.2f} {p.shares:<10} {p.cost_total:<12.2f}")
    else:
        print("  📭 当前无持仓")
    
    print("-" * 60)
    active_codes = position_manager.get_active_codes()
    max_holdings = position_manager.max_holdings
    per_stock_capital = position_manager.per_stock_capital
    
    print(f"  启用仓位: {len(active_codes)} / {max_holdings} 只")
    print(f"  每只初始资金: {per_stock_capital:,.2f} 元")
    
    # 打印现金详情
    for code in active_codes:
        cash = position_manager.get_cash_remain(code)
        print(f"    - {code} 仓位剩余可用现金: {cash:,.2f} 元")
    
    idle_slots = max_holdings - len(active_codes)
    if idle_slots > 0:
        print(f"    - 闲置仓位 ({idle_slots} 个) 可用资金: {idle_slots * per_stock_capital:,.2f} 元")
        
    total_cash = position_manager.get_cash_remain()
    print(f"  总可用现金: {total_cash:,.2f} 元")
    print("=" * 60 + "\n")

def main():
    print("=" * 60)
    print("  📈 量化交易持仓手工更新系统")
    print("=" * 60)
    
    print("请选择要登记/更新持仓的策略:")
    print("  [1] 价格优先策略 (position_price.csv)")
    print("  [2] PE 优先策略 (position_pe.csv)")
    print("  [3] ROE 优先策略 (position_roe.csv)")
    
    choice = input("请输入选择 (1-3, 默认为1): ").strip()
    suffix = "_price.csv"
    strategy_name = "价格优先策略"
    if choice == '2':
        suffix = "_pe.csv"
        strategy_name = "PE优先策略"
    elif choice == '3':
        suffix = "_roe.csv"
        strategy_name = "ROE优先策略"
        
    config = load_config()
    config['strategy']['name'] = strategy_name
    
    # 强制将 position_file 替换为 csv，确保读写 .csv 格式
    config['paths']['position_file'] = config['paths']['position_file'].replace('.json', '.csv')
    if 'position.csv' in config['paths']['position_file']:
        config['paths']['position_file'] = config['paths']['position_file'].replace('position.csv', f'position{suffix}')
    else:
        # Fallback if config has another name
        base, ext = os.path.splitext(config['paths']['position_file'])
        config['paths']['position_file'] = f"{base}{suffix}"
        
    print(f"\n✅ 已加载 {strategy_name} 配置 (目标文件: {os.path.basename(config['paths']['position_file'])})")
    position_manager = PositionManager(config)
    
    while True:
        show_positions(position_manager)
        
        print("可用操作:")
        print("  [1] 登记买入 (Add Position)")
        print("  [2] 登记卖出 (Remove Position)")
        print("  [3] 刷新显示 (Refresh)")
        print("  [4] 退出系统 (Exit)")
        
        op = input("请输入操作序号 (1-4): ").strip()
        
        if op == '1':
            # 检查仓位是否已满
            if position_manager.get_holding_count() >= position_manager.max_holdings:
                print(f"❌ 错误: 持仓数已达上限 ({position_manager.max_holdings}只)，无法新增持仓。请先登记卖出！")
                input("\n按回车键继续...")
                continue
                
            print("\n--- 登记买入 ---")
            code = input("请输入股票代码 (如 sh.600000 或 sz.000001): ").strip()
            if not code:
                print("❌ 代码不能为空")
                continue
            
            # 代码格式化，确保有.或_
            if '.' not in code and '_' in code:
                code = code.replace('_', '.')
                
            if '.' not in code:
                print("⚠️ 警告: 代码格式建议为 sh.XXXXXX 或 sz.XXXXXX")
            
            # 检查是否已持仓
            if code in position_manager.get_active_codes():
                print(f"❌ 错误: 已持有股票 {code}")
                continue
                
            try:
                price = float(input("请输入买入价格 (元): ").strip())
                shares = int(input("请输入买入股数 (股, 通常为100的倍数): ").strip())
            except ValueError:
                print("❌ 错误: 价格或股数输入无效")
                continue
                
            # 计算成本与费用
            buy_amount = price * shares
            fee = calc_buy_cost(buy_amount)
            total_cost = buy_amount + fee
            
            print(f"  计算所得：买入金额: {buy_amount:.2f} 元, 手续费: {fee:.2f} 元, 总成本: {total_cost:.2f} 元")
            
            per_stock_capital = position_manager.per_stock_capital
            if total_cost > per_stock_capital:
                print(f"⚠️ 警告: 总成本 {total_cost:.2f} 元超过了单只股票分配限额 {per_stock_capital:.2f} 元")
                confirm = input("是否强制登记？(y/n): ").strip().lower()
                if confirm != 'y':
                    continue
                    
            confirm = input(f"确认买入 {code} {shares}股，价格 {price:.2f}？(y/n): ").strip().lower()
            if confirm == 'y':
                if position_manager.add_position(code, price, shares, total_cost):
                    print(f"✅ 成功登记买入 {code}")
                else:
                    print(f"❌ 登记买入失败")
            else:
                print("❌ 操作已取消")
                
        elif op == '2':
            print("\n--- 登记卖出 ---")
            active_codes = position_manager.get_active_codes()
            if not active_codes:
                print("❌ 错误: 当前无持仓，无法登记卖出")
                input("\n按回车键继续...")
                continue
                
            print("当前可卖出股票:")
            for i, c in enumerate(active_codes):
                print(f"  [{i+1}] {c}")
                
            sell_choice = input("请输入股票代码或序号: ").strip()
            if not sell_choice:
                continue
                
            target_code = None
            if sell_choice.isdigit():
                idx = int(sell_choice) - 1
                if 0 <= idx < len(active_codes):
                    target_code = active_codes[idx]
            else:
                # 模糊匹配/直接匹配
                if sell_choice in active_codes:
                    target_code = sell_choice
                elif sell_choice.replace('_', '.') in active_codes:
                    target_code = sell_choice.replace('_', '.')
                    
            if not target_code:
                print("❌ 错误: 输入的股票或序号不在持仓列表中")
                continue
                
            confirm = input(f"确认卖出/平仓 {target_code}？(y/n): ").strip().lower()
            if confirm == 'y':
                removed = position_manager.remove_position(target_code)
                if removed:
                    print(f"✅ 成功登记卖出 {target_code}，释放仓位和资金")
                else:
                    print(f"❌ 登记卖出失败")
            else:
                print("❌ 操作已取消")
                
        elif op == '3':
            continue
            
        elif op == '4':
            print("\n感谢使用持仓更新系统，再见！")
            break
        else:
            print("❌ 错误: 输入的序号无效")
            
        input("\n按回车键继续...")

if __name__ == '__main__':
    main()
