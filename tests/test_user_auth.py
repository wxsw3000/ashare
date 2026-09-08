# -*- coding: utf-8 -*-
"""
Unit tests for MagicSTG User Authentication & Permission System
Tests user registration, password hashing verification, duplicate username prevention, and authentication.
"""

import unittest
import uuid
from MagicSTG.core.user_manager import init_users_table, register_user, authenticate_user
from MagicSTG.core.db import get_connection_with_retry


class TestUserAuthSystem(unittest.TestCase):

    def setUp(self):
        init_users_table()
        self.test_user = f"test_user_{uuid.uuid4().hex[:8]}"
        self.test_password = "SecurePassword2026!"

    def test_01_users_table_exists(self):
        """验证 users 用户表能够在 TiDB 集中数据库中自动创建"""
        conn = get_connection_with_retry()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SHOW TABLES LIKE 'users';")
                res = cursor.fetchone()
                self.assertIsNotNone(res, "users 表应当在数据库中成功创建")
        finally:
            conn.close()

    def test_02_register_and_authenticate(self):
        """验证新用户注册、密码 Werkzeug 哈希加密与正确登录"""
        # 1. 注册新用户
        success, msg = register_user(self.test_user, self.test_password)
        self.assertTrue(success, f"注册应当成功: {msg}")

        # 2. 重复注册校验
        dup_success, dup_msg = register_user(self.test_user, self.test_password)
        self.assertFalse(dup_success, "重复用户名应当拒绝注册")

        # 3. 错误密码登录
        auth_ok, user_data, auth_msg = authenticate_user(self.test_user, "WrongPassword123")
        self.assertFalse(auth_ok, "错误密码应当鉴权失败")
        self.assertIsNone(user_data)

        # 4. 正确密码登录
        auth_ok, user_data, auth_msg = authenticate_user(self.test_user, self.test_password)
        self.assertTrue(auth_ok, f"正确密码应当鉴权成功: {auth_msg}")
        self.assertIsNotNone(user_data)
        self.assertEqual(user_data["username"], self.test_user)

    def tearDown(self):
        """清理测试产生的账号"""
        try:
            conn = get_connection_with_retry()
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM users WHERE username = %s", (self.test_user,))
            conn.commit()
            conn.close()
        except Exception:
            pass


if __name__ == '__main__':
    unittest.main()
