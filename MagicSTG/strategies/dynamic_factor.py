# -*- coding: utf-8 -*-
"""
MagicSTG Dynamic Multi-Factor Strategy Implementation
Combines technical indicators (MA, Volume Surge, DMI/ATR) and fundamental quarterly reports.
"""

import gc
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional, Any

from MagicSTG.strategies.base import BaseStrategy
from MagicSTG.core.db import get_connection


class DynamicFactorStrategy(BaseStrategy):
    """
    Dynamic Multi-Factor Stock Selection Strategy.
    Allows dynamic composition of technical and fundamental factor thresholds.
    """

    def __init__(self, name: str = "dynamic_factor", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, config)

        self.tech_config = self.config.get('tech', {})
        self.buy_di_threshold = self.tech_config.get('buy_di_threshold', 0.70)
        self.sell_di_threshold = self.tech_config.get('sell_di_threshold', 0.70)
        self.short_ma = self.tech_config.get('short_ma', 5)
        self.long_ma = self.tech_config.get('long_ma', 20)
        self.volume_surge_factor = self.tech_config.get('volume_surge_factor', 1.2)

        self.enable_golden_cross = self.tech_config.get('enable_golden_cross', True)
        self.enable_volume_surge = self.tech_config.get('enable_volume_surge', True)
        self.enable_di_ratio = self.tech_config.get('enable_di_ratio', True)

        self.fin_config = self.config.get('financial', {})
        self.enable_roe = self.fin_config.get('enable_roe', False)
        self.roe_min = self.fin_config.get('roe_min', 0.05)

        self.enable_pe = self.fin_config.get('enable_pe', False)
        self.pe_min = self.fin_config.get('pe_min', 0)
        self.pe_max = self.fin_config.get('pe_max', 35)

        self.enable_growth = self.fin_config.get('enable_growth', False)
        self.growth_min = self.fin_config.get('growth_min', 0.1)

        self.enable_debt_limit = self.fin_config.get('enable_debt_limit', False)
        self.debt_max = self.fin_config.get('debt_max', 0.7)

        self.enable_cash_quality = self.fin_config.get('enable_cash_quality', False)
        self.cfo_np_min = self.fin_config.get('cfo_np_min', 0.8)

        self.market_config = self.config.get('market', {})
        self.enable_market_trend = self.market_config.get('enable_market_trend', False)
        self.index_code = self.market_config.get('index_code', 'sh.000300')
        self.index_ma_period = self.market_config.get('index_ma', 250)

        self.ranking_config = self.config.get('ranking', {})
        self.primary_factor = self.ranking_config.get('primary_factor', 'price')
        self.reverse = self.ranking_config.get('reverse', False)

        self._indicator_cache = {}
        self.financial_data = None
        self.index_data = None

    def load_financial_data(self, target_codes: Optional[List[str]] = None) -> Dict[str, Any]:
        """Loads and merges quarterly financial reports from TiDB for target stocks."""
        if hasattr(self, '_financial_data_cached') and self._financial_data_cached:
            if not target_codes or all(c in self._financial_data_cached for c in target_codes):
                return self._financial_data_cached

        conn = get_connection()
        try:
            print("  [DynamicStrategy] Loading merged quarterly financial reports...", flush=True)
            where_clause = ""
            params = None

            if target_codes and len(target_codes) > 0:
                db_codes = [c.replace('.', '_') for c in target_codes] + [c.replace('_', '.') for c in target_codes]
                unique_codes = list(set(db_codes))
                format_strings = ','.join(['%s'] * len(unique_codes))
                where_clause = f" WHERE p.code IN ({format_strings}) "
                params = unique_codes

            query = f"""
            SELECT 
                p.code, p.stat_date, p.pub_date, p.roe_avg,
                g.YOYNI,
                b.liabilityToAsset,
                c.CFOToNP
            FROM stock_profit_quarterly p
            LEFT JOIN stock_growth_quarterly g ON p.code = g.code AND p.stat_date = g.stat_date
            LEFT JOIN stock_balance_quarterly b ON p.code = b.code AND p.stat_date = b.stat_date
            LEFT JOIN stock_cash_flow_quarterly c ON p.code = c.code AND p.stat_date = c.stat_date
            {where_clause}
            ORDER BY p.code, p.pub_date ASC
            """
            merged = pd.read_sql(query, conn, params=params)
            merged['pub_date'] = pd.to_datetime(merged['pub_date'])
            merged['code'] = merged['code'].str.replace('_', '.', regex=False)

            for col in ['roe_avg', 'YOYNI', 'liabilityToAsset', 'CFOToNP']:
                if col in merged.columns:
                    merged[col] = pd.to_numeric(merged[col], errors='coerce').astype(np.float32)

            financial_dict = getattr(self, '_financial_data_cached', {}) or {}
            for code, group in merged.groupby('code'):
                group = group.sort_values('pub_date')
                pub_dates = group['pub_date'].values
                records = group.to_dict('records')
                financial_dict[code] = (pub_dates, records)

            del merged
            gc.collect()

            print(f"  [DynamicStrategy] Financial data loaded and indexed for {len(financial_dict)} stocks. GC executed.", flush=True)
            self._financial_data_cached = financial_dict
            return financial_dict
        except Exception as e:
            print(f"  [DynamicStrategy ERROR] Failed to load financial data: {e}", flush=True)
            return {}
        finally:
            conn.close()

    def get_financials_at_date(self, code: str, date: pd.Timestamp, target_codes: Optional[List[str]] = None) -> Optional[dict]:
        """Gets latest financial disclosure data prior to date."""
        if self.financial_data is None:
            self.financial_data = self.load_financial_data(target_codes=target_codes)

        if code not in self.financial_data:
            return None

        pub_dates, records = self.financial_data[code]
        dt = np.datetime64(date)
        idx = np.searchsorted(pub_dates, dt, side='right') - 1
        if idx >= 0:
            return records[idx]
        return None

    def check_market_trend(self, date: pd.Timestamp) -> bool:
        """Checks if broader market index trend is positive."""
        if not self.enable_market_trend:
            return True
        if self.index_data is None:
            self.index_data = self.load_index_data()
        if self.index_data.empty or date not in self.index_data.index:
            return True

        row = self.index_data.loc[date]
        close = row['close']
        ma = row['ma']
        if pd.isna(close) or pd.isna(ma):
            return True
        return close > ma

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Computes technical indicators and caches result."""
        if 'buy_signal' in df.columns:
            return df

        df_id = id(df)
        if df_id in self._indicator_cache:
            return self._indicator_cache[df_id]

        df = df.copy()

        df['ma5'] = df['close'].rolling(self.short_ma).mean()
        df['ma20'] = df['close'].rolling(self.long_ma).mean()
        df['vol_ma20'] = df['volume'].rolling(20).mean()

        df['golden_cross'] = (df['ma5'] > df['ma20']) & (df['ma5'].shift(1) <= df['ma20'].shift(1))
        df['death_cross'] = (df['ma5'] < df['ma20']) & (df['ma5'].shift(1) >= df['ma20'].shift(1))
        df['volume_surge'] = df['volume'] > df['vol_ma20'].shift(1) * self.volume_surge_factor

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

        tech_buy = pd.Series(True, index=df.index)
        if self.enable_golden_cross:
            tech_buy &= df['golden_cross']
        if self.enable_volume_surge:
            tech_buy &= df['volume_surge']
        if self.enable_di_ratio:
            tech_buy &= (df['di_ratio'] >= self.buy_di_threshold)

        df['buy_signal'] = tech_buy
        df['sell_signal'] = df['death_cross'] & (df['di_ratio'] < self.sell_di_threshold)

        self._indicator_cache[df_id] = df
        return df

    def generate_signals(self, date: pd.Timestamp, data: Dict[str, pd.DataFrame], exclude_codes: List[str] = None) -> Tuple[List[Tuple], List[Tuple]]:
        """Implementation of BaseStrategy.generate_signals."""
        return self.get_signals(data, date, exclude_codes)

    def get_signals(
        self,
        all_data: Dict[str, pd.DataFrame],
        date: pd.Timestamp,
        exclude_codes: List[str] = None
    ) -> Tuple[List[Tuple], List[Tuple[str, float]]]:
        if exclude_codes is None:
            exclude_codes = []

        buy_candidates = []
        sell_signals = []

        if self.financial_data is None:
            self.financial_data = self.load_financial_data(target_codes=list(all_data.keys()))

        if not self.check_market_trend(date):
            for code, df in all_data.items():
                if date not in df.index:
                    continue
                df_with_indicators = self.compute_indicators(df)
                row = df_with_indicators.loc[date]
                if row.get('sell_signal', False):
                    sell_signals.append((code, row['close']))
            return [], sell_signals

        for code, df in all_data.items():
            if date not in df.index:
                continue

            df_with_indicators = self.compute_indicators(df)
            row = df_with_indicators.loc[date]
            price = row['close']
            if pd.isna(price) or price <= 0:
                continue

            if row.get('sell_signal', False):
                sell_signals.append((code, price))

            if code in exclude_codes:
                continue

            if not row.get('buy_signal', False):
                continue

            fin = self.get_financials_at_date(code, date)

            if self.enable_roe:
                roe = fin.get('roe_avg') if fin else None
                if roe is None or pd.isna(roe) or roe < (self.roe_min * 100):
                    continue

            pe = row.get('peTTM')
            if self.enable_pe:
                if pe is None or pd.isna(pe) or pe < self.pe_min or pe > self.pe_max:
                    continue

            if self.enable_growth:
                yoy_ni = fin.get('YOYNI') if fin else None
                if yoy_ni is None or pd.isna(yoy_ni) or yoy_ni < (self.growth_min * 100):
                    continue

            if self.enable_debt_limit:
                debt = fin.get('liabilityToAsset') if fin else None
                if debt is None or pd.isna(debt) or debt > self.debt_max:
                    continue

            if self.enable_cash_quality:
                cfo_np = fin.get('CFOToNP') if fin else None
                if cfo_np is None or pd.isna(cfo_np) or cfo_np < self.cfo_np_min:
                    continue

            rank_val = 0.0
            if self.primary_factor == 'price':
                rank_val = price
            elif self.primary_factor == 'roe':
                rank_val = fin.get('roe_avg', -999.0) if fin else -999.0
            elif self.primary_factor == 'pe':
                rank_val = pe if pe is not None and not pd.isna(pe) else (999.0 if self.reverse else -999.0)
            elif self.primary_factor == 'growth':
                rank_val = fin.get('YOYNI', -999.0) if fin else -999.0

            buy_candidates.append((code, price, rank_val))

        buy_candidates.sort(key=lambda x: x[2] if not pd.isna(x[2]) else -9999.0, reverse=self.reverse)

        return buy_candidates, sell_signals

    def check_sell_signal(self, df: pd.DataFrame, date: pd.Timestamp) -> bool:
        if date not in df.index:
            return False
        df_with_indicators = self.compute_indicators(df)
        row = df_with_indicators.loc[date]
        return row.get('sell_signal', False)


# Alias class for backward compatibility
SignalGeneratorDynamic = DynamicFactorStrategy
