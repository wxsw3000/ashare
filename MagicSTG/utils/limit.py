# ============ 涨跌停检测 ============
import pandas as pd

def check_limit_up(df: pd.DataFrame, date, price: float) -> bool:
    if date not in df.index:
        return False
    idx = df.index.get_loc(date)
    if idx == 0:
        return False
    prev_close = df['close'].iloc[idx - 1]
    return price >= prev_close * 1.098

def check_limit_down(df: pd.DataFrame, date, price: float) -> bool:
    if date not in df.index:
        return False
    idx = df.index.get_loc(date)
    if idx == 0:
        return False
    prev_close = df['close'].iloc[idx - 1]
    return price <= prev_close * 0.902