import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    InventoryTransactionRecord,
    MaterialInventory,
    MobileRequestRecord,
    ProductDrawing,
)
from app.routers.mobile import (
    ProductInboundPayload,
    ProductOutboundPayload,
    ScrapConfirmPayload,
    ScrapOutboundPayload,
    TransactionReversePayload,
    confirm_scrap,
    product_inbound,
    product_outbound,
    reverse_product_transaction,
    reverse_scrap_transaction,
    scrap_outbound,
)
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

    def add_drawing(self, db, code: str = "TNX-MOBILE-001") -> ProductDrawing:
        drawing = ProductDrawing(
            product_code=code,
            product_name="幂等测试产品",
            dxf_file_url=f"/tmp/{code}.dxf",
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
        return drawing

    def add_inventory(
        self,
        db,
        *,
        inventory_type: str,
        quantity: int,
        status: str = "available",
        code: str | None = None,
        location: str = "A-01",
    ) -> MaterialInventory:
        item = MaterialInventory(
            material_code=code,
            inventory_type=inventory_type,
            material="65Mn",
            thickness=1.2,
            shape="circle",
            diameter=50,
            usable_size="φ50",
            quantity=quantity,
            location=location,
            status=status,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

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
            drawing = self.add_drawing(db)

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

    def test_product_outbound_replays_and_rejects_changed_payload(self) -> None:
        with self.Session() as db:
            drawing = self.add_drawing(db, "TNX-MOBILE-OUT")
            item = self.add_inventory(
                db,
                inventory_type="product",
                quantity=10,
                code=drawing.product_code,
            )
            payload = ProductOutboundPayload(
                client_request_id="mobile-outbound-001",
                drawing_id=drawing.id,
                quantity=3,
                location="A-01",
                operator_name="测试员",
            )
            first = product_outbound(payload, db=db)
            second = product_outbound(payload, db=db)
            db.refresh(item)

            self.assertEqual(first, second)
            self.assertEqual(item.quantity, 7)
            self.assertEqual(
                db.query(InventoryTransactionRecord)
                .filter(InventoryTransactionRecord.transaction_type == "out")
                .count(),
                1,
            )
            with self.assertRaises(HTTPException) as raised:
                product_outbound(payload.model_copy(update={"quantity": 4}), db=db)
            self.assertEqual(raised.exception.status_code, 409)

    def test_scrap_confirm_replays_and_rejects_changed_payload(self) -> None:
        with self.Session() as db:
            item = self.add_inventory(
                db,
                inventory_type="scrap",
                quantity=1,
                status="pending",
                location="待入库",
            )
            payload = ScrapConfirmPayload(
                client_request_id="mobile-scrap-confirm-001",
                actual_quantity=4,
                actual_diameter=52,
                location="S-01",
                operator_name="测试员",
            )
            first = confirm_scrap(item.id, payload, db=db)
            second = confirm_scrap(item.id, payload, db=db)
            db.refresh(item)

            self.assertEqual(first, second)
            self.assertEqual((item.quantity, item.location, item.status), (4, "S-01", "available"))
            self.assertEqual(
                db.query(InventoryTransactionRecord)
                .filter(InventoryTransactionRecord.transaction_type == "confirm")
                .count(),
                1,
            )
            with self.assertRaises(HTTPException) as raised:
                confirm_scrap(
                    item.id,
                    payload.model_copy(update={"actual_quantity": 5}),
                    db=db,
                )
            self.assertEqual(raised.exception.status_code, 409)

    def test_scrap_outbound_replays_and_rejects_changed_payload(self) -> None:
        with self.Session() as db:
            item = self.add_inventory(
                db,
                inventory_type="scrap",
                quantity=8,
            )
            payload = ScrapOutboundPayload(
                client_request_id="mobile-scrap-outbound-001",
                scrap_group_key="65Mn||1.2||φ50||A-01",
                quantity=3,
                operator_name="测试员",
            )
            first = scrap_outbound(payload, db=db)
            second = scrap_outbound(payload, db=db)
            db.refresh(item)

            self.assertEqual(first, second)
            self.assertEqual(item.quantity, 5)
            self.assertEqual(
                db.query(InventoryTransactionRecord)
                .filter(InventoryTransactionRecord.transaction_type == "out")
                .count(),
                1,
            )
            with self.assertRaises(HTTPException) as raised:
                scrap_outbound(payload.model_copy(update={"quantity": 4}), db=db)
            self.assertEqual(raised.exception.status_code, 409)

    def test_product_reverse_replays_and_rejects_changed_payload(self) -> None:
        with self.Session() as db:
            item = self.add_inventory(
                db,
                inventory_type="product",
                quantity=5,
                code="TNX-REV-PRODUCT",
            )
            original = InventoryTransactionRecord(
                inventory_id=item.id,
                transaction_type="in",
                quantity=2,
                before_quantity=3,
                after_quantity=5,
            )
            db.add(original)
            db.commit()
            db.refresh(original)
            payload = TransactionReversePayload(
                client_request_id="mobile-product-reverse-001",
                operator_name="测试员",
                remark="录入错误",
            )
            first = reverse_product_transaction(original.id, payload, db=db)
            second = reverse_product_transaction(original.id, payload, db=db)
            db.refresh(item)

            self.assertEqual(first, second)
            self.assertEqual(item.quantity, 3)
            self.assertEqual(
                db.query(InventoryTransactionRecord)
                .filter(InventoryTransactionRecord.reversed_transaction_id == original.id)
                .count(),
                1,
            )
            with self.assertRaises(HTTPException) as raised:
                reverse_product_transaction(
                    original.id,
                    payload.model_copy(update={"remark": "另一个原因"}),
                    db=db,
                )
            self.assertEqual(raised.exception.status_code, 409)

    def test_scrap_reverse_replays_and_rejects_changed_payload(self) -> None:
        with self.Session() as db:
            item = self.add_inventory(
                db,
                inventory_type="scrap",
                quantity=2,
            )
            original = InventoryTransactionRecord(
                inventory_id=item.id,
                transaction_type="out",
                quantity=3,
                before_quantity=5,
                after_quantity=2,
            )
            db.add(original)
            db.commit()
            db.refresh(original)
            payload = TransactionReversePayload(
                client_request_id="mobile-scrap-reverse-001",
                operator_name="测试员",
                remark="出库错误",
            )
            first = reverse_scrap_transaction(original.id, payload, db=db)
            second = reverse_scrap_transaction(original.id, payload, db=db)
            db.refresh(item)

            self.assertEqual(first, second)
            self.assertEqual(item.quantity, 5)
            self.assertEqual(
                db.query(InventoryTransactionRecord)
                .filter(InventoryTransactionRecord.reversed_transaction_id == original.id)
                .count(),
                1,
            )
            with self.assertRaises(HTTPException) as raised:
                reverse_scrap_transaction(
                    original.id,
                    payload.model_copy(update={"remark": "另一个原因"}),
                    db=db,
                )
            self.assertEqual(raised.exception.status_code, 409)
