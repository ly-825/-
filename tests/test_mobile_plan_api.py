import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import MaterialInventory, ProductDrawing
from app.routers.mobile_plan import mobile_plan_drawings, mobile_plan_match


class MobilePlanApiTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def test_drawings_and_match_use_shared_plan_result(self) -> None:
        with self.Session() as db:
            drawing = ProductDrawing(
                product_code="TNX-MOBILE-PLAN",
                product_name="移动计划",
                dxf_file_url="/tmp/mobile-plan.dxf",
                material="65Mn",
                plate_thickness=1.2,
                max_outer_diameter=100,
                confirmed=1,
                is_active=1,
            )
            db.add(drawing)
            db.flush()
            db.add(
                MaterialInventory(
                    inventory_type="raw_plate",
                    material_code="RAW-PLAN",
                    material="65Mn",
                    thickness=1.2,
                    shape="rectangle",
                    length=130,
                    width=130,
                    quantity=5,
                    status="available",
                )
            )
            db.commit()

            drawings = mobile_plan_drawings(q="TNX-MOBILE", db=db)
            result = mobile_plan_match(drawing_id=drawing.id, quantity=10, db=db)

            self.assertEqual(drawings[0]["product_code"], "TNX-MOBILE-PLAN")
            self.assertEqual(result["raw_plate"]["quantity"], 5)
            self.assertEqual(result["recommendation_code"], "raw_plate")


if __name__ == "__main__":
    unittest.main()
