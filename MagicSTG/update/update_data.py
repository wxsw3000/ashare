import os
import sys
import time
import random
from datetime import datetime, timedelta
import baostock as bs
import pandas as pd
import pymysql

# ========== 性能与日志工具 ==========
class ScriptLogger:
    """带时间戳的日志记录器"""
    def __init__(self):
        self.start_time = time.time()
        self.step_times = {}
    
    def log(self, msg, level="INFO"):
        """打印带时间戳的日志"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] [{level}] {msg}", flush=True)
    
    def start_step(self, step_name):
        """开始记录一个步骤的耗时"""
        self.step_times[step_name] = {
            'start': time.time(),
            'end': None,
            'duration': None
        }
        self.log(f"⏱️ 开始: {step_name}", "STEP")
    
    def end_step(self, step_name):
        """结束记录一个步骤，打印耗时"""
        if step_name in self.step_times:
            self.step_times[step_name]['end'] = time.time()
            duration = self.step_times[step_name]['end'] - self.step_times[step_name]['start']
            self.step_times[step_name]['duration'] = duration
            self.log(f"⏱️ 完成: {step_name} (耗时: {duration:.2f}秒)", "STEP")
        else:
            self.log(f"⚠️ 未找到步骤: {step_name}", "WARN")
    
    def print_final_summary(self):
        """打印最终性能汇总"""
        total_time = time.time() - self.start_time
        self.log("=" * 70, "SUMMARY")
        self.log(f"📊 脚本总运行时间: {total_time:.2f} 秒 ({total_time/60:.2f} 分钟)", "SUMMARY")
        self.log("-" * 70, "SUMMARY")
        self.log("各步骤耗时明细:", "SUMMARY")
        for name, data in self.step_times.items():
            if data['duration'] is not None:
                self.log(f"  {name}: {data['duration']:.2f} 秒", "SUMMARY")
        self.log("=" * 70, "SUMMARY")

# 全局日志实例
logger = ScriptLogger()

# 尝试加载 .env（本地环境），如果失败则跳过（GitHub Actions 直接使用系统环境变量）
try:
    from dotenv import load_dotenv
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ENV_PATH = os.path.join(PROJECT_ROOT, 'dbconfig', '.env')
    if os.path.exists(ENV_PATH):
        load_dotenv(ENV_PATH)
        logger.log(f"加载 .env 文件: {ENV_PATH}")
    else:
        logger.log("未找到 .env 文件，使用系统环境变量")
except ImportError:
    logger.log("python-dotenv 未安装，使用系统环境变量")

# 从环境变量读取配置
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "")
DB_SSL_CA = os.getenv("DB_SSL_CA", "")

logger.log(f"数据库配置: Host={DB_HOST}, Port={DB_PORT}, User={DB_USER}, Database={DB_NAME}")


def get_connection():
    """Establishes connection to the MySQL/TiDB database server."""
    is_github_actions = os.environ.get('GITHUB_ACTIONS') == 'true'
    
    if is_github_actions:
        ssl_ca = "/etc/ssl/cert.pem"
        logger.log("检测到 GitHub Actions 环境，使用系统 CA 证书")
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
            logger.log(f"使用 SSL CA 证书: {ssl_ca}")
        else:
            if os.path.exists("/etc/ssl/cert.pem"):
                ssl_ca = "/etc/ssl/cert.pem"
                logger.log("使用系统 CA 证书: /etc/ssl/cert.pem")
            else:
                ssl_ca = None
                logger.log("⚠️ 未找到 SSL CA 证书", "WARN")
    
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
        logger.log("SSL 连接已启用（证书验证）")
    else:
        conn_params["ssl"] = {
            "verify_cert": False,
            "verify_identity": False
        }
        logger.log("⚠️ SSL 连接已启用（跳过证书验证）", "WARN")
    
    return pymysql.connect(**conn_params)


def ensure_bs_login():
    """确保 Baostock 已登录，如果未登录或会话失效则重新登录"""
    try:
        rs = bs.query_stock_basic()
        if rs.error_code == '0':
            return True
    except Exception:
        pass
    
    logger.log("Baostock 会话已过期或未登录，正在重新登录...")
    try:
        bs.logout()
    except Exception:
        pass
    time.sleep(1)
    lg = bs.login()
    if lg.error_code != '0':
        logger.log(f"Baostock 登录失败: {lg.error_msg}", "ERROR")
        return False
    logger.log("Baostock 重新登录成功")
    return True


def get_db_stock_status():
    """Queries the stock_kline table to find existing stock codes and their last update dates."""
    conn = get_connection()
    status = {}
    try:
        logger.log("查询数据库现有股票和最后更新日期...")
        t0 = time.time()
        with conn.cursor() as cur:
            cur.execute("SELECT stock_code, MAX(date) FROM stock_kline GROUP BY stock_code")
            rows = cur.fetchall()
            for row in rows:
                code_dot = row[0].replace('_', '.')
                status[code_dot] = row[1].strftime('%Y-%m-%d')
        elapsed = time.time() - t0
        logger.log(f"数据库查询完成，找到 {len(status)} 只股票，耗时 {elapsed:.2f} 秒")
    except Exception as e:
        logger.log(f"查询数据库失败: {e}", "ERROR")
    finally:
        conn.close()
    return status


def get_all_stock_codes():
    """Gets A-share stock list from Baostock for new stock detection."""
    if not ensure_bs_login():
        return []
    rs = bs.query_stock_basic()
    if rs.error_code != '0':
        logger.log(f"获取股票列表失败: {rs.error_msg}", "ERROR")
        return []
    stocks = []
    while rs.next():
        row = rs.get_row_data()
        if row[4] == '1' and row[5] == '1':
            stocks.append(row[0])
    logger.log(f"从 Baostock 获取到 {len(stocks)} 只股票")
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
                logger.log(f"Baostock 请求失败: {rs.error_msg}", "WARN")
                return None, False
            data_list = []
            while rs.next():
                data_list.append(rs.get_row_data())
            return data_list, True
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            logger.log(f"网络连接错误: {e}，正在重连...", "WARN")
            if attempt < max_retries - 1:
                ensure_bs_login()
                time.sleep(random.uniform(2, 4))
            else:
                return None, False
        except Exception as e:
            logger.log(f"未知错误: {e}，尝试重试...", "WARN")
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
            logger.log(f"批量插入 {len(batch_data)} 行成功")
            return conn
        except Exception as e:
            logger.log(f"批量插入失败 (尝试 {attempt}/3): {e}", "WARN")
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
    logger.log("=" * 70)
    logger.log("  A股市场数据增量同步 (数据库模式)")
    logger.log(f"  今日日期: {datetime.now().strftime('%Y-%m-%d')}")
    logger.log("=" * 70)
    
    total_start = time.time()
    
    # ========== 关键优化：本地时间判断，决定是否需要更新 ==========
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    yesterday_str = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    
    # 规则：18:00 之前预期数据到昨天，18:00 之后（含）预期数据到今天
    if now.hour < 18:
        expected_date = yesterday_str
        logger.log(f"⏰ 当前时间 {now.strftime('%H:%M')} < 18:00，预期数据截止到 {expected_date}")
    else:
        expected_date = today_str
        logger.log(f"⏰ 当前时间 {now.strftime('%H:%M')} >= 18:00，预期数据截止到 {expected_date}")
    
    # 建立数据库连接
    conn = None
    for attempt in range(1, 6):
        try:
            conn = get_connection()
            logger.log("数据库连接建立成功")
            break
        except Exception as e:
            logger.log(f"数据库连接失败 (尝试 {attempt}/5): {e}", "ERROR")
            if attempt < 5:
                time.sleep(3)
            else:
                logger.log("无法建立数据库连接，退出程序", "ERROR")
                return

    # 登录 Baostock（仅用于获取新股票列表，实际数据更新时才需要）
    if not ensure_bs_login():
        logger.log("Baostock 登录失败，退出程序", "ERROR")
        if conn:
            conn.close()
        return
    
    try:
        # 查询数据库现有股票
        logger.start_step("查询数据库现有股票")
        db_stocks = get_db_stock_status()
        logger.end_step("查询数据库现有股票")
        
        db_buffer = []
        db_buffer_limit = 200  # 每 200 行提交一次
        
        total_new_rows = 0
        updated_count = 0
        fail_count = 0
        new_codes_count = 0
        
        # 检测新股票
        logger.start_step("检测新股票")
        all_codes = get_all_stock_codes()
        if not all_codes:
            logger.log("无法从 Baostock 获取股票列表", "ERROR")
            return
        
        new_codes = [c for c in all_codes if c not in db_stocks]
        logger.log(f"发现 {len(new_codes)} 只新股票")
        logger.end_step("检测新股票")
        
        # 下载新股票历史数据
        if new_codes:
            logger.start_step(f"下载 {len(new_codes)} 只新股票历史数据")
            for idx, code in enumerate(new_codes):
                stock_start = time.time()
                logger.log(f"  [{idx+1}/{len(new_codes)}] {code} ...")
                data_list, ok = fetch_stock_kline(code, "2020-01-01", datetime.now().strftime('%Y-%m-%d'))
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
                    elapsed = time.time() - stock_start
                    logger.log(f"  [成功] {valid_rows} 行 (耗时 {elapsed:.2f}s)")
                else:
                    logger.log(f"  [失败]", "WARN")
                
                if (idx + 1) % 20 == 0:
                    time.sleep(random.uniform(0.5, 1.0))
            
            logger.end_step(f"下载 {len(new_codes)} 只新股票历史数据")
        else:
            logger.log("没有发现新股票")
        
        # ========== 更新现有股票（使用本地时间判断优化） ==========
        logger.log(f"\n更新现有股票 (目标日期: {expected_date})...")
        logger.log("-" * 70)
        
        # 统计需要更新的股票数量（用于进度显示）
        need_update_count = 0
        for code, last_date in db_stocks.items():
            if pd.to_datetime(last_date) < pd.to_datetime(expected_date):
                need_update_count += 1
        
        logger.log(f"需要更新的股票数量: {need_update_count} / {len(db_stocks)}")
        
        if need_update_count == 0:
            logger.log("✅ 所有股票数据已是最新，无需更新")
        else:
            logger.start_step(f"更新现有股票 ({need_update_count} 只)")
            processed = 0
            
            for i, (code, last_date) in enumerate(db_stocks.items()):
                # ========== 关键判断：使用 expected_date 而非 today_str ==========
                if pd.to_datetime(last_date) >= pd.to_datetime(expected_date):
                    continue
                    
                processed += 1
                start_date = (pd.to_datetime(last_date) + timedelta(days=1)).strftime('%Y-%m-%d')
                
                stock_start = time.time()
                logger.log(f"[{processed}/{need_update_count}] {code} (更新: {last_date} -> {expected_date}) ...")
                
                data_list, ok = fetch_stock_kline(code, start_date, expected_date)
                if not ok or data_list is None:
                    logger.log(f"  [失败]", "WARN")
                    fail_count += 1
                    continue
                if len(data_list) == 0:
                    logger.log(f"  [跳过] 无新交易日")
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
                elapsed = time.time() - stock_start
                logger.log(f"  [成功] 添加 {valid_rows} 行 (耗时 {elapsed:.2f}s)")
                
                # 每 20 只股票休息一下
                if (i + 1) % 20 == 0:
                    time.sleep(random.uniform(0.5, 1.5))
            
            logger.end_step(f"更新现有股票 ({need_update_count} 只)")
        
        # 刷新剩余缓冲区
        if db_buffer:
            logger.log("刷新剩余数据到数据库...")
            conn = flush_db_buffer(conn, db_buffer)
            conn.commit()
            db_buffer = []
        
        # 打印最终结果
        total_time = time.time() - total_start
        logger.log("=" * 70)
        logger.log("📊 数据同步摘要")
        logger.log(f"  预期数据日期: {expected_date}")
        logger.log(f"  新增股票数量: {new_codes_count}")
        logger.log(f"  更新股票数量: {updated_count}")
        logger.log(f"  写入数据库行数: {total_new_rows}")
        logger.log(f"  失败股票数量: {fail_count}")
        logger.log(f"  总运行时间: {total_time:.2f} 秒 ({total_time/60:.2f} 分钟)")
        logger.log("=" * 70)
        
        logger.print_final_summary()
        
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.log(f"运行时发生致命错误: {e}", "ERROR")
    finally:
        try:
            bs.logout()
            logger.log("Baostock 已登出")
        except Exception:
            pass
        if conn:
            try:
                conn.close()
                logger.log("数据库连接已关闭")
            except Exception:
                pass

if __name__ == "__main__":
    main()