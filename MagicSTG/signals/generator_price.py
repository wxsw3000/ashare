# ============ 信号生成器 ============
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional


class SignalGenerator:
    """
    完全复刻回测逻辑的信号生成器
    买入: 金叉 + 成交量放大 + PDI >= 0.7 * NDI
    卖出: 死叉 + DI < 0.7
    排序: 价格从低到高（低价优先）
    """
    
    def __init__(self, config: dict):
        self.config = config
        self.buy_di_threshold = config['strategy']['buy_di_threshold']
        self.sell_di_threshold = config['strategy']['sell_di_threshold']
        self.short_ma = config['strategy']['short_ma']
        self.long_ma = config['strategy']['long_ma']
    
    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算所有技术指标"""
        if 'buy_signal' in df.columns:
            return df
        if not hasattr(self, '_indicator_cache'):
            self._indicator_cache = {}
        df_id = id(df)
        if df_id in self._indicator_cache:
            return self._indicator_cache[df_id]
            
        df = df.copy()
        
        # 均线
        df['ma5'] = df['close'].rolling(self.short_ma).mean()
        df['ma20'] = df['close'].rolling(self.long_ma).mean()
        df['vol_ma20'] = df['volume'].rolling(20).mean()
        
        # 金叉/死叉
        df['golden_cross'] = (df['ma5'] > df['ma20']) & (df['ma5'].shift(1) <= df['ma20'].shift(1))
        df['death_cross'] = (df['ma5'] < df['ma20']) & (df['ma5'].shift(1) >= df['ma20'].shift(1))
        
        # 成交量放大
        df['volume_surge'] = df['volume'] > df['vol_ma20'].shift(1) * 1.2
        
        # ATR
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1)))
        )
        df['atr'] = df['tr'].rolling(14).mean()
        
        # PDI / NDI
        df['up'] = df['high'] - df['high'].shift(1)
        df['down'] = df['low'].shift(1) - df['low']
        df['pdm'] = np.where((df['up'] > df['down']) & (df['up'] > 0), df['up'], 0)
        df['ndm'] = np.where((df['down'] > df['up']) & (df['down'] > 0), df['down'], 0)
        atr_s = df['atr'].replace(0, np.nan)
        df['pdi'] = 100 * (df['pdm'].rolling(14).mean() / atr_s)
        df['ndi'] = 100 * (df['ndm'].rolling(14).mean() / atr_s)
        df['di_ratio'] = df['pdi'] / df['ndi']
        
        # 买入/卖出信号
        df['buy_signal'] = df['golden_cross'] & df['volume_surge'] & (df['di_ratio'] >= self.buy_di_threshold)
        df['sell_signal'] = df['death_cross'] & (df['di_ratio'] < self.sell_di_threshold)
        
        self._indicator_cache[df_id] = df
        return df
    
    def get_signals(
        self, 
        all_data: Dict[str, pd.DataFrame], 
        date: pd.Timestamp,
        exclude_codes: List[str] = None
    ) -> Tuple[List[Tuple[str, float]], List[Tuple[str, float]]]:
        """获取指定日期的买入和卖出信号（自动计算指标）"""
        if exclude_codes is None:
            exclude_codes = []
        
        buy_candidates = []
        sell_signals = []
        
        for code, df in all_data.items():
            if date not in df.index:
                continue
            if code in exclude_codes:
                continue
            
            # ⭐ 关键：每次调用时计算指标
            df_with_indicators = self.compute_indicators(df)
            
            row = df_with_indicators.loc[date]
            price = row['close']
            
            if pd.isna(price) or price <= 0:
                continue
            
            if row.get('buy_signal', False):
                buy_candidates.append((code, price))
            
            if row.get('sell_signal', False):
                sell_signals.append((code, price))
        
        buy_candidates.sort(key=lambda x: x[1])
        return buy_candidates, sell_signals
    
    def check_sell_signal(self, df: pd.DataFrame, date: pd.Timestamp) -> bool:
        """检查某只股票在指定日期是否触发卖出信号"""
        if date not in df.index:
            return False
        df_with_indicators = self.compute_indicators(df)
        row = df_with_indicators.loc[date]
        return row.get('sell_signal', False)
    
    def get_current_price(self, df: pd.DataFrame, date: pd.Timestamp) -> Optional[float]:
        """获取指定日期的最新价格"""
        if date not in df.index:
            return None
        price = df.loc[date, 'close']
        if pd.isna(price) or price <= 0:
            return None
        return price