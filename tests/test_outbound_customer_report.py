import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.admin_pages import outbound_report_rows
from app.database import Base
from app.models import InventoryTransactionRecord, MaterialInventory
from app.services.excel_export import _outbound_report_rows


class OutboundCustomerReportTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def test_admin_report_keeps_same_model_outbound_records_separate(self) -> None:
        with self.Session() as db:
            item = MaterialInventory(
                material_code="TNX-001",
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
            first = InventoryTransactionRecord(
                inventory_id=item.id,
                transaction_type="out",
                quantity=1,
                before_quantity=10,
                after_quantity=9,
                customer_name="客户A",
                operator_name="张三",
                remark="第一单",
                created_at=datetime(2026, 6, 19, 9, 0),
            )
            second = InventoryTransactionRecord(
                inventory_id=item.id,
                transaction_type="out",
                quantity=2,
                before_quantity=9,
                after_quantity=7,
                customer_name="客户B",
                operator_name="李四",
                remark="第二单",
                created_at=datetime(2026, 6, 19, 10, 0),
            )
            db.add_all([first, second])
            db.commit()

            records = db.query(InventoryTransactionRecord).order_by(InventoryTransactionRecord.created_at.desc()).all()
            inventory_map = {item.id: item for item in db.query(MaterialInventory).all()}

            rows, total = outbound_report_rows(records, inventory_map, "product")

            self.assertEqual(total, 3)
            self.assertEqual(rows.count("<tr>"), 2)
            self.assertIn("客户A", rows)
            self.assertIn("客户B", rows)
            self.assertIn(str(first.id), rows)
            self.assertIn(str(second.id), rows)

    def test_excel_outbound_report_exports_customer_per_transaction(self) -> None:
        with self.Session() as db:
            item = MaterialInventory(
                material_code="TNX-001",
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
            db.add_all(
                [
                    InventoryTransactionRecord(
                        inventory_id=item.id,
                        transaction_type="out",
                        quantity=1,
                        before_quantity=10,
                        after_quantity=9,
                        customer_name="客户A",
                        operator_name="张三",
                        remark="第一单",
                        created_at=datetime(2026, 6, 19, 9, 0),
                    ),
                    InventoryTransactionRecord(
                        inventory_id=item.id,
                        transaction_type="out",
                        quantity=2,
                        before_quantity=9,
                        after_quantity=7,
                        customer_name="客户B",
                        operator_name="李四",
                        remark="第二单",
                        created_at=datetime(2026, 6, 19, 10, 0),
                    ),
                ]
            )
            db.commit()

            headings, rows = _outbound_report_rows(db, {"start_date": "2026-06-19", "end_date": "2026-06-19"})

            self.assertIn("客户/去向", headings)
            self.assertEqual(len(rows), 2)
            self.assertEqual([row[7] for row in rows], ["客户B", "客户A"])
            self.assertEqual([row[8] for row in rows], [2, 1])


if __name__ == "__main__":
    unittest.main()
