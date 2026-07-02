import pymysql
import os
import time
import pandas as pd
from dotenv import load_dotenv

env_path = r"E:\ashare\MagicSTG\dbconfig\.env"
load_dotenv(env_path)

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
    "autocommit": True
}
if DB_SSL_CA and os.path.exists(DB_SSL_CA):
    conn_params["ssl"] = {"ca": DB_SSL_CA}

try:
    conn = pymysql.connect(**conn_params)
    print("Getting list of CSI 300 stock codes from roe_history...")
    t0 = time.time()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT code FROM roe_history")
        csi300_codes = [r[0] for r in cur.fetchall()]
    print(f"Found {len(csi300_codes)} unique stocks in roe_history in {time.time() - t0:.2f} seconds.")
    
    # Convert to database stock_kline code format (sh.600000 -> sh_600000)
    db_codes = [c.replace('.', '_') for c in csi300_codes]
    
    # Query stock_kline for these 289 stocks for the entire 6.5 year period
    start_date = "2020-01-02"
    end_date = "2026-07-01"
    print(f"Querying stock_kline for {len(db_codes)} stocks from {start_date} to {end_date}...")
    
    t0 = time.time()
    format_strings = ','.join(['%s'] * len(db_codes))
    query = f"""
    SELECT stock_code, date, open, close, high, low, volume, pe_ttm
    FROM stock_kline
    WHERE stock_code IN ({format_strings}) AND date >= %s AND date <= %s
    """
    params = db_codes + [start_date, end_date]
    
    df = pd.read_sql(query, conn, params=params)
    t1 = time.time()
    print(f"CSI 300 query took {t1 - t0:.2f} seconds. Returned {len(df)} rows.")
    
except Exception as e:
    print("Error:", e)
