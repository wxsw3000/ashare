import os
import sys
import pymysql
import pandas as pd
from flask import Flask, render_template, jsonify, request
from datetime import datetime
from dotenv import load_dotenv

app = Flask(__name__)

# ========== 加载环境变量 ==========
# 获取项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
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


@app.route('/api/backtest', methods=['POST'])
def run_dynamic_backtest():
    """执行自定义因子的动态策略回测"""
    data = request.json or {}
    start_date_str = data.get('start_date', '2024-01-01')
    end_date_str = data.get('end_date', '2026-06-30')
    universe = data.get('universe', 'csi300') # 'csi300' or 'all'
    config = data.get('config', {})
    
    # 补齐策略核心参数
    if 'strategy' not in config:
        config['strategy'] = {}
    config['strategy']['max_holdings'] = int(config['strategy'].get('max_holdings', 4))
    config['strategy']['per_stock_capital'] = float(config['strategy'].get('per_stock_capital', 10000.0))
    config['strategy']['buy_di_threshold'] = float(config.get('tech', {}).get('buy_di_threshold', 0.70))
    config['strategy']['sell_di_threshold'] = float(config.get('tech', {}).get('sell_di_threshold', 0.70))
    config['strategy']['short_ma'] = int(config.get('tech', {}).get('short_ma', 5))
    config['strategy']['long_ma'] = int(config.get('tech', {}).get('long_ma', 20))
    config['strategy']['volume_surge_factor'] = float(config.get('tech', {}).get('volume_surge_factor', 1.2))
    
    if 'paths' not in config:
        config['paths'] = {}
    config['paths']['position_file'] = 'positions/position_backtest_temp.csv'
    
    if 'risk' not in config:
        config['risk'] = {}
    config['risk']['stop_loss'] = float(config['risk'].get('stop_loss', -15.0))
    config['risk']['enable_stop_loss'] = bool(config['risk'].get('enable_stop_loss', False))
    
    if 'fee' not in config:
        config['fee'] = {
            'commission_rate': 0.0001,
            'min_commission': 5.0,
            'stamp_tax_rate': 0.0005
        }
    
    try:
        # 1. 加载 K 线数据
        limit_to_csi300 = (universe == 'csi300')
        from utils.db import load_all_data_db
        print(f"[API Backtest] 开始加载 K 线数据, universe={universe}, range={start_date_str} ~ {end_date_str}...", flush=True)
        all_data = load_all_data_db(start_date=start_date_str, end_date=end_date_str, limit_to_csi300=limit_to_csi300)
        if not all_data:
            return jsonify({'status': 'error', 'message': '未加载到任何 K 线数据'})
        
        # 2. 初始化 dynamic 信号生成器
        from signals.generator_dynamic import SignalGeneratorDynamic
        generator = SignalGeneratorDynamic(config)
        
        # 3. 预计算技术指标并缓存
        processed_data = {}
        for code, df in all_data.items():
            processed_data[code] = generator.compute_indicators(df)
            
        # 4. 获取交易日期范围
        start_date = pd.Timestamp(start_date_str)
        end_date = pd.Timestamp(end_date_str)
        all_dates = sorted(set().union(*[set(df.index) for df in all_data.values()]))
        backtest_dates = [d for d in all_dates if start_date <= d <= end_date]
        if not backtest_dates:
            return jsonify({'status': 'error', 'message': '所选时间段内没有交易日期'})
            
        # 5. 初始化仓位管理器与决策器
        from positions.manager import PositionManager
        from decisions.maker import DecisionMaker
        
        class InMemoryPositionManager(PositionManager):
            def _load(self):
                pass
            def save(self):
                pass
                
        position_manager = InMemoryPositionManager(config)
        position_manager.positions = []
        position_manager.cash_remains = {}
        max_holdings = config['strategy']['max_holdings']
        per_stock_capital = config['strategy']['per_stock_capital']
        initial_equity = max_holdings * per_stock_capital
        position_manager.slot_cash = [float(per_stock_capital)] * max_holdings
        position_manager.realized_pnl = 0.0
        
        decision_maker = DecisionMaker(config, position_manager, generator)
        
        # 6. 回测循环
        trade_log = []
        equity_history = []
        
        from utils.cost import calc_buy_cost, calc_sell_cost
        
        for date in backtest_dates:
            # 更新持有期最高价
            for pos in position_manager.get_positions():
                code = pos.code
                df = processed_data[code]
                if date in df.index:
                    high_price = df.loc[date, 'high']
                    if not pd.isna(high_price):
                        position_manager.update_highest_price(code, high_price)
                        
            # 决策
            decisions = decision_maker.make_decisions(all_data, date)
            
            # 卖出
            for sell in decisions.sells:
                code = sell['code']
                pos = position_manager.get_position(code)
                if not pos:
                    continue
                sell_amount = sell['shares'] * sell['price']
                fee = calc_sell_cost(sell_amount)
                net_sell = sell_amount - fee
                pnl = net_sell - pos.cost_total
                position_manager.remove_position(code, sell['price'], fee)
                
                trade_log.append({
                    'date': date.strftime('%Y-%m-%d'),
                    'code': code,
                    'action': 'SELL',
                    'price': sell['price'],
                    'shares': sell['shares'],
                    'amount': sell_amount,
                    'fee': fee,
                    'pnl': pnl,
                    'pnl_pct': (pnl / pos.cost_total * 100) if pos.cost_total > 0 else 0,
                    'reason': sell['reason']
                })
                
            # 买入
            for buy in decisions.buys:
                code = buy['code']
                position_manager.add_position(code, buy['price'], buy['shares'], buy['total'])
                
                trade_log.append({
                    'date': date.strftime('%Y-%m-%d'),
                    'code': code,
                    'action': 'BUY',
                    'price': buy['price'],
                    'shares': buy['shares'],
                    'amount': buy['amount'],
                    'fee': buy['fee'],
                    'pnl': 0.0,
                    'pnl_pct': 0.0,
                    'reason': '买入信号'
                })
                
            # 日终资产净值
            current_prices = {}
            for pos in position_manager.get_positions():
                code = pos.code
                df = processed_data[code]
                if date in df.index:
                    current_prices[code] = df.loc[date, 'close']
                else:
                    current_prices[code] = pos.buy_price
            total_equity = position_manager.get_total_equity(current_prices)
            equity_history.append((date.strftime('%Y-%m-%d'), total_equity))
            
        # 7. 计算指标
        if not equity_history:
            return jsonify({'status': 'error', 'message': '未生成任何资产净值曲线'})
            
        final_equity = equity_history[-1][1]
        total_return = (final_equity - initial_equity) / initial_equity * 100
        
        total_days = len(backtest_dates)
        years = total_days / 242.0
        annual_return = ((final_equity / initial_equity) ** (1.0 / years) - 1) * 100 if (years > 0 and final_equity > 0) else 0.0
        
        max_drawdown = 0.0
        peak = initial_equity
        for date_str, eq in equity_history:
            if eq > peak:
                peak = eq
            dd = (eq - peak) / peak * 100
            if dd < max_drawdown:
                max_drawdown = dd
                
        buys_count = sum(1 for t in trade_log if t['action'] == 'BUY')
        completed_trades = [t for t in trade_log if t['action'] == 'SELL']
        win_count = sum(1 for t in completed_trades if t['pnl'] > 0)
        win_rate = win_count / len(completed_trades) * 100 if completed_trades else 0.0
        total_fees = sum(t['fee'] for t in trade_log)
        
        return jsonify({
            'status': 'success',
            'summary': {
                'initial_equity': initial_equity,
                'final_equity': final_equity,
                'total_return': round(total_return, 2),
                'annual_return': round(annual_return, 2),
                'max_drawdown': round(max_drawdown, 2),
                'win_rate': round(win_rate, 2),
                'trades_count': len(trade_log),
                'buys_count': buys_count,
                'sells_count': len(completed_trades),
                'total_fees': round(total_fees, 2)
            },
            'equity_history': equity_history,
            'trade_log': trade_log
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': f'回测执行失败: {str(e)}'})


if __name__ == '__main__':
    # 获取 Render 等云托管平台分配的端口，默认为 5000
    port = int(os.environ.get("PORT", 5000))
    # 生产环境建议关闭 debug 模式，可通过 FLASK_ENV=development 开启
    debug_mode = os.environ.get("FLASK_ENV") == "development"
    app.run(debug=debug_mode, host='0.0.0.0', port=port)