# -*- coding: utf-8 -*-
"""
MagicSTG Core Database Infrastructure
Provides database connections, heartbeat ping, data loaders, and strategy checkpoints.
"""

import gc
import time
import pymysql
import numpy as np
import pandas as pd
from typing import Optional, Dict, Any, List, Tuple

from MagicSTG.config import (
    DB_HOST,
    DB_PORT,
    DB_USER,
    DB_PASSWORD,
    DB_NAME,
    get_ssl_ca_path
)


def get_connection() -> pymysql.Connection:
    """
    Establishes and returns a connection to TiDB Cloud with SSL support.
    """
    conn_params = {
        "host": DB_HOST,
        "port": DB_PORT,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "database": DB_NAME,
        "charset": "utf8mb4",
        "autocommit": True,
        "connect_timeout": 15,
        "read_timeout": 90
    }

    ssl_ca = get_ssl_ca_path()
    if ssl_ca:
        conn_params["ssl"] = {"ca": ssl_ca}

    return pymysql.connect(**conn_params)


def get_connection_with_retry(max_retries: int = 3, delay: float = 1.0) -> pymysql.Connection:
    """
    Establishes connection with automatic retry logic for transient network failures.
    """
    for attempt in range(1, max_retries + 1):
        try:
            return get_connection()
        except Exception as e:
            if attempt == max_retries:
                raise
            time.sleep(delay * attempt)


def ensure_connection_alive(conn: pymysql.Connection) -> pymysql.Connection:
    """
    Ensures that the MySQL connection is active. Reconnects if dropped.
    """
    try:
        if conn is None:
            return get_connection()
        conn.ping(reconnect=True)
        return conn
    except Exception:
        return get_connection()


def safe_read_sql_with_retry(query: str, conn, params=None, max_retries: int = 5) -> Tuple[pd.DataFrame, Any]:
    """
    Executes pd.read_sql safely with automatic reconnect and exponential backoff retry.
    Guarantees that no data is silently lost due to transient network glitches.
    Returns (df, updated_connection).
    """
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            conn = ensure_connection_alive(conn)
            batch_df = pd.read_sql(query, conn, params=params)
            return batch_df, conn
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError, Exception) as e:
            last_err = e
            wait_sec = min(1.0 * (2 ** (attempt - 1)), 10.0)
            print(f"  [DB Read ⚠️] SQL query attempt ({attempt}/{max_retries}) failed: {e}. Retrying in {wait_sec:.1f}s...", flush=True)
            time.sleep(wait_sec)
            try:
                conn = get_connection_with_retry()
            except Exception:
                conn = None

    raise RuntimeError(f"Database read query failed after {max_retries} attempts. Aborting to prevent silent stock loss: {last_err}")


def load_all_stock_codes_db(limit_to_csi300: bool = False) -> List[str]:
    """
    Retrieves all distinct stock codes from database.
    """
    conn = get_connection_with_retry()
    try:
        with conn.cursor() as cur:
            if limit_to_csi300:
                cur.execute("SELECT DISTINCT code FROM stock_profit_quarterly ORDER BY code ASC LIMIT 300")
            else:
                cur.execute("SELECT DISTINCT code FROM stock_kline_day ORDER BY code ASC")
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def get_trading_dates_db(start_date_str: str, end_date_str: str) -> List[pd.Timestamp]:
    """
    Retrieves sorted trading dates within specified date range.
    """
    conn = get_connection_with_retry()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT date FROM stock_kline_day WHERE date >= %s AND date <= %s ORDER BY date ASC", (start_date_str, end_date_str))
            return [pd.Timestamp(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()


def load_all_data_db(start_date=None, end_date=None, limit_days=250, limit_to_csi300=False, target_codes=None) -> Dict[str, pd.DataFrame]:
    """
    Load stock K-line daily data from TiDB Cloud database.
    """
    conn = get_connection_with_retry()
    try:
        min_date_str = None
        max_date_str = None

        if start_date is not None:
            start_dt = pd.Timestamp(start_date)
            min_date = start_dt - pd.Timedelta(days=60)
            min_date_str = min_date.strftime('%Y-%m-%d')
            if end_date is not None:
                max_date_str = pd.Timestamp(end_date).strftime('%Y-%m-%d')
        else:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT date FROM stock_kline_day ORDER BY date DESC LIMIT %s", (limit_days,))
                dates = [r[0] for r in cur.fetchall()]
                if dates:
                    min_date_str = min(dates).strftime('%Y-%m-%d')
                    max_date_str = max(dates).strftime('%Y-%m-%d')

        df = None

        # Direct target codes mode (for explicit stock lists)
        # Direct target codes mode (for explicit stock lists)
        if target_codes is not None:
            batch_size = 500
            all_dfs = []
            for i in range(0, len(target_codes), batch_size):
                batch = target_codes[i:i+batch_size]
                format_strings = ','.join(['%s'] * len(batch))
                query = f"""
                SELECT code AS stock_code, date, open, close, high, low, volume, peTTM AS pe_ttm, pbMRQ AS pb_mrq, turn, psTTM AS ps_ttm
                FROM stock_kline_day
                WHERE code IN ({format_strings}) AND date >= %s
                """
                params = batch + [min_date_str]
                if max_date_str is not None:
                    query += " AND date <= %s"
                    params.append(max_date_str)
                query += " ORDER BY code ASC, date ASC"

                batch_df, conn = safe_read_sql_with_retry(query, conn, params=params)
                if not batch_df.empty:
                    all_dfs.append(batch_df)

            df = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()

        elif limit_to_csi300:
            print("  [DB] Retrieving csi300 stock codes from stock_profit_quarterly...", flush=True)
            db_codes = load_all_stock_codes_db(limit_to_csi300=True)
            print(f"  [DB] Target universe limited to {len(db_codes)} stocks. Fetching K-lines in batches...", flush=True)

            batch_size = 100
            all_dfs = []
            for i in range(0, len(db_codes), batch_size):
                batch = db_codes[i:i+batch_size]
                format_strings = ','.join(['%s'] * len(batch))
                query = f"""
                SELECT code AS stock_code, date, open, close, high, low, volume, peTTM AS pe_ttm, pbMRQ AS pb_mrq, turn, psTTM AS ps_ttm
                FROM stock_kline_day
                WHERE code IN ({format_strings}) AND date >= %s
                """
                params = batch + [min_date_str]
                if max_date_str is not None:
                    query += " AND date <= %s"
                    params.append(max_date_str)
                query += " ORDER BY code ASC, date ASC"

                batch_df, conn = safe_read_sql_with_retry(query, conn, params=params)
                if not batch_df.empty:
                    all_dfs.append(batch_df)

            df = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()

        else:
            print(f"  [DB] Fetching all stock daily K-lines for date range: {min_date_str} to {max_date_str} in batches...", flush=True)
            db_codes = load_all_stock_codes_db(limit_to_csi300=False)
            batch_size = 200
            all_dfs = []
            for i in range(0, len(db_codes), batch_size):
                batch = db_codes[i:i+batch_size]
                format_strings = ','.join(['%s'] * len(batch))
                query = f"""
                SELECT code AS stock_code, date, open, close, high, low, volume, peTTM AS pe_ttm, pbMRQ AS pb_mrq, turn, psTTM AS ps_ttm
                FROM stock_kline_day
                WHERE code IN ({format_strings}) AND date >= %s
                """
                params = batch + [min_date_str]
                if max_date_str is not None:
                    query += " AND date <= %s"
                    params.append(max_date_str)
                query += " ORDER BY code ASC, date ASC"

                batch_df, conn = safe_read_sql_with_retry(query, conn, params=params)
                if not batch_df.empty:
                    all_dfs.append(batch_df)

            df = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()


        if df is None or df.empty:
            print("  [WARN] No data returned from database query.", flush=True)
            return {}

        print(f"  [SUCCESS] Loaded {len(df)} rows. Formatting into dict...", flush=True)

        df['code'] = df['stock_code'].str.replace('_', '.', regex=False)
        df['date'] = pd.to_datetime(df['date'])
        df.rename(columns={'pe_ttm': 'peTTM', 'pb_mrq': 'pbMRQ', 'ps_ttm': 'psTTM'}, inplace=True)

        for col in ['open', 'close', 'high', 'low', 'peTTM', 'pbMRQ', 'psTTM', 'turn']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').astype(np.float32)
        if 'volume' in df.columns:
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').astype(np.float32)

        df.sort_values(['code', 'date'], inplace=True)
        df.drop_duplicates(subset=['code', 'date'], keep='last', inplace=True)

        codes = df['code'].values
        dates = df['date'].values
        opens = df['open'].values
        closes = df['close'].values
        highs = df['high'].values
        lows = df['low'].values
        vols = df['volume'].values if 'volume' in df.columns else None
        pes = df['peTTM'].values if 'peTTM' in df.columns else None
        pbs = df['pbMRQ'].values if 'pbMRQ' in df.columns else None
        turns = df['turn'].values if 'turn' in df.columns else None
        pss = df['psTTM'].values if 'psTTM' in df.columns else None

        del df
        if 'all_dfs' in locals():
            del all_dfs
        gc.collect()

        unique_codes, start_indices, counts = np.unique(codes, return_index=True, return_counts=True)
        all_data = {}
        for ucode, start, count in zip(unique_codes, start_indices, counts):
            if count < 20:
                continue
            end = start + count
            sub_dict = {
                'open': opens[start:end],
                'close': closes[start:end],
                'high': highs[start:end],
                'low': lows[start:end]
            }
            if vols is not None:
                sub_dict['volume'] = vols[start:end]
            if pes is not None:
                sub_dict['peTTM'] = pes[start:end]
            if pbs is not None:
                sub_dict['pbMRQ'] = pbs[start:end]
            if turns is not None:
                sub_dict['turn'] = turns[start:end]
            if pss is not None:
                sub_dict['psTTM'] = pss[start:end]

            sub_df = pd.DataFrame(sub_dict, index=pd.Index(dates[start:end], name='date'))
            all_data[ucode] = sub_df

        print(f"  [SUCCESS] Loaded and parsed {len(all_data)} stocks data. Memory garbage collected.", flush=True)
        return all_data

    finally:
        conn.close()


def load_roe_data_db() -> Dict[str, pd.DataFrame]:
    """
    Load ROE history data from TiDB Cloud database.
    """
    conn = get_connection()
    try:
        print("  [DB] Querying ROE history data from stock_profit_quarterly...", flush=True)
        query = """
        SELECT code, stat_date, pub_date, YEAR(stat_date) AS year, QUARTER(stat_date) AS quarter, roe_avg AS roe
        FROM stock_profit_quarterly
        """
        df = pd.read_sql(query, conn)
        if df.empty:
            print("  [WARN] No ROE data returned from database query.", flush=True)
            return {}

        df.rename(columns={
            'code': '代码',
            'stat_date': '统计日期',
            'pub_date': '发布日期',
            'year': '年份',
            'quarter': '季度',
            'roe': 'ROE'
        }, inplace=True)

        df['统计日期'] = pd.to_datetime(df['统计日期'])
        df['发布日期'] = pd.to_datetime(df['发布日期'])
        df['ROE'] = pd.to_numeric(df['ROE'], errors='coerce').astype(np.float32)

        df.dropna(subset=['统计日期', '发布日期', 'ROE'], inplace=True)

        roe_data = {}
        for code, group in df.groupby('代码'):
            group = group.sort_values('统计日期')
            roe_data[code] = group

        del df
        gc.collect()

        print(f"  [SUCCESS] Loaded ROE data for {len(roe_data)} stocks. Memory garbage collected.", flush=True)
        return roe_data

    finally:
        conn.close()


def load_cb_data_db(start_date=None, end_date=None) -> Dict[str, pd.DataFrame]:
    """
    Load convertible bond indicators and master info from TiDB Cloud database.
    """
    conn = get_connection_with_retry()
    try:
        query = """
        SELECT code, date, cb_price, stock_code, stock_price, convert_price, convert_value, convert_premium_rate, db_low_value
        FROM cb_daily_indicator
        """
        params = []
        if start_date is not None or end_date is not None:
            query += " WHERE 1=1"
            if start_date is not None:
                query += " AND date >= %s"
                params.append(start_date)
            if end_date is not None:
                query += " AND date <= %s"
                params.append(end_date)

        query += " ORDER BY date ASC, db_low_value ASC"

        print(f"  [DB] Fetching convertible bond daily indicators ({start_date} ~ {end_date})...", flush=True)
        df_indicators = pd.read_sql(query, conn, params=params if params else None)

        for col in ['cb_price', 'stock_price', 'convert_price', 'convert_value', 'convert_premium_rate', 'db_low_value']:
            if col in df_indicators.columns:
                df_indicators[col] = pd.to_numeric(df_indicators[col], errors='coerce').astype(np.float32)

        query_basic = "SELECT code, name, stock_code, stock_name, convert_price, list_date, rating FROM cb_basic"
        df_basic = pd.read_sql(query_basic, conn)
        if 'convert_price' in df_basic.columns:
            df_basic['convert_price'] = pd.to_numeric(df_basic['convert_price'], errors='coerce').astype(np.float32)

        num_bonds = df_indicators['code'].nunique() if not df_indicators.empty else 0
        gc.collect()
        print(f"  [SUCCESS] Loaded {len(df_indicators)} CB indicator records for {num_bonds} bonds. Memory garbage collected.", flush=True)
        return {
            'cb_daily_indicator': df_indicators,
            'cb_basic': df_basic
        }
    finally:
        conn.close()


def get_last_check_date_db(strategy_name: str) -> Optional[pd.Timestamp]:
    """Reads checkpoint for strategy from database."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT last_check_date FROM strategy_checkpoints WHERE strategy = %s", (strategy_name,))
            row = cur.fetchone()
            if row and row[0]:
                return pd.Timestamp(row[0])
        return None
    except Exception as e:
        print(f"  [DB] ⚠️ Reading checkpoint for {strategy_name} failed: {e}", flush=True)
        return None
    finally:
        conn.close()


def save_checkpoint_db(strategy_name: str, date: pd.Timestamp):
    """Saves checkpoint for strategy to database."""
    conn = get_connection()
    try:
        date_str = date.strftime('%Y-%m-%d')
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO strategy_checkpoints (strategy, last_check_date)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE last_check_date = %s
            """, (strategy_name, date_str, date_str))
        print(f"  [DB] ✅ Saved checkpoint for {strategy_name}: {date_str}", flush=True)
    except Exception as e:
        print(f"  [DB] ❌ Saving checkpoint for {strategy_name} failed: {e}", flush=True)
    finally:
        conn.close()


# Alias for backward compatibility
get_db_connection = get_connection
