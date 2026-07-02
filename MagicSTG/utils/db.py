import os
import time
import pymysql
import pandas as pd
from dotenv import load_dotenv

# Get project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(PROJECT_ROOT, 'dbconfig', '.env')

def get_connection():
    load_dotenv(ENV_PATH)
    DB_HOST = os.getenv("DB_HOST")
    DB_PORT = int(os.getenv("DB_PORT", 4000))
    DB_USER = os.getenv("DB_USER")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DB_NAME = os.getenv("DB_NAME")
    DB_SSL_CA = os.getenv("DB_SSL_CA")
    
    conn_params = {
        "host": DB_HOST,
        "port": DB_PORT,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "database": DB_NAME,
        "charset": "utf8mb4",
        "autocommit": True,
        "connect_timeout": 15,
        "read_timeout": 90       # Prevent infinite hanging if TCP drops connection silently
    }
    if DB_SSL_CA:
        resolved_ssl_ca = DB_SSL_CA
        if not os.path.exists(resolved_ssl_ca):
            # Try relative paths
            filename = os.path.basename(DB_SSL_CA)
            for path_candidate in [
                os.path.join(PROJECT_ROOT, 'dbconfig', filename),
                os.path.join(PROJECT_ROOT, filename),
                os.path.join(PROJECT_ROOT, DB_SSL_CA)
            ]:
                if os.path.exists(path_candidate):
                    resolved_ssl_ca = path_candidate
                    break
        if os.path.exists(resolved_ssl_ca):
            conn_params["ssl"] = {"ca": resolved_ssl_ca}
        else:
            print(f"  [WARN] SSL CA path '{DB_SSL_CA}' does not exist and could not be resolved relative to project root.", flush=True)
        
    return pymysql.connect(**conn_params)

def load_all_data_db(start_date=None, end_date=None, limit_days=250, limit_to_csi300=False):
    """
    Load stock K-line data from TiDB Cloud database.
    
    Parameters:
    - start_date, end_date: Range for backtesting (warmup is automatically handled).
    - limit_days: Number of recent trading days to fetch for daily screening.
    - limit_to_csi300: If True, restricts the stock universe to the CSI 300 stocks
                       present in the roe_history table (289 stocks).
                       If False (default), loads all 5135 stocks.
    """
    conn = get_connection()
    try:
        # Determine the date range parameters
        min_date_str = None
        max_date_str = None
        
        if start_date is not None:
            # Backtesting mode: start_date is specified
            start_dt = pd.Timestamp(start_date)
            # Subtract 365 days for indicator warmup
            min_date_str = (start_dt - pd.Timedelta(days=365)).strftime('%Y-%m-%d')
            if end_date is not None:
                max_date_str = pd.Timestamp(end_date).strftime('%Y-%m-%d')
            print(f"  [DB] Fetching backtest K-line data from {min_date_str} to {max_date_str or 'latest'}...", flush=True)
        else:
            # Daily check mode: load the latest limit_days of trading days
            print(f"  [DB] Determining the latest {limit_days} trading dates from DB...", flush=True)
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT date FROM stock_kline ORDER BY date DESC LIMIT %s", (limit_days,))
                dates = [r[0] for r in cur.fetchall()]
                if not dates:
                    print("  [ERROR] No dates found in stock_kline!", flush=True)
                    return {}
                min_date_str = min(dates).strftime('%Y-%m-%d')
            print(f"  [DB] Fetching latest {limit_days} trading days of K-line data since {min_date_str}...", flush=True)
            
        df = None
        
        # Scenario 1: Daily mode for all stocks (limit_to_csi300=False, start_date=None)
        # We query the entire market since min_date_str in a single scan query to avoid multiple-query connection loss.
        if not limit_to_csi300 and start_date is None:
            print("  [DB] Performing a single range query for the entire A-share market (5135 stocks)...", flush=True)
            query = """
            SELECT stock_code, date, open, close, high, low, volume, pe_ttm
            FROM stock_kline
            WHERE date >= %s
            """
            params = [min_date_str]
            df = pd.read_sql(query, conn, params=params)
            
        # Scenario 2: CSI 300 Mode (limit_to_csi300=True)
        # We fetch the codes from roe_history and execute a single IN query (very fast).
        elif limit_to_csi300:
            print("  [DB] Retrieving CSI 300 stock codes from roe_history...", flush=True)
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT code FROM roe_history")
                raw_csi_codes = [r[0] for r in cur.fetchall()]
            db_codes = [c.replace('.', '_') for c in raw_csi_codes]
            print(f"  [DB] Target universe limited to {len(db_codes)} stocks in roe_history.", flush=True)
            
            format_strings = ','.join(['%s'] * len(db_codes))
            query = f"""
            SELECT stock_code, date, open, close, high, low, volume, pe_ttm
            FROM stock_kline
            WHERE stock_code IN ({format_strings}) AND date >= %s
            """
            params = db_codes + [min_date_str]
            if max_date_str is not None:
                query += " AND date <= %s"
                params.append(max_date_str)
                
            df = pd.read_sql(query, conn, params=params)
            
        # Scenario 3: Backtest mode for all stocks (limit_to_csi300=False, start_date is not None)
        # We query in small batches of 100 stocks, and sleep between batches to avoid connection loss/throttling.
        else:
            print("  [DB] Retrieving all stock codes from stock_kline...", flush=True)
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT stock_code FROM stock_kline")
                target_codes = [r[0] for r in cur.fetchall()]
            print(f"  [DB] Target universe contains all {len(target_codes)} stocks. Fetching in batches of 100...", flush=True)
            
            batch_size = 100
            all_dfs = []
            
            for i in range(0, len(target_codes), batch_size):
                batch = target_codes[i:i+batch_size]
                format_strings = ','.join(['%s'] * len(batch))
                
                query = f"""
                SELECT stock_code, date, open, close, high, low, volume, pe_ttm
                FROM stock_kline
                WHERE stock_code IN ({format_strings}) AND date >= %s
                """
                params = batch + [min_date_str]
                if max_date_str is not None:
                    query += " AND date <= %s"
                    params.append(max_date_str)
                
                # Try executing the query, recreate connection if lost
                try:
                    batch_df = pd.read_sql(query, conn, params=params)
                except (pymysql.err.OperationalError, pymysql.err.InterfaceError):
                    print("  [DB] Connection lost or timed out, reconnecting...", flush=True)
                    conn.close()
                    conn = get_connection()
                    batch_df = pd.read_sql(query, conn, params=params)
                    
                if not batch_df.empty:
                    all_dfs.append(batch_df)
                
                print(f"    - Processed batch {i//batch_size + 1}/{(len(target_codes)-1)//batch_size + 1} ({len(batch_df)} rows)...", flush=True)
                # Sleep a little to prevent connection throttling
                time.sleep(0.2)
                
            if all_dfs:
                df = pd.concat(all_dfs, ignore_index=True)
                
        if df is None or df.empty:
            print("  [WARN] No data returned from database query.", flush=True)
            return {}
            
        print(f"  [SUCCESS] Loaded {len(df)} rows in total. Formatting into dict...", flush=True)
        
        # Convert stock_code (e.g., sh_600000) to standard code format (e.g., sh.600000)
        df['code'] = df['stock_code'].str.replace('_', '.', regex=False)
        df['date'] = pd.to_datetime(df['date'])
        
        # Rename pe_ttm to peTTM
        df.rename(columns={'pe_ttm': 'peTTM'}, inplace=True)
        
        # Convert columns to numeric
        for col in ['open', 'close', 'high', 'low', 'volume', 'peTTM']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                
        # Group by stock code
        all_data = {}
        grouped = df.groupby('code')
        for code, group in grouped:
            # Sort by date
            group = group.sort_values('date')
            group.set_index('date', inplace=True)
            
            # Keep only standard columns to match original CSV structure
            cols_to_keep = [c for c in ['open', 'close', 'high', 'low', 'volume', 'peTTM'] if c in group.columns]
            group = group[cols_to_keep]
            
            # We require at least 200 rows of history to compute rolling indicators
            if len(group) < 200:
                continue
                
            all_data[code] = group
            
        print(f"  [SUCCESS] Loaded and parsed {len(all_data)} stocks data.", flush=True)
        return all_data
        
    finally:
        conn.close()

def load_roe_data_db():
    """
    Load ROE history data from TiDB Cloud database.
    Returns a dictionary of stock code -> ROE DataFrame.
    """
    conn = get_connection()
    try:
        print("  [DB] Querying ROE history data from DB...", flush=True)
        query = """
        SELECT code, stat_date, pub_date, year, quarter, roe
        FROM roe_history
        """
        df = pd.read_sql(query, conn)
        if df.empty:
            print("  [WARN] No ROE data returned from database query.", flush=True)
            return {}
            
        # Map columns to Chinese names as expected by generator_roe.py
        # code -> 代码, stat_date -> 统计日期, pub_date -> 发布日期, year -> 年份, quarter -> 季度, roe -> ROE
        df.rename(columns={
            'code': '代码',
            'stat_date': '统计日期',
            'pub_date': '发布日期',
            'year': '年份',
            'quarter': '季度',
            'roe': 'ROE'
        }, inplace=True)
        
        # Parse dates and convert ROE to float
        df['统计日期'] = pd.to_datetime(df['统计日期'])
        df['发布日期'] = pd.to_datetime(df['发布日期'])
        df['ROE'] = pd.to_numeric(df['ROE'], errors='coerce')
        
        # Drop rows with NaN in critical columns
        df.dropna(subset=['统计日期', '发布日期', 'ROE'], inplace=True)
        
        # Group by stock code
        roe_data = {}
        for code, group in df.groupby('代码'):
            group = group.sort_values('统计日期')
            roe_data[code] = group
            
        print(f"  [SUCCESS] Loaded ROE data for {len(roe_data)} stocks from database.", flush=True)
        return roe_data
        
    finally:
        conn.close()
