# ============ 每日报告生成器 ============
import os
from datetime import datetime
from typing import Dict, List
import pandas as pd

from positions.manager import PositionManager
from decisions.maker import TradeDecision


class DailyReport:
    """生成每日交易报告"""
    
    def __init__(self, config: dict, position_manager: PositionManager):
        self.config = config
        self.position_manager = position_manager
        
        report_dir = config['paths']['report_dir']
        if not os.path.isabs(report_dir):
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            report_dir = os.path.join(project_root, report_dir)
        self.report_dir = report_dir
        os.makedirs(self.report_dir, exist_ok=True)
    
    def generate(self, date: pd.Timestamp, decisions: TradeDecision, current_prices: Dict[str, float]) -> str:
        """生成报告（文本格式）"""
        lines = []
        lines.append("=" * 70)
        strategy_name = self.config['strategy'].get('name', '价格优先策略')
        lines.append(f"  📊 {strategy_name} - 每日交易报告")
        lines.append(f"  📅 报告日期: {date.strftime('%Y-%m-%d')}")
        lines.append(f"  ⏰ 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 70)
        
        positions = self.position_manager.get_positions()
        total_equity = self.position_manager.get_total_equity(current_prices)
        total_initial = self.config['strategy']['max_holdings'] * self.config['strategy']['per_stock_capital']
        total_pnl = total_equity - total_initial
        total_pnl_pct = (total_pnl / total_initial) * 100 if total_initial > 0 else 0
        
        lines.append("\n【📈 持仓概况】")
        lines.append(f"  总初始资金: {total_initial:>12,.2f} 元")
        lines.append(f"  当前权益:   {total_equity:>12,.2f} 元")
        lines.append(f"  总盈亏:     {total_pnl:>12,.2f} 元  ({total_pnl_pct:>+6.2f}%)")
        lines.append(f"  持仓数量:   {len(positions):>12} / {self.config['strategy']['max_holdings']} 只")
        
        lines.append("\n【📋 持仓明细】")
        if positions:
            lines.append(f"{'代码':<12} {'买入日期':<12} {'买入价':<10} {'当前价':<10} {'股数':<8} {'市值':<12} {'盈亏':<12} {'盈亏%':<8}")
            lines.append("-" * 80)
            pnl_list = self.position_manager.get_pnl(current_prices)
            for p in pnl_list:
                lines.append(
                    f"{p['code']:<12} {p['buy_date']:<12} {p['buy_price']:<10.2f} {p['current_price']:<10.2f} "
                    f"{p['shares']:<8} {p['market_value']:<12.2f} {p['pnl']:<+12.2f} {p['pnl_pct']:<+8.2f}%"
                )
        else:
            lines.append("  📭 当前无持仓")
        
        lines.append("\n【🔄 今日交易建议】")
        
        if decisions.sells:
            lines.append("\n  🔴 卖出:")
            for sell in decisions.sells:
                lines.append(f"    {sell['code']} 价格:{sell['price']:.2f} 股数:{sell['shares']} 原因:{sell['reason']}")
        else:
            lines.append("\n  ✅ 无卖出信号")
        
        if decisions.buys:
            lines.append("\n  🟢 买入:")
            for buy in decisions.buys:
                lines.append(f"    {buy['code']} 价格:{buy['price']:.2f} 股数:{buy['shares']} 金额:{buy['amount']:.2f}")
        else:
            lines.append("\n  ⏸️ 无买入信号")
        
        if decisions.holds:
            lines.append("\n  ⏸️ 继续持有:")
            for hold in decisions.holds:
                lines.append(f"    {hold['code']} 当前价:{hold['price']:.2f} 盈亏:{hold.get('pnl_pct', 0):.2f}%")
        
        if decisions.missed:
            lines.append("\n  ⚠️ 错失信号（资金不足/涨停）:")
            for miss in decisions.missed:
                lines.append(f"    {miss['code']} 价格:{miss['price']:.2f} 原因:{miss['reason']}")
        
        lines.append("\n" + "=" * 70)
        lines.append("  💡 提示：请根据上述建议手动操作，操作后运行 python update_positions.py 更新持仓")
        lines.append("=" * 70)
        
        report_text = "\n".join(lines)
        
        date_str = date.strftime('%Y%m%d')
        strategy_name = self.config['strategy'].get('name', '价格优先策略')
        if 'PE' in strategy_name or 'pe' in strategy_name:
            prefix = 'pe'
        elif 'ROE' in strategy_name or 'roe' in strategy_name:
            prefix = 'roe'
        else:
            prefix = 'price'
        filename = f"{self.report_dir}/report_{prefix}_{date_str}.txt"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(report_text)
            
        # 更新每日买入和卖出建议记录 CSV
        self.update_suggestions_csv(date, decisions, prefix)
        
        return report_text

    def update_suggestions_csv(self, date: pd.Timestamp, decisions: TradeDecision, prefix: str):
        csv_file = f"{self.report_dir}/suggestions_{prefix}.csv"
        
        # 准备新记录
        new_records = []
        date_str = date.strftime('%Y-%m-%d')
        strategy_name = self.config['strategy'].get('name', '量化策略')
        
        # 收集买入建议
        for buy in decisions.buys:
            new_records.append({
                'date': date_str,
                'strategy': strategy_name,
                'action': 'BUY',
                'code': buy['code'],
                'price': buy['price'],
                'shares': buy['shares'],
                'reason': buy.get('reason', '买入信号')
            })
            
        # 收集卖出建议
        for sell in decisions.sells:
            new_records.append({
                'date': date_str,
                'strategy': strategy_name,
                'action': 'SELL',
                'code': sell['code'],
                'price': sell['price'],
                'shares': sell['shares'],
                'reason': sell.get('reason', '卖出信号')
            })
            
        # 读取或初始化 DataFrame
        if os.path.exists(csv_file):
            try:
                df = pd.read_csv(csv_file, encoding='utf-8-sig')
                # 过滤掉今天已有的记录，实现“每一次运行脚本就更新对应日期的内容”
                df = df[df['date'] != date_str]
            except Exception as e:
                df = pd.DataFrame(columns=['date', 'strategy', 'action', 'code', 'price', 'shares', 'reason'])
        else:
            df = pd.DataFrame(columns=['date', 'strategy', 'action', 'code', 'price', 'shares', 'reason'])
            
        if new_records:
            df_new = pd.DataFrame(new_records)
            if df.empty:
                df = df_new
            else:
                df = pd.concat([df, df_new], ignore_index=True)
            
        # 按日期排序
        if not df.empty:
            df = df.sort_values('date').reset_index(drop=True)
            
        # 保存到 CSV 文件 (采用 utf-8-sig 编码，方便 Windows Excel 直接打开)
        try:
            df.to_csv(csv_file, index=False, encoding='utf-8-sig')
            print(f"  📝 已更新交易建议记录 CSV: {csv_file}")
        except Exception as e:
            print(f"  ⚠️ 更新交易建议记录 CSV 失败: {e}")