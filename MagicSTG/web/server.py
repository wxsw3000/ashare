import os
import pymysql
import pandas as pd
from flask import Flask, render_template, jsonify, request
from datetime import datetime
from dotenv import load_dotenv

app = Flask(__name__)

# ========== 加载环境变量 ==========
# 获取项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(PROJECT_ROOT, 'dbconfig', '.env')

# 加载 .env 文件
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)
    print(f"[ENV] 加载 .env 文件: {ENV_PATH}")
else:
    print(f"[ENV] ⚠️ 未找到 .env 文件: {ENV_PATH}")


# ========== 数据库连接配置 ==========
def get_db_connection():
    """获取数据库连接，自动适配本地和 Render 环境"""
    
    # 从环境变量读取数据库配置
    db_host = os.getenv("DB_HOST")
    db_port = int(os.getenv("DB_PORT", 4000))
    db_user = os.getenv("DB_USER")
    db_password = os.getenv("DB_PASSWORD")
    db_name = os.getenv("DB_NAME")
    db_ssl_ca = os.getenv("DB_SSL_CA")
    
    # 打印配置信息（隐藏密码）
    print(f"[DB] Host: {db_host}, Port: {db_port}, User: {db_user}, Database: {db_name}")
    
    # 判断是否在 Render 环境
    is_render = os.environ.get('PORT') is not None
    
    # 根据环境选择 SSL CA 路径
    if is_render:
        ssl_ca = "/etc/ssl/cert.pem"
        print(f"[DB] Render 环境，使用系统 CA: {ssl_ca}")
    else:
        # 本地环境
        if db_ssl_ca and os.path.exists(db_ssl_ca):
            ssl_ca = db_ssl_ca
            print(f"[DB] 本地环境，使用 CA: {ssl_ca}")
        else:
            # 尝试在常见位置查找
            filename = os.path.basename(db_ssl_ca) if db_ssl_ca else "isrgrootx1.pem"
            for path_candidate in [
                os.path.join(PROJECT_ROOT, 'dbconfig', filename),
                os.path.join(PROJECT_ROOT, filename),
            ]:
                if os.path.exists(path_candidate):
                    ssl_ca = path_candidate
                    print(f"[DB] 本地环境，使用 CA: {ssl_ca}")
                    break
            else:
                # 如果都找不到，尝试系统证书
                if os.path.exists("/etc/ssl/cert.pem"):
                    ssl_ca = "/etc/ssl/cert.pem"
                else:
                    ssl_ca = None
                    print("[DB] ⚠️ 未找到 SSL CA 证书")
    
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
    if ssl_ca and os.path.exists(ssl_ca):
        conn_params["ssl"] = {
            "ca": ssl_ca,
            "verify_cert": True,
            "verify_identity": True
        }
        print("[DB] SSL 连接已启用（证书验证）")
    else:
        # 如果找不到证书，尝试跳过验证
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


@app.route('/api/recommendations')
def get_recommendations():
    """获取每日推荐信号"""
    strategy = request.args.get('strategy', 'price')
    date = request.args.get('date', None)
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        if date is None:
            cursor.execute("""
                SELECT MAX(signal_date) FROM recommendations WHERE strategy = %s
            """, (strategy,))
            row = cursor.fetchone()
            if row and row[0]:
                date = row[0].strftime('%Y-%m-%d')
            else:
                return jsonify({'status': 'error', 'message': 'No data found'})
        
        cursor.execute("""
            SELECT stock_code, action, price, reason, signal_date
            FROM recommendations
            WHERE strategy = %s AND signal_date = %s
            ORDER BY action, stock_code
        """, (strategy, date))
        rows = cursor.fetchall()
        
        recommendations = []
        for row in rows:
            recommendations.append({
                'code': row[0],
                'action': row[1],
                'price': float(row[2]) if row[2] else None,
                'reason': row[3],
                'date': row[4].strftime('%Y-%m-%d') if row[4] else None
            })
        
        cursor.execute("""
            SELECT DISTINCT signal_date FROM recommendations 
            WHERE strategy = %s 
            ORDER BY signal_date DESC 
            LIMIT 30
        """, (strategy,))
        date_rows = cursor.fetchall()
        available_dates = [row[0].strftime('%Y-%m-%d') for row in date_rows]
        
        return jsonify({
            'status': 'success',
            'strategy': strategy,
            'date': date,
            'available_dates': available_dates,
            'data': recommendations
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/positions')
def get_positions():
    """获取当前持仓数据"""
    strategy = request.args.get('strategy', 'price')
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT stock_code, buy_date, buy_price, shares, cost_total, 
                   current_price, market_value, pnl, pnl_pct, status
            FROM positions
            WHERE strategy = %s AND status = 'HOLDING'
            ORDER BY stock_code
        """, (strategy,))
        rows = cursor.fetchall()
        
        positions = []
        total_market_value = 0
        total_pnl = 0
        
        for row in rows:
            pos = {
                'code': row[0],
                'buy_date': row[1].strftime('%Y-%m-%d') if row[1] else None,
                'buy_price': float(row[2]) if row[2] else 0,
                'shares': int(row[3]) if row[3] else 0,
                'cost_total': float(row[4]) if row[4] else 0,
                'current_price': float(row[5]) if row[5] else 0,
                'market_value': float(row[6]) if row[6] else 0,
                'pnl': float(row[7]) if row[7] else 0,
                'pnl_pct': float(row[8]) if row[8] else 0,
                'status': row[9]
            }
            positions.append(pos)
            total_market_value += pos['market_value']
            total_pnl += pos['pnl']
        
        cursor.execute("""
            SELECT SUM(cost_total) FROM positions WHERE strategy = %s AND status = 'HOLDING'
        """, (strategy,))
        row = cursor.fetchone()
        initial_capital = 40000
        
        return jsonify({
            'status': 'success',
            'strategy': strategy,
            'data': {
                'positions': positions,
                'summary': {
                    'initial_capital': initial_capital,
                    'current_equity': initial_capital + total_pnl,
                    'total_market_value': total_market_value,
                    'holding_count': len(positions),
                    'max_holdings': 4,
                    'total_pnl': total_pnl,
                    'total_pnl_pct': (total_pnl / initial_capital * 100) if initial_capital > 0 else 0
                },
                'cash_details': {
                    'total_cash': initial_capital - total_market_value
                }
            }
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/backtests')
def get_backtests():
    """获取回测报告列表"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, strategy, run_date, date_range_start, date_range_end,
                   total_return, annual_return, max_drawdown, win_rate
            FROM backtest_results
            ORDER BY run_date DESC
            LIMIT 20
        """)
        rows = cursor.fetchall()
        
        backtests = []
        for row in rows:
            backtests.append({
                'id': row[0],
                'strategy': row[1],
                'run_date': row[2].strftime('%Y-%m-%d') if row[2] else None,
                'date_range': f"{row[3].strftime('%Y-%m-%d') if row[3] else 'N/A'} ~ {row[4].strftime('%Y-%m-%d') if row[4] else 'N/A'}",
                'total_return': float(row[5]) if row[5] else 0,
                'annual_return': float(row[6]) if row[6] else 0,
                'max_drawdown': float(row[7]) if row[7] else 0,
                'win_rate': float(row[8]) if row[8] else 0
            })
        
        return jsonify({'status': 'success', 'data': backtests})
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/backtest/trades/<int:backtest_id>')
def get_backtest_trades(backtest_id):
    """获取回测成交明细"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT trade_date, stock_code, action, price, shares, fee, pnl, pnl_pct, reason
            FROM backtest_trades
            WHERE backtest_id = %s
            ORDER BY trade_date
            LIMIT 100
        """, (backtest_id,))
        rows = cursor.fetchall()
        
        trades = []
        for row in rows:
            trades.append({
                'date': row[0].strftime('%Y-%m-%d') if row[0] else None,
                'code': row[1],
                'action': row[2],
                'price': float(row[3]) if row[3] else 0,
                'shares': int(row[4]) if row[4] else 0,
                'fee': float(row[5]) if row[5] else 0,
                'pnl': float(row[6]) if row[6] else None,
                'pnl_pct': float(row[7]) if row[7] else None,
                'reason': row[8]
            })
        
        return jsonify({'status': 'success', 'data': trades})
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)