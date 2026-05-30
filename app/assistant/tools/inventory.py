from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.assistant.render import table
from app.assistant.types import AssistantAction, AssistantIntent, AssistantResponse
from app.models import MaterialInventory, ProductDrawing
from app.services.inventory_service import drawing_has_inventory_references


def query_inventory(intent: AssistantIntent, db: Session) -> AssistantResponse:
    entity = intent.get("entity") or "product"
    if entity == "raw_plate":
        return _query_raw_plate(intent, db)
    if entity == "scrap":
        return _query_scrap(intent, db)
    return _query_product(intent, db)


def query_drawings(intent: AssistantIntent, db: Session) -> AssistantResponse:
    keyword = _keyword(intent, "drawing")
    query = db.query(ProductDrawing)
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            (ProductDrawing.product_code.ilike(like))
            | (ProductDrawing.product_name.ilike(like))
            | (ProductDrawing.material.ilike(like))
        )
    drawings = query.order_by(ProductDrawing.updated_at.desc()).limit(50).all()
    rows = []
    for drawing in drawings:
        locked = drawing_has_inventory_references(drawing.id, db)
        rows.append(
            {
                "id": drawing.id,
                "product_code": drawing.product_code or "-",
                "version": f"V{drawing.version or 1}",
                "material": drawing.material or "-",
                "status": "已确认" if drawing.confirmed else "待确认",
                "active": "当前版本" if drawing.is_active else "历史版本",
                "editable": "不可直接修改" if locked else "可修改/重识别/删除",
            }
        )
    return AssistantResponse(
        answer=f"查到 {len(rows)} 张图纸。",
        data=table(
            f"{keyword or '全部'}图纸",
            [
                {"prop": "id", "label": "ID"},
                {"prop": "product_code", "label": "产品型号"},
                {"prop": "version", "label": "版本"},
                {"prop": "material", "label": "材质"},
                {"prop": "status", "label": "状态"},
                {"prop": "active", "label": "版本状态"},
                {"prop": "editable", "label": "可操作性"},
            ],
            rows,
        ),
        actions=[AssistantAction("图纸列表", "/admin/drawings"), AssistantAction("待确认图纸", "/admin/drawings/pending")],
    )


def _query_product(intent: AssistantIntent, db: Session) -> AssistantResponse:
    keyword = _keyword(intent, "product")
    query = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "product", MaterialInventory.quantity > 0)
    if keyword:
        query = _like_inventory(query, keyword)
    items = query.order_by(MaterialInventory.updated_at.desc()).limit(200).all()
    grouped: dict[str, dict] = {}
    for item in items:
        code = item.material_code or item.source_product_code or "未编号"
        group = grouped.setdefault(
            code,
            {"product_code": code, "quantity": 0, "material": item.material, "thickness": item.thickness, "locations": set()},
        )
        group["quantity"] += item.quantity
        if item.location:
            group["locations"].add(item.location)
    rows = [
        {
            "product_code": value["product_code"],
            "quantity": value["quantity"],
            "material": value["material"],
            "thickness": value["thickness"],
            "locations": " / ".join(sorted(value["locations"])) or "-",
        }
        for value in grouped.values()
    ]
    return AssistantResponse(
        answer=f"查到 {len(rows)} 个产品库存汇总。",
        data=table(
            f"{keyword or '全部'}产品库存",
            [
                {"prop": "product_code", "label": "产品型号"},
                {"prop": "quantity", "label": "数量"},
                {"prop": "material", "label": "材质"},
                {"prop": "thickness", "label": "厚度"},
                {"prop": "locations", "label": "库位"},
            ],
            rows,
        ),
        actions=[AssistantAction("产品库存", "/admin/inventory"), AssistantAction("产品出库", "/admin/inventory/outbound")],
    )


def _query_raw_plate(intent: AssistantIntent, db: Session) -> AssistantResponse:
    keyword = _keyword(intent, "raw_plate")
    query = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "raw_plate", MaterialInventory.quantity > 0)
    if keyword:
        query = _like_inventory(query, keyword)
    items = query.order_by(MaterialInventory.created_at.asc()).limit(200).all()
    grouped: dict[tuple, dict] = {}
    for item in items:
        key = (item.material, item.length, item.width, item.thickness)
        group = grouped.setdefault(key, {"quantity": 0, "batches": 0, "locations": set()})
        group["quantity"] += item.quantity
        group["batches"] += 1
        if item.location:
            group["locations"].add(item.location)
    rows = [
        {
            "material": material,
            "size": f"{_fmt(length)}×{_fmt(width)}×{_fmt(thickness)}mm",
            "quantity": value["quantity"],
            "batches": value["batches"],
            "locations": " / ".join(sorted(value["locations"])) or "-",
        }
        for (material, length, width, thickness), value in grouped.items()
    ]
    return AssistantResponse(
        answer=f"查到 {len(rows)} 个板料规格汇总。",
        data=table(
            f"{keyword or '全部'}板料库存",
            [
                {"prop": "material", "label": "材质"},
                {"prop": "size", "label": "规格"},
                {"prop": "quantity", "label": "张数"},
                {"prop": "batches", "label": "批次数"},
                {"prop": "locations", "label": "库位"},
            ],
            rows,
        ),
        actions=[AssistantAction("板料库存", "/admin/raw-plates"), AssistantAction("板料出库", "/admin/raw-plates/outbound")],
    )


def _query_scrap(intent: AssistantIntent, db: Session) -> AssistantResponse:
    keyword = _keyword(intent, "scrap")
    query = db.query(MaterialInventory).filter(
        MaterialInventory.inventory_type == "scrap",
        MaterialInventory.status == "available",
        MaterialInventory.quantity > 0,
    )
    if keyword:
        query = _like_inventory(query, keyword)
    items = query.order_by(MaterialInventory.diameter.asc(), MaterialInventory.created_at.asc()).limit(100).all()
    rows = [
        {
            "material": item.material,
            "thickness": item.thickness,
            "size": item.usable_size or "-",
            "quantity": item.quantity,
            "location": item.location or "-",
            "source": item.source_product_code or "-",
        }
        for item in items
    ]
    return AssistantResponse(
        answer=f"查到 {len(rows)} 条可用余料。",
        data=table(
            f"{keyword or '全部'}可用余料",
            [
                {"prop": "material", "label": "材质"},
                {"prop": "thickness", "label": "厚度"},
                {"prop": "size", "label": "尺寸"},
                {"prop": "quantity", "label": "数量"},
                {"prop": "location", "label": "库位"},
                {"prop": "source", "label": "来源产品"},
            ],
            rows,
        ),
        actions=[AssistantAction("余料记录", "/admin/scraps"), AssistantAction("余料出库", "/admin/scraps/outbound")],
    )


def _keyword(intent: AssistantIntent, domain: str) -> str:
    filters = intent.get("filters") or {}
    explicit = filters.get("product_code") or filters.get("material") or filters.get("location") or filters.get("keyword")
    if explicit:
        return str(explicit).strip()
    message = str(intent.get("_message") or "")
    phrases = (
        "查一下", "查询", "查", "库存", "有没有", "还有多少", "多少", "几个", "几件", "几张",
        "帮我", "请", "一下", "情况", "数量", "统计", "报表", "明细", "列表", "有什么", "有哪些",
    )
    if domain == "raw_plate":
        phrases += ("板料", "钢板", "原料")
    elif domain == "scrap":
        phrases += ("余料",)
    elif domain == "product":
        phrases += ("产品", "型号", "成品")
    elif domain == "drawing":
        phrases += ("图纸",)
    text = message
    for phrase in phrases:
        text = text.replace(phrase, " ")
    return " ".join(part.strip(" ，,。？?：:") for part in text.split() if part.strip(" ，,。？?：:"))


def _like_inventory(query, keyword: str):
    like = f"%{keyword}%"
    return query.filter(
        (MaterialInventory.material_code.ilike(like))
        | (MaterialInventory.material.ilike(like))
        | (MaterialInventory.location.ilike(like))
        | (MaterialInventory.usable_size.ilike(like))
        | (MaterialInventory.source_product_code.ilike(like))
    )


def _fmt(value: float | int | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)

