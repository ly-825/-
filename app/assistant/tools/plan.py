from __future__ import annotations

import re
from urllib.parse import urlencode

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.assistant.render import table
from app.assistant.types import AssistantAction, AssistantIntent, AssistantResponse
from app.models import MaterialInventory, ProductDrawing
from app.services.material_matching import (
    drawing_required_diameter,
    effective_drawing_thickness,
    raw_plate_matches_drawing,
    scrap_matches_drawing,
    scrap_required_diameter,
)


def check_plan_material(intent: AssistantIntent, db: Session) -> AssistantResponse:
    message = str(intent.get("_message") or "")
    quantity = _extract_quantity(message)
    drawings = _find_drawings(intent, db)
    if not drawings:
        return AssistantResponse(
            answer="没有找到符合条件的已确认当前版本图纸。请补充产品型号，或提供材质、厚度、外径、内径、齿数后再查。",
            actions=[AssistantAction("计划管理", "/admin/plans"), AssistantAction("已确认图纸", "/admin/drawings/confirmed")],
        )
    if len(drawings) > 1:
        rows = [_drawing_row(drawing) for drawing in drawings[:20]]
        return AssistantResponse(
            answer=f"找到 {len(drawings)} 张可能的图纸，请先确认要按哪张图纸查料。",
            data=table(
                "可用于计划查料的图纸",
                [
                    {"prop": "id", "label": "图纸ID"},
                    {"prop": "product_code", "label": "产品型号"},
                    {"prop": "product_name", "label": "产品名称"},
                    {"prop": "remark", "label": "备注"},
                    {"prop": "material", "label": "材质"},
                    {"prop": "thickness", "label": "厚度"},
                    {"prop": "outer_diameter", "label": "外径"},
                    {"prop": "inner_diameter", "label": "内径"},
                    {"prop": "teeth_count", "label": "齿数"},
                ],
                rows,
            ),
            actions=[AssistantAction("到计划管理筛选", _plan_filter_url(message, quantity))],
        )

    drawing = drawings[0]
    product_total = _product_total(drawing, db)
    scrap_total = _scrap_total(drawing, db)
    raw_total = _raw_total(drawing, db)
    suggestion = _suggestion(product_total, scrap_total, raw_total, quantity)
    required_scrap_diameter = scrap_required_diameter(drawing)
    required_drawing_diameter = drawing_required_diameter(drawing)
    rows = [
        {
            "category": "成品库存",
            "quantity": product_total,
            "unit": "件",
            "status": "够用" if product_total >= quantity else ("有库存但不足" if product_total else "无库存"),
        },
        {
            "category": "匹配余料",
            "quantity": scrap_total,
            "unit": "件",
            "status": "够用" if scrap_total >= quantity else ("有可用余料但不足" if scrap_total else "无匹配"),
        },
        {
            "category": "匹配板料",
            "quantity": raw_total,
            "unit": "张",
            "status": "有可用板料" if raw_total else "无匹配",
        },
    ]
    answer = (
        f"按 {drawing.product_code or drawing.product_name or '这张图纸'} 计划 {quantity} 件查料："
        f"成品 {product_total} 件，匹配余料 {scrap_total} 件，匹配板料 {raw_total} 张。"
        f"{suggestion}"
    )
    return AssistantResponse(
        answer=answer,
        data=table(
            "计划查料结果",
            [
                {"prop": "category", "label": "类别"},
                {"prop": "quantity", "label": "数量"},
                {"prop": "unit", "label": "单位"},
                {"prop": "status", "label": "状态"},
            ],
            rows,
        ),
        actions=[
            AssistantAction("打开计划管理", _plan_url(drawing.id, quantity)),
            AssistantAction("查看图纸", f"/admin/drawings/{drawing.id}"),
        ],
    )


def _find_drawings(intent: AssistantIntent, db: Session) -> list[ProductDrawing]:
    message = str(intent.get("_message") or "")
    filters = _extract_spec_filters(message)
    keyword = _extract_keyword(intent, message)
    query = db.query(ProductDrawing).filter(ProductDrawing.confirmed == 1, ProductDrawing.is_active == 1)
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            (ProductDrawing.product_code.ilike(like))
            | (ProductDrawing.product_name.ilike(like))
            | (ProductDrawing.remark.ilike(like))
            | (ProductDrawing.material.ilike(like))
        )
    if filters["material"]:
        query = query.filter(ProductDrawing.material.ilike(f"%{filters['material']}%"))
    if filters["thickness"] is not None:
        value = filters["thickness"]
        query = query.filter(
            _number_clause(ProductDrawing.thickness, value)
            | _number_clause(ProductDrawing.product_thickness, value)
            | _number_clause(ProductDrawing.plate_thickness, value)
        )
    if filters["outer_diameter"] is not None:
        query = query.filter(_number_clause(ProductDrawing.max_outer_diameter, filters["outer_diameter"]))
    if filters["inner_diameter"] is not None:
        query = query.filter(_number_clause(ProductDrawing.min_inner_diameter, filters["inner_diameter"]))
    if filters["teeth_count"] is not None:
        query = query.filter(ProductDrawing.teeth_count == filters["teeth_count"])
    if not keyword and not any(value is not None and value != "" for value in filters.values()):
        return []
    return query.order_by(ProductDrawing.product_code.asc(), ProductDrawing.version.desc()).limit(50).all()


def _extract_quantity(message: str) -> int:
    match = re.search(r"(?:数量|计划数量)\s*(?:为|是|=)?\s*(\d+)", message)
    if not match:
        match = re.search(r"(\d+)\s*(?:件|个|套)", message)
    if not match:
        return 1
    try:
        return max(1, int(match.group(1)))
    except ValueError:
        return 1


def _extract_keyword(intent: AssistantIntent, message: str) -> str:
    filters = intent.get("filters") or {}
    for key in ("product_code", "keyword"):
        value = filters.get(key)
        if value:
            return str(value).strip()
    code_match = re.search(r"\b[A-Za-z][A-Za-z0-9_.#-]*\d[A-Za-z0-9_.#-]*\b", message)
    if code_match:
        return code_match.group(0).strip()
    return ""


def _extract_spec_filters(message: str) -> dict[str, float | int | str | None]:
    return {
        "material": _text_after_label(message, ("材质", "材料")),
        "thickness": _number_after_label(message, ("厚度", "板厚", "钢板厚度", "产品厚度")),
        "outer_diameter": _number_after_label(message, ("最大外径", "外径", "外圆")),
        "inner_diameter": _number_after_label(message, ("最小内径", "内径", "内孔")),
        "teeth_count": _int_after_label(message, ("齿数", "齿", "z")),
    }


def _text_after_label(message: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*(?:为|是|=|:|：)?\s*([A-Za-z0-9#_.-]+)", message, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _number_after_label(message: str, labels: tuple[str, ...]) -> float | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*(?:为|是|=|:|：)?\s*(-?\d+(?:\.\d+)?)", message, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _int_after_label(message: str, labels: tuple[str, ...]) -> int | None:
    value = _number_after_label(message, labels)
    return int(value) if value is not None else None


def _number_clause(column, value: float):
    tolerance = 0.001
    return and_(column >= value - tolerance, column <= value + tolerance)


def _product_total(drawing: ProductDrawing, db: Session) -> int:
    if not drawing.product_code:
        return 0
    items = (
        db.query(MaterialInventory)
        .filter(MaterialInventory.inventory_type == "product", MaterialInventory.quantity > 0)
        .all()
    )
    return sum(item.quantity for item in items if item.material_code == drawing.product_code or item.source_product_code == drawing.product_code)


def _scrap_total(drawing: ProductDrawing, db: Session) -> int:
    items = (
        db.query(MaterialInventory)
        .filter(MaterialInventory.inventory_type == "scrap", MaterialInventory.status == "available", MaterialInventory.quantity > 0)
        .all()
    )
    return sum(item.quantity for item in items if scrap_matches_drawing(item, drawing))


def _raw_total(drawing: ProductDrawing, db: Session) -> int:
    items = (
        db.query(MaterialInventory)
        .filter(MaterialInventory.inventory_type == "raw_plate", MaterialInventory.quantity > 0)
        .all()
    )
    return sum(item.quantity for item in items if raw_plate_matches_drawing(item, drawing))


def _suggestion(product_total: int, scrap_total: int, raw_total: int, quantity: int) -> str:
    if product_total >= quantity:
        return "建议优先使用成品库存。"
    if scrap_total >= quantity:
        return "成品不足，建议优先使用匹配余料安排生产。"
    if raw_total > 0:
        return "成品和余料不足，但有匹配板料，可安排板料生产。"
    return "成品、余料和板料都没有匹配到足够材料，建议先采购或入库。"


def _drawing_row(drawing: ProductDrawing) -> dict:
    return {
        "id": drawing.id,
        "product_code": drawing.product_code or "-",
        "product_name": drawing.product_name or "-",
        "remark": drawing.remark or "-",
        "material": drawing.material or "-",
        "thickness": effective_drawing_thickness(drawing) or "-",
        "outer_diameter": drawing.max_outer_diameter or "-",
        "inner_diameter": drawing.min_inner_diameter or "-",
        "teeth_count": drawing.teeth_count or "-",
    }


def _plan_url(drawing_id: int, quantity: int) -> str:
    return "/admin/plans?" + urlencode({"drawing_id": drawing_id, "quantity": quantity})


def _plan_filter_url(message: str, quantity: int) -> str:
    filters = _extract_spec_filters(message)
    params = {"quantity": quantity}
    mapping = {
        "material": "material",
        "thickness": "thickness",
        "outer_diameter": "outer_diameter",
        "inner_diameter": "inner_diameter",
        "teeth_count": "teeth_count",
    }
    for source, target in mapping.items():
        value = filters.get(source)
        if value not in ("", None):
            params[target] = value
    return "/admin/plans?" + urlencode(params)
