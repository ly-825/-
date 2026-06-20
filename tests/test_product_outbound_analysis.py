import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import InventoryTransactionRecord, MaterialInventory
from app.services.product_outbound_analysis import analyze_product_outbound


class ProductOutboundAnalysisTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def test_analyzes_product_outbound_by_customer_month_and_purpose(self) -> None:
        with self.Session() as db:
            item = MaterialInventory(
                material_code="TNX-001",
                inventory_type="product",
                material="65Mn",
                thickness=1.2,
                shape="circle",
                quantity=100,
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
                        quantity=10,
                        before_quantity=100,
                        after_quantity=90,
                        customer_name="客户A",
                        outbound_purpose="sales",
                        operator_name="张三",
                        remark="一月销售",
                        created_at=datetime(2026, 1, 5, 9, 0),
                    ),
                    InventoryTransactionRecord(
                        inventory_id=item.id,
                        transaction_type="out",
                        quantity=20,
                        before_quantity=90,
                        after_quantity=70,
                        customer_name="客户B",
                        outbound_purpose=None,
                        operator_name="李四",
                        remark="历史未分类销售",
                        created_at=datetime(2026, 2, 8, 10, 0),
                    ),
                    InventoryTransactionRecord(
                        inventory_id=item.id,
                        transaction_type="out",
                        quantity=5,
                        before_quantity=70,
                        after_quantity=65,
                        customer_name="车间",
                        outbound_purpose="internal",
                        operator_name="王五",
                        remark="内部领用",
                        created_at=datetime(2026, 2, 9, 11, 0),
                    ),
                ]
            )
            db.commit()

            result = analyze_product_outbound(
                db,
                product_code="TNX-001",
                start_date="2026-01-01",
                end_date="2026-12-31",
                purpose="sales",
            )

            self.assertEqual(result["summary"]["total_quantity"], 30)
            self.assertEqual(result["summary"]["sales_quantity"], 30)
            self.assertEqual(result["summary"]["transaction_count"], 2)
            self.assertEqual(result["summary"]["customer_count"], 2)
            self.assertEqual(result["summary"]["suggested_year_quantity"], 180)
            self.assertEqual([row["month"] for row in result["monthly_rows"]], ["2026-01", "2026-02"])
            self.assertEqual([row["quantity"] for row in result["monthly_rows"]], [10, 20])
            self.assertEqual([row["customer_name"] for row in result["detail_rows"]], ["客户B", "客户A"])
            self.assertEqual(result["detail_rows"][0]["purpose_label"], "销售/发货")

    def test_customer_filter_limits_detail_and_summary(self) -> None:
        with self.Session() as db:
            item = MaterialInventory(
                material_code="TNX-002",
                inventory_type="product",
                material="65Mn",
                thickness=1.2,
                shape="circle",
                quantity=100,
                location="A-02",
                status="available",
            )
            db.add(item)
            db.flush()
            db.add_all(
                [
                    InventoryTransactionRecord(inventory_id=item.id, transaction_type="out", quantity=8, before_quantity=100, after_quantity=92, customer_name="客户A", outbound_purpose="sales", created_at=datetime(2026, 3, 1, 9, 0)),
                    InventoryTransactionRecord(inventory_id=item.id, transaction_type="out", quantity=12, before_quantity=92, after_quantity=80, customer_name="客户B", outbound_purpose="sales", created_at=datetime(2026, 3, 2, 9, 0)),
                ]
            )
            db.commit()

            result = analyze_product_outbound(
                db,
                product_code="TNX-002",
                start_date="2026-03-01",
                end_date="2026-03-31",
                customer="客户B",
            )

            self.assertEqual(result["summary"]["total_quantity"], 12)
            self.assertEqual(result["detail_rows"][0]["customer_name"], "客户B")


if __name__ == "__main__":
    unittest.main()
