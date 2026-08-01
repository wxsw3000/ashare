# -*- coding: utf-8 -*-
"""
MagicSTG Convertible Bond Double-Low Strategy Implementation
双低策略：推荐/选择双低值(收盘价 + 转股溢价率)最小的优质可转债标的进行组合配置与定期轮动。
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional, Any

from MagicSTG.strategies.base import BaseStrategy


class CBDoubleLowStrategy(BaseStrategy):
    """
    可转债双低策略 (Double-Low Convertible Bond Strategy)

    因子定义：
        双低值 (db_low_value) = 转债收盘价 + 转股溢价率 (%)

    筛选规则：
        1. 转债收盘价 <= max_price (默认 130.0 元，提供债底防守)
        2. 转股溢价率在 [min_convert_premium, max_convert_premium] (默认 [-20, 50] %)
        3. 按双低值升序排列，选取 Top N (默认 10)

    持仓控制：
        定期轮动（如每 rebalance_days 天调整一次）
        单只转债触发卖出条件（如价格突破 sell_threshold_price=140.0 元止盈或不再在 Top N 榜中）
    """

    def __init__(self, name: str = "cb_double_low", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, config)

        self.top_n = int(self.config.get('top_n', 10))
        self.max_price = float(self.config.get('max_price', 130.0))
        self.min_convert_premium = float(self.config.get('min_convert_premium', -20.0))
        self.max_convert_premium = float(self.config.get('max_convert_premium', 50.0))
        self.sell_threshold_price = float(self.config.get('sell_threshold_price', 140.0))
        self.rebalance_days = int(self.config.get('rebalance_days', 5))

    def select_top_bonds(self, cb_df: pd.DataFrame) -> pd.DataFrame:
        """
        根据双低值从给定日期的可转债截面数据中筛选 Top N 标的
        """
        if cb_df is None or cb_df.empty:
            return pd.DataFrame()

        df = cb_df.copy()

        # 保证价格和溢价率存在
        if 'cb_price' not in df.columns or 'convert_premium_rate' not in df.columns:
            return pd.DataFrame()

        # 价格上限筛选
        df = df[df['cb_price'] <= self.max_price]

        # 溢价率范围筛选
        df = df[
            (df['convert_premium_rate'] >= self.min_convert_premium) &
            (df['convert_premium_rate'] <= self.max_convert_premium)
        ]

        if df.empty:
            return pd.DataFrame()

        # 计算双低值（若表中未提前计算）
        if 'db_low_value' not in df.columns or df['db_low_value'].isna().all():
            df['db_low_value'] = df['cb_price'] + df['convert_premium_rate']

        # 按双低值升序排序
        sorted_df = df.sort_values('db_low_value', ascending=True)

        return sorted_df.head(self.top_n)

    def generate_signals(
        self,
        date: pd.Timestamp,
        data: Dict[str, Any],
        current_holdings: Optional[List[str]] = None
    ) -> Tuple[List[Tuple], List[Tuple]]:
        """
        生成买入与卖出信号

        Parameters:
            date: 当前交易日 Timestamp
            data: 数据字典，支持 'cb_daily_indicator' DataFrame 或字典结构
            current_holdings: 当前持仓的转债代码列表

        Returns:
            Tuple[List[Tuple], List[Tuple]]: (buy_signals, sell_signals)
            Tuple 格式: (code, price, reason)
        """
        buy_signals = []
        sell_signals = []
        current_holdings = current_holdings or []

        date_str = date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date)

        # 1. 提取当日截面行情与衍生指标
        today_df = pd.DataFrame()

        if isinstance(data, dict) and 'cb_daily_indicator' in data:
            indicator_df = data['cb_daily_indicator']
            if isinstance(indicator_df, pd.DataFrame) and not indicator_df.empty:
                if 'date' in indicator_df.columns:
                    if not pd.api.types.is_string_dtype(indicator_df['date']):
                        indicator_df['date'] = indicator_df['date'].astype(str)
                    today_df = indicator_df[indicator_df['date'] == date_str]
                else:
                    today_df = indicator_df

        elif isinstance(data, dict):
            rows = []
            for code, df in data.items():
                if isinstance(df, pd.DataFrame) and not df.empty:
                    if hasattr(df.index, 'strftime') and date in df.index:
                        row = df.loc[date].to_dict()
                        row['code'] = code
                        rows.append(row)
                    elif 'date' in df.columns:
                        sub = df[df['date'] == date_str]
                        if not sub.empty:
                            row = sub.iloc[-1].to_dict()
                            row['code'] = code
                            rows.append(row)
            if rows:
                today_df = pd.DataFrame(rows)

        if today_df.empty:
            return buy_signals, sell_signals

        # 2. 选出 Top N 双低优质转债
        top_df = self.select_top_bonds(today_df)
        target_codes = set(top_df['code'].tolist()) if not top_df.empty else set()

        # 3. 检查卖出信号 (针对现有持仓)
        for code in current_holdings:
            code_row = today_df[today_df['code'] == code]
            if code_row.empty:
                sell_signals.append((code, 0.0, "数据缺失调出"))
            else:
                curr_price = float(code_row.iloc[0]['cb_price'])
                if curr_price >= self.sell_threshold_price:
                    sell_signals.append((code, curr_price, f"触发止盈离场({curr_price:.2f} >= {self.sell_threshold_price:.2f})"))
                elif code not in target_codes:
                    sell_signals.append((code, curr_price, "双低排名跌出 Top 榜"))

        # 4. 检查买入信号 (针对入榜的新转债)
        for _, row in top_df.iterrows():
            code = str(row['code'])
            price = float(row['cb_price'])
            db_low = float(row['db_low_value'])
            prem = float(row['convert_premium_rate'])
            if code not in current_holdings:
                reason = f"双低入选(价格:{price:.2f}, 溢价率:{prem:.1f}%, 双低值:{db_low:.2f})"
                buy_signals.append((code, price, reason))

        return buy_signals, sell_signals
