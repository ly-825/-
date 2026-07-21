import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import PaperInventoryBatch, PaperInventoryTransaction, PaperSpecification
from app.schema_migrations import ensure_runtime_schema
from app.services.paper_inventory import (
    normalize_paper_specification,
    outbound_paper_fifo,
    paper_inventory_groups,
    reverse_paper_transaction,
)


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


class PaperInventoryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def test_normalization_generates_sheet_model_and_validates_roll_diameters(self) -> None:
        sheet = normalize_paper_specification(
            "sheet", "ignored", "白纸", 0.5, None, None, 400, 400
        )

        self.assertEqual(sheet["model"], "0.5×400×400")
        self.assertIsNone(sheet["inner_diameter"])
        with self.assertRaisesRegex(HTTPException, "外径必须大于内径"):
            normalize_paper_specification(
                "roll", "Tnx236.2A", "蓝纸", 0.5, 120, 80, None, None
            )

    def test_group_price_range_excludes_zero_stock_batches(self) -> None:
        base_time = datetime(2026, 7, 1, 8, 0)
        batches = [
            PaperInventoryBatch(
                specification_id=1,
                batch_code="P-1",
                paper_type="roll",
                model="Tnx236.2A",
                material_name="蓝纸",
                thickness=0.5,
                inner_diameter=80,
                outer_diameter=120,
                quantity=2,
                unit_price=Decimal("10.00"),
                status="available",
                created_at=base_time,
                updated_at=base_time,
            ),
            PaperInventoryBatch(
                specification_id=1,
                batch_code="P-2",
                paper_type="roll",
                model="Tnx236.2A",
                material_name="蓝纸",
                thickness=0.5,
                inner_diameter=80,
                outer_diameter=120,
                quantity=5,
                unit_price=Decimal("12.50"),
                status="available",
                created_at=base_time + timedelta(minutes=1),
                updated_at=base_time + timedelta(minutes=1),
            ),
            PaperInventoryBatch(
                specification_id=1,
                batch_code="P-OLD",
                paper_type="roll",
                model="Tnx236.2A",
                material_name="蓝纸",
                thickness=0.5,
                inner_diameter=80,
                outer_diameter=120,
                quantity=0,
                unit_price=Decimal("99.00"),
                status="used",
                created_at=base_time - timedelta(minutes=1),
                updated_at=base_time - timedelta(minutes=1),
            ),
        ]

        groups = paper_inventory_groups(batches)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["quantity"], 7)
        self.assertEqual(groups[0]["price_min"], Decimal("10.00"))
        self.assertEqual(groups[0]["price_max"], Decimal("12.50"))
        self.assertEqual(groups[0]["unit"], "圈")

    def test_fifo_is_atomic_and_reversal_restores_only_affected_batch(self) -> None:
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
            first = PaperInventoryBatch(
                specification_id=spec.id,
                batch_code="P-1",
                paper_type="roll",
                model=spec.model,
                material_name=spec.material_name,
                thickness=spec.thickness,
                inner_diameter=80,
                outer_diameter=120,
                quantity=2,
                unit_price=Decimal("10.00"),
                status="available",
                created_at=datetime(2026, 7, 1, 8, 0),
            )
            second = PaperInventoryBatch(
                specification_id=spec.id,
                batch_code="P-2",
                paper_type="roll",
                model=spec.model,
                material_name=spec.material_name,
                thickness=spec.thickness,
                inner_diameter=80,
                outer_diameter=120,
                quantity=5,
                unit_price=Decimal("12.50"),
                status="available",
                created_at=datetime(2026, 7, 1, 8, 1),
            )
            db.add_all([first, second])
            db.commit()

            records = outbound_paper_fifo(spec.id, 4, None, "一车间", "张三", "领用", db)
            db.flush()

            self.assertEqual((first.quantity, second.quantity), (0, 3))
            self.assertEqual([record.quantity for record in records], [2, 2])
            before_failed_outbound = (first.quantity, second.quantity)
            with self.assertRaisesRegex(HTTPException, "库存不足"):
                outbound_paper_fifo(spec.id, 4, None, None, None, None, db)
            self.assertEqual((first.quantity, second.quantity), before_failed_outbound)

            original_price = first.unit_price
            reversal = reverse_paper_transaction(records[0].id, "李四", "撤回测试", db)
            db.flush()

            self.assertEqual(first.quantity, 2)
            self.assertEqual(second.quantity, 3)
            self.assertEqual(first.unit_price, original_price)
            self.assertEqual(reversal.reversed_transaction_id, records[0].id)


if __name__ == "__main__":
    unittest.main()
