import re
import unittest
from datetime import datetime
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.account_pages import router as account_router
from app.auth.service import create_session
from app.database import Base, get_db
from app.models import Account, OperationLog


class OwnerAdminTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.pepper_patch = patch("app.auth.service.settings.auth_pepper", "admin-page-pepper")
        self.pepper_patch.start()

        app = FastAPI()
        app.include_router(account_router)

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        self.superadmin_client = TestClient(app, base_url="https://testserver")
        self.owner_client = TestClient(app, base_url="https://testserver")

        with self.Session() as db:
            superadmin = Account(
                username="admin", display_name="主管理员", role="superadmin",
                is_active=True, session_version=1,
            )
            owner = Account(
                username="boss", display_name="老板", role="owner",
                is_active=True, session_version=1,
            )
            db.add_all([superadmin, owner])
            db.commit()
            self.superadmin_id = superadmin.id
            self.owner_id = owner.id
            super_token = create_session(db, superadmin, "pc", datetime(2099, 1, 1))
            owner_token = create_session(db, owner, "pc", datetime(2099, 1, 1))
        self.superadmin_client.cookies.set("tns_session", super_token)
        self.owner_client.cookies.set("tns_session", owner_token)

    def tearDown(self) -> None:
        self.superadmin_client.close()
        self.owner_client.close()
        self.pepper_patch.stop()
        self.engine.dispose()

    def test_role_hierarchy_controls_account_creation(self) -> None:
        created_owner = self.superadmin_client.post(
            "/admin/accounts/owners",
            data={"username": "boss1", "display_name": "老板一"},
        )
        denied_owner = self.owner_client.post(
            "/admin/accounts/owners",
            data={"username": "boss2", "display_name": "老板二"},
        )
        created_employee = self.owner_client.post(
            "/admin/accounts/employees",
            data={"username": "TNS009", "display_name": "李四"},
        )

        self.assertEqual(created_owner.status_code, 200)
        self.assertEqual(denied_owner.status_code, 403)
        self.assertEqual(created_employee.status_code, 200)
        self.assertRegex(created_owner.text, re.compile(r"\d{8}"))
        self.assertRegex(created_employee.text, re.compile(r"\d{8}"))

    def test_owner_cannot_mutate_owner_or_superadmin(self) -> None:
        for account_id in (self.owner_id, self.superadmin_id):
            response = self.owner_client.post(
                f"/admin/accounts/{account_id}/disable", follow_redirects=False
            )
            self.assertEqual(response.status_code, 403)

    def test_superadmin_can_mutate_owner_but_never_superadmin(self) -> None:
        disabled = self.superadmin_client.post(
            f"/admin/accounts/{self.owner_id}/disable", follow_redirects=False
        )
        protected = self.superadmin_client.post(
            f"/admin/accounts/{self.superadmin_id}/disable", follow_redirects=False
        )
        self.assertEqual(disabled.status_code, 303)
        self.assertEqual(protected.status_code, 403)

    def test_page_hides_openid_and_audit_hides_one_time_code(self) -> None:
        response = self.superadmin_client.post(
            "/admin/accounts/owners",
            data={"username": "boss3", "display_name": "老板三"},
        )
        code = re.search(r"激活码[^0-9]*(\d{8})", response.text).group(1)
        with self.Session() as db:
            account = db.query(Account).filter_by(username="boss3").one()
            account.wechat_openid = "openid-never-render"
            db.commit()
            log = db.query(OperationLog).filter_by(action="account_create").one()
            self.assertNotIn(code, repr((log.before_data, log.after_data, log.remark)))
        page = self.superadmin_client.get("/admin/accounts")
        self.assertNotIn("openid-never-render", page.text)
        self.assertNotIn(code, page.text)

    def test_failed_audit_rolls_back_privileged_account_creation(self) -> None:
        with patch(
            "app.auth.account_pages.record_operation_log",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                self.superadmin_client.post(
                    "/admin/accounts/owners",
                    data={"username": "rollback-owner", "display_name": "回滚老板"},
                )
        with self.Session() as db:
            self.assertIsNone(db.query(Account).filter_by(username="rollback-owner").first())


if __name__ == "__main__":
    unittest.main()
