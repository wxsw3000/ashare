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
    print("Getting list of unique stock codes...")
    t0 = time.time()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT stock_code FROM stock_kline")
        stock_codes = [r[0] for r in cur.fetchall()]
    print(f"Found {len(stock_codes)} unique stock codes in {time.time() - t0:.2f} seconds.")
    
    # Let's test querying a batch of 500 stocks for 6.5 years
    batch = stock_codes[:500]
    start_date = "2020-01-02"
    end_date = "2026-07-01"
    
    print(f"Querying a batch of {len(batch)} stocks for date range {start_date} to {end_date}...")
    t0 = time.time()
    
    # Construct IN query
    format_strings = ','.join(['%s'] * len(batch))
    query = f"""
    SELECT stock_code, date, open, close, high, low, volume, pe_ttm
    FROM stock_kline
    WHERE stock_code IN ({format_strings}) AND date >= %s AND date <= %s
    """
    params = batch + [start_date, end_date]
    
    df = pd.read_sql(query, conn, params=params)
    print(f"Batch query took {time.time() - t0:.2f} seconds. Returned {len(df)} rows.")
    
except Exception as e:
    print("Error:", e)
