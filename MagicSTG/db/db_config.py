#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库连接公共模块
所有模块（更新脚本、策略脚本）统一从这里获取数据库连接
"""

import os
import time
import pymysql

# ============================================================
# 加载环境变量
# ============================================================

try:
    from dotenv import load_dotenv
    # 尝试从项目根目录加载 .env
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ENV_PATHS = [
        os.path.join(PROJECT_ROOT, '.env'),
        os.path.join(PROJECT_ROOT, 'dbconfig', '.env'),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'update', '.env'),
    ]
    for env_path in ENV_PATHS:
        if os.path.exists(env_path):
            load_dotenv(env_path)
            print(f"[ENV] Loaded .env from: {env_path}")
            break
except ImportError:
    pass

# ============================================================
# 配置常量（从环境变量读取）
# ============================================================

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT") or 3306)
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "")
DB_SSL_CA = os.getenv("DB_SSL_CA", "")

# 项目根目录（MagicSTG）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ============================================================
# 核心函数
# ============================================================

def get_connection():
    """
    获取数据库连接
    支持 SSL 连接，自动处理证书路径
    """
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
            print(f"[SSL] Using CA: {ssl_ca}")
        else:
            ssl_ca = "/etc/ssl/cert.pem" if os.path.exists("/etc/ssl/cert.pem") else None
    
    conn_params = {
        "host": DB_HOST,
        "port": DB_PORT,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "database": DB_NAME,
        "charset": "utf8mb4",
        "autocommit": False,
        "connect_timeout": 30,
        "read_timeout": 300,
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


def get_connection_with_retry(max_retries=3, retry_delay=2):
    """
    带重试机制的数据库连接
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            return get_connection()
        except Exception as e:
            last_error = e
            print(f"[DB] 连接失败 (尝试 {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
    raise last_error


def get_config():
    """
    返回数据库配置（用于需要配置信息的场景）
    """
    return {
        'host': DB_HOST,
        'port': DB_PORT,
        'user': DB_USER,
        'database': DB_NAME,
        'ssl_ca': DB_SSL_CA,
    }


def get_connection_info():
    """
    返回连接信息（用于日志输出，隐藏密码）
    """
    return f"Host: {DB_HOST}, Port: {DB_PORT}, Database: {DB_NAME}, User: {DB_USER}"


def execute_query(sql, params=None, fetch_one=False, fetch_all=False):
    """
    通用查询执行函数
    用于策略模块快速查询数据
    """
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            if fetch_one:
                return cur.fetchone()
            if fetch_all:
                return cur.fetchall()
            conn.commit()
            return cur.rowcount
    finally:
        if conn:
            conn.close()


def execute_many(sql, params_list):
    """
    批量执行 SQL
    """
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.executemany(sql, params_list)
            conn.commit()
            return cur.rowcount
    finally:
        if conn:
            conn.close()