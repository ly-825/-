import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import InventoryTransactionRecord, MaterialInventory
from app.schema_migrations import ensure_runtime_schema


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


if __name__ == "__main__":
    unittest.main()
