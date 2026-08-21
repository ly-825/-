from __future__ import annotations

from datetime import datetime
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.operator import verified_operator_name
from app.database import get_db
from app.models import MaterialInventory, RawPlateSpecification
from app.services.inventory_service import inventory_write_lock
from app.services.mobile_idempotency import remember_mobile_response, replayed_mobile_response
from app.services.raw_plate_inventory import (
    create_raw_plate_specification,
    inbound_raw_plate,
    list_raw_plate_batches,
    list_raw_plate_groups,
    list_raw_plate_specifications,
    list_raw_plate_transactions,
    outbound_raw_plate_fifo,
    reverse_raw_plate_transaction,
    toggle_raw_plate_specification,
    update_raw_plate_batch,
    update_raw_plate_specification,
)


router = APIRouter(tags=["mobile-raw-plates"])


class MobileWritePayload(BaseModel):
    client_request_id: str


class RawPlateSpecificationPayload(MobileWritePayload):
    material: str
    length: float
    width: float
    thickness: float
    density: float = 7.85
    remark: str = ""


class RawPlateSpecificationUpdatePayload(RawPlateSpecificationPayload):
    is_active: int = 1


class RawPlateTogglePayload(MobileWritePayload):
    pass


class RawPlateBatchUpdatePayload(MobileWritePayload):
    raw_plate_model: str
    material_code: str = ""
    material: str = ""
    length: float | None = None
    width: float | None = None
    thickness: float | None = None
    location: str = ""
    status: str = "available"
    operator_name: str = ""
    remark: str = ""


class RawPlateInboundPayload(MobileWritePayload):
    specification_id: int | None = None
    raw_plate_model: str = ""
    material_code: str = ""
    material: str = ""
    total_weight_ton: float
    length: float | None = None
    width: float | None = None
    thickness: float | None = None
    density: float | None = 7.85
    location: str = ""
    operator_name: str = ""
    remark: str = ""


class RawPlateOutboundPayload(MobileWritePayload):
    material: str
    length: float
    width: float
    thickness: float
    quantity: int
    location: str = ""
    customer_name: str = ""
    operator_name: str = ""
    remark: str = ""


class RawPlateReversePayload(MobileWritePayload):
    operator_name: str = ""
    remark: str = ""


def _time(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _spec_data(spec: RawPlateSpecification) -> dict[str, object]:
    return {
        "id": spec.id,
        "spec_name": spec.spec_name,
        "material": spec.material,
        "length": spec.length,
        "width": spec.width,
        "thickness": spec.thickness,
        "density": spec.density,
        "remark": spec.remark,
        "is_active": spec.is_active,
        "created_at": _time(spec.created_at),
        "updated_at": _time(spec.updated_at),
    }


def _batch_data(item: MaterialInventory) -> dict[str, object]:
    return {
        "id": item.id,
        "material_code": item.material_code,
        "raw_plate_model": item.raw_plate_model,
        "material": item.material,
        "length": item.length,
        "width": item.width,
        "thickness": item.thickness,
        "quantity": item.quantity,
        "location": item.location,
        "status": item.status,
        "created_at": _time(item.created_at),
        "updated_at": _time(item.updated_at),
    }


def _json_value(value):
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _group_data(group: dict) -> dict[str, object]:
    return {key: _json_value(value) for key, value in group.items()}


def _run_write(
    db: Session,
    operation_type: str,
    payload: MobileWritePayload,
    action: Callable[[], dict[str, object]],
    *,
    path_data: dict[str, object] | None = None,
) -> dict[str, object]:
    request_payload = {
        **(path_data or {}),
        **payload.model_dump(
            mode="json", exclude={"client_request_id", "operator_name"}
        ),
    }
    replay = replayed_mobile_response(
        db, operation_type, payload.client_request_id, request_payload
    )
    if replay is not None:
        return replay
    response = action()
    remember_mobile_response(
        db,
        operation_type,
        payload.client_request_id,
        request_payload,
        response,
    )
    db.commit()
    return response


@router.get("/raw-plate-specifications")
def mobile_raw_plate_specifications(
    q: str = "",
    material: str = "",
    length: str = "",
    width: str = "",
    thickness: str = "",
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return [
        _spec_data(spec)
        for spec in list_raw_plate_specifications(
            db, q=q, material=material, length=length, width=width, thickness=thickness
        )
    ]


@router.post("/raw-plate-specifications")
def create_mobile_raw_plate_specification(
    payload: RawPlateSpecificationPayload, db: Session = Depends(get_db)
) -> dict[str, object]:
    with inventory_write_lock():
        return _run_write(
            db,
            "raw_plate_specification_create",
            payload,
            lambda: _spec_data(
                create_raw_plate_specification(
                    db, **payload.model_dump(exclude={"client_request_id"})
                )
            ),
        )


@router.put("/raw-plate-specifications/{specification_id}")
def update_mobile_raw_plate_specification(
    specification_id: int,
    payload: RawPlateSpecificationUpdatePayload,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with inventory_write_lock():
        return _run_write(
            db,
            "raw_plate_specification_update",
            payload,
            lambda: _spec_data(
                update_raw_plate_specification(
                    db,
                    specification_id,
                    **payload.model_dump(exclude={"client_request_id"}),
                )
            ),
            path_data={"specification_id": specification_id},
        )


@router.post("/raw-plate-specifications/{specification_id}/toggle")
def toggle_mobile_raw_plate_specification(
    specification_id: int,
    payload: RawPlateTogglePayload,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with inventory_write_lock():
        return _run_write(
            db,
            "raw_plate_specification_toggle",
            payload,
            lambda: _spec_data(toggle_raw_plate_specification(db, specification_id)),
            path_data={"specification_id": specification_id},
        )


@router.get("/raw-plates")
def mobile_raw_plates(
    q: str = "",
    material: str = "",
    length: str = "",
    width: str = "",
    thickness: str = "",
    location: str = "",
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return [
        _group_data(group)
        for group in list_raw_plate_groups(
            db,
            q=q,
            material=material,
            length=length,
            width=width,
            thickness=thickness,
            location=location,
        )
    ]


@router.get("/raw-plates/{batch_id}")
def mobile_raw_plate_batch(
    batch_id: int, db: Session = Depends(get_db)
) -> dict[str, object]:
    item = db.get(MaterialInventory, batch_id)
    if not item or item.inventory_type != "raw_plate":
        raise HTTPException(status_code=404, detail="板料库存不存在")
    return _batch_data(item)


@router.put("/raw-plates/{batch_id}")
def update_mobile_raw_plate_batch(
    batch_id: int,
    payload: RawPlateBatchUpdatePayload,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    service_payload = payload.model_dump(
        exclude={"client_request_id", "operator_name"}
    )
    service_payload["operator_name"] = verified_operator_name()
    with inventory_write_lock():
        return _run_write(
            db,
            "raw_plate_batch_update",
            payload,
            lambda: _batch_data(
                update_raw_plate_batch(
                    db, batch_id, **service_payload
                )
            ),
            path_data={"batch_id": batch_id},
        )


@router.post("/raw-plates/inbound")
def mobile_raw_plate_inbound(
    payload: RawPlateInboundPayload, db: Session = Depends(get_db)
) -> dict[str, object]:
    service_payload = payload.model_dump(
        exclude={"client_request_id", "operator_name"}
    )
    service_payload["operator_name"] = verified_operator_name()

    def action() -> dict[str, object]:
        result = inbound_raw_plate(db, **service_payload)
        return {
            "item": _batch_data(result["item"]),
            "transaction_id": result["transaction"].id,
            "single_weight_kg": result["single_weight_kg"],
            "total_weight_kg": result["total_weight_kg"],
            "quantity": result["quantity"],
            "remaining_weight_kg": result["remaining_weight_kg"],
        }

    with inventory_write_lock():
        return _run_write(db, "raw_plate_inbound", payload, action)


@router.post("/raw-plates/outbound")
def mobile_raw_plate_outbound(
    payload: RawPlateOutboundPayload, db: Session = Depends(get_db)
) -> dict[str, object]:
    service_payload = payload.model_dump(
        exclude={"client_request_id", "operator_name"}
    )
    service_payload["operator_name"] = verified_operator_name()
    with inventory_write_lock():
        return _run_write(
            db,
            "raw_plate_outbound",
            payload,
            lambda: outbound_raw_plate_fifo(
                db, **service_payload
            ),
        )


@router.get("/raw-plates/transactions")
def mobile_raw_plate_transactions(
    q: str = "",
    material: str = "",
    transaction_type: str = "",
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    rows = list_raw_plate_transactions(
        db, q=q, material=material, transaction_type=transaction_type
    )
    return [
        {
            **{key: _json_value(value) for key, value in row.items() if key != "batch"},
            "batch": _batch_data(row["batch"]),
        }
        for row in rows
    ]


@router.post("/raw-plates/transactions/{transaction_id}/reverse")
def reverse_mobile_raw_plate_transaction(
    transaction_id: int,
    payload: RawPlateReversePayload,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    operator_name = verified_operator_name()

    def action() -> dict[str, object]:
        record = reverse_raw_plate_transaction(
            db,
            transaction_id,
            operator_name=operator_name,
            remark=payload.remark,
        )
        return {
            "id": record.id,
            "inventory_id": record.inventory_id,
            "transaction_type": record.transaction_type,
            "quantity": record.quantity,
            "before_quantity": record.before_quantity,
            "after_quantity": record.after_quantity,
            "reversed_transaction_id": record.reversed_transaction_id,
        }

    with inventory_write_lock():
        return _run_write(
            db,
            "raw_plate_transaction_reverse",
            payload,
            action,
            path_data={"transaction_id": transaction_id},
        )


# FastAPI matches routes in registration order. Keep fixed paths such as
# /raw-plates/inbound ahead of the numeric batch path.
router.routes.sort(key=lambda route: "{" in route.path)
