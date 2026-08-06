import unittest
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import PaperInventoryBatch, PaperInventoryTransaction
from app.services.paper_inventory import (
    create_paper_specification,
    inbound_paper,
    list_paper_inventory,
    list_paper_specifications,
    outbound_paper_fifo,
    reverse_paper_transaction,
    toggle_paper_specification,
    update_paper_specification,
)


class PaperInventoryServiceParityTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(
            bind=engine, autoflush=False, autocommit=False
        )

    def create_roll(self, db):
        return create_paper_specification(
            db,
            paper_type="roll",
            model="3969.01",
            material_name="黑纸",
            thickness=0.5,
            inner_diameter=55,
            outer_diameter=115,
            length=None,
            width=None,
            remark="常用",
        )

    def test_creates_roll_and_generated_sheet_then_filters(self) -> None:
        with self.Session() as db:
            roll = self.create_roll(db)
            sheet = create_paper_specification(
                db,
                paper_type="sheet",
                model="用户输入会被忽略",
                material_name="白纸",
                thickness=0.5,
                inner_diameter=None,
                outer_diameter=None,
                length=400,
                width=400,
                remark="",
            )
            db.flush()

            self.assertEqual(sheet.model, "0.5×400×400")
            self.assertEqual(
                [spec.id for spec in list_paper_specifications(db, paper_type="roll")],
                [roll.id],
            )

            updated = update_paper_specification(
                db,
                roll.id,
                paper_type="roll",
                model="3969.02",
                material_name="蓝纸",
                thickness=0.6,
                inner_diameter=60,
                outer_diameter=120,
                length=None,
                width=None,
                is_active=1,
                remark="更新",
            )
            self.assertEqual((updated.model, updated.material_name), ("3969.02", "蓝纸"))

    def test_inbound_uses_spec_snapshot_decimal_price_and_unit(self) -> None:
        with self.Session() as db:
            roll = self.create_roll(db)
            db.flush()
            receipt = inbound_paper(
                db,
                specification_id=roll.id,
                batch_code="PAPER-001",
                quantity=8,
                unit_price="12.345",
                location="P-01",
                operator_name="张三",
                remark="采购",
            )

            batch = receipt["batch"]
            self.assertEqual(batch.unit_price, Decimal("12.35"))
            self.assertEqual((batch.model, batch.material_name), ("3969.01", "黑纸"))
            groups = list_paper_inventory(db)
            self.assertEqual(groups[0]["quantity"], 8)
            self.assertEqual(groups[0]["unit"], "圈")
            self.assertEqual(
                db.query(PaperInventoryTransaction)
                .filter_by(transaction_type="in")
                .count(),
                1,
            )

            toggle_paper_specification(db, roll.id)
            with self.assertRaises(HTTPException) as stopped:
                inbound_paper(
                    db,
                    specification_id=roll.id,
                    batch_code="PAPER-002",
                    quantity=1,
                    unit_price="1.00",
                )
            self.assertEqual(stopped.exception.status_code, 400)

    def test_fifo_insufficient_is_atomic_and_reversal_restores_batch(self) -> None:
        with self.Session() as db:
            roll = self.create_roll(db)
            db.flush()
            first = inbound_paper(
                db,
                specification_id=roll.id,
                batch_code="PAPER-A",
                quantity=4,
                unit_price="10",
            )["batch"]
            second = inbound_paper(
                db,
                specification_id=roll.id,
                batch_code="PAPER-B",
                quantity=5,
                unit_price="11",
            )["batch"]
            db.flush()

            records = outbound_paper_fifo(
                roll.id, 7, "", "二车间", "李四", "领料", db
            )
            self.assertEqual([record.quantity for record in records], [4, 3])
            self.assertEqual((first.quantity, second.quantity), (0, 2))

            reversal = reverse_paper_transaction(records[1].id, "王五", "撤回", db)
            self.assertEqual(reversal.transaction_type, "in")
            self.assertEqual(second.quantity, 5)

            before = (first.quantity, second.quantity)
            transaction_count = db.query(PaperInventoryTransaction).count()
            with self.assertRaises(HTTPException) as insufficient:
                outbound_paper_fifo(
                    roll.id, 99, "", None, None, None, db
                )
            self.assertEqual(insufficient.exception.status_code, 400)
            self.assertEqual((first.quantity, second.quantity), before)
            self.assertEqual(
                db.query(PaperInventoryTransaction).count(), transaction_count
            )


if __name__ == "__main__":
    unittest.main()
