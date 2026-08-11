import re
import unittest
from unittest.mock import patch

import pyotp
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.pages import router as auth_router
from app.auth.service import create_owner
from app.database import Base, get_db
from app.models import Account, OperationLog


class EmployeeAdminTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.totp_secret = pyotp.random_base32()
        self.pepper_patch = patch(
            "app.auth.service.settings.auth_pepper", "employee-admin-pepper"
        )
        self.totp_patch = patch(
            "app.auth.service.settings.owner_totp_secret", self.totp_secret
        )
        self.pepper_patch.start()
        self.totp_patch.start()

        app = FastAPI()
        app.include_router(auth_router)

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app, base_url="https://testserver")
        with self.Session() as db:
            self.owner = create_owner(db, "owner", "老板", "strong-password-123")

        response = self.client.post(
            "/auth/login",
            data={
                "username": "owner",
                "password": "strong-password-123",
                "totp_code": pyotp.TOTP(self.totp_secret).now(),
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)

    def tearDown(self) -> None:
        self.client.close()
        self.totp_patch.stop()
        self.pepper_patch.stop()
        self.engine.dispose()

    def create_employee(self, username: str = "TNS008", name: str = "张三"):
        return self.client.post(
            "/admin/employees",
            data={"username": username, "display_name": name},
        )

    def employee(self, username: str = "tns008") -> Account:
        with self.Session() as db:
            account = db.query(Account).filter(Account.username == username).one()
            db.expunge(account)
            return account

    def test_employee_page_is_owner_only(self) -> None:
        self.client.cookies.clear()
        response = self.client.get("/admin/employees", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/auth/login")

    def test_create_shows_activation_once_and_audit_never_stores_it(self) -> None:
        response = self.create_employee()
        match = re.search(r"激活码[^0-9]*(\d{8})", response.text)

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(match)
        activation_code = match.group(1)
        self.assertNotIn(activation_code, self.client.get("/admin/employees").text)

        with self.Session() as db:
            log = db.query(OperationLog).filter_by(action="employee_create").one()
            self.assertEqual(log.operator_name, "老板")
            self.assertEqual(log.after_data["account_id"], self.employee().id)
            self.assertEqual(log.after_data["role"], "employee")
            self.assertNotIn(activation_code, str(log.before_data))
            self.assertNotIn(activation_code, str(log.after_data))
            self.assertNotIn(activation_code, str(log.remark))

    def test_disable_and_enable_change_employee_state(self) -> None:
        self.create_employee()
        account_id = self.employee().id

        disabled = self.client.post(
            f"/admin/employees/{account_id}/disable",
            follow_redirects=False,
        )
        self.assertEqual(disabled.status_code, 303)
        self.assertFalse(self.employee().is_active)

        enabled = self.client.post(
            f"/admin/employees/{account_id}/enable",
            follow_redirects=False,
        )
        self.assertEqual(enabled.status_code, 303)
        self.assertTrue(self.employee().is_active)

    def test_unbind_clears_openid_and_returns_one_time_activation(self) -> None:
        self.create_employee()
        with self.Session() as db:
            account = db.query(Account).filter_by(username="tns008").one()
            account.wechat_openid = "openid-private-value"
            db.commit()
            account_id = account.id

        response = self.client.post(
            f"/admin/employees/{account_id}/unbind-wechat"
        )
        match = re.search(r"激活码[^0-9]*(\d{8})", response.text)

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(match)
        self.assertNotIn("openid-private-value", response.text)
        self.assertIsNone(self.employee().wechat_openid)
        self.assertNotIn(match.group(1), self.client.get("/admin/employees").text)

    def test_regenerate_activation_returns_code_once(self) -> None:
        self.create_employee()
        account_id = self.employee().id

        response = self.client.post(
            f"/admin/employees/{account_id}/regenerate-activation"
        )
        match = re.search(r"激活码[^0-9]*(\d{8})", response.text)

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(match)
        self.assertNotIn(match.group(1), self.client.get("/admin/employees").text)

    def test_list_hides_openid_and_uses_post_confirmation_forms(self) -> None:
        self.create_employee()
        with self.Session() as db:
            account = db.query(Account).filter_by(username="tns008").one()
            account.wechat_openid = "openid-must-not-render"
            db.commit()

        response = self.client.get("/admin/employees")

        self.assertEqual(response.status_code, 200)
        self.assertIn("张三", response.text)
        self.assertIn("TNS008", response.text)
        self.assertIn("已绑定", response.text)
        self.assertNotIn("openid-must-not-render", response.text)
        self.assertIn('method="post"', response.text)
        self.assertIn("data-confirm", response.text)


if __name__ == "__main__":
    unittest.main()
