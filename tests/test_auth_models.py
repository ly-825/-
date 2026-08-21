import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.database import Base, build_engine
from app.models import Account, AuthSession, PcLoginRequest
from app.schema_migrations import TIMESTAMP_COLUMNS


class AuthModelTest(unittest.TestCase):
    def test_auth_settings_and_timestamp_migrations_have_stable_defaults(self) -> None:
        settings = Settings(_env_file=None)
        self.assertIsNone(settings.auth_pepper)
        self.assertIsNone(settings.owner_totp_secret)
        self.assertIsNone(settings.wechat_app_id)
        self.assertIsNone(settings.wechat_app_secret)
        self.assertEqual(settings.pc_session_hours, 12)
        self.assertEqual(settings.mobile_session_days, 30)
        self.assertEqual(settings.pc_login_request_seconds, 120)
        self.assertTrue(settings.legacy_password_login_enabled)
        self.assertEqual(
            TIMESTAMP_COLUMNS["accounts"],
            ("activation_expires_at", "created_at", "updated_at"),
        )
        self.assertEqual(
            TIMESTAMP_COLUMNS["auth_sessions"],
            ("expires_at", "revoked_at", "created_at", "last_seen_at"),
        )

    def test_account_and_session_models_expose_required_security_fields(self) -> None:
        self.assertGreaterEqual(
            {column.name for column in Account.__table__.columns},
            {
                "username",
                "display_name",
                "role",
                "password_hash",
                "wechat_openid",
                "activation_code_hash",
                "activation_expires_at",
                "is_active",
                "session_version",
            },
        )
        self.assertGreaterEqual(
            {column.name for column in AuthSession.__table__.columns},
            {
                "token_hash",
                "account_id",
                "session_version",
                "client_type",
                "expires_at",
                "revoked_at",
                "last_seen_at",
            },
        )
        self.assertGreaterEqual(
            {column.name for column in PcLoginRequest.__table__.columns},
            {
                "request_token_hash",
                "browser_secret_hash",
                "status",
                "device_summary",
                "source_ip",
                "approved_account_id",
                "expires_at",
                "approved_at",
                "consumed_at",
                "created_at",
            },
        )
        self.assertEqual(PcLoginRequest.__table__.c.status.index, True)
        self.assertEqual(PcLoginRequest.__table__.c.request_token_hash.unique, True)
        self.assertEqual(PcLoginRequest.__table__.c.source_ip.type.length, 64)

    def test_sqlite_engine_enables_wal_foreign_keys_and_busy_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "auth.db"
            engine = build_engine(f"sqlite:///{database_path}")
            Base.metadata.create_all(engine)

            with engine.connect() as connection:
                journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
                foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
                busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()

            self.assertEqual(journal_mode.lower(), "wal")
            self.assertEqual(foreign_keys, 1)
            self.assertEqual(busy_timeout, 5000)


if __name__ == "__main__":
    unittest.main()
