from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import PaperInventoryBatch, PaperInventoryTransaction, PaperSpecification
from app.services.drawing_search import natural_sort_key
from app.services.material_formats import paper_roll_size, paper_sheet_model


PAPER_TYPES = {"roll", "sheet"}
PAPER_TYPE_LABELS = {"roll": "纸圈", "sheet": "纸张"}
PAPER_UNITS = {"roll": "圈", "sheet": "张"}


def _require_positive(value: float | None, label: str) -> float:
    if value is None or value <= 0:
        raise HTTPException(status_code=400, detail=f"{label}必须大于0")
    return float(value)


def normalize_paper_specification(
    paper_type: str,
    model: str,
    material_name: str,
    thickness: float,
    inner_diameter: float | None,
    outer_diameter: float | None,
    length: float | None,
    width: float | None,
) -> dict[str, object]:
    type_value = paper_type.strip().lower()
    if type_value not in PAPER_TYPES:
        raise HTTPException(status_code=400, detail="纸材类型无效")
    material_value = material_name.strip()
    if not material_value:
        raise HTTPException(status_code=400, detail="纸材名称/材质不能为空")
    thickness_value = _require_positive(thickness, "厚度")
    if type_value == "roll":
        model_value = model.strip()
        if not model_value:
            raise HTTPException(status_code=400, detail="纸圈型号不能为空")
        inner_value = _require_positive(inner_diameter, "内径")
        outer_value = _require_positive(outer_diameter, "外径")
        if outer_value <= inner_value:
            raise HTTPException(status_code=400, detail="纸圈外径必须大于内径")
        return {
            "paper_type": type_value,
            "model": model_value,
            "material_name": material_value,
            "thickness": thickness_value,
            "inner_diameter": inner_value,
            "outer_diameter": outer_value,
            "length": None,
            "width": None,
        }
    length_value = _require_positive(length, "长度")
    width_value = _require_positive(width, "宽度")
    return {
        "paper_type": type_value,
        "model": paper_sheet_model(thickness_value, length_value, width_value),
        "material_name": material_value,
        "thickness": thickness_value,
        "inner_diameter": None,
        "outer_diameter": None,
        "length": length_value,
        "width": width_value,
    }


def paper_specification_sort_key(spec: PaperSpecification) -> tuple:
    if spec.paper_type == "roll":
        dimensions = (spec.inner_diameter or 0, spec.outer_diameter or 0)
    else:
        dimensions = (spec.length or 0, spec.width or 0)
    return (
        spec.thickness,
        0 if spec.paper_type == "roll" else 1,
        *dimensions,
        natural_sort_key(spec.model),
        natural_sort_key(spec.material_name),
    )


def paper_batch_size(batch: PaperInventoryBatch) -> str:
    if batch.paper_type == "roll":
        return paper_roll_size(batch.thickness, batch.inner_diameter, batch.outer_diameter)
    return paper_sheet_model(batch.thickness, batch.length, batch.width)


def paper_inventory_groups(batches: list[PaperInventoryBatch]) -> list[dict[str, Any]]:
    grouped: dict[tuple, dict[str, Any]] = {}
    for batch in batches:
        if batch.quantity <= 0:
            continue
        key = (
            batch.specification_id,
            batch.paper_type,
            batch.model,
            batch.material_name,
            batch.thickness,
            batch.inner_diameter,
            batch.outer_diameter,
            batch.length,
            batch.width,
        )
        group = grouped.setdefault(
            key,
            {
                "specification_id": batch.specification_id,
                "paper_type": batch.paper_type,
                "type_label": PAPER_TYPE_LABELS[batch.paper_type],
                "model": batch.model,
                "material_name": batch.material_name,
                "thickness": batch.thickness,
                "inner_diameter": batch.inner_diameter,
                "outer_diameter": batch.outer_diameter,
                "length": batch.length,
                "width": batch.width,
                "size": paper_batch_size(batch),
                "unit": PAPER_UNITS[batch.paper_type],
                "quantity": 0,
                "batch_count": 0,
                "locations": set(),
                "batch_ids": set(),
                "price_min": batch.unit_price,
                "price_max": batch.unit_price,
                "latest": batch.updated_at or batch.created_at,
            },
        )
        group["quantity"] += batch.quantity
        group["batch_count"] += 1
        group["batch_ids"].add(batch.id)
        if batch.location:
            group["locations"].add(batch.location)
        group["price_min"] = min(group["price_min"], batch.unit_price)
        group["price_max"] = max(group["price_max"], batch.unit_price)
        batch_time = batch.updated_at or batch.created_at
        if batch_time and (not group["latest"] or batch_time > group["latest"]):
            group["latest"] = batch_time
    return sorted(
        grouped.values(),
        key=lambda group: (
            group["thickness"],
            0 if group["paper_type"] == "roll" else 1,
            group["inner_diameter"] or group["length"] or 0,
            group["outer_diameter"] or group["width"] or 0,
            natural_sort_key(group["model"]),
            natural_sort_key(group["material_name"]),
        ),
    )


def outbound_paper_fifo(
    specification_id: int,
    quantity: int,
    location: str | None,
    customer_name: str | None,
    operator_name: str | None,
    remark: str | None,
    db: Session,
) -> list[PaperInventoryTransaction]:
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="出库数量必须大于0")
    spec = db.get(PaperSpecification, specification_id)
    if not spec:
        raise HTTPException(status_code=404, detail="纸材规格不存在")
    query = db.query(PaperInventoryBatch).filter(
        PaperInventoryBatch.specification_id == specification_id,
        PaperInventoryBatch.quantity > 0,
    )
    location_value = (location or "").strip()
    if location_value:
        query = query.filter(PaperInventoryBatch.location == location_value)
    batches = query.order_by(PaperInventoryBatch.created_at.asc(), PaperInventoryBatch.id.asc()).all()
    available = sum(batch.quantity for batch in batches)
    if available < quantity:
        raise HTTPException(
            status_code=400,
            detail=f"纸材库存不足，当前可出库 {available} {PAPER_UNITS[spec.paper_type]}",
        )
    remaining = quantity
    records: list[PaperInventoryTransaction] = []
    for batch in batches:
        if remaining <= 0:
            break
        outbound_quantity = min(batch.quantity, remaining)
        before_quantity = batch.quantity
        batch.quantity -= outbound_quantity
        batch.status = "used" if batch.quantity <= 0 else "available"
        record = PaperInventoryTransaction(
            inventory_id=batch.id,
            transaction_type="out",
            quantity=outbound_quantity,
            before_quantity=before_quantity,
            after_quantity=batch.quantity,
            operator_name=(operator_name or "").strip() or None,
            customer_name=(customer_name or "").strip() or None,
            remark=(remark or "").strip() or "纸材出库",
        )
        db.add(record)
        records.append(record)
        remaining -= outbound_quantity
    db.flush()
    return records


def reverse_paper_transaction(
    transaction_id: int,
    operator_name: str | None,
    remark: str | None,
    db: Session,
) -> PaperInventoryTransaction:
    record = db.get(PaperInventoryTransaction, transaction_id)
    if not record:
        raise HTTPException(status_code=404, detail="纸材流水不存在")
    if record.transaction_type not in ("in", "out"):
        raise HTTPException(status_code=400, detail="该纸材流水类型不支持撤回")
    existing = db.query(PaperInventoryTransaction).filter(
        PaperInventoryTransaction.reversed_transaction_id == record.id
    ).first()
    if record.reversed_transaction_id is not None or existing:
        raise HTTPException(status_code=400, detail="该纸材流水已撤回，不能重复撤回")
    batch = db.get(PaperInventoryBatch, record.inventory_id)
    if not batch:
        raise HTTPException(status_code=404, detail="纸材库存批次不存在")
    reverse_type = "out" if record.transaction_type == "in" else "in"
    before_quantity = batch.quantity
    if reverse_type == "out":
        if before_quantity < record.quantity:
            raise HTTPException(status_code=400, detail="当前纸材库存不足，不能撤回该入库流水")
        after_quantity = before_quantity - record.quantity
    else:
        after_quantity = before_quantity + record.quantity
    batch.quantity = after_quantity
    batch.status = "used" if after_quantity <= 0 else "available"
    reversal = PaperInventoryTransaction(
        inventory_id=batch.id,
        transaction_type=reverse_type,
        quantity=record.quantity,
        before_quantity=before_quantity,
        after_quantity=after_quantity,
        reversed_transaction_id=record.id,
        operator_name=(operator_name or "").strip() or None,
        customer_name=record.customer_name,
        remark=(remark or "").strip() or "撤回纸材流水",
    )
    db.add(reversal)
    db.flush()
    record.reversed_transaction_id = reversal.id
    return reversal


def paper_inventory_snapshot(batch: PaperInventoryBatch | None) -> dict[str, Any] | None:
    if not batch:
        return None
    return {
        "id": batch.id,
        "specification_id": batch.specification_id,
        "batch_code": batch.batch_code,
        "paper_type": batch.paper_type,
        "model": batch.model,
        "material_name": batch.material_name,
        "size": paper_batch_size(batch),
        "quantity": batch.quantity,
        "unit_price": f"{batch.unit_price:.2f}",
        "location": batch.location,
        "status": batch.status,
    }
