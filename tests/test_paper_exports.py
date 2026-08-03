import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import PaperInventoryBatch, PaperInventoryTransaction, PaperSpecification
from app.paper_admin_pages import paper_inventory_page, paper_transactions_page
from app.services.excel_export import build_export_rows


class PaperExportTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _seed(self, db) -> None:
        roll = PaperSpecification(
            paper_type="roll",
            model="Tnx236.2A",
            material_name="蓝纸",
            thickness=0.5,
            inner_diameter=80,
            outer_diameter=120,
            is_active=1,
        )
        sheet = PaperSpecification(
            paper_type="sheet",
            model="0.4×500×300",
            material_name="白纸",
            thickness=0.4,
            length=500,
            width=300,
            is_active=1,
        )
        db.add_all([roll, sheet])
        db.flush()
        base_time = datetime(2026, 7, 1, 8, 0)
        batches = [
            PaperInventoryBatch(
                specification_id=roll.id,
                batch_code="ROLL-1",
                paper_type="roll",
                model=roll.model,
                material_name=roll.material_name,
                thickness=roll.thickness,
                inner_diameter=80,
                outer_diameter=120,
                quantity=2,
                unit_price=Decimal("10.00"),
                status="available",
                created_at=base_time,
                updated_at=base_time,
            ),
            PaperInventoryBatch(
                specification_id=roll.id,
                batch_code="ROLL-2",
                paper_type="roll",
                model=roll.model,
                material_name=roll.material_name,
                thickness=roll.thickness,
                inner_diameter=80,
                outer_diameter=120,
                quantity=3,
                unit_price=Decimal("12.50"),
                status="available",
                created_at=base_time + timedelta(minutes=1),
                updated_at=base_time + timedelta(minutes=1),
            ),
            PaperInventoryBatch(
                specification_id=roll.id,
                batch_code="ROLL-EMPTY",
                paper_type="roll",
                model=roll.model,
                material_name=roll.material_name,
                thickness=roll.thickness,
                inner_diameter=80,
                outer_diameter=120,
                quantity=0,
                unit_price=Decimal("99.00"),
                status="used",
                created_at=base_time - timedelta(minutes=1),
                updated_at=base_time - timedelta(minutes=1),
            ),
            PaperInventoryBatch(
                specification_id=sheet.id,
                batch_code="SHEET-1",
                paper_type="sheet",
                model=sheet.model,
                material_name=sheet.material_name,
                thickness=sheet.thickness,
                length=500,
                width=300,
                quantity=8,
                unit_price=Decimal("2.30"),
                status="available",
                created_at=base_time + timedelta(minutes=2),
                updated_at=base_time + timedelta(minutes=2),
            ),
        ]
        db.add_all(batches)
        db.flush()
        db.add(
            PaperInventoryTransaction(
                inventory_id=batches[0].id,
                transaction_type="in",
                quantity=2,
                before_quantity=0,
                after_quantity=2,
                operator_name="张三",
                remark="采购入库",
            )
        )
        db.commit()

    def test_paper_inventory_export_has_live_price_range_and_sorted_rows(self) -> None:
        with self.Session() as db:
            self._seed(db)

            title, headings, rows = build_export_rows("paper_inventory", {}, db)

        self.assertEqual(title, "纸材库存")
        self.assertEqual(
            headings,
            ["类型", "型号", "纸材名称/材质", "尺寸", "库存数量", "单位", "最低单价", "最高单价", "批次数", "库位", "最近更新时间"],
        )
        self.assertEqual(rows[0][0:4], ["纸圈", "Tnx236.2A", "蓝纸", "0.5×80×120"])
        self.assertEqual(rows[0][6:8], ["10.00", "12.50"])
        self.assertEqual(rows[1][0:4], ["纸张", "0.4×500×300", "白纸", "0.4×500×300"])
        self.assertNotIn("99.00", str(rows))

    def test_paper_transaction_export_and_page_links(self) -> None:
        with self.Session() as db:
            self._seed(db)

            title, headings, rows = build_export_rows("paper_transactions", {}, db)
            inventory_html = paper_inventory_page(db=db).body.decode("utf-8")
            transaction_html = paper_transactions_page(db=db).body.decode("utf-8")

        self.assertEqual(title, "纸材流水")
        self.assertEqual(
            headings,
            ["流水号", "类型", "批次编号", "纸材类型", "型号", "纸材名称/材质", "尺寸", "数量", "单位", "单价", "操作前库存", "操作后库存", "客户/去向", "操作人", "备注", "创建时间"],
        )
        self.assertEqual(rows[0][2:7], ["ROLL-1", "纸圈", "Tnx236.2A", "蓝纸", "0.5×80×120"])
        self.assertIn('/admin/exports/paper_inventory', inventory_html)
        self.assertIn('/admin/exports/paper_transactions', transaction_html)


if __name__ == "__main__":
    unittest.main()
