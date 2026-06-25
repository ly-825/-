import html
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from urllib.parse import quote

import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext, layout
from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration
from ezdxf.addons.drawing.svg import SVGBackend
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.assistant.engine import run_assistant
from app.config import settings
from app.database import SessionLocal, get_db
from app.models import InventoryTransactionRecord, MaterialInventory, OperationLog, ProductDrawing, RawPlateSpecification, ScrapGenerationRecord
from app.services.dxf_parser import parse_dxf
from app.services.drawing_preview import generate_drawing_preview
from app.services.drawing_upload import delete_uploaded_drawing, save_uploaded_drawing
from app.services.drawing_version import apply_drawing_version
from app.services.excel_export import build_export_rows, content_disposition, export_filename, log_export, make_workbook_bytes
from app.services.inventory_service import adjust_inventory_quantity, ensure_drawing_can_be_changed, inventory_write_lock, product_inbound_from_drawing, reject_direct_inventory_write, reverse_inventory_transaction, sync_product_inventory_from_drawing
from app.services.material_matching import (
    drawing_required_diameter,
    effective_drawing_thickness,
    raw_plate_matches_drawing,
    scrap_matches_drawing,
    scrap_required_diameter,
)
from app.services.operation_log import drawing_snapshot, inventory_snapshot, record_operation_log
from app.services.product_outbound_analysis import OUTBOUND_PURPOSES, analyze_product_outbound, normalize_outbound_purpose
from app.services.qwen_service import recognize_drawing
from app.services.scrap_service import find_scrap_batches_for_outbound
from app.time_utils import china_now

router = APIRouter()
TOOTH_TYPES = ("IT", "IL", "IR", "OT", "OL", "OR")


def open_local_file(file_path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(file_path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(file_path)])
    else:
        subprocess.Popen(["xdg-open", str(file_path)])


def clean_text_value(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def normalize_tooth_type(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    text = (value or "").strip().upper()
    return text if text in TOOTH_TYPES else None


def tooth_type_options(selected: str | None = None) -> str:
    selected_value = normalize_tooth_type(selected) or ""
    options = [f"<option value='' {'selected' if not selected_value else ''}>未选择</option>"]
    options.extend(
        f"<option value='{value}' {'selected' if value == selected_value else ''}>{value}</option>"
        for value in TOOTH_TYPES
    )
    return "".join(options)


def first_int_value(value: str | None) -> int | None:
    text = value or ""
    match = re.search(r"\d+", text)
    return int(match.group()) if match else None


def display_teeth_count(drawing: ProductDrawing) -> str:
    value = drawing.teeth_count_text or (str(drawing.teeth_count) if drawing.teeth_count is not None else "")
    return f"{drawing.tooth_type or ''}{value}" if value else (drawing.tooth_type or "-")


def display_module(drawing: ProductDrawing) -> str:
    return drawing.module_text or fmt_option(drawing.module) or "-"


def display_common_normal_length(drawing: ProductDrawing) -> str:
    return drawing.common_normal_length_text or fmt_option(drawing.common_normal_length) or "-"


def common_normal_value_from_text(value: str | None, tooth_type: str | None) -> float | None:
    text = value or ""
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        return None
    if len(numbers) == 1:
        return numbers[0]
    normalized_type = normalize_tooth_type(tooth_type) or ""
    if normalized_type.startswith("O"):
        return max(numbers)
    if normalized_type.startswith("I"):
        return min(numbers)
    return numbers[0]


def drawing_version_code(value: ProductDrawing | int | None) -> str:
    version = getattr(value, "version", value)
    return f"A{version or 1}"


def locked_inventory_write():
    with inventory_write_lock():
        yield


def apply_recognition_to_drawing(drawing: ProductDrawing) -> None:
    candidates = parse_dxf(drawing.dxf_file_url)
    recognized = recognize_drawing(candidates)
    gear = candidates.get("gear_candidates", {})
    drawing.product_code = recognized.get("product_code")
    drawing.product_name = recognized.get("product_name")
    drawing.material = recognized.get("material")
    drawing.thickness = recognized.get("thickness") or recognized.get("product_thickness") or recognized.get("plate_thickness")
    drawing.max_outer_diameter = recognized.get("max_outer_diameter")
    drawing.min_inner_diameter = recognized.get("inner_related_diameter")
    drawing.bounding_length = recognized.get("bounding_length") or candidates.get("geometry_summary", {}).get("bounding_box", {}).get("width")
    drawing.bounding_width = recognized.get("bounding_width") or candidates.get("geometry_summary", {}).get("bounding_box", {}).get("height")
    drawing.expected_scrap_size = recognized.get("expected_scrap_usable_size")
    drawing.product_thickness = recognized.get("product_thickness") or gear.get("product_thickness")
    drawing.plate_thickness = recognized.get("plate_thickness") or gear.get("plate_thickness")
    drawing.teeth_count = recognized.get("teeth_count") or gear.get("teeth_count")
    drawing.tooth_type = normalize_tooth_type(recognized.get("tooth_type") or gear.get("tooth_type"))
    drawing.teeth_count_text = clean_text_value(recognized.get("teeth_count_text") or gear.get("teeth_count_text") or drawing.teeth_count)
    drawing.module = recognized.get("module") or gear.get("module")
    drawing.module_text = clean_text_value(recognized.get("module_text") or gear.get("module_text") or drawing.module)
    drawing.pressure_angle = recognized.get("pressure_angle") or gear.get("pressure_angle")
    drawing.profile_shift_coefficient = recognized.get("profile_shift_coefficient") or gear.get("profile_shift_coefficient")
    drawing.span_teeth_count = recognized.get("span_teeth_count") or gear.get("span_teeth_count")
    drawing.common_normal_length = recognized.get("common_normal_length") or gear.get("common_normal_length")
    drawing.common_normal_length_text = clean_text_value(
        recognized.get("common_normal_length_text")
        or gear.get("common_normal_length_text")
        or drawing.common_normal_length
    )
    drawing.common_normal_length = common_normal_value_from_text(drawing.common_normal_length_text, drawing.tooth_type) or drawing.common_normal_length
    drawing.pin_diameter = recognized.get("pin_diameter") or gear.get("pin_diameter")
    drawing.pin_span = recognized.get("pin_span") or gear.get("pin_span")
    drawing.parse_result_json = {"candidates": candidates, "recognized": recognized}
    drawing.parse_status = "parsed"
    drawing.confirmed = 0


def optional_float(value: str) -> float | None:
    try:
        return float(value) if value.strip() else None
    except ValueError:
        return None


def optional_int(value: str) -> int | None:
    try:
        return int(value) if value not in ("", None) else None
    except (TypeError, ValueError):
        return None


def export_link(module: str, params: dict[str, str]) -> str:
    query = build_query(params)
    return f"/admin/exports/{module}{'?' + query if query else ''}"


def build_query(params: dict[str, object]) -> str:
    return "&".join(
        f"{quote(str(key), safe='')}={quote(str(value), safe='')}"
        for key, value in params.items()
        if value not in ("", None)
    )


def confirmed_drawing_options(db: Session, selected_id: int | None = None, include_blank: bool = False) -> str:
    drawings = (
        db.query(ProductDrawing)
        .filter(ProductDrawing.confirmed == 1, ProductDrawing.is_active == 1)
        .order_by(ProductDrawing.product_code.asc(), ProductDrawing.version.desc())
        .all()
    )
    options = "".join(
        f"<option value='{drawing.id}' {'selected' if selected_id == drawing.id else ''}>{html.escape(drawing.product_code or '-')}｜{html.escape(drawing.product_category or '-')}｜{drawing_version_code(drawing)}｜{html.escape(drawing.product_name or '-')}｜{html.escape(drawing.material or '-')}｜厚度 {drawing.plate_thickness or drawing.product_thickness or drawing.thickness or '-'}</option>"
        for drawing in drawings
    )
    if include_blank:
        options = f"<option value='' {'selected' if selected_id is None else ''}>按图纸自动匹配</option>" + options
    return options or "<option value='' disabled selected>暂无已确认图纸，请先确认图纸</option>"


def drawing_version_label(db: Session, drawing_id: int | None) -> str:
    if not drawing_id:
        return "-"
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        return "来源图纸已不存在"
    status = "当前" if drawing.is_active else "历史"
    return f"{drawing.product_code or '-'} {drawing_version_code(drawing)}（{status}）"


def safe_value(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def fmt_option(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def select_options(values: list[object], selected: str = "", blank_label: str = "全部") -> str:
    selected_text = selected.strip()
    options = [f"<option value='' {'selected' if not selected_text else ''}>{html.escape(blank_label)}</option>"]
    seen = set()
    for value in values:
        text = fmt_option(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        options.append(f"<option value='{safe_value(text)}' {'selected' if selected_text == text else ''}>{html.escape(text)}</option>")
    return "".join(options)


def datalist_options(values: list[object]) -> str:
    options = []
    seen = set()
    for value in values:
        text = fmt_option(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        options.append(f"<option value='{safe_value(text)}'></option>")
    return "".join(options)


def inventory_distinct_options(db: Session, inventory_type: str, field: str, quantity_positive: bool = False, status: str | None = None) -> list[object]:
    column = getattr(MaterialInventory, field)
    query = db.query(column).filter(MaterialInventory.inventory_type == inventory_type)
    if quantity_positive:
        query = query.filter(MaterialInventory.quantity > 0)
    if status:
        query = query.filter(MaterialInventory.status == status)
    values = [row[0] for row in query.distinct().order_by(column.asc()).all()]
    return [value for value in values if value not in ("", None)]


def transaction_customer_options(db: Session) -> list[object]:
    values = [
        row[0]
        for row in (
            db.query(InventoryTransactionRecord.customer_name)
            .filter(InventoryTransactionRecord.customer_name.isnot(None))
            .distinct()
            .order_by(InventoryTransactionRecord.customer_name.asc())
            .all()
        )
    ]
    return [value for value in values if value not in ("", None)]


def drawing_distinct_options(db: Session, field: str, confirmed_only: bool = True) -> list[object]:
    column = getattr(ProductDrawing, field)
    query = db.query(column)
    if confirmed_only:
        query = query.filter(ProductDrawing.confirmed == 1, ProductDrawing.is_active == 1)
    values = [row[0] for row in query.distinct().order_by(column.asc()).all()]
    return [value for value in values if value not in ("", None)]


def float_between_filter(column, value: float, tolerance: float = 0.001):
    return column.between(value - tolerance, value + tolerance)


def apply_drawing_filters(
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
    thickness_value = optional_float(thickness)
    if thickness_value is not None:
        query = query.filter(
            float_between_filter(ProductDrawing.thickness, thickness_value)
            | float_between_filter(ProductDrawing.product_thickness, thickness_value)
            | float_between_filter(ProductDrawing.plate_thickness, thickness_value)
        )
    product_thickness_value = optional_float(product_thickness)
    if product_thickness_value is not None:
        query = query.filter(float_between_filter(ProductDrawing.product_thickness, product_thickness_value))
    plate_thickness_value = optional_float(plate_thickness)
    if plate_thickness_value is not None:
        query = query.filter(float_between_filter(ProductDrawing.plate_thickness, plate_thickness_value))
    outer_value = optional_float(outer_diameter)
    if outer_value is not None:
        query = query.filter(float_between_filter(ProductDrawing.max_outer_diameter, outer_value))
    inner_value = optional_float(inner_diameter)
    if inner_value is not None:
        query = query.filter(float_between_filter(ProductDrawing.min_inner_diameter, inner_value))
    teeth_text = teeth_count.strip()
    if teeth_text:
        teeth_value = optional_int(teeth_text)
        like = f"%{teeth_text}%"
        if teeth_value is not None:
            query = query.filter((ProductDrawing.teeth_count == teeth_value) | (ProductDrawing.teeth_count_text.ilike(like)))
        else:
            query = query.filter((ProductDrawing.teeth_count_text.ilike(like)) | (ProductDrawing.tooth_type.ilike(like)))
    module_text = module.strip()
    if module_text:
        module_value = optional_float(module_text)
        like = f"%{module_text}%"
        if module_value is not None:
            query = query.filter(float_between_filter(ProductDrawing.module, module_value) | ProductDrawing.module_text.ilike(like))
        else:
            query = query.filter(ProductDrawing.module_text.ilike(like))
    pressure_angle_value = optional_float(pressure_angle)
    if pressure_angle_value is not None:
        query = query.filter(float_between_filter(ProductDrawing.pressure_angle, pressure_angle_value))
    common_normal_length_text = common_normal_length.strip()
    if common_normal_length_text:
        common_normal_length_value = optional_float(common_normal_length_text)
        like = f"%{common_normal_length_text}%"
        if common_normal_length_value is not None:
            query = query.filter(float_between_filter(ProductDrawing.common_normal_length, common_normal_length_value) | ProductDrawing.common_normal_length_text.ilike(like))
        else:
            query = query.filter(ProductDrawing.common_normal_length_text.ilike(like))
    pin_diameter_value = optional_float(pin_diameter)
    if pin_diameter_value is not None:
        query = query.filter(float_between_filter(ProductDrawing.pin_diameter, pin_diameter_value))
    pin_span_value = optional_float(pin_span)
    if pin_span_value is not None:
        query = query.filter(float_between_filter(ProductDrawing.pin_span, pin_span_value))
    return query


def filtered_plan_drawings(
    db: Session,
    q: str = "",
    material: str = "",
    thickness: str = "",
    outer_diameter: str = "",
    inner_diameter: str = "",
    teeth_count: str = "",
) -> list[ProductDrawing]:
    query = db.query(ProductDrawing).filter(ProductDrawing.confirmed == 1, ProductDrawing.is_active == 1)
    query = apply_drawing_filters(
        query,
        q=q,
        material=material,
        thickness=thickness,
        outer_diameter=outer_diameter,
        inner_diameter=inner_diameter,
        teeth_count=teeth_count,
    )
    return query.order_by(ProductDrawing.product_code.asc(), ProductDrawing.version.desc()).all()


def plan_product_options(db: Session, selected_id: int | None = None, drawings: list[ProductDrawing] | None = None) -> str:
    if drawings is None:
        drawings = filtered_plan_drawings(db)
    if selected_id and all(drawing.id != selected_id for drawing in drawings):
        selected_drawing = (
            db.query(ProductDrawing)
            .filter(ProductDrawing.id == selected_id, ProductDrawing.confirmed == 1, ProductDrawing.is_active == 1)
            .first()
        )
        if selected_drawing:
            drawings = [selected_drawing] + drawings
    if not drawings:
        return "<option value='' disabled selected>暂无匹配图纸</option>"
    options = [f"<option value='' {'selected' if selected_id is None else ''}>请选择产品种类</option>"]
    for drawing in drawings:
        label = (
            f"{drawing.product_code or '-'}｜{drawing.product_category or '-'}｜{drawing.product_name or '-'}｜{drawing.material or '-'}"
            f"｜厚度 {effective_drawing_thickness(drawing) or '-'}"
            f"｜外径 {drawing.max_outer_diameter or '-'}"
            f"｜内径 {drawing.min_inner_diameter or '-'}"
            f"｜齿数 {drawing.teeth_count or '-'}"
        )
        options.append(f"<option value='{drawing.id}' {'selected' if selected_id == drawing.id else ''}>{html.escape(label)}</option>")
    return "".join(options)


def plan_drawing_rows(drawings: list[ProductDrawing], quantity_value: int, filters: dict[str, str]) -> str:
    rows = []
    for drawing in drawings:
        params = {**filters, "drawing_id": drawing.id, "quantity": quantity_value}
        href = f"/admin/plans?{build_query(params)}"
        rows.append(
            f"""
            <tr>
              <td>{html.escape(drawing.product_code or '-')}</td>
              <td>{drawing_version_code(drawing)}</td>
              <td>{html.escape(drawing.product_name or '-')}</td>
              <td>{html.escape(drawing.material or '-')}</td>
              <td>{effective_drawing_thickness(drawing) or '-'}</td>
              <td>{drawing.max_outer_diameter or '-'}</td>
              <td>{drawing.min_inner_diameter or '-'}</td>
              <td>{drawing.teeth_count or '-'}</td>
              <td><a class="btn secondary" href="{href}">用此图纸查询</a></td>
            </tr>
            """
        )
    return "".join(rows)


def raw_plate_low_stock_groups(db: Session) -> list[dict]:
    threshold = max(0, int(settings.raw_plate_low_stock_threshold))
    groups: dict[tuple, dict] = {}
    raw_items = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "raw_plate").all()
    for item in raw_items:
        key = (item.material, item.length, item.width, item.thickness)
        group = groups.setdefault(
            key,
            {
                "spec_name": "",
                "material": item.material,
                "length": item.length,
                "width": item.width,
                "thickness": item.thickness,
                "quantity": 0,
                "locations": set(),
            },
        )
        group["quantity"] += item.quantity
        if item.location:
            group["locations"].add(item.location)
    for spec in db.query(RawPlateSpecification).filter(RawPlateSpecification.is_active == 1).all():
        key = (spec.material, spec.length, spec.width, spec.thickness)
        if key in groups and spec.spec_name:
            groups[key]["spec_name"] = spec.spec_name
    low_groups = [group for group in groups.values() if group["quantity"] <= threshold]
    low_groups.sort(key=lambda group: (group["quantity"], str(group["material"]), group["thickness"] or 0, group["length"] or 0, group["width"] or 0))
    return low_groups


def raw_plate_low_stock_banner() -> str:
    try:
        with SessionLocal() as db:
            groups = raw_plate_low_stock_groups(db)
    except Exception:
        return ""
    if not groups:
        return ""
    threshold = max(0, int(settings.raw_plate_low_stock_threshold))
    visible_groups = groups[:4]
    parts = []
    for group in visible_groups:
        spec_size = f"{group['material']} {fmt_option(group['length'])}×{fmt_option(group['width'])}×{fmt_option(group['thickness'])}mm"
        spec = f"{group['spec_name']}（{spec_size}）" if group.get("spec_name") else spec_size
        parts.append(f"{html.escape(str(spec))} 仅剩 {group['quantity']} 张")
    more = f" 等 {len(groups)} 个规格" if len(groups) > len(visible_groups) else ""
    dismiss_key = "|".join(
        f"{group['material']}:{fmt_option(group['length'])}:{fmt_option(group['width'])}:{fmt_option(group['thickness'])}:{group['quantity']}"
        for group in groups
    )
    return f"""
    <div class="announcement-bar" role="status" data-announcement-key="{safe_value(f'raw-plate-low-stock:{threshold}:{dismiss_key}')}">
      <strong>板料采购提醒</strong>
      <span>{'；'.join(parts)}{more}，已低于 {threshold} 张，需要尽快采购或入库。</span>
      <a href="/admin/raw-plates">查看库存</a>
      <a href="/admin/raw-plates/inbound">板料入库</a>
      <button class="announcement-close" type="button" aria-label="关闭板料采购提醒" title="关闭">×</button>
    </div>
    """


def scrub_internal_ids(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: scrub_internal_ids(item)
            for key, item in value.items()
            if key != "id" and not key.endswith("_id") and key != "file_hash"
        }
    if isinstance(value, list):
        return [scrub_internal_ids(item) for item in value]
    return value


def render_dxf_svg(file_path: str) -> str:
    doc = ezdxf.readfile(file_path)
    backend = SVGBackend()
    context = RenderContext(doc)
    config = Configuration(background_policy=BackgroundPolicy.WHITE)
    Frontend(context, backend, config=config).draw_layout(doc.modelspace())
    page_layout = layout.Page(420, 297, layout.Units.mm, margins=layout.Margins.all(6))
    settings = layout.Settings(fit_page=True, fixed_stroke_width=0.18, output_coordinate_space=1_000_000)
    svg = backend.get_string(page_layout, settings=settings, xml_declaration=False)
    return svg.replace(
        "<svg ",
        '<svg class="cad-preview-svg" style="width:100%;height:78vh;display:block;background:#fff;border:1px solid var(--line);border-radius:18px" ',
        1,
    )


def assistant_widget() -> str:
    return """
    <div id="global-assistant" class="assistant-widget" data-open="false">
      <button id="assistant-launcher" class="assistant-launcher" type="button" aria-label="打开智能助手" title="智能助手">
        <svg xmlns="http://www.w3.org/2000/svg" width="25" height="25" viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6V2H8m7 9v2M2 12h2m16 0h2m-2 4a2 2 0 0 1-2 2H8.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 4 20.286V8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2zM9 11v2"/></svg>
      </button>
      <section id="assistant-panel" class="assistant-panel" aria-hidden="true" aria-label="智能助手">
        <div class="assistant-panel-head">
          <div class="assistant-title">
            <span class="assistant-avatar" aria-hidden="true">
              <svg xmlns="http://www.w3.org/2000/svg" width="21" height="21" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6V2H8m7 9v2M2 12h2m16 0h2m-2 4a2 2 0 0 1-2 2H8.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 4 20.286V8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2zM9 11v2"/></svg>
            </span>
            <div>
              <strong>智能助手</strong>
              <span>只读查询</span>
            </div>
          </div>
          <div class="assistant-head-actions">
            <button id="assistant-clear" class="assistant-icon-btn" type="button" title="清空对话">清空</button>
            <button id="assistant-close" class="assistant-icon-btn" type="button" aria-label="关闭智能助手">&times;</button>
          </div>
        </div>
        <div id="assistant-messages" class="assistant-messages"></div>
        <div class="assistant-prompts" aria-label="快捷问题">
          <button type="button" data-assistant-question="查一下产品库存">成品</button>
          <button type="button" data-assistant-question="查一下板料库存">板料</button>
          <button type="button" data-assistant-question="查一下余料库存">余料</button>
          <button type="button" data-assistant-question="今天出库明细">出库</button>
          <button type="button" data-assistant-question="查一下库存预警">预警</button>
          <button type="button" data-assistant-question="你能做什么">帮助</button>
        </div>
        <form id="assistant-form" class="assistant-form">
          <input id="assistant-input" placeholder="问库存、图纸、计划查料、流水或规则" autocomplete="off">
          <button id="assistant-send" type="submit">发送</button>
        </form>
      </section>
    </div>
    <script>
      (() => {
        if (window.inventoryAssistant) return;
        const root = document.getElementById('global-assistant');
        const launcher = document.getElementById('assistant-launcher');
        const panel = document.getElementById('assistant-panel');
        const closeButton = document.getElementById('assistant-close');
        const clearButton = document.getElementById('assistant-clear');
        const messages = document.getElementById('assistant-messages');
        const form = document.getElementById('assistant-form');
        const input = document.getElementById('assistant-input');
        const sendButton = document.getElementById('assistant-send');
        const historyKey = 'inventoryAssistantMessages';
        const contextKey = 'inventoryAssistantContext';
        let assistantContext = localStorage.getItem(contextKey) || '';
        let assistantHistory = loadHistory();
        let isSending = false;

        function loadHistory() {
          try {
            const value = JSON.parse(localStorage.getItem(historyKey) || '[]');
            return Array.isArray(value) ? value : [];
          } catch (error) {
            return [];
          }
        }

        function escapeHtml(text) {
          return String(text || '').replace(/[&<>"']/g, (char) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
          }[char]));
        }

        function saveAssistantState() {
          localStorage.setItem(historyKey, JSON.stringify(assistantHistory.slice(-60)));
          localStorage.setItem(contextKey, assistantContext || '');
        }

        function setOpen(open) {
          root.dataset.open = open ? 'true' : 'false';
          panel.setAttribute('aria-hidden', open ? 'false' : 'true');
          if (open) {
            window.setTimeout(() => input.focus(), 120);
          }
        }

        function renderAnalysisData(data) {
          if (!data || !Array.isArray(data.columns) || !Array.isArray(data.rows)) return '';
          const header = data.columns.map((column) => `<th>${escapeHtml(column.label || column.prop)}</th>`).join('');
          const body = data.rows.map((row) => {
            return `<tr>${data.columns.map((column) => `<td>${escapeHtml(row[column.prop])}</td>`).join('')}</tr>`;
          }).join('');
          return `
            <div class="assistant-data-table">
              <div class="assistant-data-head">
                <strong>${escapeHtml(data.title || '分析结果')}</strong>
                <div>
                  <button class="assistant-mini-btn export-analysis" type="button">导出</button>
                  <button class="assistant-mini-btn print-analysis" type="button">打印</button>
                </div>
              </div>
              <div class="assistant-table-wrap"><table><thead><tr>${header}</tr></thead><tbody>${body || `<tr><td colspan="${data.columns.length}">暂无分析数据。</td></tr>`}</tbody></table></div>
            </div>
          `;
        }

        function renderActions(actions) {
          if (!Array.isArray(actions) || !actions.length) return '';
          return `<div class="assistant-actions">${actions.map((action) => `<a href="${escapeHtml(action.url)}">${escapeHtml(action.label)}</a>`).join('')}</div>`;
        }

        function appendMessage(role, text, data, actions, options = {}) {
          const block = document.createElement('div');
          const isUser = role === '你';
          block.className = `assistant-message ${isUser ? 'is-user' : 'is-assistant'}${options.loading ? ' is-loading' : ''}`;
          block.innerHTML = `
            <div class="assistant-message-role">${escapeHtml(role)}</div>
            <div class="assistant-bubble">${escapeHtml(text)}</div>
            ${renderAnalysisData(data)}
            ${renderActions(actions)}
          `;
          const exportButton = block.querySelector('.export-analysis');
          if (exportButton && data) {
            exportButton.addEventListener('click', () => exportAnalysisData(data));
          }
          const printButton = block.querySelector('.print-analysis');
          if (printButton && data) {
            printButton.addEventListener('click', () => printAnalysisData(data));
          }
          messages.appendChild(block);
          messages.scrollTop = messages.scrollHeight;
          return block;
        }

        function rememberMessage(role, text, data, actions) {
          assistantHistory.push({role, text, data, actions});
          saveAssistantState();
        }

        function clearAssistantHistory() {
          assistantContext = '';
          assistantHistory = [];
          messages.innerHTML = '';
          const text = '对话已清空，可以重新开始查询。';
          appendMessage('助手', text);
          rememberMessage('助手', text);
        }

        async function askAssistant(text) {
          const message = (text || input.value || '').trim();
          if (!message || isSending) return;
          setOpen(true);
          input.value = '';
          appendMessage('你', message);
          rememberMessage('你', message);
          isSending = true;
          sendButton.disabled = true;
          input.disabled = true;
          const loadingBlock = appendMessage('助手', '正在查询...', null, null, {loading: true});
          try {
            const response = await fetch('/admin/assistant/chat', {
              method: 'POST',
              headers: {'Content-Type': 'application/x-www-form-urlencoded'},
              body: new URLSearchParams({message, context: assistantContext})
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            assistantContext = data.context || assistantContext;
            const answer = data.answer || data.detail || '没有返回结果';
            loadingBlock.remove();
            appendMessage('助手', answer, data.data, data.actions);
            rememberMessage('助手', answer, data.data, data.actions);
          } catch (error) {
            loadingBlock.remove();
            const answer = '查询失败，请稍后重试，或换一种更明确的问法。';
            appendMessage('助手', answer);
            rememberMessage('助手', answer);
          } finally {
            isSending = false;
            sendButton.disabled = false;
            input.disabled = false;
            input.focus();
          }
        }

        function printAnalysisData(data) {
          const columns = Array.isArray(data.columns) ? data.columns : [];
          const rows = Array.isArray(data.rows) ? data.rows : [];
          const header = columns.map((column) => `<th>${escapeHtml(column.label || column.prop)}</th>`).join('');
          const body = rows.map((row) => `<tr>${columns.map((column) => `<td>${escapeHtml(row[column.prop])}</td>`).join('')}</tr>`).join('');
          const printWindow = window.open('', '_blank');
          if (!printWindow) {
            alert('浏览器阻止了打印窗口，请允许弹出窗口后重试。');
            return;
          }
          printWindow.document.write(`
            <!doctype html>
            <html>
            <head>
              <meta charset="utf-8">
              <title>${escapeHtml(data.title || '打印结果')}</title>
              <style>
                body { font-family: Arial, 'Microsoft YaHei', sans-serif; margin: 24px; color: #111827; }
                h1 { font-size: 20px; margin: 0 0 14px; }
                table { width: 100%; border-collapse: collapse; font-size: 12px; }
                th, td { border: 1px solid #d1d5db; padding: 7px 8px; text-align: left; vertical-align: top; }
                th { background: #eef2f7; font-weight: 700; }
                @media print { body { margin: 12mm; } }
              </style>
            </head>
            <body>
              <h1>${escapeHtml(data.title || '打印结果')}</h1>
              <table><thead><tr>${header}</tr></thead><tbody>${body || `<tr><td colspan="${columns.length}">暂无数据。</td></tr>`}</tbody></table>
            </body>
            </html>
          `);
          printWindow.document.close();
          printWindow.focus();
          printWindow.print();
        }

        async function exportAnalysisData(data) {
          const response = await fetch('/admin/assistant/analysis/export', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({title: data.title, columns: data.columns, rows: data.rows})
          });
          if (!response.ok) {
            alert('导出失败，请稍后重试。');
            return;
          }
          const blob = await response.blob();
          const url = URL.createObjectURL(blob);
          const link = document.createElement('a');
          link.href = url;
          link.download = `${data.title || 'AI分析结果'}.xlsx`;
          link.click();
          URL.revokeObjectURL(url);
        }

        launcher.addEventListener('click', () => setOpen(true));
        closeButton.addEventListener('click', () => setOpen(false));
        clearButton.addEventListener('click', clearAssistantHistory);
        form.addEventListener('submit', (event) => {
          event.preventDefault();
          askAssistant();
        });
        root.addEventListener('click', (event) => {
          const promptButton = event.target.closest('[data-assistant-question]');
          if (promptButton) askAssistant(promptButton.dataset.assistantQuestion || '');
        });
        document.addEventListener('keydown', (event) => {
          if (event.key === 'Escape' && root.dataset.open === 'true') setOpen(false);
        });

        if (assistantHistory.length) {
          assistantHistory.forEach((item) => appendMessage(item.role, item.text, item.data, item.actions));
        } else {
          const welcome = '你好，我可以查库存、图纸、计划用料、流水、预警和规则。';
          appendMessage('助手', welcome);
          rememberMessage('助手', welcome);
        }

        window.inventoryAssistant = {
          open: () => setOpen(true),
          close: () => setOpen(false),
          ask: askAssistant,
          clear: clearAssistantHistory
        };
      })();
    </script>
    """


def page(title: str, body: str, notice: str = "") -> HTMLResponse:
    notice_script = ""
    if notice == "confirmed":
        notice_script = """
  <script>
    window.addEventListener('DOMContentLoaded', () => {
      alert('更新成功');
    });
  </script>
        """
    html = f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}｜杭州特耐时</title>
  <style>
    :root {{ --bg:#f5f7fb; --card:#fff; --text:#172033; --muted:#667085; --primary:#1d4ed8; --line:#e5eaf3; --danger:#dc2626; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; background:var(--bg); color:var(--text); }}
    a {{ color:inherit; text-decoration:none; }}
    .layout {{ display:grid; grid-template-columns:220px 1fr; min-height:100vh; }}
    aside {{ background:#0f1f46; color:white; padding:20px 14px; max-height:100vh; overflow:auto; position:sticky; top:0; }}
    .brand {{ font-size:17px; font-weight:800; margin:0 8px 12px; }}
    .nav-current {{ margin:0 6px 14px; padding:10px 12px; border-radius:10px; background:rgba(147,197,253,.14); color:#dbeafe; border:1px solid rgba(147,197,253,.24); }}
    .nav-current span {{ display:block; font-size:11px; color:rgba(219,234,254,.62); margin-bottom:4px; }}
    .nav-current strong {{ display:block; font-size:13px; line-height:1.35; font-weight:800; }}
    nav a {{ display:block; padding:8px 10px; border-radius:8px; color:rgba(255,255,255,.68); margin-bottom:3px; font-size:13px; font-weight:600; line-height:1.35; }}
    nav a:hover, nav a.active {{ background:rgba(255,255,255,.08); color:white; }}
    nav a.active {{ box-shadow:inset 2px 0 0 #93c5fd; }}
    .nav-section {{ margin:6px 0; }}
    .nav-section summary {{ list-style:none; display:flex; align-items:center; justify-content:space-between; min-height:36px; padding:8px 10px; border-radius:9px; color:rgba(255,255,255,.58); font-size:12px; font-weight:800; letter-spacing:0; cursor:pointer; user-select:none; }}
    .nav-section summary::-webkit-details-marker {{ display:none; }}
    .nav-section summary::after {{ content:"⌄"; font-size:14px; color:rgba(255,255,255,.62); transition:transform .18s ease; }}
    .nav-section:not([open]) summary::after {{ transform:rotate(-90deg); }}
    .nav-section summary:hover {{ background:rgba(255,255,255,.08); color:rgba(255,255,255,.86); }}
    .nav-section .nav-items {{ padding:2px 0 4px 8px; border-left:1px solid rgba(255,255,255,.10); margin-left:10px; }}
    .nav-section .nav-items a {{ padding:7px 9px; font-size:12px; font-weight:600; color:rgba(255,255,255,.66); }}
    .nav-section .nav-items a.active {{ background:rgba(147,197,253,.18); color:white; }}
    .nav-subhead {{ display:block; margin:9px 9px 5px; padding-top:9px; border-top:1px solid rgba(255,255,255,.12); color:rgba(219,234,254,.58); font-size:11px; font-weight:900; }}
    .nav-root {{ margin-bottom:10px; }}
    main {{ padding:28px; }}
    .top {{ display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:22px; }}
    h1 {{ margin:0; font-size:28px; }}
    .muted {{ color:var(--muted); }}
    .announcement-bar {{ display:flex; align-items:center; gap:12px; margin:0 0 18px; padding:12px 14px; border:1px solid #fed7aa; border-radius:14px; background:#fff7ed; color:#7c2d12; box-shadow:0 10px 24px rgba(124,45,18,.08); }}
    .announcement-bar strong {{ flex:0 0 auto; font-size:14px; }}
    .announcement-bar span {{ flex:1; min-width:0; font-size:13px; line-height:1.5; }}
    .announcement-bar a {{ flex:0 0 auto; display:inline-flex; align-items:center; justify-content:center; height:30px; padding:0 10px; border-radius:9px; background:#ffedd5; color:#9a3412; font-size:12px; font-weight:800; }}
    .announcement-bar a:hover {{ background:#fed7aa; }}
    .announcement-close {{ flex:0 0 auto; width:30px; height:30px; border:1px solid #fed7aa; border-radius:9px; background:#fff7ed; color:#9a3412; font-size:18px; line-height:1; font-weight:900; cursor:pointer; }}
    .announcement-close:hover {{ background:#fed7aa; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:20px; padding:20px; box-shadow:0 12px 34px rgba(20,32,55,.06); margin-bottom:18px; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; }}
    .stat strong {{ display:block; font-size:30px; margin-top:8px; }}
    .home-shell {{ display:grid; gap:18px; }}
    .workbench-head {{ position:relative; overflow:hidden; display:flex; justify-content:space-between; gap:20px; align-items:flex-start; padding:24px; border-radius:24px; background:linear-gradient(135deg,#14213a 0%,#213f5d 62%,#5a5548 100%); color:#f8fbff; box-shadow:0 24px 54px rgba(20,32,55,.18); }}
    .workbench-head::after {{ content:""; position:absolute; inset:0; opacity:.12; background:repeating-linear-gradient(90deg,transparent 0 32px,rgba(255,255,255,.28) 33px,transparent 34px),repeating-linear-gradient(0deg,transparent 0 32px,rgba(255,255,255,.18) 33px,transparent 34px); pointer-events:none; }}
    .workbench-head > * {{ position:relative; z-index:1; }}
    .workbench-kicker {{ display:inline-flex; align-items:center; min-height:28px; padding:0 10px; border:1px solid rgba(255,255,255,.20); border-radius:999px; background:rgba(255,255,255,.10); color:#dbeafe; font-size:12px; font-weight:800; margin-bottom:12px; }}
    .workbench-head h1 {{ color:#fff; font-size:32px; line-height:1.18; margin:0 0 8px; text-wrap:balance; }}
    .workbench-head p {{ margin:0; max-width:640px; color:rgba(248,251,255,.76); line-height:1.65; }}
    .workbench-date {{ min-width:168px; padding:12px 14px; border:1px solid rgba(255,255,255,.18); border-radius:16px; background:rgba(255,255,255,.10); text-align:right; }}
    .workbench-date span {{ display:block; color:rgba(248,251,255,.68); font-size:12px; margin-bottom:4px; }}
    .workbench-date strong {{ display:block; font-size:16px; }}
    .workbench-layout {{ display:grid; grid-template-columns:minmax(0,1.35fr) minmax(320px,.65fr); gap:18px; align-items:start; }}
    .home-panel {{ border:1px solid var(--line); border-radius:22px; background:#ffffff; box-shadow:0 14px 34px rgba(20,32,55,.07); overflow:hidden; }}
    .home-panel-head {{ display:flex; justify-content:space-between; align-items:flex-start; gap:14px; padding:18px 20px; border-bottom:1px solid var(--line); background:#fbfcff; }}
    .home-panel-head h2 {{ margin:0; font-size:18px; }}
    .home-panel-head p {{ margin:6px 0 0; color:var(--muted); font-size:13px; line-height:1.5; }}
    .home-panel-count {{ min-width:74px; text-align:right; }}
    .home-panel-count span {{ display:block; color:var(--muted); font-size:12px; margin-bottom:3px; }}
    .home-panel-count strong {{ display:block; font-size:30px; line-height:1; font-variant-numeric:tabular-nums; }}
    .task-list {{ padding:8px; }}
    .task-row {{ display:grid; grid-template-columns:44px minmax(0,1fr) auto; align-items:center; gap:12px; padding:13px 12px; border:1px solid transparent; border-radius:15px; transition:background .18s ease,border-color .18s ease,transform .18s ease; }}
    .task-row:hover {{ background:#f6f8fc; border-color:#dbe3f1; transform:translateY(-1px); }}
    .task-row:active {{ transform:translateY(0); }}
    .task-row.is-warning {{ background:#fff7ed; border-color:#fed7aa; }}
    .task-row.is-warning:hover {{ background:#ffedd5; }}
    .task-icon {{ width:44px; height:44px; display:grid; place-items:center; border-radius:14px; background:#edf3ff; color:#1d4ed8; font-weight:900; }}
    .task-row.is-warning .task-icon {{ background:#ffedd5; color:#9a3412; }}
    .task-main strong {{ display:block; font-size:15px; margin-bottom:4px; }}
    .task-main span {{ display:block; color:var(--muted); font-size:12px; line-height:1.45; }}
    .task-number {{ min-width:54px; text-align:right; font-size:28px; font-weight:900; font-variant-numeric:tabular-nums; color:#172033; }}
    .task-row.is-warning .task-number {{ color:#9a3412; }}
    .quick-action-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; padding:0 20px 20px; }}
    .quick-action {{ display:flex; min-height:54px; align-items:center; justify-content:space-between; gap:8px; padding:10px 12px; border:1px solid #dbe3f1; border-radius:15px; background:#f8fbff; color:#172033; font-size:13px; font-weight:800; transition:background .18s ease,border-color .18s ease,transform .18s ease; }}
    .quick-action:hover {{ background:#eef4ff; border-color:#bfcef3; transform:translateY(-1px); }}
    .quick-action span {{ color:#1d4ed8; font-size:15px; }}
    .inventory-overview {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }}
    .inventory-tile {{ position:relative; overflow:hidden; min-height:150px; padding:18px; border:1px solid var(--line); border-radius:20px; background:#fff; box-shadow:0 12px 30px rgba(20,32,55,.06); }}
    .inventory-tile::after {{ content:""; position:absolute; right:-34px; bottom:-42px; width:120px; height:120px; border-radius:36px; background:#eef4ff; transform:rotate(18deg); }}
    .inventory-tile.raw::after {{ background:#fff0d5; }}
    .inventory-tile.scrap::after {{ background:#e9f8ef; }}
    .inventory-tile > * {{ position:relative; z-index:1; }}
    .inventory-tile span {{ color:var(--muted); font-size:13px; font-weight:700; }}
    .inventory-tile strong {{ display:block; margin:12px 0 8px; font-size:34px; line-height:1; font-variant-numeric:tabular-nums; }}
    .inventory-tile p {{ margin:0; color:#667085; font-size:12px; line-height:1.5; }}
    .inventory-tile a {{ display:inline-flex; margin-top:14px; color:#1d4ed8; font-size:13px; font-weight:800; }}
    .flow-summary {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; padding:16px 20px 0; }}
    .flow-metric {{ padding:13px 14px; border-radius:16px; background:#f8fbff; border:1px solid #dbe3f1; }}
    .flow-metric span {{ display:block; color:var(--muted); font-size:12px; margin-bottom:8px; }}
    .flow-metric strong {{ display:block; font-size:26px; line-height:1; font-variant-numeric:tabular-nums; }}
    .movement-list {{ padding:12px 20px 20px; display:grid; gap:8px; }}
    .movement-row {{ display:grid; grid-template-columns:auto minmax(0,1fr) auto; align-items:center; gap:10px; min-height:48px; padding:9px 0; border-bottom:1px solid #edf1f7; }}
    .movement-row:last-child {{ border-bottom:0; }}
    .movement-type {{ display:inline-flex; align-items:center; justify-content:center; min-width:42px; height:26px; padding:0 8px; border-radius:9px; background:#eef2ff; color:#1d4ed8; font-size:12px; font-weight:900; }}
    .movement-type.is-out {{ background:#fee2e2; color:#b91c1c; }}
    .movement-type.is-in,.movement-type.is-confirm {{ background:#dcfce7; color:#166534; }}
    .movement-main {{ min-width:0; }}
    .movement-main strong {{ display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:13px; }}
    .movement-main span {{ display:block; color:var(--muted); font-size:12px; margin-top:3px; }}
    .movement-qty {{ font-weight:900; font-variant-numeric:tabular-nums; }}
    .risk-list {{ padding:12px 20px 20px; display:grid; gap:10px; }}
    .risk-item,.risk-ok {{ padding:12px 13px; border-radius:15px; border:1px solid #fed7aa; background:#fff7ed; color:#7c2d12; }}
    .risk-item strong,.risk-ok strong {{ display:block; font-size:13px; margin-bottom:5px; }}
    .risk-item span,.risk-ok span {{ display:block; font-size:12px; line-height:1.45; color:#9a3412; }}
    .risk-ok {{ border-color:#bbf7d0; background:#f0fdf4; color:#166534; }}
    .risk-ok span {{ color:#15803d; }}
    table {{ width:100%; min-width:0; border-collapse:separate; border-spacing:0; table-layout:auto; }}
    th,td {{ padding:10px 8px; border-bottom:1px solid var(--line); text-align:left; font-size:13px; vertical-align:middle; overflow-wrap:anywhere; }}
    th {{ color:var(--muted); font-weight:800; background:#fbfcff; position:sticky; top:0; z-index:1; white-space:nowrap; }}
    tbody tr:nth-child(even) td {{ background:#fcfdff; }}
    tbody tr:hover td {{ background:#f6f8fc; }}
    .table-scroll {{ overflow:auto; max-height:68vh; border:1px solid var(--line); border-radius:16px; }}
    .table-scroll table {{ margin:0; min-width:0; }}
    .table-scroll tr:last-child td {{ border-bottom:0; }}
    .num-col {{ text-align:right; font-variant-numeric:tabular-nums; }}
    .action-col {{ position:sticky; right:0; z-index:2; min-width:116px; background:#fff; box-shadow:-10px 0 18px rgba(20,32,55,.05); }}
    th.action-col {{ z-index:4; background:#fbfcff; }}
    tbody tr:nth-child(even) td.action-col {{ background:#fcfdff; }}
    tbody tr:hover td.action-col {{ background:#f6f8fc; }}
    .cell-clip {{ max-width:220px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .status-pill {{ display:inline-flex; align-items:center; justify-content:center; min-height:26px; padding:0 9px; border-radius:999px; background:#eef2ff; color:#1d4ed8; font-size:12px; font-weight:900; white-space:nowrap; }}
    .status-pill.is-ok {{ background:#dcfce7; color:#166534; }}
    .status-pill.is-warn {{ background:#ffedd5; color:#9a3412; }}
    .status-pill.is-muted {{ background:#edf1f7; color:#475569; }}
    .confirm-hint {{ grid-column:1/-1; margin:0; padding:10px 12px; border:1px solid #dbe3f1; border-radius:14px; background:#f8fbff; color:#475569; font-size:13px; line-height:1.55; }}
    .confirm-overlay {{ position:fixed; inset:0; z-index:120; display:none; align-items:center; justify-content:center; padding:24px; background:rgba(15,23,42,.34); }}
    .confirm-overlay.is-open {{ display:flex; }}
    .confirm-dialog {{ width:min(560px, 100%); overflow:hidden; border:1px solid #dbe3f1; border-radius:20px; background:#fff; box-shadow:0 28px 70px rgba(15,23,42,.28); }}
    .confirm-dialog-head {{ display:flex; justify-content:space-between; gap:14px; padding:18px 20px; border-bottom:1px solid var(--line); background:#fbfcff; }}
    .confirm-dialog-head h2 {{ margin:0; font-size:18px; }}
    .confirm-dialog-head p {{ margin:6px 0 0; color:var(--muted); font-size:13px; line-height:1.5; }}
    .confirm-close {{ width:34px; height:34px; border:1px solid var(--line); border-radius:10px; background:#fff; color:#334155; cursor:pointer; font-size:20px; font-weight:900; }}
    .confirm-list {{ display:grid; gap:8px; padding:16px 20px; max-height:48vh; overflow:auto; }}
    .confirm-line {{ display:grid; grid-template-columns:132px minmax(0,1fr); gap:12px; align-items:start; padding:10px 0; border-bottom:1px solid #edf1f7; }}
    .confirm-line:last-child {{ border-bottom:0; }}
    .confirm-line span {{ color:var(--muted); font-size:13px; }}
    .confirm-line strong {{ color:#172033; font-size:14px; line-height:1.45; word-break:break-word; }}
    .confirm-actions {{ display:flex; justify-content:flex-end; gap:10px; padding:14px 20px 18px; border-top:1px solid var(--line); background:#fbfcff; }}
    .form-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }}
    label {{ display:block; font-size:13px; color:var(--muted); margin-bottom:6px; }}
    input,select,textarea {{ width:100%; border:1px solid var(--line); border-radius:12px; background:white; font:inherit; }}
    input,select {{ height:42px; padding:0 12px; }}
    .inline-input-group {{ display:flex; width:100%; }}
    .inline-input-group > select {{ flex:0 0 106px; border-top-right-radius:0; border-bottom-right-radius:0; border-right:0; }}
    .inline-input-group > input {{ flex:1 1 auto; min-width:0; border-top-left-radius:0; border-bottom-left-radius:0; }}
    textarea {{ min-height:86px; padding:10px 12px; resize:vertical; line-height:1.5; }}
    .btn {{ display:inline-flex; align-items:center; justify-content:center; height:42px; padding:0 16px; border-radius:12px; border:none; background:var(--primary); color:white; font-weight:700; cursor:pointer; }}
    .btn.secondary {{ background:#eef2ff; color:var(--primary); }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; }}
    .badge {{ display:inline-block; padding:4px 9px; border-radius:999px; background:#eef2ff; color:#1d4ed8; font-size:12px; font-weight:700; }}
    .assistant-launcher {{ position:fixed; right:24px; bottom:24px; z-index:80; width:58px; height:58px; border:0; border-radius:18px; display:grid; place-items:center; color:white; background:#1d4ed8; box-shadow:0 18px 36px rgba(29,78,216,.28), 0 2px 8px rgba(20,32,55,.16); cursor:pointer; transition:transform .18s ease, box-shadow .18s ease, opacity .18s ease; }}
    .assistant-launcher:hover {{ transform:translateY(-2px); box-shadow:0 22px 42px rgba(29,78,216,.34), 0 2px 8px rgba(20,32,55,.18); }}
    .assistant-widget[data-open="true"] .assistant-launcher {{ opacity:0; pointer-events:none; transform:translateY(8px) scale(.94); }}
    .assistant-panel {{ position:fixed; right:24px; bottom:94px; z-index:90; width:min(440px, calc(100vw - 32px)); max-height:min(680px, calc(100vh - 116px)); display:flex; flex-direction:column; overflow:hidden; background:#fbfcff; border:1px solid #dbe3f1; border-radius:18px; box-shadow:0 26px 64px rgba(20,32,55,.20); opacity:0; pointer-events:none; transform:translateY(14px) scale(.98); transform-origin:bottom right; transition:opacity .18s ease, transform .18s ease; }}
    .assistant-widget[data-open="true"] .assistant-panel {{ opacity:1; pointer-events:auto; transform:translateY(0) scale(1); }}
    .assistant-panel-head {{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 14px; border-bottom:1px solid var(--line); background:#f6f8fc; }}
    .assistant-title {{ display:flex; align-items:center; gap:10px; min-width:0; }}
    .assistant-title strong {{ display:block; font-size:14px; line-height:1.2; }}
    .assistant-title span {{ display:block; font-size:12px; color:var(--muted); margin-top:2px; }}
    .assistant-avatar {{ width:34px; height:34px; display:grid; place-items:center; border-radius:12px; color:#1d4ed8; background:#eaf1ff; border:1px solid #d7e4ff; }}
    .assistant-head-actions {{ display:flex; align-items:center; gap:6px; }}
    .assistant-icon-btn {{ min-width:34px; height:34px; border:1px solid var(--line); border-radius:10px; background:white; color:#334155; cursor:pointer; font-weight:700; }}
    .assistant-icon-btn:hover {{ background:#eef4ff; color:#1d4ed8; }}
    .assistant-messages {{ flex:1; min-height:260px; max-height:430px; overflow:auto; padding:14px; background:#f8fbff; }}
    .assistant-message {{ margin:0 0 12px; }}
    .assistant-message.is-user {{ text-align:right; }}
    .assistant-message-role {{ margin-bottom:5px; color:var(--muted); font-size:12px; font-weight:700; }}
    .assistant-bubble {{ display:inline-block; max-width:100%; text-align:left; white-space:pre-wrap; word-break:break-word; border-radius:14px; padding:10px 12px; font-size:13px; line-height:1.55; background:#0f172a; color:#dbeafe; }}
    .assistant-message.is-user .assistant-bubble {{ background:#eaf1ff; color:#172033; }}
    .assistant-message.is-loading .assistant-bubble {{ color:#667085; background:#eef2f7; }}
    .assistant-data-table {{ margin-top:8px; text-align:left; border:1px solid var(--line); border-radius:14px; background:white; overflow:hidden; }}
    .assistant-data-head {{ display:flex; align-items:center; justify-content:space-between; gap:10px; padding:10px 10px 8px; border-bottom:1px solid var(--line); }}
    .assistant-data-head strong {{ font-size:13px; }}
    .assistant-mini-btn {{ height:28px; padding:0 9px; border-radius:8px; border:1px solid #dbe3f1; background:#f8fbff; color:#1d4ed8; font-weight:700; cursor:pointer; }}
    .assistant-table-wrap {{ overflow:auto; max-height:230px; }}
    .assistant-table-wrap table {{ min-width:620px; }}
    .assistant-table-wrap th,.assistant-table-wrap td {{ padding:8px; font-size:12px; }}
    .assistant-actions {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }}
    .assistant-actions a {{ display:inline-flex; align-items:center; min-height:30px; padding:0 10px; border-radius:9px; background:#eef2ff; color:#1d4ed8; font-size:12px; font-weight:700; }}
    .assistant-prompts {{ display:flex; gap:7px; padding:10px 12px 0; flex-wrap:wrap; background:#fbfcff; border-top:1px solid var(--line); }}
    .assistant-prompts button {{ height:30px; padding:0 10px; border:1px solid #dbe3f1; border-radius:999px; background:white; color:#334155; font-weight:700; font-size:12px; cursor:pointer; }}
    .assistant-prompts button:hover {{ border-color:#bfcef3; color:#1d4ed8; background:#f3f6ff; }}
    .assistant-form {{ display:flex; gap:8px; padding:10px 12px 12px; background:#fbfcff; }}
    .assistant-form input {{ flex:1; min-width:0; height:40px; border-radius:12px; }}
    .assistant-form button {{ width:64px; height:40px; border:0; border-radius:12px; background:#1d4ed8; color:white; font-weight:800; cursor:pointer; }}
    .assistant-form button:disabled {{ opacity:.55; cursor:not-allowed; }}
    .dropzone {{ border:2px dashed #b8c4dc; border-radius:18px; background:#f8fbff; padding:34px; text-align:center; transition:.2s ease; cursor:pointer; }}
    .dropzone:hover,.dropzone.dragover {{ border-color:var(--primary); background:#eef4ff; transform:translateY(-1px); }}
    .dropzone strong {{ display:block; font-size:18px; margin-bottom:8px; }}
    .dropzone span {{ color:var(--muted); }}
    .file-name {{ margin-top:12px; color:var(--primary); font-weight:700; }}
    .hidden-file {{ position:absolute; width:1px; height:1px; opacity:0; pointer-events:none; }}
    pre {{ white-space:pre-wrap; word-break:break-all; background:#0f172a; color:#dbeafe; padding:16px; border-radius:14px; overflow:auto; }}
    @media (max-width:1100px) {{ .workbench-layout,.inventory-overview {{ grid-template-columns:1fr; }} }}
    @media (max-width:900px) {{ .layout {{ grid-template-columns:1fr; }} aside {{ position:static; max-height:none; }} .grid,.form-grid,.quick-action-grid,.flow-summary {{ grid-template-columns:1fr; }} .workbench-head {{ flex-direction:column; }} .workbench-date {{ width:100%; text-align:left; }} .announcement-bar {{ align-items:flex-start; flex-direction:column; }} .announcement-bar a,.announcement-close {{ width:100%; }} .assistant-launcher {{ right:16px; bottom:16px; }} .assistant-panel {{ right:16px; bottom:84px; max-height:calc(100vh - 104px); }} }}
  </style>
</head>
<body>
  <div class="layout">
    <aside id="admin-sidebar">
      <div class="brand">杭州特耐时</div>
      <div id="nav-current" class="nav-current"><span>当前位置</span><strong>后台首页</strong></div>
      <nav>
        <div class="nav-root">
          <a href="/admin">后台首页</a>
        </div>
        <details class="nav-section" data-nav-section="drawing">
          <summary>图纸管理</summary>
          <div class="nav-items">
            <a href="/admin/drawings">图纸识别</a>
            <a href="/admin/drawings/pending">待确认图纸</a>
            <a href="/admin/drawings/confirmed">已确认图纸</a>
          </div>
        </details>
        <div class="nav-root">
          <a href="/admin/plans">计划管理</a>
        </div>
        <details class="nav-section" data-nav-section="material">
          <summary>材料管理</summary>
          <div class="nav-items">
            <a href="/admin/raw-plate-specifications">板料规格</a>
            <a href="/admin/raw-plates/inbound">板料入库</a>
            <a href="/admin/raw-plates/outbound">板料出库</a>
            <a href="/admin/raw-plates">板料库存</a>
            <a href="/admin/raw-plates/transactions">板料流水</a>
            <span class="nav-subhead">余料</span>
            <a href="/admin/scraps/pending">待入库余料</a>
            <a href="/admin/scraps/outbound">余料出库</a>
            <a href="/admin/scraps">余料库存</a>
            <a href="/admin/scraps/transactions">余料流水</a>
          </div>
        </details>
        <details class="nav-section" data-nav-section="finished-product">
          <summary>成品管理</summary>
          <div class="nav-items">
            <a href="/admin/inventory">成品库存</a>
            <a href="/admin/inventory/inbound">成品入库</a>
            <a href="/admin/inventory/outbound">成品出库</a>
            <a href="/admin/inventory/transactions">成品流水</a>
            <a href="/admin/reports/product-outbound">产品出库分析</a>
          </div>
        </details>
      </nav>
    </aside>
    <main>{raw_plate_low_stock_banner()}{body}</main>
  </div>
  {assistant_widget()}
  <script>
    document.querySelectorAll('.card > table').forEach((table) => {{
      const wrapper = document.createElement('div');
      wrapper.className = 'table-scroll';
      table.parentNode.insertBefore(wrapper, table);
      wrapper.appendChild(table);
    }});
    document.querySelectorAll('.table-scroll table').forEach((table) => {{
      const headers = Array.from(table.querySelectorAll('thead th'));
      const numericWords = ['数量','总数','块数','批次数','厚','长','宽','直径','重量','库存','入库','出库','操作前','操作后'];
      headers.forEach((header, index) => {{
        const label = header.textContent.trim();
        const cells = [header, ...Array.from(table.querySelectorAll(`tbody tr td:nth-child(${{index + 1}})`))];
        if (label.includes('操作')) cells.forEach((cell) => cell.classList.add('action-col'));
        if (numericWords.some((word) => label.includes(word))) cells.forEach((cell) => cell.classList.add('num-col'));
      }});
      const statusMap = {{
        'available': 'is-ok',
        'used': 'is-muted',
        'pending': 'is-warn',
        '启用': 'is-ok',
        '停用': 'is-muted',
        '当前': 'is-ok',
        '历史': 'is-muted',
        '已确认': 'is-ok',
        '待确认': 'is-warn',
        'parsed': 'is-ok',
        'pending_parse': 'is-warn'
      }};
      table.querySelectorAll('tbody td').forEach((cell) => {{
        if (cell.querySelector('a,button,input,select,textarea,form')) return;
        const text = cell.textContent.trim();
        if (Object.prototype.hasOwnProperty.call(statusMap, text)) {{
          cell.textContent = '';
          const pill = document.createElement('span');
          pill.className = `status-pill ${{statusMap[text]}}`;
          pill.textContent = text;
          cell.appendChild(pill);
          return;
        }}
        if (text.length > 18) {{
          cell.classList.add('cell-clip');
          cell.title = text;
        }}
      }});
    }});
    document.querySelectorAll('[data-select-filter]').forEach((input) => {{
      const select = document.getElementById(input.dataset.selectFilter || '');
      if (!select) return;
      Array.from(select.options).forEach((option) => {{
        option.dataset.originalDisabled = option.disabled ? '1' : '0';
      }});
      const applySelectFilter = () => {{
        const words = input.value.trim().toLowerCase().split(/\\s+/).filter(Boolean);
        let firstVisible = null;
        Array.from(select.options).forEach((option) => {{
          const originalDisabled = option.dataset.originalDisabled === '1';
          const text = option.textContent.trim().toLowerCase();
          const visible = !words.length || words.every((word) => text.includes(word));
          option.hidden = !visible;
          option.disabled = originalDisabled || !visible;
          if (visible && !originalDisabled && !firstVisible) firstVisible = option;
        }});
        const selectedOption = select.selectedOptions[0];
        if ((!selectedOption || selectedOption.hidden || selectedOption.disabled) && firstVisible) {{
          select.value = firstVisible.value;
        }}
      }};
      input.addEventListener('input', applySelectFilter);
      applySelectFilter();
    }});
    const confirmOverlay = document.createElement('div');
    confirmOverlay.className = 'confirm-overlay';
    confirmOverlay.innerHTML = `
      <section class="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title">
        <div class="confirm-dialog-head">
          <div><h2 id="confirm-dialog-title">确认提交</h2><p id="confirm-dialog-note">请核对本次操作信息。</p></div>
          <button class="confirm-close" type="button" aria-label="关闭确认">&times;</button>
        </div>
        <div class="confirm-list"></div>
        <div class="confirm-actions">
          <button class="btn secondary" type="button" data-confirm-cancel>返回修改</button>
          <button class="btn" type="button" data-confirm-submit>确认提交</button>
        </div>
      </section>
    `;
    document.body.appendChild(confirmOverlay);
    let pendingConfirmForm = null;
    const closeConfirm = () => {{
      confirmOverlay.classList.remove('is-open');
      pendingConfirmForm = null;
    }};
    confirmOverlay.querySelector('.confirm-close').addEventListener('click', closeConfirm);
    confirmOverlay.querySelector('[data-confirm-cancel]').addEventListener('click', closeConfirm);
    confirmOverlay.addEventListener('click', (event) => {{
      if (event.target === confirmOverlay) closeConfirm();
    }});
    confirmOverlay.querySelector('[data-confirm-submit]').addEventListener('click', () => {{
      if (!pendingConfirmForm) return;
      pendingConfirmForm.dataset.confirmReady = '1';
      pendingConfirmForm.requestSubmit();
    }});
    function fieldLabel(field) {{
      const wrapLabel = field.closest('div') ? field.closest('div').querySelector('label') : null;
      if (wrapLabel) return wrapLabel.textContent.trim();
      if (field.id) {{
        const directLabel = document.querySelector(`label[for="${{field.id}}"]`);
        if (directLabel) return directLabel.textContent.trim();
      }}
      return field.getAttribute('aria-label') || field.getAttribute('placeholder') || field.name || '字段';
    }}
    function fieldValue(field) {{
      if (field.tagName === 'SELECT') {{
        const selected = Array.from(field.selectedOptions).map((option) => option.textContent.trim()).filter(Boolean).join('，');
        return selected || field.value || '未选择';
      }}
      const value = field.value ? field.value.trim() : '';
      return value || '未填写';
    }}
    document.querySelectorAll('form[data-confirm-flow="true"]').forEach((form) => {{
      form.addEventListener('submit', (event) => {{
        if (form.dataset.confirmReady === '1') return;
        event.preventDefault();
        if (!form.reportValidity()) return;
        pendingConfirmForm = form;
        const rows = [];
        form.querySelectorAll('input,select,textarea').forEach((field) => {{
          if (field.type === 'hidden' || field.type === 'submit' || field.type === 'button') return;
          if (field.name === 'client_request_id') return;
          const label = fieldLabel(field);
          if (!label || label === '字段') return;
          rows.push([label, fieldValue(field)]);
        }});
        confirmOverlay.querySelector('#confirm-dialog-title').textContent = form.dataset.confirmTitle || '确认提交';
        confirmOverlay.querySelector('#confirm-dialog-note').textContent = form.dataset.confirmNote || '请核对本次操作信息，提交后会写入库存流水。';
        const list = confirmOverlay.querySelector('.confirm-list');
        list.textContent = '';
        rows.forEach(([label, value]) => {{
          const line = document.createElement('div');
          line.className = 'confirm-line';
          const left = document.createElement('span');
          left.textContent = label;
          const right = document.createElement('strong');
          right.textContent = value;
          line.append(left, right);
          list.appendChild(line);
        }});
        confirmOverlay.classList.add('is-open');
      }});
    }});
    const adminSidebar = document.getElementById('admin-sidebar');
    if (adminSidebar) {{
      const savedScroll = sessionStorage.getItem('adminSidebarScrollTop');
      if (savedScroll) adminSidebar.scrollTop = Number(savedScroll) || 0;
      adminSidebar.addEventListener('scroll', () => {{
        sessionStorage.setItem('adminSidebarScrollTop', String(adminSidebar.scrollTop));
      }});
      const currentPath = window.location.pathname;
      let bestLink = null;
      document.querySelectorAll('nav a[href]').forEach((link) => {{
        const href = link.getAttribute('href');
        if (href === currentPath || (href !== '/admin' && currentPath.startsWith(href + '/'))) {{
          if (!bestLink || href.length > bestLink.getAttribute('href').length) bestLink = link;
        }}
      }});
      if (bestLink) bestLink.classList.add('active');
      const currentBox = document.getElementById('nav-current');
      if (currentBox && bestLink) {{
        const section = bestLink.closest('.nav-section');
        const groupName = section ? section.querySelector('summary').textContent.trim() : '常用';
        currentBox.querySelector('strong').textContent = `${{groupName}} / ${{bestLink.textContent.trim()}}`;
      }}
      document.querySelectorAll('.nav-section').forEach((section) => {{
        const hasActiveLink = section.querySelector('a.active');
        section.open = Boolean(hasActiveLink);
        section.addEventListener('toggle', () => {{
          if (section.open) {{
            document.querySelectorAll('.nav-section').forEach((other) => {{
              if (other !== section) other.open = false;
            }});
          }}
        }});
      }});
    }}
    document.querySelectorAll('.announcement-bar[data-announcement-key]').forEach((bar) => {{
      const key = `dismissed:${{bar.dataset.announcementKey || ''}}`;
      try {{
        if (localStorage.getItem(key) === '1') {{
          bar.remove();
          return;
        }}
      }} catch (error) {{}}
      const closeButton = bar.querySelector('.announcement-close');
      if (closeButton) {{
        closeButton.addEventListener('click', () => {{
          try {{
            localStorage.setItem(key, '1');
          }} catch (error) {{}}
          bar.remove();
        }});
      }}
    }});
  </script>
  {notice_script}
</body>
</html>
    """
    return HTMLResponse(html)


@router.get("/admin", response_class=HTMLResponse)
def admin_home(db: Session = Depends(get_db)) -> HTMLResponse:
    pending_drawing_count = db.query(ProductDrawing).filter(ProductDrawing.confirmed == 0).count()
    pending_scrap_count = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "scrap", MaterialInventory.status == "pending").count()
    product_items = (
        db.query(MaterialInventory)
        .filter(MaterialInventory.inventory_type == "product", MaterialInventory.status == "available", MaterialInventory.quantity > 0)
        .all()
    )
    raw_plate_items = (
        db.query(MaterialInventory)
        .filter(MaterialInventory.inventory_type == "raw_plate", MaterialInventory.status == "available", MaterialInventory.quantity > 0)
        .all()
    )
    scrap_items = (
        db.query(MaterialInventory)
        .filter(MaterialInventory.inventory_type == "scrap", MaterialInventory.status == "available", MaterialInventory.quantity > 0)
        .all()
    )
    product_total = sum(int(item.quantity or 0) for item in product_items)
    raw_plate_total = sum(int(item.quantity or 0) for item in raw_plate_items)
    scrap_total = sum(int(item.quantity or 0) for item in scrap_items)
    product_kinds = len({item.material_code or item.source_product_code or item.id for item in product_items})
    raw_plate_specs = len({(item.material, item.length, item.width, item.thickness) for item in raw_plate_items})
    scrap_specs = len({(item.material, item.thickness, item.usable_size or item.diameter) for item in scrap_items})
    low_groups = raw_plate_low_stock_groups(db)
    total_task_count = pending_drawing_count + pending_scrap_count + len(low_groups)
    today_start, today_end, _ = outbound_report_range("day", "", "")
    today_records = (
        db.query(InventoryTransactionRecord)
        .filter(
            InventoryTransactionRecord.reversed_transaction_id.is_(None),
            InventoryTransactionRecord.created_at >= today_start,
            InventoryTransactionRecord.created_at < today_end,
        )
        .all()
    )
    def record_quantity(record: InventoryTransactionRecord) -> int:
        if record.transaction_type == "confirm" and record.quantity == 0:
            return int(record.after_quantity or 0)
        return int(record.quantity or 0)
    today_in_total = sum(record_quantity(record) for record in today_records if record.transaction_type in ("in", "confirm"))
    today_out_total = sum(record_quantity(record) for record in today_records if record.transaction_type == "out")
    recent_records = (
        db.query(InventoryTransactionRecord)
        .filter(InventoryTransactionRecord.reversed_transaction_id.is_(None))
        .order_by(InventoryTransactionRecord.created_at.desc())
        .limit(6)
        .all()
    )
    inventory_ids = [record.inventory_id for record in recent_records]
    inventory_map = {
        item.id: item
        for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()
    } if inventory_ids else {}

    def inventory_brief(item: MaterialInventory | None) -> str:
        if not item:
            return "库存记录已不存在"
        if item.inventory_type == "product":
            return item.material_code or item.source_product_code or "成品库存"
        if item.inventory_type == "raw_plate":
            return f"{item.material} {fmt_option(item.length)}×{fmt_option(item.width)}×{fmt_option(item.thickness)}mm"
        return f"{item.material} {item.usable_size or fmt_option(item.diameter)}"

    movement_rows = ""
    for record in recent_records:
        item = inventory_map.get(record.inventory_id)
        type_class = {"out": "is-out", "in": "is-in", "confirm": "is-confirm"}.get(record.transaction_type, "")
        created = record.created_at.strftime("%m-%d %H:%M") if record.created_at else "-"
        movement_rows += f"""
        <div class="movement-row">
          <span class="movement-type {type_class}">{transaction_label(record.transaction_type)}</span>
          <div class="movement-main"><strong>{html.escape(inventory_brief(item))}</strong><span>{created}｜{html.escape(record.operator_name or "未填操作人")}</span></div>
          <strong class="movement-qty">{record_quantity(record)}</strong>
        </div>
        """
    if not movement_rows:
        movement_rows = "<div class='movement-row'><span class='movement-type'>暂无</span><div class='movement-main'><strong>还没有库存流水</strong><span>入库、出库后这里会自动更新。</span></div><strong class='movement-qty'>0</strong></div>"

    risk_rows = ""
    for group in low_groups[:4]:
        spec_size = f"{group['material']} {fmt_option(group['length'])}×{fmt_option(group['width'])}×{fmt_option(group['thickness'])}mm"
        spec = f"{group['spec_name']}（{spec_size}）" if group.get("spec_name") else spec_size
        locations = "、".join(sorted(group["locations"])) if group["locations"] else "未填写库位"
        risk_rows += f"<div class='risk-item'><strong>{html.escape(str(spec))}</strong><span>仅剩 {group['quantity']} 张，库位：{html.escape(locations)}</span></div>"
    if not risk_rows:
        risk_rows = f"<div class='risk-ok'><strong>板料库存暂时正常</strong><span>低于 {settings.raw_plate_low_stock_threshold} 张时，系统会在顶部挂采购提醒。</span></div>"

    today_label = china_now().strftime("%Y-%m-%d")
    body = f"""
    <div class="home-shell">
      <header class="workbench-head">
        <div>
          <span class="workbench-kicker">今日工作台</span>
          <h1>待办、库存风险和出入库情况集中看。</h1>
          <p>把每天最常处理的图纸确认、余料入库、计划查料、板料风险和出库统计放到首页，减少来回翻菜单。</p>
        </div>
        <div class="workbench-date"><span>当前日期</span><strong>{today_label}</strong></div>
      </header>

      <section class="workbench-layout">
        <section class="home-panel">
          <div class="home-panel-head">
            <div><h2>需要处理</h2><p>优先看有数量的项目，点击后直接进入对应页面。</p></div>
            <div class="home-panel-count"><span>合计</span><strong>{total_task_count}</strong></div>
          </div>
          <div class="task-list">
            <a class="task-row" href="/admin/drawings/pending">
              <span class="task-icon">图</span>
              <span class="task-main"><strong>待确认图纸</strong><span>上传识别后还没有确认的图纸，需要确认规格和版本。</span></span>
              <strong class="task-number">{pending_drawing_count}</strong>
            </a>
            <a class="task-row" href="/admin/scraps/pending">
              <span class="task-icon">余</span>
              <span class="task-main"><strong>待入库余料</strong><span>生产后登记但还没有确认入库的余料。</span></span>
              <strong class="task-number">{pending_scrap_count}</strong>
            </a>
            <a class="task-row {'is-warning' if low_groups else ''}" href="/admin/raw-plates">
              <span class="task-icon">板</span>
              <span class="task-main"><strong>板料库存风险</strong><span>{'已有规格低于预警线，需要采购或补录入库。' if low_groups else '当前没有低于预警线的板料规格。'}</span></span>
              <strong class="task-number">{len(low_groups)}</strong>
            </a>
          </div>
          <div class="quick-action-grid">
            <a class="quick-action" href="/admin/plans">计划查料<span>›</span></a>
            <a class="quick-action" href="/admin/raw-plates/inbound">板料入库<span>›</span></a>
            <a class="quick-action" href="/admin/inventory/outbound">成品出库<span>›</span></a>
            <a class="quick-action" href="/admin/drawings">上传图纸<span>›</span></a>
            <a class="quick-action" href="/admin/reports/outbound">综合出库统计<span>›</span></a>
            <a class="quick-action" href="/admin/reports/product-outbound">产品出库分析<span>›</span></a>
            <a class="quick-action" href="/admin/scraps/outbound">余料出库<span>›</span></a>
          </div>
        </section>

        <section class="home-panel">
          <div class="home-panel-head">
            <div><h2>今日库存流动</h2><p>按库存流水统计今日变化。</p></div>
          </div>
          <div class="flow-summary">
            <div class="flow-metric"><span>今日入库</span><strong>{today_in_total}</strong></div>
            <div class="flow-metric"><span>今日出库</span><strong>{today_out_total}</strong></div>
          </div>
          <div class="movement-list">{movement_rows}</div>
        </section>
      </section>

      <section class="inventory-overview">
        <div class="inventory-tile product">
          <span>成品可用库存</span>
          <strong>{product_total}</strong>
          <p>{product_kinds} 个产品型号，来自 {len(product_items)} 个库存批次。</p>
          <a href="/admin/inventory">查看成品库存</a>
        </div>
        <div class="inventory-tile raw">
          <span>板料可用库存</span>
          <strong>{raw_plate_total}</strong>
          <p>{raw_plate_specs} 个板料规格，低库存规格 {len(low_groups)} 个。</p>
          <a href="/admin/raw-plates">查看板料库存</a>
        </div>
        <div class="inventory-tile scrap">
          <span>余料可用库存</span>
          <strong>{scrap_total}</strong>
          <p>{scrap_specs} 个余料规格，待确认入库 {pending_scrap_count} 条。</p>
          <a href="/admin/scraps">查看余料记录</a>
        </div>
      </section>

      <section class="home-panel">
        <div class="home-panel-head">
          <div><h2>板料风险摘要</h2><p>这里只展示库存风险，不弹窗，不影响你继续操作。</p></div>
          <a class="btn secondary" href="/admin/raw-plates">查看全部</a>
        </div>
        <div class="risk-list">{risk_rows}</div>
      </section>
    </div>
    """
    return page("后台首页", body)


@router.get("/admin/plans", response_class=HTMLResponse)
def plans_page(
    drawing_id: str = "",
    quantity: str = "1",
    q: str = "",
    material: str = "",
    thickness: str = "",
    outer_diameter: str = "",
    inner_diameter: str = "",
    teeth_count: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    selected_id = int(drawing_id) if drawing_id.isdigit() else None
    drawing = (
        db.query(ProductDrawing)
        .filter(ProductDrawing.id == selected_id, ProductDrawing.confirmed == 1, ProductDrawing.is_active == 1)
        .first()
        if selected_id
        else None
    )
    quantity_value = optional_int(quantity) or 1
    if quantity_value <= 0:
        quantity_value = 1
    plan_filters = {
        "q": q.strip(),
        "material": material.strip(),
        "thickness": thickness.strip(),
        "outer_diameter": outer_diameter.strip(),
        "inner_diameter": inner_diameter.strip(),
        "teeth_count": teeth_count.strip(),
    }
    has_plan_filter = any(plan_filters.values())
    matched_drawings = filtered_plan_drawings(db, **plan_filters)
    if drawing is None and selected_id is None and has_plan_filter and len(matched_drawings) == 1:
        drawing = matched_drawings[0]
        selected_id = drawing.id

    product_rows = raw_rows = scrap_rows = ""
    summary_html = """
    <section class="card">
      <p class="muted" style="margin:0">可先按产品型号、材质、厚度、内外径和齿数筛选图纸，再选择图纸查询成品库存、可用余料和可用板料。</p>
    </section>
    """
    match_note = ""
    suggestion_html = ""
    drawing_match_html = ""

    if has_plan_filter:
        drawing_match_html = f"""
        <section class="card">
          <h2>匹配图纸</h2>
          <p class="muted">按当前条件找到 {len(matched_drawings)} 张已确认当前版本图纸。</p>
          <table><thead><tr><th>产品型号</th><th>版本</th><th>产品名称</th><th>材质</th><th>厚度</th><th>外径</th><th>内径</th><th>齿数</th><th>操作</th></tr></thead><tbody>{plan_drawing_rows(matched_drawings, quantity_value, plan_filters) or "<tr><td colspan='9'>暂无匹配图纸。</td></tr>"}</tbody></table>
        </section>
        """

    if drawing:
        product_code = drawing.product_code or ""
        required_material = drawing.material
        required_thickness = effective_drawing_thickness(drawing)
        required_diameter = drawing_required_diameter(drawing)
        required_size_label = f"φ{required_diameter:g}" if required_diameter else "-"

        product_items = (
            db.query(MaterialInventory)
            .filter(MaterialInventory.inventory_type == "product", MaterialInventory.quantity > 0)
            .order_by(MaterialInventory.updated_at.desc())
            .all()
        )
        product_items = [
            item for item in product_items
            if product_code and (item.material_code == product_code or item.source_product_code == product_code)
        ]
        product_total = sum(item.quantity for item in product_items)
        product_status = "够用" if product_total >= quantity_value else ("有库存" if product_total > 0 else "无库存")
        product_rows = "".join(
            f"<tr><td>{item.material_code or item.source_product_code or '-'}</td><td>{item.quantity}</td><td>{item.material}</td><td>{item.thickness:g}</td><td>{item.location or '-'}</td><td>{item.updated_at or item.created_at}</td></tr>"
            for item in product_items
        )

        raw_candidates = (
            db.query(MaterialInventory)
            .filter(MaterialInventory.inventory_type == "raw_plate", MaterialInventory.quantity > 0)
            .order_by(MaterialInventory.created_at.asc())
            .all()
        )
        raw_matches = [
            item for item in raw_candidates
            if raw_plate_matches_drawing(item, drawing)
        ]
        raw_total = sum(item.quantity for item in raw_matches)
        raw_rows = "".join(
            f"<tr><td>{item.material_code or '-'}</td><td>{item.material}</td><td>{item.length or '-'}</td><td>{item.width or '-'}</td><td>{item.thickness:g}</td><td>{item.quantity}</td><td>{item.location or '-'}</td></tr>"
            for item in raw_matches[:100]
        )

        scrap_candidates = (
            db.query(MaterialInventory)
            .filter(MaterialInventory.inventory_type == "scrap", MaterialInventory.status == "available", MaterialInventory.quantity > 0)
            .order_by(MaterialInventory.diameter.asc(), MaterialInventory.created_at.asc())
            .all()
        )
        required_scrap_diameter = scrap_required_diameter(drawing)
        scrap_matches = [
            item for item in scrap_candidates
            if scrap_matches_drawing(item, drawing)
        ]
        scrap_total = sum(item.quantity for item in scrap_matches)
        scrap_rows = "".join(
            f"<tr><td>{item.material}</td><td>{item.thickness:g}</td><td>{item.usable_size or '-'}</td><td>{item.quantity}</td><td>{scrap_location_label(item)}</td><td>{item.source_product_code or '-'}</td></tr>"
            for item in scrap_matches[:100]
        )

        product_badge = "badge"
        if product_total >= quantity_value:
            suggestion = "建议优先使用成品库存，当前成品数量已满足计划。"
        elif scrap_total >= quantity_value:
            suggestion = "成品不足，建议优先使用匹配余料安排生产。"
        elif raw_total > 0:
            suggestion = "成品和余料不足，当前有匹配板料，可安排板料生产。"
        else:
            suggestion = "成品、余料和板料都未匹配到足够材料，建议先采购或入库。"
        suggestion_html = f"""
        <section class="card">
          <h2 style="margin-top:0">系统建议</h2>
          <p style="margin-bottom:0">{suggestion}</p>
        </section>
        """
        summary_html = f"""
        <section class="grid">
          <div class="card stat"><span class="muted">成品库存</span><strong>{product_total}</strong><span class="{product_badge}">{product_status}</span></div>
          <div class="card stat"><span class="muted">匹配余料</span><strong>{scrap_total}</strong><span class="badge">{'有可用余料' if scrap_total else '暂无匹配'}</span></div>
          <div class="card stat"><span class="muted">匹配板料</span><strong>{raw_total}</strong><span class="badge">{'有可用板料' if raw_total else '暂无匹配'}</span></div>
        </section>
        """
        match_note = f"""
        <section class="card">
          <strong>匹配条件：</strong>
          产品 {html.escape(product_code or '-')}；
          材质 {html.escape(required_material or '-')}；
          厚度 {required_thickness if required_thickness is not None else '-'}；
          计划数量 {quantity_value}；
          余料所需尺寸 {required_scrap_diameter if required_scrap_diameter is not None else required_size_label}；
          板料所需尺寸按图纸外框/外径 + 加工余量 {settings.machining_margin:g} 判断
        </section>
        """

    keyword_options = datalist_options(drawing_distinct_options(db, "product_code") + drawing_distinct_options(db, "product_name"))
    material_options = select_options(drawing_distinct_options(db, "material"), material, "全部材质")
    thickness_options = select_options(
        drawing_distinct_options(db, "plate_thickness") + drawing_distinct_options(db, "product_thickness") + drawing_distinct_options(db, "thickness"),
        thickness,
        "全部厚度",
    )
    outer_options = select_options(drawing_distinct_options(db, "max_outer_diameter"), outer_diameter, "全部外径")
    inner_options = select_options(drawing_distinct_options(db, "min_inner_diameter"), inner_diameter, "全部内径")
    teeth_options = select_options(drawing_distinct_options(db, "teeth_count"), teeth_count, "全部齿数")
    body = f"""
    <div class="top"><div><h1>计划管理</h1><p class="muted">按产品或规格找到图纸，再检查成品、余料、板料有没有可用库存。</p></div></div>
    <section class="card">
      <form method="get" action="/admin/plans" class="form-grid">
        <div><label>产品型号/名称</label><input name="q" list="planKeywordOptions" value="{safe_value(q)}" placeholder="可输入型号或名称"></div>
        <datalist id="planKeywordOptions">{keyword_options}</datalist>
        <div><label>材质</label><select name="material">{material_options}</select></div>
        <div><label>厚度</label><select name="thickness">{thickness_options}</select></div>
        <div><label>外径</label><select name="outer_diameter">{outer_options}</select></div>
        <div><label>内径</label><select name="inner_diameter">{inner_options}</select></div>
        <div><label>齿数</label><select name="teeth_count">{teeth_options}</select></div>
        <div><label>匹配图纸</label><select name="drawing_id">{plan_product_options(db, selected_id, matched_drawings if has_plan_filter else None)}</select></div>
        <div><label>计划数量</label><input name="quantity" type="number" min="1" value="{quantity_value}"></div>
        <div style="align-self:end" class="actions"><button class="btn" type="submit">查询有没有料</button><a class="btn secondary" href="/admin/plans">清空</a></div>
      </form>
    </section>
    {drawing_match_html}
    {summary_html}
    {suggestion_html}
    {match_note}
    <section class="card"><h2>成品库存</h2><table><thead><tr><th>产品型号</th><th>数量</th><th>材质</th><th>厚度</th><th>库位</th><th>更新时间</th></tr></thead><tbody>{product_rows or "<tr><td colspan='6'>暂无匹配成品。</td></tr>"}</tbody></table></section>
    <section class="card"><h2>可用余料</h2><table><thead><tr><th>材质</th><th>厚度</th><th>可用尺寸</th><th>数量</th><th>库位</th><th>来源产品</th></tr></thead><tbody>{scrap_rows or "<tr><td colspan='6'>暂无匹配余料。</td></tr>"}</tbody></table></section>
    <section class="card"><h2>可用板料</h2><table><thead><tr><th>批次编号</th><th>材质</th><th>长mm</th><th>宽mm</th><th>厚mm</th><th>数量</th><th>库位</th></tr></thead><tbody>{raw_rows or "<tr><td colspan='7'>暂无匹配板料。</td></tr>"}</tbody></table></section>
    """
    return page("计划管理", body)


@router.get("/admin/operation-logs", response_class=HTMLResponse)
def operation_logs_page(action: str = "", object_type: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    query = db.query(OperationLog)
    if action.strip():
        query = query.filter(OperationLog.action == action.strip())
    if object_type.strip():
        query = query.filter(OperationLog.object_type == object_type.strip())
    logs = query.order_by(OperationLog.created_at.desc()).limit(500).all()
    rows = "".join(
        f"""
        <tr>
          <td>{log.created_at}</td>
          <td><span class="badge">{html.escape(log.action)}</span></td>
          <td>{html.escape(log.object_type)}</td>
          <td>{html.escape(log.operator_name or '-')}</td>
          <td>{html.escape(log.remark or '-')}</td>
          <td><pre>{html.escape(str(scrub_internal_ids(log.before_data) or '-'))}</pre></td>
          <td><pre>{html.escape(str(scrub_internal_ids(log.after_data) or '-'))}</pre></td>
        </tr>
        """
        for log in logs
    )
    body = f"""
    <div class="top"><div><h1>操作日志</h1><p class="muted">查看关键业务操作的审计记录，最多显示最近 500 条。</p></div></div>
    <section class="card">
      <form method="get" action="/admin/operation-logs" class="actions" style="justify-content:flex-start">
        <input name="action" value="{html.escape(action.strip())}" placeholder="操作类型，例如 drawing_delete">
        <input name="object_type" value="{html.escape(object_type.strip())}" placeholder="对象类型，例如 drawing">
        <button class="btn" type="submit">筛选</button>
        <a class="btn secondary" href="/admin/operation-logs">清空</a>
      </form>
    </section>
    <section class="card"><table><thead><tr><th>时间</th><th>操作</th><th>对象</th><th>操作人</th><th>备注</th><th>操作前</th><th>操作后</th></tr></thead><tbody>{rows or "<tr><td colspan='7'>暂无操作日志。</td></tr>"}</tbody></table></section>
    """
    return page("操作日志", body)


@router.get("/admin/assistant", response_class=HTMLResponse)
def assistant_page() -> HTMLResponse:
    body = """
    <div class="top"><div><h1>智能助手</h1><p class="muted">助手现在固定在右下角，可在任意后台页面直接打开。</p></div></div>
    <section class="card">
      <h2 style="margin-top:0">常用问题</h2>
      <div class="actions" style="margin-bottom:14px">
        <button class="btn secondary" type="button" onclick="window.inventoryAssistant.ask('查一下产品库存')">查成品库存</button>
        <button class="btn secondary" type="button" onclick="window.inventoryAssistant.ask('查一下板料库存')">查板料库存</button>
        <button class="btn secondary" type="button" onclick="window.inventoryAssistant.ask('查一下余料库存')">查余料库存</button>
        <button class="btn secondary" type="button" onclick="window.inventoryAssistant.ask('今天出库明细')">今天出库明细</button>
        <button class="btn secondary" type="button" onclick="window.inventoryAssistant.ask('查一下库存预警')">库存预警</button>
        <button class="btn secondary" type="button" onclick="window.inventoryAssistant.ask('计划生产某个型号有没有料')">计划查料</button>
        <button class="btn secondary" type="button" onclick="window.inventoryAssistant.ask('图纸能不能修改')">图纸规则</button>
      </div>
      <p class="muted" style="margin-bottom:0">助手只查询和分析，不执行新增、修改、删除、入库、出库或撤销。</p>
      <script>
        window.addEventListener('load', () => {
          if (window.inventoryAssistant) window.inventoryAssistant.open();
        });
      </script>
    </section>
    """
    return page("智能助手", body)


@router.post("/admin/assistant/chat")
def assistant_chat(message: str = Form(...), context: str = Form(""), db: Session = Depends(get_db)) -> dict:
    return run_assistant(message, context, db)


@router.get("/admin/exports/{module}")
def export_admin_module(module: str, request: Request, db: Session = Depends(get_db)) -> StreamingResponse:
    filters = dict(request.query_params)
    title, headings, rows = build_export_rows(module, filters, db)
    log_export(module, filters, len(rows), db)
    db.commit()
    filename = export_filename(title)
    output = make_workbook_bytes(title, headings, rows)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition(filename)},
    )


@router.post("/admin/assistant/analysis/export")
async def export_assistant_analysis(request: Request, db: Session = Depends(get_db)) -> StreamingResponse:
    payload = await request.json()
    title = str(payload.get("title") or "AI分析结果")
    columns = payload.get("columns") or []
    data_rows = payload.get("rows") or []
    headings = [str(column.get("label") or column.get("prop") or "") for column in columns]
    props = [str(column.get("prop") or "") for column in columns]
    rows = [[row.get(prop, "") for prop in props] for row in data_rows]
    log_export("ai_analysis", {"title": title}, len(rows), db)
    db.commit()
    filename = export_filename(title)
    output = make_workbook_bytes(title, headings, rows)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition(filename)},
    )


@router.get("/admin/inventory", response_class=HTMLResponse)
def inventory_page(
    q: str = "",
    inventory_type: str = "",
    status: str = "",
    material: str = "",
    thickness: str = "",
    location: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    query = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "product")
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            (MaterialInventory.material_code.ilike(like))
            | (MaterialInventory.material.ilike(like))
            | (MaterialInventory.usable_size.ilike(like))
            | (MaterialInventory.location.ilike(like))
            | (MaterialInventory.paper_material.ilike(like))
            | (MaterialInventory.source_product_code.ilike(like))
        )
    if inventory_type:
        query = query.filter(MaterialInventory.inventory_type == inventory_type)
    else:
        query = query.filter(MaterialInventory.inventory_type == "product")
    if material.strip():
        query = query.filter(MaterialInventory.material.ilike(f"%{material.strip()}%"))
    thickness_value = optional_float(thickness)
    if thickness_value is not None:
        query = query.filter(MaterialInventory.thickness == thickness_value)
    if location.strip():
        query = query.filter(MaterialInventory.location.ilike(f"%{location.strip()}%"))
    items = query.order_by(MaterialInventory.created_at.desc()).all()
    grouped = {}
    for item in items:
        code = item.material_code or item.source_product_code or "未编号"
        if code not in grouped:
            grouped[code] = {
                "code": code,
                "material": item.material,
                "product_thicknesses": set(),
                "plate_thicknesses": set(),
                "quantity": 0,
                "locations": set(),
                "paper_materials": set(),
                "latest": item.updated_at or item.created_at,
            }
        grouped[code]["quantity"] += item.quantity
        grouped[code]["product_thicknesses"].add(fmt_option(item.product_thickness or item.thickness))
        grouped[code]["plate_thicknesses"].add(fmt_option(item.plate_thickness or item.thickness))
        if item.location:
            grouped[code]["locations"].add(item.location)
        if item.paper_material:
            grouped[code]["paper_materials"].add(item.paper_material)
        item_time = item.updated_at or item.created_at
        if item_time and item_time > grouped[code]["latest"]:
            grouped[code]["latest"] = item_time
    rows = "".join(
        f"""
        <tr>
          <td>{group['code']}</td><td>{group['material']}</td><td>{' / '.join(sorted(value for value in group['product_thicknesses'] if value)) or '-'}</td><td>{' / '.join(sorted(value for value in group['plate_thicknesses'] if value)) or '-'}</td><td>{' / '.join(sorted(group['paper_materials'])) or '-'}</td><td><strong>{group['quantity']}</strong></td><td>{' / '.join(sorted(group['locations'])) or '-'}</td><td>{group['latest'] or '-'}</td><td><a class='btn secondary' href='/admin/inventory/product/{quote(str(group['code']), safe="")}'>查看明细</a></td>
        </tr>
        """
        for group in grouped.values()
    )
    product_codes = inventory_distinct_options(db, "product", "material_code", quantity_positive=True)
    source_codes = inventory_distinct_options(db, "product", "source_product_code", quantity_positive=True)
    product_code_options = datalist_options(product_codes + source_codes)
    material_options = datalist_options(inventory_distinct_options(db, "product", "material", quantity_positive=True))
    thickness_options = datalist_options(inventory_distinct_options(db, "product", "thickness", quantity_positive=True))
    location_options = datalist_options(inventory_distinct_options(db, "product", "location", quantity_positive=True))
    body = f"""
    <div class="top"><div><h1>成品库存</h1><p class="muted">只查询成品库存汇总；入库和出库请进入单独页面操作。</p></div><div class="actions"><a class="btn" href="/admin/inventory/inbound">成品入库</a><a class="btn secondary" href="/admin/inventory/outbound">成品出库</a><a class="btn secondary" href="/admin/reports/product-outbound">产品出库分析</a><a class="btn secondary" href="/admin/inventory/transactions">成品流水</a><a class="btn secondary" href="{export_link('product_inventory', {'q': keyword, 'material': material.strip(), 'thickness': thickness.strip(), 'location': location.strip()})}">导出Excel</a></div></div>
    <section class="card">
      <form method="get" action="/admin/inventory" class="actions" style="justify-content:flex-start">
        <input name="q" value="{safe_value(keyword)}" list="product-code-options" placeholder="输入型号筛选" style="width:220px"><datalist id="product-code-options">{product_code_options}</datalist>
        <input type="hidden" name="inventory_type" value="product">
        <input name="material" value="{safe_value(material.strip())}" list="product-material-options" placeholder="材质" style="width:150px"><datalist id="product-material-options">{material_options}</datalist>
        <input name="thickness" value="{safe_value(thickness.strip())}" list="product-thickness-options" placeholder="厚度" style="width:130px"><datalist id="product-thickness-options">{thickness_options}</datalist>
        <input name="location" value="{safe_value(location.strip())}" list="product-location-options" placeholder="库位" style="width:150px"><datalist id="product-location-options">{location_options}</datalist>
        <button class="btn" type="submit">搜索库存</button>
        <a class="btn secondary" href="/admin/inventory">清空</a>
      </form>
    </section>
    <section class="card"><h2>成品汇总</h2><table><thead><tr><th>产品编号</th><th>材质</th><th>总成品厚度</th><th>钢板厚度</th><th>纸材质</th><th>总数量</th><th>库位</th><th>最近更新时间</th><th>操作</th></tr></thead><tbody>{rows or "<tr><td colspan='9'>暂无成品库存。</td></tr>"}</tbody></table></section>
    """
    return page("成品管理", body)


@router.get("/admin/raw-plates", response_class=HTMLResponse)
def raw_plates_page(
    q: str = "",
    material: str = "",
    thickness: str = "",
    length: str = "",
    width: str = "",
    location: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    query = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "raw_plate")
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            (MaterialInventory.material_code.ilike(like))
            | (MaterialInventory.material.ilike(like))
            | (MaterialInventory.location.ilike(like))
            | (MaterialInventory.usable_size.ilike(like))
        )
    if material.strip():
        query = query.filter(MaterialInventory.material.ilike(f"%{material.strip()}%"))
    length_value = optional_float(length)
    if length_value is not None:
        query = query.filter(MaterialInventory.length == length_value)
    width_value = optional_float(width)
    if width_value is not None:
        query = query.filter(MaterialInventory.width == width_value)
    thickness_value = optional_float(thickness)
    if thickness_value is not None:
        query = query.filter(MaterialInventory.thickness == thickness_value)
    if location.strip():
        query = query.filter(MaterialInventory.location.ilike(f"%{location.strip()}%"))
    items = query.order_by(MaterialInventory.created_at.desc()).all()
    spec_names = {
        (spec.material, spec.length, spec.width, spec.thickness): spec.spec_name
        for spec in db.query(RawPlateSpecification).filter(RawPlateSpecification.is_active == 1).all()
    }
    summary = {}
    for item in items:
        key = (
            item.material,
            item.length,
            item.width,
            item.thickness,
        )
        if key not in summary:
            summary[key] = {
                "spec_name": spec_names.get(key) or "临时规格",
                "material": item.material,
                "length": item.length,
                "width": item.width,
                "thickness": item.thickness,
                "quantity": 0,
                "batch_count": 0,
                "locations": set(),
            }
        summary[key]["quantity"] += item.quantity
        summary[key]["batch_count"] += 1
        if item.location:
            summary[key]["locations"].add(item.location)
    summary_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(str(group['spec_name']))}</td>
          <td>{html.escape(str(group['material']))}</td>
          <td>{group['length'] or '-'}</td>
          <td>{group['width'] or '-'}</td>
          <td>{group['thickness']}</td>
          <td><strong>{group['quantity']}</strong></td>
          <td>{group['batch_count']}</td>
          <td>{html.escape(' / '.join(sorted(group['locations'])) or '-')}</td>
          <td><a class="btn secondary" href="/admin/raw-plates/detail?{build_query({'material': group['material'], 'length': group['length'], 'width': group['width'], 'thickness': group['thickness']})}">查看明细</a></td>
        </tr>
        """
        for group in sorted(summary.values(), key=lambda group: (str(group["material"]), group["thickness"] or 0, group["length"] or 0, group["width"] or 0))
    )
    batch_options = datalist_options(inventory_distinct_options(db, "raw_plate", "material_code", quantity_positive=True))
    material_options = datalist_options(inventory_distinct_options(db, "raw_plate", "material", quantity_positive=True))
    length_options = datalist_options(inventory_distinct_options(db, "raw_plate", "length", quantity_positive=True))
    width_options = datalist_options(inventory_distinct_options(db, "raw_plate", "width", quantity_positive=True))
    thickness_options = datalist_options(inventory_distinct_options(db, "raw_plate", "thickness", quantity_positive=True))
    location_options = datalist_options(inventory_distinct_options(db, "raw_plate", "location", quantity_positive=True))
    body = f"""
    <div class="top"><div><h1>板料库存</h1><p class="muted">查看按重量换算入库的原料钢板库存。</p></div><div class="actions"><a class="btn" href="/admin/raw-plates/inbound">板料入库</a><a class="btn secondary" href="/admin/raw-plates/outbound">板料出库</a><a class="btn secondary" href="{export_link('raw_plate_inventory', {'q': keyword, 'material': material.strip(), 'thickness': thickness.strip()})}">导出Excel</a></div></div>
    <section class="card">
      <form method="get" action="/admin/raw-plates" class="actions" style="justify-content:flex-start">
        <input name="q" value="{safe_value(keyword)}" list="raw-plate-batch-options" placeholder="输入批次/材质/尺寸/库位" style="width:220px"><datalist id="raw-plate-batch-options">{batch_options}</datalist>
        <input name="material" value="{safe_value(material.strip())}" list="raw-plate-material-options" placeholder="材质" style="width:150px"><datalist id="raw-plate-material-options">{material_options}</datalist>
        <input name="length" value="{safe_value(length.strip())}" list="raw-plate-length-options" placeholder="长度" style="width:120px"><datalist id="raw-plate-length-options">{length_options}</datalist>
        <input name="width" value="{safe_value(width.strip())}" list="raw-plate-width-options" placeholder="宽度" style="width:120px"><datalist id="raw-plate-width-options">{width_options}</datalist>
        <input name="thickness" value="{safe_value(thickness.strip())}" list="raw-plate-thickness-options" placeholder="厚度" style="width:120px"><datalist id="raw-plate-thickness-options">{thickness_options}</datalist>
        <input name="location" value="{safe_value(location.strip())}" list="raw-plate-location-options" placeholder="库位" style="width:150px"><datalist id="raw-plate-location-options">{location_options}</datalist>
        <button class="btn" type="submit">搜索板料</button>
        <a class="btn secondary" href="/admin/raw-plates">清空</a>
      </form>
    </section>
    <section class="card"><h2>板料规格汇总</h2><table><thead><tr><th>规格</th><th>材质</th><th>长mm</th><th>宽mm</th><th>厚mm</th><th>总块数</th><th>批次数</th><th>库位</th><th>操作</th></tr></thead><tbody>{summary_rows or "<tr><td colspan='9'>暂无板料库存。</td></tr>"}</tbody></table></section>
    """
    return page("板料库存", body)


@router.get("/admin/raw-plates/detail", response_class=HTMLResponse)
def raw_plate_group_detail_page(
    material: str = "",
    length: str = "",
    width: str = "",
    thickness: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    material_value = material.strip()
    length_value = optional_float(length)
    width_value = optional_float(width)
    thickness_value = optional_float(thickness)
    if not material_value or length_value is None or width_value is None or thickness_value is None:
        raise HTTPException(status_code=400, detail="板料规格参数错误")
    items = (
        db.query(MaterialInventory)
        .filter(
            MaterialInventory.inventory_type == "raw_plate",
            MaterialInventory.material == material_value,
            MaterialInventory.length == length_value,
            MaterialInventory.width == width_value,
            MaterialInventory.thickness == thickness_value,
        )
        .order_by(MaterialInventory.created_at.asc())
        .all()
    )
    item_ids = [item.id for item in items]
    records = (
        db.query(InventoryTransactionRecord)
        .filter(InventoryTransactionRecord.inventory_id.in_(item_ids))
        .order_by(InventoryTransactionRecord.created_at.desc())
        .all()
    ) if item_ids else []
    item_map = {item.id: item for item in items}
    total_quantity = sum(item.quantity for item in items)
    batch_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(item.material_code or '-')}</td>
          <td><strong>{item.quantity}</strong></td>
          <td>{html.escape(item.location or '-')}</td>
          <td>{html.escape(item.status or '-')}</td>
          <td>{item.created_at}</td>
          <td>{item.updated_at}</td>
          <td><a class="btn secondary" href="/admin/raw-plates/{item.id}/edit">修改</a></td>
        </tr>
        """
        for item in items
    )
    transaction_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(item_map.get(record.inventory_id).material_code if item_map.get(record.inventory_id) and item_map.get(record.inventory_id).material_code else '-')}</td>
          <td>{transaction_label(record.transaction_type)}</td>
          <td>{record.quantity}</td>
          <td>{record.before_quantity}</td>
          <td>{record.after_quantity}</td>
          <td>{html.escape(item_map.get(record.inventory_id).location if item_map.get(record.inventory_id) and item_map.get(record.inventory_id).location else '-')}</td>
          <td>{html.escape(record.customer_name or '-')}</td>
          <td>{html.escape(record.operator_name or '-')}</td>
          <td>{html.escape(record.remark or '-')}</td>
          <td>{record.created_at}</td>
        </tr>
        """
        for record in records
    )
    title = f"{material_value} {length_value:g}×{width_value:g}×{thickness_value:g}mm"
    body = f"""
    <div class="top"><div><h1>板料明细：{html.escape(title)}</h1><p class="muted">当前总块数：<strong>{total_quantity}</strong>，按该材质和长宽厚汇总所有固定规格与临时规格批次。</p></div><div class="actions"><a class="btn secondary" href="/admin/raw-plates">返回板料库存</a><a class="btn secondary" href="/admin/raw-plates/transactions">板料流水</a></div></div>
    <section class="card"><h2>批次明细</h2><table><thead><tr><th>批次编号</th><th>剩余块数</th><th>库位</th><th>状态</th><th>创建时间</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{batch_rows or "<tr><td colspan='7'>暂无该规格板料批次。</td></tr>"}</tbody></table></section>
    <section class="card"><h2>出入库流水</h2><table><thead><tr><th>批次编号</th><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>库位</th><th>客户/去向</th><th>操作人</th><th>备注</th><th>时间</th></tr></thead><tbody>{transaction_rows or "<tr><td colspan='10'>暂无该规格板料流水。</td></tr>"}</tbody></table></section>
    """
    return page("板料明细", body)


@router.get("/admin/raw-plates/{inventory_id}/edit", response_class=HTMLResponse)
def edit_raw_plate_page(inventory_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    item = db.get(MaterialInventory, inventory_id)
    if not item or item.inventory_type != "raw_plate":
        raise HTTPException(status_code=404, detail="板料库存不存在")
    has_out_record = db.query(InventoryTransactionRecord).filter(
        InventoryTransactionRecord.inventory_id == item.id,
        InventoryTransactionRecord.transaction_type == "out",
    ).first() is not None
    disabled = "readonly" if has_out_record else ""
    tip = "该批次已有出库流水，只允许修改批次号和库位，规格信息不允许修改。" if has_out_record else "该批次暂无出库流水，可修改批次号、材质、长宽厚、库位和状态。"
    body = f"""
    <div class="top"><div><h1>修改板料批次</h1><p class="muted">{tip}</p></div><div class="actions"><a class="btn secondary" href="/admin/raw-plates">返回板料库存</a></div></div>
    <section class="card">
      <form method="post" action="/admin/raw-plates/{item.id}/edit" class="form-grid">
        <div><label>批次编号</label><input name="material_code" value="{html.escape(item.material_code or '')}"></div>
        <div><label>材质</label><input name="material" value="{html.escape(item.material)}" {disabled} required></div>
        <div><label>长度 mm</label><input name="length" type="number" step="0.01" min="0.01" value="{item.length or ''}" {disabled} required></div>
        <div><label>宽度 mm</label><input name="width" type="number" step="0.01" min="0.01" value="{item.width or ''}" {disabled} required></div>
        <div><label>厚度 mm</label><input name="thickness" type="number" step="0.01" min="0.01" value="{item.thickness}" {disabled} required></div>
        <div><label>库位</label><input name="location" value="{html.escape(item.location or '')}"></div>
        <div><label>状态</label><select name="status" {"disabled" if has_out_record else ""}><option value="available" {"selected" if item.status == "available" else ""}>available</option><option value="used" {"selected" if item.status == "used" else ""}>used</option></select></div>
        <div><label>操作人</label><input name="operator_name" placeholder="例如 张三"></div>
        <div><label>修改原因</label><input name="remark" placeholder="例如 修正录入错误"></div>
        <div style="align-self:end"><button class="btn" type="submit">保存修改</button></div>
      </form>
    </section>
    """
    return page("修改板料批次", body)


@router.post("/admin/raw-plates/{inventory_id}/edit")
def update_raw_plate_from_page(
    inventory_id: int,
    material_code: str = Form(""),
    material: str = Form(""),
    length: float | None = Form(None),
    width: float | None = Form(None),
    thickness: float | None = Form(None),
    location: str = Form(""),
    status: str = Form("available"),
    operator_name: str = Form(""),
    remark: str = Form(""),
    _lock=Depends(locked_inventory_write),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    item = db.get(MaterialInventory, inventory_id)
    if not item or item.inventory_type != "raw_plate":
        raise HTTPException(status_code=404, detail="板料库存不存在")
    has_out_record = db.query(InventoryTransactionRecord).filter(
        InventoryTransactionRecord.inventory_id == item.id,
        InventoryTransactionRecord.transaction_type == "out",
    ).first() is not None
    before_data = inventory_snapshot(item)
    item.material_code = material_code.strip() or None
    item.location = location.strip() or None
    if not has_out_record:
        if not material.strip() or length is None or width is None or thickness is None or length <= 0 or width <= 0 or thickness <= 0:
            raise HTTPException(status_code=400, detail="材质、长宽厚必须有效")
        if status not in ("available", "used"):
            raise HTTPException(status_code=400, detail="状态无效")
        item.material = material.strip()
        item.length = length
        item.width = width
        item.thickness = thickness
        item.usable_size = f"{length:g}×{width:g}×{thickness:g}mm"
        item.status = status
    record_operation_log(
        db,
        "raw_plate_update",
        "inventory",
        item.id,
        operator_name or None,
        remark or "修改板料批次信息",
        before_data=before_data,
        after_data=inventory_snapshot(item),
    )
    db.commit()
    return RedirectResponse("/admin/raw-plates", status_code=303)


@router.get("/admin/raw-plate-specifications", response_class=HTMLResponse)
def raw_plate_specifications_page(db: Session = Depends(get_db)) -> HTMLResponse:
    specs = db.query(RawPlateSpecification).order_by(RawPlateSpecification.is_active.desc(), RawPlateSpecification.created_at.desc()).all()
    rows = "".join(
        f"""
        <tr>
          <td>{spec.spec_name}</td><td>{spec.material}</td><td>{spec.length:g}</td><td>{spec.width:g}</td><td>{spec.thickness:g}</td><td>{spec.density:g}</td><td>{'启用' if spec.is_active else '停用'}</td><td>{spec.remark or '-'}</td>
          <td><div class="actions" style="gap:6px;justify-content:flex-start"><a class="btn secondary" href="/admin/raw-plate-specifications/{spec.id}/edit">修改</a><form method="post" action="/admin/raw-plate-specifications/{spec.id}/toggle"><button class="btn secondary" type="submit">{'停用' if spec.is_active else '启用'}</button></form></div></td>
        </tr>
        """
        for spec in specs
    )
    body = f"""
    <div class="top"><div><h1>板料规格</h1><p class="muted">维护常用固定板料型号，入库时可直接选择带出材质、长宽厚和密度。</p></div><div class="actions"><a class="btn secondary" href="/admin/raw-plates/inbound">板料入库</a></div></div>
    <section class="card">
      <form method="post" action="/admin/raw-plate-specifications" class="form-grid">
        <div><label>规格名称</label><input name="spec_name" placeholder="例如 65Mn 2000×1000×2" required></div>
        <div><label>材质</label><input name="material" placeholder="例如 65Mn" required></div>
        <div><label>长度 mm</label><input name="length" type="number" step="0.01" min="0.01" required></div>
        <div><label>宽度 mm</label><input name="width" type="number" step="0.01" min="0.01" required></div>
        <div><label>厚度 mm</label><input name="thickness" type="number" step="0.01" min="0.01" required></div>
        <div><label>密度 g/cm³</label><input name="density" type="number" step="0.001" min="0.001" value="7.85" required></div>
        <div><label>备注</label><input name="remark" placeholder="例如 常用钢板"></div>
        <div style="align-self:end"><button class="btn" type="submit">保存规格</button></div>
      </form>
    </section>
    <section class="card"><table><thead><tr><th>规格名称</th><th>材质</th><th>长mm</th><th>宽mm</th><th>厚mm</th><th>密度</th><th>状态</th><th>备注</th><th>操作</th></tr></thead><tbody>{rows or "<tr><td colspan='9'>暂无板料规格。</td></tr>"}</tbody></table></section>
    """
    return page("板料规格", body)


@router.post("/admin/raw-plate-specifications")
def create_raw_plate_specification(
    spec_name: str = Form(...),
    material: str = Form(...),
    length: float = Form(...),
    width: float = Form(...),
    thickness: float = Form(...),
    density: float = Form(7.85),
    remark: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if length <= 0 or width <= 0 or thickness <= 0 or density <= 0:
        raise HTTPException(status_code=400, detail="长宽厚和密度必须大于0")
    db.add(
        RawPlateSpecification(
            spec_name=spec_name.strip(),
            material=material.strip(),
            length=length,
            width=width,
            thickness=thickness,
            density=density,
            remark=remark.strip() or None,
            is_active=1,
        )
    )
    db.commit()
    return RedirectResponse("/admin/raw-plate-specifications", status_code=303)


@router.get("/admin/raw-plate-specifications/{spec_id}/edit", response_class=HTMLResponse)
def edit_raw_plate_specification_page(spec_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    spec = db.get(RawPlateSpecification, spec_id)
    if not spec:
        raise HTTPException(status_code=404, detail="板料规格不存在")
    body = f"""
    <div class="top"><div><h1>修改板料规格</h1><p class="muted">修改的是固定规格资料，只影响后续入库选择，不会自动修改已经入库的历史批次。</p></div><div class="actions"><a class="btn secondary" href="/admin/raw-plate-specifications">返回板料规格</a></div></div>
    <section class="card">
      <form method="post" action="/admin/raw-plate-specifications/{spec.id}/edit" class="form-grid">
        <div><label>规格名称</label><input name="spec_name" value="{html.escape(spec.spec_name)}" required></div>
        <div><label>材质</label><input name="material" value="{html.escape(spec.material)}" required></div>
        <div><label>长度 mm</label><input name="length" type="number" step="0.01" min="0.01" value="{spec.length:g}" required></div>
        <div><label>宽度 mm</label><input name="width" type="number" step="0.01" min="0.01" value="{spec.width:g}" required></div>
        <div><label>厚度 mm</label><input name="thickness" type="number" step="0.01" min="0.01" value="{spec.thickness:g}" required></div>
        <div><label>密度 g/cm³</label><input name="density" type="number" step="0.001" min="0.001" value="{spec.density:g}" required></div>
        <div><label>状态</label><select name="is_active"><option value="1" {"selected" if spec.is_active else ""}>启用</option><option value="0" {"selected" if not spec.is_active else ""}>停用</option></select></div>
        <div><label>备注</label><input name="remark" value="{html.escape(spec.remark or '')}"></div>
        <div style="align-self:end"><button class="btn" type="submit">保存修改</button></div>
      </form>
    </section>
    """
    return page("修改板料规格", body)


@router.post("/admin/raw-plate-specifications/{spec_id}/edit")
def update_raw_plate_specification(
    spec_id: int,
    spec_name: str = Form(...),
    material: str = Form(...),
    length: float = Form(...),
    width: float = Form(...),
    thickness: float = Form(...),
    density: float = Form(7.85),
    is_active: int = Form(1),
    remark: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    spec = db.get(RawPlateSpecification, spec_id)
    if not spec:
        raise HTTPException(status_code=404, detail="板料规格不存在")
    if length <= 0 or width <= 0 or thickness <= 0 or density <= 0:
        raise HTTPException(status_code=400, detail="长宽厚和密度必须大于0")
    spec.spec_name = spec_name.strip()
    spec.material = material.strip()
    spec.length = length
    spec.width = width
    spec.thickness = thickness
    spec.density = density
    spec.is_active = 1 if is_active else 0
    spec.remark = remark.strip() or None
    db.commit()
    return RedirectResponse("/admin/raw-plate-specifications", status_code=303)


@router.post("/admin/raw-plate-specifications/{spec_id}/toggle")
def toggle_raw_plate_specification(spec_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    spec = db.get(RawPlateSpecification, spec_id)
    if not spec:
        raise HTTPException(status_code=404, detail="板料规格不存在")
    spec.is_active = 0 if spec.is_active else 1
    db.commit()
    return RedirectResponse("/admin/raw-plate-specifications", status_code=303)


@router.get("/admin/raw-plates/inbound", response_class=HTMLResponse)
def raw_plate_inbound_page(db: Session = Depends(get_db)) -> HTMLResponse:
    specs = db.query(RawPlateSpecification).filter(RawPlateSpecification.is_active == 1).order_by(RawPlateSpecification.spec_name.asc()).all()
    spec_options = "".join(
        f"<option value='{spec.id}' data-material='{html.escape(spec.material)}' data-length='{spec.length:g}' data-width='{spec.width:g}' data-thickness='{spec.thickness:g}' data-density='{spec.density:g}'>{html.escape(spec.spec_name)}｜{html.escape(spec.material)}｜{spec.length:g}×{spec.width:g}×{spec.thickness:g}</option>"
        for spec in specs
    )
    material_candidates = datalist_options(inventory_distinct_options(db, "raw_plate", "material") + [spec.material for spec in specs])
    location_candidates = datalist_options(inventory_distinct_options(db, "raw_plate", "location"))
    body = f"""
    <div class="top"><div><h1>板料入库</h1><p class="muted">选择常用板料规格或手动输入，总重量会按规格和密度换算入库块数。</p></div><div class="actions"><a class="btn secondary" href="/admin/raw-plate-specifications">维护板料规格</a><a class="btn secondary" href="/admin/raw-plates">返回板料库存</a></div></div>
    <section class="card">
      <form method="post" action="/admin/raw-plates/inbound" class="form-grid" data-confirm-flow="true" data-confirm-title="确认板料入库" data-confirm-note="系统会按总重量、长宽厚和密度换算块数，并生成板料入库流水。">
        <div><label>筛选板料规格</label><input type="search" data-select-filter="raw-plate-spec-select" placeholder="输入规格、材质或尺寸"></div>
        <div><label>选择板料规格</label><select id="raw-plate-spec-select"><option value="">手动输入/临时规格</option>{spec_options}</select></div>
        <div><label>板料编号/批次号</label><input name="material_code" placeholder="例如 采购批次/炉号/自编号，不填自动生成"></div>
        <div><label>材质</label><input id="raw-plate-material" name="material" list="raw-plate-material-options" placeholder="例如 45#钢 / Q235" required><datalist id="raw-plate-material-options">{material_candidates}</datalist></div>
        <div><label>总重量 吨</label><input name="total_weight_ton" type="number" step="0.001" min="0.001" required></div>
        <div><label>长度 mm</label><input id="raw-plate-length" name="length" type="number" step="0.01" min="0.01" required></div>
        <div><label>宽度 mm</label><input id="raw-plate-width" name="width" type="number" step="0.01" min="0.01" required></div>
        <div><label>厚度 mm</label><input id="raw-plate-thickness" name="thickness" type="number" step="0.01" min="0.01" required></div>
        <div><label>密度 g/cm³</label><input id="raw-plate-density" name="density" type="number" step="0.001" min="0.001" value="7.85" required></div>
        <div><label>库位</label><input name="location" list="raw-plate-location-options" placeholder="例如 原料区-A01"><datalist id="raw-plate-location-options">{location_candidates}</datalist></div>
        <div><label>操作人</label><input name="operator_name" placeholder="例如 张三"></div>
        <div><label>备注</label><input name="remark" placeholder="例如 采购入库"></div>
        <p class="confirm-hint">提交前会先列出板料编号、材质、重量、尺寸、密度和库位，确认无误后才会真正入库。</p>
        <div style="align-self:end"><button class="btn" type="submit">计算并入库</button></div>
      </form>
    </section>
    <script>
      const rawPlateSpecSelect = document.getElementById('raw-plate-spec-select');
      rawPlateSpecSelect.addEventListener('change', () => {{
        const option = rawPlateSpecSelect.selectedOptions[0];
        if (!option || !option.value) return;
        document.getElementById('raw-plate-material').value = option.dataset.material || '';
        document.getElementById('raw-plate-length').value = option.dataset.length || '';
        document.getElementById('raw-plate-width').value = option.dataset.width || '';
        document.getElementById('raw-plate-thickness').value = option.dataset.thickness || '';
        document.getElementById('raw-plate-density').value = option.dataset.density || '7.85';
      }});
    </script>
    """
    return page("板料入库", body)


@router.post("/admin/raw-plates/inbound")
def create_raw_plate_from_page(
    material_code: str = Form(""),
    material: str = Form(...),
    total_weight_ton: float = Form(...),
    length: float = Form(...),
    width: float = Form(...),
    thickness: float = Form(...),
    density: float = Form(7.85),
    location: str = Form(""),
    operator_name: str = Form(""),
    remark: str = Form(""),
    _lock=Depends(locked_inventory_write),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if total_weight_ton <= 0 or length <= 0 or width <= 0 or thickness <= 0 or density <= 0:
        raise HTTPException(status_code=400, detail="总重量、长宽厚和密度必须大于0")
    single_weight_kg = length * width * thickness * density / 1_000_000
    total_weight_kg = total_weight_ton * 1000
    quantity = math.floor(total_weight_kg / single_weight_kg)
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="总重量不足一块板料")
    remaining_weight_kg = total_weight_kg - quantity * single_weight_kg
    material_value = material.strip()
    location_value = location.strip() or None
    batch_code = material_code.strip() or f"RAW-{china_now().strftime('%Y%m%d%H%M%S')}"
    usable_size = f"{length:g}×{width:g}×{thickness:g}mm"
    item = MaterialInventory(
        material_code=batch_code,
        inventory_type="raw_plate",
        material=material_value,
        thickness=thickness,
        shape="rectangle",
        length=length,
        width=width,
        usable_size=usable_size,
        quantity=quantity,
        location=location_value,
        status="available",
    )
    db.add(item)
    db.flush()
    transaction_remark = (
        f"{remark or '板料入库'}；总重量{total_weight_ton:g}吨，密度{density:g}g/cm³，"
        f"单块约{single_weight_kg:.3f}kg，入库{quantity}块，余重约{remaining_weight_kg:.3f}kg"
    )
    db.add(
        InventoryTransactionRecord(
            inventory_id=item.id,
            transaction_type="in",
            quantity=quantity,
            before_quantity=0,
            after_quantity=quantity,
            operator_name=operator_name or None,
            remark=transaction_remark,
        )
    )
    record_operation_log(
        db,
        "raw_plate_inbound",
        "inventory",
        item.id,
        operator_name or None,
        transaction_remark,
        after_data=inventory_snapshot(item),
    )
    db.commit()
    return RedirectResponse("/admin/raw-plates", status_code=303)


@router.get("/admin/raw-plates/outbound", response_class=HTMLResponse)
def raw_plate_outbound_page(
    material: str = "",
    length: str = "",
    width: str = "",
    thickness: str = "",
    location: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    query = db.query(MaterialInventory).filter(
        MaterialInventory.inventory_type == "raw_plate",
        MaterialInventory.quantity > 0,
    )
    if material.strip():
        query = query.filter(MaterialInventory.material.ilike(f"%{material.strip()}%"))
    length_value = optional_float(length)
    if length_value is not None:
        query = query.filter(MaterialInventory.length == length_value)
    width_value = optional_float(width)
    if width_value is not None:
        query = query.filter(MaterialInventory.width == width_value)
    thickness_value = optional_float(thickness)
    if thickness_value is not None:
        query = query.filter(MaterialInventory.thickness == thickness_value)
    if location.strip():
        query = query.filter(MaterialInventory.location.ilike(f"%{location.strip()}%"))
    items = query.order_by(MaterialInventory.created_at.asc()).all()
    summary = {}
    for item in items:
        key = (item.material, item.length, item.width, item.thickness)
        if key not in summary:
            summary[key] = {
                "material": item.material,
                "length": item.length,
                "width": item.width,
                "thickness": item.thickness,
                "quantity": 0,
                "batch_count": 0,
                "locations": set(),
            }
        summary[key]["quantity"] += item.quantity
        summary[key]["batch_count"] += 1
        if item.location:
            summary[key]["locations"].add(item.location)
    summary_rows = "".join(
        f"<tr><td>{group['material']}</td><td>{group['length'] or '-'}</td><td>{group['width'] or '-'}</td><td>{group['thickness']}</td><td><strong>{group['quantity']}</strong></td><td>{group['batch_count']}</td><td>{' / '.join(sorted(group['locations'])) or '-'}</td><td><a class='btn secondary' href='/admin/raw-plates/outbound?material={quote(str(group['material']), safe='')}&length={quote(str(group['length'] or ''), safe='')}&width={quote(str(group['width'] or ''), safe='')}&thickness={quote(str(group['thickness'] or ''), safe='')}'>选择出库</a></td></tr>"
        for group in summary.values()
    )
    material_options = datalist_options(inventory_distinct_options(db, "raw_plate", "material", quantity_positive=True))
    length_options = datalist_options(inventory_distinct_options(db, "raw_plate", "length", quantity_positive=True))
    width_options = datalist_options(inventory_distinct_options(db, "raw_plate", "width", quantity_positive=True))
    thickness_options = datalist_options(inventory_distinct_options(db, "raw_plate", "thickness", quantity_positive=True))
    location_options = datalist_options(inventory_distinct_options(db, "raw_plate", "location", quantity_positive=True))
    location_candidates = datalist_options(inventory_distinct_options(db, "raw_plate", "location", quantity_positive=True))
    customer_candidates = datalist_options(transaction_customer_options(db))
    body = f"""
    <div class="top"><div><h1>板料出库</h1><p class="muted">按材质和长宽厚申请出库，系统自动按最早入库批次先进先出扣减。</p></div><div class="actions"><a class="btn secondary" href="/admin/raw-plates">返回板料库存</a><a class="btn secondary" href="/admin/raw-plates/transactions">板料流水</a></div></div>
    <section class="card">
      <form method="get" action="/admin/raw-plates/outbound" class="actions" style="justify-content:flex-start">
        <input name="material" value="{safe_value(material.strip())}" list="raw-out-material-options" placeholder="材质" style="width:150px"><datalist id="raw-out-material-options">{material_options}</datalist>
        <input name="length" value="{safe_value(length.strip())}" list="raw-out-length-options" placeholder="长度" style="width:120px"><datalist id="raw-out-length-options">{length_options}</datalist>
        <input name="width" value="{safe_value(width.strip())}" list="raw-out-width-options" placeholder="宽度" style="width:120px"><datalist id="raw-out-width-options">{width_options}</datalist>
        <input name="thickness" value="{safe_value(thickness.strip())}" list="raw-out-thickness-options" placeholder="厚度" style="width:120px"><datalist id="raw-out-thickness-options">{thickness_options}</datalist>
        <input name="location" value="{safe_value(location.strip())}" list="raw-out-filter-location-options" placeholder="库位" style="width:140px"><datalist id="raw-out-filter-location-options">{location_options}</datalist>
        <button class="btn" type="submit">查看可用规格</button>
        <a class="btn secondary" href="/admin/raw-plates/outbound">清空</a>
      </form>
    </section>
    <section class="card">
      <h2>确认出库</h2>
      <p class="muted">先在下方“当前可用规格”里点击“选择出库”，系统会自动带入规格信息。</p>
      <form method="post" action="/admin/raw-plates/outbound" class="form-grid" data-confirm-flow="true" data-confirm-title="确认板料出库" data-confirm-note="库位为空时，系统会按所有库位的最早入库批次 FIFO 扣减，并生成出库流水。">
        <div><label>材质</label><input name="material" value="{html.escape(material.strip())}" readonly required></div>
        <div><label>长度 mm</label><input name="length" type="number" step="0.01" min="0.01" value="{html.escape(length.strip())}" readonly required></div>
        <div><label>宽度 mm</label><input name="width" type="number" step="0.01" min="0.01" value="{html.escape(width.strip())}" readonly required></div>
        <div><label>厚度 mm</label><input name="thickness" type="number" step="0.01" min="0.01" value="{html.escape(thickness.strip())}" readonly required></div>
        <div><label>出库块数</label><input name="quantity" type="number" min="1" value="1" required></div>
        <div><label>指定库位，可选</label><input name="location" value="{html.escape(location.strip())}" list="raw-out-location-options" placeholder="不填则所有库位FIFO"><datalist id="raw-out-location-options">{location_candidates}</datalist></div>
        <div><label>客户/去向</label><input name="customer_name" list="raw-out-customer-options" placeholder="例如 XX客户 / 车间领用"><datalist id="raw-out-customer-options">{customer_candidates}</datalist></div>
        <div><label>操作人</label><input name="operator_name" placeholder="例如 张三"></div>
        <div><label>备注</label><input name="remark" placeholder="例如 生产领料"></div>
        <p class="confirm-hint">提交前会先核对材质、长宽厚、出库块数、指定库位和客户/去向，确认后才扣减库存。</p>
        <div style="align-self:end"><button class="btn" type="submit">确认出库</button></div>
      </form>
    </section>
    <section class="card"><h2>当前可用规格</h2><table><thead><tr><th>材质</th><th>长mm</th><th>宽mm</th><th>厚mm</th><th>可出库总块数</th><th>批次数</th><th>库位</th><th>操作</th></tr></thead><tbody>{summary_rows or "<tr><td colspan='8'>暂无可出库板料。</td></tr>"}</tbody></table></section>
    """
    return page("板料出库", body)


@router.post("/admin/raw-plates/outbound")
def outbound_raw_plate_from_page(
    material: str = Form(...),
    length: float = Form(...),
    width: float = Form(...),
    thickness: float = Form(...),
    quantity: int = Form(...),
    location: str = Form(""),
    customer_name: str = Form(""),
    operator_name: str = Form(""),
    remark: str = Form(""),
    _lock=Depends(locked_inventory_write),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if not material.strip():
        raise HTTPException(status_code=400, detail="材质不能为空")
    if length <= 0 or width <= 0 or thickness <= 0:
        raise HTTPException(status_code=400, detail="长宽厚必须大于0")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="出库块数必须大于0")
    query = db.query(MaterialInventory).filter(
        MaterialInventory.inventory_type == "raw_plate",
        MaterialInventory.material == material.strip(),
        MaterialInventory.length == length,
        MaterialInventory.width == width,
        MaterialInventory.thickness == thickness,
        MaterialInventory.quantity > 0,
    )
    location_value = location.strip()
    if location_value:
        query = query.filter(MaterialInventory.location == location_value)
    batches = query.order_by(MaterialInventory.created_at.asc()).all()
    available_quantity = sum(item.quantity for item in batches)
    if available_quantity < quantity:
        raise HTTPException(status_code=400, detail=f"板料库存不足，当前可出库 {available_quantity} 块")
    remaining = quantity
    affected_batches = []
    customer_value = customer_name.strip()
    for item in batches:
        if remaining <= 0:
            break
        outbound_quantity = min(item.quantity, remaining)
        before_data = inventory_snapshot(item)
        before_quantity = item.quantity
        item.quantity -= outbound_quantity
        item.status = "used" if item.quantity <= 0 else "available"
        record_remark = remark or "板料出库"
        db.add(
            InventoryTransactionRecord(
                inventory_id=item.id,
                transaction_type="out",
                quantity=outbound_quantity,
                before_quantity=before_quantity,
                after_quantity=item.quantity,
                operator_name=operator_name or None,
                customer_name=customer_value or None,
                remark=record_remark,
            )
        )
        affected_batches.append(f"{item.material_code or item.id}:{outbound_quantity}")
        record_operation_log(
            db,
            "raw_plate_outbound",
            "inventory",
            item.id,
            operator_name or None,
            f"{record_remark}；FIFO扣减批次 {item.material_code or item.id}，数量 {outbound_quantity}{f'，客户/去向 {customer_value}' if customer_value else ''}",
            before_data=before_data,
            after_data=inventory_snapshot(item),
        )
        remaining -= outbound_quantity
    record_operation_log(
        db,
        "raw_plate_outbound_fifo",
        "inventory",
        None,
        operator_name or None,
        f"板料按规格出库：{material.strip()} {length:g}×{width:g}×{thickness:g}mm，数量 {quantity}{f'，客户/去向 {customer_value}' if customer_value else ''}；批次扣减 {'，'.join(affected_batches)}",
    )
    db.commit()
    return RedirectResponse("/admin/raw-plates", status_code=303)


@router.get("/admin/raw-plates/transactions", response_class=HTMLResponse)
def raw_plate_transactions_page(q: str = "", material: str = "", transaction_type: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    records_query = db.query(InventoryTransactionRecord)
    if transaction_type.strip():
        records_query = records_query.filter(InventoryTransactionRecord.transaction_type == transaction_type.strip())
    records = records_query.order_by(InventoryTransactionRecord.created_at.desc()).limit(500).all()
    inventory_ids = [record.inventory_id for record in records]
    inventory_map = {
        item.id: item
        for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()
    } if inventory_ids else {}
    rows = ""
    keyword = q.strip().lower()
    for record in records:
        item = inventory_map.get(record.inventory_id)
        if not item or item.inventory_type != "raw_plate":
            continue
        if material.strip() and material.strip() not in item.material:
            continue
        if keyword:
            searchable = " ".join(
                str(value or "")
                for value in (
                    item.material_code,
                    item.material,
                    item.usable_size,
                    item.location,
                    record.customer_name,
                    record.operator_name,
                    record.remark,
                )
            ).lower()
            if keyword not in searchable:
                continue
        reverse_form = "-"
        if record.transaction_type in ("in", "out") and record.reversed_transaction_id is None:
            reverse_form = f"""
            <form method="post" action="/admin/raw-plates/transactions/{record.id}/reverse" class="actions" style="gap:6px;justify-content:flex-start">
              <input name="operator_name" placeholder="操作人" style="width:90px">
              <input name="remark" placeholder="撤回原因" style="width:120px">
              <button class="btn secondary" type="submit">撤回</button>
            </form>
            """
        rows += f"<tr><td>{item.material_code or '-'}</td><td>{item.material}</td><td>{item.usable_size or '-'}</td><td>{item.location or '-'}</td><td>{transaction_label(record.transaction_type)}</td><td>{record.quantity}</td><td>{record.before_quantity}</td><td>{record.after_quantity}</td><td>{record.customer_name or '-'}</td><td>{record.operator_name or '-'}</td><td>{record.remark or '-'}</td><td>{record.created_at}</td><td>{reverse_form}</td></tr>"
    material_options = datalist_options(inventory_distinct_options(db, "raw_plate", "material"))
    type_options = "".join(
        f"<option value='{value}' {'selected' if transaction_type == value else ''}>{label}</option>"
        for value, label in (("", "全部类型"), ("in", "入库"), ("out", "出库"), ("confirm", "确认"))
    )
    body = f"""
    <div class="top"><div><h1>板料流水</h1><p class="muted">查看原料板料的入库、出库流水和计算备注。</p></div><div class="actions"><a class="btn secondary" href="/admin/raw-plates">返回板料库存</a><a class="btn secondary" href="/admin/raw-plates/inbound">板料入库</a><a class="btn secondary" href="/admin/raw-plates/outbound">板料出库</a><a class="btn secondary" href="/admin/exports/raw_plate_transactions">导出Excel</a></div></div>
    <section class="card">
      <form method="get" action="/admin/raw-plates/transactions" class="actions" style="justify-content:flex-start">
        <input name="q" value="{safe_value(q.strip())}" placeholder="输入批次/材质/尺寸/库位" style="width:220px">
        <input name="material" value="{safe_value(material.strip())}" list="raw-transaction-material-options" placeholder="材质" style="width:150px"><datalist id="raw-transaction-material-options">{material_options}</datalist>
        <select name="transaction_type" style="width:130px">{type_options}</select>
        <button class="btn" type="submit">查询流水</button>
        <a class="btn secondary" href="/admin/raw-plates/transactions">清空</a>
      </form>
    </section>
    <section class="card"><table><thead><tr><th>批次编号</th><th>材质</th><th>尺寸</th><th>库位</th><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>客户/去向</th><th>操作人</th><th>备注</th><th>时间</th><th>操作</th></tr></thead><tbody>{rows or "<tr><td colspan='13'>暂无板料流水。</td></tr>"}</tbody></table></section>
    """
    return page("板料流水", body)


@router.post("/admin/raw-plates/transactions/{transaction_id}/reverse")
def reverse_raw_plate_transaction_from_page(
    transaction_id: int,
    operator_name: str = Form(""),
    remark: str = Form(""),
    _lock=Depends(locked_inventory_write),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    record = db.get(InventoryTransactionRecord, transaction_id)
    if not record:
        raise HTTPException(status_code=404, detail="板料流水不存在")
    item = db.get(MaterialInventory, record.inventory_id)
    if not item or item.inventory_type != "raw_plate":
        raise HTTPException(status_code=400, detail="该流水不是板料流水")
    reverse_inventory_transaction(transaction_id, operator_name or None, remark or "撤回板料流水", db)
    db.commit()
    return RedirectResponse("/admin/raw-plates/transactions", status_code=303)


@router.get("/admin/inventory/inbound", response_class=HTMLResponse)
def inventory_inbound_page(db: Session = Depends(get_db)) -> HTMLResponse:
    drawing_options = confirmed_drawing_options(db)
    location_candidates = datalist_options(inventory_distinct_options(db, "product", "location"))
    client_request_id = uuid4().hex
    body = f"""
    <div class="top"><div><h1>成品入库</h1><p class="muted">选择已确认图纸对应的产品型号，填写入库数量和库位。</p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">返回成品库存</a></div></div>
    <section class="card">
      <form method="post" action="/admin/inventory" class="form-grid" data-confirm-flow="true" data-confirm-title="确认成品入库" data-confirm-note="系统会按所选图纸生成成品库存，并写入成品入库流水。">
        <input type="hidden" name="client_request_id" value="{client_request_id}">
        <div><label>筛选产品型号</label><input type="search" data-select-filter="product-inbound-drawing-select" placeholder="输入型号、分类、名称、材质或厚度"></div>
        <div><label>选择产品型号</label><select id="product-inbound-drawing-select" name="drawing_id" required>{drawing_options}</select></div>
        <div><label>数量</label><input name="quantity" type="number" value="1" min="1" required></div>
        <div><label>库位</label><input name="location" list="product-in-location-options" placeholder="例如 A-01"><datalist id="product-in-location-options">{location_candidates}</datalist></div>
        <div><label>纸材质/颜色</label><input name="paper_material" placeholder="例如 蓝色纸 / 黄色纸"></div>
        <div><label>操作人</label><input name="operator_name" placeholder="例如 张三"></div>
        <p class="confirm-hint">提交前会先核对产品型号、数量、库位和操作人，确认无误后才会入库。</p>
        <div style="align-self:end"><button class="btn" type="submit">确认入库</button></div>
      </form>
    </section>
    """
    return page("成品入库", body)


@router.get("/admin/inventory/outbound", response_class=HTMLResponse)
def inventory_outbound_page(
    q: str = "",
    material: str = "",
    thickness: str = "",
    location: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    client_request_id = uuid4().hex
    query = db.query(MaterialInventory).filter(
        MaterialInventory.inventory_type == "product",
        MaterialInventory.quantity > 0,
    )
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
    thickness_value = optional_float(thickness)
    if thickness_value is not None:
        query = query.filter(MaterialInventory.thickness == thickness_value)
    if location.strip():
        query = query.filter(MaterialInventory.location.ilike(f"%{location.strip()}%"))
    items = query.order_by(MaterialInventory.created_at.asc()).all()
    grouped = {}
    for item in items:
        code = item.material_code or item.source_product_code or "未编号"
        if code not in grouped:
            grouped[code] = {
                "code": code,
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
    drawings = (
        db.query(ProductDrawing)
        .filter(ProductDrawing.confirmed == 1, ProductDrawing.is_active == 1)
        .order_by(ProductDrawing.product_code.asc(), ProductDrawing.version.desc())
        .all()
    )
    drawing_map = {}
    for drawing in drawings:
        if drawing.product_code and drawing.product_code not in drawing_map:
            drawing_map[drawing.product_code] = drawing
    drawing_options = "".join(
        f"<option value='{drawing_map[code].id}'>{html.escape(code)}｜{html.escape(drawing_map[code].product_category or '-')}｜{drawing_version_code(drawing_map[code])}｜{html.escape(str(group['material']))}｜厚度 {group['thickness']}｜纸材质 {' / '.join(sorted(group['paper_materials'])) or '-'}｜库存 {group['quantity']}｜库位 {' / '.join(sorted(group['locations'])) or '-'}</option>"
        for code, group in grouped.items()
        if code in drawing_map
    ) or "<option value='' disabled selected>暂无可出库成品库存</option>"
    rows = "".join(
        f"""
        <tr>
          <td>{html.escape(str(group['code']))}</td><td>{group['material']}</td><td>{group['thickness']}</td><td>{' / '.join(sorted(group['paper_materials'])) or '-'}</td><td><strong>{group['quantity']}</strong></td><td>{' / '.join(sorted(group['locations'])) or '-'}</td><td>{group['latest'] or '-'}</td><td><a class='btn secondary' href='/admin/inventory/product/{quote(str(group['code']), safe="")}'>查看明细</a></td>
        </tr>
        """
        for group in grouped.values()
    )
    product_codes = inventory_distinct_options(db, "product", "material_code", quantity_positive=True)
    source_codes = inventory_distinct_options(db, "product", "source_product_code", quantity_positive=True)
    product_code_options = datalist_options(product_codes + source_codes)
    material_options = select_options(inventory_distinct_options(db, "product", "material", quantity_positive=True), material, "全部材质")
    thickness_options = select_options(inventory_distinct_options(db, "product", "thickness", quantity_positive=True), thickness, "全部厚度")
    location_options = select_options(inventory_distinct_options(db, "product", "location", quantity_positive=True), location, "全部库位")
    location_candidates = datalist_options(inventory_distinct_options(db, "product", "location", quantity_positive=True))
    customer_candidates = datalist_options(transaction_customer_options(db))
    purpose_options = "".join(
        f"<option value='{value}' {'selected' if value == 'sales' else ''}>{label}</option>"
        for value, label in OUTBOUND_PURPOSES
    )
    body = f"""
    <div class="top"><div><h1>成品出库</h1><p class="muted">在本页查看当前成品库存，并按产品型号填写出库数量；库位不填时按所有库位先进先出扣减。</p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">返回成品库存</a></div></div>
    <section class="card">
      <form method="get" action="/admin/inventory/outbound" class="actions" style="justify-content:flex-start;margin-bottom:14px">
        <input name="q" value="{safe_value(keyword)}" list="product-outbound-code-options" placeholder="输入型号筛选" style="width:220px"><datalist id="product-outbound-code-options">{product_code_options}</datalist>
        <select name="material" style="width:150px">{material_options}</select>
        <select name="thickness" style="width:130px">{thickness_options}</select>
        <select name="location" style="width:150px">{location_options}</select>
        <button class="btn secondary" type="submit">筛选</button>
        <a class="btn secondary" href="/admin/inventory/outbound">清空</a>
      </form>
      <form method="post" action="/admin/inventory/product/out" class="form-grid" data-confirm-flow="true" data-confirm-title="确认成品出库" data-confirm-note="库位为空时，系统会按所有库位的最早入库批次 FIFO 扣减，并生成成品出库流水。">
        <input type="hidden" name="client_request_id" value="{client_request_id}">
        <div><label>筛选产品型号</label><input type="search" data-select-filter="product-outbound-drawing-select" placeholder="输入型号、分类、材质、纸材质、厚度、库存或库位"></div>
        <div><label>选择产品型号</label><select id="product-outbound-drawing-select" name="drawing_id" required>{drawing_options}</select></div>
        <div><label>出库数量</label><input name="quantity" type="number" value="1" min="1" required></div>
        <div><label>指定库位，可选</label><input name="location" value="{html.escape(location.strip())}" list="product-out-location-options" placeholder="不填则所有库位FIFO"><datalist id="product-out-location-options">{location_candidates}</datalist></div>
        <div><label>客户/去向</label><input name="customer_name" list="product-out-customer-options" placeholder="例如 XX客户 / 车间领用"><datalist id="product-out-customer-options">{customer_candidates}</datalist></div>
        <div><label>用途</label><select name="outbound_purpose">{purpose_options}</select></div>
        <div><label>操作人</label><input name="operator_name" placeholder="例如 张三"></div>
        <div><label>备注</label><input name="remark" placeholder="例如 发货/领用"></div>
        <p class="confirm-hint">提交前会先核对产品型号、出库数量、指定库位、客户/去向、用途和备注，确认后才扣减库存。</p>
        <div style="align-self:end"><button class="btn" type="submit">确认出库</button></div>
      </form>
    </section>
    <section class="card"><h2>当前可出库成品库存</h2><table><thead><tr><th>产品编号</th><th>材质</th><th>厚度</th><th>纸材质</th><th>可出库数量</th><th>库位</th><th>最近更新时间</th><th>操作</th></tr></thead><tbody>{rows or "<tr><td colspan='8'>暂无可出库成品库存。</td></tr>"}</tbody></table></section>
    """
    return page("成品出库", body)


@router.post("/admin/inventory")
def create_inventory_from_page(
    drawing_id: int = Form(...),
    quantity: int = Form(1),
    location: str = Form(""),
    paper_material: str = Form(""),
    operator_name: str = Form(""),
    client_request_id: str = Form(""),
    _lock=Depends(locked_inventory_write),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    client_request_value = client_request_id.strip() if isinstance(client_request_id, str) else ""
    idempotency_key = f"admin_product_inbound:{client_request_value}" if client_request_value else None
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing or drawing.confirmed != 1 or drawing.is_active != 1:
        raise HTTPException(status_code=404, detail="已确认图纸不存在")
    result = product_inbound_from_drawing(
        drawing=drawing,
        quantity=quantity,
        location=location,
        paper_material=paper_material,
        operator_name=operator_name or None,
        db=db,
        idempotency_key=idempotency_key,
    )
    if result.duplicated_request:
        return RedirectResponse("/admin/inventory", status_code=303)
    record_operation_log(
        db,
        "product_inbound",
        "inventory",
        result.item.id,
        operator_name or None,
        f"产品入库：{drawing.product_code}，数量 {quantity}",
        before_data={"quantity": result.before_total_quantity, "drawing": drawing_snapshot(drawing)},
        after_data=inventory_snapshot(result.item),
    )
    db.commit()
    return RedirectResponse("/admin/inventory", status_code=303)


@router.post("/admin/inventory/product/out")
def outbound_inventory_from_page(
    drawing_id: int = Form(...),
    quantity: int = Form(...),
    location: str = Form(""),
    customer_name: str = Form(""),
    outbound_purpose: str = Form("sales"),
    operator_name: str = Form(""),
    remark: str = Form(""),
    client_request_id: str = Form(""),
    _lock=Depends(locked_inventory_write),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    idempotency_key = f"admin_product_outbound:{client_request_id.strip()}" if client_request_id.strip() else None
    if idempotency_key:
        existing_record = db.query(InventoryTransactionRecord).filter(InventoryTransactionRecord.idempotency_key == idempotency_key).first()
        if existing_record:
            return RedirectResponse("/admin/inventory", status_code=303)
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing or drawing.confirmed != 1 or drawing.is_active != 1 or not drawing.product_code:
        raise HTTPException(status_code=404, detail="已确认图纸不存在")
    query = db.query(MaterialInventory).filter(
        MaterialInventory.inventory_type == "product",
        MaterialInventory.material_code == drawing.product_code,
        MaterialInventory.quantity > 0,
    )
    location_value = location.strip()
    if location_value:
        query = query.filter(MaterialInventory.location == location_value)
    batches = query.order_by(MaterialInventory.created_at.asc()).all()
    before_total_quantity = sum(item.quantity for item in batches)
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="出库数量必须大于0")
    if before_total_quantity < quantity:
        stock_scope = f"库位 {location_value} " if location_value else ""
        raise HTTPException(status_code=400, detail=f"{stock_scope}库存不足，当前总库存 {before_total_quantity}")
    remaining = quantity
    affected_items = []
    customer_value = customer_name.strip()
    purpose_value = normalize_outbound_purpose(outbound_purpose)
    for item in batches:
        if remaining <= 0:
            break
        item_before_quantity = item.quantity
        deduction = min(item.quantity, remaining)
        item.quantity -= deduction
        remaining -= deduction
        if item.quantity <= 0:
            item.status = "used"
        else:
            item.status = "available"
        affected_items.append((item, deduction, item_before_quantity, item.quantity))
    after_total_quantity = before_total_quantity - quantity
    for index, (item, deduction, item_before_quantity, item_after_quantity) in enumerate(affected_items):
        db.add(
            InventoryTransactionRecord(
                inventory_id=item.id,
                transaction_type="out",
                quantity=deduction,
                before_quantity=item_before_quantity,
                after_quantity=item_after_quantity,
                idempotency_key=idempotency_key if index == 0 else None,
                operator_name=operator_name or None,
                customer_name=customer_value or None,
                outbound_purpose=purpose_value,
                remark=remark or "产品出库",
            )
        )
    record_operation_log(
        db,
        "product_outbound",
        "inventory",
        affected_items[0][0].id if affected_items else None,
        operator_name or None,
        remark or f"产品出库：{drawing.product_code}，数量 {quantity}{f'，客户/去向 {customer_value}' if customer_value else ''}",
        before_data={"quantity": before_total_quantity, "location": location_value or None, "drawing": drawing_snapshot(drawing)},
        after_data={"quantity": after_total_quantity, "location": location_value or None, "customer_name": customer_value or None, "outbound_purpose": purpose_value},
    )
    db.commit()
    return RedirectResponse("/admin/inventory", status_code=303)


@router.get("/admin/inventory/product/{product_code}", response_class=HTMLResponse)
def inventory_product_detail_page(product_code: str, db: Session = Depends(get_db)) -> HTMLResponse:
    items = (
        db.query(MaterialInventory)
        .filter(
            MaterialInventory.inventory_type == "product",
            (MaterialInventory.material_code == product_code) | (MaterialInventory.source_product_code == product_code),
        )
        .order_by(MaterialInventory.created_at.desc())
        .all()
    )
    total_quantity = sum(item.quantity for item in items)
    item_map = {item.id: item for item in items}
    item_ids = list(item_map.keys())
    records = (
        db.query(InventoryTransactionRecord)
        .filter(InventoryTransactionRecord.inventory_id.in_(item_ids))
        .order_by(InventoryTransactionRecord.created_at.desc())
        .all()
    ) if item_ids else []
    rows = "".join(
        f"""
        <tr>
          <td>{item.material_code or product_code}</td>
          <td>{item.quantity}</td>
          <td>{item.location or '-'}</td>
          <td>{item.paper_material or '-'}</td>
          <td>{item.material}</td>
          <td>{fmt_option(item.product_thickness or item.thickness) or '-'}</td>
          <td>{fmt_option(item.plate_thickness or item.thickness) or '-'}</td>
          <td>{item.status}</td>
          <td>{item.created_at}</td>
          <td>{item.updated_at}</td>
        </tr>
        """
        for item in items
    )
    transaction_rows = "".join(
        f"""
        <tr>
          <td>{transaction_label(record.transaction_type)}</td>
          <td>{record.quantity}</td>
          <td>{record.before_quantity}</td>
          <td>{record.after_quantity}</td>
          <td>{item_map.get(record.inventory_id).location if item_map.get(record.inventory_id) and item_map.get(record.inventory_id).location else '-'}</td>
          <td>{record.customer_name or '-'}</td>
          <td>{record.operator_name or '-'}</td>
          <td>{record.remark or '-'}</td>
          <td>{record.created_at}</td>
        </tr>
        """
        for record in records
    )
    body = f"""
    <div class="top"><div><h1>成品明细：{html.escape(product_code)}</h1><p class="muted">当前总数量：<strong>{total_quantity}</strong></p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">返回成品汇总</a></div></div>
    <section class="card"><h2>入库批次</h2><table><thead><tr><th>产品型号</th><th>数量</th><th>库位</th><th>纸材质</th><th>材质</th><th>总成品厚度</th><th>钢板厚度</th><th>状态</th><th>创建时间</th><th>更新时间</th></tr></thead><tbody>{rows or "<tr><td colspan='10'>暂无该成品库存。</td></tr>"}</tbody></table></section>
    <section class="card"><h2>产品流水</h2><table><thead><tr><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>关联库位</th><th>客户/去向</th><th>操作人</th><th>备注</th><th>时间</th></tr></thead><tbody>{transaction_rows or "<tr><td colspan='9'>暂无该产品流水。</td></tr>"}</tbody></table></section>
    """
    return page("库存明细", body)


@router.post("/admin/inventory/{inventory_id}/adjust")
def adjust_inventory_from_page(
    inventory_id: int,
    transaction_type: str = Form(...),
    quantity: int = Form(...),
    operator_name: str = Form(""),
    remark: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    reject_direct_inventory_write()
    item = db.get(MaterialInventory, inventory_id)
    if not item:
        raise HTTPException(status_code=404, detail="库存不存在")
    before_data = inventory_snapshot(item)
    record = adjust_inventory_quantity(
        item,
        transaction_type,
        quantity,
        operator_name or None,
        remark or ("手工入库" if transaction_type == "in" else "手工出库"),
        db,
    )
    db.flush()
    record_operation_log(
        db,
        "inventory_adjust",
        "inventory",
        item.id,
        operator_name or None,
        remark or record.remark,
        before_data=before_data,
        after_data=inventory_snapshot(item),
    )
    db.commit()
    return RedirectResponse("/admin/inventory", status_code=303)


def transaction_label(transaction_type: str) -> str:
    return {"in": "入库", "out": "出库", "confirm": "确认入库"}.get(transaction_type, transaction_type)


def scrap_location_label(item: MaterialInventory | None) -> str:
    if not item:
        return "-"
    if item.status == "available" and item.location in ("待入库", "未入库"):
        return "未设置库位"
    return item.location or "-"


@router.get("/admin/inventory/item/{inventory_id}", response_class=HTMLResponse)
def inventory_detail_page(inventory_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    item = db.get(MaterialInventory, inventory_id)
    if not item:
        raise HTTPException(status_code=404, detail="库存不存在")
    records = (
        db.query(InventoryTransactionRecord)
        .filter(InventoryTransactionRecord.inventory_id == inventory_id)
        .order_by(InventoryTransactionRecord.created_at.desc())
        .all()
    )
    rows = "".join(
        f"<tr><td>{transaction_label(r.transaction_type)}</td><td>{r.quantity}</td><td>{r.before_quantity}</td><td>{r.after_quantity}</td><td>{r.customer_name or '-'}</td><td>{r.operator_name or '-'}</td><td>{r.remark or '-'}</td><td>{r.created_at}</td></tr>"
        for r in records
    )
    display_code = item.material_code or item.source_product_code or "-"
    body = f"""
    <div class="top"><div><h1>成品详情：{display_code}</h1><p class="muted">查看该型号成品库存的基础信息和全部出入库流水。</p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">返回成品管理</a></div></div>
    <section class="card">
      <h2>基础信息</h2>
      <div class="grid">
        <div><span class="muted">材料编号</span><strong>{item.material_code or '-'}</strong></div>
        <div><span class="muted">类型</span><strong>{'余料' if item.inventory_type == 'scrap' else '库存'}</strong></div>
        <div><span class="muted">材质</span><strong>{item.material}</strong></div>
        <div><span class="muted">厚度</span><strong>{item.thickness}</strong></div>
        <div><span class="muted">形状</span><strong>{item.shape}</strong></div>
        <div><span class="muted">直径</span><strong>{item.diameter or '-'}</strong></div>
        <div><span class="muted">长宽</span><strong>{item.length or '-'} × {item.width or '-'}</strong></div>
        <div><span class="muted">数量</span><strong>{item.quantity}</strong></div>
        <div><span class="muted">库位</span><strong>{item.location or '-'}</strong></div>
        <div><span class="muted">纸材质</span><strong>{item.paper_material or '-'}</strong></div>
        <div><span class="muted">状态</span><strong>{item.status}</strong></div>
        <div><span class="muted">来源产品</span><strong>{item.source_product_code or '-'}</strong></div>
        <div><span class="muted">来源图纸</span><strong>{drawing_version_label(db, item.source_drawing_id)}</strong></div>
        <div><span class="muted">可用尺寸</span><strong>{item.usable_size or '-'}</strong></div>
      </div>
    </section>
    <section class="card"><h2>该成品流水</h2><table><thead><tr><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>客户/去向</th><th>操作人</th><th>备注</th><th>时间</th></tr></thead><tbody>{rows or "<tr><td colspan='8'>暂无该成品流水。</td></tr>"}</tbody></table></section>
    """
    return page("库存详情", body)


def outbound_report_range(period: str, start_date: str, end_date: str) -> tuple[datetime, datetime, str]:
    now = china_now()
    if start_date and end_date:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        return start, end, f"{start_date} 至 {end_date}"
    if period == "year":
        start = datetime(now.year, 1, 1)
        end = datetime(now.year + 1, 1, 1)
        return start, end, f"{now.year}年"
    if period == "month":
        start = datetime(now.year, now.month, 1)
        end = datetime(now.year + 1, 1, 1) if now.month == 12 else datetime(now.year, now.month + 1, 1)
        return start, end, f"{now.year}年{now.month}月"
    start = datetime(now.year, now.month, now.day)
    end = start + timedelta(days=1)
    return start, end, "今天"


def outbound_report_rows(records: list[InventoryTransactionRecord], inventory_map: dict[int, MaterialInventory], inventory_type: str) -> tuple[str, int]:
    rows = []
    total_quantity = 0
    for record in records:
        item = inventory_map.get(record.inventory_id)
        if not item:
            continue
        if inventory_type == "product" and item.inventory_type != "product":
            continue
        if inventory_type == "scrap" and item.inventory_type != "scrap":
            continue
        if inventory_type == "raw_plate" and item.inventory_type != "raw_plate":
            continue
        if inventory_type == "product":
            key = item.material_code or item.source_product_code or "-"
            name = item.material or "-"
            spec = f"厚度 {item.thickness:g}" if item.thickness is not None else "-"
            location = item.location or "-"
        elif inventory_type == "raw_plate":
            key = item.material or "-"
            name = item.usable_size or f"{item.length:g}×{item.width:g}×{item.thickness:g}mm"
            spec = f"长 {item.length:g}｜宽 {item.width:g}｜厚 {item.thickness:g}"
            location = item.location or "-"
        else:
            key = item.material or "-"
            name = item.usable_size or "-"
            spec = f"厚度 {item.thickness:g}｜直径 {item.diameter:g}" if item.diameter is not None else f"厚度 {item.thickness:g}"
            location = scrap_location_label(item)
        rows.append(
            f"""
            <tr>
              <td>{record.id}</td>
              <td>{html.escape(record.created_at.strftime('%Y-%m-%d %H:%M:%S') if record.created_at else '-')}</td>
              <td>{html.escape(str(key))}</td>
              <td>{html.escape(str(name))}</td>
              <td>{html.escape(str(spec))}</td>
              <td>{html.escape(str(location))}</td>
              <td>{html.escape(record.customer_name or '-')}</td>
              <td>{record.quantity}</td>
              <td>{html.escape(record.operator_name or '-')}</td>
              <td>{html.escape(record.remark or '-')}</td>
            </tr>
            """
        )
        total_quantity += record.quantity
    return "".join(rows), total_quantity


@router.get("/admin/reports/product-outbound", response_class=HTMLResponse)
def product_outbound_analysis_page(
    product_code: str = "",
    period: str = "recent_365",
    start_date: str = "",
    end_date: str = "",
    customer: str = "",
    purpose: str = "sales",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        result = analyze_product_outbound(
            db,
            product_code=product_code,
            period=period,
            start_date=start_date,
            end_date=end_date,
            customer=customer,
            purpose=purpose,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式错误，请使用YYYY-MM-DD")
    summary = result["summary"]
    product_codes = inventory_distinct_options(db, "product", "material_code") + inventory_distinct_options(db, "product", "source_product_code")
    product_options = datalist_options(product_codes)
    customer_options = datalist_options(transaction_customer_options(db))
    period_options = "".join(
        f"<option value='{value}' {'selected' if period == value else ''}>{label}</option>"
        for value, label in (
            ("recent_365", "近一年"),
            ("recent_90", "近90天"),
            ("recent_30", "近30天"),
            ("month", "本月"),
            ("quarter", "本季度"),
            ("year", "本年"),
            ("week", "本周"),
            ("today", "今天"),
            ("custom", "自定义时间段"),
        )
    )
    purpose_options = [f"<option value='' {'selected' if not purpose else ''}>全部用途</option>"]
    purpose_options.append(f"<option value='sales' {'selected' if purpose == 'sales' else ''}>销售/发货（含历史未分类）</option>")
    purpose_options.extend(
        f"<option value='{value}' {'selected' if purpose == value else ''}>{label}</option>"
        for value, label in OUTBOUND_PURPOSES
        if value != "sales"
    )
    monthly_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['month'])}</td>
          <td><strong>{row['sales_quantity']}</strong></td>
          <td>{row['quantity']}</td>
          <td>{row['transaction_count']}</td>
          <td>{row['customer_count']}</td>
        </tr>
        """
        for row in result["monthly_rows"]
    )
    detail_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['time'])}</td>
          <td>{html.escape(row['product_code'])}</td>
          <td><strong>{row['quantity']}</strong></td>
          <td>{html.escape(row['customer_name'])}</td>
          <td>{html.escape(row['purpose_label'])}</td>
          <td>{html.escape(row['location'])}</td>
          <td>{html.escape(row['operator_name'])}</td>
          <td>{html.escape(row['remark'])}</td>
        </tr>
        """
        for row in result["detail_rows"]
    )
    export_params = {
        "product_code": product_code.strip(),
        "period": period,
        "start_date": start_date.strip(),
        "end_date": end_date.strip(),
        "customer": customer.strip(),
        "purpose": purpose.strip(),
    }
    body = f"""
    <div class="top"><div><h1>产品出库分析</h1><p class="muted">按产品型号、时间范围、客户和用途分析销售/出库情况，用于下一阶段备货参考。当前范围：{html.escape(summary['range_label'])}</p></div><div class="actions"><a class="btn secondary" href="/admin/reports/outbound">综合出库统计</a><a class="btn secondary" href="/admin/inventory">成品库存</a><a class="btn secondary" href="{export_link('product_outbound_analysis', export_params)}">导出Excel</a></div></div>
    <section class="card">
      <form method="get" action="/admin/reports/product-outbound" class="form-grid">
        <div><label>产品型号</label><input name="product_code" value="{safe_value(product_code.strip())}" list="analysis-product-options" placeholder="输入型号筛选"><datalist id="analysis-product-options">{product_options}</datalist></div>
        <div><label>时间范围</label><select name="period">{period_options}</select></div>
        <div><label>开始日期</label><input type="date" name="start_date" value="{html.escape(start_date)}"></div>
        <div><label>结束日期</label><input type="date" name="end_date" value="{html.escape(end_date)}"></div>
        <div><label>客户/去向</label><input name="customer" value="{html.escape(customer)}" list="analysis-customer-options" placeholder="可按客户筛选"><datalist id="analysis-customer-options">{customer_options}</datalist></div>
        <div><label>用途</label><select name="purpose">{''.join(purpose_options)}</select></div>
        <div style="align-self:end"><button class="btn" type="submit">查询分析</button></div>
      </form>
      <p class="muted">选择“销售/发货”时，会把历史未填写用途的成品出库也计入销售口径。</p>
    </section>
    <section class="grid">
      <div class="card stat"><span class="muted">销售出库量</span><strong>{summary['sales_quantity']}</strong></div>
      <div class="card stat"><span class="muted">总出库量</span><strong>{summary['total_quantity']}</strong></div>
      <div class="card stat"><span class="muted">出库次数</span><strong>{summary['transaction_count']}</strong></div>
      <div class="card stat"><span class="muted">客户数</span><strong>{summary['customer_count']}</strong></div>
    </section>
    <section class="card">
      <h2>备货建议</h2>
      <div class="grid">
        <div><span class="muted">月均销售</span><strong>{summary['monthly_avg']}</strong></div>
        <div><span class="muted">最近3个月月均</span><strong>{summary['recent_3_month_avg']}</strong></div>
        <div><span class="muted">最高月销售</span><strong>{summary['peak_month_quantity']}</strong></div>
        <div><span class="muted">下一年度建议量</span><strong>{summary['suggested_year_quantity']}</strong></div>
        <div><span class="muted">加10%安全库存</span><strong>{summary['safety_stock_10']}</strong></div>
        <div><span class="muted">加20%安全库存</span><strong>{summary['safety_stock_20']}</strong></div>
      </div>
      <p class="muted">建议量按“月均销售”和“最近3个月月均”两者较高者估算，适合做生产计划初步参考。</p>
    </section>
    <section class="card"><h2>月度汇总</h2><table><thead><tr><th>月份</th><th>销售出库量</th><th>总出库量</th><th>出库次数</th><th>客户数</th></tr></thead><tbody>{monthly_rows or "<tr><td colspan='5'>当前条件暂无月度数据。</td></tr>"}</tbody></table></section>
    <section class="card"><h2>逐单明细</h2><table><thead><tr><th>出库时间</th><th>产品型号</th><th>数量</th><th>客户/去向</th><th>用途</th><th>库位</th><th>操作人</th><th>备注</th></tr></thead><tbody>{detail_rows or "<tr><td colspan='8'>当前条件暂无出库明细。</td></tr>"}</tbody></table></section>
    """
    return page("产品出库分析", body)


@router.get("/admin/reports/outbound", response_class=HTMLResponse)
def outbound_report_page(period: str = "day", start_date: str = "", end_date: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    try:
        start, end, range_label = outbound_report_range(period, start_date.strip(), end_date.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式错误，请使用YYYY-MM-DD")
    records = (
        db.query(InventoryTransactionRecord)
        .filter(
            InventoryTransactionRecord.transaction_type == "out",
            InventoryTransactionRecord.reversed_transaction_id.is_(None),
            InventoryTransactionRecord.created_at >= start,
            InventoryTransactionRecord.created_at < end,
        )
        .order_by(InventoryTransactionRecord.created_at.desc())
        .all()
    )
    inventory_ids = [record.inventory_id for record in records]
    inventory_map = {
        item.id: item
        for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()
    } if inventory_ids else {}
    product_rows, product_total = outbound_report_rows(records, inventory_map, "product")
    scrap_rows, scrap_total = outbound_report_rows(records, inventory_map, "scrap")
    raw_plate_rows, raw_plate_total = outbound_report_rows(records, inventory_map, "raw_plate")
    period_options = "".join(
        f"<option value='{value}' {'selected' if period == value else ''}>{label}</option>"
        for value, label in (("day", "今天"), ("month", "本月"), ("year", "本年"), ("custom", "自定义时间段"))
    )
    body = f"""
    <div class="top"><div><h1>综合出库统计</h1><p class="muted">查询天、月、年或某个时间段内的成品、余料和板料出库情况。当前范围：{html.escape(range_label)}</p></div><div class="actions"><a class="btn secondary" href="/admin/reports/product-outbound">产品出库分析</a><a class="btn secondary" href="/admin/inventory/transactions">成品流水</a><a class="btn secondary" href="/admin/scraps/transactions">余料流水</a><a class="btn secondary" href="/admin/raw-plates/transactions">板料流水</a><a class="btn secondary" href="{export_link('outbound_report', {'period': period, 'start_date': start_date.strip(), 'end_date': end_date.strip()})}">导出Excel</a></div></div>
    <section class="card">
      <form method="get" action="/admin/reports/outbound" class="form-grid">
        <div><label>快捷时间</label><select name="period">{period_options}</select></div>
        <div><label>开始日期</label><input type="date" name="start_date" value="{html.escape(start_date)}"></div>
        <div><label>结束日期</label><input type="date" name="end_date" value="{html.escape(end_date)}"></div>
        <div style="align-self:end"><button class="btn" type="submit">查询</button></div>
      </form>
      <p class="muted">填写开始和结束日期时，优先按自定义时间段查询；不填日期时按快捷时间查询。</p>
    </section>
    <section class="grid">
      <div class="card stat"><span class="muted">成品出库总数</span><strong>{product_total}</strong></div>
      <div class="card stat"><span class="muted">余料出库总数</span><strong>{scrap_total}</strong></div>
      <div class="card stat"><span class="muted">板料出库总块数</span><strong>{raw_plate_total}</strong></div>
    </section>
    <section class="card"><h2>成品出库逐单明细</h2><table><thead><tr><th>流水号</th><th>出库时间</th><th>产品型号/来源</th><th>材质</th><th>规格</th><th>库位</th><th>客户/去向</th><th>出库数量</th><th>操作人</th><th>备注</th></tr></thead><tbody>{product_rows or "<tr><td colspan='10'>该时间段暂无成品出库。</td></tr>"}</tbody></table></section>
    <section class="card"><h2>余料出库逐单明细</h2><table><thead><tr><th>流水号</th><th>出库时间</th><th>材质</th><th>可用尺寸</th><th>规格</th><th>库位</th><th>客户/去向</th><th>出库数量</th><th>操作人</th><th>备注</th></tr></thead><tbody>{scrap_rows or "<tr><td colspan='10'>该时间段暂无余料出库。</td></tr>"}</tbody></table></section>
    <section class="card"><h2>板料出库逐单明细</h2><table><thead><tr><th>流水号</th><th>出库时间</th><th>材质</th><th>板料规格</th><th>尺寸</th><th>库位</th><th>客户/去向</th><th>出库块数</th><th>操作人</th><th>备注</th></tr></thead><tbody>{raw_plate_rows or "<tr><td colspan='10'>该时间段暂无板料出库。</td></tr>"}</tbody></table></section>
    """
    return page("综合出库统计", body)


@router.get("/admin/inventory/transactions", response_class=HTMLResponse)
def inventory_transactions_page(product_code: str = "", transaction_type: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    product_filter = product_code.strip()
    records_query = db.query(InventoryTransactionRecord)
    if transaction_type.strip():
        records_query = records_query.filter(InventoryTransactionRecord.transaction_type == transaction_type.strip())
    records = records_query.order_by(InventoryTransactionRecord.created_at.desc()).limit(500).all()
    inventory_ids = [r.inventory_id for r in records]
    inventory_map = {
        item.id: item
        for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()
    } if inventory_ids else {}
    rows = ""
    for r in records:
        item = inventory_map.get(r.inventory_id)
        if not item or item.inventory_type != "product":
            continue
        row_product_code = item.material_code or item.source_product_code if item else "-"
        if product_filter and row_product_code != product_filter:
            continue
        product_link = (
            f"<a href='/admin/inventory/product/{quote(str(row_product_code), safe='')}'>{row_product_code}</a>"
            if item and item.inventory_type == "product" and row_product_code != "-"
            else row_product_code
        )
        before_quantity = "-" if r.transaction_type == "confirm" else r.before_quantity
        after_quantity = "-" if r.transaction_type == "confirm" else r.after_quantity
        quantity_label = r.after_quantity if r.transaction_type == "confirm" and r.quantity == 0 else r.quantity
        reverse_action = "-" if r.transaction_type not in ("in", "out") or r.reversed_transaction_id else f"<form method='post' action='/admin/inventory/transactions/{r.id}/reverse' class='actions' style='margin:0;justify-content:flex-start' onsubmit=\"return confirm('确定撤销这条流水吗？系统会生成一条反向流水，不会删除原记录。')\"><input name='operator_name' placeholder='操作人' style='width:80px'><input name='remark' placeholder='撤销原因' required style='width:120px'><button class='btn secondary' type='submit'>撤销</button></form>"
        rows += f"<tr><td>{product_link}</td><td>{transaction_label(r.transaction_type)}</td><td>{quantity_label}</td><td>{before_quantity}</td><td>{after_quantity}</td><td>{r.customer_name or '-'}</td><td>{r.operator_name or '-'}</td><td>{r.remark or '-'}</td><td>{r.created_at}</td><td>{reverse_action}</td></tr>"
    product_codes = inventory_distinct_options(db, "product", "material_code") + inventory_distinct_options(db, "product", "source_product_code")
    product_options = datalist_options(product_codes)
    type_options = "".join(
        f"<option value='{value}' {'selected' if transaction_type == value else ''}>{label}</option>"
        for value, label in (("", "全部类型"), ("in", "入库"), ("out", "出库"), ("confirm", "确认"))
    )
    body = f"""
    <div class="top"><div><h1>成品流水</h1><p class="muted">只查看成品库存的入库/出库记录；余料记录请到余料流水查看。</p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">返回成品管理</a><a class="btn secondary" href="/admin/scraps/transactions">余料流水</a><a class="btn secondary" href="/admin/exports/product_transactions">导出Excel</a></div></div>
    <section class="card">
      <form method="get" action="/admin/inventory/transactions" class="actions" style="justify-content:flex-start">
        <input name="product_code" value="{safe_value(product_filter)}" list="transaction-product-options" placeholder="输入型号筛选" style="width:220px"><datalist id="transaction-product-options">{product_options}</datalist>
        <select name="transaction_type" style="width:130px">{type_options}</select>
        <button class="btn" type="submit">查询流水</button>
        <a class="btn secondary" href="/admin/inventory/transactions">清空</a>
      </form>
    </section>
    <section class="card"><table><thead><tr><th>产品型号/来源</th><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>客户/去向</th><th>操作人</th><th>备注</th><th>时间</th><th>操作</th></tr></thead><tbody>{rows or "<tr><td colspan='10'>暂无成品流水。</td></tr>"}</tbody></table></section>
    """
    return page("成品流水", body)


@router.get("/admin/scraps/transactions", response_class=HTMLResponse)
def scrap_transactions_page(q: str = "", material: str = "", transaction_type: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    records_query = db.query(InventoryTransactionRecord)
    if transaction_type.strip():
        records_query = records_query.filter(InventoryTransactionRecord.transaction_type == transaction_type.strip())
    records = records_query.order_by(InventoryTransactionRecord.created_at.desc()).limit(500).all()
    inventory_ids = [r.inventory_id for r in records]
    inventory_map = {
        item.id: item
        for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()
    } if inventory_ids else {}
    rows = ""
    keyword = q.strip().lower()
    for r in records:
        item = inventory_map.get(r.inventory_id)
        if not item or item.inventory_type != "scrap":
            continue
        if material.strip() and material.strip() not in item.material:
            continue
        if keyword:
            searchable = " ".join(
                str(value or "")
                for value in (
                    item.source_product_code,
                    item.material,
                    item.usable_size,
                    item.location,
                    r.customer_name,
                    r.operator_name,
                    r.remark,
                )
            ).lower()
            if keyword not in searchable:
                continue
            continue
        before_quantity = "-" if r.transaction_type == "confirm" else r.before_quantity
        after_quantity = "-" if r.transaction_type == "confirm" else r.after_quantity
        quantity_label = r.after_quantity if r.transaction_type == "confirm" and r.quantity == 0 else r.quantity
        reverse_action = "-" if r.transaction_type not in ("in", "out") or r.reversed_transaction_id else f"<form method='post' action='/admin/scraps/transactions/{r.id}/reverse' class='actions' style='margin:0;justify-content:flex-start' onsubmit=\"return confirm('确定撤销这条余料流水吗？系统会生成一条反向流水，不会删除原记录。')\"><input name='operator_name' placeholder='操作人' style='width:80px'><input name='remark' placeholder='撤销原因' required style='width:120px'><button class='btn secondary' type='submit'>撤销</button></form>"
        rows += f"<tr><td>{item.material}</td><td>{item.thickness}</td><td>{item.usable_size or '-'}</td><td>{scrap_location_label(item)}</td><td>{transaction_label(r.transaction_type)}</td><td>{quantity_label}</td><td>{before_quantity}</td><td>{after_quantity}</td><td>{r.customer_name or '-'}</td><td>{r.operator_name or '-'}</td><td>{r.remark or '-'}</td><td>{r.created_at}</td><td>{reverse_action}</td></tr>"
    material_options = datalist_options(inventory_distinct_options(db, "scrap", "material"))
    type_options = "".join(
        f"<option value='{value}' {'selected' if transaction_type == value else ''}>{label}</option>"
        for value, label in (("", "全部类型"), ("in", "入库"), ("out", "出库"), ("confirm", "确认"))
    )
    body = f"""
    <div class="top"><div><h1>余料流水</h1><p class="muted">查看余料确认入库、出库等流转记录。</p></div><div class="actions"><a class="btn secondary" href="/admin/scraps">返回余料记录</a><a class="btn secondary" href="/admin/inventory/transactions">成品流水</a><a class="btn secondary" href="/admin/exports/scrap_transactions">导出Excel</a></div></div>
    <section class="card">
      <form method="get" action="/admin/scraps/transactions" class="actions" style="justify-content:flex-start">
        <input name="q" value="{safe_value(q.strip())}" placeholder="输入来源/材质/尺寸/库位" style="width:220px">
        <input name="material" value="{safe_value(material.strip())}" list="scrap-transaction-material-options" placeholder="材质" style="width:150px"><datalist id="scrap-transaction-material-options">{material_options}</datalist>
        <select name="transaction_type" style="width:130px">{type_options}</select>
        <button class="btn" type="submit">查询流水</button>
        <a class="btn secondary" href="/admin/scraps/transactions">清空</a>
      </form>
    </section>
    <section class="card"><table><thead><tr><th>材质</th><th>厚度</th><th>可用尺寸</th><th>库位</th><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>客户/去向</th><th>操作人</th><th>备注</th><th>时间</th><th>操作</th></tr></thead><tbody>{rows or "<tr><td colspan='13'>暂无余料流水。</td></tr>"}</tbody></table></section>
    """
    return page("余料流水", body)


@router.post("/admin/inventory/transactions/{transaction_id}/reverse")
def reverse_inventory_transaction_from_page(
    transaction_id: int,
    operator_name: str = Form(""),
    remark: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    reversal = reverse_inventory_transaction(transaction_id, operator_name or None, remark or None, db)
    db.flush()
    record_operation_log(
        db,
        "transaction_reverse",
        "inventory_transaction",
        transaction_id,
        operator_name or None,
        remark or "撤销库存流水",
        after_data={"reversal_transaction_id": reversal.id},
    )
    db.commit()
    return RedirectResponse("/admin/inventory/transactions", status_code=303)


@router.post("/admin/scraps/transactions/{transaction_id}/reverse")
def reverse_scrap_transaction_from_page(
    transaction_id: int,
    operator_name: str = Form(""),
    remark: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    reversal = reverse_inventory_transaction(transaction_id, operator_name or None, remark or None, db)
    db.flush()
    record_operation_log(
        db,
        "transaction_reverse",
        "inventory_transaction",
        transaction_id,
        operator_name or None,
        remark or "撤销余料流水",
        after_data={"reversal_transaction_id": reversal.id},
    )
    db.commit()
    return RedirectResponse("/admin/scraps/transactions", status_code=303)


@router.get("/admin/scraps/pending", response_class=HTMLResponse)
def pending_scraps_page(db: Session = Depends(get_db)) -> HTMLResponse:
    items = (
        db.query(MaterialInventory)
        .filter(MaterialInventory.inventory_type == "scrap", MaterialInventory.status == "pending")
        .order_by(MaterialInventory.created_at.desc())
        .all()
    )
    location_candidates = datalist_options(inventory_distinct_options(db, "scrap", "location"))
    rows = "".join(
        f"""
        <tr>
          <td>{item.source_product_code or '-'}</td><td>{drawing_version_label(db, item.source_drawing_id)}</td><td>{item.quantity}</td><td>{item.material}</td><td>{item.thickness}</td><td>{item.diameter or '-'}</td><td>{item.usable_size or '-'}</td><td>{item.location or '-'}</td>
          <td>
            <form method='post' action='/admin/scraps/{item.id}/confirm' data-confirm-flow='true' data-confirm-title='确认余料入库' data-confirm-note='系统会把该余料从待确认转为可用库存，并写入确认入库流水。' style='display:flex;gap:6px;align-items:center;margin:0'>
              <input name='actual_quantity' aria-label='实际数量' type='number' min='0' value='{item.quantity}' style='width:75px'>
              <input name='actual_diameter' aria-label='实际直径' type='number' step='0.01' value='{item.diameter or ''}' style='width:90px'>
              <input name='location' aria-label='库位' value='{'' if item.location in ('待入库', '未入库') else item.location or ''}' list='pending-scrap-location-options' placeholder='库位' style='width:100px' required>
              <input name='operator_name' aria-label='确认人' placeholder='确认人' style='width:90px'>
              <button class='btn secondary' type='submit' style='min-width:96px;white-space:nowrap'>确认入库</button>
            </form>
          </td>
        </tr>
        """
        for item in items
    )
    body = f"""
    <div class="top"><div><h1>待入库余料</h1><p class="muted">成品入库后自动生成的中心余料先进入待确认，测量实际尺寸和库位后再变为可用。</p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">成品管理</a><a class="btn secondary" href="/admin/scraps">余料记录</a></div></div>
    <section class="card"><table><thead><tr><th>来源产品</th><th>来源图纸</th><th>数量</th><th>材质</th><th>厚度</th><th>理论直径</th><th>可用尺寸</th><th>当前库位</th><th>确认入库</th></tr></thead><tbody>{rows or "<tr><td colspan='9'>暂无待入库余料。</td></tr>"}</tbody></table></section>
    <datalist id="pending-scrap-location-options">{location_candidates}</datalist>
    """
    return page("待入库余料", body)


@router.post("/admin/scraps/{inventory_id}/confirm")
def confirm_pending_scrap_from_page(
    inventory_id: int,
    actual_quantity: int = Form(...),
    actual_diameter: str = Form(""),
    location: str = Form(""),
    operator_name: str = Form(""),
    _lock=Depends(locked_inventory_write),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    item = db.get(MaterialInventory, inventory_id)
    if not item:
        raise HTTPException(status_code=404, detail="余料不存在")
    if item.inventory_type != "scrap":
        raise HTTPException(status_code=400, detail="该库存不是余料")
    if item.status != "pending":
        raise HTTPException(status_code=400, detail="该余料不是待入库状态，不能重复确认")
    if actual_quantity < 0:
        raise HTTPException(status_code=400, detail="实际数量不能小于0")
    if not location.strip():
        raise HTTPException(status_code=400, detail="确认入库时必须填写库位")
    before_data = inventory_snapshot(item)
    before_quantity = item.quantity
    item.quantity = actual_quantity
    item.diameter = optional_float(actual_diameter) or item.diameter
    item.usable_size = f"φ{item.diameter:g}" if item.diameter else item.usable_size
    item.location = location or item.location
    item.status = "available" if actual_quantity > 0 else "used"
    db.add(
        InventoryTransactionRecord(
            inventory_id=item.id,
            transaction_type="confirm",
            quantity=actual_quantity,
            before_quantity=before_quantity,
            after_quantity=actual_quantity,
            operator_name=operator_name or None,
            remark="余料确认入库",
        )
    )
    record_operation_log(
        db,
        "scrap_confirm",
        "inventory",
        item.id,
        operator_name or None,
        f"余料确认入库：数量 {actual_quantity}，库位 {location.strip()}",
        before_data=before_data,
        after_data=inventory_snapshot(item),
    )
    db.commit()
    return RedirectResponse("/admin/scraps/pending", status_code=303)


@router.get("/admin/scraps/outbound", response_class=HTMLResponse)
def scrap_outbound_page(
    drawing_id: str = "",
    material: str = "",
    thickness: str = "",
    required_diameter: str = "",
    location: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    client_request_id = uuid4().hex
    has_filter = any([drawing_id.strip(), material.strip(), thickness.strip(), required_diameter.strip(), location.strip()])
    scraps = []
    selected_drawing = db.get(ProductDrawing, int(drawing_id)) if drawing_id.isdigit() else None
    required_drawing_diameter = None
    drawing_required_thickness = None
    required_scrap_diameter = None
    if selected_drawing:
        required_drawing_diameter = drawing_required_diameter(selected_drawing)
        drawing_required_thickness = effective_drawing_thickness(selected_drawing)
        required_scrap_diameter = scrap_required_diameter(selected_drawing)
    if has_filter:
        query = (
            db.query(MaterialInventory)
            .filter(
                MaterialInventory.inventory_type == "scrap",
                MaterialInventory.status == "available",
                MaterialInventory.quantity > 0,
            )
        )
        if material.strip():
            query = query.filter(MaterialInventory.material.ilike(f"%{material.strip()}%"))
        thickness_value = optional_float(thickness)
        if thickness_value is not None:
            query = query.filter(MaterialInventory.thickness == thickness_value)
        required_diameter_value = optional_float(required_diameter)
        if required_diameter_value is not None:
            query = query.filter(MaterialInventory.diameter >= required_diameter_value)
        if location.strip():
            query = query.filter(MaterialInventory.location.ilike(f"%{location.strip()}%"))
        scraps = query.order_by(MaterialInventory.diameter.asc(), MaterialInventory.created_at.asc()).all()
        if selected_drawing:
            scraps = [
                item for item in scraps
                if scrap_matches_drawing(item, selected_drawing)
            ]
    grouped = {}
    for item in scraps:
        location_label = scrap_location_label(item)
        key = f"{item.material}||{item.thickness}||{item.usable_size or '-'}||{location_label}"
        if key not in grouped:
            grouped[key] = {
                "key": key,
                "material": item.material,
                "thickness": item.thickness,
                "usable_size": item.usable_size or "-",
                "location": location_label,
                "quantity": 0,
            }
        grouped[key]["quantity"] += item.quantity
    options = "".join(
        f"<option value='{html.escape(group['key'])}'>{group['material']}｜厚度 {group['thickness']}｜{group['usable_size']}｜库位 {group['location']}｜总数量 {group['quantity']}</option>"
        for group in grouped.values()
    ) or "<option value='' disabled selected>请先查询匹配余料</option>"
    material_options = datalist_options(inventory_distinct_options(db, "scrap", "material", quantity_positive=True, status="available"))
    thickness_options = datalist_options(inventory_distinct_options(db, "scrap", "thickness", quantity_positive=True, status="available"))
    diameter_options = datalist_options(inventory_distinct_options(db, "scrap", "diameter", quantity_positive=True, status="available"))
    location_options = datalist_options(inventory_distinct_options(db, "scrap", "location", quantity_positive=True, status="available"))
    customer_candidates = datalist_options(transaction_customer_options(db))
    body = f"""
    <div class="top"><div><h1>余料出库</h1><p class="muted">先查询可用余料，再按规格和库位汇总出库。</p></div><div class="actions"><a class="btn secondary" href="/admin/scraps">返回余料查询</a></div></div>
    <section class="card">
      <form method="get" action="/admin/scraps/outbound" class="actions" style="justify-content:flex-start">
        <input type="search" data-select-filter="scrap-outbound-drawing-select" placeholder="筛选匹配图纸" style="width:180px">
        <select id="scrap-outbound-drawing-select" name="drawing_id">{confirmed_drawing_options(db, selected_id=int(drawing_id) if drawing_id.isdigit() else None, include_blank=True)}</select>
        <input name="material" value="{safe_value(material.strip())}" list="scrap-out-material-options" placeholder="材质" style="width:140px"><datalist id="scrap-out-material-options">{material_options}</datalist>
        <input name="thickness" value="{safe_value(thickness.strip())}" list="scrap-out-thickness-options" placeholder="厚度" style="width:120px"><datalist id="scrap-out-thickness-options">{thickness_options}</datalist>
        <input name="required_diameter" value="{safe_value(required_diameter.strip())}" list="scrap-out-diameter-options" placeholder="直径≥" style="width:120px"><datalist id="scrap-out-diameter-options">{diameter_options}</datalist>
        <input name="location" value="{safe_value(location.strip())}" list="scrap-out-location-options" placeholder="库位" style="width:140px"><datalist id="scrap-out-location-options">{location_options}</datalist>
        <button class="btn" type="submit">查询可出库余料</button>
        <a class="btn secondary" href="/admin/scraps/outbound">清空</a>
      </form>
    </section>
    {f'<section class="card"><strong>当前按图纸匹配：</strong>{selected_drawing.product_code or "-"}，图纸外径/外框 {required_drawing_diameter or "-"}，需要余料直径 ≥ {required_scrap_diameter:g}，厚度 {drawing_required_thickness or "-"}，材质 {selected_drawing.material or "-"}</section>' if selected_drawing and required_scrap_diameter is not None else ''}
    <section class="card">
      <form method="post" action="/admin/scraps/outbound" class="form-grid" data-confirm-flow="true" data-confirm-title="确认余料出库" data-confirm-note="系统会按所选余料规格扣减可用余料，并生成余料出库流水。">
        <input type="hidden" name="client_request_id" value="{client_request_id}">
        <input type="hidden" name="drawing_id" value="{html.escape(drawing_id.strip())}">
        <div><label>筛选余料规格</label><input type="search" data-select-filter="scrap-outbound-group-select" placeholder="输入材质、厚度、尺寸或库位"></div>
        <div><label>选择余料规格</label><select id="scrap-outbound-group-select" name="scrap_group_key" required>{options}</select></div>
        <div><label>出库数量</label><input name="quantity" type="number" value="1" min="1" required></div>
        <div><label>客户/去向</label><input name="customer_name" list="scrap-out-customer-options" placeholder="例如 XX客户 / 车间领用"><datalist id="scrap-out-customer-options">{customer_candidates}</datalist></div>
        <div><label>操作人</label><input name="operator_name" placeholder="例如 张三"></div>
        <div><label>备注</label><input name="remark" placeholder="例如 生产领用/报废"></div>
        <p class="confirm-hint">提交前会先核对余料规格、出库数量、客户/去向、操作人和备注，确认后才扣减库存。</p>
        <div style="align-self:end"><button class="btn" type="submit">确认出库</button></div>
      </form>
    </section>
    """
    return page("余料出库", body)


@router.post("/admin/scraps/outbound")
def outbound_scrap_from_page(
    scrap_group_key: str = Form(...),
    quantity: int = Form(...),
    customer_name: str = Form(""),
    operator_name: str = Form(""),
    remark: str = Form(""),
    client_request_id: str = Form(""),
    drawing_id: str = Form(""),
    _lock=Depends(locked_inventory_write),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    idempotency_key = f"admin_scrap_outbound:{client_request_id.strip()}" if client_request_id.strip() else None
    if idempotency_key:
        existing_record = db.query(InventoryTransactionRecord).filter(InventoryTransactionRecord.idempotency_key == idempotency_key).first()
        if existing_record:
            return RedirectResponse("/admin/scraps", status_code=303)
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="出库数量必须大于0")
    parts = scrap_group_key.split("||")
    if len(parts) != 4:
        raise HTTPException(status_code=400, detail="余料规格参数错误")
    material_value = parts[0]
    selected_drawing = db.get(ProductDrawing, int(drawing_id)) if drawing_id.isdigit() else None
    batches = find_scrap_batches_for_outbound(scrap_group_key, db, drawing=selected_drawing)
    before_quantity = sum(item.quantity for item in batches)
    if before_quantity < quantity:
        raise HTTPException(status_code=400, detail=f"余料数量不足，当前数量 {before_quantity}")
    remaining = quantity
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
    after_quantity = before_quantity - quantity
    customer_value = customer_name.strip()
    for index, (item, deduction, item_before_quantity, item_after_quantity) in enumerate(affected_items):
        db.add(
            InventoryTransactionRecord(
                inventory_id=item.id,
                transaction_type="out",
                quantity=deduction,
                before_quantity=item_before_quantity,
                after_quantity=item_after_quantity,
                idempotency_key=idempotency_key if index == 0 else None,
                operator_name=operator_name or None,
                customer_name=customer_value or None,
                remark=remark or "余料出库",
            )
        )
    record_operation_log(
        db,
        "scrap_outbound",
        "inventory",
        affected_items[0][0].id if affected_items else None,
        operator_name or None,
        remark or f"余料出库：{material_value}，数量 {quantity}{f'，客户/去向 {customer_value}' if customer_value else ''}",
        before_data={"quantity": before_quantity, "scrap_group_key": scrap_group_key},
        after_data={"quantity": after_quantity, "customer_name": customer_value or None},
    )
    db.commit()
    return RedirectResponse("/admin/scraps", status_code=303)


@router.get("/admin/drawings", response_class=HTMLResponse)
def drawings_page(
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
    confirmed: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    query = db.query(ProductDrawing)
    keyword = q.strip()
    query = apply_drawing_filters(
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
    if confirmed in ("0", "1"):
        query = query.filter(ProductDrawing.confirmed == int(confirmed))
    drawings = query.order_by(ProductDrawing.created_at.desc()).all()
    rows = drawing_rows(drawings)
    product_code_options = datalist_options(drawing_distinct_options(db, "product_code", confirmed_only=False))
    product_category_options = datalist_options(drawing_distinct_options(db, "product_category", confirmed_only=False) + ["汽车", "摩托车"])
    material_options = datalist_options(drawing_distinct_options(db, "material", confirmed_only=False))
    product_thickness_options = datalist_options(drawing_distinct_options(db, "product_thickness", confirmed_only=False))
    plate_thickness_options = datalist_options(drawing_distinct_options(db, "plate_thickness", confirmed_only=False))
    outer_options = datalist_options(drawing_distinct_options(db, "max_outer_diameter", confirmed_only=False))
    inner_options = datalist_options(drawing_distinct_options(db, "min_inner_diameter", confirmed_only=False))
    teeth_options = datalist_options(drawing_distinct_options(db, "teeth_count", confirmed_only=False))
    confirmed_options = "".join(
        f"<option value='{value}' {'selected' if confirmed == value else ''}>{label}</option>"
        for value, label in (("", "全部状态"), ("1", "已确认"), ("0", "待确认"))
    )
    body = f"""
    <div class="top"><div><h1>图纸识别</h1><p class="muted">上传DXF文件，自动识别产品用料信息。</p></div><div class="actions"><a class="btn secondary" href="/admin/drawings/pending">待确认图纸</a><a class="btn secondary" href="/admin/drawings/confirmed">已确认图纸</a><form method="post" action="/admin/drawings/previews/regenerate-missing" style="margin:0" onsubmit="return confirm('将为所有缺少高清PDF预览的图纸生成预览，可能需要等待一段时间，继续吗？')"><button class="btn secondary" type="submit">批量生成高清预览</button></form></div></div>
    <section class="card">
      <h2>上传DXF</h2>
      <form id="uploadForm" method="post" action="/admin/drawings/upload" enctype="multipart/form-data">
        <label id="dropzone" class="dropzone" for="dxfFile">
          <strong>拖拽 DXF 文件到这里</strong>
          <span>或点击选择文件，仅支持 .dxf</span>
          <div id="fileName" class="file-name"></div>
        </label>
        <input id="dxfFile" class="hidden-file" type="file" name="file" accept=".dxf" required>
        <br>
        <button class="btn" type="submit">上传并识别</button>
      </form>
      <script>
        const dropzone = document.getElementById('dropzone');
        const fileInput = document.getElementById('dxfFile');
        const fileName = document.getElementById('fileName');
        const showFile = () => {{
          const file = fileInput.files && fileInput.files[0];
          fileName.textContent = file ? `已选择：${{file.name}}` : '';
        }};
        ['dragenter', 'dragover'].forEach(eventName => {{
          dropzone.addEventListener(eventName, event => {{
            event.preventDefault();
            dropzone.classList.add('dragover');
          }});
        }});
        ['dragleave', 'drop'].forEach(eventName => {{
          dropzone.addEventListener(eventName, event => {{
            event.preventDefault();
            dropzone.classList.remove('dragover');
          }});
        }});
        dropzone.addEventListener('drop', event => {{
          const file = event.dataTransfer.files && event.dataTransfer.files[0];
          if (!file) return;
          if (!file.name.toLowerCase().endsWith('.dxf')) {{
            alert('请上传 .dxf 文件');
            return;
          }}
          const dataTransfer = new DataTransfer();
          dataTransfer.items.add(file);
          fileInput.files = dataTransfer.files;
          showFile();
        }});
        fileInput.addEventListener('change', showFile);
      </script>
    </section>
    <section class="card">
      <h2>批量导入DXF</h2>
      <p class="muted">一次最多选择50张DXF。系统会逐张识别，成功或重复的图纸进入图纸记录，失败的文件会单独显示原因。</p>
      <form method="post" action="/admin/drawings/upload-batch" enctype="multipart/form-data">
        <input type="file" name="files" accept=".dxf" multiple required>
        <br><br>
        <button class="btn" type="submit">批量上传并识别</button>
      </form>
    </section>
    <section class="card">
      <form method="get" action="/admin/drawings" class="actions" style="justify-content:flex-start">
        <input name="q" value="{safe_value(keyword)}" list="drawing-code-options" placeholder="型号/名称/材质筛选" style="width:220px"><datalist id="drawing-code-options">{product_code_options}</datalist>
        <input name="product_category" value="{safe_value(product_category.strip())}" list="drawing-category-options" placeholder="产品分类" style="width:150px"><datalist id="drawing-category-options">{product_category_options}</datalist>
        <input name="material" value="{safe_value(material.strip())}" list="drawing-material-options" placeholder="材质" style="width:130px"><datalist id="drawing-material-options">{material_options}</datalist>
        <input name="product_thickness" value="{safe_value(product_thickness.strip())}" list="drawing-product-thickness-options" placeholder="总成品厚度" style="width:130px"><datalist id="drawing-product-thickness-options">{product_thickness_options}</datalist>
        <input name="plate_thickness" value="{safe_value(plate_thickness.strip())}" list="drawing-plate-thickness-options" placeholder="钢板厚度" style="width:120px"><datalist id="drawing-plate-thickness-options">{plate_thickness_options}</datalist>
        <input name="outer_diameter" value="{safe_value(outer_diameter.strip())}" list="drawing-outer-options" placeholder="外径" style="width:110px"><datalist id="drawing-outer-options">{outer_options}</datalist>
        <input name="inner_diameter" value="{safe_value(inner_diameter.strip())}" list="drawing-inner-options" placeholder="内径" style="width:110px"><datalist id="drawing-inner-options">{inner_options}</datalist>
        <input name="teeth_count" value="{safe_value(teeth_count.strip())}" list="drawing-teeth-options" placeholder="齿数" style="width:110px"><datalist id="drawing-teeth-options">{teeth_options}</datalist>
        <input name="module" value="{safe_value(module.strip())}" placeholder="模数" style="width:100px">
        <input name="pressure_angle" value="{safe_value(pressure_angle.strip())}" placeholder="压力角" style="width:110px">
        <input name="common_normal_length" value="{safe_value(common_normal_length.strip())}" placeholder="公法线" style="width:120px">
        <select name="confirmed" style="width:140px">{confirmed_options}</select>
        <button class="btn" type="submit">搜索图纸</button>
        <a class="btn secondary" href="/admin/drawings">清空</a>
      </form>
    </section>
    <section class="card"><h2>图纸记录</h2><table><thead><tr><th>产品分类</th><th>产品编号</th><th>版本</th><th>产品名称</th><th>材质</th><th>厚度</th><th>尺寸</th><th>齿轮参数</th><th>确认状态</th><th>操作</th></tr></thead><tbody>{rows}</tbody></table></section>
    """
    return page("图纸识别", body)


def drawing_rows(drawings: list[ProductDrawing], show_id: bool = True) -> str:
    rows = "".join(
        f"<tr><td>{html.escape(d.product_category or '-')}</td><td>{html.escape(d.product_code or '-')}</td><td>{drawing_version_code(d)}<br><span class='muted'>{'当前' if d.is_active else '历史'}</span></td><td>{html.escape(d.product_name or '-')}<br><span class='muted'>{html.escape(d.remark or '')}</span></td><td>{html.escape(d.material or '-')}</td><td>总 {fmt_option(d.product_thickness) or '-'}<br>钢 {fmt_option(d.plate_thickness) or '-'}</td><td>外 {fmt_option(d.max_outer_diameter) or '-'}<br>内 {fmt_option(d.min_inner_diameter) or '-'}</td><td>{html.escape(display_teeth_count(d))}<br>模数 {html.escape(display_module(d))}<br>公法线 {html.escape(display_common_normal_length(d))}</td><td>{'已确认' if d.confirmed else '待确认'}</td><td><a class='btn secondary' href='/admin/drawings/{d.id}'>查看</a></td></tr>"
        for d in drawings
    )
    return rows or "<tr><td colspan='10'>暂无图纸记录。</td></tr>"


@router.get("/admin/drawings/confirmed", response_class=HTMLResponse)
def confirmed_drawings_page(
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
) -> HTMLResponse:
    query = db.query(ProductDrawing).filter(ProductDrawing.confirmed == 1, ProductDrawing.is_active == 1)
    keyword = q.strip()
    query = apply_drawing_filters(
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
    drawings = query.order_by(ProductDrawing.updated_at.desc()).all()
    product_code_options = datalist_options(drawing_distinct_options(db, "product_code"))
    product_category_options = datalist_options(drawing_distinct_options(db, "product_category") + ["汽车", "摩托车"])
    material_options = datalist_options(drawing_distinct_options(db, "material"))
    product_thickness_options = datalist_options(drawing_distinct_options(db, "product_thickness"))
    plate_thickness_options = datalist_options(drawing_distinct_options(db, "plate_thickness"))
    outer_options = datalist_options(drawing_distinct_options(db, "max_outer_diameter"))
    inner_options = datalist_options(drawing_distinct_options(db, "min_inner_diameter"))
    teeth_options = datalist_options(drawing_distinct_options(db, "teeth_count"))
    export_params = {
        "q": keyword,
        "product_category": product_category.strip(),
        "material": material.strip(),
        "thickness": thickness.strip(),
        "product_thickness": product_thickness.strip(),
        "plate_thickness": plate_thickness.strip(),
        "outer_diameter": outer_diameter.strip(),
        "inner_diameter": inner_diameter.strip(),
        "teeth_count": teeth_count.strip(),
        "module": module.strip(),
        "pressure_angle": pressure_angle.strip(),
        "common_normal_length": common_normal_length.strip(),
        "pin_diameter": pin_diameter.strip(),
        "pin_span": pin_span.strip(),
    }
    body = f"""
    <div class="top"><div><h1>已确认图纸</h1><p class="muted">这些图纸已经人工确认，可直接用于成品入库，也可按分类和参数导出给客户确认。</p></div><div class="actions"><a class="btn secondary" href="/admin/drawings">全部图纸</a><a class="btn secondary" href="/admin/drawings/pending">待确认图纸</a><a class="btn secondary" href="{export_link('product_catalog', export_params)}">导出Excel</a></div></div>
    <section class="card">
      <form method="get" action="/admin/drawings/confirmed" class="actions" style="justify-content:flex-start;margin-bottom:14px">
        <input name="q" value="{safe_value(keyword)}" list="confirmed-drawing-code-options" placeholder="型号/名称/材质筛选" style="width:220px"><datalist id="confirmed-drawing-code-options">{product_code_options}</datalist>
        <input name="product_category" value="{safe_value(product_category.strip())}" list="confirmed-drawing-category-options" placeholder="产品分类" style="width:150px"><datalist id="confirmed-drawing-category-options">{product_category_options}</datalist>
        <input name="material" value="{safe_value(material.strip())}" list="confirmed-drawing-material-options" placeholder="材质" style="width:130px"><datalist id="confirmed-drawing-material-options">{material_options}</datalist>
        <input name="product_thickness" value="{safe_value(product_thickness.strip())}" list="confirmed-drawing-product-thickness-options" placeholder="总成品厚度" style="width:130px"><datalist id="confirmed-drawing-product-thickness-options">{product_thickness_options}</datalist>
        <input name="plate_thickness" value="{safe_value(plate_thickness.strip())}" list="confirmed-drawing-plate-thickness-options" placeholder="钢板厚度" style="width:120px"><datalist id="confirmed-drawing-plate-thickness-options">{plate_thickness_options}</datalist>
        <input name="outer_diameter" value="{safe_value(outer_diameter.strip())}" list="confirmed-drawing-outer-options" placeholder="外径" style="width:110px"><datalist id="confirmed-drawing-outer-options">{outer_options}</datalist>
        <input name="inner_diameter" value="{safe_value(inner_diameter.strip())}" list="confirmed-drawing-inner-options" placeholder="内径" style="width:110px"><datalist id="confirmed-drawing-inner-options">{inner_options}</datalist>
        <input name="teeth_count" value="{safe_value(teeth_count.strip())}" list="confirmed-drawing-teeth-options" placeholder="齿数" style="width:110px"><datalist id="confirmed-drawing-teeth-options">{teeth_options}</datalist>
        <input name="module" value="{safe_value(module.strip())}" placeholder="模数" style="width:100px">
        <input name="pressure_angle" value="{safe_value(pressure_angle.strip())}" placeholder="压力角" style="width:110px">
        <input name="common_normal_length" value="{safe_value(common_normal_length.strip())}" placeholder="公法线" style="width:120px">
        <button class="btn" type="submit">搜索</button>
        <a class="btn secondary" href="/admin/drawings/confirmed">清空</a>
      </form>
      <table><thead><tr><th>产品分类</th><th>产品编号</th><th>版本</th><th>产品名称</th><th>材质</th><th>厚度</th><th>尺寸</th><th>齿轮参数</th><th>确认状态</th><th>操作</th></tr></thead><tbody>{drawing_rows(drawings, show_id=False)}</tbody></table>
    </section>
    """
    return page("已确认图纸", body)


@router.get("/admin/drawings/pending", response_class=HTMLResponse)
def pending_drawings_page(db: Session = Depends(get_db)) -> HTMLResponse:
    drawings = db.query(ProductDrawing).filter(ProductDrawing.confirmed == 0).order_by(ProductDrawing.created_at.desc()).all()
    body = f"""
    <div class="top"><div><h1>待确认图纸</h1><p class="muted">这些图纸需要人工检查并保存确认结果。</p></div><div class="actions"><a class="btn secondary" href="/admin/drawings">全部图纸</a><a class="btn secondary" href="/admin/drawings/confirmed">已确认图纸</a></div></div>
    <section class="card"><table><thead><tr><th>产品分类</th><th>产品编号</th><th>版本</th><th>产品名称</th><th>材质</th><th>厚度</th><th>尺寸</th><th>齿轮参数</th><th>确认状态</th><th>操作</th></tr></thead><tbody>{drawing_rows(drawings)}</tbody></table></section>
    """
    return page("待确认图纸", body)


@router.post("/admin/drawings/previews/regenerate-missing", response_class=HTMLResponse)
def regenerate_missing_drawing_previews(db: Session = Depends(get_db)) -> HTMLResponse:
    drawings = db.query(ProductDrawing).order_by(ProductDrawing.created_at.desc()).all()
    results = []
    for drawing in drawings:
        preview_path = Path(drawing.preview_file_url) if drawing.preview_file_url else None
        if preview_path and preview_path.exists() and preview_path.is_file():
            continue
        before_data = drawing_snapshot(drawing)
        result = generate_drawing_preview(drawing)
        results.append((drawing, result))
        record_operation_log(
            db,
            "drawing_preview_generate",
            "drawing",
            drawing.id,
            None,
            "批量生成图纸高清预览",
            before_data=before_data,
            after_data=drawing_snapshot(drawing),
        )
    db.commit()
    success_count = sum(1 for _, result in results if result.status == "generated")
    failed_count = sum(1 for _, result in results if result.status == "failed")
    unconfigured_count = sum(1 for _, result in results if result.status == "unconfigured")
    rows = "".join(
        f"<tr><td>{safe_value(drawing.product_code or '-')}</td><td>{drawing.id}</td><td>{safe_value(result.status)}</td><td>{safe_value(result.error or result.file_path or '-')}</td></tr>"
        for drawing, result in results
    )
    if not rows:
        rows = "<tr><td colspan='4'>所有图纸都已经有高清预览。</td></tr>"
    body = f"""
    <div class="top"><div><h1>高清预览生成结果</h1><p class="muted">成功 {success_count} 张，失败 {failed_count} 张，未配置 {unconfigured_count} 张。</p></div><div class="actions"><a class="btn secondary" href="/admin/drawings">返回图纸列表</a></div></div>
    <section class="card"><table><thead><tr><th>产品型号</th><th>图纸ID</th><th>状态</th><th>说明</th></tr></thead><tbody>{rows}</tbody></table></section>
    """
    return page("高清预览生成结果", body)


@router.post("/admin/drawings/upload")
def upload_drawing_from_page(file: UploadFile = File(...), db: Session = Depends(get_db)) -> RedirectResponse:
    drawing, duplicated = save_uploaded_drawing(file=file, db=db)
    record_operation_log(
        db,
        "drawing_upload",
        "drawing",
        drawing.id,
        None,
        "重复图纸上传" if duplicated else "上传图纸",
        after_data=drawing_snapshot(drawing),
    )
    db.commit()
    notice = "duplicated" if duplicated else "uploaded"
    return RedirectResponse(f"/admin/drawings/{drawing.id}?notice={notice}", status_code=303)


@router.post("/admin/drawings/upload-batch", response_class=HTMLResponse)
def upload_drawings_batch_from_page(files: list[UploadFile] = File(...), db: Session = Depends(get_db)) -> HTMLResponse:
    if not files:
        raise HTTPException(status_code=400, detail="请选择DXF文件")
    if len(files) > 50:
        raise HTTPException(status_code=400, detail="一次最多批量上传50张DXF")
    results = []
    for file in files:
        filename = html.escape(file.filename or "-")
        try:
            drawing, duplicated = save_uploaded_drawing(file=file, db=db)
            record_operation_log(
                db,
                "drawing_upload",
                "drawing",
                drawing.id,
                None,
                "批量导入重复图纸" if duplicated else "批量导入图纸",
                after_data=drawing_snapshot(drawing),
            )
            db.commit()
            results.append({
                "filename": filename,
                "status": "重复" if duplicated else "成功",
                "message": f"<a class='btn secondary' href='/admin/drawings/{drawing.id}'>查看</a>",
            })
        except HTTPException as exc:
            db.rollback()
            results.append({"filename": filename, "status": "失败", "message": html.escape(str(exc.detail))})
        except Exception as exc:
            db.rollback()
            results.append({"filename": filename, "status": "失败", "message": html.escape(str(exc))})
    success_count = sum(1 for item in results if item["status"] == "成功")
    duplicate_count = sum(1 for item in results if item["status"] == "重复")
    failed_count = sum(1 for item in results if item["status"] == "失败")
    rows = "".join(
        f"<tr><td>{item['filename']}</td><td>{item['status']}</td><td>{item['message']}</td></tr>"
        for item in results
    )
    body = f"""
    <div class="top"><div><h1>批量导入结果</h1><p class="muted">共 {len(results)} 张，成功 {success_count} 张，重复 {duplicate_count} 张，失败 {failed_count} 张。</p></div><div class="actions"><a class="btn secondary" href="/admin/drawings">返回图纸识别</a><a class="btn secondary" href="/admin/drawings/pending">查看待确认图纸</a></div></div>
    <section class="card"><table><thead><tr><th>文件名</th><th>结果</th><th>说明</th></tr></thead><tbody>{rows}</tbody></table></section>
    """
    return page("批量导入结果", body)


@router.get("/admin/drawings/{drawing_id}", response_class=HTMLResponse)
def drawing_detail_page(drawing_id: int, notice: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    notice_html = ""
    if notice == "duplicated":
        notice_html = "<section class='card'><strong>这张图纸已经上传过。</strong><p class='muted'>已为你打开已有图纸记录，可直接查看或重新识别。</p></section>"
    elif notice == "uploaded":
        notice_html = "<section class='card'><strong>图纸上传成功，已完成自动识别。</strong><p class='muted'>请核对下面的识别结果，确认无误后保存。</p></section>"
    elif notice == "opened":
        notice_html = "<section class='card'><strong>已调用本机软件打开图纸。</strong><p class='muted'>如果没有弹出软件，请检查这台电脑是否已安装并关联DXF查看软件。</p></section>"
    parse_json_text = html.escape(json.dumps(drawing.parse_result_json or {}, ensure_ascii=True, default=str, indent=2), quote=False)
    body = f"""
    <div class="top">
      <div><h1>图纸详情</h1><p class="muted">请人工确认识别结果，确认后可进行成品入库。</p></div>
      <div class="actions">
        <a class="btn secondary" href="/admin/drawings">返回列表</a>
        <form method="post" action="/admin/drawings/{drawing.id}/open-local" style="margin:0">
          <button class="btn" type="submit">用本机软件打开图纸</button>
        </form>
        <a class="btn secondary" href="/admin/drawings/{drawing.id}/download">下载DXF</a>
        <a class="btn secondary" href="/admin/drawings/{drawing.id}/preview" target="_blank">浏览器临时预览</a>
        <form method="post" action="/admin/drawings/{drawing.id}/rerun" style="margin:0">
          <button class="btn secondary" type="submit">重新识别当前图纸</button>
        </form>
        <form method="post" action="/admin/drawings/{drawing.id}/delete" style="margin:0" onsubmit="return confirm('确定删除这张图纸吗？删除后如需更新可以重新上传。')">
          <button class="btn secondary" type="submit">删除图纸</button>
        </form>
        <a class="btn secondary" href="/admin/inventory/inbound">成品入库</a>
      </div>
    </div>
    {notice_html}
    <section class="card">
      <h2>人工确认识别结果</h2>
      <form method="post" action="/admin/drawings/{drawing.id}/confirm" class="form-grid">
        <div><label>产品型号</label><input name="product_code" value="{safe_value(drawing.product_code)}"></div>
        <div><label>产品名称</label><input name="product_name" value="{safe_value(drawing.product_name)}"></div>
        <div><label>产品分类</label><input name="product_category" value="{safe_value(drawing.product_category)}" list="drawing-category-confirm-options" placeholder="例如 汽车 / 摩托车"><datalist id="drawing-category-confirm-options"><option value="汽车"></option><option value="摩托车"></option></datalist></div>
        <div style="grid-column:1/-1"><label>备注</label><textarea name="remark" placeholder="记录客户要求、加工说明或内部备注">{safe_value(drawing.remark)}</textarea></div>
        <div><label>材质</label><input name="material" value="{safe_value(drawing.material)}" placeholder="例如 50#"></div>
        <div><label>外径</label><input name="max_outer_diameter" type="number" step="0.01" value="{safe_value(drawing.max_outer_diameter)}" placeholder="mm"></div>
        <div><label>内径</label><input name="min_inner_diameter" type="number" step="0.01" value="{safe_value(drawing.min_inner_diameter)}" placeholder="mm"></div>
        <div><label>总成品厚度</label><input name="product_thickness" type="number" step="0.001" value="{safe_value(drawing.product_thickness)}" placeholder="含复合材料总厚"></div>
        <div><label>钢板厚度</label><input name="plate_thickness" type="number" step="0.001" value="{safe_value(drawing.plate_thickness)}" placeholder="基板厚度"></div>
        <div><label>齿数 z</label><div class="inline-input-group tooth-count-field"><select name="tooth_type">{tooth_type_options(drawing.tooth_type)}</select><input name="teeth_count" value="{safe_value(drawing.teeth_count_text or drawing.teeth_count)}" placeholder="例如 41 / 48(52)"></div></div>
        <div><label>模数 m</label><input name="module" value="{safe_value(drawing.module_text or drawing.module)}" placeholder="公制数字或英制字母"></div>
        <div><label>压力角 α</label><input name="pressure_angle" type="number" step="0.01" value="{safe_value(drawing.pressure_angle)}" placeholder="常见20°"></div>
        <div><label>变位系数 x</label><input name="profile_shift_coefficient" type="number" step="0.001" value="{safe_value(drawing.profile_shift_coefficient)}"></div>
        <div><label>公法线长度 L</label><input name="common_normal_length" value="{safe_value(drawing.common_normal_length_text or drawing.common_normal_length)}" placeholder="可填范围，例如 58.26-58.14"></div>
        <div><label>跨齿数 n</label><input name="span_teeth_count" type="number" value="{safe_value(drawing.span_teeth_count)}"></div>
        <div><label>量棒直径 dp</label><input name="pin_diameter" type="number" step="0.001" value="{safe_value(drawing.pin_diameter)}" placeholder="mm"></div>
        <div><label>棒间距 M</label><input name="pin_span" type="number" step="0.001" value="{safe_value(drawing.pin_span)}" placeholder="mm"></div>
        <div><label>中心余料尺寸</label><input name="expected_scrap_size" value="{safe_value(drawing.expected_scrap_size)}" placeholder="中间割下来的圆料，例如 φ77.5"></div>
        <div style="align-self:end"><button class="btn" type="submit">保存确认结果</button></div>
      </form>
    </section>
    <section class="card"><h2>原始解析JSON</h2><pre>{parse_json_text}</pre></section>
    """
    return page("图纸详情", body, notice=notice)


@router.get("/admin/drawings/{drawing_id}/preview", response_class=HTMLResponse)
def drawing_preview_page(drawing_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    preview_file_path = Path(drawing.preview_file_url) if drawing.preview_file_url else None
    has_pdf_preview = bool(preview_file_path and preview_file_path.exists() and preview_file_path.is_file())
    if has_pdf_preview:
        preview_content = f"""
      <p class="muted">这里保留浏览器预览作为快速参考；正式看尺寸和文字时请用本机CAD/看图软件打开原始DXF。</p>
      <iframe src="/admin/drawings/{drawing.id}/preview-file" title="高清PDF预览" style="width:100%;height:82vh;border:1px solid var(--line);border-radius:18px;background:#fff"></iframe>
        """
    else:
        try:
            svg = render_dxf_svg(drawing.dxf_file_url)
        except Exception as exc:
            svg = f"<p>图纸预览生成失败：{html.escape(str(exc))}</p>"
        status_hint = ""
        if drawing.preview_status == "unconfigured":
            status_hint = f"<p class='muted'>高清PDF预览未配置：{safe_value(drawing.preview_error)}。当前先显示临时SVG预览。</p>"
        elif drawing.preview_status == "failed":
            status_hint = f"<p class='muted'>高清PDF预览生成失败：{safe_value(drawing.preview_error)}。当前先显示临时SVG预览。</p>"
        preview_content = f"""
      {status_hint}
      <p class="muted">浏览器临时预览可能看不清文字或与专业CAD有差异；正式查看请下载原始DXF后用本机软件打开。</p>
      {svg}
        """
    body = f"""
    <div class="top">
      <div><h1>图纸预览</h1><p class="muted">产品型号：{drawing.product_code or '-'}　版本：{drawing_version_code(drawing)}</p></div>
      <div class="actions">
        <a class="btn secondary" href="/admin/drawings/{drawing.id}">返回详情</a>
        <form method="post" action="/admin/drawings/{drawing.id}/open-local" style="margin:0">
          <button class="btn" type="submit">用本机软件打开DXF</button>
        </form>
        <a class="btn secondary" href="/admin/drawings/{drawing.id}/download">下载原始DXF</a>
        <form method="post" action="/admin/drawings/{drawing.id}/preview/regenerate" style="margin:0">
          <button class="btn secondary" type="submit">重新生成高清预览</button>
        </form>
      </div>
    </div>
    <section class="card">
      {preview_content}
    </section>
    """
    return page("图纸预览", body)


@router.get("/admin/drawings/{drawing_id}/preview-file")
def drawing_preview_file(drawing_id: int, db: Session = Depends(get_db)) -> FileResponse:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    if not drawing.preview_file_url:
        raise HTTPException(status_code=404, detail="高清预览文件不存在")
    file_path = Path(drawing.preview_file_url)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="高清预览文件不存在")
    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=file_path.name,
    )


@router.post("/admin/drawings/{drawing_id}/preview/regenerate")
def regenerate_drawing_preview(drawing_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    generate_drawing_preview(drawing, force=True)
    record_operation_log(
        db,
        "drawing_preview_generate",
        "drawing",
        drawing.id,
        None,
        "重新生成图纸高清预览",
        after_data=drawing_snapshot(drawing),
    )
    db.commit()
    return RedirectResponse(f"/admin/drawings/{drawing.id}/preview", status_code=303)


@router.get("/admin/drawings/{drawing_id}/download")
def download_drawing_file(drawing_id: int, db: Session = Depends(get_db)) -> FileResponse:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    file_path = Path(drawing.dxf_file_url)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="图纸文件不存在")
    return FileResponse(
        path=file_path,
        media_type="application/dxf",
        filename=file_path.name,
    )


@router.post("/admin/drawings/{drawing_id}/open-local")
def open_local_drawing_file_from_page(drawing_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    file_path = Path(drawing.dxf_file_url)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="图纸文件不存在")
    try:
        open_local_file(file_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"调用本机软件失败：{exc}") from exc
    record_operation_log(
        db,
        "drawing_open_local",
        "drawing",
        drawing.id,
        None,
        "用本机软件打开DXF",
        after_data=drawing_snapshot(drawing),
    )
    db.commit()
    return RedirectResponse(f"/admin/drawings/{drawing.id}?notice=opened", status_code=303)


@router.post("/admin/drawings/{drawing_id}/rerun")
def rerun_drawing_recognition_from_page(drawing_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    ensure_drawing_can_be_changed(drawing, db)
    before_data = drawing_snapshot(drawing)
    apply_recognition_to_drawing(drawing)
    record_operation_log(
        db,
        "drawing_rerun",
        "drawing",
        drawing.id,
        None,
        "重新识别图纸",
        before_data=before_data,
        after_data=drawing_snapshot(drawing),
    )
    db.commit()
    return RedirectResponse(f"/admin/drawings/{drawing.id}", status_code=303)


@router.post("/admin/drawings/{drawing_id}/delete")
def delete_drawing_from_page(drawing_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    ensure_drawing_can_be_changed(drawing, db)
    before_data = drawing_snapshot(drawing)
    delete_uploaded_drawing(drawing_id, db)
    record_operation_log(
        db,
        "drawing_delete",
        "drawing",
        drawing_id,
        None,
        "删除图纸",
        before_data=before_data,
    )
    db.commit()
    return RedirectResponse("/admin/drawings", status_code=303)


@router.post("/admin/drawings/{drawing_id}/confirm")
def confirm_drawing_from_page(
    drawing_id: int,
    product_code: str = Form(""),
    product_name: str = Form(""),
    product_category: str = Form(""),
    remark: str = Form(""),
    material: str = Form(""),
    max_outer_diameter: str = Form(""),
    min_inner_diameter: str = Form(""),
    product_thickness: str = Form(""),
    plate_thickness: str = Form(""),
    tooth_type: str = Form(""),
    teeth_count: str = Form(""),
    module: str = Form(""),
    pressure_angle: str = Form(""),
    profile_shift_coefficient: str = Form(""),
    span_teeth_count: str = Form(""),
    common_normal_length: str = Form(""),
    pin_diameter: str = Form(""),
    pin_span: str = Form(""),
    expected_scrap_size: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    was_confirmed = drawing.confirmed == 1
    before_data = drawing_snapshot(drawing)
    max_outer_diameter_value = optional_float(max_outer_diameter)
    min_inner_diameter_value = optional_float(min_inner_diameter)
    product_thickness_value = optional_float(product_thickness)
    plate_thickness_value = optional_float(plate_thickness)
    tooth_type_value = normalize_tooth_type(tooth_type)
    teeth_count_text = clean_text_value(teeth_count)
    teeth_count_value = first_int_value(teeth_count_text)
    module_text = clean_text_value(module)
    module_value = optional_float(module) if module_text else None
    pressure_angle_value = optional_float(pressure_angle)
    profile_shift_coefficient_value = optional_float(profile_shift_coefficient)
    span_teeth_count_value = optional_int(span_teeth_count)
    common_normal_length_text = clean_text_value(common_normal_length)
    common_normal_length_value = common_normal_value_from_text(common_normal_length_text, tooth_type_value)
    pin_diameter_value = optional_float(pin_diameter)
    pin_span_value = optional_float(pin_span)
    drawing.product_code = product_code or None
    drawing.product_name = product_name or None
    drawing.product_category = product_category.strip() or None
    drawing.remark = remark.strip() or None
    drawing.material = material or None
    drawing.thickness = product_thickness_value or plate_thickness_value
    drawing.max_outer_diameter = max_outer_diameter_value
    drawing.min_inner_diameter = min_inner_diameter_value
    drawing.product_thickness = product_thickness_value
    drawing.plate_thickness = plate_thickness_value
    drawing.tooth_type = tooth_type_value
    drawing.teeth_count = teeth_count_value
    drawing.teeth_count_text = teeth_count_text
    drawing.module = module_value
    drawing.module_text = module_text
    drawing.pressure_angle = pressure_angle_value
    drawing.profile_shift_coefficient = profile_shift_coefficient_value
    drawing.span_teeth_count = span_teeth_count_value
    drawing.common_normal_length = common_normal_length_value
    drawing.common_normal_length_text = common_normal_length_text
    drawing.pin_diameter = pin_diameter_value
    drawing.pin_span = pin_span_value
    drawing.expected_scrap_size = expected_scrap_size or None
    drawing.confirmed = 1
    apply_drawing_version(drawing, db, force_increment=was_confirmed)
    synced_count = sync_product_inventory_from_drawing(drawing, db)
    record_operation_log(
        db,
        "drawing_confirm",
        "drawing",
        drawing.id,
        None,
        f"确认图纸，同步成品库存 {synced_count} 批",
        before_data=before_data,
        after_data=drawing_snapshot(drawing),
    )
    db.commit()
    return RedirectResponse(f"/admin/drawings/{drawing.id}?notice=confirmed", status_code=303)


@router.get("/admin/scraps", response_class=HTMLResponse)
def scraps_page(
    source_product_code: str = "",
    drawing_id: str = "",
    material: str = "",
    thickness: str = "",
    required_diameter: str = "",
    location: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    records = db.query(ScrapGenerationRecord).order_by(ScrapGenerationRecord.registered_at.desc()).all()
    scrap_ids = [record.scrap_inventory_id for record in records if record.scrap_inventory_id]
    scrap_map = {
        item.id: item
        for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(scrap_ids)).all()
    } if scrap_ids else {}
    filtered_records = []
    thickness_value = optional_float(thickness)
    required_diameter_value = optional_float(required_diameter)
    selected_drawing = db.get(ProductDrawing, int(drawing_id)) if drawing_id.isdigit() else None
    required_drawing_diameter = None
    drawing_required_thickness = None
    required_scrap_diameter = None
    if selected_drawing:
        required_drawing_diameter = drawing_required_diameter(selected_drawing)
        drawing_required_thickness = effective_drawing_thickness(selected_drawing)
        required_scrap_diameter = scrap_required_diameter(selected_drawing)
    for record in records:
        item = scrap_map.get(record.scrap_inventory_id)
        if source_product_code.strip():
            keyword_value = source_product_code.strip().lower()
            searchable = " ".join(
                str(value or "")
                for value in (
                    record.source_product_code,
                    item.material if item else "",
                    item.usable_size if item else "",
                    item.location if item else "",
                )
            ).lower()
            if keyword_value not in searchable:
                continue
        if item and item.status != "available":
            continue
        if item and material.strip() and material.strip() not in item.material:
            continue
        if item and thickness_value is not None and item.thickness != thickness_value:
            continue
        if item and required_diameter_value is not None and (item.diameter is None or item.diameter < required_diameter_value):
            continue
        if item and selected_drawing:
            if not scrap_matches_drawing(item, selected_drawing):
                continue
        if item and location.strip() and location.strip() not in (item.location or ""):
            continue
        filtered_records.append(record)
    spec_grouped = {}
    for record in filtered_records:
        item = scrap_map.get(record.scrap_inventory_id)
        if item:
            location_label = scrap_location_label(item)
            size_label = item.usable_size or (f"φ{item.diameter:g}" if item.diameter is not None else "-")
            spec_key = (item.material, item.thickness, size_label)
            if spec_key not in spec_grouped:
                spec_grouped[spec_key] = {"material": item.material, "thickness": item.thickness, "usable_size": size_label, "locations": set(), "quantity": 0, "batch_count": 0}
            spec_grouped[spec_key]["quantity"] += item.quantity
            spec_grouped[spec_key]["batch_count"] += 1
            if location_label != "-":
                spec_grouped[spec_key]["locations"].add(location_label)
    spec_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(str(group['material']))}</td>
          <td>{group['thickness']}</td>
          <td>{html.escape(str(group['usable_size']))}</td>
          <td><strong>{group['quantity']}</strong></td>
          <td>{group['batch_count']}</td>
          <td>{html.escape(' / '.join(sorted(group['locations'])) or '-')}</td>
          <td><a class="btn secondary" href="/admin/scraps/detail?{build_query({'material': group['material'], 'thickness': group['thickness'], 'usable_size': group['usable_size']})}">查看明细</a></td>
        </tr>
        """
        for group in sorted(spec_grouped.values(), key=lambda group: (str(group["material"]), group["thickness"] or 0, str(group["usable_size"])))
    )
    source_product_options = datalist_options([record.source_product_code for record in records])
    material_options = datalist_options(inventory_distinct_options(db, "scrap", "material", quantity_positive=True, status="available"))
    thickness_options = datalist_options(inventory_distinct_options(db, "scrap", "thickness", quantity_positive=True, status="available"))
    diameter_options = datalist_options(inventory_distinct_options(db, "scrap", "diameter", quantity_positive=True, status="available"))
    location_options = datalist_options(inventory_distinct_options(db, "scrap", "location", quantity_positive=True, status="available"))
    body = f"""
    <div class='top'><div><h1>余料库存</h1><p class='muted'>按材质、厚度和可用尺寸汇总余料，点击明细查看每批来源和出入库流水。</p></div><div class="actions"><a class="btn" href="/admin/scraps/outbound">余料出库</a><a class="btn secondary" href="/admin/scraps/transactions">余料流水</a><a class="btn secondary" href="/admin/scraps/pending">待入库余料</a><a class="btn secondary" href="{export_link('scrap_inventory', {'material': material.strip(), 'thickness': thickness.strip(), 'location': location.strip()})}">导出Excel</a></div></div>
    <section class="card">
      <form method="get" action="/admin/scraps" class="actions" style="justify-content:flex-start">
        <input name="source_product_code" value="{safe_value(source_product_code.strip())}" list="scrap-source-product-options" placeholder="输入来源/材质/尺寸/库位" style="width:220px"><datalist id="scrap-source-product-options">{source_product_options}</datalist>
        <input type="search" data-select-filter="scrap-drawing-select" placeholder="筛选匹配图纸" style="width:180px">
        <select id="scrap-drawing-select" name="drawing_id">{confirmed_drawing_options(db, selected_id=int(drawing_id) if drawing_id.isdigit() else None, include_blank=True)}</select>
        <input name="material" value="{safe_value(material.strip())}" list="scrap-material-options" placeholder="材质" style="width:140px"><datalist id="scrap-material-options">{material_options}</datalist>
        <input name="thickness" value="{safe_value(thickness.strip())}" list="scrap-thickness-options" placeholder="厚度" style="width:120px"><datalist id="scrap-thickness-options">{thickness_options}</datalist>
        <input name="required_diameter" value="{safe_value(required_diameter.strip())}" list="scrap-diameter-options" placeholder="直径≥" style="width:120px"><datalist id="scrap-diameter-options">{diameter_options}</datalist>
        <input name="location" value="{safe_value(location.strip())}" list="scrap-location-options" placeholder="库位" style="width:140px"><datalist id="scrap-location-options">{location_options}</datalist>
        <button class="btn" type="submit">搜索余料</button>
        <a class="btn secondary" href="/admin/scraps">清空</a>
      </form>
    </section>
    {f'<section class="card"><strong>当前按图纸匹配：</strong>{selected_drawing.product_code or "-"}，图纸外径/外框 {required_drawing_diameter or "-"}，需要余料直径 ≥ {required_scrap_diameter:g}，厚度 {drawing_required_thickness or "-"}，材质 {selected_drawing.material or "-"}</section>' if selected_drawing and required_scrap_diameter is not None else ''}
    <section class='card'><h2>按规格汇总</h2><table><thead><tr><th>材质</th><th>厚度</th><th>可用尺寸</th><th>总数量</th><th>批次数</th><th>库位</th><th>操作</th></tr></thead><tbody>{spec_rows or "<tr><td colspan='7'>暂无余料。</td></tr>"}</tbody></table></section>
    """
    return page("余料库存", body)


@router.get("/admin/scraps/detail", response_class=HTMLResponse)
def scrap_group_detail_page(
    material: str = "",
    thickness: str = "",
    usable_size: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    material_value = material.strip()
    thickness_value = optional_float(thickness)
    usable_size_value = usable_size.strip()
    if not material_value or thickness_value is None or not usable_size_value:
        raise HTTPException(status_code=400, detail="余料规格参数错误")
    candidates = (
        db.query(MaterialInventory)
        .filter(
            MaterialInventory.inventory_type == "scrap",
            MaterialInventory.material == material_value,
            MaterialInventory.thickness == thickness_value,
            MaterialInventory.status == "available",
        )
        .order_by(MaterialInventory.created_at.asc())
        .all()
    )
    items = [
        item for item in candidates
        if (item.usable_size or (f"φ{item.diameter:g}" if item.diameter is not None else "-")) == usable_size_value
    ]
    item_ids = [item.id for item in items]
    record_map = {
        record.scrap_inventory_id: record
        for record in db.query(ScrapGenerationRecord).filter(ScrapGenerationRecord.scrap_inventory_id.in_(item_ids)).all()
    } if item_ids else {}
    transactions = (
        db.query(InventoryTransactionRecord)
        .filter(InventoryTransactionRecord.inventory_id.in_(item_ids))
        .order_by(InventoryTransactionRecord.created_at.desc())
        .all()
    ) if item_ids else []
    item_map = {item.id: item for item in items}
    total_quantity = sum(item.quantity for item in items)
    batch_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(record_map.get(item.id).source_product_code if record_map.get(item.id) and record_map.get(item.id).source_product_code else item.source_product_code or '-')}</td>
          <td>{drawing_version_label(db, record_map.get(item.id).source_drawing_id) if record_map.get(item.id) else drawing_version_label(db, item.source_drawing_id)}</td>
          <td><strong>{item.quantity}</strong></td>
          <td>{html.escape(scrap_location_label(item))}</td>
          <td>{html.escape(item.status or '-')}</td>
          <td>{html.escape(item.usable_size or '-')}</td>
          <td>{html.escape(record_map.get(item.id).theoretical_size if record_map.get(item.id) and record_map.get(item.id).theoretical_size else '-')}</td>
          <td>{html.escape(record_map.get(item.id).actual_size if record_map.get(item.id) and record_map.get(item.id).actual_size else '-')}</td>
          <td>{html.escape(record_map.get(item.id).operator_name if record_map.get(item.id) and record_map.get(item.id).operator_name else '-')}</td>
          <td>{record_map.get(item.id).registered_at if record_map.get(item.id) else item.created_at}</td>
        </tr>
        """
        for item in items
    )
    transaction_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(item_map.get(record.inventory_id).source_product_code if item_map.get(record.inventory_id) and item_map.get(record.inventory_id).source_product_code else '-')}</td>
          <td>{transaction_label(record.transaction_type)}</td>
          <td>{record.after_quantity if record.transaction_type == "confirm" and record.quantity == 0 else record.quantity}</td>
          <td>{"-" if record.transaction_type == "confirm" else record.before_quantity}</td>
          <td>{"-" if record.transaction_type == "confirm" else record.after_quantity}</td>
          <td>{html.escape(scrap_location_label(item_map.get(record.inventory_id)))}</td>
          <td>{html.escape(record.customer_name or '-')}</td>
          <td>{html.escape(record.operator_name or '-')}</td>
          <td>{html.escape(record.remark or '-')}</td>
          <td>{record.created_at}</td>
        </tr>
        """
        for record in transactions
    )
    title = f"{material_value} 厚{thickness_value:g} {usable_size_value}"
    body = f"""
    <div class="top"><div><h1>余料明细：{html.escape(title)}</h1><p class="muted">当前总数量：<strong>{total_quantity}</strong>，按材质、厚度和可用尺寸汇总。</p></div><div class="actions"><a class="btn secondary" href="/admin/scraps">返回余料库存</a><a class="btn secondary" href="/admin/scraps/transactions">余料流水</a></div></div>
    <section class="card"><h2>批次来源</h2><table><thead><tr><th>来源产品</th><th>来源图纸</th><th>数量</th><th>库位</th><th>状态</th><th>可用尺寸</th><th>理论尺寸</th><th>实际尺寸</th><th>登记人</th><th>时间</th></tr></thead><tbody>{batch_rows or "<tr><td colspan='10'>暂无该规格余料批次。</td></tr>"}</tbody></table></section>
    <section class="card"><h2>出入库流水</h2><table><thead><tr><th>来源产品</th><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>库位</th><th>客户/去向</th><th>操作人</th><th>备注</th><th>时间</th></tr></thead><tbody>{transaction_rows or "<tr><td colspan='10'>暂无该规格余料流水。</td></tr>"}</tbody></table></section>
    """
    return page("余料明细", body)
