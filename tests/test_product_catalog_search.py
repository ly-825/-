import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.admin_pages import confirmed_drawings_page
from app.database import Base
from app.models import MaterialInventory, ProductDrawing, RawPlateSpecification, ScrapGenerationRecord
from app.routers.mobile import drawings as mobile_drawings
from app.services.excel_export import build_export_rows


class ProductCatalogSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def test_confirmed_drawings_can_filter_by_category_and_parameters(self) -> None:
        with self.Session() as db:
            db.add_all(
                [
                    ProductDrawing(
                        product_code="CAR-100",
                        product_name="汽车齿轮",
                        product_category="汽车",
                        remark="客户要求热处理",
                        dxf_file_url="/tmp/car-100.dxf",
                        material="65Mn",
                        product_thickness=1.2,
                        plate_thickness=1.0,
                        max_outer_diameter=100,
                        min_inner_diameter=50,
                        teeth_count=32,
                        confirmed=1,
                        is_active=1,
                    ),
                    ProductDrawing(
                        product_code="MOTO-200",
                        product_name="摩托车齿轮",
                        product_category="摩托车",
                        dxf_file_url="/tmp/moto-200.dxf",
                        material="65Mn",
                        product_thickness=1.2,
                        plate_thickness=1.0,
                        max_outer_diameter=100,
                        min_inner_diameter=50,
                        teeth_count=32,
                        confirmed=1,
                        is_active=1,
                    ),
                ]
            )
            db.commit()

            response = confirmed_drawings_page(
                product_category="汽车",
                outer_diameter="100",
                inner_diameter="50",
                teeth_count="32",
                db=db,
            )

            html = response.body.decode("utf-8")
            self.assertIn(">CAR-100</td>", html)
            self.assertNotIn(">MOTO-200</td>", html)

    def test_confirmed_drawings_show_actual_filtered_parameters_in_status_column(self) -> None:
        with self.Session() as db:
            db.add(
                ProductDrawing(
                    product_code="DYN-1",
                    product_name="动态参数图纸",
                    dxf_file_url="/tmp/dyn-1.dxf",
                    module=2,
                    pressure_angle=30,
                    confirmed=1,
                    is_active=1,
                )
            )
            db.commit()

            single_html = confirmed_drawings_page(pressure_angle="30", db=db).body.decode("utf-8")
            multiple_html = confirmed_drawings_page(pressure_angle="30", module="2", db=db).body.decode("utf-8")
            text_only_html = confirmed_drawings_page(q="DYN", db=db).body.decode("utf-8")

        self.assertIn("<th>参数信息</th>", single_html)
        self.assertIn("<th>状态</th>", single_html)
        self.assertIn("<strong>压力角</strong> 30°", single_html)
        self.assertIn("<strong>压力角</strong> 30°", multiple_html)
        self.assertIn("<strong>模数</strong> 2", multiple_html)
        self.assertIn("<th>参数信息</th>", text_only_html)
        self.assertIn('<span class="parameter-line matched"><strong>产品编号</strong> DYN-1</span>', text_only_html)
        self.assertIn("已确认", text_only_html)

    def test_product_catalog_export_uses_requested_natural_code_sort(self) -> None:
        with self.Session() as db:
            db.add_all(
                [
                    ProductDrawing(product_code="TNX10", dxf_file_url="/tmp/tnx10.dxf", confirmed=1, is_active=1),
                    ProductDrawing(product_code="TNX2", dxf_file_url="/tmp/tnx2.dxf", confirmed=1, is_active=1),
                    ProductDrawing(product_code="TNX1", dxf_file_url="/tmp/tnx1.dxf", confirmed=1, is_active=1),
                ]
            )
            db.commit()

            _, _, rows = build_export_rows(
                "product_catalog",
                {"sort_by": "product_code", "sort_dir": "desc"},
                db,
            )

        self.assertEqual([row[1] for row in rows], ["TNX10", "TNX2", "TNX1"])

    def test_product_catalog_export_matches_page_for_combined_tooth_filter(self) -> None:
        with self.Session() as db:
            db.add_all(
                [
                    ProductDrawing(product_code="OT-48", dxf_file_url="/tmp/ot48.dxf", tooth_type="OT", teeth_count=48, teeth_count_text="48(52)", confirmed=1, is_active=1),
                    ProductDrawing(product_code="IT-48", dxf_file_url="/tmp/it48.dxf", tooth_type="IT", teeth_count=48, teeth_count_text="48", confirmed=1, is_active=1),
                ]
            )
            db.commit()

            page_html = confirmed_drawings_page(teeth_count="OT48", db=db).body.decode("utf-8")
            _, _, parameter_rows = build_export_rows("product_catalog", {"teeth_count": "OT48"}, db)
            _, _, keyword_rows = build_export_rows("product_catalog", {"q": "OT48(52)"}, db)

        self.assertIn(">OT-48</td>", page_html)
        self.assertNotIn(">IT-48</td>", page_html)
        self.assertEqual([row[1] for row in parameter_rows], ["OT-48"])
        self.assertEqual([row[1] for row in keyword_rows], ["OT-48"])

    def test_inventory_exports_group_batches_and_use_summary_sorting(self) -> None:
        with self.Session() as db:
            product_a = MaterialInventory(material_code="TNX2", inventory_type="product", material="65Mn", thickness=1.2, shape="circle", quantity=2, status="available", location="A1")
            product_a_second = MaterialInventory(material_code="TNX2", inventory_type="product", material="65Mn", thickness=1.2, shape="circle", quantity=3, status="available", location="A2")
            product_b = MaterialInventory(material_code="TNX10", inventory_type="product", material="65Mn", thickness=1.2, shape="circle", quantity=10, status="available", location="B1")
            raw_a = MaterialInventory(material_code="R1", inventory_type="raw_plate", material="Q235", thickness=2, length=1000, width=500, shape="rectangle", quantity=2, status="available", location="R1")
            raw_a_second = MaterialInventory(material_code="R2", inventory_type="raw_plate", material="Q235", thickness=2, length=1000, width=500, shape="rectangle", quantity=3, status="available", location="R2")
            raw_b = MaterialInventory(material_code="R3", inventory_type="raw_plate", material="65Mn", thickness=3, length=2000, width=1000, shape="rectangle", quantity=10, status="available", location="R3")
            scrap_a = MaterialInventory(inventory_type="scrap", material="Q235", thickness=2, diameter=50, usable_size="φ50", shape="round", quantity=2, status="available", location="S1")
            scrap_b = MaterialInventory(inventory_type="scrap", material="65Mn", thickness=3, diameter=80, usable_size="φ80", shape="round", quantity=10, status="available", location="S2")
            db.add_all([product_a, product_a_second, product_b, raw_a, raw_a_second, raw_b, scrap_a, scrap_b])
            db.flush()
            db.add(RawPlateSpecification(spec_name="Q235常用板", material="Q235", length=1000, width=500, thickness=2))
            db.add_all(
                [
                    ScrapGenerationRecord(source_product_code="P2", scrap_inventory_id=scrap_a.id),
                    ScrapGenerationRecord(source_product_code="P10", scrap_inventory_id=scrap_b.id),
                ]
            )
            db.commit()

            _, product_headings, product_rows = build_export_rows("product_inventory", {"sort_by": "quantity", "sort_dir": "desc"}, db)
            _, raw_headings, raw_rows = build_export_rows("raw_plate_inventory", {"sort_by": "quantity", "sort_dir": "desc"}, db)
            _, scrap_headings, scrap_rows = build_export_rows("scrap_inventory", {"sort_by": "quantity", "sort_dir": "desc"}, db)

        self.assertEqual(product_headings[:2], ["产品型号", "库存数量"])
        self.assertEqual([(row[0], row[1]) for row in product_rows], [("TNX10", 10), ("TNX2", 5)])
        self.assertIn("批次数", raw_headings)
        self.assertEqual([(row[1], row[5]) for row in raw_rows], [("65Mn", 10), ("Q235", 5)])
        self.assertIn("批次数", scrap_headings)
        self.assertEqual([(row[0], row[3]) for row in scrap_rows], [("65Mn", 10), ("Q235", 2)])

    def test_product_catalog_export_filters_category_and_includes_parameters(self) -> None:
        with self.Session() as db:
            db.add_all(
                [
                    ProductDrawing(
                        product_code="CAR-100",
                        product_name="汽车齿轮",
                        product_category="汽车",
                        dxf_file_url="/tmp/car-100.dxf",
                        material="65Mn",
                        product_thickness=1.2,
                        plate_thickness=1.0,
                        max_outer_diameter=100,
                        min_inner_diameter=50,
                        teeth_count=32,
                        module=2,
                        pressure_angle=20,
                        common_normal_length=88.5,
                        pin_diameter=4,
                        pin_span=91,
                        expected_scrap_size="φ50",
                        remark="客户确认样件",
                        confirmed=1,
                        is_active=1,
                    ),
                    ProductDrawing(
                        product_code="MOTO-200",
                        product_name="摩托车齿轮",
                        product_category="摩托车",
                        dxf_file_url="/tmp/moto-200.dxf",
                        material="65Mn",
                        product_thickness=1.2,
                        plate_thickness=1.0,
                        max_outer_diameter=100,
                        min_inner_diameter=50,
                        teeth_count=32,
                        confirmed=1,
                        is_active=1,
                    ),
                ]
            )
            db.commit()

            title, headings, rows = build_export_rows("product_catalog", {"product_category": "汽车"}, db)

            self.assertEqual(title, "产品参数清单")
            self.assertIn("产品分类", headings)
            self.assertIn("外径", headings)
            self.assertIn("齿数", headings)
            self.assertIn("备注", headings)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "汽车")
            self.assertEqual(rows[0][1], "CAR-100")
            self.assertIn(32, rows[0])
            self.assertIn("客户确认样件", rows[0])

    def test_confirmed_drawings_display_product_and_plate_thickness_without_keyword_thickness_matching(self) -> None:
        with self.Session() as db:
            db.add_all(
                [
                    ProductDrawing(
                        product_code="CAR-125",
                        product_name="汽车厚片",
                        product_category="汽车",
                        dxf_file_url="/tmp/car-125.dxf",
                        material="65Mn",
                        product_thickness=1.25,
                        plate_thickness=0.95,
                        confirmed=1,
                        is_active=1,
                    ),
                    ProductDrawing(
                        product_code="CAR-080",
                        product_name="汽车薄片",
                        product_category="汽车",
                        dxf_file_url="/tmp/car-080.dxf",
                        material="65Mn",
                        product_thickness=0.8,
                        plate_thickness=0.6,
                        confirmed=1,
                        is_active=1,
                    ),
                ]
            )
            db.commit()

            all_response = confirmed_drawings_page(db=db)
            all_html = all_response.body.decode("utf-8")
            keyword_response = confirmed_drawings_page(q="1.25", db=db)
            keyword_html = keyword_response.body.decode("utf-8")

            self.assertNotIn("<th>厚度</th>", all_html)
            self.assertIn(">CAR-125</td>", all_html)
            self.assertIn(">CAR-080</td>", all_html)
            self.assertNotIn(">CAR-125</td>", keyword_html)
            self.assertNotIn(">CAR-080</td>", keyword_html)

    def test_mobile_drawing_keyword_search_does_not_match_product_or_plate_thickness(self) -> None:
        with self.Session() as db:
            db.add_all(
                [
                    ProductDrawing(
                        product_code="MOB-125",
                        dxf_file_url="/tmp/mob-125.dxf",
                        product_thickness=1.25,
                        plate_thickness=0.95,
                        confirmed=1,
                        is_active=1,
                    ),
                    ProductDrawing(
                        product_code="MOB-080",
                        dxf_file_url="/tmp/mob-080.dxf",
                        product_thickness=0.8,
                        plate_thickness=0.6,
                        confirmed=1,
                        is_active=1,
                    ),
                ]
            )
            db.commit()

            product_matches = mobile_drawings(status="confirmed", q="1.25", db=db)
            plate_matches = mobile_drawings(status="confirmed", q="0.95", db=db)

            self.assertEqual([drawing.product_code for drawing in product_matches], [])
            self.assertEqual([drawing.product_code for drawing in plate_matches], [])

    def test_product_catalog_export_keyword_search_does_not_match_plate_thickness(self) -> None:
        with self.Session() as db:
            db.add_all(
                [
                    ProductDrawing(
                        product_code="EXP-095",
                        dxf_file_url="/tmp/exp-095.dxf",
                        product_thickness=1.25,
                        plate_thickness=0.95,
                        confirmed=1,
                        is_active=1,
                    ),
                    ProductDrawing(
                        product_code="EXP-060",
                        dxf_file_url="/tmp/exp-060.dxf",
                        product_thickness=0.8,
                        plate_thickness=0.6,
                        confirmed=1,
                        is_active=1,
                    ),
                ]
            )
            db.commit()

            _, _, rows = build_export_rows("product_catalog", {"q": "0.95"}, db)

            self.assertEqual([row[1] for row in rows], [])

    def test_confirmed_drawings_has_separate_product_and_plate_thickness_filters(self) -> None:
        with self.Session() as db:
            db.add_all(
                [
                    ProductDrawing(
                        product_code="SEP-PRODUCT",
                        dxf_file_url="/tmp/sep-product.dxf",
                        product_thickness=1.25,
                        plate_thickness=0.95,
                        confirmed=1,
                        is_active=1,
                    ),
                    ProductDrawing(
                        product_code="SEP-PLATE",
                        dxf_file_url="/tmp/sep-plate.dxf",
                        product_thickness=0.95,
                        plate_thickness=1.25,
                        confirmed=1,
                        is_active=1,
                    ),
                ]
            )
            db.commit()

            product_response = confirmed_drawings_page(product_thickness="1.25", db=db)
            product_html = product_response.body.decode("utf-8")
            plate_response = confirmed_drawings_page(plate_thickness="1.25", db=db)
            plate_html = plate_response.body.decode("utf-8")

            self.assertIn('name="product_thickness"', product_html)
            self.assertIn('name="plate_thickness"', product_html)
            self.assertIn(">SEP-PRODUCT</td>", product_html)
            self.assertNotIn(">SEP-PLATE</td>", product_html)
            self.assertIn(">SEP-PLATE</td>", plate_html)
            self.assertNotIn(">SEP-PRODUCT</td>", plate_html)

    def test_mobile_drawings_have_separate_product_and_plate_thickness_filters(self) -> None:
        with self.Session() as db:
            db.add_all(
                [
                    ProductDrawing(
                        product_code="MOB-PRODUCT",
                        dxf_file_url="/tmp/mob-product.dxf",
                        product_thickness=1.25,
                        plate_thickness=0.95,
                        confirmed=1,
                        is_active=1,
                    ),
                    ProductDrawing(
                        product_code="MOB-PLATE",
                        dxf_file_url="/tmp/mob-plate.dxf",
                        product_thickness=0.95,
                        plate_thickness=1.25,
                        confirmed=1,
                        is_active=1,
                    ),
                ]
            )
            db.commit()

            product_matches = mobile_drawings(status="confirmed", product_thickness="1.25", db=db)
            plate_matches = mobile_drawings(status="confirmed", plate_thickness="1.25", db=db)

            self.assertEqual([drawing.product_code for drawing in product_matches], ["MOB-PRODUCT"])
            self.assertEqual([drawing.product_code for drawing in plate_matches], ["MOB-PLATE"])

    def test_product_catalog_export_has_separate_product_and_plate_thickness_filters(self) -> None:
        with self.Session() as db:
            db.add_all(
                [
                    ProductDrawing(
                        product_code="EXP-PRODUCT",
                        dxf_file_url="/tmp/exp-product.dxf",
                        product_thickness=1.25,
                        plate_thickness=0.95,
                        confirmed=1,
                        is_active=1,
                    ),
                    ProductDrawing(
                        product_code="EXP-PLATE",
                        dxf_file_url="/tmp/exp-plate.dxf",
                        product_thickness=0.95,
                        plate_thickness=1.25,
                        confirmed=1,
                        is_active=1,
                    ),
                ]
            )
            db.commit()

            _, _, product_rows = build_export_rows("product_catalog", {"product_thickness": "1.25"}, db)
            _, _, plate_rows = build_export_rows("product_catalog", {"plate_thickness": "1.25"}, db)

            self.assertEqual([row[1] for row in product_rows], ["EXP-PRODUCT"])
            self.assertEqual([row[1] for row in plate_rows], ["EXP-PLATE"])


if __name__ == "__main__":
    unittest.main()
