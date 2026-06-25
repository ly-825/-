import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.admin_pages import drawing_preview_file, drawing_preview_page, regenerate_missing_drawing_previews
from app.config import settings
from app.database import Base
from app.models import ProductDrawing
from app.services.drawing_preview import generate_drawing_preview


class DrawingPdfPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self._old_preview_dir = settings.drawing_preview_dir
        self._old_converter_path = settings.drawing_preview_converter_path
        self._old_converter_args = settings.drawing_preview_converter_args

    def tearDown(self) -> None:
        settings.drawing_preview_dir = self._old_preview_dir
        settings.drawing_preview_converter_path = self._old_converter_path
        settings.drawing_preview_converter_args = self._old_converter_args

    def test_configured_converter_generates_pdf_preview_and_preview_page_redirects_to_detail(self) -> None:
        with TemporaryDirectory() as temp_dir, self.Session() as db:
            root = Path(temp_dir)
            dxf_path = root / "tnx001.dxf"
            dxf_path.write_text("0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
            converter_script = root / "fake_converter.py"
            converter_script.write_text(
                "import pathlib, sys\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])\n"
                "out.parent.mkdir(parents=True, exist_ok=True)\n"
                "out.write_bytes(b'%PDF-1.4\\n%fake preview\\n')\n",
                encoding="utf-8",
            )
            settings.drawing_preview_dir = str(root / "previews")
            settings.drawing_preview_converter_path = sys.executable
            settings.drawing_preview_converter_args = str(converter_script)
            drawing = ProductDrawing(
                product_code="TNX001",
                dxf_file_url=str(dxf_path),
                file_hash="abc123",
                confirmed=1,
                is_active=1,
            )
            db.add(drawing)
            db.commit()
            db.refresh(drawing)

            result = generate_drawing_preview(drawing, force=True)
            db.commit()
            db.refresh(drawing)

            self.assertEqual(result.status, "generated")
            self.assertEqual(drawing.preview_status, "generated")
            self.assertTrue(Path(drawing.preview_file_url).exists())
            self.assertEqual(Path(drawing.preview_file_url).read_bytes()[:8], b"%PDF-1.4")

            preview_response = drawing_preview_page(drawing.id, db=db)
            self.assertEqual(preview_response.status_code, 303)
            self.assertEqual(preview_response.headers["location"], f"/admin/drawings/{drawing.id}")

            response = drawing_preview_file(drawing.id, db=db)
            self.assertEqual(Path(response.path), Path(drawing.preview_file_url))

    def test_batch_generation_creates_missing_pdf_previews_only(self) -> None:
        with TemporaryDirectory() as temp_dir, self.Session() as db:
            root = Path(temp_dir)
            converter_script = root / "fake_converter.py"
            converter_script.write_text(
                "import pathlib, sys\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])\n"
                "out.parent.mkdir(parents=True, exist_ok=True)\n"
                "out.write_bytes(b'%PDF-1.4\\n%batch preview\\n')\n",
                encoding="utf-8",
            )
            settings.drawing_preview_dir = str(root / "previews")
            settings.drawing_preview_converter_path = sys.executable
            settings.drawing_preview_converter_args = str(converter_script)
            missing_dxf = root / "missing.dxf"
            existing_dxf = root / "existing.dxf"
            missing_dxf.write_text("0\nEOF\n", encoding="utf-8")
            existing_dxf.write_text("0\nEOF\n", encoding="utf-8")
            existing_preview = root / "existing.pdf"
            existing_preview.write_bytes(b"%PDF-1.4\n%existing\n")
            missing = ProductDrawing(
                product_code="MISSING",
                dxf_file_url=str(missing_dxf),
                file_hash="missing-hash",
                confirmed=1,
                is_active=1,
            )
            existing = ProductDrawing(
                product_code="EXISTING",
                dxf_file_url=str(existing_dxf),
                file_hash="existing-hash",
                preview_file_url=str(existing_preview),
                preview_status="generated",
                confirmed=1,
                is_active=1,
            )
            db.add_all([missing, existing])
            db.commit()

            html = regenerate_missing_drawing_previews(db=db).body.decode("utf-8")
            db.refresh(missing)
            db.refresh(existing)

            self.assertIn("成功 1 张", html)
            self.assertTrue(Path(missing.preview_file_url).exists())
            self.assertEqual(Path(existing.preview_file_url), existing_preview)


if __name__ == "__main__":
    unittest.main()
