import os
import pymysql
import pandas as pd
from flask import Flask, render_template, jsonify, request
from datetime import datetime

app = Flask(__name__)

# ========== 数据库连接配置 ==========
def get_db_connection():
    """获取数据库连接，自动适配本地和 Render 环境"""
    
    # 判断是否在 Render 环境（Render 会设置 PORT 环境变量）
    is_render = os.environ.get('PORT') is not None
    
    # 从环境变量读取数据库配置
    db_host = os.environ.get('DB_HOST')
    db_port = int(os.environ.get('DB_PORT', 4000))
    db_user = os.environ.get('DB_USER')
    db_password = os.environ.get('DB_PASSWORD')
    db_name = os.environ.get('DB_NAME')
    
    # 根据环境选择 SSL CA 路径
    if is_render:
        # Render Linux 系统自带的 CA 证书
        ssl_ca = "/etc/ssl/cert.pem"
        print(f"[DB] Render 环境，使用系统 CA: {ssl_ca}")
    else:
        # 本地 Windows 环境，使用项目中的证书
        # 获取当前文件所在目录
        current_dir = os.path.dirname(os.path.abspath(__file__))
        ssl_ca = os.path.join(current_dir, '..', 'dbconfig', 'isrgrootx1.pem')
        if not os.path.exists(ssl_ca):
            # 如果找不到，尝试绝对路径
            ssl_ca = "E:/ashare/MagicSTG/dbconfig/isrgrootx1.pem"
        print(f"[DB] 本地环境，使用 CA: {ssl_ca}")
    
    conn_params = {
        "host": db_host,
        "port": db_port,
        "user": db_user,
        "password": db_password,
        "database": db_name,
        "charset": "utf8mb4",
        "connect_timeout": 15,
        "read_timeout": 60,
    }
    
    # 添加 SSL 配置
    if os.path.exists(ssl_ca):
        conn_params["ssl"] = {
            "ca": ssl_ca,
            "verify_cert": True,
            "verify_identity": True
        }
        print("[DB] SSL 连接已启用（证书验证）")
    else:
        # 如果找不到证书，尝试跳过验证（仅用于测试）
        conn_params["ssl"] = {
            "verify_cert": False,
            "verify_identity": False
        }
        print("[DB] ⚠️ SSL 连接已启用（跳过证书验证）")
    
    return pymysql.connect(**conn_params)


# ========== 路由 ==========
@app.route('/')
def index():
    """首页"""
    return render_template('index.html')


@app.route('/api/stocks')
def get_stocks():
    """获取股票列表"""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT code, MAX(date) as last_date 
                FROM stock_kline 
                GROUP BY code 
                ORDER BY code 
                LIMIT 100
            """)
            rows = cur.fetchall()
        conn.close()
        
        stocks = [{'code': row[0], 'last_date': row[1].strftime('%Y-%m-%d') if row[1] else None} for row in rows]
        return jsonify({'status': 'success', 'data': stocks})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/api/kline/<code>')
def get_kline(code):
    """获取某只股票的 K 线数据"""
    limit = request.args.get('limit', 100, type=int)
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, open, close, high, low, volume 
                FROM stock_kline 
                WHERE stock_code = %s 
                ORDER BY date DESC 
                LIMIT %s
            """, (code.replace('.', '_'), limit))
            rows = cur.fetchall()
        conn.close()
        
        # 按日期升序排列（前端图表通常需要升序）
        data = [{
            'date': row[0].strftime('%Y-%m-%d'),
            'open': float(row[1]) if row[1] else None,
            'close': float(row[2]) if row[2] else None,
            'high': float(row[3]) if row[3] else None,
            'low': float(row[4]) if row[4] else None,
            'volume': int(row[5]) if row[5] else None
        } for row in rows]
        data.reverse()
        
        return jsonify({'status': 'success', 'data': data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/api/roe/<code>')
def get_roe(code):
    """获取某只股票的 ROE 历史数据"""
    limit = request.args.get('limit', 20, type=int)
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT stat_date, year, quarter, roe 
                FROM stock_roe_history 
                WHERE code = %s 
                ORDER BY stat_date DESC 
                LIMIT %s
            """, (code, limit))
            rows = cur.fetchall()
        conn.close()
        
        data = [{
            'stat_date': row[0].strftime('%Y-%m-%d') if row[0] else None,
            'year': row[1],
            'quarter': row[2],
            'roe': float(row[3]) if row[3] else None
        } for row in rows]
        data.reverse()
        
        return jsonify({'status': 'success', 'data': data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/api/dashboard')
def get_dashboard():
    """获取仪表盘数据"""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # 总股票数
            cur.execute("SELECT COUNT(DISTINCT stock_code) FROM stock_kline")
            total_stocks = cur.fetchone()[0]
            
            # 总数据行数
            cur.execute("SELECT COUNT(*) FROM stock_kline")
            total_rows = cur.fetchone()[0]
            
            # 最新日期
            cur.execute("SELECT MAX(date) FROM stock_kline")
            latest_date = cur.fetchone()[0]
            
            # ROE 数据统计
            cur.execute("SELECT COUNT(DISTINCT code) FROM stock_roe_history")
            roe_stocks = cur.fetchone()[0]
            
        conn.close()
        
        return jsonify({
            'status': 'success',
            'data': {
                'total_stocks': total_stocks,
                'total_rows': total_rows,
                'latest_date': latest_date.strftime('%Y-%m-%d') if latest_date else None,
                'roe_stocks': roe_stocks
            }
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)