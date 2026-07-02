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
    print("Testing simple date range query...")
    t0 = time.time()
    
    # Query for date >= '2025-08-01' (about 11 months, which is plenty of trading days)
    query = """
    SELECT stock_code, date, open, close, high, low, volume, pe_ttm, pb_mrq
    FROM stock_kline
    WHERE date >= '2025-08-01'
    """
    
    df = pd.read_sql(query, conn)
    t1 = time.time()
    print(f"Date range query took {t1 - t0:.2f} seconds. Row count: {len(df)}")
    
    # Let's count unique stock codes
    print("Number of unique stocks returned:", df['stock_code'].nunique())
    
    # Check average rows per stock
    counts = df.groupby('stock_code').size()
    print("Average rows per stock:", counts.mean())
    print("Min rows per stock:", counts.min())
    print("Max rows per stock:", counts.max())
    
except Exception as e:
    print("Error:", e)
