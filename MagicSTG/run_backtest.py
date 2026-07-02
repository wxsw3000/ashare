#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
量化策略回测引擎 - 逻辑统一版
调用 decisions.maker.DecisionMaker 保证回测与每日运行逻辑绝对一致
"""

import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import numpy as np
from datetime import datetime
import yaml
import argparse

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.cost import calc_buy_cost, calc_sell_cost
from utils.limit import check_limit_up, check_limit_down
from positions.manager import PositionManager
from signals.generator_price import SignalGenerator
from signals.generator_pe import SignalGeneratorPE
from signals.generator_roe import SignalGeneratorROE
from decisions.maker import DecisionMaker


class InMemoryPositionManager(PositionManager):
    """内存持仓管理器：重写 save 方法以避免在回测时写入硬盘，防文件冲突与卡顿"""
    def save(self):
        pass


def load_config():
    config_path = os.path.join(PROJECT_ROOT, 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_all_data(data_dir: str, start_date=None, end_date=None):
    from utils.db import load_all_data_db
    return load_all_data_db(start_date=start_date, end_date=end_date)


def run_backtest(strategy_type: str, start_date_str: str, end_date_str: str):
    config = load_config()
    
    # 策略基本参数
    max_holdings = config['strategy']['max_holdings']
    per_stock_capital = config['strategy']['per_stock_capital']
    initial_equity = max_holdings * per_stock_capital
    
    start_date = pd.Timestamp(start_date_str)
    end_date = pd.Timestamp(end_date_str)
    
    print("=" * 70)
    print(f"  🎬 启动统一架构历史回测引擎")
    print(f"  📊 策略类型: {strategy_type.upper()}")
    print(f"  📅 回测区间: {start_date_str} ~ {end_date_str}")
    print(f"  💰 初始资金: {initial_equity:,.2f} 元 (分 {max_holdings} 个仓位，每个 {per_stock_capital:,.2f} 元)")
    print("=" * 70)

    # 1. 初始化对应的信号生成器
    if strategy_type == 'pe':
        generator = SignalGeneratorPE(config)
        strategy_name = "PE优先策略"
        config['strategy']['name'] = strategy_name
        config['strategy']['pe_min'] = config['strategy'].get('pe_min', 0)
        config['strategy']['pe_max'] = config['strategy'].get('pe_max', 500)
    elif strategy_type == 'roe':
        generator = SignalGeneratorROE(config)
        strategy_name = "ROE优先策略"
        config['strategy']['name'] = strategy_name
        print("  ⏳ 加载 ROE 报表数据...")
        roe_data = generator.load_roe_data()
        generator.roe_data = roe_data
    else:
        generator = SignalGenerator(config)
        strategy_name = "价格优先策略"
        config['strategy']['name'] = strategy_name
        strategy_type = 'price'

    # 2. 强制指定位置路径为 csv
    config['paths']['position_file'] = config['paths']['position_file'].replace('.json', '.csv')

    # 3. 加载 K 线数据
    print("  ⏳ 加载历史日线数据...")
    all_data = load_all_data(config['paths']['data_dir'], start_date_str, end_date_str)
    if not all_data:
        print("  ❌ 未找到任何股票日线数据，退出")
        return
    print(f"  ✅ 成功加载 {len(all_data)} 只股票的数据")

    # 4. 预计算所有技术指标 (激活 generator 内部的高速 id(df) 缓存机制)
    print("  ⏳ 预计算所有技术指标并缓存...")
    processed_data = {}
    for code, df in all_data.items():
        processed_data[code] = generator.compute_indicators(df)
    print("  ✅ 缓存预热完成！")

    # 5. 获取回测区间内所有有效的交易日期
    all_dates = sorted(set().union(*[set(df.index) for df in all_data.values()]))
    backtest_dates = [d for d in all_dates if start_date <= d <= end_date]
    if not backtest_dates:
        print("  ❌ 指定的时间段内没有可用的交易日数据，退出")
        return
    print(f"  📅 共需回测 {len(backtest_dates)} 个交易日...")

    # 6. 初始化统一业务组件 (InMemoryPositionManager + DecisionMaker)
    position_manager = InMemoryPositionManager(config)
    # 强制清空加载的实盘持仓，使得回测从空仓开始
    position_manager.positions = []
    position_manager.cash_remains = {}
    position_manager.slot_cash = [float(per_stock_capital)] * max_holdings
    position_manager.realized_pnl = 0.0
    
    decision_maker = DecisionMaker(config, position_manager, generator)

    realized_pnl = 0.0     # 已实现盈亏
    trade_log = []         # 交易日志
    equity_history = []    # 资产净值历史
    
    # 7. 时间轴回测循环
    for date in backtest_dates:
        # ----- 7.1 更新持仓中股票的持有期最高价 (用于移动止损判定) -----
        for pos in position_manager.get_positions():
            code = pos.code
            df = processed_data[code]
            if date in df.index:
                high_price = df.loc[date, 'high']
                if not pd.isna(high_price):
                    position_manager.update_highest_price(code, high_price)

        # ----- 7.2 调用共享业务逻辑: 生成交易决策 (与 daily 运行调用的同一函数) -----
        decisions = decision_maker.make_decisions(all_data, date)

        # ----- 7.3 静默执行卖出决策 -----
        for sell in decisions.sells:
            code = sell['code']
            pos = position_manager.get_position(code)
            if not pos:
                continue
                
            sell_amount = sell['shares'] * sell['price']
            fee = calc_sell_cost(sell_amount)
            net_sell = sell_amount - fee
            pnl = net_sell - pos.cost_total
            
            realized_pnl += pnl
            position_manager.remove_position(code, sell['price'], fee)
            
            trade_log.append({
                'date': date.strftime('%Y-%m-%d'),
                'code': code,
                'action': 'SELL',
                'price': sell['price'],
                'shares': sell['shares'],
                'amount': sell_amount,
                'fee': fee,
                'pnl': pnl,
                'pnl_pct': pnl / pos.cost_total * 100,
                'reason': sell['reason']
            })

        # ----- 7.4 静默执行买入决策 -----
        for buy in decisions.buys:
            code = buy['code']
            position_manager.add_position(code, buy['price'], buy['shares'], buy['total'])
            
            trade_log.append({
                'date': date.strftime('%Y-%m-%d'),
                'code': code,
                'action': 'BUY',
                'price': buy['price'],
                'shares': buy['shares'],
                'amount': buy['amount'],
                'fee': buy['fee'],
                'pnl': 0.0,
                'pnl_pct': 0.0,
                'reason': '买入信号'
            })

        # ----- 7.5 估值并记录日终资产净值 -----
        current_prices = {}
        for pos in position_manager.get_positions():
            code = pos.code
            df = processed_data[code]
            if date in df.index:
                current_prices[code] = df.loc[date, 'close']
            else:
                current_prices[code] = pos.buy_price
                
        total_equity = position_manager.get_total_equity(current_prices)
        equity_history.append((date, total_equity))

    # 8. 回测结束，指标计算与统计
    print("  ✅ 回测运行结束！开始统计分析报告...")
    
    total_days = len(backtest_dates)
    final_equity = equity_history[-1][1]
    total_return = (final_equity - initial_equity) / initial_equity * 100
    
    # 年化收益率
    years = total_days / 242.0  # A股交易年
    annual_return = ((final_equity / initial_equity) ** (1.0 / years) - 1) * 100 if years > 0 else 0.0
    
    # 最大回撤
    max_drawdown = 0.0
    peak = initial_equity
    for date, eq in equity_history:
        if eq > peak:
            peak = eq
        dd = (eq - peak) / peak * 100
        if dd < max_drawdown:
            max_drawdown = dd
            
    # 统计交易
    buys_count = sum(1 for t in trade_log if t['action'] == 'BUY')
    completed_trades = [t for t in trade_log if t['action'] == 'SELL']
    win_count = sum(1 for t in completed_trades if t['pnl'] > 0)
    win_rate = win_count / len(completed_trades) * 100 if completed_trades else 0.0
    total_fees = sum(t['fee'] for t in trade_log)
    
    # 9. 拼装报告
    report_lines = []
    report_lines.append("=" * 70)
    report_lines.append(f"  📈 量化回测分析报告 (共享决策模块版) - {strategy_name}")
    report_lines.append(f"  📅 统计区间: {start_date_str} ~ {end_date_str}")
    report_lines.append("=" * 70)
    report_lines.append(f"  1. 初始资金:    {initial_equity:>15,.2f} 元")
    report_lines.append(f"  2. 期末总净值:  {final_equity:>15,.2f} 元")
    report_lines.append(f"  3. 累计收益率:  {total_return:>14.2f}%")
    report_lines.append(f"  4. 年化收益率:  {annual_return:>14.2f}% (基于 A 股交易日)")
    report_lines.append(f"  5. 历史最大回撤:{max_drawdown:>14.2f}%")
    report_lines.append("-" * 70)
    report_lines.append(f"  6. 总计买入笔数:{buys_count:>15} 笔")
    report_lines.append(f"  7. 已平仓笔数:  {len(completed_trades):>15} 笔")
    report_lines.append(f"  8. 交易胜率:    {win_rate:>14.2f}%")
    report_lines.append(f"  9. 累计交易税费:{total_fees:>15,.2f} 元")
    report_lines.append("=" * 70)
    
    report_lines.append("\n📋 历史持仓成交明细 (前 30 笔):")
    report_lines.append(f"{'交易日期':<12} {'代码':<10} {'方向':<6} {'价格':<8} {'股数':<8} {'手续费':<8} {'盈亏额':<10} {'盈亏%':<8} {'触发原因':<15}")
    report_lines.append("-" * 90)
    for t in trade_log[:30]:
        pnl_str = f"{t['pnl']:+.2f}" if t['action'] == 'SELL' else "--"
        pnl_pct_str = f"{t['pnl_pct']:+.2f}%" if t['action'] == 'SELL' else "--"
        report_lines.append(
            f"{t['date']:<12} {t['code']:<10} {t['action']:<6} {t['price']:<8.2f} "
            f"{t['shares']:<8} {t['fee']:<8.2f} {pnl_str:<10} {pnl_pct_str:<8} {t['reason']:<15}"
        )
    if len(trade_log) > 30:
        report_lines.append(f"... 还有 {len(trade_log) - 30} 笔交易记录未完全展示")
        
    report_lines.append("\n" + "=" * 70)
    report_text = "\n".join(report_lines)
    
    print(report_text)
    
    # 10. 保存报告
    os.makedirs(f"{PROJECT_ROOT}/reports", exist_ok=True)
    report_filename = f"{PROJECT_ROOT}/reports/backtest_{strategy_type}_{start_date_str.replace('-','')}_{end_date_str.replace('-','')}.txt"
    with open(report_filename, 'w', encoding='utf-8') as f:
        f.write(report_text)
    print(f"✅ 详细回测日志报告已保存至: {report_filename}")
    
    # 保存交易记录为 CSV 方便对比
    trade_df = pd.DataFrame(trade_log)
    trade_csv_path = f"{PROJECT_ROOT}/reports/backtest_{strategy_type}_trades.csv"
    trade_df.to_csv(trade_csv_path, index=False, encoding='utf-8-sig')
    print(f"📝 交易记录 CSV 已保存至: {trade_csv_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="共享逻辑量化回测脚本")
    parser.add_argument("--strategy", type=str, choices=['price', 'pe', 'roe'], default='price', help="回测的策略名称")
    parser.add_argument("--start", type=str, default="2023-01-01", help="回测开始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=str, default="2025-12-31", help="回测结束日期 YYYY-MM-DD")
    
    args = parser.parse_args()
    run_backtest(args.strategy, args.start, args.end)
