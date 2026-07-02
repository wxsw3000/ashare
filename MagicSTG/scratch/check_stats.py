import pymysql
import os
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
    with conn.cursor() as cur:
        # Unique stock codes count
        cur.execute("SELECT COUNT(DISTINCT stock_code) FROM stock_kline")
        distinct_stocks = cur.fetchone()[0]
        print(f"Distinct stock codes in stock_kline: {distinct_stocks}")
        
        # Min/Max date
        cur.execute("SELECT MIN(date), MAX(date) FROM stock_kline")
        min_date, max_date = cur.fetchone()
        print(f"Date range in stock_kline: {min_date} to {max_date}")
        
        # Check a sample of stocks with their row counts
        cur.execute("SELECT stock_code, COUNT(*) FROM stock_kline GROUP BY stock_code LIMIT 10")
        rows = cur.fetchall()
        print("Sample stocks and their row counts in stock_kline:")
        for r in rows:
            print(f"  {r[0]}: {r[1]} rows")
            
        # Check roe_history stats
        cur.execute("SELECT COUNT(DISTINCT code) FROM roe_history")
        distinct_roe = cur.fetchone()[0]
        print(f"Distinct stock codes in roe_history: {distinct_roe}")
        
        cur.execute("SELECT MIN(stat_date), MAX(stat_date), MIN(pub_date), MAX(pub_date) FROM roe_history")
        min_stat, max_stat, min_pub, max_pub = cur.fetchone()
        print(f"roe_history stat_date range: {min_stat} to {max_stat}")
        print(f"roe_history pub_date range: {min_pub} to {max_pub}")
        
except Exception as e:
    print("Error:", e)
