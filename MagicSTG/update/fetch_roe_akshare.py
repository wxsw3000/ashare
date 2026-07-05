import os
import sys
import time
import random
from datetime import datetime
import pandas as pd
import pymysql

# ========== 配置 ==========
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BATCH_SIZE = 200
START_YEAR = "2020"
REQUEST_DELAY = 0.3
SLEEP_INTERVAL = 20
SLEEP_DURATION = (0.5, 1.5)
TEST_MODE = True  # True=测试5只, False=全量
TEST_COUNT = 5

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


def get_existing_roe_data(conn):
    """查询数据库中已有的 ROE 数据"""
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


def get_all_stock_codes():
    """获取所有 A 股股票代码"""
    try:
        import akshare as ak
        logger.log("正在从 AkShare 获取股票列表...")
        df = ak.stock_info_a_code_name()
        codes = df['code'].tolist()
        formatted_codes = []
        for code in codes:
            if code.startswith('6'):
                formatted_codes.append(f"sh.{code}")
            else:
                formatted_codes.append(f"sz.{code}")
        logger.log(f"获取到 {len(formatted_codes)} 只股票")
        return formatted_codes
    except Exception as e:
        logger.log(f"获取股票列表失败: {e}", "ERROR")
        return []


def fetch_roe_akshare(code, start_year=START_YEAR):
    """
    使用 AkShare 获取单只股票的 ROE 历史数据
    """
    try:
        import akshare as ak
        
        pure_code = code.replace('sh.', '').replace('sz.', '')
        
        time.sleep(REQUEST_DELAY)
        
        df = ak.stock_financial_analysis_indicator(symbol=pure_code, start_year=start_year)
        
        if df.empty:
            return []
        
        results = []
        
        # ROE 列名
        roe_col = '净资产收益率(%)'
        if roe_col not in df.columns:
            # 尝试备选列名
            for col in df.columns:
                if '净资产收益率' in col:
                    roe_col = col
                    break
            else:
                return []
        
        for _, row in df.iterrows():
            try:
                report_date = row.get('日期', None)
                if not report_date or pd.isna(report_date):
                    continue
                
                dt = datetime.strptime(str(report_date), '%Y-%m-%d')
                year = dt.year
                
                if year < int(start_year):
                    continue
                
                roe_val_raw = row.get(roe_col)
                if roe_val_raw is None or pd.isna(roe_val_raw):
                    continue
                
                # ROE 已经是百分比数值（如 -0.38 表示 -0.38%）
                roe_val = float(roe_val_raw) / 100
                
                # 计算季度
                month = dt.month
                if month <= 3:
                    quarter = 1
                elif month <= 6:
                    quarter = 2
                elif month <= 9:
                    quarter = 3
                else:
                    quarter = 4
                
                results.append({
                    'code': code,
                    'stat_date': str(report_date),
                    'pub_date': None,
                    'year': year,
                    'quarter': quarter,
                    'roe': roe_val
                })
                
            except Exception as e:
                continue
        
        return results
        
    except Exception as e:
        logger.log(f"  [ERROR] {code} 获取失败: {e}", "ERROR")
        return []


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
    logger.log("=" * 70)
    logger.log("  ROE 历史数据获取工具 (AkShare 版本)")
    logger.log(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.log(f"  数据范围: {START_YEAR} 年至今")
    logger.log(f"  测试模式: {TEST_MODE}")
    if TEST_MODE:
        logger.log(f"  测试数量: {TEST_COUNT} 只")
    logger.log("=" * 70)
    
    total_start = time.time()
    total_processed = 0
    total_rows_fetched = 0
    total_skipped = 0
    total_failed = 0
    buffer = []
    
    # 检查 AkShare
    try:
        import akshare as ak
        logger.log(f"✅ AkShare 版本: {ak.__version__}")
    except ImportError:
        logger.log("❌ AkShare 未安装", "ERROR")
        return
    
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
    
    try:
        # 获取股票列表
        logger.start_step("获取股票列表")
        all_codes = get_all_stock_codes()
        if not all_codes:
            logger.log("无法获取股票列表，退出程序", "ERROR")
            return
        logger.end_step("获取股票列表")
        
        # 查询已有数据
        logger.start_step("查询已存在的 ROE 数据")
        existing_data = get_existing_roe_data(conn)
        logger.end_step("查询已存在的 ROE 数据")
        
        # 确定要处理的股票列表
        if TEST_MODE:
            codes_to_fetch = all_codes[:TEST_COUNT]
            logger.log(f"🧪 测试模式：处理前 {TEST_COUNT} 只股票")
        else:
            # 全量模式：只处理没有完整数据的股票
            codes_to_fetch = []
            for code in all_codes:
                existing_dates = existing_data.get(code, set())
                if len(existing_dates) < 4 * (datetime.now().year - int(START_YEAR) + 1):
                    codes_to_fetch.append(code)
            logger.log(f"需要获取 ROE 数据的股票: {len(codes_to_fetch)} 只")
        
        if len(codes_to_fetch) == 0:
            logger.log("✅ 所有数据已完整，无需更新")
            return
        
        # 开始获取数据
        logger.start_step(f"获取 {len(codes_to_fetch)} 只股票的 ROE 数据")
        
        print("\n" + "-" * 95)
        print(f"{'序号':>6} {'股票代码':<12} {'状态':<10} {'新增行数':<8} {'总行数':<10} {'耗时(秒)':<10}")
        print("-" * 95)
        
        for idx, code in enumerate(codes_to_fetch):
            current_num = idx + 1
            stock_start = time.time()
            
            existing_dates = existing_data.get(code, set())
            
            data_list = fetch_roe_akshare(code)
            elapsed = time.time() - stock_start
            
            if data_list and len(data_list) > 0:
                new_rows = [r for r in data_list if r['stat_date'] not in existing_dates]
                
                for row in new_rows:
                    buffer.append(row)
                    total_rows_fetched += 1
                    
                    if len(buffer) >= BATCH_SIZE:
                        logger.log(f"  缓冲区已满 ({len(buffer)} 行)，执行批量插入...")
                        conn = flush_db_buffer(conn, buffer)
                        conn.commit()
                        buffer = []
                
                if len(new_rows) > 0:
                    total_processed += 1
                    logger.log_progress(current_num, len(codes_to_fetch), code, "success", len(new_rows), total_rows_fetched, elapsed)
                else:
                    total_skipped += 1
                    logger.log_progress(current_num, len(codes_to_fetch), code, "skip", 0, total_rows_fetched, elapsed)
            else:
                total_failed += 1
                logger.log_progress(current_num, len(codes_to_fetch), code, "fail", 0, total_rows_fetched, elapsed)
            
            if current_num % SLEEP_INTERVAL == 0:
                sleep_time = random.uniform(SLEEP_DURATION[0], SLEEP_DURATION[1])
                logger.log(f"  休息 {sleep_time:.2f} 秒 (已处理 {current_num}/{len(codes_to_fetch)})")
                time.sleep(sleep_time)
        
        # 保存剩余数据
        if buffer:
            logger.log(f"\n刷新剩余数据到数据库 ({len(buffer)} 行)...")
            conn = flush_db_buffer(conn, buffer)
            conn.commit()
            buffer = []
        
        logger.end_step(f"获取 {len(codes_to_fetch)} 只股票的 ROE 数据")
        
        # 统计
        total_time = time.time() - total_start
        logger.log("=" * 70)
        logger.log("📊 ROE 数据获取摘要")
        logger.log(f"  处理股票数: {len(codes_to_fetch)}")
        logger.log(f"  ✅ 成功: {total_processed}")
        logger.log(f"  ⏭️ 跳过: {total_skipped}")
        logger.log(f"  ❌ 失败: {total_failed}")
        logger.log(f"  📝 新增行数: {total_rows_fetched}")
        logger.log(f"  ⏱️ 总运行时间: {total_time:.2f} 秒 ({total_time/60:.2f} 分钟)")
        logger.log("=" * 70)
        
        if TEST_MODE and total_processed > 0:
            logger.log("✅ 测试通过！将 TEST_MODE = False 即可全量运行")
        
        logger.print_summary()
        
    except KeyboardInterrupt:
        logger.log("⚠️ 用户中断", "WARN")
        if buffer:
            try:
                conn = flush_db_buffer(conn, buffer)
                conn.commit()
            except:
                pass
    except Exception as e:
        logger.log(f"运行时错误: {e}", "ERROR")
    finally:
        if conn:
            conn.close()
            logger.log("数据库连接已关闭")


if __name__ == "__main__":
    main()