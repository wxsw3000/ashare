# -*- coding: utf-8 -*-
"""
MagicSTG User Management & Authentication Module
Provides user table creation, registration, authentication, password hashing, and session management.
"""

import datetime
import pymysql
from typing import Optional, Dict, Any, Tuple
from werkzeug.security import generate_password_hash, check_password_hash
from MagicSTG.core.db import get_connection_with_retry


def init_users_table():
    """
    Ensures that the `users` table exists in TiDB Cloud database.
    """
    conn = get_connection_with_retry()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(50) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
        conn.commit()
    except Exception as e:
        print(f"[UserDB ⚠️] Failed to initialize users table: {e}", flush=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def register_user(username: str, password: str) -> Tuple[bool, str]:
    """
    Registers a new user into the users table.
    Returns (success, message_or_error).
    """
    username = (username or "").strip()
    password = password or ""
    
    if not username:
        return False, "用户名不能为空"
    if len(username) < 3 or len(username) > 30:
        return False, "用户名长度必须在 3 到 30 个字符之间"
    if not password or len(password) < 6:
        return False, "密码长度必须至少为 6 位"

    init_users_table()
    conn = get_connection_with_retry()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
            if cursor.fetchone():
                return False, "该用户名已被注册，请直接登录或换一个用户名"

            pwd_hash = generate_password_hash(password)
            cursor.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (%s, %s, %s)",
                (username, pwd_hash, datetime.datetime.now())
            )
        conn.commit()
        return True, "注册成功！请使用新账号登录"
    except Exception as e:
        return False, f"注册失败: {str(e)}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def authenticate_user(username: str, password: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Authenticates a user by username and password.
    Returns (success, user_dict, message_or_error).
    """
    username = (username or "").strip()
    password = password or ""

    if not username or not password:
        return False, None, "用户名和密码不能为空"

    init_users_table()
    conn = get_connection_with_retry()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id, username, password_hash, created_at FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()
            if not user:
                return False, None, "用户名或密码错误"

            if not check_password_hash(user['password_hash'], password):
                return False, None, "用户名或密码错误"

            created_at_str = user["created_at"].strftime('%Y-%m-%d %H:%M:%S') if isinstance(user["created_at"], datetime.datetime) else str(user["created_at"])
            user_data = {
                "id": user["id"],
                "username": user["username"],
                "created_at": created_at_str
            }
            return True, user_data, "登录成功"
    except Exception as e:
        return False, None, f"登录验证失败: {str(e)}"
    finally:
        try:
            conn.close()
        except Exception:
            pass
