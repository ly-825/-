import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.context import current_account
from app.database import Base
from app.models import (
    Account,
    InventoryTransactionRecord,
    MaterialInventory,
    PaperInventoryTransaction,
    ProductDrawing,
)
from app.routers.mobile_paper import (
    PaperOutboundPayload,
    PaperReversePayload,
    mobile_paper_inventory,
    mobile_paper_outbound,
    reverse_mobile_paper_transaction,
)
from app.routers.mobile_plan import mobile_plan_match
from app.routers.mobile_raw_plates import (
    RawPlateOutboundPayload,
    RawPlateReversePayload,
    mobile_raw_plate_outbound,
    mobile_raw_plates,
    reverse_mobile_raw_plate_transaction,
)
from app.services.paper_inventory import (
    create_paper_specification,
    inbound_paper,
    list_paper_inventory,
)
from app.services.plan_material_service import match_plan_materials
from app.services.raw_plate_inventory import (
    create_raw_plate_specification,
    inbound_raw_plate,
    list_raw_plate_groups,
)


class PcMobileMaterialParityTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(
            bind=engine, autoflush=False, autocommit=False
        )
        self.account_token = current_account.set(
            Account(username="tns008", display_name="张三", role="employee")
        )

    def tearDown(self) -> None:
        current_account.reset(self.account_token)

    def test_pc_raw_plate_write_and_mobile_outbound_share_one_balance(self) -> None:
        with self.Session() as db:
            specification = create_raw_plate_specification(
                db,
                material="65Mn",
                length=1270,
                width=130,
                thickness=3.0,
                density=7.85,
                remark="PC 创建",
            )
            db.flush()
            receipt = inbound_raw_plate(
                db,
                specification_id=specification.id,
                raw_plate_model="",
                material_code="RAW-PARITY",
                material="",
                total_weight_ton=1,
                length=None,
                width=None,
                thickness=None,
                density=None,
                location="A-01",
                operator_name="电脑端",
                remark="一致性验收",
            )
            db.commit()

            mobile_groups = mobile_raw_plates(db=db)
            self.assertEqual(mobile_groups[0]["quantity"], receipt["quantity"])

            outbound = mobile_raw_plate_outbound(
                RawPlateOutboundPayload(
                    client_request_id="parity-raw-out-001",
                    material="65Mn",
                    length=1270,
                    width=130,
                    thickness=3.0,
                    quantity=7,
                    location="",
                    customer_name="一车间",
                    operator_name="手机端",
                    remark="一致性验收",
                ),
                db=db,
            )

            pc_groups = list_raw_plate_groups(db)
            self.assertEqual(pc_groups[0]["quantity"], receipt["quantity"] - 7)
            self.assertEqual(pc_groups[0]["batch_ids"], {receipt["item"].id})
            outbound_record = db.get(
                InventoryTransactionRecord,
                outbound["allocations"][0]["transaction_id"],
            )
            self.assertEqual(outbound_record.operator_name, "张三")

            reversal = reverse_mobile_raw_plate_transaction(
                outbound["allocations"][0]["transaction_id"],
                RawPlateReversePayload(
                    client_request_id="parity-raw-reverse-001",
                    operator_name="手机端",
                    remark="撤销一致性验收",
                ),
                db=db,
            )
            self.assertEqual(
                list_raw_plate_groups(db)[0]["quantity"], receipt["quantity"]
            )
            reversal_record = db.get(InventoryTransactionRecord, reversal["id"])
            self.assertEqual(reversal_record.operator_name, "张三")

    def test_pc_paper_write_and_mobile_outbound_share_one_balance(self) -> None:
        with self.Session() as db:
            specification = create_paper_specification(
                db,
                paper_type="roll",
                model="3969.01",
                material_name="黑纸",
                thickness=0.5,
                inner_diameter=55,
                outer_diameter=115,
                length=None,
                width=None,
                remark="PC 创建",
            )
            db.flush()
            inbound_paper(
                db,
                specification_id=specification.id,
                batch_code="PAPER-PARITY",
                quantity=8,
                unit_price="12.35",
                location="P-01",
                operator_name="电脑端",
                remark="一致性验收",
            )
            db.commit()

            mobile_groups = mobile_paper_inventory(db=db)
            self.assertEqual(mobile_groups[0]["quantity"], 8)

            outbound = mobile_paper_outbound(
                PaperOutboundPayload(
                    client_request_id="parity-paper-out-001",
                    specification_id=specification.id,
                    quantity=3,
                    location="",
                    customer_name="二车间",
                    operator_name="手机端",
                    remark="一致性验收",
                ),
                db=db,
            )

            pc_groups = list_paper_inventory(db)
            self.assertEqual(pc_groups[0]["quantity"], 5)
            self.assertEqual(pc_groups[0]["batch_count"], 1)
            outbound_record = db.get(
                PaperInventoryTransaction,
                outbound["allocations"][0]["transaction_id"],
            )
            self.assertEqual(outbound_record.operator_name, "张三")

            reversal = reverse_mobile_paper_transaction(
                outbound["allocations"][0]["transaction_id"],
                PaperReversePayload(
                    client_request_id="parity-paper-reverse-001",
                    operator_name="手机端",
                    remark="撤销一致性验收",
                ),
                db=db,
            )
            self.assertEqual(list_paper_inventory(db)[0]["quantity"], 8)
            reversal_record = db.get(PaperInventoryTransaction, reversal["id"])
            self.assertEqual(reversal_record.operator_name, "张三")

    def test_pc_and_mobile_plan_matching_return_the_same_result(self) -> None:
        with self.Session() as db:
            drawing = ProductDrawing(
                product_code="TNX-PARITY",
                product_name="计划一致性",
                dxf_file_url="/tmp/plan-parity.dxf",
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
                    material_code="RAW-PLAN-PARITY",
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

            pc_result = match_plan_materials(
                db, drawing_id=drawing.id, quantity=10
            )
            mobile_result = mobile_plan_match(
                drawing_id=drawing.id, quantity=10, db=db
            )

            self.assertEqual(mobile_result, pc_result)


if __name__ == "__main__":
    unittest.main()
