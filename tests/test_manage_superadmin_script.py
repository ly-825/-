import re
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.auth.service import activate_account, resolve_session
from app.database import Base
from app.models import Account
from scripts.manage_superadmin import bootstrap_superadmin, reset_superadmin_wechat


class ManageSuperadminScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.now = datetime(2026, 8, 20, 12, 0, 0)
        self.pepper_patch = patch("app.auth.service.settings.auth_pepper", "test-pepper")
        self.pepper_patch.start()

    def tearDown(self) -> None:
        self.pepper_patch.stop()
        self.engine.dispose()

    def test_bootstrap_creates_exactly_one_passwordless_superadmin(self) -> None:
        with self.Session() as db:
            account, code = bootstrap_superadmin(db, "admin", "主管理员", now=self.now)
            self.assertEqual(account.role, "superadmin")
            self.assertIsNone(account.password_hash)
            self.assertRegex(code, re.compile(r"^\d{8}$"))
            self.assertNotEqual(account.activation_code_hash, code)
            self.assertEqual(account.activation_expires_at, self.now + timedelta(minutes=30))
            with self.assertRaisesRegex(ValueError, "主管理员已存在"):
                bootstrap_superadmin(db, "admin2", "第二管理员", now=self.now)

    def test_reset_requires_named_sole_superadmin_and_revokes_sessions(self) -> None:
        with self.Session() as db:
            account, code = bootstrap_superadmin(db, "admin", "主管理员", now=self.now)
            account, token = activate_account(
                db, account.username, code, "admin-openid", now=self.now
            )
            previous_version = account.session_version
            reset_account, replacement_code = reset_superadmin_wechat(
                db, "admin", now=self.now
            )
            self.assertEqual(reset_account.session_version, previous_version + 1)
            self.assertIsNone(reset_account.wechat_openid)
            self.assertRegex(replacement_code, re.compile(r"^\d{8}$"))
            self.assertIsNone(resolve_session(db, token, "miniprogram", now=self.now))
            with self.assertRaisesRegex(ValueError, "主管理员账号不存在"):
                reset_superadmin_wechat(db, "missing", now=self.now)

    def test_database_rejects_second_superadmin_even_without_cli(self) -> None:
        with self.Session() as db:
            db.add_all([
                Account(username="admin1", display_name="一", role="superadmin", is_active=True, session_version=1),
                Account(username="admin2", display_name="二", role="superadmin", is_active=True, session_version=1),
            ])
            with self.assertRaises(IntegrityError):
                db.commit()


if __name__ == "__main__":
    unittest.main()
