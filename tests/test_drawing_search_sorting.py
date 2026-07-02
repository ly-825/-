import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.admin_pages import confirmed_drawings_page
from app.assistant.tools.drawing import list_drawings_by_parameter
from app.assistant.tools.plan import _find_drawings
from app.database import Base
from app.models import ProductDrawing


class DrawingSearchSortingTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def add_drawings(self, db) -> None:
        base_time = datetime(2026, 1, 1, 8, 0, 0)
        db.add_all(
            [
                ProductDrawing(
                    product_code="A10",
                    product_name="外齿产品",
                    dxf_file_url="/tmp/a10.dxf",
                    tooth_type="OT",
                    teeth_count=48,
                    teeth_count_text="48(52)",
                    module_text="DP15",
                    confirmed=1,
                    is_active=1,
                    updated_at=base_time,
                ),
                ProductDrawing(
                    product_code="A2",
                    product_name="内齿产品",
                    dxf_file_url="/tmp/a2.dxf",
                    tooth_type="IT",
                    teeth_count=41,
                    teeth_count_text="41",
                    module_text="2.5",
                    confirmed=1,
                    is_active=1,
                    updated_at=base_time + timedelta(minutes=1),
                ),
                ProductDrawing(
                    product_code="B1",
                    product_name="外齿小规格",
                    dxf_file_url="/tmp/b1.dxf",
                    tooth_type="OT",
                    teeth_count=4,
                    teeth_count_text="4",
                    module_text="DP8",
                    confirmed=1,
                    is_active=1,
                    updated_at=base_time + timedelta(minutes=2),
                ),
            ]
        )
        db.commit()

    def test_confirmed_drawings_use_natural_product_code_order(self) -> None:
        with self.Session() as db:
            self.add_drawings(db)

            html = confirmed_drawings_page(db=db).body.decode("utf-8")

        self.assertLess(html.index(">A2</td>"), html.index(">A10</td>"))
        self.assertLess(html.index(">A10</td>"), html.index(">B1</td>"))

    def test_combined_tooth_type_and_count_work_in_dedicated_and_general_search(self) -> None:
        with self.Session() as db:
            self.add_drawings(db)

            dedicated_html = confirmed_drawings_page(teeth_count="OT48", db=db).body.decode("utf-8")
            general_html = confirmed_drawings_page(q="OT48(52)", db=db).body.decode("utf-8")

        for html in (dedicated_html, general_html):
            self.assertIn(">A10</td>", html)
            self.assertNotIn(">A2</td>", html)
            self.assertNotIn(">B1</td>", html)

    def test_letter_number_module_search_matches_full_text(self) -> None:
        with self.Session() as db:
            self.add_drawings(db)

            html = confirmed_drawings_page(module="DP15", db=db).body.decode("utf-8")

        self.assertIn(">A10</td>", html)
        self.assertNotIn(">A2</td>", html)

    def test_assistant_and_plan_queries_match_combined_tooth_value(self) -> None:
        with self.Session() as db:
            self.add_drawings(db)

            assistant_response = list_drawings_by_parameter(
                {"_message": "按齿数等于OT48列出图纸"},
                db,
            )
            plan_drawings = _find_drawings(
                {"_message": "查找齿数为OT48的图纸", "filters": {}},
                db,
            )

        self.assertEqual([row["product_code"] for row in assistant_response.data["rows"]], ["A10"])
        self.assertEqual([drawing.product_code for drawing in plan_drawings], ["A10"])


if __name__ == "__main__":
    unittest.main()
