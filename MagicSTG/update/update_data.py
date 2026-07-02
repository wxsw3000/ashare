import os
import sys
import time
import random
from datetime import datetime, timedelta
import baostock as bs
import pandas as pd
import pymysql
from dotenv import load_dotenv

# Ensure we load the .env file from the correct location E:\ashare\MagicSTG\dbconfig\.env
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(PROJECT_ROOT, 'dbconfig', '.env')
load_dotenv(ENV_PATH)

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "")
DB_SSL_CA = os.getenv("DB_SSL_CA", "")

def get_connection():
    """Establishes connection to the MySQL/TiDB database server."""
    conn_params = {
        "host": DB_HOST,
        "port": DB_PORT,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "database": DB_NAME,
        "charset": "utf8mb4",
        "autocommit": False,  # Explicit transactions for speed
        "connect_timeout": 15,
        "read_timeout": 60
    }
    
    # Configure SSL if SSL CA path is provided
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
            print(f"Warning: SSL CA certificate path '{DB_SSL_CA}' was specified but could not be resolved.", flush=True)
            
    return pymysql.connect(**conn_params)

def get_db_stock_status():
    """
    Queries the stock_kline table to find existing stock codes and their last update dates.
    Returns a dictionary of {stock_code_with_dot: last_date_str}
    """
    conn = get_connection()
    status = {}
    try:
        print("  [DB] Querying existing stocks and last update dates from stock_kline table...", flush=True)
        t0 = time.time()
        with conn.cursor() as cur:
            # Group by stock_code and get the max date. Very fast in TiDB due to composite primary key index.
            cur.execute("SELECT stock_code, MAX(date) FROM stock_kline GROUP BY stock_code")
            rows = cur.fetchall()
            for row in rows:
                code_dot = row[0].replace('_', '.')
                status[code_dot] = row[1].strftime('%Y-%m-%d')
        print(f"  [DB] Found {len(status)} stocks in database in {time.time() - t0:.2f} seconds.", flush=True)
    except Exception as e:
        print(f"  [ERROR] Failed to query stock status from database: {e}", flush=True)
    finally:
        conn.close()
    return status

def get_all_stock_codes():
    """Gets A-share stock list from Baostock for new stock detection."""
    rs = bs.query_stock_basic()
    if rs.error_code != '0':
        return []
    stocks = []
    while rs.next():
        row = rs.get_row_data()
        if row[4] == '1' and row[5] == '1':  # type=Stock, status=Listed
            stocks.append(row[0])
    return stocks

def fetch_stock_kline(code, start_date, end_date, max_retries=3):
    """Queries K-line data from Baostock with retry logic."""
    for attempt in range(max_retries):
        try:
            rs = bs.query_history_k_data_plus(
                code,
                "date,open,close,high,low,volume,turn,peTTM,pbMRQ",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="2"
            )
            if rs.error_code != '0':
                if attempt < max_retries - 1:
                    time.sleep(random.uniform(2, 4))
                    continue
                return None, False
            data_list = []
            while rs.next():
                data_list.append(rs.get_row_data())
            return data_list, True
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(random.uniform(3, 6))
            else:
                return None, False
    return None, False

def safe_int(val, default=0):
    if val is None or val == "" or pd.isna(val):
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default

def safe_float(val, default=None):
    if val is None or val == "" or pd.isna(val):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def flush_db_buffer(conn, batch_data):
    """Executes bulk insert statement on TiDB for database synchronization."""
    if not batch_data:
        return conn
        
    placeholders = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"] * len(batch_data))
    sql = f"""
    INSERT INTO `stock_kline` (
        `stock_code`, `date`, `open`, `close`, `high`, `low`, `volume`, `turn`, `pe_ttm`, `pb_mrq`
    ) VALUES {placeholders}
    ON DUPLICATE KEY UPDATE
        `open` = VALUES(`open`),
        `close` = VALUES(`close`),
        `high` = VALUES(`high`),
        `low` = VALUES(`low`),
        `volume` = VALUES(`volume`),
        `turn` = VALUES(`turn`),
        `pe_ttm` = VALUES(`pe_ttm`),
        `pb_mrq` = VALUES(`pb_mrq`);
    """
    
    flat_args = [val for record in batch_data for val in record]
    
    for attempt in range(1, 4):
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, flat_args)
            return conn
        except Exception as e:
            print(f"Database error during bulk insert (attempt {attempt}/3): {e}", flush=True)
            if attempt < 3:
                time.sleep(2)
                try:
                    conn.close()
                except Exception:
                    pass
                conn = get_connection()
            else:
                raise e
    return conn

def main():
    print("=" * 70)
    print("  [UPDATE] A-share Market Data Incremental Sync (DB-only Mode)")
    print(f"  Today: {datetime.now().strftime('%Y-%m-%d')}")
    print("=" * 70, flush=True)
    
    # Establish database connection with retry logic
    conn = None
    for attempt in range(1, 6):
        try:
            conn = get_connection()
            break
        except Exception as e:
            print(f"Failed to connect to database (attempt {attempt}/5): {e}", flush=True)
            if attempt < 5:
                time.sleep(3)
            else:
                print("Error: Could not establish database connection. Exiting.", flush=True)
                return

    # Login to Baostock
    lg = bs.login()
    if lg.error_code != '0':
        print(f"Login failed: {lg.error_msg}", flush=True)
        conn.close()
        return
    print("Baostock login successful", flush=True)
    
    try:
        # Query database to get existing stock status
        db_stocks = get_db_stock_status()
        
        # DB buffer for bulk inserts
        db_buffer = []
        db_buffer_limit = 2000
        
        total_new_rows = 0
        updated_count = 0
        fail_count = 0
        new_codes_count = 0
        
        # [1] Detect and download new stocks (not present in database)
        print("\n[1] Detecting new stocks not present in DB...", flush=True)
        all_codes = get_all_stock_codes()
        new_codes = [c for c in all_codes if c not in db_stocks]
        
        if new_codes:
            print(f"  [NEW] Found new stocks: {len(new_codes)} count", flush=True)
            print(f"        {', '.join(new_codes[:10])}{'...' if len(new_codes)>10 else ''}", flush=True)
            
            print("\n[2] Downloading history for new stocks (since 2020-01-01)...", flush=True)
            for code in new_codes:
                print(f"  {code} ...", end=" ", flush=True)
                data_list, ok = fetch_stock_kline(code, "2020-01-01", datetime.now().strftime('%Y-%m-%d'))
                if ok and data_list:
                    # Filter and convert to DB tuples
                    valid_rows = 0
                    for row in data_list:
                        open_val = safe_float(row[1])
                        close_val = safe_float(row[2])
                        high_val = safe_float(row[3])
                        low_val = safe_float(row[4])
                        if open_val is None or close_val is None or high_val is None or low_val is None:
                            continue
                            
                        db_row = (
                            code.replace('.', '_'),
                            row[0], # date
                            open_val,
                            close_val,
                            high_val,
                            low_val,
                            safe_int(row[5], 0), # volume
                            safe_float(row[6]), # turn
                            safe_float(row[7]), # pe_ttm
                            safe_float(row[8])  # pb_mrq
                        )
                        db_buffer.append(db_row)
                        valid_rows += 1
                        
                    # Flush DB buffer if full
                    if len(db_buffer) >= db_buffer_limit:
                        conn = flush_db_buffer(conn, db_buffer)
                        conn.commit()
                        db_buffer = []
                        
                    new_codes_count += 1
                    total_new_rows += valid_rows
                    print(f"[SUCCESS] {valid_rows} rows inserted into DB", flush=True)
                else:
                    print("[FAIL]", flush=True)
        else:
            print("  [SUCCESS] No new stocks found.", flush=True)
        
        # [2] Update existing stocks in database
        today_str = datetime.now().strftime('%Y-%m-%d')
        print(f"\n[3] Incrementally updating existing stocks in database...", flush=True)
        print("-" * 70, flush=True)
        
        for i, (code, last_date) in enumerate(db_stocks.items()):
            # If the database last date is already today or newer, skip
            if pd.to_datetime(last_date) >= pd.to_datetime(today_str):
                continue
                
            start_date = (pd.to_datetime(last_date) + timedelta(days=1)).strftime('%Y-%m-%d')
            print(f"[{i+1}/{len(db_stocks)}] {code} (Update: {last_date} -> {today_str}) ...", end=" ", flush=True)
            
            data_list, ok = fetch_stock_kline(code, start_date, today_str)
            if not ok or data_list is None:
                print("[FAIL]", flush=True)
                fail_count += 1
                continue
            if len(data_list) == 0:
                print("[SKIP] No new trading days", flush=True)
                continue
                
            # Filter and convert to DB tuples
            valid_rows = 0
            for row in data_list:
                open_val = safe_float(row[1])
                close_val = safe_float(row[2])
                high_val = safe_float(row[3])
                low_val = safe_float(row[4])
                if open_val is None or close_val is None or high_val is None or low_val is None:
                    continue
                    
                db_row = (
                    code.replace('.', '_'),
                    row[0], # date
                    open_val,
                    close_val,
                    high_val,
                    low_val,
                    safe_int(row[5], 0), # volume
                    safe_float(row[6]), # turn
                    safe_float(row[7]), # pe_ttm
                    safe_float(row[8])  # pb_mrq
                )
                db_buffer.append(db_row)
                valid_rows += 1
            
            # Flush DB buffer if full
            if len(db_buffer) >= db_buffer_limit:
                conn = flush_db_buffer(conn, db_buffer)
                conn.commit()
                db_buffer = []
                
            updated_count += 1
            total_new_rows += valid_rows
            print(f"[SUCCESS] Added {valid_rows} rows", flush=True)
            
            # Add small delay to respect Baostock request limit
            if (i + 1) % 20 == 0:
                time.sleep(random.uniform(0.2, 0.4))
                
        # Flush any remaining records in the DB buffer
        if db_buffer:
            print("\nFlushing remaining updates to database...", flush=True)
            conn = flush_db_buffer(conn, db_buffer)
            conn.commit()
            db_buffer = []
            
        print("=" * 70)
        print("📊 Data Sync Summary")
        print(f"  [New Stocks Added]      : {new_codes_count}")
        print(f"  [Existing Stocks Updated]: {updated_count}")
        print(f"  [Rows Written to DB]     : {total_new_rows}")
        print(f"  [Failed Stocks]          : {fail_count}")
        print("=" * 70, flush=True)
        
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"\nFatal error during runtime: {e}", flush=True)
    finally:
        bs.logout()
        if conn:
            try:
                conn.close()
            except Exception:
                pass

if __name__ == "__main__":
    main()