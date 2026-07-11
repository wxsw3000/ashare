import os
import sys
import time
import json
import numpy as np
import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.types import VARCHAR, DATE, DECIMAL
import pymysql

# ========== 配置 ==========
# 从环境变量读取数据库配置（Render 推荐方式）
DB_HOST = os.getenv("DB_HOST", "gateway01.ap-southeast-1.prod.aws.tidbcloud.com")
DB_PORT = int(os.getenv("DB_PORT", 4000))
DB_USER = os.getenv("DB_USER", "oQdmqAEE8zDH18E.root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "AA5n2cadUkt62LZJ")
DB_NAME = os.getenv("DB_NAME", "asharedb")
DB_SSL_CA = os.getenv("DB_SSL_CA", "/etc/ssl/cert.pem")

# 表名配置
TABLE_NAME = 'stock_operation_quarterly'
STOCK_BASIC_TABLE = 'stock_basic'

# 断点文件路径（Render 环境使用 /tmp/ 或项目目录）
CHECKPOINT_FILE = '/tmp/operation_data_checkpoint.json'

print("=" * 80)
print("📊 季频营运能力数据采集程序 (Render 版)")
print(f"⏰ 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 80)


def get_engine():
    """创建 TiDB Cloud 数据库连接引擎"""
    db_url = (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        f"?charset=utf8mb4"
    )
    
    # 如果 SSL CA 证书存在，添加 SSL 配置
    connect_args = {'connect_timeout': 30}
    if DB_SSL_CA and os.path.exists(DB_SSL_CA):
        connect_args['ssl'] = {'ca': DB_SSL_CA}
    
    engine = create_engine(db_url, connect_args=connect_args)
    return engine


# ========== 创建表 ==========
def create_table_if_not_exists(engine):
    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        code VARCHAR(20) NOT NULL COMMENT '证券代码',
        pubDate DATE COMMENT '公司发布财报的日期',
        statDate DATE COMMENT '财报统计季度最后一天',
        NRTurnRatio DECIMAL(20, 6) COMMENT '应收账款周转率(次)',
        NRTurnDays DECIMAL(20, 6) COMMENT '应收账款周转天数(天)',
        INVTurnRatio DECIMAL(20, 6) COMMENT '存货周转率(次)',
        INVTurnDays DECIMAL(20, 6) COMMENT '存货周转天数(天)',
        CATurnRatio DECIMAL(20, 6) COMMENT '流动资产周转率(次)',
        AssetTurnRatio DECIMAL(20, 6) COMMENT '总资产周转率(次)',
        year INT COMMENT '统计年份',
        quarter INT COMMENT '统计季度',
        update_date DATE DEFAULT NULL COMMENT '数据更新日期',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
        PRIMARY KEY (code, statDate, year, quarter)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='季频营运能力数据';
    """
    with engine.connect() as conn:
        conn.execute(text(create_sql))
        conn.commit()
    print("✅ 数据表创建成功（或已存在）")


# ========== 获取股票代码 ==========
def get_stock_codes_from_db(engine):
    try:
        query = f"""
        SELECT DISTINCT code 
        FROM {STOCK_BASIC_TABLE} 
        WHERE status = '1' AND type = '1'
        ORDER BY code
        """
        df = pd.read_sql(query, engine)
        stock_codes = df['code'].tolist()
        print(f"✅ 从数据库获取到 {len(stock_codes)} 只股票代码")
        return stock_codes
    except Exception as e:
        print(f"⚠️ 从数据库获取股票代码失败: {e}")
        return ['sh.600000', 'sh.600036', 'sh.600519', 'sh.601318']


# ========== 从 Baostock 获取数据 ==========
def fetch_operation_data(code, year, quarter):
    try:
        import baostock as bs
        rs = bs.query_operation_data(code=code, year=year, quarter=quarter)
        
        if rs.error_code != '0':
            return None
        
        data_list = []
        while rs.next():
            data_list.append(rs.get_row_data())
        
        if not data_list:
            return None
        
        df = pd.DataFrame(data_list, columns=rs.fields)
        df['year'] = year
        df['quarter'] = quarter
        df['update_date'] = datetime.now().strftime('%Y-%m-%d')
        
        if 'pubDate' in df.columns:
            df['pubDate'] = pd.to_datetime(df['pubDate'])
        if 'statDate' in df.columns:
            df['statDate'] = pd.to_datetime(df['statDate'])
        
        return df
    except Exception as e:
        return None


# ========== 保存数据 ==========
def save_with_upsert(df, engine, table_name=TABLE_NAME):
    if df is None or df.empty:
        return 0
    
    df = df.replace([np.nan, pd.NA, float('nan'), ''], None)
    
    fields = ['code', 'pubDate', 'statDate', 'NRTurnRatio', 'NRTurnDays', 
              'INVTurnRatio', 'INVTurnDays', 'CATurnRatio', 'AssetTurnRatio', 
              'year', 'quarter', 'update_date']
    
    existing_fields = [f for f in fields if f in df.columns]
    placeholders = ', '.join([':' + f for f in existing_fields])
    fields_str = ', '.join(existing_fields)
    
    update_fields = [f for f in existing_fields if f not in ['code', 'statDate', 'year', 'quarter']]
    update_clause = ', '.join([f"{f} = VALUES({f})" for f in update_fields])
    
    sql = f"""
    INSERT INTO {table_name} ({fields_str})
    VALUES ({placeholders})
    ON DUPLICATE KEY UPDATE {update_clause}
    """
    
    records = df[existing_fields].to_dict('records')
    
    for record in records:
        for key, value in record.items():
            if isinstance(value, (pd.Timestamp, datetime)):
                record[key] = value.strftime('%Y-%m-%d')
    
    try:
        with engine.connect() as conn:
            for i in range(0, len(records), 100):
                batch = records[i:i+100]
                conn.execute(text(sql), batch)
            conn.commit()
        return len(records)
    except Exception as e:
        print(f"  ❌ 保存失败：{e}")
        return 0


# ========== 断点续传 ==========
def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_checkpoint(checkpoint):
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump(checkpoint, f)


# ========== 格式化时间 ==========
def format_time(seconds):
    if seconds < 60:
        return f"{seconds:.0f}秒"
    elif seconds < 3600:
        return f"{int(seconds//60)}分{int(seconds%60)}秒"
    else:
        return f"{int(seconds//3600)}小时{int((seconds%3600)//60)}分"


# ========== 主程序 ==========
def main():
    # 导入 baostock（在函数内导入，便于处理异常）
    try:
        import baostock as bs
    except ImportError:
        print("❌ baostock 未安装，请运行: pip install baostock")
        return
    
    engine = get_engine()
    
    # 测试连接
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("✅ 数据库连接成功！")
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        return
    
    create_table_if_not_exists(engine)
    
    print("🔑 正在登录 BaoStock...")
    lg = bs.login()
    if lg.error_code != '0':
        print(f"❌ 登录失败：{lg.error_msg}")
        return
    print(f"✅ BaoStock 登录成功")
    
    try:
        checkpoint = load_checkpoint()
        processed_keys = set(checkpoint.get('processed', []))
        
        if processed_keys:
            print(f"🔄 检测到断点记录，已处理 {len(processed_keys)} 个任务")
        
        stock_codes = get_stock_codes_from_db(engine)
        if not stock_codes:
            print("⚠️ 未获取到股票代码，使用备用列表")
            stock_codes = ['sh.600000', 'sz.000001']
        
        current_year = datetime.now().year
        years = list(range(2020, current_year + 1))
        quarters = [1, 2, 3, 4]
        
        total_tasks = len(stock_codes) * len(years) * len(quarters)
        completed_tasks = len(processed_keys)
        total_records = checkpoint.get('total_records', 0)
        success_tasks = checkpoint.get('success_tasks', 0)
        empty_tasks = checkpoint.get('empty_tasks', 0)
        fail_tasks = checkpoint.get('fail_tasks', 0)
        
        start_time = time.time() - checkpoint.get('elapsed_seconds', 0)
        
        print(f"\n📈 总任务数: {total_tasks} | 已完成: {completed_tasks}")
        print(f"   - 股票数量: {len(stock_codes)}")
        print(f"   - 年份范围: {min(years)}-{max(years)}")
        print("\n开始采集...")
        print("-" * 80)
        
        for idx, code in enumerate(stock_codes, 1):
            for year in years:
                for quarter in quarters:
                    task_key = f"{code}_{year}_{quarter}"
                    
                    if task_key in processed_keys:
                        continue
                    
                    df = fetch_operation_data(code, year, quarter)
                    
                    if df is not None and not df.empty:
                        saved = save_with_upsert(df, engine)
                        if saved > 0:
                            total_records += saved
                            success_tasks += 1
                        else:
                            fail_tasks += 1
                    else:
                        empty_tasks += 1
                    
                    processed_keys.add(task_key)
                    completed_tasks += 1
                    
                    checkpoint = {
                        'processed': list(processed_keys),
                        'total_records': total_records,
                        'success_tasks': success_tasks,
                        'empty_tasks': empty_tasks,
                        'fail_tasks': fail_tasks,
                        'elapsed_seconds': time.time() - start_time
                    }
                    save_checkpoint(checkpoint)
                    
                    if completed_tasks % 50 == 0 or completed_tasks == total_tasks:
                        elapsed = time.time() - start_time
                        progress = (completed_tasks / total_tasks) * 100
                        print(f"📊 进度: {progress:.1f}% | {completed_tasks}/{total_tasks} | "
                              f"✅{success_tasks} ⚠️{empty_tasks} ❌{fail_tasks} | "
                              f"记录:{total_records} | 耗时:{format_time(elapsed)}")
        
        print("\n" + "=" * 80)
        print("🎉 全部处理完成！")
        print(f"   ✅ 成功保存: {total_records} 条记录")
        print(f"   ✅ 成功任务: {success_tasks} | ⚠️ 无数据: {empty_tasks} | ❌ 失败: {fail_tasks}")
        print("=" * 80)
        
        if os.path.exists(CHECKPOINT_FILE):
            os.remove(CHECKPOINT_FILE)
            
    except Exception as e:
        print(f"❌ 运行失败: {e}")
    finally:
        bs.logout()
        print("✅ BaoStock 登出完成")


if __name__ == "__main__":
    main()