import os
import sys
import time
import random
from datetime import datetime
import baostock as bs
import pymysql

# ========== 配置 ==========
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BATCH_SIZE = 200
START_YEAR = 2020  # 从 2020 年开始
REQUEST_DELAY = 0.3
SLEEP_INTERVAL = 20
SLEEP_DURATION = (0.5, 1.5)

# ========== 日志工具 ==========
class Logger:
    def __init__(self):
        self.start_time = time.time()
        self.step_times = {}
    
    def log(self, msg, level="INFO"):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] [{level}] {msg}", flush=True)
    
    def log_progress(self, idx, total, code, status, new_count, total_rows, elapsed):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        status_icon = "✅" if status == "success" else "⏭️" if status == "skip" else "❌"
        print(f"[{timestamp}] [{idx:>4}/{total}] {code:<12} {status_icon} {status:<6} 新增:{new_count:<3} 累计:{total_rows:>6} {elapsed:>6.2f}s", flush=True)
    
    def start_step(self, step_name):
        self.step_times[step_name] = {'start': time.time(), 'end': None, 'duration': None}
        self.log(f"⏱️ 开始: {step_name}", "STEP")
    
    def end_step(self, step_name):
        if step_name in self.step_times:
            self.step_times[step_name]['end'] = time.time()
            duration = self.step_times[step_name]['end'] - self.step_times[step_name]['start']
            self.step_times[step_name]['duration'] = duration
            self.log(f"⏱️ 完成: {step_name} (耗时: {duration:.2f}秒)", "STEP")
    
    def print_summary(self):
        total = time.time() - self.start_time
        self.log("=" * 70, "SUMMARY")
        self.log(f"📊 脚本总运行时间: {total:.2f} 秒 ({total/60:.2f} 分钟)", "SUMMARY")
        self.log("-" * 70, "SUMMARY")
        self.log("各步骤耗时:", "SUMMARY")
        for name, data in self.step_times.items():
            if data['duration'] is not None:
                self.log(f"  {name}: {data['duration']:.2f} 秒", "SUMMARY")
        self.log("=" * 70, "SUMMARY")

logger = Logger()

# ========== 环境变量加载 ==========
try:
    from dotenv import load_dotenv
    ENV_PATH = os.path.join(PROJECT_ROOT, 'dbconfig', '.env')
    if os.path.exists(ENV_PATH):
        load_dotenv(ENV_PATH)
        logger.log(f"加载 .env 文件: {ENV_PATH}")
    else:
        logger.log("未找到 .env 文件，使用系统环境变量")
except ImportError:
    logger.log("python-dotenv 未安装，使用系统环境变量")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "")
DB_SSL_CA = os.getenv("DB_SSL_CA", "")

logger.log(f"数据库配置: Host={DB_HOST}, Port={DB_PORT}, User={DB_USER}, Database={DB_NAME}")


def get_connection():
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
            logger.log(f"使用 SSL CA 证书: {ssl_ca}")
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
        "connect_timeout": 30,
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
    try:
        rs = bs.query_stock_basic()
        if rs.error_code == '0':
            return True
    except Exception:
        pass
    
    logger.log("Baostock 会话已过期，重新登录...")
    try:
        bs.logout()
    except Exception:
        pass
    time.sleep(1)
    lg = bs.login()
    if lg.error_code != '0':
        logger.log(f"Baostock 登录失败: {lg.error_msg}", "ERROR")
        return False
    logger.log("Baostock 登录成功")
    return True


def get_all_stock_codes():
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
    logger.log(f"获取到 {len(stocks)} 只股票")
    return stocks


def get_existing_roe_data(conn):
    """
    查询数据库中已有的 ROE 数据
    返回: dict {code: set(stat_date)}
    """
    result = {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT code, stat_date FROM stock_roe_history")
            rows = cur.fetchall()
            for row in rows:
                code = row[0]
                stat_date = row[1].strftime('%Y-%m-%d') if hasattr(row[1], 'strftime') else str(row[1])
                if code not in result:
                    result[code] = set()
                result[code].add(stat_date)
        logger.log(f"  [DB] 查询到 {len(result)} 只股票的已有 ROE 数据")
    except Exception as e:
        logger.log(f"  [DB] 查询失败: {e} (可能表还不存在)", "WARN")
    return result


def fetch_quarter_roe(code, year, quarter, max_retries=2):
    """
    获取单只股票单个季度的 ROE 数据
    返回: dict 或 None
    """
    for attempt in range(max_retries):
        try:
            time.sleep(REQUEST_DELAY)
            
            rs = bs.query_profit_data(code=code, year=year, quarter=quarter)
            
            if rs.error_code == '0':
                while rs.next():
                    row = rs.get_row_data()
                    if len(row) >= 4:
                        stat_date = row[2] if len(row) > 2 else None
                        roe = row[3] if len(row) > 3 else None
                        pub_date = row[1] if len(row) > 1 else None
                        
                        if stat_date and roe and roe != "" and roe != "None":
                            try:
                                roe_val = float(roe) / 100
                                dt = datetime.strptime(stat_date, '%Y-%m-%d')
                                return {
                                    'code': code,
                                    'stat_date': stat_date,
                                    'pub_date': pub_date if pub_date and pub_date != "" else None,
                                    'year': dt.year,
                                    'quarter': quarter,
                                    'roe': roe_val
                                }
                            except (ValueError, TypeError):
                                pass
                return None
            else:
                if attempt < max_retries - 1:
                    time.sleep(random.uniform(1, 2))
                    continue
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(random.uniform(1, 2))
                continue
    return None


def get_missing_quarters(code, existing_dates, current_year):
    """
    计算从 2020 年到当前年哪些季度的数据缺失
    返回: list of (year, quarter)
    """
    missing = []
    # 从 START_YEAR 开始，到 current_year 结束
    for year in range(START_YEAR, current_year + 1):
        # 如果当前年份还没到，只处理到当前季度
        if year == current_year:
            current_quarter = (datetime.now().month - 1) // 3 + 1
            quarters = list(range(1, current_quarter + 1))
        else:
            quarters = [1, 2, 3, 4]
        
        for quarter in quarters:
            # 构造 stat_date 格式：YYYY-MM-DD
            month_map = {1: 3, 2: 6, 3: 9, 4: 12}
            day_map = {1: 31, 2: 30, 3: 30, 4: 31}
            stat_date_str = f"{year}-{month_map[quarter]:02d}-{day_map[quarter]:02d}"
            
            # 检查是否已存在
            if stat_date_str not in existing_dates:
                missing.append((year, quarter))
    return missing


def flush_db_buffer(conn, batch_data):
    """批量插入数据"""
    if not batch_data:
        return conn
    
    sql = """
    INSERT INTO `stock_roe_history` (
        `code`, `stat_date`, `pub_date`, `year`, `quarter`, `roe`
    ) VALUES (%s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        `roe` = VALUES(`roe`),
        `updated_at` = CURRENT_TIMESTAMP
    """
    
    args_list = []
    for row in batch_data:
        args_list.append((
            row['code'],
            row['stat_date'],
            row['pub_date'],
            row['year'],
            row['quarter'],
            row['roe']
        ))
    
    for attempt in range(1, 4):
        try:
            with conn.cursor() as cursor:
                cursor.executemany(sql, args_list)
            logger.log(f"  [DB] ✅ 批量插入 {len(batch_data)} 行成功")
            return conn
        except Exception as e:
            logger.log(f"  [DB] 批量插入失败 (尝试 {attempt}/3): {e}", "WARN")
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
    current_year = datetime.now().year
    
    logger.log("=" * 70)
    logger.log("  ROE 历史数据增量获取工具 (从 2020 年开始)")
    logger.log(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.log(f"  数据范围: {START_YEAR} 年 ~ {current_year} 年 (每季度)")
    logger.log(f"  请求间隔: {REQUEST_DELAY}秒/次")
    logger.log(f"  批量保存: 每 {BATCH_SIZE} 行")
    logger.log("=" * 70)
    
    total_start = time.time()
    
    total_processed = 0
    total_rows_fetched = 0
    total_skipped = 0
    total_failed = 0
    buffer = []
    
    # 建立数据库连接
    conn = None
    logger.start_step("建立数据库连接")
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
    logger.end_step("建立数据库连接")
    
    # 登录 Baostock
    logger.start_step("Baostock 登录")
    if not ensure_bs_login():
        logger.log("Baostock 登录失败，退出程序", "ERROR")
        if conn:
            conn.close()
        return
    logger.end_step("Baostock 登录")
    
    try:
        # 获取所有股票代码
        logger.start_step("获取股票列表")
        all_codes = get_all_stock_codes()
        if not all_codes:
            logger.log("无法获取股票列表，退出程序", "ERROR")
            return
        logger.end_step("获取股票列表")
        
        # 查询数据库中已有的数据
        logger.start_step("查询已存在的 ROE 数据")
        existing_data = get_existing_roe_data(conn)
        logger.end_step("查询已存在的 ROE 数据")
        
        # 开始获取数据
        logger.start_step(f"获取 ROE 数据")
        
        print("\n" + "-" * 95)
        print(f"{'序号':>6} {'股票代码':<12} {'状态':<10} {'新增行数':<8} {'总行数':<10} {'耗时(秒)':<10}")
        print("-" * 95)
        
        for idx, code in enumerate(all_codes):
            current_num = idx + 1
            stock_start = time.time()
            
            existing_dates = existing_data.get(code, set())
            missing_quarters = get_missing_quarters(code, existing_dates, current_year)
            
            if len(missing_quarters) == 0:
                total_skipped += 1
                elapsed = time.time() - stock_start
                # 每 50 只跳过才打印一次，减少日志
                if current_num % 50 == 0 or current_num <= 10:
                    logger.log_progress(current_num, len(all_codes), code, "skip", 0, total_rows_fetched, elapsed)
                continue
            
            # 获取缺失季度的数据
            fetched_rows = []
            for year, quarter in missing_quarters:
                data = fetch_quarter_roe(code, year, quarter)
                if data:
                    fetched_rows.append(data)
                    buffer.append(data)
                    total_rows_fetched += 1
                    
                    if len(buffer) >= BATCH_SIZE:
                        logger.log(f"  缓冲区已满 ({len(buffer)} 行)，执行批量插入...")
                        conn = flush_db_buffer(conn, buffer)
                        conn.commit()
                        buffer = []
            
            elapsed = time.time() - stock_start
            
            if len(fetched_rows) > 0:
                total_processed += 1
                logger.log_progress(current_num, len(all_codes), code, "success", len(fetched_rows), total_rows_fetched, elapsed)
            else:
                total_failed += 1
                logger.log_progress(current_num, len(all_codes), code, "fail", 0, total_rows_fetched, elapsed)
            
            # 每 N 只股票休息一下
            if current_num % SLEEP_INTERVAL == 0:
                sleep_time = random.uniform(SLEEP_DURATION[0], SLEEP_DURATION[1])
                logger.log(f"  休息 {sleep_time:.2f} 秒 (已处理 {current_num}/{len(all_codes)})")
                time.sleep(sleep_time)
        
        # 插入剩余数据
        if buffer:
            logger.log(f"\n刷新剩余数据到数据库 ({len(buffer)} 行)...")
            conn = flush_db_buffer(conn, buffer)
            conn.commit()
            buffer = []
        
        logger.end_step(f"获取 ROE 数据")
        
        # 打印统计
        total_time = time.time() - total_start
        logger.log("=" * 70)
        logger.log("📊 ROE 数据增量更新摘要")
        logger.log(f"  总股票数: {len(all_codes)}")
        logger.log(f"  ✅ 更新股票: {total_processed}")
        logger.log(f"  ⏭️ 跳过股票: {total_skipped} (数据已完整)")
        logger.log(f"  ❌ 失败股票: {total_failed}")
        logger.log(f"  📝 新增行数: {total_rows_fetched}")
        logger.log(f"  ⏱️ 总运行时间: {total_time:.2f} 秒 ({total_time/60:.2f} 分钟)")
        logger.log("=" * 70)
        
        logger.print_summary()
        
    except KeyboardInterrupt:
        logger.log("⚠️ 用户中断执行", "WARN")
        if buffer:
            logger.log(f"正在保存已获取的 {len(buffer)} 行数据...")
            try:
                conn = flush_db_buffer(conn, buffer)
                conn.commit()
                logger.log("数据已保存")
            except Exception as e:
                logger.log(f"保存失败: {e}", "ERROR")
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.log(f"运行时发生错误: {e}", "ERROR")
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