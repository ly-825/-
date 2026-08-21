import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.service import create_employee, resolve_session
from app.database import Base, get_db
from app.models import Account, AuthSession


class WechatAuthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.pepper_patch = patch(
            "app.auth.service.settings.auth_pepper", "wechat-test-pepper"
        )
        self.pepper_patch.start()

        from app.auth.api import router as api_router

        app = FastAPI()
        app.include_router(api_router)

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.pepper_patch.stop()
        self.engine.dispose()

    def new_employee(self, username: str = "TNS008") -> tuple[Account, str]:
        with self.Session() as db:
            account, activation_code = create_employee(db, username, "张三")
            db.expunge(account)
            return account, activation_code

    @patch("app.auth.api.exchange_code_for_openid", return_value="openid-zhangsan")
    def test_activation_returns_safe_account_and_miniprogram_session(self, exchange) -> None:
        _, activation_code = self.new_employee()

        response = self.client.post(
            "/api/auth/wechat/activate",
            json={
                "username": "TNS008",
                "activation_code": activation_code,
                "wx_code": "wx-code-1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["account"],
            {"username": "tns008", "display_name": "张三", "role": "employee"},
        )
        self.assertNotIn("openid", response.text.lower())
        self.assertNotIn("session_key", response.text.lower())
        exchange.assert_called_once_with("wx-code-1")
        with self.Session() as db:
            self.assertIsNotNone(
                resolve_session(db, response.json()["token"], "miniprogram")
            )

    @patch("app.auth.api.exchange_code_for_openid", return_value="openid-zhangsan")
    def test_bound_employee_can_login_and_read_me(self, exchange) -> None:
        _, activation_code = self.new_employee()
        activation = self.client.post(
            "/api/auth/wechat/activate",
            json={"username": "TNS008", "activation_code": activation_code, "wx_code": "first"},
        )
        login = self.client.post(
            "/api/auth/wechat/login",
            json={"wx_code": "second"},
        )
        token = login.json()["token"]

        me = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(activation.status_code, 200)
        self.assertEqual(login.status_code, 200)
        self.assertEqual(
            me.json(),
            {"username": "tns008", "display_name": "张三", "role": "employee"},
        )
        self.assertNotIn("openid", me.text.lower())

    @patch("app.auth.api.exchange_code_for_openid", return_value="openid-owner")
    def test_bound_owner_can_login_and_read_safe_identity(self, exchange) -> None:
        with self.Session() as db:
            account = Account(
                username="boss1",
                display_name="老板一",
                role="owner",
                wechat_openid="openid-owner",
                is_active=True,
                session_version=1,
            )
            db.add(account)
            db.commit()

        login = self.client.post(
            "/api/auth/wechat/login", json={"wx_code": "owner-code"}
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["account"]["role"], "owner")
        self.assertNotIn("openid", login.text.lower())

        me = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {login.json()['token']}"},
        )
        self.assertEqual(
            me.json(),
            {"username": "boss1", "display_name": "老板一", "role": "owner"},
        )

    @patch("app.auth.api.exchange_code_for_openid", return_value="openid-superadmin")
    def test_me_accepts_superadmin_and_returns_only_safe_fields(self, exchange) -> None:
        with self.Session() as db:
            db.add(Account(
                username="admin", display_name="主管理员", role="superadmin",
                wechat_openid="openid-superadmin", is_active=True, session_version=1,
            ))
            db.commit()
        login = self.client.post(
            "/api/auth/wechat/login", json={"wx_code": "admin-code"}
        )
        me = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {login.json()['token']}"},
        )
        self.assertEqual(set(me.json()), {"username", "display_name", "role"})

    @patch("app.auth.api.exchange_code_for_openid", return_value="openid-zhangsan")
    def test_activation_code_cannot_be_reused(self, exchange) -> None:
        _, activation_code = self.new_employee()
        payload = {"username": "TNS008", "activation_code": activation_code, "wx_code": "one"}

        first = self.client.post("/api/auth/wechat/activate", json=payload)
        second = self.client.post("/api/auth/wechat/activate", json=payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)

    @patch("app.auth.api.exchange_code_for_openid", return_value="openid-disabled")
    def test_disabled_employee_cannot_login(self, exchange) -> None:
        with self.Session() as db:
            account, _ = create_employee(db, "TNS009", "李四")
            account.wechat_openid = "openid-disabled"
            account.is_active = False
            db.commit()

        response = self.client.post(
            "/api/auth/wechat/login",
            json={"wx_code": "disabled-code"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("openid-disabled", response.text)

    @patch("app.config.settings.mobile_session_days", 2)
    @patch("app.auth.api.exchange_code_for_openid", return_value="openid-session-days")
    def test_login_uses_configured_mobile_session_lifetime(self, exchange) -> None:
        with self.Session() as db:
            account, _ = create_employee(db, "TNS010", "王五")
            account.wechat_openid = "openid-session-days"
            db.commit()

        response = self.client.post(
            "/api/auth/wechat/login",
            json={"wx_code": "session-days-code"},
        )

        self.assertEqual(response.status_code, 200)
        with self.Session() as db:
            session = db.query(AuthSession).order_by(AuthSession.id.desc()).first()
            self.assertAlmostEqual(
                (session.expires_at - session.created_at).total_seconds(),
                2 * 24 * 3600,
                delta=5,
            )

    @patch("app.auth.api.exchange_code_for_openid", return_value="openid-logout")
    def test_logout_revokes_mobile_session(self, exchange) -> None:
        with self.Session() as db:
            account, _ = create_employee(db, "TNS011", "赵六")
            account.wechat_openid = "openid-logout"
            db.commit()
        login = self.client.post(
            "/api/auth/wechat/login",
            json={"wx_code": "logout-code"},
        )
        token = login.json()["token"]

        logout = self.client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        me = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(logout.status_code, 200)
        self.assertEqual(logout.json(), {"ok": True})
        self.assertEqual(me.status_code, 401)

    @patch("app.services.wechat_auth.settings.wechat_app_id", "appid-test")
    @patch("app.services.wechat_auth.settings.wechat_app_secret", "secret-test")
    @patch("app.services.wechat_auth.httpx.get")
    def test_code_exchange_returns_only_openid(self, http_get) -> None:
        from app.services.wechat_auth import exchange_code_for_openid

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "openid": "openid-safe",
            "session_key": "must-never-leave-boundary",
        }
        http_get.return_value = response

        result = exchange_code_for_openid("temporary-wx-code")

        self.assertEqual(result, "openid-safe")
        called_params = http_get.call_args.kwargs["params"]
        self.assertEqual(called_params["grant_type"], "authorization_code")
        self.assertNotIn("must-never-leave-boundary", str(result))

    @patch("app.services.wechat_auth.settings.wechat_app_id", "appid-test")
    @patch("app.services.wechat_auth.settings.wechat_app_secret", "secret-test")
    @patch("app.services.wechat_auth.httpx.get")
    def test_wechat_errors_use_generic_message(self, http_get) -> None:
        from app.services.wechat_auth import exchange_code_for_openid

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"errcode": 40029, "errmsg": "invalid code detail"}
        http_get.return_value = response

        with self.assertRaisesRegex(ValueError, "微信登录失败，请重试"):
            exchange_code_for_openid("bad-code")

    @patch("app.services.wechat_auth.settings.wechat_app_id", "appid-test")
    @patch("app.services.wechat_auth.settings.wechat_app_secret", "secret-test")
    @patch("app.services.wechat_auth.httpx.get")
    def test_malformed_wechat_response_uses_generic_message(self, http_get) -> None:
        from app.services.wechat_auth import exchange_code_for_openid

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = ["unexpected"]
        http_get.return_value = response

        with self.assertRaisesRegex(ValueError, "微信登录失败，请重试"):
            exchange_code_for_openid("malformed-code")


if __name__ == "__main__":
    unittest.main()
