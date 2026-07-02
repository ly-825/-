import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.admin_pages import (
    confirm_drawing_from_page,
    confirmed_drawings_page,
    create_inventory_from_page,
    inventory_page,
    inventory_product_detail_page,
)
from app.database import Base
from app.models import MaterialInventory, ProductDrawing
from app.services.excel_export import build_export_rows


class DrawingParameterTextAndProductPaperTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def test_confirmed_drawing_with_inventory_can_still_update_manual_parameters(self) -> None:
        with self.Session() as db:
            drawing = ProductDrawing(
                product_code="TNX-INV",
                dxf_file_url="/tmp/tnx-inv.dxf",
                material="65Mn",
                product_thickness=1.8,
                plate_thickness=1.2,
                confirmed=1,
                is_active=1,
            )
            db.add(drawing)
            db.flush()
            db.add(
                MaterialInventory(
                    material_code="TNX-INV",
                    inventory_type="product",
                    material="65Mn",
                    thickness=1.2,
                    shape="circle",
                    quantity=5,
                    source_product_code="TNX-INV",
                    source_drawing_id=drawing.id,
                    status="available",
                )
            )
            db.commit()

            response = confirm_drawing_from_page(
                drawing_id=drawing.id,
                product_code="TNX-INV",
                product_name="库存后修正",
                product_category="汽车",
                remark="库存后允许修正参数",
                material="65Mn",
                max_outer_diameter="100",
                min_inner_diameter="50",
                product_thickness="1.8",
                plate_thickness="1.2",
                tooth_type="OT",
                teeth_count="48(52)",
                module="DP",
                pressure_angle="20",
                profile_shift_coefficient="",
                span_teeth_count="",
                common_normal_length="58.26-58.14",
                pin_diameter="",
                pin_span="",
                expected_scrap_size="φ50",
                db=db,
            )
            db.refresh(drawing)

            self.assertEqual(response.headers["location"], f"/admin/drawings/{drawing.id}?notice=confirmed")
            self.assertEqual(drawing.tooth_type, "OT")
            self.assertEqual(drawing.teeth_count_text, "48(52)")
            self.assertEqual(drawing.module_text, "DP")
            self.assertEqual(drawing.common_normal_length_text, "58.26-58.14")
            self.assertEqual(drawing.common_normal_length, 58.26)

    def test_confirming_drawing_syncs_existing_product_inventory_thicknesses(self) -> None:
        with self.Session() as db:
            drawing = ProductDrawing(
                product_code="TNX-SYNC",
                dxf_file_url="/tmp/tnx-sync.dxf",
                material="65Mn",
                product_thickness=1.5,
                plate_thickness=0.8,
                max_outer_diameter=90,
                confirmed=1,
                is_active=1,
            )
            db.add(drawing)
            db.flush()
            item = MaterialInventory(
                material_code="TNX-SYNC",
                inventory_type="product",
                material="65Mn",
                thickness=0.8,
                shape="circle",
                quantity=7,
                source_product_code="TNX-SYNC",
                source_drawing_id=drawing.id,
                status="available",
            )
            db.add(item)
            db.commit()

            confirm_drawing_from_page(
                drawing_id=drawing.id,
                product_code="TNX-SYNC",
                product_name="同步厚度",
                product_category="汽车",
                remark="",
                material="65Mn",
                max_outer_diameter="120",
                min_inner_diameter="60",
                product_thickness="2.4",
                plate_thickness="1.1",
                tooth_type="OT",
                teeth_count="48",
                module="2",
                pressure_angle="20",
                profile_shift_coefficient="",
                span_teeth_count="",
                common_normal_length="",
                pin_diameter="",
                pin_span="",
                expected_scrap_size="φ60",
                db=db,
            )
            db.refresh(item)
            summary_html = inventory_page(db=db).body.decode("utf-8")
            detail_html = inventory_product_detail_page("TNX-SYNC", db=db).body.decode("utf-8")

            self.assertEqual(item.thickness, 2.4)
            self.assertEqual(item.diameter, 120)
            self.assertIn("<th>总成品厚度</th>", summary_html)
            self.assertIn("<th>钢板厚度</th>", summary_html)
            self.assertIn("<td>2.4</td>", summary_html)
            self.assertIn("<td>1.1</td>", summary_html)
            self.assertIn("<th>总成品厚度</th>", detail_html)
            self.assertIn("<td>2.4</td>", detail_html)

    def test_text_gear_parameters_are_searchable_and_exported(self) -> None:
        with self.Session() as db:
            db.add_all(
                [
                    ProductDrawing(
                        product_code="TNX-OT",
                        dxf_file_url="/tmp/tnx-ot.dxf",
                        material="65Mn",
                        tooth_type="OT",
                        teeth_count_text="48(52)",
                        module_text="DP",
                        common_normal_length_text="58.26-58.14",
                        confirmed=1,
                        is_active=1,
                    ),
                    ProductDrawing(
                        product_code="TNX-STD",
                        dxf_file_url="/tmp/tnx-std.dxf",
                        material="65Mn",
                        tooth_type="IT",
                        teeth_count_text="41",
                        module_text="2.5",
                        common_normal_length_text="45.2",
                        confirmed=1,
                        is_active=1,
                    ),
                ]
            )
            db.commit()

            html = confirmed_drawings_page(module="DP", teeth_count="48(52)", db=db).body.decode("utf-8")
            _, headings, rows = build_export_rows("product_catalog", {"module": "DP"}, db)

            self.assertIn(">TNX-OT</td>", html)
            self.assertNotIn(">TNX-STD</td>", html)
            self.assertIn("齿型", headings)
            self.assertEqual([row[1] for row in rows], ["TNX-OT"])
            self.assertIn("DP", rows[0])
            self.assertIn("48(52)", rows[0])

    def test_product_inbound_saves_and_displays_paper_material(self) -> None:
        with self.Session() as db:
            drawing = ProductDrawing(
                product_code="TNX-PAPER",
                dxf_file_url="/tmp/tnx-paper.dxf",
                material="65Mn",
                plate_thickness=1.2,
                max_outer_diameter=100,
                confirmed=1,
                is_active=1,
            )
            db.add(drawing)
            db.commit()
            db.refresh(drawing)

            create_inventory_from_page(
                drawing_id=drawing.id,
                quantity=3,
                location="A-01",
                paper_material="蓝色纸",
                operator_name="张三",
                db=db,
            )
            item = db.query(MaterialInventory).filter(MaterialInventory.material_code == "TNX-PAPER").first()
            summary_html = inventory_page(db=db).body.decode("utf-8")
            detail_html = inventory_product_detail_page("TNX-PAPER", db=db).body.decode("utf-8")

            self.assertEqual(item.paper_material, "蓝色纸")
            self.assertIn("<th>纸材质</th>", summary_html)
            self.assertIn("<td>蓝色纸</td>", summary_html)
            self.assertIn("<th>纸材质</th>", detail_html)
            self.assertIn("<td>蓝色纸</td>", detail_html)


if __name__ == "__main__":
    unittest.main()
