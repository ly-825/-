from datetime import datetime
from decimal import Decimal
from typing import Callable

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.operator import verified_operator_name
from app.database import get_db
from app.models import PaperInventoryBatch, PaperSpecification
from app.services.inventory_service import inventory_write_lock
from app.services.mobile_idempotency import remember_mobile_response, replayed_mobile_response
from app.services.paper_inventory import (
    PAPER_UNITS,
    create_paper_specification,
    inbound_paper,
    list_paper_batches,
    list_paper_inventory,
    list_paper_specifications,
    list_paper_transactions,
    outbound_paper_fifo,
    paper_batch_size,
    reverse_paper_transaction,
    toggle_paper_specification,
    update_paper_specification,
)

router = APIRouter(tags=["mobile-paper"])


class WritePayload(BaseModel):
    client_request_id: str


class PaperSpecificationPayload(WritePayload):
    paper_type: str
    model: str = ""
    material_name: str
    thickness: float
    inner_diameter: float | None = None
    outer_diameter: float | None = None
    length: float | None = None
    width: float | None = None
    remark: str = ""


class PaperSpecificationUpdatePayload(PaperSpecificationPayload):
    is_active: int = 1


class PaperTogglePayload(WritePayload):
    pass


class PaperInboundPayload(WritePayload):
    specification_id: int
    batch_code: str = ""
    quantity: int
    unit_price: str
    location: str = ""
    operator_name: str = ""
    remark: str = ""


class PaperOutboundPayload(WritePayload):
    specification_id: int
    quantity: int
    location: str = ""
    customer_name: str = ""
    operator_name: str = ""
    remark: str = ""


class PaperReversePayload(WritePayload):
    operator_name: str = ""
    remark: str = ""


def _time(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _spec(spec: PaperSpecification) -> dict[str, object]:
    return {
        "id": spec.id, "paper_type": spec.paper_type, "model": spec.model,
        "material_name": spec.material_name, "thickness": spec.thickness,
        "inner_diameter": spec.inner_diameter, "outer_diameter": spec.outer_diameter,
        "length": spec.length, "width": spec.width, "remark": spec.remark,
        "is_active": spec.is_active, "unit": PAPER_UNITS[spec.paper_type],
        "created_at": _time(spec.created_at), "updated_at": _time(spec.updated_at),
    }


def _batch(batch: PaperInventoryBatch) -> dict[str, object]:
    return {
        "id": batch.id, "specification_id": batch.specification_id,
        "batch_code": batch.batch_code, "paper_type": batch.paper_type,
        "model": batch.model, "material_name": batch.material_name,
        "thickness": batch.thickness, "inner_diameter": batch.inner_diameter,
        "outer_diameter": batch.outer_diameter, "length": batch.length,
        "width": batch.width, "size_text": paper_batch_size(batch),
        "unit": PAPER_UNITS[batch.paper_type], "quantity": batch.quantity,
        "unit_price": f"{batch.unit_price:.2f}", "location": batch.location,
        "status": batch.status, "created_at": _time(batch.created_at),
        "updated_at": _time(batch.updated_at),
    }


def _json(value):
    if isinstance(value, set): return sorted(value)
    if isinstance(value, Decimal): return f"{value:.2f}"
    if isinstance(value, datetime): return value.isoformat()
    return value


def _write(db: Session, operation: str, payload: WritePayload, action: Callable[[], dict], path: dict | None = None) -> dict:
    request = {
        **(path or {}),
        **payload.model_dump(
            mode="json", exclude={"client_request_id", "operator_name"}
        ),
    }
    replay = replayed_mobile_response(db, operation, payload.client_request_id, request)
    if replay is not None: return replay
    response = action()
    remember_mobile_response(db, operation, payload.client_request_id, request, response)
    db.commit()
    return response


@router.get("/paper-specifications")
def mobile_paper_specifications(q: str = "", paper_type: str = "", material_name: str = "", db: Session = Depends(get_db)):
    return [_spec(item) for item in list_paper_specifications(db, q=q, paper_type=paper_type, material_name=material_name)]


@router.post("/paper-specifications")
def create_mobile_paper_specification(payload: PaperSpecificationPayload, db: Session = Depends(get_db)):
    with inventory_write_lock():
        return _write(db, "paper_specification_create", payload, lambda: _spec(create_paper_specification(db, **payload.model_dump(exclude={"client_request_id"}))))


@router.put("/paper-specifications/{specification_id}")
def update_mobile_paper_specification(specification_id: int, payload: PaperSpecificationUpdatePayload, db: Session = Depends(get_db)):
    with inventory_write_lock():
        return _write(db, "paper_specification_update", payload, lambda: _spec(update_paper_specification(db, specification_id, **payload.model_dump(exclude={"client_request_id"}))), {"specification_id": specification_id})


@router.post("/paper-specifications/{specification_id}/toggle")
def toggle_mobile_paper_specification(specification_id: int, payload: PaperTogglePayload, db: Session = Depends(get_db)):
    with inventory_write_lock():
        return _write(db, "paper_specification_toggle", payload, lambda: _spec(toggle_paper_specification(db, specification_id)), {"specification_id": specification_id})


@router.get("/paper-materials")
def mobile_paper_inventory(q: str = "", paper_type: str = "", material_name: str = "", location: str = "", db: Session = Depends(get_db)):
    return [{key: _json(value) for key, value in group.items()} for group in list_paper_inventory(db, q=q, paper_type=paper_type, material_name=material_name, location=location)]


@router.get("/paper-materials/{specification_id}/batches")
def mobile_paper_batches(specification_id: int, q: str = "", location: str = "", include_zero: bool = False, db: Session = Depends(get_db)):
    return [_batch(item) for item in list_paper_batches(db, specification_id, q=q, location=location, include_zero=include_zero)]


@router.post("/paper-materials/inbound")
def mobile_paper_inbound(payload: PaperInboundPayload, db: Session = Depends(get_db)):
    service_payload = payload.model_dump(exclude={"client_request_id", "operator_name"})
    service_payload["operator_name"] = verified_operator_name()

    def action():
        result = inbound_paper(db, **service_payload)
        return {"batch": _batch(result["batch"]), "transaction_id": result["transaction"].id}
    with inventory_write_lock(): return _write(db, "paper_inbound", payload, action)


@router.post("/paper-materials/outbound")
def mobile_paper_outbound(payload: PaperOutboundPayload, db: Session = Depends(get_db)):
    operator_name = verified_operator_name()

    def action():
        records = outbound_paper_fifo(payload.specification_id, payload.quantity, payload.location, payload.customer_name, operator_name, payload.remark, db)
        return {"message": "纸材出库成功", "quantity": payload.quantity, "allocations": [{"transaction_id": record.id, "inventory_id": record.inventory_id, "quantity": record.quantity, "before_quantity": record.before_quantity, "after_quantity": record.after_quantity} for record in records]}
    with inventory_write_lock(): return _write(db, "paper_outbound", payload, action)


@router.get("/paper-materials/transactions")
def mobile_paper_transactions(q: str = "", paper_type: str = "", transaction_type: str = "", db: Session = Depends(get_db)):
    return [{"id": row["record"].id, "transaction_type": row["record"].transaction_type, "quantity": row["record"].quantity, "before_quantity": row["record"].before_quantity, "after_quantity": row["record"].after_quantity, "reversed_transaction_id": row["record"].reversed_transaction_id, "operator_name": row["record"].operator_name, "customer_name": row["record"].customer_name, "remark": row["record"].remark, "created_at": _time(row["record"].created_at), "batch": _batch(row["batch"])} for row in list_paper_transactions(db, q=q, paper_type=paper_type, transaction_type=transaction_type)]


@router.post("/paper-materials/transactions/{transaction_id}/reverse")
def reverse_mobile_paper_transaction(transaction_id: int, payload: PaperReversePayload, db: Session = Depends(get_db)):
    operator_name = verified_operator_name()

    def action():
        record = reverse_paper_transaction(transaction_id, operator_name, payload.remark, db)
        return {"id": record.id, "inventory_id": record.inventory_id, "transaction_type": record.transaction_type, "quantity": record.quantity, "before_quantity": record.before_quantity, "after_quantity": record.after_quantity, "reversed_transaction_id": record.reversed_transaction_id}
    with inventory_write_lock(): return _write(db, "paper_transaction_reverse", payload, action, {"transaction_id": transaction_id})
