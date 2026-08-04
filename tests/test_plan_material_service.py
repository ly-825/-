import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import MaterialInventory, ProductDrawing
from app.services.plan_material_service import list_plan_drawings, match_plan_materials


class PlanMaterialServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(
            bind=engine, autoflush=False, autocommit=False
        )

    def add_drawing(self, db, code: str = "TNX-001") -> ProductDrawing:
        drawing = ProductDrawing(
            product_code=code,
            product_name="同步测试产品",
            dxf_file_url=f"/tmp/{code}.dxf",
            material="65Mn",
            plate_thickness=1.2,
            max_outer_diameter=100,
            min_inner_diameter=40,
            teeth_count=20,
            teeth_count_text="20",
            confirmed=1,
            is_active=1,
        )
        db.add(drawing)
        db.flush()
        return drawing

    def add_inventory(
        self,
        db,
        *,
        inventory_type: str,
        quantity: int,
        material_code: str | None = None,
        material: str = "65Mn",
        thickness: float = 1.2,
        diameter: float | None = None,
        length: float | None = None,
        width: float | None = None,
        status: str = "available",
    ) -> MaterialInventory:
        item = MaterialInventory(
            inventory_type=inventory_type,
            material_code=material_code,
            material=material,
            thickness=thickness,
            diameter=diameter,
            length=length,
            width=width,
            shape="circle" if diameter else "rectangle",
            quantity=quantity,
            status=status,
            location="A-01",
        )
        db.add(item)
        db.flush()
        return item

    def test_filters_confirmed_current_drawings_with_pc_fields(self) -> None:
        with self.Session() as db:
            expected = self.add_drawing(db)
            self.add_drawing(db, "OTHER-001").material = "50#"
            inactive = self.add_drawing(db, "TNX-OLD")
            inactive.is_active = 0
            db.commit()

            drawings = list_plan_drawings(
                db,
                q="TNX",
                material="65Mn",
                thickness="1.2",
                outer_diameter="100",
                inner_diameter="40",
                teeth_count="20",
            )

            self.assertEqual([item.id for item in drawings], [expected.id])

    def test_matches_three_inventory_types_and_recommends_scrap(self) -> None:
        with self.Session() as db:
            drawing = self.add_drawing(db)
            product = self.add_inventory(
                db,
                inventory_type="product",
                material_code=drawing.product_code,
                quantity=2,
            )
            scrap = self.add_inventory(
                db, inventory_type="scrap", quantity=6, diameter=110
            )
            self.add_inventory(
                db,
                inventory_type="scrap",
                quantity=99,
                material="50#",
                diameter=110,
            )
            raw_plate = self.add_inventory(
                db,
                inventory_type="raw_plate",
                quantity=10,
                length=130,
                width=130,
            )
            db.commit()
            before = {item.id: item.quantity for item in (product, scrap, raw_plate)}

            result = match_plan_materials(
                db, drawing_id=drawing.id, quantity=6
            )

            self.assertEqual(result["requested_quantity"], 6)
            self.assertEqual(result["product"]["quantity"], 2)
            self.assertEqual(result["scrap"]["quantity"], 6)
            self.assertEqual(result["raw_plate"]["quantity"], 10)
            self.assertEqual(result["recommendation_code"], "scrap")
            self.assertEqual(
                [row["id"] for row in result["scrap"]["batches"]],
                [scrap.id],
            )
            self.assertEqual(
                {item.id: item.quantity for item in (product, scrap, raw_plate)},
                before,
            )

    def test_rejects_invalid_quantity_and_missing_drawing(self) -> None:
        with self.Session() as db:
            drawing = self.add_drawing(db)
            db.commit()
            with self.assertRaises(HTTPException) as quantity_error:
                match_plan_materials(db, drawing_id=drawing.id, quantity=0)
            self.assertEqual(quantity_error.exception.status_code, 400)

            with self.assertRaises(HTTPException) as missing_error:
                match_plan_materials(db, drawing_id=9999, quantity=1)
            self.assertEqual(missing_error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
