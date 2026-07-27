# -*- coding: utf-8 -*-
"""
MagicSTG Core Price Limit Detection
Detects limit-up and limit-down status for stock prices.
"""

import pandas as pd


def check_limit_up(df: pd.DataFrame, date, price: float) -> bool:
    """Checks if price reached upper limit (+10% / +9.8%)."""
    if date not in df.index:
        return False
    idx = df.index.get_loc(date)
    if idx == 0:
        return False
    prev_close = df['close'].iloc[idx - 1]
    return price >= prev_close * 1.098


def check_limit_down(df: pd.DataFrame, date, price: float) -> bool:
    """Checks if price reached lower limit (-10% / -9.8%)."""
    if date not in df.index:
        return False
    idx = df.index.get_loc(date)
    if idx == 0:
        return False
    prev_close = df['close'].iloc[idx - 1]
    return price <= prev_close * 0.902
