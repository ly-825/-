import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from scripts.create_owner import bootstrap_owner, validate_password


class CreateOwnerScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_validate_password_requires_twelve_characters(self) -> None:
        with self.assertRaisesRegex(ValueError, "至少需要 12 位"):
            validate_password("short")
        self.assertEqual(validate_password("long-password-123"), "long-password-123")

    def test_bootstrap_creates_one_owner_and_returns_totp_uri(self) -> None:
        with self.Session() as db:
            account, provisioning_uri = bootstrap_owner(
                db,
                username="owner",
                display_name="老板",
                password="long-password-123",
                totp_secret="JBSWY3DPEHPK3PXP",
            )
            self.assertEqual(account.role, "owner")
            self.assertIn("otpauth://totp/", provisioning_uri)
            self.assertIn("secret=JBSWY3DPEHPK3PXP", provisioning_uri)

            with self.assertRaisesRegex(ValueError, "老板账号已存在"):
                bootstrap_owner(
                    db,
                    username="second-owner",
                    display_name="另一个老板",
                    password="another-password-123",
                    totp_secret="JBSWY3DPEHPK3PXP",
                )


if __name__ == "__main__":
    unittest.main()
