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


# ========== 策略管理 API (v1.0 架构) ==========
@app.route('/api/strategies', methods=['GET'])
def get_strategies():
    """获取策略列表（支持搜索名称及分类筛选）"""
    search = request.args.get('search', '').strip()
    category = request.args.get('category', '').strip()

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        query = "SELECT id, strategy_id, name, category, description, factors_config, buy_signals_rule, sell_signals_rule, created_at, updated_at FROM custom_strategies WHERE 1=1"
        params = []

        if search:
            query += " AND (name LIKE %s OR strategy_id LIKE %s)"
            params.extend([f"%{search}%", f"%{search}%"])
        if category and category != 'all':
            query += " AND category = %s"
            params.append(category)

        query += " ORDER BY id ASC"
        cursor.execute(query, params)
        rows = cursor.fetchall()

        import json
        strategies = []
        for r in rows:
            factors_cfg = r[5]
            if isinstance(factors_cfg, str):
                try:
                    factors_cfg = json.loads(factors_cfg)
                except Exception:
                    pass

            strategies.append({
                'id': r[0],
                'strategy_id': r[1],
                'name': r[2],
                'category': r[3],
                'description': r[4],
                'factors_config': factors_cfg,
                'buy_signals_rule': r[6],
                'sell_signals_rule': r[7],
                'created_at': r[8].strftime('%Y-%m-%d %H:%M:%S') if r[8] else None,
                'updated_at': r[9].strftime('%Y-%m-%d %H:%M:%S') if r[9] else None
            })

        return jsonify({'status': 'success', 'data': strategies})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/strategies', methods=['POST'])
def create_strategy():
    """创建新策略"""
    data = request.json or {}
    strategy_id = data.get('strategy_id', '').strip()
    name = data.get('name', '').strip()
    category = data.get('category', 'stock').strip()
    description = data.get('description', '').strip()
    factors_config = data.get('factors_config', {})
    buy_signals_rule = data.get('buy_signals_rule', '').strip()
    sell_signals_rule = data.get('sell_signals_rule', '').strip()

    if not strategy_id or not name:
        return jsonify({'status': 'error', 'message': '策略ID与策略名称不能为空'})

    import json
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM custom_strategies WHERE strategy_id = %s OR name = %s", (strategy_id, name))
        if cursor.fetchone():
            return jsonify({'status': 'error', 'message': '策略ID或策略名称已存在'})

        cursor.execute("""
            INSERT INTO custom_strategies 
            (strategy_id, name, category, description, factors_config, buy_signals_rule, sell_signals_rule)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            strategy_id, name, category, description,
            json.dumps(factors_config, ensure_ascii=False),
            buy_signals_rule, sell_signals_rule
        ))
        new_id = cursor.lastrowid
        conn.commit()
        return jsonify({'status': 'success', 'message': '策略创建成功', 'id': new_id})
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/strategies/<int:stg_id>', methods=['PUT'])
def update_strategy(stg_id):
    """编辑已有策略"""
    data = request.json or {}
    name = data.get('name', '').strip()
    category = data.get('category', 'stock').strip()
    description = data.get('description', '').strip()
    factors_config = data.get('factors_config', {})
    buy_signals_rule = data.get('buy_signals_rule', '').strip()
    sell_signals_rule = data.get('sell_signals_rule', '').strip()

    if not name:
        return jsonify({'status': 'error', 'message': '策略名称不能为空'})

    import json
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE custom_strategies
            SET name = %s, category = %s, description = %s, factors_config = %s, 
                buy_signals_rule = %s, sell_signals_rule = %s
            WHERE id = %s
        """, (
            name, category, description,
            json.dumps(factors_config, ensure_ascii=False),
            buy_signals_rule, sell_signals_rule,
            stg_id
        ))
        conn.commit()
        return jsonify({'status': 'success', 'message': '策略更新成功'})
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/strategies/<int:stg_id>', methods=['DELETE'])
def delete_strategy(stg_id):
    """删除策略（选择性同步删除对应的策略推荐结果）"""
    purge_results = request.args.get('purge_results', 'true').lower() == 'true'

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT strategy_id FROM custom_strategies WHERE id = %s", (stg_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({'status': 'error', 'message': '策略不存在'})

        stg_code = row[0]

        if purge_results:
            cursor.execute("DELETE FROM recommendations WHERE strategy = %s", (stg_code,))

        cursor.execute("DELETE FROM custom_strategies WHERE id = %s", (stg_id,))
        conn.commit()
        return jsonify({'status': 'success', 'message': f'策略 {stg_code} 已成功删除'})
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/strategies/<int:stg_id>/run', methods=['POST'])
def run_strategy(stg_id):
    """运行策略：计算指定日期的信号并落库推荐表。若当天已运行过且未指定 force 则弹窗提示。"""
    data = request.json or {}
    target_date = data.get('date', None)
    force = data.get('force', False)

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT strategy_id, name, category, factors_config FROM custom_strategies WHERE id = %s", (stg_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({'status': 'error', 'message': '策略不存在'})

        stg_code, stg_name, category, factors_cfg = row[0], row[1], row[2], row[3]

        if target_date is None:
            cursor.execute("SELECT MAX(date) FROM stock_kline_day")
            max_row = cursor.fetchone()
            target_date = max_row[0].strftime('%Y-%m-%d') if max_row and max_row[0] else datetime.now().strftime('%Y-%m-%d')

        # 检查当天是否已运行过
        cursor.execute("SELECT COUNT(*) FROM recommendations WHERE strategy = %s AND signal_date = %s", (stg_code, target_date))
        cnt = cursor.fetchone()[0]

        if cnt > 0 and not force:
            return jsonify({
                'status': 'already_run',
                'message': f"策略『{stg_name}』在 {target_date} 已在当天运行过（已有 {cnt} 条推荐结果）。",
                'already_run': True,
                'date': target_date,
                'count': cnt
            })

        # 触发策略引擎进行实时推荐计算
        from MagicSTG.strategies.runner import (
            run_cb_double_low_recommendation,
            run_dynamic_factor_recommendation
        )

        success = False
        if category == 'convertible_bond' or stg_code == 'cb_double_low':
            success = run_cb_double_low_recommendation(target_date)
        else:
            success = run_dynamic_factor_recommendation(target_date)

        if success:
            cursor.execute("SELECT COUNT(*) FROM recommendations WHERE strategy = %s AND signal_date = %s", (stg_code, target_date))
            new_cnt = cursor.fetchone()[0]
            return jsonify({
                'status': 'success',
                'message': f"策略『{stg_name}』计算完成！生成 {new_cnt} 条推荐记录。",
                'date': target_date,
                'count': new_cnt
            })
        else:
            return jsonify({'status': 'error', 'message': '策略运行计算未生成有效推荐数据'})

    except Exception as e:
        return jsonify({'status': 'error', 'message': f'运行失败: {str(e)}'})
    finally:
        conn.close()


@app.route('/api/recommendation-runs', methods=['GET'])
def get_recommendation_runs():
    """获取所有『行情时间 + 策略』策略执行历史履历卡片列表"""
    search = request.args.get('search', '').strip()
    category = request.args.get('category', '').strip()

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        query = """
            SELECT 
                r.signal_date,
                r.strategy,
                COALESCE(s.name, r.strategy) AS strategy_name,
                COALESCE(s.category, IF(r.strategy LIKE 'cb_%%', 'convertible_bond', 'stock')) AS category,
                COUNT(*) AS total_count,
                SUM(CASE WHEN r.action = 'BUY' THEN 1 ELSE 0 END) AS buy_count,
                SUM(CASE WHEN r.action = 'SELL' THEN 1 ELSE 0 END) AS sell_count,
                MAX(r.created_at) AS run_time
            FROM recommendations r
            LEFT JOIN custom_strategies s ON r.strategy = s.strategy_id
            WHERE 1=1
        """
        params = []
        if search:
            query += " AND (r.strategy LIKE %s OR s.name LIKE %s OR r.signal_date LIKE %s)"
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
        if category and category != 'all':
            query += " AND (s.category = %s OR (s.category IS NULL AND %s = 'convertible_bond' AND r.strategy LIKE 'cb_%%'))"
            params.extend([category, category])

        query += " GROUP BY r.signal_date, r.strategy ORDER BY r.signal_date DESC, r.strategy ASC LIMIT 100"
        cursor.execute(query, params)
        rows = cursor.fetchall()

        runs = []
        for r in rows:
            runs.append({
                'signal_date': r[0].strftime('%Y-%m-%d') if r[0] else None,
                'strategy_id': r[1],
                'strategy_name': r[2],
                'category': r[3],
                'total_count': int(r[4]),
                'buy_count': int(r[5]),
                'sell_count': int(r[6]),
                'run_time': r[7].strftime('%Y-%m-%d %H:%M:%S') if r[7] else None
            })

        return jsonify({'status': 'success', 'data': runs})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/recommendations')
def get_recommendations():
    """获取每日推荐信号（按 v1.0 架构区分股票策略与可转债策略展示指标）"""
    strategy = request.args.get('strategy', 'cb_double_low')
    date = request.args.get('date', None)

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # 获取策略分类信息
        cursor.execute("SELECT category FROM custom_strategies WHERE strategy_id = %s", (strategy,))
        stg_row = cursor.fetchone()
        category = stg_row[0] if stg_row else ('convertible_bond' if 'cb_' in strategy else 'stock')

        if date is None:
            cursor.execute("SELECT MAX(signal_date) FROM recommendations WHERE strategy = %s", (strategy,))
            row = cursor.fetchone()
            if row and row[0]:
                date = row[0].strftime('%Y-%m-%d')
            else:
                return jsonify({'status': 'error', 'message': f'策略 {strategy} 暂无推荐记录'})

        # 可转债策略详细多维指标关联
        if category == 'convertible_bond':
            cursor.execute("""
                SELECT 
                    r.stock_code AS cb_code,
                    COALESCE(b.name, r.stock_code) AS cb_name,
                    r.action,
                    COALESCE(r.price, i.cb_price) AS cb_price,
                    i.convert_value,
                    i.stock_code AS stock_code,
                    b.stock_name AS stock_name,
                    i.stock_price,
                    r.reason,
                    r.signal_date,
                    i.convert_premium_rate,
                    i.db_low_value
                FROM recommendations r
                LEFT JOIN cb_basic b ON r.stock_code = b.code
                LEFT JOIN cb_daily_indicator i ON r.stock_code = i.code AND r.signal_date = i.date
                WHERE r.strategy = %s AND r.signal_date = %s
                ORDER BY r.action ASC, r.stock_code ASC
            """, (strategy, date))
            rows = cursor.fetchall()

            recommendations = []
            for row in rows:
                recommendations.append({
                    'code': row[0],
                    'name': row[1],
                    'action': row[2],
                    'price': float(row[3]) if row[3] is not None else None,
                    'convert_value': float(row[4]) if row[4] is not None else None,
                    'pure_bond_value': round(float(row[3]) * 0.85, 2) if row[3] is not None else None, # 纯债价值估计估算
                    'ytm': 2.50, # 到期收益率标准估算
                    'stock_code': row[5],
                    'stock_name': row[6],
                    'stock_price': float(row[7]) if row[7] is not None else None,
                    'reason': row[8],
                    'date': row[9].strftime('%Y-%m-%d') if row[9] else None,
                    'convert_premium_rate': float(row[10]) if row[10] is not None else None,
                    'db_low_value': float(row[11]) if row[11] is not None else None
                })
        else:
            # 股票策略查询
            cursor.execute("""
                SELECT stock_code, action, price, reason, signal_date
                FROM recommendations
                WHERE strategy = %s AND signal_date = %s
                ORDER BY action ASC, stock_code ASC
            """, (strategy, date))
            rows = cursor.fetchall()

            recommendations = []
            for row in rows:
                recommendations.append({
                    'code': row[0],
                    'name': row[0], # 可通过扩展或者字典映射名称
                    'action': row[1],
                    'price': float(row[2]) if row[2] is not None else None,
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
            'category': category,
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
    universe = data.get('universe', 'csi300')
    config = data.get('config', {})

    if 'strategy' not in config:
        config['strategy'] = {}
    config['strategy']['max_holdings'] = int(config['strategy'].get('max_holdings', 4))
    config['strategy']['per_stock_capital'] = float(config['strategy'].get('per_stock_capital', 10000.0))
    config['strategy']['buy_di_threshold'] = float(config.get('tech', {}).get('buy_di_threshold', 0.70))
    config['strategy']['sell_di_threshold'] = float(config.get('tech', {}).get('sell_di_threshold', 0.70))
    config['strategy']['short_ma'] = int(config.get('tech', {}).get('short_ma', 5))
    config['strategy']['long_ma'] = int(config.get('tech', {}).get('long_ma', 20))
    config['strategy']['volume_surge_factor'] = float(config.get('tech', {}).get('volume_surge_factor', 1.2))

    try:
        from MagicSTG.backtests.engine import run_backtest_engine

        save_db = bool(data.get('save_db', False))
        stg_name = data.get('strategy_name', 'dynamic_factor')

        res = run_backtest_engine(
            config=config,
            start_date_str=start_date_str,
            end_date_str=end_date_str,
            strategy_name=stg_name,
            universe=universe,
            save_to_db=save_db
        )

        if res.get('status') == 'error':
            return jsonify({'status': 'error', 'message': res.get('message', '回测失败')})

        equity_history_tuples = [[h['date'], h['equity']] for h in res.get('equity_history', [])]

        return jsonify({
            'status': 'success',
            'summary': {
                'initial_equity': res['initial_equity'],
                'final_equity': res['final_equity'],
                'total_return': res['total_return'],
                'annual_return': res['annual_return'],
                'max_drawdown': res['max_drawdown'],
                'win_rate': res['win_rate'],
                'total_buys': res['total_buys'],
                'total_sells': res['total_sells'],
                'total_fees': res['total_fees']
            },
            'equity_history': equity_history_tuples,
            'trade_log': res['trades']
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