from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import InventoryTransactionRecord, MaterialInventory, ProductDrawing
from app.schemas import DrawingConfirm, DrawingOut, DrawingUploadOut
from app.services.drawing_parameters import common_normal_value_from_text, first_int_value, normalize_tooth_type, plain_float_value
from app.services.drawing_upload import delete_uploaded_drawing, save_uploaded_drawing
from app.services.drawing_version import apply_drawing_version
from app.services.inventory_service import ensure_drawing_can_be_changed, inventory_write_lock, product_inbound_from_drawing, reverse_inventory_transaction, sync_product_inventory_from_drawing
from app.services.operation_log import drawing_snapshot, inventory_snapshot, record_operation_log
from app.services.product_outbound_analysis import normalize_outbound_purpose
from app.services.scrap_service import find_scrap_batches_for_outbound


router = APIRouter()


class ProductInboundPayload(BaseModel):
    drawing_id: int
    quantity: int = 1
    location: str | None = None
    paper_material: str | None = None
    operator_name: str | None = None
    client_request_id: str | None = None


class ProductOutboundPayload(BaseModel):
    drawing_id: int
    quantity: int
    location: str | None = None
    operator_name: str | None = None
    customer_name: str | None = None
    outbound_purpose: str | None = "sales"
    remark: str | None = None
    client_request_id: str | None = None


class TransactionReversePayload(BaseModel):
    operator_name: str | None = None
    remark: str | None = None


class ScrapConfirmPayload(BaseModel):
    actual_quantity: int
    actual_diameter: float | None = None
    location: str
    operator_name: str | None = None


class ScrapOutboundPayload(BaseModel):
    scrap_group_key: str
    quantity: int
    operator_name: str | None = None
    customer_name: str | None = None
    remark: str | None = None
    client_request_id: str | None = None


class MobileSummaryOut(BaseModel):
    pending_drawing_count: int
    confirmed_drawing_count: int
    product_available_quantity: int
    pending_scrap_count: int
    scrap_available_quantity: int


class ProductInventoryGroupOut(BaseModel):
    product_code: str
    material: str | None
    thickness: float | None
    quantity: int
    locations: list[str]
    paper_materials: list[str] = []
    latest: str | None


class ScrapInventoryGroupOut(BaseModel):
    group_key: str
    material: str
    thickness: float
    usable_size: str
    location: str
    quantity: int
    diameter: float | None = None


class TransactionOut(BaseModel):
    id: int
    inventory_id: int
    inventory_type: str
    code: str | None
    material: str | None
    thickness: float | None
    usable_size: str | None
    location: str | None
    transaction_type: str
    quantity: int
    before_quantity: int
    after_quantity: int
    reversed_transaction_id: int | None = None
    operator_name: str | None
    customer_name: str | None = None
    outbound_purpose: str | None = None
    remark: str | None
    created_at: str


class InventoryItemOut(BaseModel):
    id: int
    material_code: str | None
    inventory_type: str
    material: str
    thickness: float
    shape: str
    diameter: float | None
    length: float | None
    width: float | None
    usable_size: str | None
    quantity: int
    location: str | None
    paper_material: str | None = None
    status: str
    source_product_code: str | None

    model_config = ConfigDict(from_attributes=True)


def _idempotency_key(scope: str, client_request_id: str | None) -> str | None:
    if not client_request_id:
        return None
    value = client_request_id.strip()
    if not value:
        return None
    return f"{scope}:{value}"


def _optional_float(value: str | float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _optional_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _float_between_filter(column, value: float, tolerance: float = 0.001):
    return column.between(value - tolerance, value + tolerance)


def _apply_drawing_filters(
    query,
    q: str = "",
    product_category: str = "",
    material: str = "",
    thickness: str = "",
    product_thickness: str = "",
    plate_thickness: str = "",
    outer_diameter: str = "",
    inner_diameter: str = "",
    teeth_count: str = "",
    module: str = "",
    pressure_angle: str = "",
    common_normal_length: str = "",
    pin_diameter: str = "",
    pin_span: str = "",
):
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        keyword_filter = (
            (ProductDrawing.product_code.ilike(like))
            | (ProductDrawing.product_name.ilike(like))
            | (ProductDrawing.product_category.ilike(like))
            | (ProductDrawing.remark.ilike(like))
            | (ProductDrawing.material.ilike(like))
            | (ProductDrawing.tooth_type.ilike(like))
            | (ProductDrawing.teeth_count_text.ilike(like))
            | (ProductDrawing.module_text.ilike(like))
            | (ProductDrawing.common_normal_length_text.ilike(like))
        )
        query = query.filter(keyword_filter)
    if product_category.strip():
        query = query.filter(ProductDrawing.product_category.ilike(f"%{product_category.strip()}%"))
    if material.strip():
        query = query.filter(ProductDrawing.material.ilike(f"%{material.strip()}%"))
    thickness_value = _optional_float(thickness)
    if thickness_value is not None:
        query = query.filter(
            _float_between_filter(ProductDrawing.thickness, thickness_value)
            | _float_between_filter(ProductDrawing.product_thickness, thickness_value)
            | _float_between_filter(ProductDrawing.plate_thickness, thickness_value)
        )
    product_thickness_value = _optional_float(product_thickness)
    if product_thickness_value is not None:
        query = query.filter(_float_between_filter(ProductDrawing.product_thickness, product_thickness_value))
    plate_thickness_value = _optional_float(plate_thickness)
    if plate_thickness_value is not None:
        query = query.filter(_float_between_filter(ProductDrawing.plate_thickness, plate_thickness_value))
    outer_value = _optional_float(outer_diameter)
    if outer_value is not None:
        query = query.filter(_float_between_filter(ProductDrawing.max_outer_diameter, outer_value))
    inner_value = _optional_float(inner_diameter)
    if inner_value is not None:
        query = query.filter(_float_between_filter(ProductDrawing.min_inner_diameter, inner_value))
    teeth_text = teeth_count.strip()
    if teeth_text:
        teeth_value = _optional_int(teeth_text)
        like = f"%{teeth_text}%"
        if teeth_value is not None:
            query = query.filter((ProductDrawing.teeth_count == teeth_value) | ProductDrawing.teeth_count_text.ilike(like))
        else:
            query = query.filter(ProductDrawing.teeth_count_text.ilike(like) | ProductDrawing.tooth_type.ilike(like))
    module_text = module.strip()
    if module_text:
        module_value = _optional_float(module_text)
        like = f"%{module_text}%"
        if module_value is not None:
            query = query.filter(_float_between_filter(ProductDrawing.module, module_value) | ProductDrawing.module_text.ilike(like))
        else:
            query = query.filter(ProductDrawing.module_text.ilike(like))
    pressure_angle_value = _optional_float(pressure_angle)
    if pressure_angle_value is not None:
        query = query.filter(_float_between_filter(ProductDrawing.pressure_angle, pressure_angle_value))
    common_normal_length_text = common_normal_length.strip()
    if common_normal_length_text:
        common_normal_length_value = _optional_float(common_normal_length_text)
        like = f"%{common_normal_length_text}%"
        if common_normal_length_value is not None:
            query = query.filter(_float_between_filter(ProductDrawing.common_normal_length, common_normal_length_value) | ProductDrawing.common_normal_length_text.ilike(like))
        else:
            query = query.filter(ProductDrawing.common_normal_length_text.ilike(like))
    pin_diameter_value = _optional_float(pin_diameter)
    if pin_diameter_value is not None:
        query = query.filter(_float_between_filter(ProductDrawing.pin_diameter, pin_diameter_value))
    pin_span_value = _optional_float(pin_span)
    if pin_span_value is not None:
        query = query.filter(_float_between_filter(ProductDrawing.pin_span, pin_span_value))
    return query


def _scrap_location_label(item: MaterialInventory | None) -> str:
    if not item:
        return "-"
    if item.status == "available" and item.location in ("待入库", "未入库"):
        return "未设置库位"
    return item.location or "-"


def _group_product_inventory(items: list[MaterialInventory]) -> list[ProductInventoryGroupOut]:
    grouped: dict[str, dict] = {}
    for item in items:
        code = item.material_code or item.source_product_code or "未编号"
        if code not in grouped:
            grouped[code] = {
                "product_code": code,
                "material": item.material,
                "thickness": item.thickness,
                "quantity": 0,
                "locations": set(),
                "paper_materials": set(),
                "latest": item.updated_at or item.created_at,
            }
        grouped[code]["quantity"] += item.quantity
        if item.location:
            grouped[code]["locations"].add(item.location)
        if item.paper_material:
            grouped[code]["paper_materials"].add(item.paper_material)
        item_time = item.updated_at or item.created_at
        if item_time and item_time > grouped[code]["latest"]:
            grouped[code]["latest"] = item_time
    return [
        ProductInventoryGroupOut(
            product_code=value["product_code"],
            material=value["material"],
            thickness=value["thickness"],
            quantity=value["quantity"],
            locations=sorted(value["locations"]),
            paper_materials=sorted(value["paper_materials"]),
            latest=value["latest"].isoformat() if value["latest"] else None,
        )
        for value in grouped.values()
    ]


def _transaction_rows(records: list[InventoryTransactionRecord], inventory_type: str, db: Session) -> list[TransactionOut]:
    inventory_ids = [record.inventory_id for record in records]
    inventory_map = {
        item.id: item
        for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()
    } if inventory_ids else {}
    rows = []
    for record in records:
        item = inventory_map.get(record.inventory_id)
        if not item:
            continue
        if inventory_type == "product" and item.inventory_type == "scrap":
            continue
        if inventory_type == "scrap" and item.inventory_type != "scrap":
            continue
        rows.append(
            TransactionOut(
                id=record.id,
                inventory_id=record.inventory_id,
                inventory_type=item.inventory_type,
                code=item.material_code or item.source_product_code,
                material=item.material,
                thickness=item.thickness,
                usable_size=item.usable_size,
                location=_scrap_location_label(item) if item.inventory_type == "scrap" else item.location,
                transaction_type=record.transaction_type,
                quantity=record.quantity,
                before_quantity=record.before_quantity,
                after_quantity=record.after_quantity,
                reversed_transaction_id=record.reversed_transaction_id,
                operator_name=record.operator_name,
                customer_name=record.customer_name,
                outbound_purpose=record.outbound_purpose,
                remark=record.remark,
                created_at=record.created_at.isoformat(),
            )
        )
    return rows


@router.get("/summary", response_model=MobileSummaryOut)
def summary(db: Session = Depends(get_db)) -> MobileSummaryOut:
    product_available_quantity = sum(
        item.quantity for item in db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "product", MaterialInventory.quantity > 0).all()
    )
    scrap_available_quantity = sum(
        item.quantity for item in db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "scrap", MaterialInventory.status == "available", MaterialInventory.quantity > 0).all()
    )
    return MobileSummaryOut(
        pending_drawing_count=db.query(ProductDrawing).filter(ProductDrawing.confirmed == 0).count(),
        confirmed_drawing_count=db.query(ProductDrawing).filter(ProductDrawing.confirmed == 1, ProductDrawing.is_active == 1).count(),
        product_available_quantity=product_available_quantity,
        pending_scrap_count=db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "scrap", MaterialInventory.status == "pending").count(),
        scrap_available_quantity=scrap_available_quantity,
    )


@router.post("/drawings/upload", response_model=DrawingUploadOut)
def upload_drawing(file: UploadFile = File(...), db: Session = Depends(get_db)) -> DrawingUploadOut:
    drawing, duplicated = save_uploaded_drawing(file, db)
    record_operation_log(
        db,
        "drawing_upload",
        "drawing",
        drawing.id,
        None,
        "小程序重复图纸上传" if duplicated else "小程序上传图纸",
        after_data=drawing_snapshot(drawing),
    )
    db.commit()
    return DrawingUploadOut(drawing=drawing, duplicated=duplicated)


@router.get("/drawings", response_model=list[DrawingOut])
def drawings(
    status: str | None = None,
    q: str = "",
    product_category: str = "",
    material: str = "",
    thickness: str = "",
    product_thickness: str = "",
    plate_thickness: str = "",
    outer_diameter: str = "",
    inner_diameter: str = "",
    teeth_count: str = "",
    module: str = "",
    pressure_angle: str = "",
    common_normal_length: str = "",
    pin_diameter: str = "",
    pin_span: str = "",
    db: Session = Depends(get_db),
) -> list[ProductDrawing]:
    query = db.query(ProductDrawing)
    if status == "pending":
        query = query.filter(ProductDrawing.confirmed == 0)
    elif status == "confirmed":
        query = query.filter(ProductDrawing.confirmed == 1, ProductDrawing.is_active == 1)
    query = _apply_drawing_filters(
        query,
        q=q,
        product_category=product_category,
        material=material,
        thickness=thickness,
        product_thickness=product_thickness,
        plate_thickness=plate_thickness,
        outer_diameter=outer_diameter,
        inner_diameter=inner_diameter,
        teeth_count=teeth_count,
        module=module,
        pressure_angle=pressure_angle,
        common_normal_length=common_normal_length,
        pin_diameter=pin_diameter,
        pin_span=pin_span,
    )
    return query.order_by(ProductDrawing.created_at.desc()).all()


@router.get("/drawings/pending", response_model=list[DrawingOut])
def pending_drawings(db: Session = Depends(get_db)) -> list[ProductDrawing]:
    return db.query(ProductDrawing).filter(ProductDrawing.confirmed == 0).order_by(ProductDrawing.created_at.desc()).all()


@router.get("/drawings/confirmed", response_model=list[DrawingOut])
def confirmed_drawings(
    q: str = "",
    product_category: str = "",
    material: str = "",
    thickness: str = "",
    product_thickness: str = "",
    plate_thickness: str = "",
    outer_diameter: str = "",
    inner_diameter: str = "",
    teeth_count: str = "",
    module: str = "",
    pressure_angle: str = "",
    common_normal_length: str = "",
    pin_diameter: str = "",
    pin_span: str = "",
    db: Session = Depends(get_db),
) -> list[ProductDrawing]:
    return drawings(
        status="confirmed",
        q=q,
        product_category=product_category,
        material=material,
        thickness=thickness,
        product_thickness=product_thickness,
        plate_thickness=plate_thickness,
        outer_diameter=outer_diameter,
        inner_diameter=inner_diameter,
        teeth_count=teeth_count,
        module=module,
        pressure_angle=pressure_angle,
        common_normal_length=common_normal_length,
        pin_diameter=pin_diameter,
        pin_span=pin_span,
        db=db,
    )


@router.get("/drawings/{drawing_id}", response_model=DrawingOut)
def drawing_detail(drawing_id: int, db: Session = Depends(get_db)) -> ProductDrawing:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    return drawing


@router.delete("/drawings/{drawing_id}")
def delete_drawing(drawing_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    ensure_drawing_can_be_changed(drawing, db)
    before_data = drawing_snapshot(drawing)
    delete_uploaded_drawing(drawing_id, db)
    record_operation_log(db, "drawing_delete", "drawing", drawing_id, None, "小程序删除图纸", before_data=before_data)
    db.commit()
    return {"id": drawing_id, "message": "图纸已删除"}


@router.post("/drawings/{drawing_id}/confirm", response_model=DrawingOut)
def confirm_drawing(drawing_id: int, payload: DrawingConfirm, db: Session = Depends(get_db)) -> ProductDrawing:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    was_confirmed = drawing.confirmed == 1
    before_data = drawing_snapshot(drawing)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(drawing, key, value)
    drawing.tooth_type = normalize_tooth_type(drawing.tooth_type)
    if drawing.teeth_count_text:
        drawing.teeth_count = first_int_value(drawing.teeth_count_text)
    if drawing.module_text:
        drawing.module = plain_float_value(drawing.module_text)
    if drawing.common_normal_length_text:
        drawing.common_normal_length = common_normal_value_from_text(drawing.common_normal_length_text, drawing.tooth_type)
    drawing.thickness = drawing.product_thickness or drawing.plate_thickness or drawing.thickness
    drawing.confirmed = 1
    apply_drawing_version(drawing, db, force_increment=was_confirmed)
    synced_count = sync_product_inventory_from_drawing(drawing, db)
    record_operation_log(db, "drawing_confirm", "drawing", drawing.id, None, f"小程序确认图纸，同步成品库存 {synced_count} 批", before_data=before_data, after_data=drawing_snapshot(drawing))
    db.commit()
    db.refresh(drawing)
    return drawing


@router.post("/drawings/{drawing_id}/rerun", response_model=DrawingOut)
def rerun_drawing(drawing_id: int, db: Session = Depends(get_db)) -> ProductDrawing:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    ensure_drawing_can_be_changed(drawing, db)
    before_data = drawing_snapshot(drawing)
    try:
        candidates = parse_dxf(drawing.dxf_file_url)
        recognized = recognize_drawing(candidates)
        drawing.parse_status = "parsed"
    except Exception as exc:
        candidates = {}
        recognized = {"error": str(exc), "need_manual_review": True, "confidence": 0}
        drawing.parse_status = "failed"
    drawing.parse_result_json = {"candidates": candidates, "recognized": recognized}
    record_operation_log(db, "drawing_rerun", "drawing", drawing.id, None, "小程序重新识别图纸", before_data=before_data, after_data=drawing_snapshot(drawing))
    db.commit()
    db.refresh(drawing)
    return drawing


@router.get("/products", response_model=list[ProductInventoryGroupOut])
def products(q: str = "", material: str = "", thickness: str = "", db: Session = Depends(get_db)) -> list[ProductInventoryGroupOut]:
    query = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "product")
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            (MaterialInventory.material_code.ilike(like))
            | (MaterialInventory.material.ilike(like))
            | (MaterialInventory.location.ilike(like))
            | (MaterialInventory.paper_material.ilike(like))
            | (MaterialInventory.source_product_code.ilike(like))
        )
    if material.strip():
        query = query.filter(MaterialInventory.material.ilike(f"%{material.strip()}%"))
    thickness_value = _optional_float(thickness)
    if thickness_value is not None:
        query = query.filter(MaterialInventory.thickness == thickness_value)
    return _group_product_inventory(query.order_by(MaterialInventory.created_at.desc()).all())


@router.get("/products/{product_code}/batches", response_model=list[InventoryItemOut])
def product_batches(product_code: str, db: Session = Depends(get_db)) -> list[MaterialInventory]:
    return db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "product", MaterialInventory.material_code == product_code).order_by(MaterialInventory.created_at.desc()).all()


@router.post("/products/inbound", response_model=InventoryItemOut)
def product_inbound(payload: ProductInboundPayload, db: Session = Depends(get_db)) -> MaterialInventory:
    with inventory_write_lock():
        drawing = db.get(ProductDrawing, payload.drawing_id)
        if not drawing or drawing.confirmed != 1 or drawing.is_active != 1:
            raise HTTPException(status_code=404, detail="已确认图纸不存在")
        idempotency_key = _idempotency_key("mobile_product_inbound", payload.client_request_id)
        result = product_inbound_from_drawing(
            drawing=drawing,
            quantity=payload.quantity,
            location=payload.location,
            paper_material=payload.paper_material,
            operator_name=payload.operator_name,
            db=db,
            idempotency_key=idempotency_key,
        )
        if result.duplicated_request:
            return result.item
        record_operation_log(
            db,
            "product_inbound",
            "inventory",
            result.item.id,
            payload.operator_name or None,
            f"小程序产品入库：{drawing.product_code}，数量 {payload.quantity}",
            before_data={"quantity": result.before_total_quantity, "drawing": drawing_snapshot(drawing)},
            after_data=inventory_snapshot(result.item),
        )
        db.commit()
        db.refresh(result.item)
        return result.item


@router.post("/products/outbound")
def product_outbound(payload: ProductOutboundPayload, db: Session = Depends(get_db)) -> dict[str, int | str]:
    with inventory_write_lock():
        idempotency_key = _idempotency_key("mobile_product_outbound", payload.client_request_id)
        existing_record = db.query(InventoryTransactionRecord).filter(InventoryTransactionRecord.idempotency_key == idempotency_key).first() if idempotency_key else None
        if existing_record:
            return {"message": "产品出库成功", "before_quantity": existing_record.before_quantity, "after_quantity": existing_record.after_quantity}
        drawing = db.get(ProductDrawing, payload.drawing_id)
        if not drawing or drawing.confirmed != 1 or drawing.is_active != 1 or not drawing.product_code:
            raise HTTPException(status_code=404, detail="已确认图纸不存在")
        if payload.quantity <= 0:
            raise HTTPException(status_code=400, detail="出库数量必须大于0")
        query = db.query(MaterialInventory).filter(
            MaterialInventory.inventory_type == "product",
            MaterialInventory.material_code == drawing.product_code,
            MaterialInventory.quantity > 0,
        )
        location_value = payload.location.strip() if payload.location else ""
        if location_value:
            query = query.filter(MaterialInventory.location == location_value)
        batches = query.order_by(MaterialInventory.created_at.asc()).all()
        before_total_quantity = sum(item.quantity for item in batches)
        if before_total_quantity < payload.quantity:
            stock_scope = f"库位 {location_value} " if location_value else ""
            raise HTTPException(status_code=400, detail=f"{stock_scope}库存不足，当前总库存 {before_total_quantity}")
        remaining = payload.quantity
        affected_items = []
        for item in batches:
            if remaining <= 0:
                break
            item_before_quantity = item.quantity
            deduction = min(item.quantity, remaining)
            item.quantity -= deduction
            remaining -= deduction
            item.status = "used" if item.quantity <= 0 else "available"
            affected_items.append((item, deduction, item_before_quantity, item.quantity))
        for index, (item, deduction, item_before_quantity, item_after_quantity) in enumerate(affected_items):
            db.add(InventoryTransactionRecord(inventory_id=item.id, transaction_type="out", quantity=deduction, before_quantity=item_before_quantity, after_quantity=item_after_quantity, idempotency_key=idempotency_key if index == 0 else None, operator_name=payload.operator_name or None, customer_name=(payload.customer_name or "").strip() or None, outbound_purpose=normalize_outbound_purpose(payload.outbound_purpose), remark=payload.remark or "产品出库"))
        record_operation_log(
            db,
            "product_outbound",
            "inventory",
            affected_items[0][0].id if affected_items else None,
            payload.operator_name or None,
            payload.remark or f"小程序产品出库：{drawing.product_code}，数量 {payload.quantity}",
            before_data={"quantity": before_total_quantity, "location": location_value or None, "drawing": drawing_snapshot(drawing)},
            after_data={"quantity": before_total_quantity - payload.quantity, "location": location_value or None},
        )
        db.commit()
        return {"message": "产品出库成功", "before_quantity": before_total_quantity, "after_quantity": before_total_quantity - payload.quantity}


@router.get("/products/transactions", response_model=list[TransactionOut])
def product_transactions(db: Session = Depends(get_db)) -> list[TransactionOut]:
    records = db.query(InventoryTransactionRecord).order_by(InventoryTransactionRecord.created_at.desc()).limit(500).all()
    return _transaction_rows(records, "product", db)


@router.post("/products/transactions/{transaction_id}/reverse", response_model=TransactionOut)
def reverse_product_transaction(transaction_id: int, payload: TransactionReversePayload, db: Session = Depends(get_db)) -> TransactionOut:
    reversal = reverse_inventory_transaction(transaction_id, payload.operator_name, payload.remark, db)
    db.flush()
    record_operation_log(
        db,
        "transaction_reverse",
        "inventory_transaction",
        transaction_id,
        payload.operator_name or None,
        payload.remark or "小程序撤销产品流水",
        after_data={"reversal_transaction_id": reversal.id},
    )
    db.commit()
    db.refresh(reversal)
    rows = _transaction_rows([reversal], "product", db)
    if not rows:
        raise HTTPException(status_code=400, detail="该流水不是产品库存流水")
    return rows[0]


@router.get("/scraps/pending", response_model=list[InventoryItemOut])
def pending_scraps(db: Session = Depends(get_db)) -> list[MaterialInventory]:
    return db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "scrap", MaterialInventory.status == "pending").order_by(MaterialInventory.created_at.desc()).all()


@router.post("/scraps/{inventory_id}/confirm", response_model=InventoryItemOut)
def confirm_scrap(inventory_id: int, payload: ScrapConfirmPayload, db: Session = Depends(get_db)) -> MaterialInventory:
    with inventory_write_lock():
        item = db.get(MaterialInventory, inventory_id)
        if not item:
            raise HTTPException(status_code=404, detail="余料不存在")
        if item.inventory_type != "scrap":
            raise HTTPException(status_code=400, detail="该库存不是余料")
        if item.status != "pending":
            raise HTTPException(status_code=400, detail="该余料不是待入库状态，不能重复确认")
        if payload.actual_quantity < 0:
            raise HTTPException(status_code=400, detail="实际数量不能小于0")
        if not payload.location.strip():
            raise HTTPException(status_code=400, detail="确认入库时必须填写库位")
        before_quantity = item.quantity
        item.quantity = payload.actual_quantity
        item.diameter = payload.actual_diameter or item.diameter
        item.usable_size = f"φ{item.diameter:g}" if item.diameter else item.usable_size
        item.location = payload.location.strip()
        item.status = "available" if payload.actual_quantity > 0 else "used"
        db.add(InventoryTransactionRecord(inventory_id=item.id, transaction_type="confirm", quantity=payload.actual_quantity, before_quantity=before_quantity, after_quantity=payload.actual_quantity, operator_name=payload.operator_name or None, remark="余料确认入库"))
        record_operation_log(
            db,
            "scrap_confirm",
            "inventory",
            item.id,
            payload.operator_name or None,
            f"小程序余料确认入库：数量 {payload.actual_quantity}，库位 {payload.location.strip()}",
            before_data={"quantity": before_quantity},
            after_data=inventory_snapshot(item),
        )
        db.commit()
        db.refresh(item)
        return item


@router.get("/scraps", response_model=list[ScrapInventoryGroupOut])
def scraps(material: str = "", thickness: str = "", required_diameter: str = "", location: str = "", db: Session = Depends(get_db)) -> list[ScrapInventoryGroupOut]:
    query = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "scrap", MaterialInventory.status == "available", MaterialInventory.quantity > 0)
    if material.strip():
        query = query.filter(MaterialInventory.material.ilike(f"%{material.strip()}%"))
    thickness_value = _optional_float(thickness)
    if thickness_value is not None:
        query = query.filter(MaterialInventory.thickness == thickness_value)
    required_diameter_value = _optional_float(required_diameter)
    if required_diameter_value is not None:
        query = query.filter(MaterialInventory.diameter >= required_diameter_value)
    if location.strip():
        query = query.filter(MaterialInventory.location.ilike(f"%{location.strip()}%"))
    grouped: dict[str, dict] = {}
    for item in query.order_by(MaterialInventory.diameter.asc(), MaterialInventory.created_at.asc()).all():
        location_label = _scrap_location_label(item)
        key = f"{item.material}||{item.thickness}||{item.usable_size or '-'}||{location_label}"
        if key not in grouped:
            grouped[key] = {"group_key": key, "material": item.material, "thickness": item.thickness, "usable_size": item.usable_size or "-", "location": location_label, "quantity": 0, "diameter": item.diameter}
        grouped[key]["quantity"] += item.quantity
    return [ScrapInventoryGroupOut(**value) for value in grouped.values()]


@router.post("/scraps/outbound")
def scrap_outbound(payload: ScrapOutboundPayload, db: Session = Depends(get_db)) -> dict[str, int | str]:
    with inventory_write_lock():
        idempotency_key = _idempotency_key("mobile_scrap_outbound", payload.client_request_id)
        existing_record = db.query(InventoryTransactionRecord).filter(InventoryTransactionRecord.idempotency_key == idempotency_key).first() if idempotency_key else None
        if existing_record:
            return {"message": "余料出库成功", "before_quantity": existing_record.before_quantity, "after_quantity": existing_record.after_quantity}
        if payload.quantity <= 0:
            raise HTTPException(status_code=400, detail="出库数量必须大于0")
        parts = payload.scrap_group_key.split("||")
        if len(parts) != 4:
            raise HTTPException(status_code=400, detail="余料规格参数错误")
        material_value = parts[0]
        batches = find_scrap_batches_for_outbound(payload.scrap_group_key, db)
        before_quantity = sum(item.quantity for item in batches)
        if before_quantity < payload.quantity:
            raise HTTPException(status_code=400, detail=f"余料数量不足，当前数量 {before_quantity}")
        remaining = payload.quantity
        affected_items = []
        for item in batches:
            if remaining <= 0:
                break
            item_before_quantity = item.quantity
            deduction = min(item.quantity, remaining)
            item.quantity -= deduction
            remaining -= deduction
            if item.quantity <= 0:
                item.status = "used"
            affected_items.append((item, deduction, item_before_quantity, item.quantity))
        for index, (item, deduction, item_before_quantity, item_after_quantity) in enumerate(affected_items):
            db.add(InventoryTransactionRecord(inventory_id=item.id, transaction_type="out", quantity=deduction, before_quantity=item_before_quantity, after_quantity=item_after_quantity, idempotency_key=idempotency_key if index == 0 else None, operator_name=payload.operator_name or None, customer_name=(payload.customer_name or "").strip() or None, remark=payload.remark or "余料出库"))
        record_operation_log(
            db,
            "scrap_outbound",
            "inventory",
            affected_items[0][0].id if affected_items else None,
            payload.operator_name or None,
            payload.remark or f"小程序余料出库：{material_value}，数量 {payload.quantity}",
            before_data={"quantity": before_quantity, "scrap_group_key": payload.scrap_group_key},
            after_data={"quantity": before_quantity - payload.quantity},
        )
        db.commit()
        return {"message": "余料出库成功", "before_quantity": before_quantity, "after_quantity": before_quantity - payload.quantity}


@router.get("/scraps/transactions", response_model=list[TransactionOut])
def scrap_transactions(db: Session = Depends(get_db)) -> list[TransactionOut]:
    records = db.query(InventoryTransactionRecord).order_by(InventoryTransactionRecord.created_at.desc()).limit(500).all()
    return _transaction_rows(records, "scrap", db)


@router.post("/scraps/transactions/{transaction_id}/reverse", response_model=TransactionOut)
def reverse_scrap_transaction(transaction_id: int, payload: TransactionReversePayload, db: Session = Depends(get_db)) -> TransactionOut:
    reversal = reverse_inventory_transaction(transaction_id, payload.operator_name, payload.remark, db)
    db.flush()
    record_operation_log(
        db,
        "transaction_reverse",
        "inventory_transaction",
        transaction_id,
        payload.operator_name or None,
        payload.remark or "小程序撤销余料流水",
        after_data={"reversal_transaction_id": reversal.id},
    )
    db.commit()
    db.refresh(reversal)
    rows = _transaction_rows([reversal], "scrap", db)
    if not rows:
        raise HTTPException(status_code=400, detail="该流水不是余料库存流水")
    return rows[0]
