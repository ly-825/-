import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.admin_pages import confirmed_drawings_page
from app.database import Base
from app.models import ProductDrawing
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


if __name__ == "__main__":
    unittest.main()
