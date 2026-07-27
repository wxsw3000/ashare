# -*- coding: utf-8 -*-
"""
MagicSTG Strategy Backtest Standalone Code Template
Provides standalone data fetching and backtest simulation example for custom strategy development.
"""

import os
import sys
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional, Any

MAGICSTG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(MAGICSTG_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from MagicSTG.core.db import get_connection, load_all_data_db
from MagicSTG.core.cost import calc_buy_cost, calc_sell_cost
from MagicSTG.core.limit import check_limit_up


def fetch_stock_klines(start_date: str = "2024-01-01", end_date: Optional[str] = None, limit_to_csi300: bool = True) -> Dict[str, pd.DataFrame]:
    return load_all_data_db(start_date=start_date, end_date=end_date, limit_to_csi300=limit_to_csi300)


def fetch_financial_reports() -> Dict[str, pd.DataFrame]:
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
        df['pub_date'] = pd.to_datetime(df['pub_date'])
        df['code'] = df['code'].str.replace('_', '.', regex=False)
        financial_dict = {}
        for code, group in df.groupby('code'):
            financial_dict[code] = group.sort_values('pub_date')
        return financial_dict
    finally:
        conn.close()


class CustomStrategy:
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {'short_ma': 5, 'long_ma': 20, 'min_roe': 8.0}

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['ma5'] = df['close'].rolling(self.config['short_ma']).mean()
        df['ma20'] = df['close'].rolling(self.config['long_ma']).mean()
        df['buy_signal'] = (df['ma5'] > df['ma20']) & (df['ma5'].shift(1) <= df['ma20'].shift(1))
        df['sell_signal'] = (df['ma5'] < df['ma20']) & (df['ma5'].shift(1) >= df['ma20'].shift(1))
        return df


if __name__ == '__main__':
    print("[*] MagicSTG Custom Backtest Template Loaded.")
