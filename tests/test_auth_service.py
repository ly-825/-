import re
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import pyotp
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.security import hash_password, verify_password
from app.auth.service import (
    activate_employee,
    authenticate_owner,
    create_employee,
    create_owner,
    disable_account,
    resolve_session,
    unbind_wechat,
)
from app.database import Base
from app.models import Account, AuthSession


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


if __name__ == "__main__":
    unittest.main()
