import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.admin_pages import (
    confirmed_drawing_options,
    create_raw_plate_from_page,
    inventory_outbound_page,
    inventory_page,
    page,
    raw_plate_group_detail_page,
    raw_plate_specifications_page,
    raw_plates_page,
    scrap_group_detail_page,
    scraps_page,
    update_raw_plate_from_page,
)
from app.database import Base
from app.models import InventoryTransactionRecord, MaterialInventory, ProductDrawing, RawPlateSpecification, ScrapGenerationRecord


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

    def test_product_inventory_outbound_and_drawing_options_use_natural_code_order(self) -> None:
        with self.Session() as db:
            base_time = datetime(2026, 1, 1, 8, 0, 0)
            codes_and_minutes = (("TNX2", 1), ("TNX10", 2), ("TNX1", 3))
            for code, minutes in codes_and_minutes:
                db.add(
                    MaterialInventory(
                        inventory_type="product",
                        material_code=code,
                        material="65Mn",
                        thickness=1.2,
                        shape="circle",
                        quantity=1,
                        status="available",
                        created_at=base_time + timedelta(minutes=minutes),
                        updated_at=base_time + timedelta(minutes=minutes),
                    )
                )
                db.add(
                    ProductDrawing(
                        product_code=code,
                        product_name=code,
                        dxf_file_url=f"/tmp/{code}.dxf",
                        material="65Mn",
                        confirmed=1,
                        is_active=1,
                    )
                )
            db.commit()

            inventory_html = inventory_page(db=db).body.decode("utf-8")
            outbound_html = inventory_outbound_page(db=db).body.decode("utf-8")
            options_html = confirmed_drawing_options(db)

        for html in (inventory_html, outbound_html):
            self.assertLess(html.index(">TNX1</td>"), html.index(">TNX2</td>"))
            self.assertLess(html.index(">TNX2</td>"), html.index(">TNX10</td>"))
        self.assertLess(options_html.index("TNX1｜"), options_html.index("TNX2｜"))
        self.assertLess(options_html.index("TNX2｜"), options_html.index("TNX10｜"))

    def test_product_inventory_ignores_legacy_sorting_and_uses_natural_code(self) -> None:
        with self.Session() as db:
            db.add_all(
                [
                    MaterialInventory(material_code="TNX2", inventory_type="product", material="65Mn", thickness=1.2, product_thickness=1.2, plate_thickness=3, shape="circle", quantity=2, status="available"),
                    MaterialInventory(material_code="TNX10", inventory_type="product", material="65Mn", thickness=1.2, product_thickness=2, plate_thickness=1, shape="circle", quantity=10, status="available"),
                ]
            )
            db.commit()

            html = inventory_page(sort_by="quantity", sort_dir="desc", db=db).body.decode("utf-8")

        self.assertNotIn('name="sort_by"', html)
        self.assertNotIn('name="sort_dir"', html)
        self.assertLess(html.index(">TNX2</td>"), html.index(">TNX10</td>"))
        self.assertIn("<strong>10</strong>", html)
        self.assertIn("<strong>2</strong>", html)

    def test_product_inventory_keeps_base_info_and_highlights_actual_filter_values(self) -> None:
        with self.Session() as db:
            drawing = ProductDrawing(
                product_code="TNX-P3",
                product_name="内纸片",
                dxf_file_url="/tmp/tnx-p3.dxf",
                confirmed=1,
                is_active=1,
            )
            db.add(drawing)
            db.add(
                MaterialInventory(
                    material_code="TNX-P3",
                    inventory_type="product",
                    material="65Mn",
                    thickness=1.2,
                    product_thickness=3,
                    plate_thickness=1.2,
                    paper_material="蓝纸",
                    shape="circle",
                    quantity=12,
                    location="C1",
                    status="available",
                )
            )
            db.commit()

            html = inventory_page(product_thickness="3", db=db).body.decode("utf-8")

        self.assertIn("<th>产品型号</th>", html)
        self.assertIn("<th>产品名称</th>", html)
        self.assertIn("<th>库存数量</th>", html)
        self.assertIn("<th>参数信息</th>", html)
        self.assertIn(">TNX-P3</td>", html)
        self.assertIn(">内纸片</td>", html)
        self.assertIn('<span class="parameter-line matched"><strong>总成品厚度</strong> 3</span>', html)
        self.assertIn("钢板厚度", html)

    def test_material_pages_highlight_search_values_and_keep_base_info(self) -> None:
        with self.Session() as db:
            db.add(
                RawPlateSpecification(
                    spec_name="RP-2",
                    material="65Mn",
                    length=1000,
                    width=500,
                    thickness=2,
                    density=7.85,
                )
            )
            db.add(
                MaterialInventory(
                    raw_plate_model="RP-2",
                    material_code="RAW-B1",
                    inventory_type="raw_plate",
                    material="65Mn",
                    thickness=2,
                    length=1000,
                    width=500,
                    shape="rectangle",
                    quantity=8,
                    location="R1",
                    status="available",
                )
            )
            scrap = MaterialInventory(
                inventory_type="scrap",
                material="65Mn",
                thickness=2,
                diameter=80,
                usable_size="φ80",
                shape="round",
                quantity=4,
                location="S1",
                status="available",
                source_product_code="TNX-SOURCE",
            )
            db.add(scrap)
            db.flush()
            db.add(
                ScrapGenerationRecord(
                    source_product_code="TNX-SOURCE",
                    scrap_inventory_id=scrap.id,
                )
            )
            db.commit()

            raw_html = raw_plates_page(thickness="2", db=db).body.decode("utf-8")
            scrap_html = scraps_page(material="65Mn", db=db).body.decode("utf-8")
            spec_html = raw_plate_specifications_page(thickness="2", db=db).body.decode("utf-8")

        for page_html in (raw_html, scrap_html, spec_html):
            self.assertIn("<th>参数信息</th>", page_html)
        self.assertIn("<th>板料型号</th>", raw_html)
        self.assertIn("<th>库存数量</th>", raw_html)
        self.assertIn('<span class="parameter-line matched"><strong>厚度</strong> 2</span>', raw_html)
        self.assertIn("<th>来源型号</th>", scrap_html)
        self.assertIn('<span class="parameter-line matched"><strong>材质</strong> 65Mn</span>', scrap_html)
        self.assertIn("<th>规格型号</th>", spec_html)
        self.assertIn('<span class="parameter-line matched"><strong>厚度</strong> 2</span>', spec_html)

    def test_product_outbound_page_omits_redundant_stock_filter_row(self) -> None:
        with self.Session() as db:
            html = inventory_outbound_page(db=db).body.decode("utf-8")

        self.assertNotIn('method="get" action="/admin/inventory/outbound"', html)
        self.assertNotIn('placeholder="输入型号筛选"', html)
        self.assertIn('data-select-filter="product-outbound-drawing-select"', html)
        self.assertIn('<select id="product-outbound-drawing-select" name="drawing_id" required>', html)
        self.assertIn('<input name="location" list="product-out-location-options"', html)
        self.assertIn('<button class="btn" type="submit">确认出库</button>', html)
        self.assertIn("<h2>当前可出库成品库存</h2>", html)

    def test_raw_plate_specifications_ignore_legacy_sorting(self) -> None:
        with self.Session() as db:
            db.add_all(
                [
                    RawPlateSpecification(spec_name="S10", material="Q235", length=1000, width=500, thickness=10),
                    RawPlateSpecification(spec_name="S2", material="65Mn", length=1000, width=500, thickness=2),
                ]
            )
            db.commit()

            html = raw_plate_specifications_page(sort_by="thickness", sort_dir="desc", db=db).body.decode("utf-8")

        self.assertNotIn('name="sort_by"', html)
        self.assertNotIn('name="sort_dir"', html)
        self.assertLess(html.index(">S2</td>"), html.index(">S10</td>"))

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

    def test_raw_plate_summary_prefers_saved_model_and_hides_zero_stock(self) -> None:
        with self.Session() as db:
            db.add_all(
                [
                    MaterialInventory(
                        raw_plate_model="MODEL-X",
                        material_code="B1",
                        inventory_type="raw_plate",
                        material="65Mn",
                        thickness=2,
                        length=1000,
                        width=500,
                        shape="rectangle",
                        quantity=3,
                        status="available",
                    ),
                    MaterialInventory(
                        material_code="B2",
                        inventory_type="raw_plate",
                        material="Q235",
                        thickness=4,
                        length=2000,
                        width=1000,
                        shape="rectangle",
                        quantity=0,
                        status="used",
                    ),
                ]
            )
            db.commit()

            html = raw_plates_page(db=db).body.decode("utf-8")

        self.assertIn("MODEL-X", html)
        self.assertNotIn("临时规格", html)

    def test_raw_plate_model_can_change_without_changing_dimensions_or_quantity(self) -> None:
        with self.Session() as db:
            item = MaterialInventory(
                material_code="BATCH-1",
                inventory_type="raw_plate",
                material="65Mn",
                thickness=2,
                length=1000,
                width=500,
                shape="rectangle",
                quantity=3,
                status="available",
            )
            db.add(item)
            db.flush()
            db.add(
                InventoryTransactionRecord(
                    inventory_id=item.id,
                    transaction_type="out",
                    quantity=1,
                    before_quantity=4,
                    after_quantity=3,
                )
            )
            db.commit()

            update_raw_plate_from_page(
                item.id,
                material_code="BATCH-1",
                raw_plate_model="MODEL-NEW",
                material="Q235",
                length=9,
                width=9,
                thickness=9,
                location="A1",
                status="available",
                operator_name="张三",
                remark="补型号",
                _lock=None,
                db=db,
            )
            db.refresh(item)

            self.assertEqual(
                (
                    item.raw_plate_model,
                    item.material,
                    item.length,
                    item.width,
                    item.thickness,
                    item.quantity,
                ),
                ("MODEL-NEW", "65Mn", 1000, 500, 2, 3),
            )

    def test_raw_plate_inbound_saves_selected_or_manual_model(self) -> None:
        with self.Session() as db:
            spec = RawPlateSpecification(
                spec_name="S-2",
                material="65Mn",
                length=1000,
                width=500,
                thickness=2,
                density=7.85,
            )
            db.add(spec)
            db.commit()

            create_raw_plate_from_page(
                raw_plate_spec_id=str(spec.id),
                raw_plate_model="IGNORED",
                material_code="FIXED-1",
                material="错误材质",
                total_weight_ton=0.1,
                length=9,
                width=9,
                thickness=9,
                density=1,
                location="A1",
                operator_name="张三",
                remark="固定规格",
                _lock=None,
                db=db,
            )
            create_raw_plate_from_page(
                raw_plate_spec_id="",
                raw_plate_model="CUSTOM-2",
                material_code="MANUAL-1",
                material="Q235",
                total_weight_ton=0.1,
                length=1000,
                width=500,
                thickness=2,
                density=7.85,
                location="A2",
                operator_name="李四",
                remark="手工规格",
                _lock=None,
                db=db,
            )

            fixed = db.query(MaterialInventory).filter_by(material_code="FIXED-1").one()
            manual = db.query(MaterialInventory).filter_by(material_code="MANUAL-1").one()
            records = {
                record.inventory_id: record
                for record in db.query(InventoryTransactionRecord).filter(
                    InventoryTransactionRecord.inventory_id.in_([fixed.id, manual.id])
                )
            }

            self.assertEqual((fixed.raw_plate_model, fixed.material, fixed.length), ("S-2", "65Mn", 1000))
            self.assertEqual(manual.raw_plate_model, "CUSTOM-2")
            self.assertEqual(records[fixed.id].after_quantity, fixed.quantity)
            self.assertEqual(records[manual.id].after_quantity, manual.quantity)

    def test_raw_plate_summary_ignores_legacy_sorting_and_uses_model_order(self) -> None:
        with self.Session() as db:
            db.add_all(
                [
                    MaterialInventory(raw_plate_model="A-MODEL", material_code="R2", inventory_type="raw_plate", material="Q235", thickness=2, length=1000, width=500, shape="rectangle", quantity=2, status="available"),
                    MaterialInventory(raw_plate_model="Z-MODEL", material_code="R10", inventory_type="raw_plate", material="65Mn", thickness=3, length=2000, width=1000, shape="rectangle", quantity=10, status="available"),
                ]
            )
            db.commit()

            html = raw_plates_page(sort_by="quantity", sort_dir="desc", db=db).body.decode("utf-8")

        self.assertNotIn('name="sort_by"', html)
        self.assertNotIn('name="sort_dir"', html)
        self.assertLess(html.index(">A-MODEL</td>"), html.index(">Z-MODEL</td>"))
        self.assertIn("<strong>10</strong>", html)
        self.assertIn("<strong>2</strong>", html)

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
                    ScrapGenerationRecord(source_product_code="P-1-补充记录", scrap_inventory_id=first.id, theoretical_size="φ80", actual_size="φ80", operator_name="王五"),
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

    def test_scrap_summary_ignores_legacy_sorting(self) -> None:
        with self.Session() as db:
            small = MaterialInventory(inventory_type="scrap", material="Q235", thickness=2, diameter=50, usable_size="φ50", shape="round", quantity=2, status="available")
            large = MaterialInventory(inventory_type="scrap", material="65Mn", thickness=3, diameter=80, usable_size="φ80", shape="round", quantity=10, status="available")
            db.add_all([small, large])
            db.flush()
            db.add_all(
                [
                    ScrapGenerationRecord(source_product_code="P2", scrap_inventory_id=small.id),
                    ScrapGenerationRecord(source_product_code="P10", scrap_inventory_id=large.id),
                ]
            )
            db.commit()

            html = scraps_page(sort_by="quantity", sort_dir="asc", db=db).body.decode("utf-8")

        self.assertNotIn('name="sort_by"', html)
        self.assertNotIn('name="sort_dir"', html)
        self.assertLess(html.index("<strong>材质</strong> 65Mn"), html.index("<strong>材质</strong> Q235"))
        self.assertIn("<strong>10</strong>", html)
        self.assertIn("<strong>2</strong>", html)

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
