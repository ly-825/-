from __future__ import annotations

import re

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.assistant.render import table
from app.assistant.types import AssistantAction, AssistantIntent, AssistantResponse
from app.models import ProductDrawing
from app.services.inventory_service import drawing_has_inventory_references


DRAWING_PARAMETERS: dict[str, dict] = {
    "product_code": {"label": "产品型号", "aliases": ("产品型号", "产品编号", "型号", "编号", "product_code"), "kind": "text"},
    "product_name": {"label": "产品名称", "aliases": ("产品名称", "名称", "product_name"), "kind": "text"},
    "product_category": {"label": "产品分类", "aliases": ("产品分类", "分类", "汽车", "摩托车", "product_category"), "kind": "text"},
    "remark": {"label": "备注", "aliases": ("备注", "备注信息", "说明", "remark"), "kind": "text"},
    "material": {"label": "材质", "aliases": ("材质", "材料", "material"), "kind": "text"},
    "thickness": {"label": "厚度", "aliases": ("厚度", "总厚", "thickness"), "kind": "number"},
    "product_thickness": {"label": "产品厚度", "aliases": ("产品厚度", "总厚度", "复合厚度", "product_thickness"), "kind": "number"},
    "plate_thickness": {"label": "钢板厚度", "aliases": ("钢板厚度", "板厚", "基板厚度", "plate_thickness"), "kind": "number"},
    "max_outer_diameter": {"label": "外径", "aliases": ("外径", "最大外径", "外圆", "max_outer_diameter"), "kind": "number"},
    "min_inner_diameter": {"label": "内径", "aliases": ("内径", "最小内径", "内孔", "min_inner_diameter"), "kind": "number"},
    "bounding_length": {"label": "外框长度", "aliases": ("外框长度", "包围长度", "长度", "bounding_length"), "kind": "number"},
    "bounding_width": {"label": "外框宽度", "aliases": ("外框宽度", "包围宽度", "宽度", "bounding_width"), "kind": "number"},
    "expected_scrap_size": {"label": "中心余料尺寸", "aliases": ("中心余料", "余料尺寸", "中心余料尺寸", "expected_scrap_size"), "kind": "text"},
    "teeth_count": {"label": "齿数", "aliases": ("齿数", "齿", "z", "teeth_count"), "kind": "number"},
    "module": {"label": "模数", "aliases": ("模数", "m", "module"), "kind": "number"},
    "pressure_angle": {"label": "压力角", "aliases": ("压力角", "α", "pressure_angle"), "kind": "number"},
    "profile_shift_coefficient": {"label": "变位系数", "aliases": ("变位系数", "变位", "x", "profile_shift_coefficient"), "kind": "number"},
    "span_teeth_count": {"label": "跨齿数", "aliases": ("跨齿数", "跨齿", "n", "span_teeth_count"), "kind": "number"},
    "common_normal_length": {"label": "公法线长度", "aliases": ("公法线", "公法线长度", "common_normal_length"), "kind": "number"},
    "pin_diameter": {"label": "量棒直径", "aliases": ("量棒直径", "量棒", "pin_diameter"), "kind": "number"},
    "pin_span": {"label": "棒间距", "aliases": ("棒间距", "棒距", "pin_span"), "kind": "number"},
    "version": {"label": "版本", "aliases": ("版本", "version"), "kind": "number"},
    "confirmed": {"label": "确认状态", "aliases": ("确认状态", "是否确认", "confirmed"), "kind": "status"},
    "is_active": {"label": "版本状态", "aliases": ("版本状态", "当前版本", "is_active"), "kind": "status"},
    "created_at": {"label": "创建时间", "aliases": ("创建时间", "上传时间", "created_at"), "kind": "date"},
    "updated_at": {"label": "更新时间", "aliases": ("更新时间", "修改时间", "updated_at"), "kind": "date"},
}


def list_drawings_by_parameter(intent: AssistantIntent, db: Session) -> AssistantResponse:
    message = str(intent.get("_message") or "")
    field = detect_drawing_parameter(message) or "product_code"
    meta = DRAWING_PARAMETERS[field]
    value_keyword = _extract_value_keyword(message, meta["aliases"])
    if field == "product_category":
        for category in ("汽车", "摩托车"):
            if category in message:
                value_keyword = category
                break

    query = db.query(ProductDrawing)
    if value_keyword:
        query = _apply_value_filter(query, field, value_keyword, meta["kind"])
    if meta["kind"] == "number" and field != "thickness":
        query = query.order_by(getattr(ProductDrawing, field).is_(None), getattr(ProductDrawing, field).asc(), ProductDrawing.product_code.asc())
    elif meta["kind"] == "date":
        query = query.order_by(getattr(ProductDrawing, field).desc())
    elif field != "thickness":
        query = query.order_by(getattr(ProductDrawing, field).is_(None), getattr(ProductDrawing, field).asc(), ProductDrawing.product_code.asc())

    drawings = query.limit(300).all()
    if field == "thickness":
        drawings = sorted(drawings, key=lambda drawing: (_drawing_value(drawing, field) is None, _drawing_value(drawing, field) or 0, drawing.product_code or ""))
    rows = []
    for drawing in drawings:
        locked = drawing_has_inventory_references(drawing.id, db)
        rows.append(
            {
                "parameter": meta["label"],
                "value": _display_value(_drawing_value(drawing, field), meta["kind"]),
                "product_code": drawing.product_code or "-",
                "product_category": drawing.product_category or "-",
                "product_name": drawing.product_name or "-",
                "remark": drawing.remark or "-",
                "material": drawing.material or "-",
                "version": f"A{drawing.version or 1}",
                "status": "已确认" if drawing.confirmed else "待确认",
                "editable": "不可直接修改" if locked else "可修改",
                "drawing_id": drawing.id,
            }
        )

    suffix = f"（筛选：{value_keyword}）" if value_keyword else ""
    return AssistantResponse(
        answer=f"已按“{meta['label']}”列出 {len(rows)} 张图纸{suffix}。",
        data=table(
            f"按{meta['label']}列出图纸",
            [
                {"prop": "parameter", "label": "参数"},
                {"prop": "value", "label": "参数值"},
                {"prop": "product_code", "label": "产品型号"},
                {"prop": "product_category", "label": "产品分类"},
                {"prop": "product_name", "label": "产品名称"},
                {"prop": "remark", "label": "备注"},
                {"prop": "material", "label": "材质"},
                {"prop": "version", "label": "版本"},
                {"prop": "status", "label": "确认状态"},
                {"prop": "editable", "label": "可操作性"},
                {"prop": "drawing_id", "label": "图纸ID"},
            ],
            rows,
        ),
        actions=[AssistantAction("图纸列表", "/admin/drawings"), AssistantAction("已确认图纸", "/admin/drawings/confirmed")],
    )


def detect_drawing_parameter(message: str) -> str | None:
    normalized = message.lower().replace(" ", "")
    matches: list[tuple[int, str]] = []
    for field, meta in DRAWING_PARAMETERS.items():
        for alias in meta["aliases"]:
            alias_text = str(alias).lower().replace(" ", "")
            if alias_text and alias_text in normalized:
                matches.append((len(alias_text), field))
    if not matches:
        return None
    return sorted(matches, reverse=True)[0][1]


def _apply_value_filter(query, field: str, keyword: str, kind: str):
    if kind == "number":
        number = _extract_number(keyword)
        if number is not None:
            if field == "thickness":
                return query.filter(
                    _number_clause(ProductDrawing.thickness, number)
                    | _number_clause(ProductDrawing.product_thickness, number)
                    | _number_clause(ProductDrawing.plate_thickness, number)
                )
            column = getattr(ProductDrawing, field)
            return query.filter(_number_clause(column, number))
    column = getattr(ProductDrawing, field)
    if kind == "status":
        if re.search(r"(已确认|确认)", keyword):
            return query.filter(column == 1)
        if re.search(r"(未确认|待确认)", keyword):
            return query.filter(column == 0)
        if re.search(r"(当前|有效)", keyword):
            return query.filter(column == 1)
        if re.search(r"(历史|旧)", keyword):
            return query.filter(column == 0)
    return query.filter(column.ilike(f"%{keyword}%"))


def _extract_value_keyword(message: str, aliases: tuple[str, ...]) -> str:
    text = message
    for phrase in ("按", "根据", "列出", "列表", "呈现", "显示", "并打印", "打印", "图纸", "参数", "所有", "全部", "帮我", "请", "并"):
        text = text.replace(phrase, " ")
    for alias in aliases:
        text = text.replace(str(alias), " ")
    match = re.search(r"(?:为|是|等于|=|大于|小于)\s*([A-Za-z0-9#.\-]+)", message)
    if match:
        return match.group(1).strip()
    return " ".join(part.strip(" ，,。？?：:") for part in text.split() if part.strip(" ，,。？?：:"))


def _extract_number(value: str) -> float | int | None:
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    if not match:
        return None
    number = float(match.group(0))
    return int(number) if number.is_integer() else number


def _display_value(value, kind: str) -> str:
    if value is None:
        return "-"
    if kind == "date":
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else "-"
    if kind == "status":
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _number_clause(column, value: float | int):
    tolerance = 0.0001
    return and_(column >= value - tolerance, column <= value + tolerance)


def _drawing_value(drawing: ProductDrawing, field: str):
    if field == "thickness":
        return drawing.plate_thickness or drawing.product_thickness or drawing.thickness
    return getattr(drawing, field)
