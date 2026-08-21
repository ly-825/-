import unittest
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.context import current_account
from app.auth.operator import verified_operator_name
from app.database import Base
from app.models import Account, InventoryTransactionRecord, MaterialInventory, OperationLog
from app.routers.inventory import reverse_transaction
from app.schemas import TransactionReverse
from app.services.operation_log import record_operation_log


class OperatorIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_verified_operator_name_uses_trimmed_authenticated_name(self) -> None:
        token = current_account.set(
            Account(username="tns008", display_name=" 张三 ", role="employee")
        )
        try:
            self.assertEqual(verified_operator_name(), "张三")
        finally:
            current_account.reset(token)

    def test_verified_operator_name_rejects_missing_identity(self) -> None:
        token = current_account.set(None)
        try:
            with self.assertRaises(HTTPException) as caught:
                verified_operator_name()
            self.assertEqual(caught.exception.status_code, 401)
        finally:
            current_account.reset(token)

    def test_verified_operator_name_rejects_blank_identity(self) -> None:
        token = current_account.set(
            Account(username="tns008", display_name="   ", role="employee")
        )
        try:
            with self.assertRaises(HTTPException) as caught:
                verified_operator_name()
            self.assertEqual(caught.exception.status_code, 400)
        finally:
            current_account.reset(token)

    def test_operation_log_trims_authenticated_name(self) -> None:
        token = current_account.set(
            Account(username="tns008", display_name=" 张三 ", role="employee")
        )
        try:
            with self.Session() as db:
                record_operation_log(db, "test", "inventory")
                db.flush()
                log = db.query(OperationLog).one()
                self.assertEqual(log.operator_name, "张三")
        finally:
            current_account.reset(token)

    def test_pc_inventory_forms_have_no_editable_operator_name(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative_path in ("app/admin_pages.py", "app/paper_admin_pages.py"):
            source = (root / relative_path).read_text(encoding="utf-8")
            with self.subTest(path=relative_path):
                self.assertNotIn('name="operator_name"', source)
                self.assertNotIn("name='operator_name'", source)

    def test_pc_inventory_reverse_ignores_forged_operator_name(self) -> None:
        token = current_account.set(
            Account(username="boss", display_name="老板", role="owner")
        )
        try:
            with self.Session() as db:
                item = MaterialInventory(
                    inventory_type="product",
                    material_code="TNX-PC-OPERATOR",
                    material="65Mn",
                    thickness=1.2,
                    shape="circle",
                    quantity=2,
                    status="available",
                )
                db.add(item)
                db.flush()
                original = InventoryTransactionRecord(
                    inventory_id=item.id,
                    transaction_type="in",
                    quantity=2,
                    before_quantity=0,
                    after_quantity=2,
                    operator_name="历史人员",
                )
                db.add(original)
                db.commit()

                reversal = reverse_transaction(
                    original.id,
                    TransactionReverse(operator_name="伪造电脑员", remark="录入错误"),
                    db=db,
                )

                self.assertEqual(reversal.operator_name, "老板")
        finally:
            current_account.reset(token)


if __name__ == "__main__":
    unittest.main()
