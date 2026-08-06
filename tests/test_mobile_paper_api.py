import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import MobileRequestRecord, PaperInventoryBatch, PaperSpecification
from app.routers.mobile_paper import (
    PaperInboundPayload,
    PaperSpecificationPayload,
    create_mobile_paper_specification,
    mobile_paper_inbound,
)


class MobilePaperApiTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def test_specification_and_inbound_are_idempotent(self) -> None:
        with self.Session() as db:
            spec_payload = PaperSpecificationPayload(
                client_request_id="paper-spec-001",
                paper_type="roll",
                model="3969.01",
                material_name="黑纸",
                thickness=0.5,
                inner_diameter=55,
                outer_diameter=115,
            )
            spec = create_mobile_paper_specification(spec_payload, db=db)
            self.assertEqual(create_mobile_paper_specification(spec_payload, db=db), spec)
            self.assertEqual(db.query(PaperSpecification).count(), 1)

            payload = PaperInboundPayload(
                client_request_id="paper-in-001",
                specification_id=spec["id"],
                batch_code="PAPER-001",
                quantity=8,
                unit_price="12.345",
                location="P-01",
            )
            first = mobile_paper_inbound(payload, db=db)
            self.assertEqual(mobile_paper_inbound(payload, db=db), first)
            self.assertEqual(first["batch"]["unit_price"], "12.35")
            self.assertEqual(first["batch"]["unit"], "圈")
            self.assertEqual(db.query(PaperInventoryBatch).count(), 1)
            self.assertEqual(db.query(MobileRequestRecord).count(), 2)

            with self.assertRaises(HTTPException) as changed:
                mobile_paper_inbound(payload.model_copy(update={"quantity": 9}), db=db)
            self.assertEqual(changed.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
