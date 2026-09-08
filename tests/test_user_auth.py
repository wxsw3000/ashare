# -*- coding: utf-8 -*-
"""
Unit tests for MagicSTG RBAC, Invite Code & User Management System
Tests:
- Pre-seeded Super Admin magicStarAdmin
- Admin-generated invite codes
- User registration requiring invite codes
- Password modification
- Account freezing & activation lifecycle
"""

import unittest
import uuid
from MagicSTG.core.user_manager import (
    init_users_and_invites_tables,
    register_user,
    authenticate_user,
    change_password,
    generate_invite_code,
    revoke_invite_code,
    toggle_user_status,
    DEFAULT_ADMIN_USERNAME,
    DEFAULT_ADMIN_PASSWORD
)
from MagicSTG.core.db import get_connection_with_retry


class TestRBACAuthSystem(unittest.TestCase):

    def setUp(self):
        init_users_and_invites_tables()
        self.test_user = f"user_{uuid.uuid4().hex[:8]}"
        self.test_password = "UserPassword2026!"

    def test_01_preseeded_super_admin(self):
        """验证预置超级管理员 magicStarAdmin 能成功登录且角色为 admin"""
        ok, user_data, msg = authenticate_user(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
        self.assertTrue(ok, f"超级管理员登录失败: {msg}")
        self.assertEqual(user_data["username"], DEFAULT_ADMIN_USERNAME)
        self.assertEqual(user_data["role"], "admin")
        self.assertEqual(user_data["status"], "active")

    def test_02_invite_code_and_user_registration(self):
        """验证必须有有效邀请码才能注册普通用户"""
        # 1. 无邀请码注册应当被拒绝
        ok, msg = register_user(self.test_user, self.test_password, "")
        self.assertFalse(ok)

        # 2. 无效邀请码应当被拒绝
        ok, msg = register_user(self.test_user, self.test_password, "INVALID-CODE-999")
        self.assertFalse(ok)

        # 3. 管理员生成邀请码
        gen_ok, gen_msg, code = generate_invite_code(DEFAULT_ADMIN_USERNAME)
        self.assertTrue(gen_ok)
        self.assertIsNotNone(code)

        # 4. 使用邀请码注册成功
        reg_ok, reg_msg = register_user(self.test_user, self.test_password, code)
        self.assertTrue(reg_ok, f"注册应当成功: {reg_msg}")

        # 5. 二次使用已用邀请码应当被拒绝
        dup_user = f"user2_{uuid.uuid4().hex[:6]}"
        reg2_ok, reg2_msg = register_user(dup_user, self.test_password, code)
        self.assertFalse(reg2_ok, "使用已过的邀请码应当被拒绝")

        # 6. 新用户鉴权登录
        auth_ok, u_data, a_msg = authenticate_user(self.test_user, self.test_password)
        self.assertTrue(auth_ok)
        self.assertEqual(u_data["role"], "user")

    def test_03_account_freezing_and_password_change(self):
        """验证用户修改密码及管理员冻结/解冻逻辑"""
        # 1. 产生测试账号
        gen_ok, _, code = generate_invite_code(DEFAULT_ADMIN_USERNAME)
        register_user(self.test_user, self.test_password, code)

        # 2. 测试修改密码
        new_pwd = "BrandNewPassword2026!"
        chg_ok, chg_msg = change_password(self.test_user, self.test_password, new_pwd)
        self.assertTrue(chg_ok, f"修改密码应当成功: {chg_msg}")

        # 用新密码登录
        auth_ok, u_data, _ = authenticate_user(self.test_user, new_pwd)
        self.assertTrue(auth_ok)
        user_id = u_data["id"]

        # 3. 管理员冻结账号
        frz_ok, frz_msg = toggle_user_status(user_id, "frozen")
        self.assertTrue(frz_ok)

        # 冻结状态登录应当被拒绝
        frz_auth_ok, _, frz_auth_msg = authenticate_user(self.test_user, new_pwd)
        self.assertFalse(frz_auth_ok)
        self.assertIn("冻结", frz_auth_msg)

        # 4. 管理员解冻账号
        act_ok, _ = toggle_user_status(user_id, "active")
        self.assertTrue(act_ok)

        # 解冻后恢复登录
        rec_auth_ok, _, _ = authenticate_user(self.test_user, new_pwd)
        self.assertTrue(rec_auth_ok)

    def tearDown(self):
        """清理测试产生的账号与邀请码"""
        try:
            conn = get_connection_with_retry()
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM users WHERE username = %s", (self.test_user,))
                cursor.execute("DELETE FROM invite_codes WHERE used_by = %s", (self.test_user,))
            conn.commit()
            conn.close()
        except Exception:
            pass


if __name__ == '__main__':
    unittest.main()
