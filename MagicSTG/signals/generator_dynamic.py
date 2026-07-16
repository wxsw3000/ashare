# signals/generator_dynamic.py
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional

class SignalGeneratorDynamic:
    """
    动态信号生成器：支持通过 Web 界面自由组合各种因子和阈值
    """
    
    def __init__(self, config: dict):
        self.config = config
        
        # 1. 技术指标参数
        self.tech_config = config.get('tech', {})
        self.buy_di_threshold = self.tech_config.get('buy_di_threshold', 0.70)
        self.sell_di_threshold = self.tech_config.get('sell_di_threshold', 0.70)
        self.short_ma = self.tech_config.get('short_ma', 5)
        self.long_ma = self.tech_config.get('long_ma', 20)
        self.volume_surge_factor = self.tech_config.get('volume_surge_factor', 1.2)
        
        # 是否启用对应技术因子判断
        self.enable_golden_cross = self.tech_config.get('enable_golden_cross', True)
        self.enable_volume_surge = self.tech_config.get('enable_volume_surge', True)
        self.enable_di_ratio = self.tech_config.get('enable_di_ratio', True)
        
        # 2. 财务指标参数
        self.fin_config = config.get('financial', {})
        self.enable_roe = self.fin_config.get('enable_roe', False)
        self.roe_min = self.fin_config.get('roe_min', 0.05)
        
        self.enable_pe = self.fin_config.get('enable_pe', False)
        self.pe_min = self.fin_config.get('pe_min', 0)
        self.pe_max = self.fin_config.get('pe_max', 35)
        
        self.enable_growth = self.fin_config.get('enable_growth', False)
        self.growth_min = self.fin_config.get('growth_min', 0.1) # 默认 10%
        
        self.enable_debt_limit = self.fin_config.get('enable_debt_limit', False)
        self.debt_max = self.fin_config.get('debt_max', 0.7) # 默认 70%
        
        self.enable_cash_quality = self.fin_config.get('enable_cash_quality', False)
        self.cfo_np_min = self.fin_config.get('cfo_np_min', 0.8) # 默认 0.8
        
        # 3. 大盘过滤参数
        self.market_config = config.get('market', {})
        self.enable_market_trend = self.market_config.get('enable_market_trend', False)
        self.index_code = self.market_config.get('index_code', 'sh.000300')
        self.index_ma_period = self.market_config.get('index_ma', 250)
        
        # 4. 排序参数
        self.ranking_config = config.get('ranking', {})
        self.primary_factor = self.ranking_config.get('primary_factor', 'price') # 'price', 'pe', 'roe', 'growth'
        self.reverse = self.ranking_config.get('reverse', False) # True 代表从高到低，False 代表从低到高
        
        # 缓存
        self._indicator_cache = {}
        self.financial_data = None
        self.index_data = None

    def load_financial_data(self) -> Dict[str, pd.DataFrame]:
        """批量从数据库加载所有财务指标并进行合并缓存"""
        from utils.db import get_connection
        conn = get_connection()
        try:
            print("  [DynamicSignal] Loading financial tables from DB...", flush=True)
            # 1. 盈利能力 (ROE, EPS)
            df_profit = pd.read_sql("SELECT code, pub_date, stat_date, roe_avg, eps_ttm FROM stock_profit_quarterly", conn)
            # 2. 成长能力 (YOYNI)
            df_growth = pd.read_sql("SELECT code, stat_date, YOYNI FROM stock_growth_quarterly", conn)
            # 3. 偿债能力 (liabilityToAsset)
            df_balance = pd.read_sql("SELECT code, stat_date, liabilityToAsset FROM stock_balance_quarterly", conn)
            # 4. 现金流量 (CFOToNP)
            df_cash = pd.read_sql("SELECT code, stat_date, CFOToNP FROM stock_cash_flow_quarterly", conn)
            
            # 类型转换与对齐
            for df in [df_profit, df_growth, df_balance, df_cash]:
                if not df.empty:
                    df['code'] = df['code'].str.strip()
                    df['stat_date'] = pd.to_datetime(df['stat_date'])
            
            df_profit['pub_date'] = pd.to_datetime(df_profit['pub_date'])
            
            # 以 profit 为基准，其余表按 code, stat_date 进行合并
            merged = df_profit
            if not df_growth.empty:
                merged = pd.merge(merged, df_growth, on=['code', 'stat_date'], how='left')
            if not df_balance.empty:
                merged = pd.merge(merged, df_balance, on=['code', 'stat_date'], how='left')
            if not df_cash.empty:
                merged = pd.merge(merged, df_cash, on=['code', 'stat_date'], how='left')
            
            # 排序并清除空发布日期的记录
            merged.dropna(subset=['pub_date', 'stat_date'], inplace=True)
            
            # 按代码存入字典
            financial_dict = {}
            for code, group in merged.groupby('code'):
                group = group.sort_values('pub_date')
                financial_dict[code] = group
                
            print(f"  [DynamicSignal] Successfully loaded financial data for {len(financial_dict)} stocks.", flush=True)
            return financial_dict
        except Exception as e:
            print(f"  [DynamicSignal ERROR] Failed to load financial data: {e}", flush=True)
            return {}
        finally:
            conn.close()

    def load_index_data(self) -> pd.DataFrame:
        """从数据库加载指数数据计算均线趋势"""
        from utils.db import get_connection
        conn = get_connection()
        try:
            print(f"  [DynamicSignal] Loading index {self.index_code} data from DB...", flush=True)
            query = f"SELECT date, close FROM index_kline_day WHERE code = '{self.index_code}' ORDER BY date"
            df = pd.read_sql(query, conn)
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            df['ma'] = df['close'].rolling(self.index_ma_period).mean()
            return df
        except Exception as e:
            print(f"  [DynamicSignal ERROR] Failed to load index data: {e}", flush=True)
            return pd.DataFrame()
        finally:
            conn.close()

    def get_financials_at_date(self, code: str, date: pd.Timestamp) -> Optional[dict]:
        """获取指定股票在指定日期最新发布的财务数据"""
        if self.financial_data is None:
            self.financial_data = self.load_financial_data()
        
        if code not in self.financial_data:
            return None
            
        df = self.financial_data[code]
        available = df[df['pub_date'] <= date]
        if available.empty:
            return None
        return available.iloc[-1].to_dict()

    def check_market_trend(self, date: pd.Timestamp) -> bool:
        """检查大盘趋势是否健康"""
        if not self.enable_market_trend:
            return True
        if self.index_data is None:
            self.index_data = self.load_index_data()
        if self.index_data.empty or date not in self.index_data.index:
            return True # 找不到大盘数据时默认放行
        
        row = self.index_data.loc[date]
        close = row['close']
        ma = row['ma']
        if pd.isna(close) or pd.isna(ma):
            return True
        return close > ma

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算技术指标并缓存"""
        if 'buy_signal' in df.columns:
            return df
            
        df_id = id(df)
        if df_id in self._indicator_cache:
            return self._indicator_cache[df_id]
            
        df = df.copy()
        
        # 均线
        df['ma5'] = df['close'].rolling(self.short_ma).mean()
        df['ma20'] = df['close'].rolling(self.long_ma).mean()
        df['vol_ma20'] = df['volume'].rolling(20).mean()
        
        # 信号基本构成
        df['golden_cross'] = (df['ma5'] > df['ma20']) & (df['ma5'].shift(1) <= df['ma20'].shift(1))
        df['death_cross'] = (df['ma5'] < df['ma20']) & (df['ma5'].shift(1) >= df['ma20'].shift(1))
        df['volume_surge'] = df['volume'] > df['vol_ma20'].shift(1) * self.volume_surge_factor
        
        # DMI/ATR 计算
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
        
        # 买入技术信号过滤
        tech_buy = pd.Series(True, index=df.index)
        if self.enable_golden_cross:
            tech_buy &= df['golden_cross']
        if self.enable_volume_surge:
            tech_buy &= df['volume_surge']
        if self.enable_di_ratio:
            tech_buy &= (df['di_ratio'] >= self.buy_di_threshold)
            
        df['buy_signal'] = tech_buy
        
        # 卖出技术信号（默认采用死金交叉 + DMI 强弱）
        df['sell_signal'] = df['death_cross'] & (df['di_ratio'] < self.sell_di_threshold)
        
        self._indicator_cache[df_id] = df
        return df

    def get_signals(
        self, 
        all_data: Dict[str, pd.DataFrame], 
        date: pd.Timestamp,
        exclude_codes: List[str] = None
    ) -> Tuple[List[Tuple], List[Tuple[str, float]]]:
        """获取指定日期的买入/卖出信号并执行动态因子排序"""
        if exclude_codes is None:
            exclude_codes = []
            
        buy_candidates = []
        sell_signals = []
        
        # 1. 检查大盘过滤
        if not self.check_market_trend(date):
            # 若大盘处于空头，则不产生买入信号，只保留卖出检查
            for code, df in all_data.items():
                if date not in df.index:
                    continue
                df_with_indicators = self.compute_indicators(df)
                row = df_with_indicators.loc[date]
                if row.get('sell_signal', False):
                    sell_signals.append((code, row['close']))
            return [], sell_signals
            
        # 2. 个股循环过滤
        for code, df in all_data.items():
            if date not in df.index:
                continue
            
            df_with_indicators = self.compute_indicators(df)
            row = df_with_indicators.loc[date]
            price = row['close']
            if pd.isna(price) or price <= 0:
                continue
                
            # 卖出判定
            if row.get('sell_signal', False):
                sell_signals.append((code, price))
                
            # 买入判定（剔除已持仓股票）
            if code in exclude_codes:
                continue
                
            if not row.get('buy_signal', False):
                continue
                
            # --- 动态财务指标过滤 ---
            fin = self.get_financials_at_date(code, date)
            
            # ROE 限制
            if self.enable_roe:
                roe = fin.get('roe_avg') if fin else None
                if roe is None or pd.isna(roe) or roe < (self.roe_min * 100): # 数据库里通常存的是百分比数值如 12 代表 12%
                    continue
            
            # PE 限制
            pe = row.get('peTTM')
            if self.enable_pe:
                if pe is None or pd.isna(pe) or pe < self.pe_min or pe > self.pe_max:
                    continue
            
            # 净利润同比增速限制
            if self.enable_growth:
                yoy_ni = fin.get('YOYNI') if fin else None
                if yoy_ni is None or pd.isna(yoy_ni) or yoy_ni < (self.growth_min * 100):
                    continue
            
            # 资产负债率限制
            if self.enable_debt_limit:
                debt = fin.get('liabilityToAsset') if fin else None
                if debt is None or pd.isna(debt) or debt > self.debt_max:
                    continue
            
            # 现金流质量限制
            if self.enable_cash_quality:
                cfo_np = fin.get('CFOToNP') if fin else None
                if cfo_np is None or pd.isna(cfo_np) or cfo_np < self.cfo_np_min:
                    continue
            
            # 计算排序特征值
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
            
        # 3. 动态因子排序
        buy_candidates.sort(key=lambda x: x[2] if not pd.isna(x[2]) else -9999.0, reverse=self.reverse)
        
        return buy_candidates, sell_signals

    def check_sell_signal(self, df: pd.DataFrame, date: pd.Timestamp) -> bool:
        if date not in df.index:
            return False
        df_with_indicators = self.compute_indicators(df)
        row = df_with_indicators.loc[date]
        return row.get('sell_signal', False)
