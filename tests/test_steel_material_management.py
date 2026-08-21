import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.admin_pages import (
    create_raw_plate_specification,
    page,
    raw_plate_inbound_page,
    raw_plate_outbound_page,
    raw_plate_specifications_page,
    raw_plate_transactions_page,
    raw_plates_page,
)
from app.database import Base
from app.models import InventoryTransactionRecord, MaterialInventory, RawPlateSpecification

from app.services.material_formats import (
    format_steel_thickness,
    paper_roll_size,
    paper_sheet_model,
    steel_dimension_sort_key,
    steel_spec_name,
)


class SteelMaterialFormattingTest(unittest.TestCase):
    def test_steel_name_keeps_one_decimal_thickness_first(self) -> None:
        self.assertEqual(steel_spec_name(3, 130, 1270), "3.0×130×1270")
        self.assertEqual(format_steel_thickness(1.84), "1.8")

    def test_steel_sort_key_is_numeric_thickness_width_length(self) -> None:
        values = [(2, 130, 1270), (1.8, 270, 1000), (1.8, 140, 1340)]

        result = sorted(
            values,
            key=lambda value: steel_dimension_sort_key(value[0], value[1], value[2]),
        )

        self.assertEqual(
            result,
            [(1.8, 140, 1340), (1.8, 270, 1000), (2, 130, 1270)],
        )

    def test_paper_sizes_put_thickness_first(self) -> None:
        self.assertEqual(paper_roll_size(0.5, 80, 120), "0.5×80×120")
        self.assertEqual(paper_sheet_model(0.5, 400, 400), "0.5×400×400")


class SteelMaterialPagesTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _create_specifications(self, db) -> list[RawPlateSpecification]:
        dimensions = [
            ("手工名称-C", "65Mn", 1270, 130, 3),
            ("手工名称-B", "65Mn", 1000, 270, 1.8),
            ("手工名称-A", "65Mn", 1340, 140, 1.8),
        ]
        for name, material, length, width, thickness in dimensions:
            create_raw_plate_specification(
                spec_name=name,
                material=material,
                length=length,
                width=width,
                thickness=thickness,
                density=7.85,
                remark="",
                db=db,
            )
        return db.query(RawPlateSpecification).all()

    def test_specification_names_are_generated_and_sorted_by_dimensions(self) -> None:
        with self.Session() as db:
            specs = self._create_specifications(db)
            html = raw_plate_specifications_page(db=db).body.decode("utf-8")

            names = {spec.spec_name for spec in specs}

        self.assertEqual(names, {"1.8×140×1340", "1.8×270×1000", "3.0×130×1270"})
        self.assertLess(html.index(">1.8×140×1340</td>"), html.index(">1.8×270×1000</td>"))
        self.assertLess(html.index(">1.8×270×1000</td>"), html.index(">3.0×130×1270</td>"))
        self.assertIn('name="thickness" type="number" step="0.1"', html)

    def test_stock_inbound_and_outbound_share_numeric_dimension_order(self) -> None:
        with self.Session() as db:
            specs = self._create_specifications(db)
            by_thickness_width = {(spec.thickness, spec.width): spec for spec in specs}
            base_time = datetime(2026, 7, 1, 8, 0)
            batches = [
                ("LEGACY-Z", 3, 130, 1270, 0),
                ("LEGACY-A", 1.8, 270, 1000, 1),
                ("LEGACY-M", 1.8, 140, 1340, 2),
            ]
            for legacy_model, thickness, width, length, minutes in batches:
                spec = by_thickness_width[(thickness, width)]
                db.add(
                    MaterialInventory(
                        material_code=f"BATCH-{minutes}",
                        raw_plate_model=legacy_model,
                        inventory_type="raw_plate",
                        material=spec.material,
                        thickness=thickness,
                        length=length,
                        width=width,
                        shape="rectangle",
                        quantity=5,
                        status="available",
                        created_at=base_time + timedelta(minutes=minutes),
                        updated_at=base_time + timedelta(minutes=minutes),
                    )
                )
            db.commit()

            stock = raw_plates_page(db=db).body.decode("utf-8")
            inbound = raw_plate_inbound_page(db=db).body.decode("utf-8")
            outbound = raw_plate_outbound_page(db=db).body.decode("utf-8")

        for html in (stock, inbound):
            self.assertLess(html.index("1.8×140×1340"), html.index("1.8×270×1000"))
            self.assertLess(html.index("1.8×270×1000"), html.index("3.0×130×1270"))
        self.assertNotIn("LEGACY-Z", stock)
        self.assertLess(outbound.index("<td>140</td>"), outbound.index("<td>270</td>"))
        self.assertLess(outbound.index("<td>270</td>"), outbound.index("<td>130</td>"))

    def test_steel_navigation_and_outbound_layout_follow_workflow_order(self) -> None:
        with self.Session() as db:
            navigation = page("测试", "").body.decode("utf-8")
            outbound = raw_plate_outbound_page(db=db).body.decode("utf-8")

        self.assertIn("<summary>钢板材料管理</summary>", navigation)
        search_start = outbound.index('<form method="get" action="/admin/raw-plates/outbound"')
        search_end = outbound.index("</form>", search_start)
        search_html = outbound[search_start:search_end]
        self.assertLess(search_html.index('name="thickness"'), search_html.index('name="material"'))
        self.assertLess(search_html.index('name="width"'), search_html.index('name="length"'))
        self.assertLess(outbound.index("<h2>当前可用规格</h2>"), outbound.index("<h2>确认出库</h2>"))
        self.assertIn("先在上方“当前可用规格”里点击“选择出库”", outbound)

    def test_raw_plate_outbound_available_table_puts_thickness_first(self) -> None:
        with self.Session() as db:
            db.add(
                MaterialInventory(
                    material_code="RAW-COLUMN-ORDER",
                    inventory_type="raw_plate",
                    material="65Mn",
                    thickness=0.8,
                    length=1251,
                    width=182,
                    shape="rectangle",
                    quantity=1990,
                    status="available",
                )
            )
            db.commit()
            html = raw_plate_outbound_page(db=db).body.decode("utf-8")

        self.assertIn(
            "<tr><th>厚mm</th><th>长mm</th><th>宽mm</th><th>材质</th>",
            html,
        )
        self.assertIn(
            "<tr><td>0.8</td><td>1251</td><td>182</td><td>65Mn</td>",
            html,
        )

    def test_steel_transactions_render_readable_records_without_horizontal_table(self) -> None:
        with self.Session() as db:
            item = MaterialInventory(
                material_code="RAW-202607210001",
                inventory_type="raw_plate",
                material="50#",
                thickness=2.3,
                length=1140,
                width=145,
                usable_size="1140×145×2.3mm",
                shape="rectangle",
                quantity=200,
                status="available",
            )
            db.add(item)
            db.flush()
            db.add(
                InventoryTransactionRecord(
                    inventory_id=item.id,
                    transaction_type="in",
                    quantity=200,
                    before_quantity=0,
                    after_quantity=200,
                    operator_name="李乙",
                    remark="板料入库；总重量 260.48kg；单块重量 4.13kg；入库 200 块。",
                )
            )
            db.commit()

            transaction_html = raw_plate_transactions_page(db=db).body.decode("utf-8")

        self.assertNotIn('<table class="wide-transaction-table"', transaction_html)
        self.assertIn('class="transaction-record-list"', transaction_html)
        self.assertIn('class="transaction-record"', transaction_html)
        self.assertIn("RAW-202607210001", transaction_html)
        self.assertIn("2.3×145×1140mm", transaction_html)
        self.assertNotIn("1140×145×2.3mm", transaction_html)
        self.assertIn("0 → 200", transaction_html)
        self.assertIn('class="transaction-note"', transaction_html)
        self.assertIn("板料入库；总重量", transaction_html)
        self.assertNotIn('name="operator_name"', transaction_html)
        self.assertIn("当前操作员：当前登录账号", transaction_html)
        self.assertIn('name="remark"', transaction_html)


if __name__ == "__main__":
    unittest.main()
