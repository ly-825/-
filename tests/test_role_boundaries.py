import unittest
from unittest.mock import patch

import pyotp
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.pages import router as auth_pages_router
from app.auth.dependencies import require_mobile_account
from app.auth.service import activate_employee, create_employee, create_owner
from app.database import Base, get_db
from app.models import OperationLog, ProductDrawing
from app.routers.mobile import router as mobile_router
from app.main import app as production_app


class RoleBoundaryTest(unittest.TestCase):
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
            "app.auth.service.settings.auth_pepper", "role-boundary-pepper"
        )
        self.totp_patch = patch(
            "app.auth.service.settings.owner_totp_secret", self.totp_secret
        )
        self.pepper_patch.start()
        self.totp_patch.start()

        app = FastAPI()
        app.include_router(auth_pages_router)
        app.include_router(mobile_router, prefix="/api/mobile")

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
            _, activation_code = create_employee(db, "TNS008", "张三")
            _, self.employee_token = activate_employee(
                db, "TNS008", activation_code, "openid-employee"
            )
            drawing = ProductDrawing(
                product_code="SEC-100",
                product_name="安全产品",
                dxf_file_url="/private/drawings/sec-100.dxf",
                preview_file_url="/private/previews/sec-100.pdf",
                file_hash="sensitive-file-hash",
                parse_result_json={"recognition": "sensitive raw text"},
                material="65Mn",
                product_thickness=1.2,
                plate_thickness=1.0,
                max_outer_diameter=100,
                confirmed=1,
                is_active=1,
            )
            db.add(drawing)
            db.commit()
            self.drawing_id = drawing.id

        login = self.client.post(
            "/auth/login",
            data={
                "username": "owner",
                "password": "strong-password-123",
                "totp_code": pyotp.TOTP(self.totp_secret).now(),
            },
            follow_redirects=False,
        )
        self.assertEqual(login.status_code, 303)

    def tearDown(self) -> None:
        self.client.close()
        self.totp_patch.stop()
        self.pepper_patch.stop()
        self.engine.dispose()

    def employee_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.employee_token}"}

    def test_employee_cannot_list_drawings(self) -> None:
        response = self.client.get(
            "/api/mobile/drawings",
            headers=self.employee_headers(),
        )
        self.assertEqual(response.status_code, 403)

    def test_employee_cannot_upload_detail_confirm_or_delete_drawings(self) -> None:
        cases = (
            ("POST", "/api/mobile/drawings/upload", {"files": {"file": ("test.dxf", b"0")}}),
            ("GET", f"/api/mobile/drawings/{self.drawing_id}", {}),
            ("POST", f"/api/mobile/drawings/{self.drawing_id}/confirm", {"json": {}}),
            ("DELETE", f"/api/mobile/drawings/{self.drawing_id}", {}),
        )
        for method, path, kwargs in cases:
            with self.subTest(path=path):
                response = self.client.request(
                    method,
                    path,
                    headers=self.employee_headers(),
                    **kwargs,
                )
                self.assertEqual(response.status_code, 403)

    def test_owner_can_list_sensitive_drawings_with_pc_session(self) -> None:
        response = self.client.get("/api/mobile/drawings")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["dxf_file_url"], "/private/drawings/sec-100.dxf")

    def test_product_options_exclude_every_sensitive_drawing_field(self) -> None:
        response = self.client.get(
            "/api/mobile/product-options",
            headers=self.employee_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()[0]),
            {
                "id",
                "product_code",
                "product_name",
                "material",
                "product_thickness",
                "plate_thickness",
            },
        )
        for secret in ("dxf_file_url", "preview_file_url", "file_hash", "parse_result_json"):
            self.assertNotIn(secret, response.text)

    def test_business_routes_require_employee_session(self) -> None:
        self.client.cookies.clear()
        anonymous = self.client.get("/api/mobile/summary")
        employee = self.client.get(
            "/api/mobile/summary",
            headers=self.employee_headers(),
        )

        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(employee.status_code, 200)

    def test_verified_employee_name_overrides_payload_operator_in_audit_log(self) -> None:
        response = self.client.post(
            "/api/mobile/products/inbound",
            headers=self.employee_headers(),
            json={
                "drawing_id": self.drawing_id,
                "quantity": 1,
                "location": "A1",
                "operator_name": "伪造姓名",
                "client_request_id": "verified-actor-1",
            },
        )

        self.assertEqual(response.status_code, 200)
        with self.Session() as db:
            log = db.query(OperationLog).filter_by(action="product_inbound").one()
            self.assertEqual(log.operator_name, "张三")

    def test_every_registered_business_router_requires_employee_session(self) -> None:
        route_map = {
            (route.path, method): route
            for route in production_app.routes
            for method in getattr(route, "methods", set())
        }

        for route_key in (
            ("/api/mobile/plans/drawings", "GET"),
            ("/api/mobile/raw-plates", "GET"),
            ("/api/mobile/paper-materials", "GET"),
        ):
            with self.subTest(route=route_key):
                dependency_calls = {
                    dependency.call
                    for dependency in route_map[route_key].dependant.dependencies
                }
                self.assertIn(require_mobile_account, dependency_calls)


if __name__ == "__main__":
    unittest.main()
