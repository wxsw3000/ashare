# -*- coding: utf-8 -*-
"""
MagicSTG User Management & RBAC Module
Supports:
- Pre-seeded Super Admin: magicStarAdmin
- Password hashing & self-service password modification
- Registration requiring Admin-generated invite codes
- Account freezing & activation lifecycle ('active' / 'frozen')
"""

import datetime
import secrets
import pymysql
from typing import Optional, Dict, Any, Tuple, List
from werkzeug.security import generate_password_hash, check_password_hash
from MagicSTG.core.db import get_connection_with_retry

DEFAULT_ADMIN_USERNAME = "magicStarAdmin"
DEFAULT_ADMIN_PASSWORD = "Admin@MagicSTG2026"


def init_users_and_invites_tables():
    """
    Ensures that `users` and `invite_codes` tables exist in TiDB Cloud database.
    Auto-seeds pre-configured Super Admin `magicStarAdmin` if not present.
    """
    conn = get_connection_with_retry()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(50) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(20) NOT NULL DEFAULT 'user',
                    status VARCHAR(20) NOT NULL DEFAULT 'active',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # 自动迁移已存在的 users 表结构以补充 role 与 status 列
            cursor.execute("SHOW COLUMNS FROM users LIKE 'role'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'")
                print("[UserDB] Added 'role' column to users table", flush=True)

            cursor.execute("SHOW COLUMNS FROM users LIKE 'status'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE users ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'active'")
                print("[UserDB] Added 'status' column to users table", flush=True)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS invite_codes (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    code VARCHAR(64) NOT NULL UNIQUE,
                    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
                    created_by VARCHAR(50) NOT NULL DEFAULT 'system',
                    used_by VARCHAR(50) DEFAULT NULL,
                    used_at DATETIME DEFAULT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # 预置超级管理员 magicStarAdmin
            cursor.execute("SELECT id FROM users WHERE username = %s", (DEFAULT_ADMIN_USERNAME,))
            if not cursor.fetchone():
                admin_pwd_hash = generate_password_hash(DEFAULT_ADMIN_PASSWORD)
                cursor.execute(
                    "INSERT INTO users (username, password_hash, role, status, created_at) VALUES (%s, %s, 'admin', 'active', %s)",
                    (DEFAULT_ADMIN_USERNAME, admin_pwd_hash, datetime.datetime.now())
                )
                print(f"[UserDB] Pre-seeded super admin '{DEFAULT_ADMIN_USERNAME}' created successfully", flush=True)

        conn.commit()
    except Exception as e:
        print(f"[UserDB] Initialization failed: {e}", flush=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass



def ensure_ownership_columns():
    """
    Ensures `created_by` multi-tenancy columns exist in core business tables:
    - custom_strategies
    - portfolio_tasks
    - backtest_records
    - recommendations
    """
    conn = get_connection_with_retry()
    try:
        with conn.cursor() as cursor:
            tables_to_check = ['custom_strategies', 'portfolio_tasks', 'backtest_records', 'recommendations']
            for tbl in tables_to_check:
                try:
                    cursor.execute(f"SHOW TABLES LIKE '{tbl}'")
                    if cursor.fetchone():
                        cursor.execute(f"SHOW COLUMNS FROM {tbl} LIKE 'created_by'")
                        if not cursor.fetchone():
                            cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN created_by VARCHAR(50) DEFAULT '{DEFAULT_ADMIN_USERNAME}'")
                            print(f"[DB Schema ✅] Added created_by column to {tbl}", flush=True)
                except Exception as col_err:
                    print(f"[DB Schema ⚠️] Column check error on {tbl}: {col_err}", flush=True)
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def register_user(username: str, password: str, invite_code: str) -> Tuple[bool, str]:
    """
    Registers a new standard user ('user') requiring a valid admin-generated invite code.
    """
    username = (username or "").strip()
    password = password or ""
    invite_code = (invite_code or "").strip()

    if not username:
        return False, "用户名不能为空"
    if len(username) < 3 or len(username) > 30:
        return False, "用户名长度必须在 3 到 30 个字符之间"
    if not password or len(password) < 6:
        return False, "密码长度必须至少为 6 位"
    if not invite_code:
        return False, "请输入注册邀请码"

    init_users_and_invites_tables()
    conn = get_connection_with_retry()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
            if cursor.fetchone():
                return False, "该用户名已被注册，请更换用户名"

            cursor.execute("SELECT * FROM invite_codes WHERE code = %s AND status = 'ACTIVE'", (invite_code,))
            code_record = cursor.fetchone()
            if not code_record:
                return False, "邀请码无效、已被使用或已被管理员作废"

            pwd_hash = generate_password_hash(password)
            now = datetime.datetime.now()

            cursor.execute(
                "INSERT INTO users (username, password_hash, role, status, created_at) VALUES (%s, %s, 'user', 'active', %s)",
                (username, pwd_hash, now)
            )

            cursor.execute(
                "UPDATE invite_codes SET status = 'USED', used_by = %s, used_at = %s WHERE id = %s",
                (username, now, code_record['id'])
            )

        conn.commit()
        return True, "注册成功！您已获得系统访问与量化策略管理权限"
    except Exception as e:
        return False, f"注册失败: {str(e)}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def authenticate_user(username: str, password: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Authenticates user and checks account status (active vs frozen).
    """
    username = (username or "").strip()
    password = password or ""

    if not username or not password:
        return False, None, "用户名和密码不能为空"

    init_users_and_invites_tables()
    conn = get_connection_with_retry()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id, username, password_hash, role, status, created_at FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()
            if not user:
                return False, None, "用户名或密码错误"

            if not check_password_hash(user['password_hash'], password):
                return False, None, "用户名或密码错误"

            if user.get('status') == 'frozen':
                return False, None, "您的账号已被管理员冻结，无法登录系统，请联系管理员解冻"

            created_at_str = user["created_at"].strftime('%Y-%m-%d %H:%M:%S') if isinstance(user["created_at"], datetime.datetime) else str(user["created_at"])
            user_data = {
                "id": user["id"],
                "username": user["username"],
                "role": user.get("role", "user"),
                "status": user.get("status", "active"),
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


def change_password(username: str, old_password: str, new_password: str) -> Tuple[bool, str]:
    """
    Allows logged-in users to securely change their password.
    """
    username = (username or "").strip()
    if not username or not old_password or not new_password:
        return False, "参数不能为空"
    if len(new_password) < 6:
        return False, "新密码长度至少需要 6 位"

    conn = get_connection_with_retry()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id, password_hash FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()
            if not user:
                return False, "用户不存在"

            if not check_password_hash(user['password_hash'], old_password):
                return False, "原密码验证错误，请重新输入"

            new_pwd_hash = generate_password_hash(new_password)
            cursor.execute("UPDATE users SET password_hash = %s WHERE id = %s", (new_pwd_hash, user['id']))
        conn.commit()
        return True, "密码修改成功，请牢记您的新密码"
    except Exception as e:
        return False, f"修改密码失败: {str(e)}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ========== 管理员管控辅助函数 ==========

def generate_invite_code(admin_username: str) -> Tuple[bool, str, Optional[str]]:
    """
    Generates a new random active invite code (Format: STG-XXXXXX).
    """
    code_str = f"STG-{secrets.token_hex(4).upper()}"
    conn = get_connection_with_retry()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO invite_codes (code, status, created_by, created_at) VALUES (%s, 'ACTIVE', %s, %s)",
                (code_str, admin_username, datetime.datetime.now())
            )
        conn.commit()
        return True, "生成邀请码成功", code_str
    except Exception as e:
        return False, f"生成失败: {str(e)}", None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_invite_codes() -> List[Dict[str, Any]]:
    """
    Lists all invite codes with status and usage details.
    """
    conn = get_connection_with_retry()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, code, status, created_by, used_by, used_at, created_at
                FROM invite_codes
                ORDER BY id DESC
            """)
            rows = cursor.fetchall()
            for r in rows:
                if isinstance(r.get('created_at'), datetime.datetime):
                    r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                if isinstance(r.get('used_at'), datetime.datetime):
                    r['used_at'] = r['used_at'].strftime('%Y-%m-%d %H:%M:%S')
            return list(rows)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def revoke_invite_code(code_id: int) -> Tuple[bool, str]:
    """
    Revokes an active invite code.
    """
    conn = get_connection_with_retry()
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE invite_codes SET status = 'REVOKED' WHERE id = %s AND status = 'ACTIVE'", (code_id,))
        conn.commit()
        return True, "已成功作废该邀请码"
    except Exception as e:
        return False, f"作废失败: {str(e)}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_all_users() -> List[Dict[str, Any]]:
    """
    Lists all registered users.
    """
    conn = get_connection_with_retry()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, username, role, status, created_at
                FROM users
                ORDER BY id ASC
            """)
            rows = cursor.fetchall()
            for r in rows:
                if isinstance(r.get('created_at'), datetime.datetime):
                    r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            return list(rows)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def toggle_user_status(target_user_id: int, new_status: str) -> Tuple[bool, str]:
    """
    Activates ('active') or Freezes ('frozen') a user account.
    """
    if new_status not in ('active', 'frozen'):
        return False, "无效的状态控制指令"

    conn = get_connection_with_retry()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT role, username FROM users WHERE id = %s", (target_user_id,))
            target = cursor.fetchone()
            if not target:
                return False, "用户不存在"
            if target.get('username') == DEFAULT_ADMIN_USERNAME:
                return False, "超级管理员账号不能被冻结"

            cursor.execute("UPDATE users SET status = %s WHERE id = %s", (new_status, target_user_id))
        conn.commit()
        action_name = "解冻" if new_status == 'active' else "冻结"
        return True, f"已成功{action_name}该账号"
    except Exception as e:
        return False, f"状态更新失败: {str(e)}"
    finally:
        try:
            conn.close()
        except Exception:
            pass
