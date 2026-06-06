import html
import json
import math
from datetime import datetime, timedelta
from uuid import uuid4
from urllib.parse import quote

import ezdxf
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, File, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.assistant.engine import run_assistant
from app.database import get_db
from app.models import InventoryTransactionRecord, MaterialInventory, OperationLog, ProductDrawing, RawPlateSpecification, ScrapGenerationRecord
from app.services.dxf_parser import parse_dxf
from app.services.drawing_upload import delete_uploaded_drawing, save_uploaded_drawing
from app.services.drawing_version import apply_drawing_version
from app.services.excel_export import build_export_rows, content_disposition, export_filename, log_export, make_workbook_bytes
from app.services.inventory_service import adjust_inventory_quantity, ensure_drawing_can_be_changed, inventory_write_lock, product_inbound_from_drawing, reject_direct_inventory_write, reverse_inventory_transaction
from app.services.operation_log import drawing_snapshot, inventory_snapshot, record_operation_log
from app.services.qwen_service import recognize_drawing
from app.services.scrap_service import find_scrap_batches_for_outbound

router = APIRouter()


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
    drawing.module = recognized.get("module") or gear.get("module")
    drawing.pressure_angle = recognized.get("pressure_angle") or gear.get("pressure_angle")
    drawing.profile_shift_coefficient = recognized.get("profile_shift_coefficient") or gear.get("profile_shift_coefficient")
    drawing.span_teeth_count = recognized.get("span_teeth_count") or gear.get("span_teeth_count")
    drawing.common_normal_length = recognized.get("common_normal_length") or gear.get("common_normal_length")
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
    query = "&".join(
        f"{quote(str(key), safe='')}={quote(str(value), safe='')}"
        for key, value in params.items()
        if value not in ("", None)
    )
    return f"/admin/exports/{module}{'?' + query if query else ''}"


def parse_diameter_text(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = value.replace("φ", "").replace("Φ", "").strip()
    try:
        return float(cleaned.split()[0])
    except (ValueError, IndexError):
        return None


def confirmed_drawing_options(db: Session, selected_id: int | None = None, include_blank: bool = False) -> str:
    drawings = (
        db.query(ProductDrawing)
        .filter(ProductDrawing.confirmed == 1, ProductDrawing.is_active == 1)
        .order_by(ProductDrawing.product_code.asc(), ProductDrawing.version.desc())
        .all()
    )
    options = "".join(
        f"<option value='{drawing.id}' {'selected' if selected_id == drawing.id else ''}>{drawing.product_code or '-'}｜V{drawing.version or 1}｜{drawing.product_name or '-'}｜{drawing.material or '-'}｜厚度 {drawing.plate_thickness or drawing.product_thickness or drawing.thickness or '-'}</option>"
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
    return f"{drawing.product_code or '-'} V{drawing.version or 1}（{status}）"


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


def drawing_distinct_options(db: Session, field: str, confirmed_only: bool = True) -> list[object]:
    column = getattr(ProductDrawing, field)
    query = db.query(column)
    if confirmed_only:
        query = query.filter(ProductDrawing.confirmed == 1, ProductDrawing.is_active == 1)
    values = [row[0] for row in query.distinct().order_by(column.asc()).all()]
    return [value for value in values if value not in ("", None)]


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
    msp = doc.modelspace()
    items = []
    texts = []
    points = []
    stats: dict[str, int] = {}

    def add_point(x: float, y: float) -> None:
        points.append((float(x), float(y)))

    def clean_dxf_text(value: str) -> str:
        return (
            value.replace("\\P", " ")
            .replace("%%C", "φ")
            .replace("%%c", "φ")
            .replace("%%D", "°")
            .replace("%%P", "±")
        )

    def add_polyline(polyline_points: list[tuple[float, float]], closed: bool = False) -> None:
        if len(polyline_points) < 2:
            return
        for x, y in polyline_points:
            add_point(x, y)
        items.append(("polyline", (polyline_points, closed)))

    def add_entity(entity, depth: int = 0) -> None:
        dxftype = entity.dxftype()
        stats[dxftype] = stats.get(dxftype, 0) + 1
        try:
            if dxftype == "LINE":
                start = entity.dxf.start
                end = entity.dxf.end
                add_point(start.x, start.y)
                add_point(end.x, end.y)
                items.append(("line", (start.x, start.y, end.x, end.y)))
            elif dxftype == "CIRCLE":
                center = entity.dxf.center
                radius = float(entity.dxf.radius)
                add_point(center.x - radius, center.y - radius)
                add_point(center.x + radius, center.y + radius)
                items.append(("circle", (center.x, center.y, radius)))
            elif dxftype == "ARC":
                center = entity.dxf.center
                radius = float(entity.dxf.radius)
                add_point(center.x - radius, center.y - radius)
                add_point(center.x + radius, center.y + radius)
                items.append(("arc", (center.x, center.y, radius, float(entity.dxf.start_angle), float(entity.dxf.end_angle))))
            elif dxftype == "LWPOLYLINE":
                polyline_points = [(float(point[0]), float(point[1])) for point in entity.get_points()]
                add_polyline(polyline_points, bool(entity.closed))
            elif dxftype == "POLYLINE":
                polyline_points = [(float(vertex.dxf.location.x), float(vertex.dxf.location.y)) for vertex in entity.vertices]
                add_polyline(polyline_points, bool(entity.is_closed))
            elif dxftype == "SPLINE":
                polyline_points = [(float(point.x), float(point.y)) for point in entity.flattening(0.5)]
                add_polyline(polyline_points)
            elif dxftype == "ELLIPSE":
                polyline_points = [(float(point.x), float(point.y)) for point in entity.flattening(0.5)]
                add_polyline(polyline_points, bool(entity.dxf.start_param == 0 and entity.dxf.end_param >= math.tau))
            elif dxftype in {"SOLID", "TRACE", "3DFACE"}:
                polyline_points = []
                for attr in ("vtx0", "vtx1", "vtx2", "vtx3"):
                    if hasattr(entity.dxf, attr):
                        point = getattr(entity.dxf, attr)
                        polyline_points.append((float(point.x), float(point.y)))
                add_polyline(polyline_points, True)
            elif dxftype == "HATCH":
                for path in entity.paths:
                    if hasattr(path, "vertices"):
                        polyline_points = [(float(vertex[0]), float(vertex[1])) for vertex in path.vertices]
                        add_polyline(polyline_points, True)
                    elif hasattr(path, "edges"):
                        for edge in path.edges:
                            edge_type = edge.EDGE_TYPE
                            if edge_type == "LineEdge":
                                add_entity(edge.construction_tool(), depth + 1)
                            elif hasattr(edge, "flattening"):
                                polyline_points = [(float(point.x), float(point.y)) for point in edge.flattening(0.5)]
                                add_polyline(polyline_points)
            elif dxftype == "TEXT":
                insert = entity.dxf.insert
                text = clean_dxf_text(entity.dxf.text or "")
                add_point(insert.x, insert.y)
                texts.append((insert.x, insert.y, text))
            elif dxftype == "MTEXT":
                insert = entity.dxf.insert
                text = clean_dxf_text(entity.text or "")
                add_point(insert.x, insert.y)
                texts.append((insert.x, insert.y, text))
            elif dxftype == "DIMENSION":
                raw_text = entity.dxf.text or ""
                text = clean_dxf_text(raw_text)
                midpoint = getattr(entity.dxf, "text_midpoint", None)
                if text and midpoint:
                    add_point(midpoint.x, midpoint.y)
                    texts.append((midpoint.x, midpoint.y, text))
                for virtual_entity in entity.virtual_entities():
                    add_entity(virtual_entity, depth + 1)
            elif dxftype == "INSERT" and depth < 4:
                for attrib in getattr(entity, "attribs", []):
                    insert = attrib.dxf.insert
                    text = clean_dxf_text(attrib.dxf.text or "")
                    add_point(insert.x, insert.y)
                    texts.append((insert.x, insert.y, text))
                for virtual_entity in entity.virtual_entities():
                    add_entity(virtual_entity, depth + 1)
        except Exception:
            pass

    for entity in msp:
        add_entity(entity)

    if not points:
        return "<p>该DXF暂时没有可预览的线、圆、文字实体。</p>"

    min_x = min(x for x, _ in points)
    max_x = max(x for x, _ in points)
    min_y = min(y for _, y in points)
    max_y = max(y for _, y in points)
    margin = max(max_x - min_x, max_y - min_y) * 0.08 or 10
    view_x = min_x - margin
    view_y = -(max_y + margin)
    view_w = max_x - min_x + margin * 2
    view_h = max_y - min_y + margin * 2

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_x:.3f} {view_y:.3f} {view_w:.3f} {view_h:.3f}" style="width:100%;height:78vh;background:#fff;border:1px solid var(--line);border-radius:18px">',
        '<g transform="scale(1,-1)" fill="none" stroke="#111827" stroke-width="0.6">',
    ]
    for kind, data in items:
        if kind == "line":
            x1, y1, x2, y2 = data
            svg.append(f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}"/>')
        elif kind == "circle":
            cx, cy, radius = data
            svg.append(f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{radius:.3f}"/>')
        elif kind == "arc":
            cx, cy, radius, start_angle, end_angle = data
            start = math.radians(start_angle)
            end = math.radians(end_angle)
            x1 = cx + radius * math.cos(start)
            y1 = cy + radius * math.sin(start)
            x2 = cx + radius * math.cos(end)
            y2 = cy + radius * math.sin(end)
            large_arc = 1 if ((end_angle - start_angle) % 360) > 180 else 0
            svg.append(f'<path d="M {x1:.3f} {y1:.3f} A {radius:.3f} {radius:.3f} 0 {large_arc} 0 {x2:.3f} {y2:.3f}"/>')
        elif kind == "polyline" and len(data) > 1:
            polyline_points, closed = data
            points_text = " ".join(f"{x:.3f},{y:.3f}" for x, y in polyline_points)
            if closed:
                svg.append(f'<polygon points="{points_text}" fill="none"/>')
            else:
                svg.append(f'<polyline points="{points_text}"/>')
    svg.append("</g>")
    svg.append('<g fill="#dc2626" font-family="Arial, sans-serif" font-size="4">')
    for x, y, text in texts:
        clean_text = html.escape(text[:100])
        if clean_text:
            svg.append(f'<text x="{x:.3f}" y="{-y:.3f}">{clean_text}</text>')
    stat_text = "，".join(f"{key}:{value}" for key, value in sorted(stats.items()))
    svg.append("</g>")
    svg.append(f'<text x="{view_x + 3:.3f}" y="{view_y + 8:.3f}" fill="#2563eb" font-size="5">实体统计：{html.escape(stat_text)}</text>')
    svg.append("</svg>")
    return "\n".join(svg)


def page(title: str, body: str) -> HTMLResponse:
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
    .layout {{ display:grid; grid-template-columns:240px 1fr; min-height:100vh; }}
    aside {{ background:#0f1f46; color:white; padding:24px 18px; max-height:100vh; overflow:auto; position:sticky; top:0; }}
    .brand {{ font-size:20px; font-weight:800; margin-bottom:26px; }}
    nav a {{ display:block; padding:12px 14px; border-radius:12px; color:rgba(255,255,255,.82); margin-bottom:8px; }}
    nav a:hover, nav a.active {{ background:rgba(255,255,255,.14); color:white; }}
    nav a.active {{ box-shadow:inset 3px 0 0 #93c5fd; }}
    .nav-section {{ margin:10px 0; }}
    .nav-section summary {{ list-style:none; display:flex; align-items:center; justify-content:space-between; padding:10px 14px; border-radius:12px; color:rgba(255,255,255,.56); font-size:12px; font-weight:800; letter-spacing:.08em; cursor:pointer; user-select:none; }}
    .nav-section summary::-webkit-details-marker {{ display:none; }}
    .nav-section summary::after {{ content:"⌄"; font-size:14px; color:rgba(255,255,255,.62); transition:transform .18s ease; }}
    .nav-section:not([open]) summary::after {{ transform:rotate(-90deg); }}
    .nav-section summary:hover {{ background:rgba(255,255,255,.08); color:rgba(255,255,255,.86); }}
    .nav-section .nav-items {{ padding-top:4px; }}
    .nav-root {{ margin-bottom:12px; }}
    main {{ padding:28px; }}
    .top {{ display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:22px; }}
    h1 {{ margin:0; font-size:28px; }}
    .muted {{ color:var(--muted); }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:20px; padding:20px; box-shadow:0 12px 34px rgba(20,32,55,.06); margin-bottom:18px; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; }}
    .stat strong {{ display:block; font-size:30px; margin-top:8px; }}
    table {{ width:100%; min-width:760px; border-collapse:collapse; }}
    th,td {{ padding:12px 10px; border-bottom:1px solid var(--line); text-align:left; font-size:14px; }}
    th {{ color:var(--muted); font-weight:700; background:#fbfcff; position:sticky; top:0; z-index:1; }}
    .form-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }}
    label {{ display:block; font-size:13px; color:var(--muted); margin-bottom:6px; }}
    input,select {{ width:100%; height:42px; border:1px solid var(--line); border-radius:12px; padding:0 12px; background:white; }}
    .btn {{ display:inline-flex; align-items:center; justify-content:center; height:42px; padding:0 16px; border-radius:12px; border:none; background:var(--primary); color:white; font-weight:700; cursor:pointer; }}
    .btn.secondary {{ background:#eef2ff; color:var(--primary); }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; }}
    .badge {{ display:inline-block; padding:4px 9px; border-radius:999px; background:#eef2ff; color:#1d4ed8; font-size:12px; font-weight:700; }}
    .dropzone {{ border:2px dashed #b8c4dc; border-radius:18px; background:#f8fbff; padding:34px; text-align:center; transition:.2s ease; cursor:pointer; }}
    .dropzone:hover,.dropzone.dragover {{ border-color:var(--primary); background:#eef4ff; transform:translateY(-1px); }}
    .dropzone strong {{ display:block; font-size:18px; margin-bottom:8px; }}
    .dropzone span {{ color:var(--muted); }}
    .file-name {{ margin-top:12px; color:var(--primary); font-weight:700; }}
    .hidden-file {{ position:absolute; width:1px; height:1px; opacity:0; pointer-events:none; }}
    .table-scroll {{ overflow:auto; max-height:68vh; }}
    .table-scroll table {{ margin:0; }}
    pre {{ white-space:pre-wrap; word-break:break-all; background:#0f172a; color:#dbeafe; padding:16px; border-radius:14px; overflow:auto; }}
    @media (max-width:900px) {{ .layout {{ grid-template-columns:1fr; }} aside {{ position:static; max-height:none; }} .grid,.form-grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="layout">
    <aside id="admin-sidebar">
      <div class="brand">杭州特耐时</div>
      <nav>
        <div class="nav-root">
          <a href="/admin">后台首页</a>
          <a href="/admin/assistant">智能助手</a>
        </div>
        <details class="nav-section" data-nav-section="drawing" open>
          <summary>图纸管理</summary>
          <div class="nav-items">
            <a href="/admin/drawings">图纸识别</a>
            <a href="/admin/drawings/pending">待确认图纸</a>
            <a href="/admin/drawings/confirmed">已确认图纸</a>
          </div>
        </details>
        <details class="nav-section" data-nav-section="raw-plate" open>
          <summary>原料管理</summary>
          <div class="nav-items">
            <a href="/admin/raw-plate-specifications">板料规格</a>
            <a href="/admin/raw-plates">板料库存</a>
            <a href="/admin/raw-plates/inbound">板料入库</a>
            <a href="/admin/raw-plates/outbound">板料出库</a>
            <a href="/admin/raw-plates/transactions">板料流水</a>
          </div>
        </details>
        <details class="nav-section" data-nav-section="inventory" open>
          <summary>库存管理</summary>
          <div class="nav-items">
            <a href="/admin/inventory">库存查询</a>
            <a href="/admin/inventory/inbound">产品入库</a>
            <a href="/admin/inventory/outbound">产品出库</a>
            <a href="/admin/inventory/transactions">库存流水</a>
          </div>
        </details>
        <details class="nav-section" data-nav-section="scrap" open>
          <summary>余料管理</summary>
          <div class="nav-items">
            <a href="/admin/scraps/pending">待入库余料</a>
            <a href="/admin/scraps/outbound">余料出库</a>
            <a href="/admin/scraps">余料记录</a>
            <a href="/admin/scraps/transactions">余料流水</a>
          </div>
        </details>
        <details class="nav-section" data-nav-section="report" open>
          <summary>报表中心</summary>
          <div class="nav-items">
            <a href="/admin/reports/outbound">出库统计</a>
          </div>
        </details>
        <details class="nav-section" data-nav-section="system" open>
          <summary>系统</summary>
          <div class="nav-items">
            <a href="/admin/operation-logs">操作日志</a>
          </div>
        </details>
      </nav>
    </aside>
    <main>{body}</main>
  </div>
  <script>
    document.querySelectorAll('.card > table').forEach((table) => {{
      const wrapper = document.createElement('div');
      wrapper.className = 'table-scroll';
      table.parentNode.insertBefore(wrapper, table);
      wrapper.appendChild(table);
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
      document.querySelectorAll('.nav-section').forEach((section) => {{
        const key = 'adminNavSection:' + (section.dataset.navSection || '');
        const saved = localStorage.getItem(key);
        const hasActiveLink = section.querySelector('a.active');
        if (saved === 'closed' && !hasActiveLink) section.open = false;
        if (hasActiveLink) section.open = true;
        section.addEventListener('toggle', () => {{
          localStorage.setItem(key, section.open ? 'open' : 'closed');
        }});
      }});
    }}
  </script>
</body>
</html>
    """
    return HTMLResponse(html)


@router.get("/admin", response_class=HTMLResponse)
def admin_home(db: Session = Depends(get_db)) -> HTMLResponse:
    pending_drawing_count = db.query(ProductDrawing).filter(ProductDrawing.confirmed == 0).count()
    pending_scrap_count = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "scrap", MaterialInventory.status == "pending").count()
    latest_logs = db.query(OperationLog).order_by(OperationLog.created_at.desc()).limit(6).all()
    latest_log_rows = "".join(
        f"""
        <tr>
          <td>{log.created_at}</td>
          <td><span class="badge">{html.escape(log.action)}</span></td>
          <td>{html.escape(log.object_type)}</td>
          <td>{html.escape(log.operator_name or '-')}</td>
          <td>{html.escape(log.remark or '-')}</td>
        </tr>
        """
        for log in latest_logs
    )
    body = f"""
    <div class="top"><div><h1>工作台</h1><p class="muted">只保留需要处理的待办事项。</p></div></div>
    <section class="grid">
      <div class="card stat"><span class="muted">待办：确认图纸</span><strong>{pending_drawing_count}</strong><a class="btn secondary" href="/admin/drawings/pending">处理图纸</a></div>
      <div class="card stat"><span class="muted">待办：余料入库</span><strong>{pending_scrap_count}</strong><a class="btn secondary" href="/admin/scraps/pending">确认余料</a></div>
    </section>
    <section class="card">
      <div class="top" style="margin-bottom:12px"><h2 style="margin:0">最近操作日志</h2><a class="btn secondary" href="/admin/operation-logs">查看全部</a></div>
      <table><thead><tr><th>时间</th><th>操作</th><th>对象</th><th>操作人</th><th>备注</th></tr></thead><tbody>{latest_log_rows or "<tr><td colspan='5'>暂无操作日志。</td></tr>"}</tbody></table>
    </section>
    """
    return page("后台首页", body)


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
    <div class="top"><div><h1>智能助手</h1><p class="muted">第一期为只读助手：只查询和解释规则，不执行入库、出库、撤回、删除或修改。</p></div></div>
    <section class="card">
      <div class="actions" style="margin-bottom:12px">
        <button class="btn secondary" type="button" onclick="askAssistant('查一下板料库存')">查板料库存</button>
        <button class="btn secondary" type="button" onclick="askAssistant('查一下产品库存')">查产品库存</button>
        <button class="btn secondary" type="button" onclick="askAssistant('查一下余料库存')">查余料库存</button>
        <button class="btn secondary" type="button" onclick="askAssistant('今天出库统计')">今天出库统计</button>
        <button class="btn secondary" type="button" onclick="askAssistant('图纸能不能修改')">图纸修改规则</button>
        <button class="btn secondary" type="button" onclick="clearAssistantHistory()">清空对话</button>
      </div>
      <div id="assistant-messages" style="min-height:260px;max-height:520px;overflow:auto;background:#f8fbff;border:1px solid var(--line);border-radius:16px;padding:14px;margin-bottom:14px"></div>
      <form id="assistant-form" class="actions" style="align-items:center">
        <input id="assistant-input" placeholder="例如：查 65Mn 板料库存 / 今天出库统计 / 图纸能不能改" style="flex:1;min-width:260px">
        <button class="btn" type="submit">发送</button>
      </form>
    </section>
    <script>
      const messages = document.getElementById('assistant-messages');
      const input = document.getElementById('assistant-input');
      const historyKey = 'inventoryAssistantMessages';
      const contextKey = 'inventoryAssistantContext';
      let assistantContext = localStorage.getItem(contextKey) || '';
      let assistantHistory = JSON.parse(localStorage.getItem(historyKey) || '[]');
      function escapeHtml(text) {
        return String(text || '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
      }
      function saveAssistantState() {
        localStorage.setItem(historyKey, JSON.stringify(assistantHistory.slice(-50)));
        localStorage.setItem(contextKey, assistantContext || '');
      }
      function renderAnalysisData(data) {
        if (!data || !Array.isArray(data.columns) || !Array.isArray(data.rows)) return '';
        const header = data.columns.map(column => `<th>${escapeHtml(column.label || column.prop)}</th>`).join('');
        const body = data.rows.map(row => `<tr>${data.columns.map(column => `<td>${escapeHtml(row[column.prop])}</td>`).join('')}</tr>`).join('');
        return `
          <div style="margin-top:10px;background:white;border:1px solid var(--line);border-radius:14px;padding:12px">
            <div class="top" style="margin-bottom:10px">
              <h3 style="margin:0">${escapeHtml(data.title || '分析结果')}</h3>
              <div class="actions">
                <button class="btn secondary export-analysis" type="button">导出Excel</button>
                <button class="btn secondary print-analysis" type="button">打印</button>
              </div>
            </div>
            <div class="table-scroll"><table><thead><tr>${header}</tr></thead><tbody>${body || `<tr><td colspan="${data.columns.length}">暂无分析数据。</td></tr>`}</tbody></table></div>
          </div>
        `;
      }
      function renderActions(actions) {
        if (!Array.isArray(actions) || !actions.length) return '';
        return `<div class="actions" style="margin-top:8px">${actions.map(action => `<a class="btn secondary" href="${escapeHtml(action.url)}">${escapeHtml(action.label)}</a>`).join('')}</div>`;
      }
      function appendMessage(role, text, data, actions) {
        const block = document.createElement('div');
        block.style.margin = '0 0 12px';
        block.innerHTML = `<div class="muted">${role}</div><pre style="margin:6px 0 0;background:${role === '你' ? '#eef2ff' : '#0f172a'};color:${role === '你' ? '#172033' : '#dbeafe'}">${escapeHtml(text)}</pre>${renderAnalysisData(data)}${renderActions(actions)}`;
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
      }
      function printAnalysisData(data) {
        const columns = Array.isArray(data.columns) ? data.columns : [];
        const rows = Array.isArray(data.rows) ? data.rows : [];
        const header = columns.map(column => `<th>${escapeHtml(column.label || column.prop)}</th>`).join('');
        const body = rows.map(row => `<tr>${columns.map(column => `<td>${escapeHtml(row[column.prop])}</td>`).join('')}</tr>`).join('');
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
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `${data.title || 'AI分析结果'}.xlsx`;
        link.click();
        URL.revokeObjectURL(url);
      }
      function rememberMessage(role, text, data, actions) {
        assistantHistory.push({role, text, data, actions});
        saveAssistantState();
      }
      function clearAssistantHistory() {
        assistantContext = '';
        assistantHistory = [];
        saveAssistantState();
        messages.innerHTML = '';
        appendMessage('助手', '对话已清空。你可以重新开始查询。');
      }
      async function askAssistant(text) {
        const message = (text || input.value || '').trim();
        if (!message) return;
        input.value = '';
        appendMessage('你', message);
        rememberMessage('你', message);
        const response = await fetch('/admin/assistant/chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/x-www-form-urlencoded'},
          body: new URLSearchParams({message, context: assistantContext})
        });
        const data = await response.json();
        assistantContext = data.context || assistantContext;
        const answer = data.answer || data.detail || '没有返回结果';
        appendMessage('助手', answer, data.data, data.actions);
        rememberMessage('助手', answer, data.data, data.actions);
      }
      document.getElementById('assistant-form').addEventListener('submit', (event) => {
        event.preventDefault();
        askAssistant();
      });
      if (assistantHistory.length) {
        assistantHistory.forEach((item) => appendMessage(item.role, item.text, item.data, item.actions));
      } else {
        const welcome = '你好，我可以查询产品、板料、余料、图纸和出库统计，也可以分析库存Top、低库存、出库Top、余料积压、图纸版本和智能预警。';
        appendMessage('助手', welcome);
        rememberMessage('助手', welcome);
      }
    </script>
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
                "thickness": item.thickness,
                "quantity": 0,
                "locations": set(),
                "latest": item.updated_at or item.created_at,
            }
        grouped[code]["quantity"] += item.quantity
        if item.location:
            grouped[code]["locations"].add(item.location)
        item_time = item.updated_at or item.created_at
        if item_time and item_time > grouped[code]["latest"]:
            grouped[code]["latest"] = item_time
    rows = "".join(
        f"""
        <tr>
          <td>{group['code']}</td><td>{group['material']}</td><td>{group['thickness']}</td><td><strong>{group['quantity']}</strong></td><td>{' / '.join(sorted(group['locations'])) or '-'}</td><td>{group['latest'] or '-'}</td><td><a class='btn secondary' href='/admin/inventory/product/{quote(str(group['code']), safe="")}'>查看明细</a></td>
        </tr>
        """
        for group in grouped.values()
    )
    product_codes = inventory_distinct_options(db, "product", "material_code", quantity_positive=True)
    source_codes = inventory_distinct_options(db, "product", "source_product_code", quantity_positive=True)
    product_code_options = select_options(product_codes + source_codes, keyword, "全部型号")
    material_options = select_options(inventory_distinct_options(db, "product", "material", quantity_positive=True), material, "全部材质")
    thickness_options = select_options(inventory_distinct_options(db, "product", "thickness", quantity_positive=True), thickness, "全部厚度")
    location_options = select_options(inventory_distinct_options(db, "product", "location", quantity_positive=True), location, "全部库位")
    body = f"""
    <div class="top"><div><h1>库存查询</h1><p class="muted">只查询产品库存汇总；入库和出库请进入单独页面操作。</p></div><div class="actions"><a class="btn" href="/admin/inventory/inbound">产品入库</a><a class="btn secondary" href="/admin/inventory/outbound">产品出库</a><a class="btn secondary" href="/admin/inventory/transactions">库存流水</a><a class="btn secondary" href="{export_link('product_inventory', {'q': keyword, 'material': material.strip(), 'thickness': thickness.strip()})}">导出Excel</a></div></div>
    <section class="card">
      <form method="get" action="/admin/inventory" class="actions" style="justify-content:flex-start">
        <select name="q" style="width:220px">{product_code_options}</select>
        <input type="hidden" name="inventory_type" value="product">
        <select name="material" style="width:150px">{material_options}</select>
        <select name="thickness" style="width:130px">{thickness_options}</select>
        <select name="location" style="width:150px">{location_options}</select>
        <button class="btn" type="submit">搜索库存</button>
        <a class="btn secondary" href="/admin/inventory">清空</a>
      </form>
    </section>
    <section class="card"><h2>库存汇总</h2><table><thead><tr><th>产品编号</th><th>材质</th><th>厚度</th><th>总数量</th><th>库位</th><th>最近更新时间</th><th>操作</th></tr></thead><tbody>{rows or "<tr><td colspan='7'>暂无库存。</td></tr>"}</tbody></table></section>
    """
    return page("库存管理", body)


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
          <td>{group['material']}</td><td>{group['length'] or '-'}</td><td>{group['width'] or '-'}</td><td>{group['thickness']}</td><td><strong>{group['quantity']}</strong></td><td>{group['batch_count']}</td><td>{' / '.join(sorted(group['locations'])) or '-'}</td>
        </tr>
        """
        for group in summary.values()
    )
    detail_rows = "".join(
        f"""
        <tr>
          <td>{item.material_code or '-'}</td><td>{item.material}</td><td>{item.length or '-'}</td><td>{item.width or '-'}</td><td>{item.thickness}</td><td><strong>{item.quantity}</strong></td><td>{item.location or '-'}</td><td>{item.status}</td><td>{item.updated_at or item.created_at}</td><td><a class="btn secondary" href="/admin/raw-plates/{item.id}/edit">修改</a></td>
        </tr>
        """
        for item in items
    )
    batch_options = select_options(inventory_distinct_options(db, "raw_plate", "material_code", quantity_positive=True), keyword, "全部批次")
    material_options = select_options(inventory_distinct_options(db, "raw_plate", "material", quantity_positive=True), material, "全部材质")
    length_options = select_options(inventory_distinct_options(db, "raw_plate", "length", quantity_positive=True), length, "全部长度")
    width_options = select_options(inventory_distinct_options(db, "raw_plate", "width", quantity_positive=True), width, "全部宽度")
    thickness_options = select_options(inventory_distinct_options(db, "raw_plate", "thickness", quantity_positive=True), thickness, "全部厚度")
    location_options = select_options(inventory_distinct_options(db, "raw_plate", "location", quantity_positive=True), location, "全部库位")
    body = f"""
    <div class="top"><div><h1>板料库存</h1><p class="muted">查看按重量换算入库的原料钢板库存。</p></div><div class="actions"><a class="btn" href="/admin/raw-plates/inbound">板料入库</a><a class="btn secondary" href="/admin/raw-plates/outbound">板料出库</a><a class="btn secondary" href="{export_link('raw_plate_inventory', {'q': keyword, 'material': material.strip(), 'thickness': thickness.strip()})}">导出Excel</a></div></div>
    <section class="card">
      <form method="get" action="/admin/raw-plates" class="actions" style="justify-content:flex-start">
        <select name="q" style="width:180px">{batch_options}</select>
        <select name="material" style="width:150px">{material_options}</select>
        <select name="length" style="width:130px">{length_options}</select>
        <select name="width" style="width:130px">{width_options}</select>
        <select name="thickness" style="width:130px">{thickness_options}</select>
        <select name="location" style="width:150px">{location_options}</select>
        <button class="btn" type="submit">搜索板料</button>
        <a class="btn secondary" href="/admin/raw-plates">清空</a>
      </form>
    </section>
    <section class="card"><h2>板料规格汇总</h2><table><thead><tr><th>材质</th><th>长mm</th><th>宽mm</th><th>厚mm</th><th>总块数</th><th>批次数</th><th>库位</th></tr></thead><tbody>{summary_rows or "<tr><td colspan='7'>暂无板料库存。</td></tr>"}</tbody></table></section>
    <section class="card"><h2>板料批次明细</h2><table><thead><tr><th>批次编号</th><th>材质</th><th>长mm</th><th>宽mm</th><th>厚mm</th><th>剩余块数</th><th>库位</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{detail_rows or "<tr><td colspan='10'>暂无板料批次。</td></tr>"}</tbody></table></section>
    """
    return page("板料库存", body)


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
      <form method="post" action="/admin/raw-plates/inbound" class="form-grid">
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
    batch_code = material_code.strip() or f"RAW-{datetime.now().strftime('%Y%m%d%H%M%S')}"
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
    material_options = select_options(inventory_distinct_options(db, "raw_plate", "material", quantity_positive=True), material, "全部材质")
    length_options = select_options(inventory_distinct_options(db, "raw_plate", "length", quantity_positive=True), length, "全部长度")
    width_options = select_options(inventory_distinct_options(db, "raw_plate", "width", quantity_positive=True), width, "全部宽度")
    thickness_options = select_options(inventory_distinct_options(db, "raw_plate", "thickness", quantity_positive=True), thickness, "全部厚度")
    location_options = select_options(inventory_distinct_options(db, "raw_plate", "location", quantity_positive=True), location, "全部库位")
    location_candidates = datalist_options(inventory_distinct_options(db, "raw_plate", "location", quantity_positive=True))
    body = f"""
    <div class="top"><div><h1>板料出库</h1><p class="muted">按材质和长宽厚申请出库，系统自动按最早入库批次先进先出扣减。</p></div><div class="actions"><a class="btn secondary" href="/admin/raw-plates">返回板料库存</a><a class="btn secondary" href="/admin/raw-plates/transactions">板料流水</a></div></div>
    <section class="card">
      <form method="get" action="/admin/raw-plates/outbound" class="actions" style="justify-content:flex-start">
        <select name="material" style="width:150px">{material_options}</select>
        <select name="length" style="width:130px">{length_options}</select>
        <select name="width" style="width:130px">{width_options}</select>
        <select name="thickness" style="width:130px">{thickness_options}</select>
        <select name="location" style="width:150px">{location_options}</select>
        <button class="btn" type="submit">查看可用规格</button>
        <a class="btn secondary" href="/admin/raw-plates/outbound">清空</a>
      </form>
    </section>
    <section class="card">
      <h2>确认出库</h2>
      <p class="muted">先在下方“当前可用规格”里点击“选择出库”，系统会自动带入规格信息。</p>
      <form method="post" action="/admin/raw-plates/outbound" class="form-grid">
        <div><label>材质</label><input name="material" value="{html.escape(material.strip())}" readonly required></div>
        <div><label>长度 mm</label><input name="length" type="number" step="0.01" min="0.01" value="{html.escape(length.strip())}" readonly required></div>
        <div><label>宽度 mm</label><input name="width" type="number" step="0.01" min="0.01" value="{html.escape(width.strip())}" readonly required></div>
        <div><label>厚度 mm</label><input name="thickness" type="number" step="0.01" min="0.01" value="{html.escape(thickness.strip())}" readonly required></div>
        <div><label>出库块数</label><input name="quantity" type="number" min="1" value="1" required></div>
        <div><label>指定库位，可选</label><input name="location" value="{html.escape(location.strip())}" list="raw-out-location-options" placeholder="不填则所有库位FIFO"><datalist id="raw-out-location-options">{location_candidates}</datalist></div>
        <div><label>操作人</label><input name="operator_name" placeholder="例如 张三"></div>
        <div><label>备注</label><input name="remark" placeholder="例如 生产领料"></div>
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
            f"{record_remark}；FIFO扣减批次 {item.material_code or item.id}，数量 {outbound_quantity}",
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
        f"板料按规格出库：{material.strip()} {length:g}×{width:g}×{thickness:g}mm，数量 {quantity}；批次扣减 {'，'.join(affected_batches)}",
    )
    db.commit()
    return RedirectResponse("/admin/raw-plates", status_code=303)


@router.get("/admin/raw-plates/transactions", response_class=HTMLResponse)
def raw_plate_transactions_page(material: str = "", transaction_type: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
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
    for record in records:
        item = inventory_map.get(record.inventory_id)
        if not item or item.inventory_type != "raw_plate":
            continue
        if material.strip() and item.material != material.strip():
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
        rows += f"<tr><td>{item.material_code or '-'}</td><td>{item.material}</td><td>{item.usable_size or '-'}</td><td>{item.location or '-'}</td><td>{transaction_label(record.transaction_type)}</td><td>{record.quantity}</td><td>{record.before_quantity}</td><td>{record.after_quantity}</td><td>{record.operator_name or '-'}</td><td>{record.remark or '-'}</td><td>{record.created_at}</td><td>{reverse_form}</td></tr>"
    material_options = select_options(inventory_distinct_options(db, "raw_plate", "material"), material, "全部材质")
    type_options = "".join(
        f"<option value='{value}' {'selected' if transaction_type == value else ''}>{label}</option>"
        for value, label in (("", "全部类型"), ("in", "入库"), ("out", "出库"), ("confirm", "确认"))
    )
    body = f"""
    <div class="top"><div><h1>板料流水</h1><p class="muted">查看原料板料的入库、出库流水和计算备注。</p></div><div class="actions"><a class="btn secondary" href="/admin/raw-plates">返回板料库存</a><a class="btn secondary" href="/admin/raw-plates/inbound">板料入库</a><a class="btn secondary" href="/admin/raw-plates/outbound">板料出库</a><a class="btn secondary" href="/admin/exports/raw_plate_transactions">导出Excel</a></div></div>
    <section class="card">
      <form method="get" action="/admin/raw-plates/transactions" class="actions" style="justify-content:flex-start">
        <select name="material" style="width:150px">{material_options}</select>
        <select name="transaction_type" style="width:130px">{type_options}</select>
        <button class="btn" type="submit">查询流水</button>
        <a class="btn secondary" href="/admin/raw-plates/transactions">清空</a>
      </form>
    </section>
    <section class="card"><table><thead><tr><th>批次编号</th><th>材质</th><th>尺寸</th><th>库位</th><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>操作人</th><th>备注</th><th>时间</th><th>操作</th></tr></thead><tbody>{rows or "<tr><td colspan='12'>暂无板料流水。</td></tr>"}</tbody></table></section>
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
    <div class="top"><div><h1>产品入库</h1><p class="muted">选择已确认图纸对应的产品型号，填写入库数量和库位。</p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">返回库存查询</a></div></div>
    <section class="card">
      <form method="post" action="/admin/inventory" class="form-grid">
        <input type="hidden" name="client_request_id" value="{client_request_id}">
        <div><label>选择产品型号</label><select name="drawing_id" required>{drawing_options}</select></div>
        <div><label>数量</label><input name="quantity" type="number" value="1" min="1" required></div>
        <div><label>库位</label><input name="location" list="product-in-location-options" placeholder="例如 A-01"><datalist id="product-in-location-options">{location_candidates}</datalist></div>
        <div><label>操作人</label><input name="operator_name" placeholder="例如 张三"></div>
        <div style="align-self:end"><button class="btn" type="submit">确认入库</button></div>
      </form>
    </section>
    """
    return page("产品入库", body)


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
                "latest": item.updated_at or item.created_at,
            }
        grouped[code]["quantity"] += item.quantity
        if item.location:
            grouped[code]["locations"].add(item.location)
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
        f"<option value='{drawing_map[code].id}'>{html.escape(code)}｜V{drawing_map[code].version or 1}｜{group['material']}｜厚度 {group['thickness']}｜库存 {group['quantity']}｜库位 {' / '.join(sorted(group['locations'])) or '-'}</option>"
        for code, group in grouped.items()
        if code in drawing_map
    ) or "<option value='' disabled selected>暂无可出库产品库存</option>"
    rows = "".join(
        f"""
        <tr>
          <td>{html.escape(str(group['code']))}</td><td>{group['material']}</td><td>{group['thickness']}</td><td><strong>{group['quantity']}</strong></td><td>{' / '.join(sorted(group['locations'])) or '-'}</td><td>{group['latest'] or '-'}</td><td><a class='btn secondary' href='/admin/inventory/product/{quote(str(group['code']), safe="")}'>查看明细</a></td>
        </tr>
        """
        for group in grouped.values()
    )
    product_codes = inventory_distinct_options(db, "product", "material_code", quantity_positive=True)
    source_codes = inventory_distinct_options(db, "product", "source_product_code", quantity_positive=True)
    product_code_options = select_options(product_codes + source_codes, keyword, "全部型号")
    material_options = select_options(inventory_distinct_options(db, "product", "material", quantity_positive=True), material, "全部材质")
    thickness_options = select_options(inventory_distinct_options(db, "product", "thickness", quantity_positive=True), thickness, "全部厚度")
    location_options = select_options(inventory_distinct_options(db, "product", "location", quantity_positive=True), location, "全部库位")
    location_candidates = datalist_options(inventory_distinct_options(db, "product", "location", quantity_positive=True))
    body = f"""
    <div class="top"><div><h1>产品出库</h1><p class="muted">在本页查看当前产品库存，并按产品型号填写出库数量；库位不填时按所有库位先进先出扣减。</p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">返回库存查询</a></div></div>
    <section class="card">
      <form method="get" action="/admin/inventory/outbound" class="actions" style="justify-content:flex-start;margin-bottom:14px">
        <select name="q" style="width:220px">{product_code_options}</select>
        <select name="material" style="width:150px">{material_options}</select>
        <select name="thickness" style="width:130px">{thickness_options}</select>
        <select name="location" style="width:150px">{location_options}</select>
        <button class="btn secondary" type="submit">筛选</button>
        <a class="btn secondary" href="/admin/inventory/outbound">清空</a>
      </form>
      <form method="post" action="/admin/inventory/product/out" class="form-grid">
        <input type="hidden" name="client_request_id" value="{client_request_id}">
        <div><label>选择产品型号</label><select name="drawing_id" required>{drawing_options}</select></div>
        <div><label>出库数量</label><input name="quantity" type="number" value="1" min="1" required></div>
        <div><label>指定库位，可选</label><input name="location" value="{html.escape(location.strip())}" list="product-out-location-options" placeholder="不填则所有库位FIFO"><datalist id="product-out-location-options">{location_candidates}</datalist></div>
        <div><label>操作人</label><input name="operator_name" placeholder="例如 张三"></div>
        <div><label>备注</label><input name="remark" placeholder="例如 发货/领用"></div>
        <div style="align-self:end"><button class="btn" type="submit">确认出库</button></div>
      </form>
    </section>
    <section class="card"><h2>当前可出库产品库存</h2><table><thead><tr><th>产品编号</th><th>材质</th><th>厚度</th><th>可出库数量</th><th>库位</th><th>最近更新时间</th><th>操作</th></tr></thead><tbody>{rows or "<tr><td colspan='7'>暂无可出库产品库存。</td></tr>"}</tbody></table></section>
    """
    return page("产品出库", body)


@router.post("/admin/inventory")
def create_inventory_from_page(
    drawing_id: int = Form(...),
    quantity: int = Form(1),
    location: str = Form(""),
    operator_name: str = Form(""),
    client_request_id: str = Form(""),
    _lock=Depends(locked_inventory_write),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    idempotency_key = f"admin_product_inbound:{client_request_id.strip()}" if client_request_id.strip() else None
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing or drawing.confirmed != 1 or drawing.is_active != 1:
        raise HTTPException(status_code=404, detail="已确认图纸不存在")
    result = product_inbound_from_drawing(
        drawing=drawing,
        quantity=quantity,
        location=location,
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
                remark=remark or "产品出库",
            )
        )
    record_operation_log(
        db,
        "product_outbound",
        "inventory",
        affected_items[0][0].id if affected_items else None,
        operator_name or None,
        remark or f"产品出库：{drawing.product_code}，数量 {quantity}",
        before_data={"quantity": before_total_quantity, "location": location_value or None, "drawing": drawing_snapshot(drawing)},
        after_data={"quantity": after_total_quantity, "location": location_value or None},
    )
    db.commit()
    return RedirectResponse("/admin/inventory", status_code=303)


@router.get("/admin/inventory/product/{product_code}", response_class=HTMLResponse)
def inventory_product_detail_page(product_code: str, db: Session = Depends(get_db)) -> HTMLResponse:
    items = (
        db.query(MaterialInventory)
        .filter(MaterialInventory.inventory_type == "product", MaterialInventory.material_code == product_code)
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
          <td>{item.material}</td>
          <td>{item.thickness}</td>
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
          <td>{record.operator_name or '-'}</td>
          <td>{record.remark or '-'}</td>
          <td>{record.created_at}</td>
        </tr>
        """
        for record in records
    )
    body = f"""
    <div class="top"><div><h1>库存明细：{html.escape(product_code)}</h1><p class="muted">当前总数量：<strong>{total_quantity}</strong></p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">返回库存汇总</a></div></div>
    <section class="card"><h2>入库批次</h2><table><thead><tr><th>产品型号</th><th>数量</th><th>库位</th><th>材质</th><th>厚度</th><th>状态</th><th>创建时间</th><th>更新时间</th></tr></thead><tbody>{rows or "<tr><td colspan='8'>暂无该产品库存。</td></tr>"}</tbody></table></section>
    <section class="card"><h2>产品流水</h2><table><thead><tr><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>关联库位</th><th>操作人</th><th>备注</th><th>时间</th></tr></thead><tbody>{transaction_rows or "<tr><td colspan='8'>暂无该产品流水。</td></tr>"}</tbody></table></section>
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
        f"<tr><td>{transaction_label(r.transaction_type)}</td><td>{r.quantity}</td><td>{r.before_quantity}</td><td>{r.after_quantity}</td><td>{r.operator_name or '-'}</td><td>{r.remark or '-'}</td><td>{r.created_at}</td></tr>"
        for r in records
    )
    display_code = item.material_code or item.source_product_code or "-"
    body = f"""
    <div class="top"><div><h1>库存详情：{display_code}</h1><p class="muted">查看该型号库存的基础信息和全部出入库流水。</p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">返回库存管理</a></div></div>
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
        <div><span class="muted">状态</span><strong>{item.status}</strong></div>
        <div><span class="muted">来源产品</span><strong>{item.source_product_code or '-'}</strong></div>
        <div><span class="muted">来源图纸</span><strong>{drawing_version_label(db, item.source_drawing_id)}</strong></div>
        <div><span class="muted">可用尺寸</span><strong>{item.usable_size or '-'}</strong></div>
      </div>
    </section>
    <section class="card"><h2>该库存流水</h2><table><thead><tr><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>操作人</th><th>备注</th><th>时间</th></tr></thead><tbody>{rows or "<tr><td colspan='7'>暂无该库存流水。</td></tr>"}</tbody></table></section>
    """
    return page("库存详情", body)


def outbound_report_range(period: str, start_date: str, end_date: str) -> tuple[datetime, datetime, str]:
    now = datetime.now()
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
    grouped: dict[str, dict[str, object]] = {}
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
        if key not in grouped:
            grouped[key] = {"code": key, "name": name, "spec": spec, "location": location, "quantity": 0}
        grouped[key]["quantity"] = int(grouped[key]["quantity"]) + record.quantity
        total_quantity += record.quantity
    rows = "".join(
        f"<tr><td>{html.escape(str(item['code']))}</td><td>{html.escape(str(item['name']))}</td><td>{html.escape(str(item['spec']))}</td><td>{html.escape(str(item['location']))}</td><td>{item['quantity']}</td></tr>"
        for item in sorted(grouped.values(), key=lambda value: str(value["code"]))
    )
    return rows, total_quantity


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
    <div class="top"><div><h1>出库统计</h1><p class="muted">查询天、月、年或某个时间段内的产品、余料和板料出库情况。当前范围：{html.escape(range_label)}</p></div><div class="actions"><a class="btn secondary" href="/admin/inventory/transactions">库存流水</a><a class="btn secondary" href="/admin/scraps/transactions">余料流水</a><a class="btn secondary" href="/admin/raw-plates/transactions">板料流水</a><a class="btn secondary" href="{export_link('outbound_report', {'period': period, 'start_date': start_date.strip(), 'end_date': end_date.strip()})}">导出Excel</a></div></div>
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
      <div class="card stat"><span class="muted">产品出库总数</span><strong>{product_total}</strong></div>
      <div class="card stat"><span class="muted">余料出库总数</span><strong>{scrap_total}</strong></div>
      <div class="card stat"><span class="muted">板料出库总块数</span><strong>{raw_plate_total}</strong></div>
    </section>
    <section class="card"><h2>产品出库明细汇总</h2><table><thead><tr><th>产品型号/来源</th><th>材质</th><th>规格</th><th>库位</th><th>出库数量</th></tr></thead><tbody>{product_rows or "<tr><td colspan='5'>该时间段暂无产品出库。</td></tr>"}</tbody></table></section>
    <section class="card"><h2>余料出库明细汇总</h2><table><thead><tr><th>材质</th><th>可用尺寸</th><th>规格</th><th>库位</th><th>出库数量</th></tr></thead><tbody>{scrap_rows or "<tr><td colspan='5'>该时间段暂无余料出库。</td></tr>"}</tbody></table></section>
    <section class="card"><h2>板料出库明细汇总</h2><table><thead><tr><th>材质</th><th>板料规格</th><th>尺寸</th><th>库位</th><th>出库块数</th></tr></thead><tbody>{raw_plate_rows or "<tr><td colspan='5'>该时间段暂无板料出库。</td></tr>"}</tbody></table></section>
    """
    return page("出库统计", body)


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
        rows += f"<tr><td>{product_link}</td><td>{transaction_label(r.transaction_type)}</td><td>{quantity_label}</td><td>{before_quantity}</td><td>{after_quantity}</td><td>{r.operator_name or '-'}</td><td>{r.remark or '-'}</td><td>{r.created_at}</td><td>{reverse_action}</td></tr>"
    product_codes = inventory_distinct_options(db, "product", "material_code") + inventory_distinct_options(db, "product", "source_product_code")
    product_options = select_options(product_codes, product_filter, "全部型号")
    type_options = "".join(
        f"<option value='{value}' {'selected' if transaction_type == value else ''}>{label}</option>"
        for value, label in (("", "全部类型"), ("in", "入库"), ("out", "出库"), ("confirm", "确认"))
    )
    body = f"""
    <div class="top"><div><h1>库存流水</h1><p class="muted">只查看产品库存的入库/出库记录；余料记录请到余料流水查看。</p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">返回库存管理</a><a class="btn secondary" href="/admin/scraps/transactions">余料流水</a><a class="btn secondary" href="/admin/exports/product_transactions">导出Excel</a></div></div>
    <section class="card">
      <form method="get" action="/admin/inventory/transactions" class="actions" style="justify-content:flex-start">
        <select name="product_code" style="width:220px">{product_options}</select>
        <select name="transaction_type" style="width:130px">{type_options}</select>
        <button class="btn" type="submit">查询流水</button>
        <a class="btn secondary" href="/admin/inventory/transactions">清空</a>
      </form>
    </section>
    <section class="card"><table><thead><tr><th>产品型号/来源</th><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>操作人</th><th>备注</th><th>时间</th><th>操作</th></tr></thead><tbody>{rows or "<tr><td colspan='9'>暂无库存流水。</td></tr>"}</tbody></table></section>
    """
    return page("库存流水", body)


@router.get("/admin/scraps/transactions", response_class=HTMLResponse)
def scrap_transactions_page(material: str = "", transaction_type: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
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
        if not item or item.inventory_type != "scrap":
            continue
        if material.strip() and item.material != material.strip():
            continue
        before_quantity = "-" if r.transaction_type == "confirm" else r.before_quantity
        after_quantity = "-" if r.transaction_type == "confirm" else r.after_quantity
        quantity_label = r.after_quantity if r.transaction_type == "confirm" and r.quantity == 0 else r.quantity
        reverse_action = "-" if r.transaction_type not in ("in", "out") or r.reversed_transaction_id else f"<form method='post' action='/admin/scraps/transactions/{r.id}/reverse' class='actions' style='margin:0;justify-content:flex-start' onsubmit=\"return confirm('确定撤销这条余料流水吗？系统会生成一条反向流水，不会删除原记录。')\"><input name='operator_name' placeholder='操作人' style='width:80px'><input name='remark' placeholder='撤销原因' required style='width:120px'><button class='btn secondary' type='submit'>撤销</button></form>"
        rows += f"<tr><td>{item.material}</td><td>{item.thickness}</td><td>{item.usable_size or '-'}</td><td>{scrap_location_label(item)}</td><td>{transaction_label(r.transaction_type)}</td><td>{quantity_label}</td><td>{before_quantity}</td><td>{after_quantity}</td><td>{r.operator_name or '-'}</td><td>{r.remark or '-'}</td><td>{r.created_at}</td><td>{reverse_action}</td></tr>"
    material_options = select_options(inventory_distinct_options(db, "scrap", "material"), material, "全部材质")
    type_options = "".join(
        f"<option value='{value}' {'selected' if transaction_type == value else ''}>{label}</option>"
        for value, label in (("", "全部类型"), ("in", "入库"), ("out", "出库"), ("confirm", "确认"))
    )
    body = f"""
    <div class="top"><div><h1>余料流水</h1><p class="muted">查看余料确认入库、出库等流转记录。</p></div><div class="actions"><a class="btn secondary" href="/admin/scraps">返回余料记录</a><a class="btn secondary" href="/admin/inventory/transactions">库存流水</a><a class="btn secondary" href="/admin/exports/scrap_transactions">导出Excel</a></div></div>
    <section class="card">
      <form method="get" action="/admin/scraps/transactions" class="actions" style="justify-content:flex-start">
        <select name="material" style="width:150px">{material_options}</select>
        <select name="transaction_type" style="width:130px">{type_options}</select>
        <button class="btn" type="submit">查询流水</button>
        <a class="btn secondary" href="/admin/scraps/transactions">清空</a>
      </form>
    </section>
    <section class="card"><table><thead><tr><th>材质</th><th>厚度</th><th>可用尺寸</th><th>库位</th><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>操作人</th><th>备注</th><th>时间</th><th>操作</th></tr></thead><tbody>{rows or "<tr><td colspan='12'>暂无余料流水。</td></tr>"}</tbody></table></section>
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
            <form method='post' action='/admin/scraps/{item.id}/confirm' style='display:flex;gap:6px;align-items:center;margin:0'>
              <input name='actual_quantity' type='number' min='0' value='{item.quantity}' style='width:75px'>
              <input name='actual_diameter' type='number' step='0.01' value='{item.diameter or ''}' style='width:90px'>
              <input name='location' value='{'' if item.location in ('待入库', '未入库') else item.location or ''}' list='pending-scrap-location-options' placeholder='库位' style='width:100px' required>
              <input name='operator_name' placeholder='确认人' style='width:90px'>
              <button class='btn secondary' type='submit' style='min-width:96px;white-space:nowrap'>确认入库</button>
            </form>
          </td>
        </tr>
        """
        for item in items
    )
    body = f"""
    <div class="top"><div><h1>待入库余料</h1><p class="muted">产品入库后自动生成的中心余料先进入待确认，测量实际尺寸和库位后再变为可用。</p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">库存管理</a><a class="btn secondary" href="/admin/scraps">余料记录</a></div></div>
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
    drawing_required_diameter = None
    drawing_required_thickness = None
    if selected_drawing:
        drawing_required_diameter = parse_diameter_text(selected_drawing.expected_scrap_size) or selected_drawing.min_inner_diameter or selected_drawing.max_outer_diameter
        drawing_required_thickness = selected_drawing.plate_thickness or selected_drawing.product_thickness or selected_drawing.thickness
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
                if (
                    (not selected_drawing.material or selected_drawing.material.replace(" ", "") in item.material.replace(" ", "") or item.material.replace(" ", "") in selected_drawing.material.replace(" ", ""))
                    and (drawing_required_thickness is None or abs(item.thickness - drawing_required_thickness) <= 0.05)
                    and (drawing_required_diameter is None or (item.diameter is not None and item.diameter >= drawing_required_diameter + 2.0))
                )
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
    material_options = select_options(inventory_distinct_options(db, "scrap", "material", quantity_positive=True, status="available"), material, "全部材质")
    thickness_options = select_options(inventory_distinct_options(db, "scrap", "thickness", quantity_positive=True, status="available"), thickness, "全部厚度")
    diameter_options = select_options(inventory_distinct_options(db, "scrap", "diameter", quantity_positive=True, status="available"), required_diameter, "全部直径")
    location_options = select_options(inventory_distinct_options(db, "scrap", "location", quantity_positive=True, status="available"), location, "全部库位")
    body = f"""
    <div class="top"><div><h1>余料出库</h1><p class="muted">先查询可用余料，再按规格和库位汇总出库。</p></div><div class="actions"><a class="btn secondary" href="/admin/scraps">返回余料查询</a></div></div>
    <section class="card">
      <form method="get" action="/admin/scraps/outbound" class="actions" style="justify-content:flex-start">
        <select name="drawing_id">{confirmed_drawing_options(db, selected_id=int(drawing_id) if drawing_id.isdigit() else None, include_blank=True)}</select>
        <select name="material" style="width:150px">{material_options}</select>
        <select name="thickness" style="width:130px">{thickness_options}</select>
        <select name="required_diameter" style="width:140px">{diameter_options}</select>
        <select name="location" style="width:150px">{location_options}</select>
        <button class="btn" type="submit">查询可出库余料</button>
        <a class="btn secondary" href="/admin/scraps/outbound">清空</a>
      </form>
    </section>
    {f'<section class="card"><strong>当前按图纸匹配：</strong>{selected_drawing.product_code or "-"}，需要直径 ≥ {(drawing_required_diameter + 2.0):g}，厚度 {drawing_required_thickness or "-"}，材质 {selected_drawing.material or "-"}</section>' if selected_drawing and drawing_required_diameter is not None else ''}
    <section class="card">
      <form method="post" action="/admin/scraps/outbound" class="form-grid">
        <input type="hidden" name="client_request_id" value="{client_request_id}">
        <div><label>选择余料规格</label><select name="scrap_group_key" required>{options}</select></div>
        <div><label>出库数量</label><input name="quantity" type="number" value="1" min="1" required></div>
        <div><label>操作人</label><input name="operator_name" placeholder="例如 张三"></div>
        <div><label>备注</label><input name="remark" placeholder="例如 生产领用/报废"></div>
        <div style="align-self:end"><button class="btn" type="submit">确认出库</button></div>
      </form>
    </section>
    """
    return page("余料出库", body)


@router.post("/admin/scraps/outbound")
def outbound_scrap_from_page(
    scrap_group_key: str = Form(...),
    quantity: int = Form(...),
    operator_name: str = Form(""),
    remark: str = Form(""),
    client_request_id: str = Form(""),
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
    batches = find_scrap_batches_for_outbound(scrap_group_key, db)
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
                remark=remark or "余料出库",
            )
        )
    record_operation_log(
        db,
        "scrap_outbound",
        "inventory",
        affected_items[0][0].id if affected_items else None,
        operator_name or None,
        remark or f"余料出库：{material_value}，数量 {quantity}",
        before_data={"quantity": before_quantity, "scrap_group_key": scrap_group_key},
        after_data={"quantity": after_quantity},
    )
    db.commit()
    return RedirectResponse("/admin/scraps", status_code=303)


@router.get("/admin/drawings", response_class=HTMLResponse)
def drawings_page(q: str = "", material: str = "", thickness: str = "", confirmed: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    query = db.query(ProductDrawing)
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            (ProductDrawing.product_code.ilike(like))
            | (ProductDrawing.product_name.ilike(like))
        )
    if material.strip():
        query = query.filter(ProductDrawing.material.ilike(f"%{material.strip()}%"))
    thickness_value = optional_float(thickness)
    if thickness_value is not None:
        query = query.filter(
            (ProductDrawing.thickness == thickness_value)
            | (ProductDrawing.product_thickness == thickness_value)
            | (ProductDrawing.plate_thickness == thickness_value)
        )
    if confirmed in ("0", "1"):
        query = query.filter(ProductDrawing.confirmed == int(confirmed))
    drawings = query.order_by(ProductDrawing.created_at.desc()).all()
    rows = drawing_rows(drawings)
    product_code_options = select_options(drawing_distinct_options(db, "product_code", confirmed_only=False), keyword, "全部型号")
    material_options = select_options(drawing_distinct_options(db, "material", confirmed_only=False), material, "全部材质")
    thickness_options = select_options(
        drawing_distinct_options(db, "plate_thickness", confirmed_only=False)
        + drawing_distinct_options(db, "product_thickness", confirmed_only=False)
        + drawing_distinct_options(db, "thickness", confirmed_only=False),
        thickness,
        "全部厚度",
    )
    confirmed_options = "".join(
        f"<option value='{value}' {'selected' if confirmed == value else ''}>{label}</option>"
        for value, label in (("", "全部状态"), ("1", "已确认"), ("0", "待确认"))
    )
    body = f"""
    <div class="top"><div><h1>图纸识别</h1><p class="muted">上传DXF文件，自动识别产品用料信息。</p></div><div class="actions"><a class="btn secondary" href="/admin/drawings/pending">待确认图纸</a><a class="btn secondary" href="/admin/drawings/confirmed">已确认图纸</a></div></div>
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
        <select name="q" style="width:220px">{product_code_options}</select>
        <select name="material" style="width:150px">{material_options}</select>
        <select name="thickness" style="width:130px">{thickness_options}</select>
        <select name="confirmed" style="width:140px">{confirmed_options}</select>
        <button class="btn" type="submit">搜索图纸</button>
        <a class="btn secondary" href="/admin/drawings">清空</a>
      </form>
    </section>
    <section class="card"><h2>图纸记录</h2><table><thead><tr><th>产品编号</th><th>版本</th><th>版本状态</th><th>产品名称</th><th>材质</th><th>厚度</th><th>最大外径</th><th>确认状态</th><th>操作</th></tr></thead><tbody>{rows}</tbody></table></section>
    """
    return page("图纸识别", body)


def drawing_rows(drawings: list[ProductDrawing], show_id: bool = True) -> str:
    rows = "".join(
        f"<tr><td>{d.product_code or '-'}</td><td>V{d.version or 1}</td><td>{'当前' if d.is_active else '历史'}</td><td>{d.product_name or '-'}</td><td>{d.material or '-'}</td><td>{d.thickness or '-'}</td><td>{d.max_outer_diameter or '-'}</td><td>{'已确认' if d.confirmed else '待确认'}</td><td><a class='btn secondary' href='/admin/drawings/{d.id}'>查看</a></td></tr>"
        for d in drawings
    )
    return rows or "<tr><td colspan='9'>暂无图纸记录。</td></tr>"


@router.get("/admin/drawings/confirmed", response_class=HTMLResponse)
def confirmed_drawings_page(q: str = "", material: str = "", thickness: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    query = db.query(ProductDrawing).filter(ProductDrawing.confirmed == 1, ProductDrawing.is_active == 1)
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            (ProductDrawing.product_code.ilike(like))
            | (ProductDrawing.product_name.ilike(like))
        )
    if material.strip():
        query = query.filter(ProductDrawing.material.ilike(f"%{material.strip()}%"))
    thickness_value = optional_float(thickness)
    if thickness_value is not None:
        query = query.filter(
            (ProductDrawing.thickness == thickness_value)
            | (ProductDrawing.product_thickness == thickness_value)
            | (ProductDrawing.plate_thickness == thickness_value)
        )
    drawings = query.order_by(ProductDrawing.updated_at.desc()).all()
    product_code_options = select_options(drawing_distinct_options(db, "product_code"), keyword, "全部型号")
    material_options = select_options(drawing_distinct_options(db, "material"), material, "全部材质")
    thickness_options = select_options(
        drawing_distinct_options(db, "plate_thickness") + drawing_distinct_options(db, "product_thickness") + drawing_distinct_options(db, "thickness"),
        thickness,
        "全部厚度",
    )
    body = f"""
    <div class="top"><div><h1>已确认图纸</h1><p class="muted">这些图纸已经人工确认，可直接用于产品入库。</p></div><div class="actions"><a class="btn secondary" href="/admin/drawings">全部图纸</a><a class="btn secondary" href="/admin/drawings/pending">待确认图纸</a></div></div>
    <section class="card">
      <form method="get" action="/admin/drawings/confirmed" class="actions" style="justify-content:flex-start;margin-bottom:14px">
        <select name="q" style="width:220px">{product_code_options}</select>
        <select name="material" style="width:150px">{material_options}</select>
        <select name="thickness" style="width:130px">{thickness_options}</select>
        <button class="btn" type="submit">搜索</button>
        <a class="btn secondary" href="/admin/drawings/confirmed">清空</a>
      </form>
      <table><thead><tr><th>产品编号</th><th>版本</th><th>版本状态</th><th>产品名称</th><th>材质</th><th>厚度</th><th>最大外径</th><th>确认状态</th><th>操作</th></tr></thead><tbody>{drawing_rows(drawings, show_id=False)}</tbody></table>
    </section>
    """
    return page("已确认图纸", body)


@router.get("/admin/drawings/pending", response_class=HTMLResponse)
def pending_drawings_page(db: Session = Depends(get_db)) -> HTMLResponse:
    drawings = db.query(ProductDrawing).filter(ProductDrawing.confirmed == 0).order_by(ProductDrawing.created_at.desc()).all()
    body = f"""
    <div class="top"><div><h1>待确认图纸</h1><p class="muted">这些图纸需要人工检查并保存确认结果。</p></div><div class="actions"><a class="btn secondary" href="/admin/drawings">全部图纸</a><a class="btn secondary" href="/admin/drawings/confirmed">已确认图纸</a></div></div>
    <section class="card"><table><thead><tr><th>产品编号</th><th>版本</th><th>版本状态</th><th>产品名称</th><th>材质</th><th>厚度</th><th>最大外径</th><th>确认状态</th><th>操作</th></tr></thead><tbody>{drawing_rows(drawings)}</tbody></table></section>
    """
    return page("待确认图纸", body)


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
    parse_json_text = html.escape(json.dumps(drawing.parse_result_json or {}, ensure_ascii=True, default=str, indent=2), quote=False)
    body = f"""
    <div class="top">
      <div><h1>图纸详情</h1><p class="muted">请人工确认识别结果，确认后可进行产品入库。</p></div>
      <div class="actions">
        <a class="btn secondary" href="/admin/drawings">返回列表</a>
        <a class="btn secondary" href="/admin/drawings/{drawing.id}/preview" target="_blank">查看图纸预览</a>
        <form method="post" action="/admin/drawings/{drawing.id}/rerun" style="margin:0">
          <button class="btn secondary" type="submit">重新识别当前图纸</button>
        </form>
        <form method="post" action="/admin/drawings/{drawing.id}/delete" style="margin:0" onsubmit="return confirm('确定删除这张图纸吗？删除后如需更新可以重新上传。')">
          <button class="btn secondary" type="submit">删除图纸</button>
        </form>
        <a class="btn secondary" href="/admin/inventory/inbound">产品入库</a>
      </div>
    </div>
    {notice_html}
    <section class="card">
      <h2>人工确认识别结果</h2>
      <form method="post" action="/admin/drawings/{drawing.id}/confirm" class="form-grid">
        <div><label>产品型号</label><input name="product_code" value="{safe_value(drawing.product_code)}"></div>
        <div><label>产品名称</label><input name="product_name" value="{safe_value(drawing.product_name)}"></div>
        <div><label>材质</label><input name="material" value="{safe_value(drawing.material)}" placeholder="例如 50#"></div>
        <div><label>外径</label><input name="max_outer_diameter" type="number" step="0.01" value="{safe_value(drawing.max_outer_diameter)}" placeholder="mm"></div>
        <div><label>内径</label><input name="min_inner_diameter" type="number" step="0.01" value="{safe_value(drawing.min_inner_diameter)}" placeholder="mm"></div>
        <div><label>产品厚度</label><input name="product_thickness" type="number" step="0.001" value="{safe_value(drawing.product_thickness)}" placeholder="含复合材料总厚"></div>
        <div><label>钢板厚度</label><input name="plate_thickness" type="number" step="0.001" value="{safe_value(drawing.plate_thickness)}" placeholder="基板厚度"></div>
        <div><label>齿数 z</label><input name="teeth_count" type="number" value="{safe_value(drawing.teeth_count)}"></div>
        <div><label>模数 m</label><input name="module" type="number" step="0.001" value="{safe_value(drawing.module)}"></div>
        <div><label>压力角 α</label><input name="pressure_angle" type="number" step="0.01" value="{safe_value(drawing.pressure_angle)}" placeholder="常见20°"></div>
        <div><label>变位系数 x</label><input name="profile_shift_coefficient" type="number" step="0.001" value="{safe_value(drawing.profile_shift_coefficient)}"></div>
        <div><label>公法线长度 L</label><input name="common_normal_length" type="number" step="0.001" value="{safe_value(drawing.common_normal_length)}" placeholder="mm"></div>
        <div><label>跨齿数 n</label><input name="span_teeth_count" type="number" value="{safe_value(drawing.span_teeth_count)}"></div>
        <div><label>量棒直径 dp</label><input name="pin_diameter" type="number" step="0.001" value="{safe_value(drawing.pin_diameter)}" placeholder="mm"></div>
        <div><label>棒间距 M</label><input name="pin_span" type="number" step="0.001" value="{safe_value(drawing.pin_span)}" placeholder="mm"></div>
        <div><label>中心余料尺寸</label><input name="expected_scrap_size" value="{safe_value(drawing.expected_scrap_size)}" placeholder="中间割下来的圆料，例如 φ77.5"></div>
        <div style="align-self:end"><button class="btn" type="submit">保存确认结果</button></div>
      </form>
    </section>
    <section class="card"><h2>原始解析JSON</h2><pre>{parse_json_text}</pre></section>
    """
    return page("图纸详情", body)


@router.get("/admin/drawings/{drawing_id}/preview", response_class=HTMLResponse)
def drawing_preview_page(drawing_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    try:
        svg = render_dxf_svg(drawing.dxf_file_url)
    except Exception as exc:
        svg = f"<p>图纸预览生成失败：{html.escape(str(exc))}</p>"
    body = f"""
    <div class="top">
      <div><h1>图纸预览</h1><p class="muted">产品型号：{drawing.product_code or '-'}　版本：V{drawing.version or 1}</p></div>
      <div class="actions"><a class="btn secondary" href="/admin/drawings/{drawing.id}">返回详情</a></div>
    </div>
    <section class="card">
      <p class="muted">这是浏览器粗略预览，不等同于CAD最终显示；用于快速查看图纸形状、文字和尺寸位置。</p>
      {svg}
    </section>
    """
    return page("图纸预览", body)


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
    material: str = Form(""),
    max_outer_diameter: str = Form(""),
    min_inner_diameter: str = Form(""),
    product_thickness: str = Form(""),
    plate_thickness: str = Form(""),
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
    ensure_drawing_can_be_changed(drawing, db)
    before_data = drawing_snapshot(drawing)
    max_outer_diameter_value = optional_float(max_outer_diameter)
    min_inner_diameter_value = optional_float(min_inner_diameter)
    product_thickness_value = optional_float(product_thickness)
    plate_thickness_value = optional_float(plate_thickness)
    teeth_count_value = optional_int(teeth_count)
    module_value = optional_float(module)
    pressure_angle_value = optional_float(pressure_angle)
    profile_shift_coefficient_value = optional_float(profile_shift_coefficient)
    span_teeth_count_value = optional_int(span_teeth_count)
    common_normal_length_value = optional_float(common_normal_length)
    pin_diameter_value = optional_float(pin_diameter)
    pin_span_value = optional_float(pin_span)
    drawing.product_code = product_code or None
    drawing.product_name = product_name or None
    drawing.material = material or None
    drawing.thickness = product_thickness_value or plate_thickness_value
    drawing.max_outer_diameter = max_outer_diameter_value
    drawing.min_inner_diameter = min_inner_diameter_value
    drawing.product_thickness = product_thickness_value
    drawing.plate_thickness = plate_thickness_value
    drawing.teeth_count = teeth_count_value
    drawing.module = module_value
    drawing.pressure_angle = pressure_angle_value
    drawing.profile_shift_coefficient = profile_shift_coefficient_value
    drawing.span_teeth_count = span_teeth_count_value
    drawing.common_normal_length = common_normal_length_value
    drawing.pin_diameter = pin_diameter_value
    drawing.pin_span = pin_span_value
    drawing.expected_scrap_size = expected_scrap_size or None
    drawing.confirmed = 1
    apply_drawing_version(drawing, db)
    record_operation_log(
        db,
        "drawing_confirm",
        "drawing",
        drawing.id,
        None,
        "确认图纸",
        before_data=before_data,
        after_data=drawing_snapshot(drawing),
    )
    db.commit()
    return RedirectResponse(f"/admin/drawings/{drawing.id}", status_code=303)


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
    drawing_required_diameter = None
    drawing_required_thickness = None
    if selected_drawing:
        drawing_required_diameter = parse_diameter_text(selected_drawing.expected_scrap_size) or selected_drawing.min_inner_diameter or selected_drawing.max_outer_diameter
        drawing_required_thickness = selected_drawing.plate_thickness or selected_drawing.product_thickness or selected_drawing.thickness
    for record in records:
        item = scrap_map.get(record.scrap_inventory_id)
        if source_product_code.strip() and source_product_code.strip() not in (record.source_product_code or ""):
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
            if selected_drawing.material and selected_drawing.material.replace(" ", "") not in item.material.replace(" ", "") and item.material.replace(" ", "") not in selected_drawing.material.replace(" ", ""):
                continue
            if drawing_required_thickness is not None and abs(item.thickness - drawing_required_thickness) > 0.05:
                continue
            if drawing_required_diameter is not None and (item.diameter is None or item.diameter < drawing_required_diameter + 2.0):
                continue
        if item and location.strip() and location.strip() not in (item.location or ""):
            continue
        filtered_records.append(record)
    spec_grouped = {}
    for record in filtered_records:
        item = scrap_map.get(record.scrap_inventory_id)
        if item:
            location_label = scrap_location_label(item)
            spec_key = (item.material, item.thickness, item.usable_size or "-", location_label)
            if spec_key not in spec_grouped:
                spec_grouped[spec_key] = {"material": item.material, "thickness": item.thickness, "usable_size": item.usable_size or "-", "location": location_label, "quantity": 0}
            spec_grouped[spec_key]["quantity"] += item.quantity
    spec_rows = "".join(
        f"<tr><td>{group['material']}</td><td>{group['thickness']}</td><td>{group['usable_size']}</td><td>{group['location']}</td><td><strong>{group['quantity']}</strong></td></tr>"
        for group in spec_grouped.values()
    )
    rows = "".join(
        f"<tr><td>{r.source_product_code or '-'}</td><td>{drawing_version_label(db, r.source_drawing_id)}</td><td>{scrap_map.get(r.scrap_inventory_id).quantity if scrap_map.get(r.scrap_inventory_id) else '-'}</td><td>{scrap_map.get(r.scrap_inventory_id).status if scrap_map.get(r.scrap_inventory_id) else '-'}</td><td>{scrap_location_label(scrap_map.get(r.scrap_inventory_id))}</td><td>{scrap_map.get(r.scrap_inventory_id).usable_size if scrap_map.get(r.scrap_inventory_id) else '-'}</td><td>{r.theoretical_size or '-'}</td><td>{r.actual_size or '-'}</td><td>{r.operator_name or '-'}</td><td>{r.registered_at}</td></tr>"
        for r in filtered_records
    )
    source_product_options = select_options([record.source_product_code for record in records], source_product_code, "全部来源产品")
    material_options = select_options(inventory_distinct_options(db, "scrap", "material", quantity_positive=True, status="available"), material, "全部材质")
    thickness_options = select_options(inventory_distinct_options(db, "scrap", "thickness", quantity_positive=True, status="available"), thickness, "全部厚度")
    diameter_options = select_options(inventory_distinct_options(db, "scrap", "diameter", quantity_positive=True, status="available"), required_diameter, "全部直径")
    location_options = select_options(inventory_distinct_options(db, "scrap", "location", quantity_positive=True, status="available"), location, "全部库位")
    body = f"""
    <div class='top'><div><h1>余料记录</h1><p class='muted'>查看切割后生成的新余料来源、数量与尺寸。</p></div><div class="actions"><a class="btn" href="/admin/scraps/outbound">余料出库</a><a class="btn secondary" href="/admin/scraps/transactions">余料流水</a><a class="btn secondary" href="/admin/scraps/pending">待入库余料</a><a class="btn secondary" href="{export_link('scrap_inventory', {'material': material.strip(), 'thickness': thickness.strip(), 'location': location.strip()})}">导出Excel</a></div></div>
    <section class="card">
      <form method="get" action="/admin/scraps" class="actions" style="justify-content:flex-start">
        <select name="source_product_code" style="width:180px">{source_product_options}</select>
        <select name="drawing_id">{confirmed_drawing_options(db, selected_id=int(drawing_id) if drawing_id.isdigit() else None, include_blank=True)}</select>
        <select name="material" style="width:150px">{material_options}</select>
        <select name="thickness" style="width:130px">{thickness_options}</select>
        <select name="required_diameter" style="width:140px">{diameter_options}</select>
        <select name="location" style="width:150px">{location_options}</select>
        <button class="btn" type="submit">搜索余料</button>
        <a class="btn secondary" href="/admin/scraps">清空</a>
      </form>
    </section>
    {f'<section class="card"><strong>当前按图纸匹配：</strong>{selected_drawing.product_code or "-"}，需要直径 ≥ {(drawing_required_diameter + 2.0):g}，厚度 {drawing_required_thickness or "-"}，材质 {selected_drawing.material or "-"}</section>' if selected_drawing and drawing_required_diameter is not None else ''}
    <section class='card'><h2>按规格汇总</h2><table><thead><tr><th>材质</th><th>厚度</th><th>可用尺寸</th><th>库位</th><th>总数量</th></tr></thead><tbody>{spec_rows or "<tr><td colspan='5'>暂无余料。</td></tr>"}</tbody></table></section>
    <section class='card'><h2>余料明细</h2><table><thead><tr><th>来源产品</th><th>来源图纸</th><th>数量</th><th>状态</th><th>库位</th><th>可用尺寸</th><th>理论尺寸</th><th>实际尺寸</th><th>登记人</th><th>时间</th></tr></thead><tbody>{rows or "<tr><td colspan='10'>暂无余料记录。</td></tr>"}</tbody></table></section>
    """
    return page("余料记录", body)
