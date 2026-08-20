import unittest
from unittest.mock import patch

import pyotp
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.dependencies import require_mobile_account, require_owner_account
from app.auth.pages import router as auth_router
from app.auth.service import activate_employee, create_employee, create_owner
from app.admin_pages import page
from app.database import Base, get_db
from app.main import api_schema_urls, app as production_app


class PcAuthTest(unittest.TestCase):
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
            "app.auth.service.settings.auth_pepper", "pc-test-pepper"
        )
        self.totp_patch = patch(
            "app.auth.service.settings.owner_totp_secret", self.totp_secret
        )
        self.pepper_patch.start()
        self.totp_patch.start()

        app = FastAPI()
        app.include_router(auth_router)

        @app.get("/health")
        def health() -> dict[str, str]:
            return {"status": "ok"}

        @app.get("/admin", dependencies=[Depends(require_owner_account)])
        def admin() -> dict[str, str]:
            return {"page": "admin"}

        @app.get("/mobile-private", dependencies=[Depends(require_mobile_account)])
        def mobile_private() -> dict[str, str]:
            return {"page": "mobile"}

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app, base_url="https://testserver")

        with self.Session() as db:
            create_owner(db, "owner", "老板", "strong-password-123")

    def tearDown(self) -> None:
        self.client.close()
        self.totp_patch.stop()
        self.pepper_patch.stop()
        self.engine.dispose()

    def owner_totp(self) -> str:
        return pyotp.TOTP(self.totp_secret).now()

    def login_owner(self):
        return self.client.post(
            "/auth/legacy-login",
            data={
                "username": "owner",
                "password": "strong-password-123",
                "totp_code": self.owner_totp(),
            },
            follow_redirects=False,
        )

    def test_health_is_public_and_admin_redirects_to_login(self) -> None:
        self.assertEqual(self.client.get("/health").status_code, 200)
        response = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/auth/login")

    def test_login_page_is_qr_only(self) -> None:
        response = self.client.get("/auth/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("使用小程序扫码登录", response.text)
        self.assertIn("/api/auth/pc-login/requests", response.text)
        self.assertNotIn('name="password"', response.text)

    def test_owner_login_sets_secure_cookie_and_opens_admin(self) -> None:
        response = self.login_owner()
        cookie = response.headers["set-cookie"].lower()

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin")
        self.assertIn("httponly", cookie)
        self.assertIn("secure", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertEqual(self.client.get("/admin").status_code, 200)

    def test_employee_bearer_token_cannot_open_admin(self) -> None:
        with self.Session() as db:
            _, activation_code = create_employee(db, "TNS008", "张三")
            _, employee_token = activate_employee(
                db, "TNS008", activation_code, "openid-zhangsan"
            )

        response = self.client.get(
            "/admin",
            headers={"Authorization": f"Bearer {employee_token}"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 403)

    def test_employee_bearer_token_opens_mobile_only_route(self) -> None:
        with self.Session() as db:
            _, activation_code = create_employee(db, "TNS009", "李四")
            _, employee_token = activate_employee(
                db, "TNS009", activation_code, "openid-lisi"
            )

        response = self.client.get(
            "/mobile-private",
            headers={"Authorization": f"Bearer {employee_token}"},
        )

        self.assertEqual(response.status_code, 200)

    def test_logout_revokes_session_and_clears_cookie(self) -> None:
        self.login_owner()
        self.assertEqual(self.client.get("/admin").status_code, 200)

        response = self.client.post("/auth/logout", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/auth/login")
        self.assertIn("max-age=0", response.headers["set-cookie"].lower())
        self.assertEqual(
            self.client.get("/admin", follow_redirects=False).status_code,
            303,
        )

    def test_wrong_password_and_wrong_totp_use_identical_error(self) -> None:
        wrong_password = self.client.post(
            "/auth/legacy-login",
            data={
                "username": "owner",
                "password": "wrong-password",
                "totp_code": self.owner_totp(),
            },
        )
        wrong_totp = self.client.post(
            "/auth/legacy-login",
            data={
                "username": "owner",
                "password": "strong-password-123",
                "totp_code": "000000",
            },
        )

        self.assertEqual(wrong_password.status_code, 401)
        self.assertEqual(wrong_totp.status_code, 401)
        self.assertIn("账号或验证信息不正确", wrong_password.text)
        self.assertEqual(wrong_password.text, wrong_totp.text)

    def test_legacy_login_can_be_disabled(self) -> None:
        with patch("app.auth.pages.settings.legacy_password_login_enabled", False):
            self.assertEqual(self.client.get("/auth/legacy-login").status_code, 404)
            self.assertEqual(
                self.client.post(
                    "/auth/legacy-login",
                    data={
                        "username": "owner",
                        "password": "strong-password-123",
                        "totp_code": "000000",
                    },
                ).status_code,
                404,
            )

    def test_production_app_wires_public_login_and_owner_protected_routes(self) -> None:
        route_map = {
            (route.path, method): route
            for route in production_app.routes
            for method in getattr(route, "methods", set())
        }

        self.assertIn(("/auth/login", "GET"), route_map)
        self.assertIn(("/auth/legacy-login", "GET"), route_map)
        for key in (
            ("/admin", "GET"),
            ("/api/drawings/upload", "POST"),
            ("/api/inventory", "GET"),
            ("/admin/mobile-connection", "GET"),
        ):
            with self.subTest(route=key):
                dependency_calls = {
                    dependency.call
                    for dependency in route_map[key].dependant.dependencies
                }
                self.assertIn(require_owner_account, dependency_calls)

        health_dependencies = {
            dependency.call
            for dependency in route_map[("/health", "GET")].dependant.dependencies
        }
        self.assertNotIn(require_owner_account, health_dependencies)

    def test_admin_navigation_contains_post_logout_action(self) -> None:
        html = page("测试", "").body.decode("utf-8")
        self.assertIn('action="/auth/logout"', html)
        self.assertIn('method="post"', html)
        self.assertIn("退出登录", html)

    def test_production_mode_disables_interactive_api_schema(self) -> None:
        self.assertEqual(
            api_schema_urls(production=True),
            {"docs_url": None, "redoc_url": None, "openapi_url": None},
        )


if __name__ == "__main__":
    unittest.main()
