import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

import ezdxf
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.admin_pages import admin_home, confirm_drawing_from_page, download_drawing_file, drawing_detail_page, drawing_preview_page, drawing_version_label, page, product_outbound_analysis_page, render_dxf_svg
from app.database import Base
from app.models import ProductDrawing


class AdminNavigationAndDrawingConfirmTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def test_sidebar_order_and_home_keeps_comprehensive_outbound_shortcut(self) -> None:
        with self.Session() as db:
            home_html = admin_home(db=db).body.decode("utf-8")

        self.assertLess(home_html.index('href="/admin">后台首页'), home_html.index("<summary>图纸管理</summary>"))
        self.assertLess(home_html.index("<summary>图纸管理</summary>"), home_html.index('href="/admin/plans">计划管理'))
        self.assertLess(home_html.index('href="/admin/plans">计划管理'), home_html.index("<summary>材料管理</summary>"))
        self.assertLess(home_html.index("<summary>材料管理</summary>"), home_html.index("<summary>成品管理</summary>"))
        self.assertIn('href="/admin/reports/outbound">综合出库统计', home_html)
        self.assertIn('href="/admin/reports/product-outbound">产品出库分析', home_html)

    def test_product_outbound_analysis_links_to_comprehensive_outbound_report(self) -> None:
        with self.Session() as db:
            html = product_outbound_analysis_page(db=db).body.decode("utf-8")

        self.assertIn('href="/admin/reports/outbound">综合出库统计', html)

    def test_confirming_existing_drawing_increments_a_version_and_redirects_success_notice(self) -> None:
        with self.Session() as db:
            drawing = ProductDrawing(
                product_code="TNX-001",
                product_name="旧名称",
                dxf_file_url="/tmp/tnx-001.dxf",
                material="65Mn",
                product_thickness=1.2,
                plate_thickness=1.0,
                max_outer_diameter=100,
                confirmed=1,
                is_active=1,
                version=1,
            )
            db.add(drawing)
            db.commit()
            db.refresh(drawing)

            response = confirm_drawing_from_page(
                drawing_id=drawing.id,
                product_code="TNX-001",
                product_name="新名称",
                product_category="汽车",
                remark="",
                material="65Mn",
                max_outer_diameter="100",
                min_inner_diameter="50",
                product_thickness="1.2",
                plate_thickness="1.0",
                teeth_count="32",
                module="2",
                pressure_angle="20",
                profile_shift_coefficient="",
                span_teeth_count="",
                common_normal_length="",
                pin_diameter="",
                pin_span="",
                expected_scrap_size="φ50",
                db=db,
            )

            db.refresh(drawing)
            self.assertEqual(drawing.version, 2)
            self.assertEqual(drawing_version_label(db, drawing.id), "TNX-001 A2（当前）")
            self.assertEqual(response.headers["location"], f"/admin/drawings/{drawing.id}?notice=confirmed")

    def test_drawing_success_notice_renders_popup_script(self) -> None:
        html = page("测试", "", notice="confirmed").body.decode("utf-8")

        self.assertIn("更新成功", html)
        self.assertIn("alert(", html)

    def test_drawing_confirm_saves_and_displays_remark(self) -> None:
        with self.Session() as db:
            drawing = ProductDrawing(
                dxf_file_url="/tmp/remark.dxf",
                confirmed=0,
                is_active=1,
            )
            db.add(drawing)
            db.commit()
            db.refresh(drawing)

            confirm_drawing_from_page(
                drawing_id=drawing.id,
                product_code="NOTE-001",
                product_name="带备注产品",
                product_category="汽车",
                material="65Mn",
                max_outer_diameter="100",
                min_inner_diameter="50",
                product_thickness="1.2",
                plate_thickness="1.0",
                teeth_count="32",
                module="2",
                pressure_angle="20",
                profile_shift_coefficient="",
                span_teeth_count="",
                common_normal_length="",
                pin_diameter="",
                pin_span="",
                expected_scrap_size="φ50",
                remark="客户要求热处理",
                db=db,
            )

            db.refresh(drawing)
            html = drawing_detail_page(drawing.id, db=db).body.decode("utf-8")

            self.assertEqual(drawing.remark, "客户要求热处理")
            self.assertIn("客户要求热处理", html)

    def test_tooth_type_select_is_embedded_in_teeth_count_field(self) -> None:
        with self.Session() as db:
            drawing = ProductDrawing(
                product_code="TOOTH-001",
                dxf_file_url="/tmp/tooth-layout.dxf",
                tooth_type="OT",
                teeth_count_text="48(52)",
                confirmed=0,
                is_active=1,
            )
            db.add(drawing)
            db.commit()
            db.refresh(drawing)

            html = drawing_detail_page(drawing.id, db=db).body.decode("utf-8")

            self.assertNotIn("<label>齿型</label>", html)
            self.assertIn('<label>齿数 z</label><div class="inline-input-group tooth-count-field">', html)
            self.assertIn('<select name="tooth_type"', html)
            self.assertIn('<input name="teeth_count"', html)

    def test_drawing_preview_uses_cad_svg_renderer_and_links_original_file(self) -> None:
        with TemporaryDirectory() as temp_dir, self.Session() as db:
            dxf_path = Path(temp_dir) / "preview.dxf"
            doc = ezdxf.new("R2010")
            msp = doc.modelspace()
            msp.add_line((0, 0), (80, 0))
            msp.add_text("TNX001", dxfattribs={"height": 3}).set_placement((5, 8))
            doc.saveas(dxf_path)

            svg = render_dxf_svg(str(dxf_path))
            self.assertIn("<svg", svg)
            self.assertNotIn("实体统计", svg)

            drawing = ProductDrawing(
                product_code="PREVIEW-001",
                dxf_file_url=str(dxf_path),
                confirmed=1,
                is_active=1,
            )
            db.add(drawing)
            db.commit()
            db.refresh(drawing)

            html = drawing_preview_page(drawing.id, db=db).body.decode("utf-8")
            self.assertIn("CAD渲染预览", html)
            self.assertIn(f'/admin/drawings/{drawing.id}/download', html)

            download_response = download_drawing_file(drawing.id, db=db)
            self.assertEqual(Path(download_response.path), dxf_path)


if __name__ == "__main__":
    unittest.main()
