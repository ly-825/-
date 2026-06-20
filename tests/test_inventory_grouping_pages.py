import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.admin_pages import page, raw_plate_group_detail_page, raw_plates_page, scrap_group_detail_page, scraps_page
from app.database import Base
from app.models import InventoryTransactionRecord, MaterialInventory, RawPlateSpecification, ScrapGenerationRecord


class InventoryGroupingPagesTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def test_material_sidebar_orders_raw_plate_links_and_separates_scraps(self) -> None:
        html = page("测试", "").body.decode("utf-8")

        raw_spec = html.index('href="/admin/raw-plate-specifications">板料规格')
        raw_in = html.index('href="/admin/raw-plates/inbound">板料入库')
        raw_out = html.index('href="/admin/raw-plates/outbound">板料出库')
        raw_stock = html.index('href="/admin/raw-plates">板料库存')
        raw_flow = html.index('href="/admin/raw-plates/transactions">板料流水')
        scrap_divider = html.index('<span class="nav-subhead">余料</span>')
        scrap_pending = html.index('href="/admin/scraps/pending">待入库余料')

        self.assertLess(raw_spec, raw_in)
        self.assertLess(raw_in, raw_out)
        self.assertLess(raw_out, raw_stock)
        self.assertLess(raw_stock, raw_flow)
        self.assertLess(raw_flow, scrap_divider)
        self.assertLess(scrap_divider, scrap_pending)

    def test_raw_plate_stock_groups_spec_and_temporary_batches_with_detail_link(self) -> None:
        with self.Session() as db:
            db.add(RawPlateSpecification(spec_name="常用板", material="Q235", length=1000, width=500, thickness=3))
            db.add_all(
                [
                    MaterialInventory(
                        inventory_type="raw_plate",
                        material_code="BATCH-001",
                        material="Q235",
                        thickness=3,
                        shape="plate",
                        length=1000,
                        width=500,
                        usable_size="1000×500×3mm",
                        quantity=2,
                        location="A1",
                        status="available",
                    ),
                    MaterialInventory(
                        inventory_type="raw_plate",
                        material_code="TEMP-001",
                        material="Q235",
                        thickness=3,
                        shape="plate",
                        length=1000,
                        width=500,
                        usable_size="1000×500×3mm",
                        quantity=3,
                        location="B1",
                        status="available",
                    ),
                ]
            )
            db.commit()

            html = raw_plates_page(db=db).body.decode("utf-8")

        self.assertIn("<td>常用板</td>", html)
        self.assertIn("<strong>5</strong>", html)
        self.assertIn("<td>2</td>", html)
        self.assertIn("A1 / B1", html)
        self.assertIn("查看明细", html)
        self.assertIn("/admin/raw-plates/detail?", html)
        self.assertNotIn("<h2>板料批次明细</h2>", html)
        self.assertIn('placeholder="输入批次/材质/尺寸/库位"', html)

    def test_raw_plate_group_detail_shows_batches_and_transactions(self) -> None:
        with self.Session() as db:
            first = MaterialInventory(
                inventory_type="raw_plate",
                material_code="BATCH-001",
                material="Q235",
                thickness=3,
                shape="plate",
                length=1000,
                width=500,
                usable_size="1000×500×3mm",
                quantity=2,
                location="A1",
                status="available",
            )
            second = MaterialInventory(
                inventory_type="raw_plate",
                material_code="TEMP-001",
                material="Q235",
                thickness=3,
                shape="plate",
                length=1000,
                width=500,
                usable_size="1000×500×3mm",
                quantity=3,
                location="B1",
                status="available",
            )
            db.add_all([first, second])
            db.flush()
            db.add_all(
                [
                    InventoryTransactionRecord(
                        inventory_id=first.id,
                        transaction_type="in",
                        quantity=2,
                        before_quantity=0,
                        after_quantity=2,
                        operator_name="张三",
                        remark="采购入库",
                    ),
                    InventoryTransactionRecord(
                        inventory_id=second.id,
                        transaction_type="out",
                        quantity=1,
                        before_quantity=4,
                        after_quantity=3,
                        operator_name="李四",
                        remark="生产领料",
                    ),
                ]
            )
            db.commit()

            html = raw_plate_group_detail_page(material="Q235", length="1000", width="500", thickness="3", db=db).body.decode("utf-8")

        self.assertIn("板料明细：Q235 1000×500×3mm", html)
        self.assertIn("BATCH-001", html)
        self.assertIn("TEMP-001", html)
        self.assertIn("张三", html)
        self.assertIn("采购入库", html)
        self.assertIn("李四", html)
        self.assertIn("生产领料", html)

    def test_scrap_stock_groups_batches_with_detail_link(self) -> None:
        with self.Session() as db:
            first = MaterialInventory(
                inventory_type="scrap",
                material="65Mn",
                thickness=2,
                shape="round",
                diameter=80,
                usable_size="φ80",
                quantity=1,
                location="S1",
                status="available",
                source_product_code="P-1",
            )
            second = MaterialInventory(
                inventory_type="scrap",
                material="65Mn",
                thickness=2,
                shape="round",
                diameter=80,
                usable_size="φ80",
                quantity=2,
                location="S2",
                status="available",
                source_product_code="P-2",
            )
            db.add_all([first, second])
            db.flush()
            db.add_all(
                [
                    ScrapGenerationRecord(source_product_code="P-1", scrap_inventory_id=first.id, theoretical_size="φ80", actual_size="φ80", operator_name="王五"),
                    ScrapGenerationRecord(source_product_code="P-2", scrap_inventory_id=second.id, theoretical_size="φ80", actual_size="φ80", operator_name="赵六"),
                ]
            )
            db.commit()

            html = scraps_page(db=db).body.decode("utf-8")

        self.assertIn("<strong>3</strong>", html)
        self.assertIn("<td>2</td>", html)
        self.assertIn("S1 / S2", html)
        self.assertIn("/admin/scraps/detail?", html)
        self.assertNotIn("<h2>余料明细</h2>", html)
        self.assertIn('placeholder="输入来源/材质/尺寸/库位"', html)

    def test_scrap_group_detail_shows_batches_and_transactions(self) -> None:
        with self.Session() as db:
            first = MaterialInventory(
                inventory_type="scrap",
                material="65Mn",
                thickness=2,
                shape="round",
                diameter=80,
                usable_size="φ80",
                quantity=1,
                location="S1",
                status="available",
                source_product_code="P-1",
            )
            db.add(first)
            db.flush()
            db.add(ScrapGenerationRecord(source_product_code="P-1", scrap_inventory_id=first.id, theoretical_size="φ80", actual_size="φ80", operator_name="王五"))
            db.add(
                InventoryTransactionRecord(
                    inventory_id=first.id,
                    transaction_type="confirm",
                    quantity=0,
                    before_quantity=0,
                    after_quantity=1,
                    operator_name="王五",
                    remark="余料确认入库",
                )
            )
            db.commit()

            html = scrap_group_detail_page(material="65Mn", thickness="2", usable_size="φ80", db=db).body.decode("utf-8")

        self.assertIn("余料明细：65Mn 厚2 φ80", html)
        self.assertIn("P-1", html)
        self.assertIn("王五", html)
        self.assertIn("余料确认入库", html)


if __name__ == "__main__":
    unittest.main()
