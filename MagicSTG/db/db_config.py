# -*- coding: utf-8 -*-
"""
MagicSTG DB Bridge Configuration
Delegates connection establishment to MagicSTG.core.db
"""

import os
import time
import pymysql
from MagicSTG.core.db import get_connection, ensure_connection_alive
from MagicSTG.config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME, get_ssl_ca_path


def get_connection_with_retry(max_retries=3, retry_delay=5):
    """Obtains a DB connection with retry logic."""
    for i in range(max_retries):
        try:
            return get_connection()
        except Exception as e:
            if i < max_retries - 1:
                print(f"  [DB] Connection retry ({i+1}/{max_retries}): {e}", flush=True)
                time.sleep(retry_delay)
            else:
                print(f"  [DB] ❌ Connection failed: {e}", flush=True)
                raise


def get_config():
    return {
        "host": DB_HOST,
        "port": DB_PORT,
        "user": DB_USER,
        "database": DB_NAME,
        "ssl_ca": get_ssl_ca_path()
    }


def get_connection_info():
    return f"Host: {DB_HOST}:{DB_PORT}, User: {DB_USER}, Database: {DB_NAME}"


def execute_query(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    finally:
        conn.close()


def execute_many(sql, params_list):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.executemany(sql, params_list)
            conn.commit()
            return cursor.rowcount
    finally:
        conn.close()