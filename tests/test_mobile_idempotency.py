import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import MaterialInventory, MobileRequestRecord, ProductDrawing
from app.routers.mobile import ProductInboundPayload, product_inbound
from app.schema_migrations import ensure_runtime_schema
from app.services.mobile_idempotency import (
    remember_mobile_response,
    replayed_mobile_response,
)


class MobileIdempotencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(
            bind=self.engine, autoflush=False, autocommit=False
        )

    def test_runtime_schema_creates_mobile_request_table_idempotently(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        ensure_runtime_schema(engine)
        ensure_runtime_schema(engine)
        inspector = inspect(engine)
        self.assertIn("mobile_request_records", inspector.get_table_names())
        unique_column_sets = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("mobile_request_records")
        }
        self.assertIn(
            ("operation_type", "client_request_id"),
            unique_column_sets,
        )

    def test_same_request_replays_original_response(self) -> None:
        payload = {"drawing_id": 12, "quantity": 5}
        with self.Session() as db:
            remember_mobile_response(
                db,
                "product_outbound",
                "req-001",
                payload,
                {"after_quantity": 7},
            )
            db.commit()
            replay = replayed_mobile_response(
                db, "product_outbound", "req-001", payload
            )
            self.assertEqual(replay, {"after_quantity": 7})
            self.assertEqual(db.query(MobileRequestRecord).count(), 1)

    def test_same_id_with_changed_payload_returns_conflict(self) -> None:
        with self.Session() as db:
            remember_mobile_response(
                db,
                "product_outbound",
                "req-002",
                {"quantity": 5},
                {"after_quantity": 7},
            )
            db.commit()
            with self.assertRaises(HTTPException) as raised:
                replayed_mobile_response(
                    db,
                    "product_outbound",
                    "req-002",
                    {"quantity": 6},
                )
            self.assertEqual(raised.exception.status_code, 409)

    def test_blank_request_id_is_rejected(self) -> None:
        with self.Session() as db, self.assertRaises(HTTPException) as raised:
            replayed_mobile_response(db, "product_outbound", "  ", {"quantity": 1})
        self.assertEqual(raised.exception.status_code, 422)

    def test_product_inbound_mutates_inventory_once_for_duplicate_request(self) -> None:
        with self.Session() as db:
            drawing = ProductDrawing(
                product_code="TNX-MOBILE-001",
                product_name="幂等测试产品",
                dxf_file_url="/tmp/mobile-idempotency.dxf",
                material="65Mn",
                product_thickness=1.2,
                plate_thickness=1.0,
                max_outer_diameter=100,
                confirmed=1,
                is_active=1,
            )
            db.add(drawing)
            db.commit()
            db.refresh(drawing)

            payload = ProductInboundPayload(
                client_request_id="mobile-inbound-001",
                drawing_id=drawing.id,
                quantity=3,
                location="A-01",
                operator_name="测试员",
            )
            first = product_inbound(payload, db=db)
            second = product_inbound(payload, db=db)

            self.assertEqual(first["id"], second["id"])
            total = sum(
                item.quantity
                for item in db.query(MaterialInventory)
                .filter(
                    MaterialInventory.inventory_type == "product",
                    MaterialInventory.material_code == "TNX-MOBILE-001",
                )
                .all()
            )
            self.assertEqual(total, 3)
            self.assertEqual(db.query(MobileRequestRecord).count(), 1)

            changed = ProductInboundPayload(
                client_request_id="mobile-inbound-001",
                drawing_id=drawing.id,
                quantity=4,
                location="A-01",
                operator_name="测试员",
            )
            with self.assertRaises(HTTPException) as raised:
                product_inbound(changed, db=db)
            self.assertEqual(raised.exception.status_code, 409)
