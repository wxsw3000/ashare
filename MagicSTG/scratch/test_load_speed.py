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
    print("Testing query speed for last 250 days of data for all stocks...")
    t0 = time.time()
    
    # We can fetch using a subquery with ROW_NUMBER() in TiDB
    query = """
    SELECT stock_code, date, open, close, high, low, volume, pe_ttm, pb_mrq
    FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) as rn
        FROM stock_kline
    ) t
    WHERE rn <= 250
    """
    
    # Alternatively, we can just fetch the last 1 year of dates (since 2025-01-01 to 2026-07-01 is about 360 days)
    # Let's see if range query is much faster:
    # "SELECT stock_code, date, open, close, high, low, volume, pe_ttm, pb_mrq FROM stock_kline WHERE date >= '2025-01-01'"
    
    df = pd.read_sql(query, conn)
    t1 = time.time()
    print(f"Window query took {t1 - t0:.2f} seconds. Row count: {len(df)}")
    
    t0 = time.time()
    query2 = """
    SELECT stock_code, date, open, close, high, low, volume, pe_ttm, pb_mrq
    FROM stock_kline
    WHERE date >= '2025-06-01'
    """
    df2 = pd.read_sql(query2, conn)
    t1 = time.time()
    print(f"Date range query took {t1 - t0:.2f} seconds. Row count: {len(df2)}")
    
except Exception as e:
    print("Error:", e)
