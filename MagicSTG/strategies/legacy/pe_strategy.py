# -*- coding: utf-8 -*-
"""
MagicSTG Legacy PE-First Strategy
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from MagicSTG.strategies.base import BaseStrategy


class SignalGeneratorPE(BaseStrategy):
    """
    PE-first strategy signal generator.
    """

    def __init__(self, config: dict, name: str = "pe"):
        super().__init__(name, config)
        stg = config.get('strategy', {})
        self.buy_di_threshold = stg.get('buy_di_threshold', 0.70)
        self.sell_di_threshold = stg.get('sell_di_threshold', 0.70)
        self.short_ma = stg.get('short_ma', 5)
        self.long_ma = stg.get('long_ma', 20)
        self.pe_min = stg.get('pe_min', 0)
        self.pe_max = stg.get('pe_max', 500)
        self.top_n = stg.get('top_n', 10)

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        if 'buy_signal' in df.columns:
            return df
        if not hasattr(self, '_indicator_cache'):
            self._indicator_cache = {}
        df_id = id(df)
        if df_id in self._indicator_cache:
            return self._indicator_cache[df_id]

        df = df.copy()
        df['ma5'] = df['close'].rolling(self.short_ma).mean()
        df['ma20'] = df['close'].rolling(self.long_ma).mean()
        df['vol_ma20'] = df['volume'].rolling(20).mean()

        df['golden_cross'] = (df['ma5'] > df['ma20']) & (df['ma5'].shift(1) <= df['ma20'].shift(1))
        df['death_cross'] = (df['ma5'] < df['ma20']) & (df['ma5'].shift(1) >= df['ma20'].shift(1))
        df['volume_surge'] = df['volume'] > df['vol_ma20'].shift(1) * 1.2

        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1)))
        )
        df['atr'] = df['tr'].rolling(14).mean()

        df['up'] = df['high'] - df['high'].shift(1)
        df['down'] = df['low'].shift(1) - df['low']
        df['pdm'] = np.where((df['up'] > df['down']) & (df['up'] > 0), df['up'], 0)
        df['ndm'] = np.where((df['down'] > df['up']) & (df['down'] > 0), df['down'], 0)
        atr_s = df['atr'].replace(0, np.nan)
        df['pdi'] = 100 * (df['pdm'].rolling(14).mean() / atr_s)
        df['ndi'] = 100 * (df['ndm'].rolling(14).mean() / atr_s)
        df['di_ratio'] = df['pdi'] / df['ndi']

        df['buy_signal'] = df['golden_cross'] & df['volume_surge'] & (df['di_ratio'] >= self.buy_di_threshold)
        df['sell_signal'] = df['death_cross'] & (df['di_ratio'] < self.sell_di_threshold)

        self._indicator_cache[df_id] = df
        return df

    def generate_signals(self, date: pd.Timestamp, data: Dict[str, pd.DataFrame]) -> Tuple[List[Tuple], List[Tuple]]:
        return self.get_signals(data, date)

    def get_signals(
        self,
        all_data: Dict[str, pd.DataFrame],
        date: pd.Timestamp,
        exclude_codes: List[str] = None
    ) -> Tuple[List[Tuple[str, float, float]], List[Tuple[str, float]]]:
        if exclude_codes is None:
            exclude_codes = []

        buy_candidates = []
        sell_signals = []

        try:
            for code, df in all_data.items():
                if date not in df.index or code in exclude_codes:
                    continue

                df_with_indicators = self.compute_indicators(df)
                row = df_with_indicators.loc[date]
                price = row['close']

                if pd.isna(price) or price <= 0:
                    continue

                pe = row.get('peTTM', np.nan)
                if pd.isna(pe) or pe <= self.pe_min or pe >= self.pe_max:
                    continue

                if row.get('buy_signal', False):
                    buy_candidates.append((code, price, pe))

                if row.get('sell_signal', False):
                    sell_signals.append((code, price))

            # 按 PE 升序排列（最便宜在前），并限制 Top N 精选
            buy_candidates.sort(key=lambda x: x[2], reverse=False)
            return buy_candidates[:self.top_n], sell_signals[:self.top_n]
        finally:
            self.clear_cache()

    def clear_cache(self):
        if hasattr(self, '_indicator_cache') and self._indicator_cache:
            self._indicator_cache.clear()

    def check_sell_signal(self, df: pd.DataFrame, date: pd.Timestamp) -> bool:
        if date not in df.index:
            return False
        df_with_indicators = self.compute_indicators(df)
        row = df_with_indicators.loc[date]
        return row.get('sell_signal', False)
