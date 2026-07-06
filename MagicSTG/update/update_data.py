import os
import sys
import time
import random
from datetime import datetime, timedelta
import baostock as bs
import pandas as pd
import pymysql

# 尝试加载 .env（本地环境），如果失败则跳过（GitHub Actions 直接使用系统环境变量）
try:
    from dotenv import load_dotenv
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ENV_PATH = os.path.join(PROJECT_ROOT, 'dbconfig', '.env')
    if os.path.exists(ENV_PATH):
        load_dotenv(ENV_PATH)
        print(f"[ENV] Loaded .env from: {ENV_PATH}", flush=True)
    else:
        print("[ENV] No .env file found, using system environment variables", flush=True)
except ImportError:
    print("[ENV] python-dotenv not installed, using system environment variables", flush=True)

# 从环境变量读取配置
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "")
DB_SSL_CA = os.getenv("DB_SSL_CA", "")

print(f"[DB] Host: {DB_HOST}, Port: {DB_PORT}, User: {DB_USER}, Database: {DB_NAME}", flush=True)


def get_beijing_time():
    """
    获取当前北京时间 (UTC+8)
    不依赖第三方库，直接使用 UTC 时间 + 8 小时
    """
    return datetime.utcnow() + timedelta(hours=8)


def get_connection():
    """Establishes connection to the MySQL/TiDB database server."""
    is_github_actions = os.environ.get('GITHUB_ACTIONS') == 'true'
    
    if is_github_actions:
        ssl_ca = "/etc/ssl/cert.pem"
    else:
        ssl_ca = DB_SSL_CA
        if ssl_ca and not os.path.exists(ssl_ca):
            filename = os.path.basename(ssl_ca)
            for path_candidate in [
                os.path.join(PROJECT_ROOT, 'dbconfig', filename),
                os.path.join(PROJECT_ROOT, filename),
            ]:
                if os.path.exists(path_candidate):
                    ssl_ca = path_candidate
                    break
        if ssl_ca and os.path.exists(ssl_ca):
            print(f"[SSL] Using CA: {ssl_ca}", flush=True)
        else:
            if os.path.exists("/etc/ssl/cert.pem"):
                ssl_ca = "/etc/ssl/cert.pem"
            else:
                ssl_ca = None
    
    conn_params = {
        "host": DB_HOST,
        "port": DB_PORT,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "database": DB_NAME,
        "charset": "utf8mb4",
        "autocommit": False,
        "connect_timeout": 15,
        "read_timeout": 60,
    }
    
    if ssl_ca and os.path.exists(ssl_ca):
        conn_params["ssl"] = {
            "ca": ssl_ca,
            "verify_cert": True,
            "verify_identity": True
        }
    else:
        conn_params["ssl"] = {
            "verify_cert": False,
            "verify_identity": False
        }
    
    return pymysql.connect(**conn_params)


def ensure_bs_login():
    """确保 Baostock 已登录，如果未登录或会话失效则重新登录"""
    try:
        rs = bs.query_stock_basic()
        if rs.error_code == '0':
            return True
    except Exception:
        pass
    
    print("[Baostock] Session expired or not logged in, re-logging...", flush=True)
    try:
        bs.logout()
    except Exception:
        pass
    time.sleep(1)
    lg = bs.login()
    if lg.error_code != '0':
        print(f"[Baostock] Login failed: {lg.error_msg}", flush=True)
        return False
    print("[Baostock] Re-login successful", flush=True)
    return True


def get_db_stock_status():
    """Queries the stock_kline table to find existing stock codes and their last update dates."""
    conn = get_connection()
    status = {}
    try:
        print("  [DB] Querying existing stocks and last update dates from stock_kline table...", flush=True)
        t0 = time.time()
        with conn.cursor() as cur:
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
    if not ensure_bs_login():
        return []
    rs = bs.query_stock_basic()
    if rs.error_code != '0':
        return []
    stocks = []
    while rs.next():
        row = rs.get_row_data()
        if row[4] == '1' and row[5] == '1':
            stocks.append(row[0])
    return stocks


def fetch_stock_kline(code, start_date, end_date, max_retries=2):
    """Queries K-line data from Baostock with retry logic and auto-reconnect."""
    for attempt in range(max_retries):
        try:
            if not ensure_bs_login():
                time.sleep(2)
                continue
            
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
                    ensure_bs_login()
                    continue
                return None, False
            data_list = []
            while rs.next():
                data_list.append(rs.get_row_data())
            return data_list, True
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            print(f"  [WARN] Connection error: {e}, reconnecting...", flush=True)
            if attempt < max_retries - 1:
                ensure_bs_login()
                time.sleep(random.uniform(2, 4))
            else:
                return None, False
        except Exception as e:
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
    # ========== 关键修改：使用北京时间 ==========
    beijing_time = get_beijing_time()
    today_str = beijing_time.strftime('%Y-%m-%d')
    current_hour = beijing_time.hour
    current_minute = beijing_time.minute
    
    print("=" * 70)
    print("  [UPDATE] A-share Market Data Incremental Sync (DB-only Mode)")
    print(f"  [北京时间] {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  [系统时间] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (UTC)")
    
    # ========== 关键修改：使用北京时间判断是否已收盘 ==========
    # A股收盘时间 15:00，数据通常在 15:30 之后更新完毕
    # 设置为 18:00 作为安全阈值，确保数据已完整更新
    if current_hour >= 18:
        target_date = today_str
        print(f"  ⏰ 当前时间 {current_hour:02d}:{current_minute:02d} >= 18:00，拉取截止到 {target_date} 的数据")
    else:
        # 还没到18:00，拉取昨天的数据（交易日）
        target_date = (beijing_time - timedelta(days=1)).strftime('%Y-%m-%d')
        print(f"  ⏰ 当前时间 {current_hour:02d}:{current_minute:02d} < 18:00，拉取截止到 {target_date} 的数据")
    
    print("=" * 70, flush=True)
    
    # Establish database connection with retry logic
    conn = None
    for attempt in range(1, 6):
        try:
            conn = get_connection()
            print("[DB] Database connection established successfully!", flush=True)
            break
        except Exception as e:
            print(f"Failed to connect to database (attempt {attempt}/5): {e}", flush=True)
            if attempt < 5:
                time.sleep(3)
            else:
                print("Error: Could not establish database connection. Exiting.", flush=True)
                return

    # Login to Baostock
    if not ensure_bs_login():
        print("Baostock login failed. Exiting.", flush=True)
        if conn:
            conn.close()
        return
    
    try:
        db_stocks = get_db_stock_status()
        
        db_buffer = []
        db_buffer_limit = 500
        
        total_new_rows = 0
        updated_count = 0
        fail_count = 0
        new_codes_count = 0
        
        # [1] Detect and download new stocks
        print("\n[1] Detecting new stocks not present in DB...", flush=True)
        all_codes = get_all_stock_codes()
        if not all_codes:
            print("  [ERROR] Failed to get stock list from Baostock", flush=True)
            return
        
        new_codes = [c for c in all_codes if c not in db_stocks]
        
        if new_codes:
            print(f"  [NEW] Found new stocks: {len(new_codes)} count", flush=True)
            print(f"        {', '.join(new_codes[:10])}{'...' if len(new_codes)>10 else ''}", flush=True)
            
            print("\n[2] Downloading history for new stocks (since 2020-01-01)...", flush=True)
            for idx, code in enumerate(new_codes):
                print(f"  {code} ...", end=" ", flush=True)
                data_list, ok = fetch_stock_kline(code, "2020-01-01", target_date)
                if ok and data_list:
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
                            row[0],
                            open_val,
                            close_val,
                            high_val,
                            low_val,
                            safe_int(row[5], 0),
                            safe_float(row[6]),
                            safe_float(row[7]),
                            safe_float(row[8])
                        )
                        db_buffer.append(db_row)
                        valid_rows += 1
                    
                    if len(db_buffer) >= db_buffer_limit:
                        conn = flush_db_buffer(conn, db_buffer)
                        conn.commit()
                        db_buffer = []
                    
                    new_codes_count += 1
                    total_new_rows += valid_rows
                    print(f"[SUCCESS] {valid_rows} rows inserted into DB", flush=True)
                else:
                    print("[FAIL]", flush=True)
                
                if (idx + 1) % 5 == 0:
                    time.sleep(random.uniform(0.5, 1))
        else:
            print("  [SUCCESS] No new stocks found.", flush=True)
        
        # [2] Update existing stocks
        print(f"\n[3] Incrementally updating existing stocks in database...", flush=True)
        print(f"    目标日期: {target_date}")
        print("-" * 70, flush=True)
        
        for i, (code, last_date) in enumerate(db_stocks.items()):
            # 如果数据库中已经包含了目标日期的数据，跳过
            if pd.to_datetime(last_date) >= pd.to_datetime(target_date):
                continue
                
            start_date = (pd.to_datetime(last_date) + timedelta(days=1)).strftime('%Y-%m-%d')
            print(f"[{i+1}/{len(db_stocks)}] {code} (更新: {last_date} -> {target_date}) ...", end=" ", flush=True)
            
            data_list, ok = fetch_stock_kline(code, start_date, target_date)
            if not ok or data_list is None:
                print("[FAIL]", flush=True)
                fail_count += 1
                continue
            if len(data_list) == 0:
                print("[SKIP] 无新交易日数据", flush=True)
                continue
                
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
                    row[0],
                    open_val,
                    close_val,
                    high_val,
                    low_val,
                    safe_int(row[5], 0),
                    safe_float(row[6]),
                    safe_float(row[7]),
                    safe_float(row[8])
                )
                db_buffer.append(db_row)
                valid_rows += 1
            
            if len(db_buffer) >= db_buffer_limit:
                conn = flush_db_buffer(conn, db_buffer)
                conn.commit()
                db_buffer = []
                
            updated_count += 1
            total_new_rows += valid_rows
            print(f"[SUCCESS] 新增 {valid_rows} 行", flush=True)
            
            if (i + 1) % 5 == 0:
                time.sleep(random.uniform(0.5, 1.5))
                
        if db_buffer:
            print("\nFlushing remaining updates to database...", flush=True)
            conn = flush_db_buffer(conn, db_buffer)
            conn.commit()
            db_buffer = []
            
        print("=" * 70)
        print("📊 数据同步汇总")
        print(f"  [新增股票]          : {new_codes_count}")
        print(f"  [更新股票]          : {updated_count}")
        print(f"  [写入数据库行数]    : {total_new_rows}")
        print(f"  [失败股票]          : {fail_count}")
        print(f"  [目标日期]          : {target_date}")
        print("=" * 70, flush=True)
        
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"\nFatal error during runtime: {e}", flush=True)
    finally:
        try:
            bs.logout()
        except Exception:
            pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()