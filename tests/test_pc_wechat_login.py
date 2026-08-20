import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.pc_login_service import (
    consume_login_request,
    create_login_challenge,
    decide_login_request,
    poll_login_request,
    scan_login_request,
)
from app.auth.service import create_session
from app.database import Base, get_db
from app.models import Account, AuthSession, OperationLog, PcLoginRequest


class PcWechatLoginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.now = datetime(2026, 8, 20, 14, 0, 0)
        self.pepper_patch = patch("app.auth.service.settings.auth_pepper", "qr-pepper")
        self.pepper_patch.start()

        from app.auth.pc_login_api import router

        app = FastAPI()
        app.include_router(router)

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app, base_url="https://inventory.example.com")

        with self.Session() as db:
            admin = Account(
                username="admin", display_name="主管理员", role="superadmin",
                wechat_openid="openid-admin", is_active=True, session_version=1,
            )
            owner = Account(
                username="boss", display_name="老板", role="owner",
                wechat_openid="openid-owner", is_active=True, session_version=1,
            )
            employee = Account(
                username="tns008", display_name="员工", role="employee",
                wechat_openid="openid-employee", is_active=True, session_version=1,
            )
            db.add_all([admin, owner, employee])
            db.commit()
            self.admin_token = create_session(
                db, admin, "miniprogram", datetime(2099, 1, 1)
            )
            self.employee_token = create_session(
                db, employee, "miniprogram", datetime(2099, 1, 1)
            )

    def tearDown(self) -> None:
        self.client.close()
        self.pepper_patch.stop()
        self.engine.dispose()

    def headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_endpoint_happy_path_sets_secure_cookie_and_hides_secrets(self) -> None:
        created = self.client.post(
            "/api/auth/pc-login/requests",
            json={"device_summary": "Chrome · macOS"},
        )
        self.assertEqual(created.status_code, 200)
        payload = created.json()
        request_token = payload["request_token"]
        browser_secret = payload["browser_secret"]
        self.assertEqual(payload["qr_payload"], f"tns-inventory-login:v1:{request_token}")
        self.assertTrue(payload["qr_image_data_url"].startswith("data:image/png;base64,"))
        self.assertNotIn(browser_secret, payload["qr_payload"])

        summary = self.client.post(
            "/api/auth/pc-login/scan",
            headers=self.headers(self.admin_token),
            json={"request_token": request_token},
        )
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["device_summary"], "Chrome · macOS")
        self.assertEqual(summary.json()["verified_domain"], "inventory.example.com")

        approved = self.client.post(
            "/api/auth/pc-login/decision",
            headers=self.headers(self.admin_token),
            json={"request_token": request_token, "approved": True},
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json(), {"status": "approved"})

        consumed = self.client.post(
            "/api/auth/pc-login/consume",
            json={"request_token": request_token, "browser_secret": browser_secret},
        )
        self.assertEqual(consumed.status_code, 200)
        self.assertEqual(consumed.json(), {"ok": True, "redirect_to": "/admin"})
        cookie = consumed.headers["set-cookie"].lower()
        self.assertIn("tns_session=", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("secure", cookie)
        self.assertIn("samesite=strict", cookie)

        replay = self.client.post(
            "/api/auth/pc-login/consume",
            json={"request_token": request_token, "browser_secret": browser_secret},
        )
        self.assertEqual(replay.status_code, 409)
        with self.Session() as db:
            row = db.query(PcLoginRequest).one()
            self.assertNotEqual(row.request_token_hash, request_token)
            self.assertNotEqual(row.browser_secret_hash, browser_secret)
            self.assertEqual(db.query(AuthSession).filter_by(client_type="pc").count(), 1)
            logs = db.query(OperationLog).filter(
                OperationLog.action.in_(("pc_login_scanned", "pc_login_approved"))
            ).all()
            self.assertEqual(len(logs), 2)
            self.assertNotIn(request_token, repr([(x.before_data, x.after_data) for x in logs]))
            self.assertNotIn(browser_secret, repr([(x.before_data, x.after_data) for x in logs]))

    def test_employee_cannot_scan_or_approve(self) -> None:
        created = self.client.post(
            "/api/auth/pc-login/requests", json={"device_summary": "Edge"}
        ).json()
        for path, body in (
            ("/api/auth/pc-login/scan", {"request_token": created["request_token"]}),
            ("/api/auth/pc-login/decision", {"request_token": created["request_token"], "approved": True}),
        ):
            response = self.client.post(
                path, headers=self.headers(self.employee_token), json=body
            )
            self.assertEqual(response.status_code, 403)

    def test_confirmation_page_can_reload_scanned_request_summary(self) -> None:
        created = self.client.post(
            "/api/auth/pc-login/requests", json={"device_summary": "Chrome"}
        ).json()
        first = self.client.post(
            "/api/auth/pc-login/scan",
            headers=self.headers(self.admin_token),
            json={"request_token": created["request_token"]},
        )
        second = self.client.post(
            "/api/auth/pc-login/scan",
            headers=self.headers(self.admin_token),
            json={"request_token": created["request_token"]},
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["device_summary"], "Chrome")

    def test_service_rejects_wrong_secret_deny_expiry_and_replays(self) -> None:
        with self.Session() as db:
            admin = db.query(Account).filter_by(role="superadmin").one()
            challenge = create_login_challenge(db, "Firefox", "127.0.0.1", now=self.now)
            scan_login_request(db, challenge.request_token, admin, now=self.now)
            self.assertEqual(
                decide_login_request(db, challenge.request_token, admin, False, now=self.now),
                "denied",
            )
            self.assertEqual(
                poll_login_request(db, challenge.request_token, challenge.browser_secret, now=self.now),
                "denied",
            )
            with self.assertRaisesRegex(ValueError, "不可使用"):
                consume_login_request(
                    db, challenge.request_token, challenge.browser_secret, now=self.now
                )

            expiring = create_login_challenge(db, "Safari", None, now=self.now)
            with self.assertRaisesRegex(ValueError, "已过期"):
                scan_login_request(
                    db,
                    expiring.request_token,
                    admin,
                    now=self.now + timedelta(seconds=121),
                )

            approved = create_login_challenge(db, "Chrome", None, now=self.now)
            scan_login_request(db, approved.request_token, admin, now=self.now)
            decide_login_request(db, approved.request_token, admin, True, now=self.now)
            with self.assertRaisesRegex(ValueError, "浏览器验证失败"):
                consume_login_request(db, approved.request_token, "wrong", now=self.now)
            consume_login_request(
                db, approved.request_token, approved.browser_secret, now=self.now
            )
            with self.assertRaisesRegex(ValueError, "不可使用"):
                consume_login_request(
                    db, approved.request_token, approved.browser_secret, now=self.now
                )


if __name__ == "__main__":
    unittest.main()
