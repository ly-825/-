import unittest
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    InventoryTransactionRecord,
    MaterialInventory,
    OperationLog,
)
from app.services.raw_plate_inventory import (
    create_raw_plate_specification,
    inbound_raw_plate,
    outbound_raw_plate_fifo,
    reverse_raw_plate_transaction,
    toggle_raw_plate_specification,
    update_raw_plate_batch,
)


class RawPlateInventoryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(
            bind=engine, autoflush=False, autocommit=False
        )

    def create_spec(self, db):
        return create_raw_plate_specification(
            db,
            material="65Mn",
            length=1270,
            width=130,
            thickness=3.0,
            density=7.85,
            remark="常用",
        )

    def add_batch(
        self,
        db,
        code: str,
        quantity: int,
        created_at: datetime,
        location: str = "A-01",
    ) -> MaterialInventory:
        item = MaterialInventory(
            material_code=code,
            raw_plate_model="3.0×130×1270",
            inventory_type="raw_plate",
            material="65Mn",
            thickness=3.0,
            length=1270,
            width=130,
            shape="rectangle",
            usable_size="3.0×130×1270mm",
            quantity=quantity,
            location=location,
            status="available",
            created_at=created_at,
            updated_at=created_at,
        )
        db.add(item)
        db.flush()
        return item

    def test_specification_is_normalized_and_duplicate_is_rejected(self) -> None:
        with self.Session() as db:
            spec = self.create_spec(db)
            db.flush()
            self.assertEqual(spec.spec_name, "3.0×130×1270")
            self.assertEqual(spec.thickness, 3.0)

            with self.assertRaises(HTTPException) as raised:
                self.create_spec(db)
            self.assertEqual(raised.exception.status_code, 400)

    def test_weight_inbound_returns_quantity_weight_and_remainder(self) -> None:
        with self.Session() as db:
            spec = self.create_spec(db)
            db.flush()

            receipt = inbound_raw_plate(
                db,
                specification_id=spec.id,
                raw_plate_model="",
                material_code="RAW-001",
                material="",
                total_weight_ton=1.0,
                length=None,
                width=None,
                thickness=None,
                density=None,
                location="A-01",
                operator_name="张三",
                remark="采购",
            )

            self.assertEqual(receipt["quantity"], 257)
            self.assertAlmostEqual(receipt["single_weight_kg"], 3.888105)
            self.assertAlmostEqual(receipt["remaining_weight_kg"], 0.757015)
            self.assertEqual(receipt["item"].quantity, 257)
            self.assertEqual(
                db.query(InventoryTransactionRecord)
                .filter_by(transaction_type="in")
                .count(),
                1,
            )

            toggle_raw_plate_specification(db, spec.id)
            with self.assertRaises(HTTPException) as stopped:
                inbound_raw_plate(
                    db,
                    specification_id=spec.id,
                    raw_plate_model="",
                    material_code="RAW-002",
                    material="",
                    total_weight_ton=1.0,
                    length=None,
                    width=None,
                    thickness=None,
                    density=None,
                )
            self.assertEqual(stopped.exception.status_code, 400)

    def test_fifo_outbound_allocates_oldest_batches(self) -> None:
        with self.Session() as db:
            base = datetime(2026, 8, 1, 8, 0)
            first = self.add_batch(db, "RAW-A", 5, base)
            second = self.add_batch(db, "RAW-B", 5, base + timedelta(minutes=1))

            result = outbound_raw_plate_fifo(
                db,
                material="65Mn",
                length=1270,
                width=130,
                thickness=3.0,
                quantity=8,
                location="",
                customer_name="一车间",
                operator_name="李四",
                remark="领料",
            )

            self.assertEqual(
                [row["quantity"] for row in result["allocations"]], [5, 3]
            )
            self.assertEqual((first.quantity, second.quantity), (0, 2))
            self.assertEqual(
                db.query(InventoryTransactionRecord)
                .filter_by(transaction_type="out")
                .count(),
                2,
            )

    def test_insufficient_outbound_changes_nothing(self) -> None:
        with self.Session() as db:
            batch = self.add_batch(db, "RAW-LOW", 3, datetime(2026, 8, 1, 8, 0))
            with self.assertRaises(HTTPException) as raised:
                outbound_raw_plate_fifo(
                    db,
                    material="65Mn",
                    length=1270,
                    width=130,
                    thickness=3.0,
                    quantity=4,
                )
            self.assertEqual(raised.exception.status_code, 400)
            self.assertEqual(batch.quantity, 3)
            self.assertEqual(db.query(InventoryTransactionRecord).count(), 0)

    def test_batch_update_logs_and_transaction_can_be_reversed(self) -> None:
        with self.Session() as db:
            batch = self.add_batch(db, "RAW-EDIT", 5, datetime(2026, 8, 1, 8, 0))
            update_raw_plate_batch(
                db,
                batch.id,
                raw_plate_model="3.0×130×1270",
                material_code="RAW-EDIT-2",
                material="65Mn",
                length=1270,
                width=130,
                thickness=3.0,
                location="A-02",
                status="available",
                operator_name="王五",
                remark="修正库位",
            )
            self.assertEqual(batch.location, "A-02")
            self.assertEqual(db.query(OperationLog).count(), 1)

            result = outbound_raw_plate_fifo(
                db,
                material="65Mn",
                length=1270,
                width=130,
                thickness=3.0,
                quantity=2,
            )
            transaction_id = result["allocations"][0]["transaction_id"]
            reversal = reverse_raw_plate_transaction(
                db, transaction_id, operator_name="赵六", remark="撤回测试"
            )
            self.assertEqual(reversal.transaction_type, "in")
            self.assertEqual(batch.quantity, 5)

            with self.assertRaises(HTTPException) as repeated:
                reverse_raw_plate_transaction(db, transaction_id)
            self.assertEqual(repeated.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
