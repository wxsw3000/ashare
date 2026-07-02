import pymysql
import os
from dotenv import load_dotenv

# Load env file from E:/ashare/MagicSTG/dbconfig/.env
env_path = r"E:\ashare\MagicSTG\dbconfig\.env"
load_dotenv(env_path)

DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", 4000))
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")
DB_SSL_CA = os.getenv("DB_SSL_CA")

print("Connecting to:", DB_HOST, DB_PORT, DB_USER, DB_NAME)
print("SSL CA path:", DB_SSL_CA)

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
    connection = pymysql.connect(**conn_params)
    print("Connection successful!")
    with connection.cursor() as cursor:
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        print("Tables in database:", tables)
        
        for table in tables:
            table_name = table[0]
            print(f"\nSchema for table {table_name}:")
            cursor.execute(f"DESCRIBE `{table_name}`")
            columns = cursor.fetchall()
            for col in columns:
                print(col)
                
            cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
            cnt = cursor.fetchone()
            print(f"Row count: {cnt[0]}")
            
            cursor.execute(f"SELECT * FROM `{table_name}` LIMIT 3")
            rows = cursor.fetchall()
            print(f"Sample data:")
            for r in rows:
                print(r)
except Exception as e:
    print("Error connecting/querying:", e)
