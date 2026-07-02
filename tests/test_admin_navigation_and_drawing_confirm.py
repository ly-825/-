import unittest
from io import BytesIO
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from pathlib import Path

import ezdxf
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.admin_pages import admin_home, confirm_drawing_from_page, download_drawing_file, drawing_detail_page, drawing_preview_page, drawing_version_label, drawings_page, open_local_drawing_file_from_page, page, product_outbound_analysis_page, render_dxf_svg
import app.admin_pages as admin_pages
from app.database import Base
from app.models import ProductDrawing
import app.services.drawing_upload as drawing_upload_service


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

    def test_drawing_upload_page_uses_one_multi_file_uploader_only(self) -> None:
        with self.Session() as db:
            html = drawings_page(db=db).body.decode("utf-8")

        self.assertEqual(html.count('type="file"'), 1)
        self.assertIn('action="/admin/drawings/upload-batch"', html)
        self.assertIn('name="files"', html)
        self.assertIn("multiple", html)
        self.assertNotIn('method="get" action="/admin/drawings"', html)
        self.assertNotIn("图纸记录", html)
        self.assertNotIn("批量生成高清预览", html)

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

    def test_drawing_detail_hides_browser_temporary_preview_entry(self) -> None:
        with self.Session() as db:
            drawing = ProductDrawing(
                product_code="NO-BROWSER-PREVIEW",
                dxf_file_url="/tmp/no-browser-preview.dxf",
                confirmed=1,
                is_active=1,
            )
            db.add(drawing)
            db.commit()
            db.refresh(drawing)

            html = drawing_detail_page(drawing.id, db=db).body.decode("utf-8")

            self.assertIn("用本机软件打开图纸", html)
            self.assertIn("下载DXF", html)
            self.assertNotIn("浏览器临时预览", html)
            self.assertNotIn(f'/admin/drawings/{drawing.id}/preview"', html)

    def test_drawing_detail_uses_local_software_entry_and_links_original_file(self) -> None:
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

            html = drawing_detail_page(drawing.id, db=db).body.decode("utf-8")
            self.assertIn("用本机软件打开图纸", html)
            self.assertIn(f'/admin/drawings/{drawing.id}/download', html)
            preview_response = drawing_preview_page(drawing.id, db=db)
            self.assertEqual(preview_response.status_code, 303)
            self.assertEqual(preview_response.headers["location"], f"/admin/drawings/{drawing.id}")

            download_response = download_drawing_file(drawing.id, db=db)
            self.assertEqual(Path(download_response.path), dxf_path)

    def test_open_local_drawing_file_route_invokes_system_opener(self) -> None:
        with TemporaryDirectory() as temp_dir, self.Session() as db:
            dxf_path = Path(temp_dir) / "open-local.dxf"
            dxf_path.write_text("0\nEOF\n", encoding="utf-8")
            drawing = ProductDrawing(
                product_code="OPEN-LOCAL",
                dxf_file_url=str(dxf_path),
                confirmed=1,
                is_active=1,
            )
            db.add(drawing)
            db.commit()
            db.refresh(drawing)
            opened_paths: list[Path] = []
            original_open_local_file = admin_pages.open_local_file
            admin_pages.open_local_file = lambda path: opened_paths.append(path)
            try:
                response = open_local_drawing_file_from_page(drawing.id, db=db)
            finally:
                admin_pages.open_local_file = original_open_local_file

            self.assertEqual(opened_paths, [dxf_path])
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], f"/admin/drawings/{drawing.id}?notice=opened")

    def test_uploading_same_product_code_returns_existing_drawing_even_when_file_hash_differs(self) -> None:
        with TemporaryDirectory() as temp_dir, self.Session() as db:
            existing_path = Path(temp_dir) / "existing.dxf"
            existing_path.write_text("old", encoding="utf-8")
            existing = ProductDrawing(
                product_code="TNX-DUP",
                dxf_file_url=str(existing_path),
                file_hash="old-hash",
                confirmed=1,
                is_active=1,
            )
            db.add(existing)
            db.commit()
            db.refresh(existing)

            original_upload_dir = drawing_upload_service.settings.upload_dir
            original_parse_dxf = drawing_upload_service.parse_dxf
            original_recognize_drawing = drawing_upload_service.recognize_drawing
            original_generate_preview = drawing_upload_service.generate_drawing_preview
            drawing_upload_service.settings.upload_dir = temp_dir
            drawing_upload_service.parse_dxf = lambda _path: {"gear_candidates": {}, "geometry_summary": {"bounding_box": {}}}
            drawing_upload_service.recognize_drawing = lambda _candidates: {
                "product_code": "TNX-DUP",
                "need_manual_review": False,
                "confidence": 90,
            }
            drawing_upload_service.generate_drawing_preview = lambda _drawing: None
            try:
                uploaded = SimpleNamespace(filename="same-code.dxf", file=BytesIO(b"new-file-content"))
                drawing, duplicated = drawing_upload_service.save_uploaded_drawing(uploaded, db)
            finally:
                drawing_upload_service.settings.upload_dir = original_upload_dir
                drawing_upload_service.parse_dxf = original_parse_dxf
                drawing_upload_service.recognize_drawing = original_recognize_drawing
                drawing_upload_service.generate_drawing_preview = original_generate_preview

            self.assertTrue(duplicated)
            self.assertEqual(drawing.id, existing.id)
            self.assertEqual(db.query(ProductDrawing).count(), 1)

    def test_pending_and_confirmed_drawing_lists_only_show_primary_columns(self) -> None:
        with self.Session() as db:
            confirmed = ProductDrawing(
                product_code="TNX-COMPACT",
                product_name="紧凑列表",
                product_category="汽车",
                dxf_file_url="/tmp/compact.dxf",
                material="65Mn",
                product_thickness=1.6,
                plate_thickness=0.8,
                max_outer_diameter=120,
                min_inner_diameter=84,
                tooth_type="OT",
                teeth_count_text="48(52)",
                module_text="DP",
                common_normal_length_text="58.26-58.14",
                confirmed=1,
                is_active=1,
            )
            pending = ProductDrawing(
                product_code="TNX-PENDING",
                product_name="待确认列表",
                product_category="摩托车",
                dxf_file_url="/tmp/pending.dxf",
                material="Q235",
                confirmed=0,
                is_active=1,
            )
            db.add_all([confirmed, pending])
            db.commit()

            from app.admin_pages import confirmed_drawings_page, pending_drawings_page

            confirmed_html = confirmed_drawings_page(db=db).body.decode("utf-8")
            pending_html = pending_drawings_page(db=db).body.decode("utf-8")
            detail_html = drawing_detail_page(confirmed.id, db=db).body.decode("utf-8")

            headings = "<th>产品编号</th><th>产品名称</th><th>分类/材质</th><th>版本</th><th>状态</th><th>操作</th>"
            for html in (confirmed_html, pending_html):
                self.assertIn(headings, html)
                self.assertNotIn("<th>厚度</th>", html)
                self.assertNotIn("<th>尺寸</th>", html)
                self.assertNotIn("<th>齿轮参数</th>", html)
                self.assertIn('class="compact-list"', html)
            self.assertIn("TNX-COMPACT", confirmed_html)
            self.assertNotIn("TNX-PENDING", confirmed_html)
            self.assertIn("TNX-PENDING", pending_html)
            self.assertIn("总成品厚度", detail_html)
            self.assertIn("钢板厚度", detail_html)
            self.assertIn("齿数 z", detail_html)


if __name__ == "__main__":
    unittest.main()
