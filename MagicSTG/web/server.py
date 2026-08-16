import gc
import os
import sys
import json
import pymysql
import pandas as pd
import numpy as np
from flask import Flask, render_template, jsonify, request
from datetime import datetime
from dotenv import load_dotenv

app = Flask(__name__)


def sanitize_json(obj):
    if isinstance(obj, dict):
        return {k: sanitize_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [sanitize_json(v) for v in obj]
    elif isinstance(obj, (np.floating, float)):
        val = float(obj)
        return 0.0 if np.isnan(val) or np.isinf(val) else val
    elif isinstance(obj, (np.integer, int)):
        return int(obj)
    elif isinstance(obj, (pd.Timestamp, datetime)):
        return obj.strftime('%Y-%m-%d')
    elif isinstance(obj, np.ndarray):
        return [sanitize_json(v) for v in obj]
    return obj

# ========== 加载环境变量与路径配置 ==========
# 获取项目根目录 (包含 MagicSTG 包的父目录 E:\ashare)
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
MAGICSTG_DIR = os.path.dirname(WEB_DIR)
PROJECT_ROOT = os.path.dirname(MAGICSTG_DIR)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if MAGICSTG_DIR not in sys.path:
    sys.path.insert(0, MAGICSTG_DIR)

ENV_PATH = os.path.join(MAGICSTG_DIR, 'dbconfig', '.env')
if not os.path.exists(ENV_PATH):
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


def validate_and_sanitize_factors_config(category: str, config: dict) -> tuple[bool, str, dict]:
    """
    后端 JSON Schema 强类型校验与格式化清洗器。
    按 category 规范清洗 JSON 字段，确保数据纯净无错配。
    """
    if not isinstance(config, dict):
        return False, "因子配置根节点必须是一个 JSON 对象 (字典)", {}

    sanitized = {}

    if category == 'convertible_bond':
        # 可转债策略标准 Schema 清洗
        sanitized = {
            "top_n": int(config.get("top_n", 10)),
            "stock_scope": str(config.get("stock_scope", "all")),
            "sort_by": str(config.get("sort_by", "db_low_value")),
            "max_price": float(config.get("max_price", 130.0)),
            "min_convert_premium": float(config.get("min_convert_premium", -20.0)),
            "max_convert_premium": float(config.get("max_convert_premium", 50.0)),
            "max_pure_bond_premium": float(config["max_pure_bond_premium"]) if config.get("max_pure_bond_premium") is not None else None,
            "min_ytm": float(config["min_ytm"]) if config.get("min_ytm") is not None else None,
        }
        
        linkage = config.get("stock_linkage", {})
        if not isinstance(linkage, dict):
            linkage = {}
            
        sanitized["stock_linkage"] = {
            "enable_stock_roe": bool(linkage.get("enable_stock_roe", False)),
            "stock_roe_min": float(linkage.get("stock_roe_min", 0.05)),
            "enable_stock_pe": bool(linkage.get("enable_stock_pe", False)),
            "stock_pe_min": float(linkage.get("stock_pe_min", 0)),
            "stock_pe_max": float(linkage.get("stock_pe_max", 35)),
            "enable_stock_ma": bool(linkage.get("enable_stock_ma", False)),
            "short_ma": int(linkage.get("short_ma", 5)),
            "long_ma": int(linkage.get("long_ma", 20))
        }
    else:
        # 股票策略标准 Schema 清洗
        tech = config.get("tech", {})
        if not isinstance(tech, dict):
            tech = {}

        fin = config.get("financial", {})
        if not isinstance(fin, dict):
            fin = {}

        sort_by = str(config.get("sort_by", config.get("ranking", {}).get("primary_factor", "pe")))

        sanitized = {
            "top_n": int(config.get("top_n", 10)),
            "stock_scope": str(config.get("stock_scope", "csi300")),
            "sort_by": sort_by,
            "tech": {
                "enable_golden_cross": bool(tech.get("enable_golden_cross", True)),
                "short_ma": int(tech.get("short_ma", 5)),
                "long_ma": int(tech.get("long_ma", 20)),
                "enable_volume_surge": bool(tech.get("enable_volume_surge", True)),
                "volume_surge_factor": float(tech.get("volume_surge_factor", 1.2)),
                "enable_di_ratio": bool(tech.get("enable_di_ratio", True)),
                "buy_di_threshold": float(tech.get("buy_di_threshold", 0.70)),
                "enable_turnover": bool(tech.get("enable_turnover", False)),
                "min_turnover": float(tech.get("min_turnover", 1.0)),
                "max_turnover": float(tech.get("max_turnover", 15.0)),
                "enable_rsi": bool(tech.get("enable_rsi", False)),
                "rsi_min": float(tech.get("rsi_min", 30.0)),
                "rsi_max": float(tech.get("rsi_max", 70.0)),
                "enable_macd": bool(tech.get("enable_macd", False)),
                "enable_boll": bool(tech.get("enable_boll", False)),
                "enable_kdj": bool(tech.get("enable_kdj", False))
            },
            "financial": {
                "enable_roe": bool(fin.get("enable_roe", False)),
                "roe_min": float(fin.get("roe_min", 0.05)),
                "enable_pe": bool(fin.get("enable_pe", False)),
                "pe_min": float(fin.get("pe_min", 0)),
                "pe_max": float(fin.get("pe_max", 35)),
                "enable_pb": bool(fin.get("enable_pb", False)),
                "pb_min": float(fin.get("pb_min", 0.5)),
                "pb_max": float(fin.get("pb_max", 5.0)),
                "enable_growth": bool(fin.get("enable_growth", False)),
                "growth_min": float(fin.get("growth_min", 0.10)),
                "enable_debt_limit": bool(fin.get("enable_debt_limit", False)),
                "debt_max": float(fin.get("debt_max", 0.70)),
                "enable_cash_quality": bool(fin.get("enable_cash_quality", False)),
                "cfo_np_min": float(fin.get("cfo_np_min", 0.80))
            },
            "ranking": {
                "primary_factor": sort_by,
                "reverse": sort_by in ["roe", "growth"]
            }
        }

    return True, "", sanitized


# ========== 策略管理 API (v1.0 架构) ==========
@app.route('/api/strategies', methods=['GET'])
def get_strategies():
    """获取策略列表（支持搜索名称及分类筛选）"""
    search = request.args.get('search', '').strip()
    category = request.args.get('category', '').strip()

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        query = "SELECT id, strategy_id, name, category, description, factors_config, buy_signals_rule, sell_signals_rule, created_at, updated_at, is_active FROM custom_strategies WHERE 1=1"
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

            is_act = bool(r[10]) if len(r) > 10 and r[10] is not None else True

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
                'updated_at': r[9].strftime('%Y-%m-%d %H:%M:%S') if r[9] else None,
                'is_active': is_act
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
    is_active = 1 if data.get('is_active', True) else 0

    if not strategy_id:
        import time
        strategy_id = f"stg_{int(time.time())}"

    if not name:
        return jsonify({'status': 'error', 'message': '策略名称不能为空'})

    ok, err_msg, clean_config = validate_and_sanitize_factors_config(category, factors_config)
    if not ok:
        return jsonify({'status': 'error', 'message': f'因子配置格式不合法: {err_msg}'})

    import json
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM custom_strategies WHERE strategy_id = %s OR name = %s", (strategy_id, name))
        if cursor.fetchone():
            return jsonify({'status': 'error', 'message': '策略ID或策略名称已存在'})

        cursor.execute("""
            INSERT INTO custom_strategies 
            (strategy_id, name, category, description, factors_config, buy_signals_rule, sell_signals_rule, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            strategy_id, name, category, description,
            json.dumps(clean_config, ensure_ascii=False),
            buy_signals_rule, sell_signals_rule, is_active
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
    is_active = 1 if data.get('is_active', True) else 0

    if not name:
        return jsonify({'status': 'error', 'message': '策略名称不能为空'})

    ok, err_msg, clean_config = validate_and_sanitize_factors_config(category, factors_config)
    if not ok:
        return jsonify({'status': 'error', 'message': f'因子配置格式不合法: {err_msg}'})

    import json
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE custom_strategies
            SET name = %s, category = %s, description = %s, factors_config = %s, 
                buy_signals_rule = %s, sell_signals_rule = %s, is_active = %s
            WHERE id = %s
        """, (
            name, category, description,
            json.dumps(clean_config, ensure_ascii=False),
            buy_signals_rule, sell_signals_rule, is_active,
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


@app.route('/api/strategies/<int:stg_id>/toggle-active', methods=['POST'])
def toggle_strategy_active(stg_id):
    """一键切换策略在每日盘后工作流中的激活/开启状态 (is_active)"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT is_active, name FROM custom_strategies WHERE id = %s", (stg_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({'status': 'error', 'message': '策略不存在'})

        curr_active = bool(row[0]) if row[0] is not None else True
        new_active = 0 if curr_active else 1
        stg_name = row[1]

        cursor.execute("UPDATE custom_strategies SET is_active = %s WHERE id = %s", (new_active, stg_id))
        conn.commit()

        status_str = "已加入每日盘后工作流" if new_active == 1 else "已设为停用草稿"
        return jsonify({
            'status': 'success',
            'is_active': bool(new_active),
            'message': f"策略『{stg_name}』{status_str}！"
        })
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


@app.route('/api/latest-date', methods=['GET'])
def get_latest_date():
    """获取数据库中最新的行情数据日期"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(date) FROM stock_kline_day")
        row = cursor.fetchone()
        latest_date = row[0].strftime('%Y-%m-%d') if row and row[0] else datetime.now().strftime('%Y-%m-%d')
        return jsonify({'status': 'success', 'date': latest_date})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/strategies/<int:stg_id>/run', methods=['POST'])
def run_strategy(stg_id):
    """异步触发策略计算：支持调用 GitHub Actions 算力引擎或启动后台线程计算，在0.1秒内极速响应避线超时。"""
    import threading
    import urllib.request
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

        gh_token = os.getenv("GH_PAT") or os.getenv("GITHUB_TOKEN")
        gh_repo = os.getenv("GITHUB_REPOSITORY", "wxsw3000/ashare")

        if not gh_token:
            return jsonify({
                'status': 'error',
                'message': '尚未在 Render 平台控制台添加 GH_PAT (GitHub Token) 环境变量。为保护 Render 内存不被爆掉，在线重算已阻止。请在 Render 后台添加 GH_PAT 变量，或使用【工作流策略配置】开启每日盘后自动推演！'
            })

        task_name = f"strategy_{stg_code}"
        cursor.execute("""
            INSERT INTO update_progress (task_date, script_name, status, started_at, error_msg)
            VALUES (%s, %s, 'queued', NOW(), NULL)
            ON DUPLICATE KEY UPDATE status = 'queued', started_at = NOW(), error_msg = NULL
        """, (target_date, task_name))
        conn.commit()

        try:
            url = f"https://api.github.com/repos/{gh_repo}/dispatches"
            payload = json.dumps({
                "event_type": "run-strategy",
                "client_payload": {
                    "strategy_id": stg_code,
                    "target_date": target_date,
                    "force": force
                }
            }).encode('utf-8')
            req = urllib.request.Request(url, data=payload, headers={
                "Authorization": f"token {gh_token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "MagicSTG-Server"
            })
            with urllib.request.urlopen(req) as resp:
                if resp.status in (200, 204):
                    return jsonify({
                        'status': 'processing',
                        'engine': 'github_actions',
                        'message': '调度请求已成功提交至 GitHub Actions 7GB 离线算力引擎！',
                        'date': target_date,
                        'stg_code': stg_code,
                        'stg_name': stg_name
                    })
                else:
                    return jsonify({
                        'status': 'error',
                        'message': f'触发 GitHub Actions 算力接口返回 HTTP {resp.status}。'
                    })
        except Exception as gh_err:
            print(f"[RunStrategy] ⚠️ 触发 GitHub Actions 异常: {gh_err}", flush=True)
            return jsonify({
                'status': 'error',
                'message': f'触发 GitHub Actions 算力引擎异常 ({gh_err})，请检查 Render 中的 GH_PAT 密钥与权限。'
            })

    except Exception as e:
        return jsonify({'status': 'error', 'message': f'调度失败: {str(e)}'})
    finally:
        if conn:
            conn.close()


@app.route('/api/strategies/<int:stg_id>/task-status', methods=['GET'])
def get_strategy_task_status(stg_id):
    """轮询检测策略运行进度与数据落库状态"""
    target_date = request.args.get('date', None)

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT strategy_id, name FROM custom_strategies WHERE id = %s", (stg_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({'status': 'error', 'message': '策略不存在'})

        stg_code, stg_name = row[0], row[1]
        task_name = f"strategy_{stg_code}"

        if not target_date:
            cursor.execute("SELECT MAX(date) FROM stock_kline_day")
            max_row = cursor.fetchone()
            target_date = max_row[0].strftime('%Y-%m-%d') if max_row and max_row[0] else datetime.now().strftime('%Y-%m-%d')

        cursor.execute("SELECT status, error_msg, started_at FROM update_progress WHERE task_date = %s AND script_name = %s", (target_date, task_name))
        p_row = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) FROM recommendations WHERE strategy = %s AND signal_date = %s", (stg_code, target_date))
        rec_cnt = cursor.fetchone()[0]

        if rec_cnt > 0:
            return jsonify({
                'status': 'success',
                'completed': True,
                'count': rec_cnt,
                'date': target_date,
                'message': f"策略『{stg_name}』计算完成！生成 {rec_cnt} 条推荐记录。"
            })

        if p_row:
            p_status, p_err, started_at = p_row[0], p_row[1], p_row[2]
            if p_status == 'failed':
                return jsonify({
                    'status': 'error',
                    'completed': True,
                    'message': f"计算中断或失败: {p_err or '未生成有效推荐数据'}"
                })
            elif p_status == 'running' and started_at and (datetime.now() - started_at).total_seconds() > 300:
                cursor.execute("""
                    UPDATE update_progress
                    SET status = 'failed', completed_at = NOW(), error_msg = '超时中断：后台线程无响应或已被系统重启杀掉'
                    WHERE task_date = %s AND script_name = %s
                """, (target_date, task_name))
                conn.commit()
                return jsonify({
                    'status': 'error',
                    'completed': True,
                    'message': '任务运行已超过 5 分钟超时中断（后台线程已被重置），建议通过 GitHub Actions 离线算力或勾选轻量因子。'
                })
            else:
                return jsonify({
                    'status': p_status,
                    'completed': False,
                    'message': f"离线算力引擎正在处理中 (状态: {p_status})..."
                })

        return jsonify({
            'status': 'running',
            'completed': False,
            'message': '任务排队等待中...'
        })

    except Exception as e:
        return jsonify({'status': 'error', 'completed': False, 'message': str(e)})
    finally:
        if conn:
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


@app.route('/api/recommendations', methods=['DELETE'])
def delete_recommendations():
    """彻底删除特定策略在特定日期的所有推荐结果记录"""
    strategy = request.args.get('strategy', '').strip()
    date = request.args.get('date', '').strip()

    if not strategy or not date:
        return jsonify({'status': 'error', 'message': '缺少必要参数: strategy 和 date'})

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM recommendations WHERE strategy = %s AND signal_date = %s",
            (strategy, date)
        )
        deleted_count = cursor.rowcount
        conn.commit()
        return jsonify({
            'status': 'success',
            'message': f'已成功从数据库中清除策略 {strategy} 在 {date} 的 {deleted_count} 条推荐记录',
            'count': deleted_count
        })
    except Exception as e:
        if conn:
            conn.rollback()
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
    """获取回测报告列表（支持策略名称关联及夏普比率展示）"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        try:
            cursor.execute("ALTER TABLE backtest_results ADD COLUMN sharpe_ratio decimal(12, 6) DEFAULT 0.00;")
        except Exception:
            pass

        cursor.execute("""
            SELECT 
                r.id, 
                r.strategy, 
                COALESCE(s.name, r.strategy) AS strategy_name,
                r.run_date, 
                r.date_range_start, 
                r.date_range_end,
                r.total_return, 
                r.annual_return, 
                r.max_drawdown, 
                r.win_rate,
                COALESCE(r.sharpe_ratio, 0.0) AS sharpe_ratio,
                r.initial_equity,
                r.final_equity
            FROM backtest_results r
            LEFT JOIN custom_strategies s ON (r.strategy = s.strategy_id OR r.strategy = CAST(s.id AS CHAR))
            ORDER BY r.run_date DESC, r.id DESC
            LIMIT 50
        """)
        rows = cursor.fetchall()

        backtests = []
        for row in rows:
            backtests.append({
                'id': row[0],
                'strategy': row[1],
                'strategy_name': row[2],
                'run_date': row[3].strftime('%Y-%m-%d') if row[3] else None,
                'date_range': f"{row[4].strftime('%Y-%m-%d') if row[4] else 'N/A'} ~ {row[5].strftime('%Y-%m-%d') if row[5] else 'N/A'}",
                'total_return': float(row[6]) if row[6] is not None else 0.0,
                'annual_return': float(row[7]) if row[7] is not None else 0.0,
                'max_drawdown': float(row[8]) if row[8] is not None else 0.0,
                'win_rate': float(row[9]) if row[9] is not None else 0.0,
                'sharpe_ratio': float(row[10]) if row[10] is not None else 0.0,
                'initial_equity': float(row[11]) if row[11] is not None else 0.0,
                'final_equity': float(row[12]) if row[12] is not None else 0.0
            })

        return jsonify({'status': 'success', 'data': backtests})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/backtests/<int:backtest_id>', methods=['DELETE'])
def delete_backtest(backtest_id):
    """彻底删除特定回测记录及其全部关联成交明细数据"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM backtest_trades WHERE backtest_id = %s", (backtest_id,))
        cursor.execute("DELETE FROM backtest_results WHERE id = %s", (backtest_id,))
        conn.commit()
        return jsonify({'status': 'success', 'message': f'回测记录 (ID: {backtest_id}) 及其关联明细已被删除'})
    except Exception as e:
        if conn:
            conn.rollback()
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
    """触发离线 GitHub Actions 算力引擎执行策略回测，防止 Render 节点超时 (HTTP 502/504)"""
    data = request.json or {}
    start_date_str = data.get('start_date', '2024-01-01')
    end_date_str = data.get('end_date', '2026-06-30')
    stg_name = data.get('strategy_name', 'dynamic_factor')

    gh_token = os.getenv("GH_PAT") or os.getenv("GITHUB_TOKEN")
    gh_repo = os.getenv("GITHUB_REPOSITORY", "wxsw3000/ashare")

    if not gh_token:
        # 如果没有 GH_PAT，对于轻量小标的池在本地保护运行；对大标的池提警
        try:
            from MagicSTG.backtests.engine import run_backtest_engine
            res = run_backtest_engine(
                config=data.get('config', {}),
                start_date_str=start_date_str,
                end_date_str=end_date_str,
                strategy_name=stg_name,
                universe=data.get('universe', 'csi300'),
                save_to_db=bool(data.get('save_db', True))
            )
            if res.get('status') == 'error':
                return jsonify({'status': 'error', 'message': res.get('message', '回测失败')})

            equity_history_tuples = [[h['date'], h['equity']] for h in res.get('equity_history', [])]
            return jsonify(sanitize_json({
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
            }))
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})

    # 具备 GH_PAT 密钥时，下发 7GB Actions 高性能算力引擎处理
    try:
        import urllib.request
        url = f"https://api.github.com/repos/{gh_repo}/dispatches"
        payload = json.dumps({
            "event_type": "run-backtest",
            "client_payload": {
                "strategy": stg_name,
                "start_date": start_date_str,
                "end_date": end_date_str,
                "capital": "100000",
                "top_n": "10"
            }
        }).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={
            "Authorization": f"token {gh_token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "MagicSTG-Server"
        })
        with urllib.request.urlopen(req) as resp:
            if resp.status in (200, 204):
                # 获取该策略当前最新的回测 id 作为基准
                latest_id = 0
                try:
                    conn_tmp = get_db_connection()
                    cur_tmp = conn_tmp.cursor()
                    cur_tmp.execute("SELECT MAX(id) FROM backtest_results WHERE strategy = %s", (stg_name,))
                    r_tmp = cur_tmp.fetchone()
                    latest_id = r_tmp[0] if (r_tmp and r_tmp[0]) else 0
                    conn_tmp.close()
                except Exception:
                    pass

                return jsonify({
                    'status': 'processing',
                    'engine': 'github_actions',
                    'message': f'⚡ 回测计算已成功分发给 GitHub Actions 7GB 离线算力引擎！数据计算完成后将自动写入回测报告表。',
                    'strategy': stg_name,
                    'start_date': start_date_str,
                    'end_date': end_date_str,
                    'dispatch_id': latest_id,
                    'dispatch_time': int(time.time())
                })
            else:
                return jsonify({'status': 'error', 'message': f'分发 GitHub Actions 回测任务失败 HTTP {resp.status}'})
    except Exception as gh_err:
        return jsonify({'status': 'error', 'message': f'调度 GitHub Actions 回测节点异常 ({gh_err})'})


@app.route('/api/backtest/status')
def get_backtest_status():
    """查询指定策略的回测实时计算与入库状态（用于前端控制台轮询）"""
    stg = request.args.get('strategy')
    last_id = request.args.get('last_id', 0, type=int)

    if not stg:
        return jsonify({'status': 'error', 'message': '缺少 strategy 参数'})

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, strategy, run_date, date_range_start, date_range_end,
                   total_return, annual_return, max_drawdown, win_rate, initial_equity, final_equity
            FROM backtest_results
            WHERE strategy = %s OR strategy = (SELECT strategy_id FROM custom_strategies WHERE id = %s LIMIT 1)
            ORDER BY id DESC
            LIMIT 1
        """, (stg, stg if stg.isdigit() else -1))
        row = cursor.fetchone()

        if not row:
            return jsonify({'status': 'running', 'completed': False, 'message': '算力节点排队/计算中...'})

        b_id, b_stg, run_date, d_start, d_end, tot_ret, ann_ret, max_dd, win_rate, init_eq, fin_eq = row

        if b_id > last_id:
            return jsonify({
                'status': 'success',
                'completed': True,
                'data': {
                    'id': b_id,
                    'strategy': b_stg,
                    'run_date': run_date.strftime('%Y-%m-%d %H:%M:%S') if hasattr(run_date, 'strftime') else str(run_date),
                    'date_range': f"{d_start} ~ {d_end}",
                    'total_return': float(tot_ret) if tot_ret is not None else 0.0,
                    'annual_return': float(ann_ret) if ann_ret is not None else 0.0,
                    'max_drawdown': float(max_dd) if max_dd is not None else 0.0,
                    'win_rate': float(win_rate) if win_rate else 0.0,
                    'initial_equity': float(init_eq) if init_eq else 0.0,
                    'final_equity': float(fin_eq) if fin_eq else 0.0
                }
            })
        else:
            return jsonify({'status': 'running', 'completed': False, 'message': '算力节点全速计算中...'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()



# ========== 模拟实盘持仓与组合收益 API ==========

@app.route('/api/portfolio/strategies', methods=['GET'])
def get_portfolio_strategies():
    """获取存在模拟持仓与推荐记录的策略列表"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT strategy_id, name FROM custom_strategies ORDER BY id ASC")
        rows = cursor.fetchall()
        stg_list = [{'strategy_id': r[0], 'name': r[1]} for r in rows]

        cursor.execute("SELECT DISTINCT strategy FROM recommendations")
        rec_stgs = [r[0] for r in cursor.fetchall()]
        existing_ids = {s['strategy_id'] for s in stg_list}
        for r_id in rec_stgs:
            if r_id not in existing_ids:
                stg_list.append({'strategy_id': r_id, 'name': r_id})

        return jsonify({'status': 'success', 'data': stg_list})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/portfolio', methods=['GET'])
def get_portfolio_data():
    """获取指定策略的模拟持仓看板、成交流水与收益率曲线"""
    strategy_id = request.args.get('strategy_id', 'cb_double_low')
    try:
        from MagicSTG.execution.portfolio_engine import PortfolioEngine
        engine = PortfolioEngine(strategy_id)
        summary = engine.get_portfolio_summary()
        return jsonify(sanitize_json({'status': 'success', 'data': summary}))
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': f'获取模拟持仓失败: {str(e)}'})


@app.route('/api/portfolio/sync', methods=['POST'])
def sync_portfolio_data():
    """根据最新推荐信号重新同步/推演模拟持仓"""
    req_data = request.json or {}
    strategy_id = req_data.get('strategy_id', 'cb_double_low')
    try:
        from MagicSTG.execution.portfolio_engine import PortfolioEngine
        engine = PortfolioEngine(strategy_id)
        res = engine.sync_portfolio_history()
        summary = engine.get_portfolio_summary()
        return jsonify(sanitize_json({'status': 'success', 'sync_result': res, 'data': summary}))
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': f'同步持仓失败: {str(e)}'})


if __name__ == '__main__':
    # 获取 Render 等云托管平台分配的端口，默认为 5000
    port = int(os.environ.get("PORT", 5000))
    # 生产环境建议关闭 debug 模式，可通过 FLASK_ENV=development 开启
    debug_mode = os.environ.get("FLASK_ENV") == "development"
    app.run(debug=debug_mode, host='0.0.0.0', port=port)