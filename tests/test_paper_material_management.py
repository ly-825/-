import unittest
from decimal import Decimal

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import PaperInventoryBatch, PaperInventoryTransaction, PaperSpecification
from app.schema_migrations import ensure_runtime_schema


class PaperMaterialSchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def test_paper_models_use_three_independent_tables_and_decimal_price(self) -> None:
        table_names = set(inspect(self.engine).get_table_names())
        self.assertIn("paper_specifications", table_names)
        self.assertIn("paper_inventory_batches", table_names)
        self.assertIn("paper_inventory_transactions", table_names)

        with self.Session() as db:
            spec = PaperSpecification(
                paper_type="roll",
                model="Tnx236.2A",
                material_name="蓝纸",
                thickness=0.5,
                inner_diameter=80,
                outer_diameter=120,
                is_active=1,
            )
            db.add(spec)
            db.flush()
            batch = PaperInventoryBatch(
                specification_id=spec.id,
                batch_code="PAPER-001",
                paper_type="roll",
                model=spec.model,
                material_name=spec.material_name,
                thickness=spec.thickness,
                inner_diameter=spec.inner_diameter,
                outer_diameter=spec.outer_diameter,
                quantity=20,
                unit_price=Decimal("12.30"),
                status="available",
            )
            db.add(batch)
            db.flush()
            db.add(
                PaperInventoryTransaction(
                    inventory_id=batch.id,
                    transaction_type="in",
                    quantity=20,
                    before_quantity=0,
                    after_quantity=20,
                )
            )
            db.commit()
            db.refresh(batch)

            self.assertEqual(batch.unit_price, Decimal("12.30"))
            self.assertEqual(batch.specification_id, spec.id)

    def test_runtime_schema_creates_paper_tables_idempotently(self) -> None:
        engine = create_engine("sqlite:///:memory:")

        ensure_runtime_schema(engine)
        ensure_runtime_schema(engine)

        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        self.assertTrue(
            {
                "paper_specifications",
                "paper_inventory_batches",
                "paper_inventory_transactions",
            }.issubset(tables)
        )
        batch_indexes = {index["name"] for index in inspector.get_indexes("paper_inventory_batches")}
        self.assertIn("ix_paper_inventory_batches_model", batch_indexes)
        self.assertIn("ix_paper_inventory_batches_status", batch_indexes)


if __name__ == "__main__":
    unittest.main()
