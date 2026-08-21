import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.context import current_account
from app.auth.operator import verified_operator_name
from app.database import Base
from app.models import Account, OperationLog
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


if __name__ == "__main__":
    unittest.main()
