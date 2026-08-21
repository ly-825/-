import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.context import current_account
from app.database import Base
from app.models import (
    Account,
    InventoryTransactionRecord,
    MaterialInventory,
    MobileRequestRecord,
    OperationLog,
    RawPlateSpecification,
)
from app.routers.mobile_raw_plates import (
    RawPlateInboundPayload,
    RawPlateBatchUpdatePayload,
    RawPlateSpecificationPayload,
    create_mobile_raw_plate_specification,
    mobile_raw_plate_inbound,
    router,
    update_mobile_raw_plate_batch,
)


class MobileRawPlateApiTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self.account_token = current_account.set(
            Account(username="tns008", display_name="张三", role="employee")
        )

    def tearDown(self) -> None:
        current_account.reset(self.account_token)

    def test_specification_and_inbound_replay_without_duplicate_mutation(self) -> None:
        with self.Session() as db:
            spec_payload = RawPlateSpecificationPayload(
                client_request_id="raw-spec-001",
                material="65Mn",
                length=1270,
                width=130,
                thickness=3.0,
                density=7.85,
                remark="移动创建",
            )
            first_spec = create_mobile_raw_plate_specification(spec_payload, db=db)
            replayed_spec = create_mobile_raw_plate_specification(spec_payload, db=db)
            self.assertEqual(first_spec, replayed_spec)
            self.assertEqual(db.query(RawPlateSpecification).count(), 1)

            inbound_payload = RawPlateInboundPayload(
                client_request_id="raw-in-001",
                specification_id=first_spec["id"],
                total_weight_ton=1,
                location="A-01",
                operator_name="伪造甲",
                remark="采购",
            )
            first = mobile_raw_plate_inbound(inbound_payload, db=db)
            replayed = mobile_raw_plate_inbound(
                inbound_payload.model_copy(update={"operator_name": "伪造乙"}),
                db=db,
            )
            self.assertEqual(first, replayed)
            self.assertEqual(first["quantity"], 257)
            self.assertEqual(db.query(MaterialInventory).count(), 1)
            self.assertEqual(db.query(MobileRequestRecord).count(), 2)
            transaction = db.query(InventoryTransactionRecord).one()
            self.assertEqual(transaction.operator_name, "张三")

            updated = update_mobile_raw_plate_batch(
                first["item"]["id"],
                RawPlateBatchUpdatePayload(
                    client_request_id="raw-edit-001",
                    raw_plate_model="1270×130×3",
                    material_code="RAW-001",
                    material="65Mn",
                    length=1270,
                    width=130,
                    thickness=3.0,
                    location="A-02",
                    status="available",
                    operator_name="伪造批次修改员",
                    remark="调整库位",
                ),
                db=db,
            )
            self.assertEqual(updated["location"], "A-02")
            edit_log = db.query(OperationLog).filter_by(
                action="raw_plate_batch_update"
            ).one()
            self.assertEqual(edit_log.operator_name, "张三")

            with self.assertRaises(HTTPException) as changed:
                mobile_raw_plate_inbound(
                    inbound_payload.model_copy(update={"total_weight_ton": 2}), db=db
                )
            self.assertEqual(changed.exception.status_code, 409)

    def test_static_write_routes_are_registered_before_dynamic_batch_route(self) -> None:
        paths = [route.path for route in router.routes]
        dynamic_index = paths.index("/raw-plates/{batch_id}")
        for path in (
            "/raw-plates/inbound",
            "/raw-plates/outbound",
            "/raw-plates/transactions",
        ):
            self.assertLess(paths.index(path), dynamic_index)


if __name__ == "__main__":
    unittest.main()
