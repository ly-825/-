from datetime import datetime, timedelta
from io import BytesIO
from urllib.parse import quote

from fastapi import HTTPException
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy.orm import Session

from app.models import InventoryTransactionRecord, MaterialInventory, ProductDrawing, ScrapGenerationRecord
from app.services.operation_log import record_operation_log


EXPORT_MODULES = {
    "product_inventory": "产品库存",
    "raw_plate_inventory": "板料库存",
    "scrap_inventory": "余料库存",
    "product_transactions": "产品流水",
    "raw_plate_transactions": "板料流水",
    "scrap_transactions": "余料流水",
    "outbound_report": "出库统计",
}


def _fmt_time(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _fmt_num(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _optional_float(value: str | None) -> float | None:
    try:
        return float(value) if value and value.strip() else None
    except ValueError:
        return None


def _transaction_label(value: str) -> str:
    return {"in": "入库", "out": "出库", "confirm": "确认入库"}.get(value, value)


def _apply_inventory_filters(query, filters: dict, inventory_type: str):
    query = query.filter(MaterialInventory.inventory_type == inventory_type)
    keyword = (filters.get("q") or "").strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            (MaterialInventory.material_code.ilike(like))
            | (MaterialInventory.material.ilike(like))
            | (MaterialInventory.location.ilike(like))
            | (MaterialInventory.usable_size.ilike(like))
            | (MaterialInventory.source_product_code.ilike(like))
        )
    material = (filters.get("material") or "").strip()
    if material:
        query = query.filter(MaterialInventory.material.ilike(f"%{material}%"))
    thickness = _optional_float(filters.get("thickness"))
    if thickness is not None:
        query = query.filter(MaterialInventory.thickness == thickness)
    return query


def _date_range(filters: dict) -> tuple[datetime | None, datetime | None]:
    start_value = (filters.get("start_date") or "").strip()
    end_value = (filters.get("end_date") or "").strip()
    start = datetime.strptime(start_value, "%Y-%m-%d") if start_value else None
    end = datetime.strptime(end_value, "%Y-%m-%d") + timedelta(days=1) if end_value else None
    return start, end


def _transaction_rows(db: Session, inventory_type: str, filters: dict) -> tuple[list[str], list[list[object]]]:
    query = db.query(InventoryTransactionRecord).order_by(InventoryTransactionRecord.created_at.desc())
    start, end = _date_range(filters)
    if start:
        query = query.filter(InventoryTransactionRecord.created_at >= start)
    if end:
        query = query.filter(InventoryTransactionRecord.created_at < end)
    records = query.all()
    inventory_ids = [record.inventory_id for record in records]
    inventory_map = {item.id: item for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()} if inventory_ids else {}
    product_code = (filters.get("product_code") or filters.get("q") or "").strip()
    rows = []
    for record in records:
        item = inventory_map.get(record.inventory_id)
        if not item or item.inventory_type != inventory_type:
            continue
        code = item.material_code or item.source_product_code or item.material or ""
        if product_code and product_code not in code:
            continue
        rows.append([
            record.id,
            _transaction_label(record.transaction_type),
            code,
            record.quantity,
            record.before_quantity,
            record.after_quantity,
            record.operator_name or "",
            record.remark or "",
            _fmt_time(record.created_at),
        ])
    return ["流水号", "类型", "型号/规格", "数量", "操作前库存", "操作后库存", "操作人", "备注", "创建时间"], rows


def _outbound_report_rows(db: Session, filters: dict) -> tuple[list[str], list[list[object]]]:
    start, end = _report_range(filters)
    records = db.query(InventoryTransactionRecord).filter(
        InventoryTransactionRecord.transaction_type == "out",
        InventoryTransactionRecord.reversed_transaction_id.is_(None),
        InventoryTransactionRecord.created_at >= start,
        InventoryTransactionRecord.created_at < end,
    ).all()
    inventory_ids = [record.inventory_id for record in records]
    inventory_map = {item.id: item for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()} if inventory_ids else {}
    grouped: dict[tuple[str, str], int] = {}
    total = 0
    for record in records:
        item = inventory_map.get(record.inventory_id)
        if not item:
            continue
        type_label = {"product": "产品", "raw_plate": "板料", "scrap": "余料"}.get(item.inventory_type, item.inventory_type)
        name = item.material_code or item.source_product_code or item.usable_size or item.material or "-"
        grouped[(type_label, name)] = grouped.get((type_label, name), 0) + record.quantity
        total += record.quantity
    range_label = f"{start.strftime('%Y-%m-%d')} 至 {(end - timedelta(days=1)).strftime('%Y-%m-%d')}"
    rows = []
    for (type_label, name), quantity in sorted(grouped.items(), key=lambda item: item[1], reverse=True):
        ratio = f"{quantity / total * 100:.2f}%" if total else "0.00%"
        rows.append([type_label, name, quantity, ratio, range_label])
    return ["类型", "型号/规格", "数量", "占比", "时间范围"], rows


def _report_range(filters: dict) -> tuple[datetime, datetime]:
    start_value = (filters.get("start_date") or "").strip()
    end_value = (filters.get("end_date") or "").strip()
    if start_value and end_value:
        return datetime.strptime(start_value, "%Y-%m-%d"), datetime.strptime(end_value, "%Y-%m-%d") + timedelta(days=1)
    now = datetime.now()
    period = (filters.get("period") or "day").strip()
    if period == "month":
        return datetime(now.year, now.month, 1), now + timedelta(days=1)
    if period == "year":
        return datetime(now.year, 1, 1), now + timedelta(days=1)
    return datetime(now.year, now.month, now.day), now + timedelta(days=1)


def build_export_rows(module: str, filters: dict, db: Session) -> tuple[str, list[str], list[list[object]]]:
    if module not in EXPORT_MODULES:
        raise HTTPException(status_code=404, detail="导出模块不存在")
    if module == "product_inventory":
        items = _apply_inventory_filters(db.query(MaterialInventory), filters, "product").order_by(MaterialInventory.created_at.desc()).all()
        rows = [[item.material_code or item.source_product_code or "", item.quantity, item.material, _fmt_num(item.thickness), item.location or "", item.source_drawing_id or "", _fmt_time(item.created_at)] for item in items]
        return EXPORT_MODULES[module], ["产品型号", "库存数量", "材质", "厚度", "库位", "来源图纸", "创建时间"], rows
    if module == "raw_plate_inventory":
        items = _apply_inventory_filters(db.query(MaterialInventory), filters, "raw_plate").order_by(MaterialInventory.created_at.desc()).all()
        rows = [[item.material, _fmt_num(item.length), _fmt_num(item.width), _fmt_num(item.thickness), item.quantity, item.material_code or "", item.location or "", _fmt_time(item.created_at)] for item in items]
        return EXPORT_MODULES[module], ["材质", "长度", "宽度", "厚度", "张数", "批次号", "库位", "创建时间"], rows
    if module == "scrap_inventory":
        query = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "scrap")
        material = (filters.get("material") or "").strip()
        if material:
            query = query.filter(MaterialInventory.material.ilike(f"%{material}%"))
        thickness = _optional_float(filters.get("thickness"))
        if thickness is not None:
            query = query.filter(MaterialInventory.thickness == thickness)
        location = (filters.get("location") or "").strip()
        if location:
            query = query.filter(MaterialInventory.location.ilike(f"%{location}%"))
        items = query.order_by(MaterialInventory.created_at.desc()).all()
        rows = [[item.material, _fmt_num(item.thickness), _fmt_num(item.length), _fmt_num(item.width), item.quantity, item.status, item.location or "", item.source_product_code or "", _fmt_time(item.created_at)] for item in items]
        return EXPORT_MODULES[module], ["材质", "厚度", "长度", "宽度", "数量", "状态", "库位", "来源产品", "创建时间"], rows
    if module == "product_transactions":
        return EXPORT_MODULES[module], *_transaction_rows(db, "product", filters)
    if module == "raw_plate_transactions":
        return EXPORT_MODULES[module], *_transaction_rows(db, "raw_plate", filters)
    if module == "scrap_transactions":
        return EXPORT_MODULES[module], *_transaction_rows(db, "scrap", filters)
    return EXPORT_MODULES[module], *_outbound_report_rows(db, filters)


def make_workbook_bytes(title: str, headings: list[str], rows: list[list[object]]) -> BytesIO:
    workbook = Workbook(write_only=False)
    sheet = workbook.active
    sheet.title = title[:31]
    sheet.append(headings)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1D4ED8")
    for row in rows:
        sheet.append(row)
    for column_cells in sheet.columns:
        width = min(max(len(str(cell.value or "")) for cell in column_cells) + 2, 36)
        sheet.column_dimensions[column_cells[0].column_letter].width = width
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def export_filename(title: str) -> str:
    return f"{title}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"


def content_disposition(filename: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(filename)}"


def log_export(module: str, filters: dict, row_count: int, db: Session) -> None:
    record_operation_log(
        db,
        "excel_export",
        module,
        None,
        None,
        f"导出{EXPORT_MODULES.get(module, module)}，共{row_count}行",
        after_data={"module": module, "filters": filters, "row_count": row_count, "export_time": datetime.now().isoformat()},
    )
