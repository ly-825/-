import re
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import pyotp
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.security import hash_password, verify_password
from app.auth.service import (
    activate_account,
    activate_employee,
    authenticate_owner,
    create_managed_account,
    create_employee,
    create_owner,
    disable_account,
    login_bound_wechat,
    regenerate_activation,
    resolve_session,
    unbind_wechat,
)
from app.auth.roles import EMPLOYEE, OWNER, SUPERADMIN
from app.database import Base
from app.models import Account, AuthSession
from scripts.manage_superadmin import bootstrap_superadmin


class AuthServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.now = datetime(2026, 8, 11, 10, 0, 0)
        self.pepper_patch = patch("app.auth.service.settings.auth_pepper", "test-pepper")
        self.totp_secret = pyotp.random_base32()
        self.totp_patch = patch(
            "app.auth.service.settings.owner_totp_secret", self.totp_secret
        )
        self.pepper_patch.start()
        self.totp_patch.start()

    def tearDown(self) -> None:
        self.totp_patch.stop()
        self.pepper_patch.stop()
        self.engine.dispose()

    def test_password_hash_is_not_plaintext_and_verifies(self) -> None:
        encoded = hash_password("correct horse battery staple")

        self.assertNotEqual(encoded, "correct horse battery staple")
        self.assertTrue(verify_password(encoded, "correct horse battery staple"))
        self.assertFalse(verify_password(encoded, "wrong password"))

    def test_activation_and_session_store_only_hashes(self) -> None:
        with self.Session() as db:
            account, activation_code = create_employee(
                db, "TNS008", "张三", now=self.now
            )
            self.assertRegex(activation_code, re.compile(r"^\d{8}$"))
            self.assertNotEqual(account.activation_code_hash, activation_code)

            activated_account, token = activate_employee(
                db,
                "TNS008",
                activation_code,
                "openid-zhangsan",
                now=self.now,
            )
            session = db.query(AuthSession).one()

            self.assertEqual(activated_account.wechat_openid, "openid-zhangsan")
            self.assertIsNone(activated_account.activation_code_hash)
            self.assertNotEqual(session.token_hash, token)
            self.assertEqual(
                resolve_session(db, token, "miniprogram", now=self.now),
                activated_account,
            )

    def test_activation_rejects_expired_and_reused_code(self) -> None:
        with self.Session() as db:
            _, expired_code = create_employee(db, "TNS009", "李四", now=self.now)
            with self.assertRaisesRegex(ValueError, "激活码无效或已过期"):
                activate_employee(
                    db,
                    "TNS009",
                    expired_code,
                    "openid-expired",
                    now=self.now + timedelta(hours=25),
                )

            _, valid_code = create_employee(db, "TNS010", "王五", now=self.now)
            activate_employee(
                db, "TNS010", valid_code, "openid-wangwu", now=self.now
            )
            with self.assertRaisesRegex(ValueError, "激活码无效或已过期"):
                activate_employee(
                    db, "TNS010", valid_code, "openid-another", now=self.now
                )

    def test_disabling_or_unbinding_invalidates_existing_session(self) -> None:
        with self.Session() as db:
            account, code = create_employee(db, "TNS011", "赵六", now=self.now)
            _, token = activate_employee(
                db, "TNS011", code, "openid-zhaoliu", now=self.now
            )
            self.assertIsNotNone(resolve_session(db, token, "miniprogram", now=self.now))

            disable_account(db, account)
            self.assertIsNone(resolve_session(db, token, "miniprogram", now=self.now))

            account.is_active = True
            db.commit()
            new_code = unbind_wechat(db, account, now=self.now)
            self.assertRegex(new_code, re.compile(r"^\d{8}$"))
            self.assertIsNone(account.wechat_openid)
            self.assertIsNone(resolve_session(db, token, "miniprogram", now=self.now))

    def test_owner_login_requires_password_and_current_totp(self) -> None:
        with self.Session() as db:
            create_owner(db, "owner", "老板", "strong-password-123")
            current_code = pyotp.TOTP(self.totp_secret).at(self.now)

            account, token = authenticate_owner(
                db,
                "owner",
                "strong-password-123",
                current_code,
                now=self.now,
            )
            self.assertEqual(account.role, "owner")
            self.assertIsNotNone(resolve_session(db, token, "pc", now=self.now))

            for password, code in (
                ("wrong-password", current_code),
                ("strong-password-123", "000000"),
            ):
                with self.subTest(password=password, code=code):
                    with self.assertRaisesRegex(ValueError, "账号或验证信息不正确"):
                        authenticate_owner(db, "owner", password, code, now=self.now)

    def test_owner_and_employee_usernames_are_unique(self) -> None:
        with self.Session() as db:
            create_owner(db, "owner", "老板", "strong-password-123")
            with self.assertRaisesRegex(ValueError, "账号已存在"):
                create_employee(db, "owner", "重名员工", now=self.now)
            self.assertEqual(db.query(Account).count(), 1)

    def test_superadmin_creates_owner_with_short_lived_activation(self) -> None:
        with self.Session() as db:
            superadmin, _ = bootstrap_superadmin(
                db, "admin", "主管理员", now=self.now
            )
            owner, owner_code = create_managed_account(
                db,
                actor=superadmin,
                username="boss1",
                display_name="老板一",
                role=OWNER,
                now=self.now,
            )

            self.assertEqual(owner.activation_expires_at, self.now + timedelta(minutes=30))
            bound_owner, token = activate_account(
                db, "boss1", owner_code, "openid-owner-1", now=self.now
            )
            self.assertEqual(bound_owner.role, OWNER)
            self.assertIsNotNone(resolve_session(db, token, "miniprogram", now=self.now))

    def test_role_hierarchy_and_activation_lifetimes(self) -> None:
        with self.Session() as db:
            superadmin, _ = bootstrap_superadmin(
                db, "admin", "主管理员", now=self.now
            )
            owner, _ = create_managed_account(
                db, actor=superadmin, username="boss1", display_name="老板一",
                role=OWNER, now=self.now,
            )
            employee, _ = create_managed_account(
                db, actor=owner, username="tns020", display_name="员工",
                role=EMPLOYEE, now=self.now,
            )
            self.assertEqual(employee.activation_expires_at, self.now + timedelta(hours=24))
            with self.assertRaisesRegex(ValueError, "无权创建该角色账号"):
                create_managed_account(
                    db, actor=owner, username="boss2", display_name="老板二",
                    role=OWNER, now=self.now,
                )
            with self.assertRaisesRegex(ValueError, "无权创建该角色账号"):
                create_managed_account(
                    db, actor=employee, username="tns021", display_name="员工二",
                    role=EMPLOYEE, now=self.now,
                )

    def test_generic_binding_rejects_duplicate_openid_without_role_leak(self) -> None:
        with self.Session() as db:
            first, first_code = create_employee(db, "tns030", "员工甲", now=self.now)
            activate_account(db, first.username, first_code, "shared-openid", now=self.now)
            second, second_code = create_employee(db, "tns031", "员工乙", now=self.now)
            with self.assertRaisesRegex(ValueError, "^微信已绑定其他账号$"):
                activate_account(db, second.username, second_code, "shared-openid", now=self.now)

    def test_employee_compatibility_activation_rejects_admin_account(self) -> None:
        with self.Session() as db:
            superadmin, code = bootstrap_superadmin(
                db, "admin", "主管理员", now=self.now
            )
            with self.assertRaisesRegex(ValueError, "激活码无效或已过期"):
                activate_employee(
                    db, superadmin.username, code, "admin-openid", now=self.now
                )
            db.refresh(superadmin)
            self.assertIsNone(superadmin.wechat_openid)
            self.assertIsNotNone(superadmin.activation_code_hash)

    def test_bound_wechat_login_and_revocation_are_role_neutral(self) -> None:
        with self.Session() as db:
            superadmin, code = bootstrap_superadmin(
                db, "admin", "主管理员", now=self.now
            )
            account, _ = activate_account(
                db, superadmin.username, code, "admin-openid", now=self.now
            )
            version = account.session_version
            logged_in, token = login_bound_wechat(db, "admin-openid", now=self.now)
            self.assertEqual(logged_in.role, SUPERADMIN)
            self.assertIsNotNone(resolve_session(db, token, "miniprogram", now=self.now))

            unbind_wechat(db, account, now=self.now)
            self.assertEqual(account.session_version, version + 1)
            self.assertEqual(account.activation_expires_at, self.now + timedelta(minutes=30))
            self.assertIsNone(resolve_session(db, token, "miniprogram", now=self.now))

            regenerated_version = account.session_version
            regenerate_activation(db, account, now=self.now)
            self.assertEqual(account.session_version, regenerated_version + 1)
            self.assertEqual(account.activation_expires_at, self.now + timedelta(minutes=30))


if __name__ == "__main__":
    unittest.main()
