import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.admin_pages import confirmed_drawings_page
from app.database import Base
from app.models import ProductDrawing
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
            self.assertIn("<td>CAR-100</td>", html)
            self.assertIn("<td>客户要求热处理</td>", html)
            self.assertNotIn("<td>MOTO-200</td>", html)

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

            self.assertIn("<th>总成品厚度</th>", all_html)
            self.assertIn("<th>钢板厚度</th>", all_html)
            self.assertIn("<td>1.25</td>", all_html)
            self.assertIn("<td>0.95</td>", all_html)
            self.assertIn("<td>CAR-125</td>", all_html)
            self.assertIn("<td>CAR-080</td>", all_html)
            self.assertNotIn("<td>CAR-125</td>", keyword_html)
            self.assertNotIn("<td>CAR-080</td>", keyword_html)

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
            self.assertIn("<td>SEP-PRODUCT</td>", product_html)
            self.assertNotIn("<td>SEP-PLATE</td>", product_html)
            self.assertIn("<td>SEP-PLATE</td>", plate_html)
            self.assertNotIn("<td>SEP-PRODUCT</td>", plate_html)

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
