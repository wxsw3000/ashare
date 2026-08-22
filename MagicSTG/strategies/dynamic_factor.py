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

        self.buy_di_threshold = float(self.config.get('buy_di_threshold', self.tech_config.get('buy_di_threshold', 0.70)))
        self.sell_di_threshold = float(self.config.get('sell_di_threshold', self.tech_config.get('sell_di_threshold', 0.70)))
        self.short_ma = int(self.config.get('short_ma', self.tech_config.get('short_ma', 5)))
        self.long_ma = int(self.config.get('long_ma', self.tech_config.get('long_ma', 20)))
        self.volume_surge_factor = float(self.config.get('volume_surge_factor', self.tech_config.get('volume_surge_factor', 1.2)))

        self.enable_golden_cross = self.config.get('enable_golden_cross', self.tech_config.get('enable_golden_cross', False))
        self.enable_volume_surge = self.config.get('enable_volume_surge', self.tech_config.get('enable_volume_surge', False))
        self.enable_di_ratio = self.config.get('enable_di_ratio', self.tech_config.get('enable_di_ratio', False))

        # 新增扩展技术指标: RSI, MACD, BOLL, KDJ, Turn (换手率)
        self.enable_turnover = self.config.get('enable_turnover', self.tech_config.get('enable_turnover', False))
        self.turnover_min = float(self.config.get('min_turnover', self.tech_config.get('min_turnover', 1.0))) # 换手率 %
        self.turnover_max = float(self.config.get('max_turnover', self.tech_config.get('max_turnover', 15.0)))

        self.enable_rsi = self.config.get('enable_rsi', self.tech_config.get('enable_rsi', False))
        self.rsi_period = int(self.config.get('rsi_period', self.tech_config.get('rsi_period', 14)))
        self.rsi_max = float(self.config.get('rsi_max', self.tech_config.get('rsi_max', 70.0)))
        self.rsi_min = float(self.config.get('rsi_min', self.tech_config.get('rsi_min', 30.0)))

        self.enable_macd = self.config.get('enable_macd', self.tech_config.get('enable_macd', False))
        self.enable_boll = self.config.get('enable_boll', self.tech_config.get('enable_boll', False))
        self.enable_kdj = self.config.get('enable_kdj', self.tech_config.get('enable_kdj', False))

        self.fin_config = self.config.get('financial', {})
        self.enable_roe = ('min_roe' in self.config) or self.fin_config.get('enable_roe', False)
        self.roe_min = float(self.config.get('min_roe', self.fin_config.get('roe_min', 0.05)))

        self.enable_pe = ('pe_range' in self.config) or self.fin_config.get('enable_pe', False)
        pe_range = self.config.get('pe_range', [self.fin_config.get('pe_min', 0), self.fin_config.get('pe_max', 35)])
        if isinstance(pe_range, (list, tuple)) and len(pe_range) >= 2:
            self.pe_min = float(pe_range[0])
            self.pe_max = float(pe_range[1])
        else:
            self.pe_min = 0.0
            self.pe_max = 35.0

        # 新增估值因子: PB (市净率)
        self.enable_pb = ('pb_range' in self.config) or self.fin_config.get('enable_pb', False)
        pb_range = self.config.get('pb_range', [self.fin_config.get('pb_min', 0.5), self.fin_config.get('pb_max', 10.0)])
        if isinstance(pb_range, (list, tuple)) and len(pb_range) >= 2:
            self.pb_min = float(pb_range[0])
            self.pb_max = float(pb_range[1])
        else:
            self.pb_min = 0.5
            self.pb_max = 10.0

        self.enable_growth = ('min_net_profit_yoy' in self.config) or self.fin_config.get('enable_growth', False)
        self.growth_min = float(self.config.get('min_net_profit_yoy', self.fin_config.get('growth_min', 0.1)))

        self.enable_debt_limit = ('max_debt_ratio' in self.config) or self.fin_config.get('enable_debt_limit', False)
        self.debt_max = float(self.config.get('max_debt_ratio', self.fin_config.get('debt_max', 0.7)))

        self.enable_cash_quality = ('min_cash_ratio' in self.config) or self.fin_config.get('enable_cash_quality', False)
        self.cfo_np_min = float(self.config.get('min_cash_ratio', self.fin_config.get('cfo_np_min', 0.8)))

        self.market_config = self.config.get('market', {})
        self.enable_market_trend = self.config.get('enable_market_trend', self.market_config.get('enable_market_trend', False))
        self.index_code = self.market_config.get('index_code', 'sh.000300')
        self.index_ma_period = int(self.market_config.get('index_ma', 250))

        self.ranking_config = self.config.get('ranking', {})
        sort_by = self.config.get('sort_by')
        if sort_by:
            if sort_by == 'roe_desc':
                self.primary_factor = 'roe'
                self.reverse = True
            elif sort_by == 'pe_asc':
                self.primary_factor = 'pe'
                self.reverse = False
            elif sort_by == 'pb_asc':
                self.primary_factor = 'pb'
                self.reverse = False
            elif sort_by == 'price_asc':
                self.primary_factor = 'price'
                self.reverse = False
            elif sort_by == 'growth_desc':
                self.primary_factor = 'growth'
                self.reverse = True
            else:
                self.primary_factor = 'price'
                self.reverse = False
        else:
            self.primary_factor = self.ranking_config.get('primary_factor', 'price')
            self.reverse = self.ranking_config.get('reverse', False)

        self._indicator_cache = {}
        self.financial_data = None
        self.index_data = None

    def load_financial_data(self, target_codes: Optional[List[str]] = None) -> Dict[str, Any]:
        """Loads and merges quarterly financial reports from TiDB for target stocks."""
        # 性能与内存优化：若当前策略无需财报因子（如仅为纯股价/均线/PE策略），跳过4张财报大表的跨表查询
        if not (self.enable_roe or self.enable_growth or self.enable_debt_limit or self.enable_cash_quality):
            self.financial_data = {}
            self._financial_data_cached = {}
            return {}

        if hasattr(self, '_financial_data_cached') and self._financial_data_cached:
            if not target_codes or all(c in self._financial_data_cached for c in target_codes):
                return self._financial_data_cached

        from MagicSTG.core.db import safe_read_sql_with_retry, load_all_stock_codes_db
        conn = get_connection()
        try:
            print("  [DynamicStrategy] Loading merged quarterly financial reports in batches...", flush=True)
            codes_to_load = target_codes if target_codes else load_all_stock_codes_db(limit_to_csi300=False)

            batch_size = 400
            all_dfs = []
            for i in range(0, len(codes_to_load), batch_size):
                batch = codes_to_load[i:i+batch_size]
                db_codes = tuple(set([c.replace('.', '_') for c in batch] + [c.replace('_', '.') for c in batch]))
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
                WHERE p.code IN %s
                ORDER BY p.code, p.pub_date ASC
                """
                merged_batch, conn = safe_read_sql_with_retry(query, conn, params=(db_codes,))
                if not merged_batch.empty:
                    all_dfs.append(merged_batch)

            if not all_dfs:
                self.financial_data = {}
                return {}

            merged = pd.concat(all_dfs, ignore_index=True)

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
            self.financial_data = financial_dict
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

    def compute_indicators(self, df: pd.DataFrame, code: str = None) -> pd.DataFrame:
        """Computes technical indicators and pre-aligns financial reports."""
        if 'buy_signal' in df.columns:
            return df

        df_id = id(df)
        if df_id in self._indicator_cache:
            return self._indicator_cache[df_id]

        if self.financial_data is None:
            self.financial_data = self.load_financial_data(target_codes=[code] if code else None)

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

        # 计算 RSI 指标
        delta = df['close'].diff()
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = pd.Series(gain, index=df.index).rolling(self.rsi_period).mean()
        avg_loss = pd.Series(loss, index=df.index).rolling(self.rsi_period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df['rsi'] = 100 - (100 / (1 + rs))

        # 计算 MACD 指标
        ema12 = df['close'].ewm(span=12, adjust=False).mean()
        ema26 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd_dif'] = ema12 - ema26
        df['macd_dea'] = df['macd_dif'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = (df['macd_dif'] - df['macd_dea']) * 2
        df['macd_golden_cross'] = (df['macd_dif'] > df['macd_dea']) & (df['macd_dif'].shift(1) <= df['macd_dea'].shift(1))

        # 计算 BOLL 布林带
        boll_mid = df['close'].rolling(20).mean()
        boll_std = df['close'].rolling(20).std()
        df['boll_upper'] = boll_mid + 2 * boll_std
        df['boll_lower'] = boll_mid - 2 * boll_std

        # 计算 KDJ 指标
        low_min = df['low'].rolling(9).min()
        high_max = df['high'].rolling(9).max()
        rsv = 100 * (df['close'] - low_min) / (high_max - low_min).replace(0, np.nan)
        df['kdj_k'] = rsv.ewm(com=2, adjust=False).mean()
        df['kdj_d'] = df['kdj_k'].ewm(com=2, adjust=False).mean()
        df['kdj_j'] = 3 * df['kdj_k'] - 2 * df['kdj_d']
        df['kdj_golden_cross'] = (df['kdj_k'] > df['kdj_d']) & (df['kdj_k'].shift(1) <= df['kdj_d'].shift(1))

        tech_buy = pd.Series(True, index=df.index)
        if self.enable_golden_cross:
            tech_buy &= df['golden_cross']
        if self.enable_volume_surge:
            tech_buy &= df['volume_surge']
        if self.enable_di_ratio:
            tech_buy &= (df['di_ratio'] >= self.buy_di_threshold)
        if self.enable_turnover and 'turn' in df.columns:
            tech_buy &= (df['turn'] >= self.turnover_min) & (df['turn'] <= self.turnover_max)
        if self.enable_rsi:
            tech_buy &= (df['rsi'] >= self.rsi_min) & (df['rsi'] <= self.rsi_max)
        if self.enable_macd:
            tech_buy &= df['macd_golden_cross']
        if self.enable_boll:
            tech_buy &= (df['close'] > df['boll_upper'])
        if self.enable_kdj:
            tech_buy &= df['kdj_golden_cross']

        roes = np.full(len(df), np.nan, dtype=np.float32)
        growth = np.full(len(df), np.nan, dtype=np.float32)
        debt = np.full(len(df), np.nan, dtype=np.float32)
        cfo = np.full(len(df), np.nan, dtype=np.float32)

        if code and self.financial_data and code in self.financial_data:
            pub_dates, records = self.financial_data[code]
            if len(pub_dates) > 0:
                dt_values = pd.to_datetime(df.index).values
                dt_indices = np.searchsorted(pub_dates, dt_values, side='right') - 1
                for idx_df, idx_rec in enumerate(dt_indices):
                    if idx_rec >= 0:
                        rec = records[idx_rec]
                        roes[idx_df] = rec.get('roe_avg', np.nan)
                        growth[idx_df] = rec.get('YOYNI', np.nan)
                        debt[idx_df] = rec.get('liabilityToAsset', np.nan)
                        cfo[idx_df] = rec.get('CFOToNP', np.nan)

        df['fin_roe_avg'] = roes
        df['fin_YOYNI'] = growth
        df['fin_liabilityToAsset'] = debt
        df['fin_CFOToNP'] = cfo

        buy_cond = tech_buy & (df['close'] > 0)
        if self.enable_roe:
            buy_cond &= (df['fin_roe_avg'] >= self.roe_min)
        if self.enable_pe and 'peTTM' in df.columns:
            valid_pe = df['peTTM'].notna() & (df['peTTM'] > 0)
            pe_cond = (df['peTTM'] >= self.pe_min) & (df['peTTM'] <= self.pe_max)
            buy_cond &= np.where(valid_pe, pe_cond, True)
        if self.enable_pb and 'pbMRQ' in df.columns:
            valid_pb = df['pbMRQ'].notna() & (df['pbMRQ'] > 0)
            pb_cond = (df['pbMRQ'] >= self.pb_min) & (df['pbMRQ'] <= self.pb_max)
            buy_cond &= np.where(valid_pb, pb_cond, True)
        if self.enable_growth:
            buy_cond &= (df['fin_YOYNI'] >= self.growth_min)
        if self.enable_debt_limit:
            buy_cond &= (df['fin_liabilityToAsset'] <= self.debt_max)
        if self.enable_cash_quality:
            buy_cond &= (df['fin_CFOToNP'] >= self.cfo_np_min)

        df['buy_signal'] = buy_cond
        df['sell_signal'] = df['death_cross'] & (df['di_ratio'] < self.sell_di_threshold)

        if self.primary_factor == 'roe':
            df['rank_val'] = df['fin_roe_avg'].fillna(-999.0)
        elif self.primary_factor == 'pe':
            df['rank_val'] = df['peTTM'].fillna(999.0 if self.reverse else -999.0)
        elif self.primary_factor == 'pb':
            df['rank_val'] = df['pbMRQ'].fillna(999.0 if self.reverse else -999.0) if 'pbMRQ' in df.columns else df['close']
        elif self.primary_factor == 'growth':
            df['rank_val'] = df['fin_YOYNI'].fillna(-999.0)
        else:
            df['rank_val'] = df['close']

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

        market_ok = self.check_market_trend(date)

        try:
            for code, df in all_data.items():
                if date not in df.index:
                    continue

                df_with_indicators = self.compute_indicators(df, code=code)
                row = df_with_indicators.loc[date]
                price = row['close']
                if pd.isna(price) or price <= 0:
                    continue

                if row.get('sell_signal', False):
                    sell_signals.append((code, price))

                if not market_ok or code in exclude_codes:
                    continue

                if row.get('buy_signal', False):
                    buy_candidates.append((code, price, row.get('rank_val', price)))

            buy_candidates.sort(key=lambda x: x[2] if not pd.isna(x[2]) else -9999.0, reverse=self.reverse)
            return buy_candidates, sell_signals
        finally:
            self.clear_cache(clear_financial=False)

    def clear_cache(self, clear_financial: bool = True):
        """Clears in-memory indicator and financial caches to free RAM."""
        if hasattr(self, '_indicator_cache') and self._indicator_cache:
            self._indicator_cache.clear()
        if clear_financial:
            self.financial_data = None
            if hasattr(self, '_financial_data_cached'):
                self._financial_data_cached = None
        gc.collect()

    def check_sell_signal(self, df: pd.DataFrame, date: pd.Timestamp) -> bool:
        if date not in df.index:
            return False
        df_with_indicators = self.compute_indicators(df)
        row = df_with_indicators.loc[date]
        return row.get('sell_signal', False)



# Alias class for backward compatibility
SignalGeneratorDynamic = DynamicFactorStrategy
