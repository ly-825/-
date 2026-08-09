import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    InventoryTransactionRecord,
    MaterialInventory,
    ProductDrawing,
    ScrapGenerationRecord,
)
from app.routers import mobile
from app.routers.mobile import (
    InventoryItemOut,
    product_batches,
    product_transactions,
)


class MobileBatchDetailsTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(
            bind=engine, autoflush=False, autocommit=False
        )

    @staticmethod
    def product(code: str, quantity: int, location: str) -> MaterialInventory:
        return MaterialInventory(
            material_code=code,
            inventory_type="product",
            material="65Mn",
            thickness=1.2,
            product_thickness=1.2,
            plate_thickness=1.0,
            shape="circle",
            quantity=quantity,
            location=location,
            status="available",
        )

    def test_product_details_only_return_selected_product_batches_and_transactions(
        self,
    ) -> None:
        with self.Session() as db:
            first = self.product("TNX-DETAIL", 3, "A-01")
            second = self.product("TNX-DETAIL", 5, "A-02")
            unrelated = self.product("TNX-OTHER", 7, "B-01")
            db.add_all([first, second, unrelated])
            db.flush()
            db.add_all(
                [
                    InventoryTransactionRecord(
                        inventory_id=first.id,
                        transaction_type="in",
                        quantity=3,
                        before_quantity=0,
                        after_quantity=3,
                    ),
                    InventoryTransactionRecord(
                        inventory_id=second.id,
                        transaction_type="in",
                        quantity=5,
                        before_quantity=0,
                        after_quantity=5,
                    ),
                    InventoryTransactionRecord(
                        inventory_id=unrelated.id,
                        transaction_type="in",
                        quantity=7,
                        before_quantity=0,
                        after_quantity=7,
                    ),
                ]
            )
            db.commit()

            batches = product_batches("TNX-DETAIL", db)
            transactions = product_transactions(
                product_code="TNX-DETAIL", db=db
            )

            self.assertEqual(
                {item.id for item in batches}, {first.id, second.id}
            )
            self.assertEqual(
                {row.inventory_id for row in transactions},
                {first.id, second.id},
            )
            self.assertNotIn(
                unrelated.id,
                {row.inventory_id for row in transactions},
            )

    def test_unknown_product_returns_empty_details(self) -> None:
        with self.Session() as db:
            self.assertEqual(product_batches("UNKNOWN", db), [])
            self.assertEqual(
                product_transactions(product_code="UNKNOWN", db=db),
                [],
            )

    def test_product_batch_response_keeps_pc_detail_fields(self) -> None:
        with self.Session() as db:
            item = self.product("TNX-FIELDS", 4, "A-03")
            item.paper_material = "黑纸"
            db.add(item)
            db.commit()
            db.refresh(item)

            payload = InventoryItemOut.model_validate(item).model_dump()

            self.assertEqual(payload["product_thickness"], 1.2)
            self.assertEqual(payload["plate_thickness"], 1.0)
            self.assertEqual(payload["paper_material"], "黑纸")
            self.assertIsNotNone(payload["created_at"])
            self.assertIsNotNone(payload["updated_at"])

    def test_product_batch_response_accepts_legacy_replayed_payload(self) -> None:
        legacy_payload = {
            "id": 1,
            "material_code": "TNX-LEGACY",
            "inventory_type": "product",
            "material": "65Mn",
            "thickness": 1.2,
            "shape": "circle",
            "diameter": 80,
            "length": 80,
            "width": 80,
            "usable_size": "φ80",
            "quantity": 3,
            "location": "A-01",
            "paper_material": "黑纸",
            "status": "available",
            "source_product_code": "TNX-LEGACY",
        }

        payload = InventoryItemOut.model_validate(legacy_payload)

        self.assertIsNone(payload.created_at)
        self.assertIsNone(payload.updated_at)

    def test_scrap_details_return_selected_batches_sources_and_transactions(
        self,
    ) -> None:
        with self.Session() as db:
            drawing = ProductDrawing(
                product_code="TNX-SCRAP",
                product_name="余料来源",
                dxf_file_url="/tmp/tnx-scrap.dxf",
                version=2,
                confirmed=1,
                is_active=1,
            )
            db.add(drawing)
            db.flush()
            first = MaterialInventory(
                inventory_type="scrap",
                material="65Mn",
                thickness=2.0,
                shape="circle",
                diameter=80,
                usable_size="φ80",
                quantity=1,
                location="S1",
                status="available",
                source_product_code="TNX-SCRAP",
                source_drawing_id=drawing.id,
            )
            second = MaterialInventory(
                inventory_type="scrap",
                material="65Mn",
                thickness=2.0,
                shape="circle",
                diameter=80,
                usable_size="φ80",
                quantity=2,
                location="S1",
                status="available",
                source_product_code="TNX-SCRAP",
                source_drawing_id=drawing.id,
            )
            unrelated = MaterialInventory(
                inventory_type="scrap",
                material="65Mn",
                thickness=2.0,
                shape="circle",
                diameter=80,
                usable_size="φ80",
                quantity=9,
                location="S2",
                status="available",
                source_product_code="TNX-OTHER",
            )
            db.add_all([first, second, unrelated])
            db.flush()
            db.add_all(
                [
                    ScrapGenerationRecord(
                        source_product_code="TNX-SCRAP",
                        source_drawing_id=drawing.id,
                        scrap_inventory_id=first.id,
                        theoretical_size="φ82",
                        actual_size="φ80",
                        operator_name="王五",
                    ),
                    ScrapGenerationRecord(
                        source_product_code="TNX-SCRAP",
                        source_drawing_id=drawing.id,
                        scrap_inventory_id=second.id,
                        theoretical_size="φ82",
                        actual_size="φ80",
                        operator_name="赵六",
                    ),
                    InventoryTransactionRecord(
                        inventory_id=first.id,
                        transaction_type="confirm",
                        quantity=1,
                        before_quantity=1,
                        after_quantity=1,
                        operator_name="王五",
                    ),
                    InventoryTransactionRecord(
                        inventory_id=second.id,
                        transaction_type="out",
                        quantity=1,
                        before_quantity=3,
                        after_quantity=2,
                        operator_name="赵六",
                    ),
                    InventoryTransactionRecord(
                        inventory_id=unrelated.id,
                        transaction_type="confirm",
                        quantity=9,
                        before_quantity=9,
                        after_quantity=9,
                    ),
                ]
            )
            db.commit()

            result = mobile.scrap_batch_details(
                group_key="65Mn||2.0||φ80||S1",
                db=db,
            )

            self.assertEqual(result.total_quantity, 3)
            self.assertEqual(
                {row.id for row in result.batches}, {first.id, second.id}
            )
            self.assertEqual(
                result.batches[0].source_product_code, "TNX-SCRAP"
            )
            self.assertIn("V2", result.batches[0].source_drawing_label)
            self.assertEqual(
                {row.inventory_id for row in result.transactions},
                {first.id, second.id},
            )
            self.assertNotIn(
                unrelated.id,
                {row.inventory_id for row in result.transactions},
            )

    def test_scrap_details_validate_key_and_return_empty_valid_group(
        self,
    ) -> None:
        with self.Session() as db:
            with self.assertRaises(HTTPException) as raised:
                mobile.scrap_batch_details(group_key="invalid", db=db)
            self.assertEqual(raised.exception.status_code, 400)

            result = mobile.scrap_batch_details(
                group_key="65Mn||2.0||φ80||S1",
                db=db,
            )
            self.assertEqual(result.total_quantity, 0)
            self.assertEqual(result.batches, [])
            self.assertEqual(result.transactions, [])


if __name__ == "__main__":
    unittest.main()
