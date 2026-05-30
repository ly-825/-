from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import InventoryTransactionRecord, MaterialInventory, ProductDrawing, ScrapGenerationRecord
from app.services.assistant_intent_parser import parse_assistant_intent, serialize_intent_context
from app.services.operation_log import record_operation_log


def _period_range(period: str | None) -> tuple[datetime, datetime, str]:
    now = datetime.now()
    if period == "year":
        return datetime(now.year, 1, 1), now + timedelta(days=1), "本年"
    if period == "quarter":
        quarter_month = ((now.month - 1) // 3) * 3 + 1
        return datetime(now.year, quarter_month, 1), now + timedelta(days=1), "本季度"
    if period == "month":
        return datetime(now.year, now.month, 1), now + timedelta(days=1), "本月"
    return datetime(now.year, now.month, now.day), now + timedelta(days=1), "今天"


def _intent_date_range(intent: dict) -> tuple[datetime, datetime, str]:
    time_range = intent.get("time_range") or {}
    start_date = time_range.get("start_date")
    end_date = time_range.get("end_date")
    if start_date and end_date:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        return start, end, f"{start_date} 至 {end_date}"
    range_type = time_range.get("type")
    period = {
        "today": "day",
        "this_month": "month",
        "this_quarter": "quarter",
        "this_year": "year",
    }.get(range_type, "month")
    return _period_range(period)


def _ratio(value: int, total: int) -> str:
    return f"{value / total * 100:.2f}%" if total else "0.00%"


def _table(title: str, columns: list[dict], rows: list[dict], data_type: str = "table") -> dict:
    return {"type": data_type, "title": title, "columns": columns, "rows": rows}


def inventory_top(intent: dict, db: Session) -> dict:
    limit = int((intent.get("ranking") or {}).get("limit") or intent.get("extra") or 10)
    domain = intent.get("entity") or intent.get("domain") or "product"
    items = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == domain, MaterialInventory.quantity > 0).all()
    grouped: dict[str, dict] = {}
    for item in items:
        key = item.material_code or item.source_product_code or item.material or "未编号"
        group = grouped.setdefault(key, {"product_code": key, "quantity": 0, "material": item.material, "thickness": item.thickness})
        group["quantity"] += item.quantity
    sorted_rows = sorted(grouped.values(), key=lambda row: row["quantity"], reverse=True)[:limit]
    total = sum(row["quantity"] for row in sorted_rows)
    rows = [{"rank": index + 1, **row, "ratio": _ratio(row["quantity"], total)} for index, row in enumerate(sorted_rows)]
    return {"answer": f"库存Top{limit}如下。", "data": _table(f"库存Top{limit}", [{"prop": "rank", "label": "排名"}, {"prop": "product_code", "label": "产品型号/规格"}, {"prop": "quantity", "label": "数量"}, {"prop": "ratio", "label": "占比"}, {"prop": "material", "label": "材质"}, {"prop": "thickness", "label": "厚度"}], rows, "ranking_table")}


def inventory_low(intent: dict, db: Session) -> dict:
    threshold = int((intent.get("ranking") or {}).get("limit") or intent.get("extra") or 10)
    domain = intent.get("entity") or intent.get("domain") or "product"
    items = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == domain).all()
    grouped: dict[str, dict] = {}
    for item in items:
        key = item.material_code or item.source_product_code or item.material or "未编号"
        group = grouped.setdefault(key, {"product_code": key, "quantity": 0, "material": item.material, "thickness": item.thickness})
        group["quantity"] += item.quantity
    rows = [{"rank": index + 1, **row} for index, row in enumerate(sorted([row for row in grouped.values() if row["quantity"] < threshold], key=lambda row: row["quantity"]))]
    return {"answer": f"库存低于{threshold}的项目如下。", "data": _table("低库存清单", [{"prop": "rank", "label": "排名"}, {"prop": "product_code", "label": "产品型号/规格"}, {"prop": "quantity", "label": "数量"}, {"prop": "material", "label": "材质"}, {"prop": "thickness", "label": "厚度"}], rows, "warning_table")}


def outbound_top(intent: dict, db: Session) -> dict:
    limit = int((intent.get("ranking") or {}).get("limit") or intent.get("extra") or 10)
    domain = intent.get("entity") or intent.get("domain") or "product"
    start, end, label = _intent_date_range(intent)
    records = db.query(InventoryTransactionRecord).filter(InventoryTransactionRecord.transaction_type == "out", InventoryTransactionRecord.reversed_transaction_id.is_(None), InventoryTransactionRecord.created_at >= start, InventoryTransactionRecord.created_at < end).all()
    inventory_ids = [record.inventory_id for record in records]
    inventory_map = {item.id: item for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()} if inventory_ids else {}
    grouped: dict[str, int] = {}
    for record in records:
        item = inventory_map.get(record.inventory_id)
        if not item or item.inventory_type != domain:
            continue
        key = item.material_code or item.source_product_code or item.material or "未编号"
        grouped[key] = grouped.get(key, 0) + record.quantity
    sorted_items = sorted(grouped.items(), key=lambda row: row[1], reverse=True)[:limit]
    total = sum(quantity for _, quantity in sorted_items)
    rows = [{"rank": index + 1, "product_code": key, "quantity": quantity, "ratio": _ratio(quantity, total)} for index, (key, quantity) in enumerate(sorted_items)]
    return {"answer": f"{label}出库Top{limit}如下。", "data": _table(f"{label}出库Top{limit}", [{"prop": "rank", "label": "排名"}, {"prop": "product_code", "label": "产品型号/规格"}, {"prop": "quantity", "label": "出库数量"}, {"prop": "ratio", "label": "占比"}], rows, "ranking_table")}


def transaction_ranking(intent: dict, db: Session) -> dict:
    transaction_type = "in" if intent.get("action") == "inbound" or intent.get("intent") == "inbound_ranking" else "out"
    action_label = "入库" if transaction_type == "in" else "出库"
    limit = int((intent.get("ranking") or {}).get("limit") or intent.get("extra") or 10)
    domain = intent.get("entity") or "product"
    if domain == "inventory":
        domain = "product"
    start, end, label = _intent_date_range(intent)
    records = db.query(InventoryTransactionRecord).filter(
        InventoryTransactionRecord.transaction_type == transaction_type,
        InventoryTransactionRecord.reversed_transaction_id.is_(None),
        InventoryTransactionRecord.created_at >= start,
        InventoryTransactionRecord.created_at < end,
    ).all()
    inventory_ids = [record.inventory_id for record in records]
    inventory_map = {item.id: item for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()} if inventory_ids else {}
    grouped: dict[str, int] = {}
    for record in records:
        item = inventory_map.get(record.inventory_id)
        if not item or item.inventory_type != domain:
            continue
        key = item.material_code or item.source_product_code or item.material or "未编号"
        grouped[key] = grouped.get(key, 0) + record.quantity
    reverse = (intent.get("ranking") or {}).get("sort") != "asc"
    sorted_items = sorted(grouped.items(), key=lambda row: row[1], reverse=reverse)[:limit]
    total = sum(quantity for _, quantity in sorted_items)
    rows = [{"rank": index + 1, "product_code": key, "quantity": quantity, "ratio": _ratio(quantity, total)} for index, (key, quantity) in enumerate(sorted_items)]
    return {"answer": f"{label}{action_label}Top{limit}如下。", "data": _table(f"{label}{action_label}Top{limit}", [{"prop": "rank", "label": "排名"}, {"prop": "product_code", "label": "型号/规格"}, {"prop": "quantity", "label": f"{action_label}数量"}, {"prop": "ratio", "label": "占比"}], rows, "ranking_table")}


def transaction_summary(intent: dict, db: Session) -> dict:
    transaction_type = "in" if intent.get("action") == "inbound" or intent.get("intent") == "inbound_summary" else "out"
    action_label = "入库" if transaction_type == "in" else "出库"
    domain = intent.get("entity") or "product"
    if domain == "inventory":
        domain = "product"
    start, end, label = _intent_date_range(intent)
    records = db.query(InventoryTransactionRecord).filter(
        InventoryTransactionRecord.transaction_type == transaction_type,
        InventoryTransactionRecord.reversed_transaction_id.is_(None),
        InventoryTransactionRecord.created_at >= start,
        InventoryTransactionRecord.created_at < end,
    ).all()
    inventory_ids = [record.inventory_id for record in records]
    inventory_map = {item.id: item for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()} if inventory_ids else {}
    total = 0
    grouped: dict[str, int] = {}
    for record in records:
        item = inventory_map.get(record.inventory_id)
        if not item or item.inventory_type != domain:
            continue
        key = item.material_code or item.source_product_code or item.material or "未编号"
        grouped[key] = grouped.get(key, 0) + record.quantity
        total += record.quantity
    rows = [{"item_name": key, "quantity": quantity, "ratio": _ratio(quantity, total)} for key, quantity in sorted(grouped.items(), key=lambda row: row[1], reverse=True)]
    unit = "张" if domain == "raw_plate" else "件"
    data = {
        "type": "summary_table",
        "title": f"{label}{action_label}汇总",
        "cards": [{"label": f"{action_label}总数量", "value": total, "unit": unit}],
        "columns": [{"prop": "item_name", "label": "型号/规格"}, {"prop": "quantity", "label": f"{action_label}数量"}, {"prop": "ratio", "label": "占比"}],
        "rows": rows,
    }
    return {"answer": f"{label}{action_label}总数量：{total}{unit}。", "data": data}


def scrap_idle(intent: dict, db: Session) -> dict:
    days = int((intent.get("time_range") or {}).get("days") or intent.get("extra") or 30)
    deadline = datetime.now() - timedelta(days=days)
    items = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "scrap", MaterialInventory.quantity > 0, MaterialInventory.updated_at <= deadline).order_by(MaterialInventory.updated_at.asc()).limit(50).all()
    rows = [{"material": item.material, "thickness": item.thickness, "size": item.usable_size or "", "quantity": item.quantity, "last_used_at": item.updated_at.strftime("%Y-%m-%d %H:%M:%S") if item.updated_at else "", "location": item.location or ""} for item in items]
    return {"answer": f"超过{days}天未使用余料如下。", "data": _table(f"超过{days}天未使用余料", [{"prop": "material", "label": "材质"}, {"prop": "thickness", "label": "厚度"}, {"prop": "size", "label": "尺寸"}, {"prop": "quantity", "label": "库存数量"}, {"prop": "last_used_at", "label": "最后使用时间"}, {"prop": "location", "label": "库位"}], rows, "warning_table")}


def drawing_recent(intent: dict, db: Session) -> dict:
    days = int((intent.get("time_range") or {}).get("days") or intent.get("extra") or 30)
    drawings = db.query(ProductDrawing).filter(ProductDrawing.created_at >= datetime.now() - timedelta(days=days)).order_by(ProductDrawing.created_at.desc()).limit(50).all()
    rows = [{"product_code": drawing.product_code or "", "version": drawing.version, "material": drawing.material or "", "created_at": drawing.created_at.strftime("%Y-%m-%d %H:%M:%S") if drawing.created_at else ""} for drawing in drawings]
    return {"answer": f"最近{days}天新增图纸如下。", "data": _table(f"最近{days}天新增图纸", [{"prop": "product_code", "label": "产品型号"}, {"prop": "version", "label": "版本"}, {"prop": "material", "label": "材质"}, {"prop": "created_at", "label": "创建时间"}], rows)}


def drawing_version_top(intent: dict, db: Session) -> dict:
    limit = int((intent.get("ranking") or {}).get("limit") or intent.get("extra") or 10)
    drawings = db.query(ProductDrawing).all()
    grouped: dict[str, dict] = {}
    for drawing in drawings:
        key = drawing.product_code or "未编号"
        group = grouped.setdefault(key, {"product_code": key, "version_count": 0, "created_at": drawing.created_at})
        group["version_count"] += 1
        if drawing.created_at and (not group["created_at"] or drawing.created_at > group["created_at"]):
            group["created_at"] = drawing.created_at
    rows = [{"rank": index + 1, "product_code": row["product_code"], "version_count": row["version_count"], "created_at": row["created_at"].strftime("%Y-%m-%d %H:%M:%S") if row["created_at"] else ""} for index, row in enumerate(sorted(grouped.values(), key=lambda row: row["version_count"], reverse=True)[:limit])]
    return {"answer": f"图纸版本Top{limit}如下。", "data": _table(f"图纸版本Top{limit}", [{"prop": "rank", "label": "排名"}, {"prop": "product_code", "label": "产品型号"}, {"prop": "version_count", "label": "版本数"}, {"prop": "created_at", "label": "最近创建时间"}], rows, "ranking_table")}


def loss_ranking(intent: dict, db: Session) -> dict:
    limit = int((intent.get("ranking") or {}).get("limit") or intent.get("extra") or 10)
    records = db.query(ScrapGenerationRecord).order_by(ScrapGenerationRecord.registered_at.desc()).limit(500).all()
    rows = []
    for record in records:
        rows.append({"product_code": record.source_product_code or "", "theoretical_scrap": record.theoretical_size or "", "actual_scrap": record.actual_size or "", "diff_rate": "需按尺寸复核", "usage_rate": "需按尺寸复核"})
    return {"answer": "当前已列出理论余料与实际余料，尺寸差异率需结合统一计量规则复核。", "data": _table("损耗分析基础数据", [{"prop": "product_code", "label": "产品型号"}, {"prop": "theoretical_scrap", "label": "理论余料"}, {"prop": "actual_scrap", "label": "实际余料"}, {"prop": "diff_rate", "label": "差异率"}, {"prop": "usage_rate", "label": "利用率"}], rows[:limit])}


def warning_list(intent: dict, db: Session) -> dict:
    days = int((intent.get("time_range") or {}).get("days") or intent.get("extra") or 90)
    low = inventory_low({"domain": "product", "extra": "10"}, db)["data"]["rows"]
    rows = [{"risk_level": "高", "type": "产品低库存", "item_name": row["product_code"], "quantity": row["quantity"], "reason": "库存低于10件"} for row in low]
    deadline = datetime.now() - timedelta(days=days)
    idle_items = db.query(MaterialInventory).filter(MaterialInventory.quantity > 0, MaterialInventory.updated_at <= deadline).limit(50).all()
    rows.extend({"risk_level": "中", "type": "库存长期未变化", "item_name": item.material_code or item.source_product_code or item.material, "quantity": item.quantity, "reason": f"超过{days}天未变化"} for item in idle_items)
    return {"answer": "当前智能预警如下。", "data": _table("智能预警", [{"prop": "risk_level", "label": "风险等级"}, {"prop": "type", "label": "风险类型"}, {"prop": "item_name", "label": "对象"}, {"prop": "quantity", "label": "数量"}, {"prop": "reason", "label": "原因"}], rows, "warning_table")}


def run_analysis(message: str, context: str, db: Session) -> dict | None:
    intent = parse_assistant_intent(message, context)
    if intent.get("safety", {}).get("requires_write"):
        return {
            "answer": "智能助手当前只支持查询和分析，不能执行新增、修改、删除、入库、出库或撤销操作。",
            "context": serialize_intent_context(intent),
        }
    if intent.get("intent") in (None, "unknown", "help"):
        return None
    handlers = {
        "inventory_query": inventory_top,
        "inventory_summary": inventory_top,
        "inventory_ranking": inventory_top,
        "inbound_summary": transaction_summary,
        "outbound_summary": transaction_summary,
        "inbound_ranking": transaction_ranking,
        "outbound_ranking": transaction_ranking,
        "transaction_summary": transaction_summary,
        "scrap_idle_analysis": scrap_idle,
        "scrap_ranking": inventory_top,
        "drawing_recent": drawing_recent,
        "drawing_version_ranking": drawing_version_top,
        "loss_analysis": loss_ranking,
        "warning_analysis": warning_list,
        "comparison_analysis": transaction_summary,
    }
    handler = handlers.get(intent["intent"])
    if not handler:
        return None
    result = handler(intent, db)
    next_context = serialize_intent_context(intent)
    record_operation_log(db, "ai_analysis", intent["intent"], None, None, message, after_data={"intent": intent, "title": result.get("data", {}).get("title")})
    return {"answer": result["answer"], "context": next_context, "data": result.get("data")}
