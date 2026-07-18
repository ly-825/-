import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import InventoryTransactionRecord, MaterialInventory
from app.schema_migrations import ensure_runtime_schema
from app.services.operation_log import inventory_snapshot


def naive_china_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)


class ChinaTimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def test_transaction_default_uses_china_time(self) -> None:
        with self.Session() as db:
            item = MaterialInventory(
                material_code="TNX-TIME",
                inventory_type="product",
                material="65Mn",
                thickness=1.2,
                shape="circle",
                quantity=10,
                location="A-01",
                status="available",
            )
            db.add(item)
            db.flush()
            record = InventoryTransactionRecord(
                inventory_id=item.id,
                transaction_type="out",
                quantity=1,
                before_quantity=10,
                after_quantity=9,
            )
            db.add(record)
            db.commit()
            db.refresh(record)

            self.assertLess(abs((record.created_at - naive_china_now()).total_seconds()), 60)

    def test_runtime_migration_shifts_existing_utc_rows_once(self) -> None:
        with self.Session() as db:
            item = MaterialInventory(
                material_code="TNX-MIGRATE",
                inventory_type="product",
                material="65Mn",
                thickness=1.2,
                shape="circle",
                quantity=10,
                location="A-01",
                status="available",
                created_at=datetime(2026, 6, 19, 8, 30),
                updated_at=datetime(2026, 6, 19, 8, 30),
            )
            db.add(item)
            db.flush()
            record = InventoryTransactionRecord(
                inventory_id=item.id,
                transaction_type="out",
                quantity=1,
                before_quantity=10,
                after_quantity=9,
                created_at=datetime(2026, 6, 19, 8, 30),
            )
            db.add(record)
            db.commit()

        ensure_runtime_schema(self.engine)
        ensure_runtime_schema(self.engine)

        with self.Session() as db:
            record = db.query(InventoryTransactionRecord).filter_by(transaction_type="out").one()
            self.assertEqual(record.created_at, datetime(2026, 6, 19, 16, 30))

    def test_raw_plate_model_is_in_inventory_snapshot(self) -> None:
        item = MaterialInventory(
            inventory_type="raw_plate",
            material_code="BATCH-1",
            raw_plate_model="MODEL-1",
            material="65Mn",
            thickness=2,
            length=1000,
            width=500,
            shape="rectangle",
            quantity=3,
            status="available",
        )

        self.assertEqual(inventory_snapshot(item)["raw_plate_model"], "MODEL-1")

    def test_runtime_migration_adds_raw_plate_model_once(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE material_inventory ("
                    "id INTEGER PRIMARY KEY, created_at DATETIME, updated_at DATETIME)"
                )
            )

        ensure_runtime_schema(engine)
        ensure_runtime_schema(engine)

        columns = {
            column["name"]
            for column in inspect(engine).get_columns("material_inventory")
        }
        self.assertIn("raw_plate_model", columns)


if __name__ == "__main__":
    unittest.main()
