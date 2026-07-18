import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.admin_pages import product_outbound_analysis_page
from app.database import Base
from app.models import InventoryTransactionRecord, MaterialInventory
from app.services.excel_export import build_export_rows
from app.services.product_outbound_analysis import (
    analyze_product_flow,
    analyze_product_outbound,
    product_flow_analysis_export_rows,
)


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

    def test_inbound_analysis_excludes_outbound_reversed_and_non_product_records(self) -> None:
        with self.Session() as db:
            product = MaterialInventory(
                material_code="IN-1",
                inventory_type="product",
                material="65Mn",
                thickness=1.2,
                shape="circle",
                quantity=10,
                location="A-01",
                status="available",
            )
            raw_plate = MaterialInventory(
                material_code="RAW-1",
                inventory_type="raw_plate",
                material="65Mn",
                thickness=1.2,
                length=1000,
                width=500,
                shape="rectangle",
                quantity=7,
                status="available",
            )
            db.add_all([product, raw_plate])
            db.flush()
            db.add_all(
                [
                    InventoryTransactionRecord(
                        inventory_id=product.id,
                        transaction_type="in",
                        quantity=10,
                        before_quantity=0,
                        after_quantity=10,
                        operator_name="张三",
                        remark="成品入库",
                        created_at=datetime(2026, 4, 1, 9, 0),
                    ),
                    InventoryTransactionRecord(
                        inventory_id=product.id,
                        transaction_type="out",
                        quantity=3,
                        before_quantity=10,
                        after_quantity=7,
                        created_at=datetime(2026, 4, 2, 9, 0),
                    ),
                    InventoryTransactionRecord(
                        inventory_id=product.id,
                        transaction_type="in",
                        quantity=5,
                        before_quantity=0,
                        after_quantity=5,
                        reversed_transaction_id=99,
                        created_at=datetime(2026, 4, 3, 9, 0),
                    ),
                    InventoryTransactionRecord(
                        inventory_id=raw_plate.id,
                        transaction_type="in",
                        quantity=7,
                        before_quantity=0,
                        after_quantity=7,
                        created_at=datetime(2026, 4, 4, 9, 0),
                    ),
                ]
            )
            db.commit()

            result = analyze_product_flow(
                db,
                flow_type="in",
                start_date="2026-04-01",
                end_date="2026-04-30",
            )
            headings, export_rows = product_flow_analysis_export_rows(
                db,
                {
                    "flow_type": "in",
                    "start_date": "2026-04-01",
                    "end_date": "2026-04-30",
                },
            )

        self.assertEqual(result["flow_type"], "in")
        self.assertEqual(result["summary"]["total_quantity"], 10)
        self.assertEqual(result["summary"]["transaction_count"], 1)
        self.assertEqual(result["summary"]["product_count"], 1)
        self.assertEqual(result["detail_rows"][0]["product_code"], "IN-1")
        self.assertEqual(result["detail_rows"][0]["purpose_label"], "入库")
        self.assertIn("入库时间", headings)
        self.assertIn("入库数量", headings)
        self.assertEqual(len(export_rows), 1)
        self.assertEqual(export_rows[0][2], "IN-1")

    def test_inbound_analysis_export_uses_inbound_title(self) -> None:
        with self.Session() as db:
            title, headings, rows = build_export_rows(
                "product_outbound_analysis",
                {
                    "flow_type": "in",
                    "start_date": "2026-04-01",
                    "end_date": "2026-04-30",
                },
                db,
            )

        self.assertEqual(title, "产品入库分析")
        self.assertIn("入库时间", headings)
        self.assertEqual(rows, [])

    def test_inbound_analysis_export_normalizes_uppercase_flow_type_for_title(self) -> None:
        with self.Session() as db:
            title, headings, _ = build_export_rows(
                "product_outbound_analysis",
                {"flow_type": "IN", "start_date": "2026-04-01", "end_date": "2026-04-30"},
                db,
            )

        self.assertEqual(title, "产品入库分析")
        self.assertIn("入库时间", headings)

    def test_analysis_page_switches_between_inbound_and_outbound_layouts(self) -> None:
        with self.Session() as db:
            inbound_html = product_outbound_analysis_page(flow_type="in", db=db).body.decode("utf-8")
            outbound_html = product_outbound_analysis_page(flow_type="out", db=db).body.decode("utf-8")

        self.assertIn("产品出入库分析", inbound_html)
        self.assertIn('name="flow_type"', inbound_html)
        self.assertNotIn('name="customer"', inbound_html)
        self.assertNotIn('name="purpose"', inbound_html)
        self.assertNotIn("备货建议", inbound_html)
        self.assertIn("入库总量", inbound_html)
        self.assertIn('name="customer"', outbound_html)
        self.assertIn('name="purpose"', outbound_html)
        self.assertIn("备货建议", outbound_html)
        self.assertIn("销售出库量", outbound_html)

    def test_detail_table_is_before_stats_recommendation_and_monthly_summary(self) -> None:
        with self.Session() as db:
            html = product_outbound_analysis_page(flow_type="out", db=db).body.decode("utf-8")

        detail_index = html.index("逐单明细")
        self.assertLess(detail_index, html.index("销售出库量"))
        self.assertLess(detail_index, html.index("备货建议"))
        self.assertLess(detail_index, html.index("月度汇总"))

    def test_flow_switch_preserves_shared_product_and_date_filters(self) -> None:
        with self.Session() as db:
            html = product_outbound_analysis_page(
                flow_type="out",
                product_code="TNX-10",
                period="custom",
                start_date="2026-01-01",
                end_date="2026-01-31",
                db=db,
            ).body.decode("utf-8")

        self.assertIn("flow_type=in&product_code=TNX-10&period=custom&start_date=2026-01-01&end_date=2026-01-31", html)


if __name__ == "__main__":
    unittest.main()
