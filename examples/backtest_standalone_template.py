# -*- coding: utf-8 -*-
"""
================================================================================
MagicSTG 量化回测数据获取与策略回测模板脚本 (Standalone Backtest Template)
================================================================================

功能说明：
1. 从项目当前的 TiDB Cloud 数据库中高效获取股票日 K 线、季度财务报表数据及大盘指数数据。
2. 财务数据自动包含 `pub_date`（实际披露日期），回测时严格按照 `pub_date <= date` 匹配，
   杜绝未来数据泄露（Future-data Bias）。
3. 提供开箱即用的数据加载器函数与可扩展的自定义策略回测模板框架。

运行方式：
  python examples/backtest_standalone_template.py
"""

import os
import sys
from datetime import datetime
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional, Any

# 自动定位项目根目录并导入 MagicSTG 核心模块
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from MagicSTG.core.db import get_connection, load_all_data_db
from MagicSTG.core.cost import calc_buy_cost, calc_sell_cost
from MagicSTG.core.limit import check_limit_up, check_limit_down


# ==============================================================================
# 模块一：数据库数据加载器 (Data Loaders)
# ==============================================================================

def fetch_stock_klines(
    start_date: str = "2023-01-01",
    end_date: Optional[str] = None,
    limit_to_csi300: bool = False
) -> Dict[str, pd.DataFrame]:
    """
    【数据加载器 1】获取股票日 K 线数据

    参数:
        start_date: 回测起始日期，如 "2023-01-01"（内部会自动拉取前预热期数据）
        end_date:   回测结束日期，如 "2026-06-30"；若为 None 则默认截至最新日期
        limit_to_csi300: 是否仅拉取沪深300成分股（用于快速测试验证）

    返回:
        字典: { "sh.600000": DataFrame(index=date, cols=[open, close, high, low, volume, peTTM]), ... }
    """
    print(f"\n[数据加载] 开始从 TiDB 数据库拉取 K 线数据 ({start_date} ~ {end_date or '最新'})...")
    klines_dict = load_all_data_db(
        start_date=start_date,
        end_date=end_date,
        limit_to_csi300=limit_to_csi300
    )
    print(f"[数据加载] 成功加载 {len(klines_dict)} 只股票的历史日 K 线。")
    return klines_dict


def fetch_financial_reports() -> Dict[str, pd.DataFrame]:
    """
    【数据加载器 2】获取季度财务报表数据（包含实际披露日 pub_date）

    包含指标：
        - roe_avg: 平均 ROE (%)
        - YOYNI:   净利润同比增长率 (%)
        - liabilityToAsset: 资产负债率 (0.0 ~ 1.0)
        - CFOToNP: 经营现金流 / 净利润 (现金流质量)

    返回:
        字典: { "sh.600000": DataFrame(cols=[stat_date, pub_date, roe_avg, YOYNI, liabilityToAsset, CFOToNP]), ... }
    """
    print("\n[数据加载] 开始从 TiDB 数据库拉取季度财务数据...")
    conn = get_connection()
    try:
        query = """
        SELECT 
            p.code, p.stat_date, p.pub_date, p.roe_avg,
            g.YOYNI,
            b.liabilityToAsset,
            c.CFOToNP
        FROM stock_profit_quarterly p
        LEFT JOIN stock_growth_quarterly g ON p.code = g.code AND p.stat_date = g.stat_date
        LEFT JOIN stock_balance_quarterly b ON p.code = b.code AND p.stat_date = b.stat_date
        LEFT JOIN stock_cash_flow_quarterly c ON p.code = c.code AND p.stat_date = c.stat_date
        ORDER BY p.code, p.pub_date ASC
        """
        df = pd.read_sql(query, conn)
        if df.empty:
            print("  [WARN] 财务数据为空。")
            return {}

        df['pub_date'] = pd.to_datetime(df['pub_date'])
        df['stat_date'] = pd.to_datetime(df['stat_date'])
        df['code'] = df['code'].str.replace('_', '.', regex=False)

        financial_dict = {}
        for code, group in df.groupby('code'):
            group = group.sort_values('pub_date')
            financial_dict[code] = group

        print(f"[数据加载] 成功加载 {len(financial_dict)} 只股票的季度财务报表。")
        return financial_dict
    finally:
        conn.close()


def fetch_index_kline(index_code: str = "sh.000300") -> pd.DataFrame:
    """
    【数据加载器 3】获取大盘指数 K 线数据（用于择时或基准对比）

    参数:
        index_code: 指数代码，如 'sh.000300' (沪深300), 'sh.000001' (上证指数)
    """
    print(f"\n[数据加载] 拉取大盘指数 K 线: {index_code}...")
    conn = get_connection()
    try:
        query = f"SELECT date, open, close, high, low, volume FROM index_kline_day WHERE code = '{index_code}' ORDER BY date ASC"
        df = pd.read_sql(query, conn)
        if not df.empty:
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            for col in ['open', 'close', 'high', 'low', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        print(f"[数据加载] 指数 {index_code} 加载完成，包含 {len(df)} 个交易日。")
        return df
    finally:
        conn.close()


# ==============================================================================
# 模块二：自定义因子计算与选股策略模板 (Custom Strategy Template)
# ==============================================================================

class MyCustomStrategy:
    """
    示例策略：多因子选股策略模板
    您可以修改本类中的技术因子与财务过滤逻辑
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {
            'short_ma': 5,             # 短期均线
            'long_ma': 20,             # 长期均线
            'vol_surge_factor': 1.2,   # 量比 threshold
            'min_roe': 5.0,            # 最小 ROE (%)
            'max_pe': 50.0,            # 最大 PE (TTM)
            'min_yoy_growth': 10.0,    # 最小净利润增长率 (%)
            'max_debt_ratio': 0.70     # 最大资产负债率
        }

    def compute_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        技术指标计算（均线、成交量放大、ATR/DMI）
        """
        df = df.copy()
        short_ma = self.config['short_ma']
        long_ma = self.config['long_ma']

        # 1. 均线计算
        df['ma5'] = df['close'].rolling(short_ma).mean()
        df['ma20'] = df['close'].rolling(long_ma).mean()
        df['vol_ma20'] = df['volume'].rolling(20).mean()

        # 2. 金叉/死叉与放量
        df['golden_cross'] = (df['ma5'] > df['ma20']) & (df['ma5'].shift(1) <= df['ma20'].shift(1))
        df['death_cross'] = (df['ma5'] < df['ma20']) & (df['ma5'].shift(1) >= df['ma20'].shift(1))
        df['vol_surge'] = df['volume'] > (df['vol_ma20'].shift(1) * self.config['vol_surge_factor'])

        # 3. 综合买入/卖出信号定义
        df['buy_signal'] = df['golden_cross'] & df['vol_surge']
        df['sell_signal'] = df['death_cross']
        return df

    def filter_by_fundamentals(
        self,
        code: str,
        date: pd.Timestamp,
        fin_dict: Dict[str, pd.DataFrame]
    ) -> Tuple[bool, float]:
        """
        基本面因子过滤（使用 pub_date 对齐，防止未来数据）
        返回: (是否通过过滤, ROE得分用于排序)
        """
        if code not in fin_dict:
            return False, -999.0

        fin_df = fin_dict[code]
        # 严格获取在该交易日以前已经披露的最新财报
        disclosed = fin_df[fin_df['pub_date'] <= date]
        if disclosed.empty:
            return False, -999.0

        latest_fin = disclosed.iloc[-1]

        roe = latest_fin.get('roe_avg', np.nan)
        growth = latest_fin.get('YOYNI', np.nan)
        debt = latest_fin.get('liabilityToAsset', np.nan)

        # 过滤校验
        if pd.isna(roe) or roe < self.config['min_roe']:
            return False, -999.0
        if pd.isna(growth) or growth < self.config['min_yoy_growth']:
            return False, -999.0
        if pd.isna(debt) or debt > self.config['max_debt_ratio']:
            return False, -999.0

        return True, float(roe)


# ==============================================================================
# 模块三：简易矢量/逐日回测引擎 (Backtest Runner Engine)
# ==============================================================================

def run_backtest_simulation(
    klines_dict: Dict[str, pd.DataFrame],
    fin_dict: Dict[str, pd.DataFrame],
    strategy: MyCustomStrategy,
    start_date: str = "2024-01-01",
    end_date: str = "2026-06-30",
    initial_capital: float = 100000.0,
    max_holdings: int = 5
) -> Dict[str, Any]:
    """
    运行逐日回测模拟循环

    规则：
      - 固定槽位资金分摊 (每只股票初始分配 capital / max_holdings)
      - 买入：买入信号 + 基本面过滤 + 涨停不可买
      - 卖出：卖出信号 + 扣除手续费印花税
    """
    print(f"\n[回测引擎] 启动回测 ({start_date} ~ {end_date})...")

    # 1. 预处理所有股票技术指标
    processed_klines = {}
    for code, df in klines_dict.items():
        processed_klines[code] = strategy.compute_technical_indicators(df)

    # 2. 提取交易日序列
    all_dates = sorted(list(set.union(*[set(df.index) for df in processed_klines.values()])))
    backtest_dates = [d for d in all_dates if pd.Timestamp(start_date) <= d <= pd.Timestamp(end_date)]

    if not backtest_dates:
        print("[ERR] 未匹配到回测日期范围。")
        return {}

    per_stock_capital = initial_capital / max_holdings
    slot_cash = [float(per_stock_capital)] * max_holdings
    holdings: Dict[int, Dict[str, Any]] = {}  # { slot_idx: {code, buy_price, shares, buy_date} }
    
    trade_history = []
    equity_curve = []

    for date in backtest_dates:
        # A. 检查当前持仓的卖出信号
        for slot_idx in list(holdings.keys()):
            pos = holdings[slot_idx]
            code = pos['code']
            df = processed_klines[code]

            if date not in df.index:
                continue

            curr_price = df.loc[date, 'close']
            if pd.isna(curr_price) or curr_price <= 0:
                continue

            row = df.loc[date]
            # 卖出逻辑
            if row.get('sell_signal', False):
                sell_amount = pos['shares'] * curr_price
                fee = calc_sell_cost(sell_amount)
                net_revenue = sell_amount - fee
                slot_cash[slot_idx] += net_revenue

                pnl = net_revenue - pos['cost_total']
                pnl_pct = (pnl / pos['cost_total']) * 100

                trade_history.append({
                    'date': date.strftime('%Y-%m-%d'),
                    'code': code,
                    'action': 'SELL',
                    'price': curr_price,
                    'shares': pos['shares'],
                    'pnl': round(pnl, 2),
                    'pnl_pct': round(pnl_pct, 2)
                })
                del holdings[slot_idx]

        # B. 检查买入候选股票
        holding_codes = {p['code'] for p in holdings.values()}
        empty_slots = sorted(list(set(range(max_holdings)) - set(holdings.keys())))

        if empty_slots:
            candidates = []
            for code, df in processed_klines.items():
                if date not in df.index or code in holding_codes:
                    continue

                row = df.loc[date]
                price = row['close']
                if pd.isna(price) or price <= 0:
                    continue

                # 技术买入信号
                if row.get('buy_signal', False):
                    # 基本面过滤
                    passed, score = strategy.filter_by_fundamentals(code, date, fin_dict)
                    if passed:
                        candidates.append((code, price, score))

            # 按 ROE 得分从高到低排序
            candidates.sort(key=lambda x: x[2], reverse=True)

            # 撮合买入
            for slot_idx in empty_slots:
                if not candidates:
                    break

                cand_code, cand_price, _ = candidates.pop(0)

                # 检查涨停
                if check_limit_up(processed_klines[cand_code], date, cand_price):
                    continue

                cash = slot_cash[slot_idx]
                max_shares = int(cash / cand_price / 100) * 100
                if max_shares <= 0:
                    continue

                buy_amount = max_shares * cand_price
                fee = calc_buy_cost(buy_amount)
                total_cost = buy_amount + fee

                if total_cost <= cash:
                    slot_cash[slot_idx] -= total_cost
                    holdings[slot_idx] = {
                        'code': cand_code,
                        'buy_price': cand_price,
                        'shares': max_shares,
                        'cost_total': total_cost,
                        'buy_date': date.strftime('%Y-%m-%d')
                    }
                    trade_history.append({
                        'date': date.strftime('%Y-%m-%d'),
                        'code': cand_code,
                        'action': 'BUY',
                        'price': cand_price,
                        'shares': max_shares,
                        'pnl': 0.0,
                        'pnl_pct': 0.0
                    })

        # C. 记录当日净值
        mkt_val = 0.0
        for pos in holdings.values():
            c = pos['code']
            p = processed_klines[c].loc[date, 'close'] if date in processed_klines[c].index else pos['buy_price']
            mkt_val += pos['shares'] * p

        total_equity = mkt_val + sum(slot_cash)
        equity_curve.append({
            'date': date.strftime('%Y-%m-%d'),
            'equity': round(total_equity, 2)
        })

    # 3. 统计指标计算
    eq_df = pd.DataFrame(equity_curve)
    eq_df['cummax'] = eq_df['equity'].cummax()
    eq_df['drawdown'] = (eq_df['equity'] - eq_df['cummax']) / eq_df['cummax']

    final_equity = eq_df['equity'].iloc[-1]
    total_return = (final_equity - initial_capital) / initial_capital * 100
    max_drawdown = eq_df['drawdown'].min() * 100

    sells = [t for t in trade_history if t['action'] == 'SELL']
    wins = [t for t in sells if t['pnl'] > 0]
    win_rate = (len(wins) / len(sells) * 100) if sells else 0.0



    print("\n" + "=" * 60)
    print("  [RESULT] 回测结果汇总")
    print("=" * 60)
    print(f"  初始资金:     {initial_capital:>12,.2f} 元")
    print(f"  最终资产:     {final_equity:>12,.2f} 元")
    print(f"  累计收益率:   {total_return:>12.2f} %")
    print(f"  最大回撤:     {max_drawdown:>12.2f} %")
    print(f"  胜率:         {win_rate:>12.2f} % ({len(wins)}胜 / {len(sells)}平胜负)")
    print(f"  总交易笔数:   {len(trade_history):>12} 笔")
    print("=" * 60)

    return {
        'equity_curve': eq_df,
        'trade_history': pd.DataFrame(trade_history),
        'metrics': {
            'total_return': total_return,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate
        }
    }


# ==============================================================================
# 模块四：主运行函数入口 (Main Execution)
# ==============================================================================

if __name__ == '__main__':
    print("=" * 70)
    print("  [*] MagicSTG 策略回测数据获取与验证模板")
    print("=" * 70)

    # 1. 从 TiDB 数据库拉取数据 (为了演示速度，可以设置 limit_to_csi300=True 仅加载沪深300成分股)
    klines_data = fetch_stock_klines(
        start_date="2024-01-01",
        end_date="2026-06-30",
        limit_to_csi300=True
    )
    fin_data = fetch_financial_reports()
    index_data = fetch_index_kline("sh.000300")

    # 2. 实例化您的自定义选股策略
    my_strategy = MyCustomStrategy(config={
        'short_ma': 5,
        'long_ma': 20,
        'vol_surge_factor': 1.2,
        'min_roe': 8.0,            # 要求 ROE >= 8%
        'min_yoy_growth': 10.0,    # 要求净利润同比增速 >= 10%
        'max_debt_ratio': 0.65     # 要求资产负债率 <= 65%
    })

    # 3. 运行回测模拟
    results = run_backtest_simulation(
        klines_dict=klines_data,
        fin_dict=fin_data,
        strategy=my_strategy,
        start_date="2024-01-01",
        end_date="2026-06-30",
        initial_capital=100000.0,
        max_holdings=5
    )

    print("\n[SUCCESS] 回测模板脚本运行完成！您可以修改此脚本以测试不同的选股因子。")

