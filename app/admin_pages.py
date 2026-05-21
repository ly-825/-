import html
import math
from urllib.parse import quote

import ezdxf
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import InventoryTransactionRecord, MaterialInventory, ProductDrawing, ScrapGenerationRecord
from app.routers.drawings import upload_drawing
from app.services.dxf_parser import parse_dxf
from app.services.inventory_service import adjust_inventory_quantity
from app.services.qwen_service import recognize_drawing
from app.services.scrap_service import create_center_scrap_from_drawing

router = APIRouter()


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
        .filter(ProductDrawing.confirmed == 1)
        .order_by(ProductDrawing.product_code.asc())
        .all()
    )
    options = "".join(
        f"<option value='{drawing.id}' {'selected' if selected_id == drawing.id else ''}>{drawing.product_code or '-'}｜{drawing.product_name or '-'}｜{drawing.material or '-'}｜厚度 {drawing.plate_thickness or drawing.product_thickness or drawing.thickness or '-'}</option>"
        for drawing in drawings
    )
    if include_blank:
        options = f"<option value='' {'selected' if selected_id is None else ''}>按图纸自动匹配</option>" + options
    return options or "<option value='' disabled selected>暂无已确认图纸，请先确认图纸</option>"


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
    aside {{ background:#0f1f46; color:white; padding:24px 18px; }}
    .brand {{ font-size:20px; font-weight:800; margin-bottom:26px; }}
    nav a {{ display:block; padding:12px 14px; border-radius:12px; color:rgba(255,255,255,.82); margin-bottom:8px; }}
    nav a:hover {{ background:rgba(255,255,255,.1); color:white; }}
    .nav-group {{ margin:18px 0 8px; padding:0 14px; color:rgba(255,255,255,.48); font-size:12px; font-weight:800; letter-spacing:.08em; }}
    main {{ padding:28px; }}
    .top {{ display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:22px; }}
    h1 {{ margin:0; font-size:28px; }}
    .muted {{ color:var(--muted); }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:20px; padding:20px; box-shadow:0 12px 34px rgba(20,32,55,.06); margin-bottom:18px; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; }}
    .stat strong {{ display:block; font-size:30px; margin-top:8px; }}
    table {{ width:100%; border-collapse:collapse; }}
    th,td {{ padding:12px 10px; border-bottom:1px solid var(--line); text-align:left; font-size:14px; }}
    th {{ color:var(--muted); font-weight:700; background:#fbfcff; }}
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
    pre {{ white-space:pre-wrap; word-break:break-all; background:#0f172a; color:#dbeafe; padding:16px; border-radius:14px; overflow:auto; }}
    @media (max-width:900px) {{ .layout {{ grid-template-columns:1fr; }} aside {{ position:static; }} .grid,.form-grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="layout">
    <aside>
      <div class="brand">杭州特耐时</div>
      <nav>
        <a href="/admin">后台首页</a>
        <div class="nav-group">图纸管理</div>
        <a href="/admin/drawings">图纸识别</a>
        <a href="/admin/drawings/pending">待确认图纸</a>
        <a href="/admin/drawings/confirmed">已确认图纸</a>
        <div class="nav-group">库存管理</div>
        <a href="/admin/inventory">库存查询</a>
        <a href="/admin/inventory/inbound">产品入库</a>
        <a href="/admin/inventory/outbound">产品出库</a>
        <a href="/admin/inventory/transactions">库存流水</a>
        <div class="nav-group">余料管理</div>
        <a href="/admin/scraps/pending">待入库余料</a>
        <a href="/admin/scraps/outbound">余料出库</a>
        <a href="/admin/scraps">余料记录</a>
        <a href="/admin/scraps/transactions">余料流水</a>
      </nav>
    </aside>
    <main>{body}</main>
  </div>
</body>
</html>
    """
    return HTMLResponse(html)


@router.get("/admin", response_class=HTMLResponse)
def admin_home(db: Session = Depends(get_db)) -> HTMLResponse:
    inventory_count = db.query(MaterialInventory).filter(MaterialInventory.inventory_type != "scrap").count()
    available_count = (
        db.query(MaterialInventory)
        .filter(MaterialInventory.inventory_type != "scrap", MaterialInventory.status == "available")
        .count()
    )
    drawing_count = db.query(ProductDrawing).count()
    body = f"""
    <div class="top"><div><h1>后台首页</h1><p class="muted">图纸识别、库存出入库和余料确认入库统一管理。</p></div><div class="actions"><a class="btn" href="/admin/drawings">上传图纸</a><a class="btn secondary" href="/admin/inventory">查询库存</a></div></div>
    <section class="grid">
      <div class="card stat"><span class="muted">库存总数</span><strong>{inventory_count}</strong></div>
      <div class="card stat"><span class="muted">可用库存</span><strong>{available_count}</strong></div>
      <div class="card stat"><span class="muted">图纸记录</span><strong>{drawing_count}</strong></div>
    </section>
    <section class="card"><h2>业务流程</h2><div class="actions"><a class="btn" href="/admin/drawings">1 上传/识别图纸</a><a class="btn secondary" href="/admin/drawings/pending">2 确认图纸</a><a class="btn secondary" href="/admin/inventory/inbound">3 产品入库</a><a class="btn secondary" href="/admin/scraps/pending">4 确认余料入库</a><a class="btn secondary" href="/admin/scraps/outbound">5 余料出库</a></div></section>
    """
    return page("后台首页", body)


@router.get("/admin/inventory", response_class=HTMLResponse)
def inventory_page(
    q: str = "",
    inventory_type: str = "",
    status: str = "",
    material: str = "",
    thickness: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    query = db.query(MaterialInventory).filter(MaterialInventory.inventory_type != "scrap")
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
    if material.strip():
        query = query.filter(MaterialInventory.material.ilike(f"%{material.strip()}%"))
    thickness_value = optional_float(thickness)
    if thickness_value is not None:
        query = query.filter(MaterialInventory.thickness == thickness_value)
    items = query.order_by(MaterialInventory.created_at.desc()).all()
    grouped = {}
    for item in items:
        code = item.material_code or item.source_product_code or f"未编号-{item.id}"
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
    body = f"""
    <div class="top"><div><h1>库存查询</h1><p class="muted">只查询产品库存汇总；入库和出库请进入单独页面操作。</p></div><div class="actions"><a class="btn" href="/admin/inventory/inbound">产品入库</a><a class="btn secondary" href="/admin/inventory/outbound">产品出库</a><a class="btn secondary" href="/admin/inventory/transactions">库存流水</a></div></div>
    <section class="card">
      <form method="get" action="/admin/inventory" class="actions" style="justify-content:flex-start">
        <input name="q" value="{html.escape(keyword)}" placeholder="搜索材料编号、材质、尺寸、库位、来源产品" style="max-width:380px">
        <select name="inventory_type">
          <option value="">全部类型</option>
          <option value="product" {"selected" if inventory_type == "product" else ""}>产品库存</option>
        </select>
        <input name="material" value="{html.escape(material.strip())}" placeholder="材质" style="width:110px">
        <input name="thickness" value="{html.escape(thickness.strip())}" placeholder="厚度" style="width:90px">
        <button class="btn" type="submit">搜索库存</button>
        <a class="btn secondary" href="/admin/inventory">清空</a>
      </form>
    </section>
    <section class="card"><h2>库存汇总</h2><table><thead><tr><th>产品编号</th><th>材质</th><th>厚度</th><th>总数量</th><th>库位</th><th>最近更新时间</th><th>操作</th></tr></thead><tbody>{rows or "<tr><td colspan='7'>暂无库存。</td></tr>"}</tbody></table></section>
    """
    return page("库存管理", body)


@router.get("/admin/inventory/inbound", response_class=HTMLResponse)
def inventory_inbound_page(db: Session = Depends(get_db)) -> HTMLResponse:
    drawing_options = confirmed_drawing_options(db)
    body = f"""
    <div class="top"><div><h1>产品入库</h1><p class="muted">选择已确认图纸对应的产品型号，填写入库数量和库位。</p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">返回库存查询</a></div></div>
    <section class="card">
      <form method="post" action="/admin/inventory" class="form-grid">
        <div><label>选择产品型号</label><select name="drawing_id" required>{drawing_options}</select></div>
        <div><label>数量</label><input name="quantity" type="number" value="1" min="1" required></div>
        <div><label>库位</label><input name="location" placeholder="例如 A-01"></div>
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
    db: Session = Depends(get_db),
) -> HTMLResponse:
    query = db.query(MaterialInventory).filter(
        MaterialInventory.inventory_type != "scrap",
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
    items = query.order_by(MaterialInventory.created_at.asc()).all()
    grouped = {}
    for item in items:
        code = item.material_code or item.source_product_code or f"未编号-{item.id}"
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
        .filter(ProductDrawing.confirmed == 1)
        .order_by(ProductDrawing.updated_at.desc())
        .all()
    )
    drawing_map = {}
    for drawing in drawings:
        if drawing.product_code and drawing.product_code not in drawing_map:
            drawing_map[drawing.product_code] = drawing
    drawing_options = "".join(
        f"<option value='{drawing_map[code].id}'>{html.escape(code)}｜{group['material']}｜厚度 {group['thickness']}｜库存 {group['quantity']}｜库位 {' / '.join(sorted(group['locations'])) or '-'}</option>"
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
    body = f"""
    <div class="top"><div><h1>产品出库</h1><p class="muted">在本页查看当前产品库存，并按产品型号填写出库数量。</p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">返回库存查询</a></div></div>
    <section class="card">
      <form method="get" action="/admin/inventory/outbound" class="actions" style="justify-content:flex-start;margin-bottom:14px">
        <input name="q" value="{html.escape(keyword)}" placeholder="筛选产品型号、材质、库位" style="max-width:320px">
        <input name="material" value="{html.escape(material.strip())}" placeholder="材质" style="width:110px">
        <input name="thickness" value="{html.escape(thickness.strip())}" placeholder="厚度" style="width:90px">
        <button class="btn secondary" type="submit">筛选</button>
        <a class="btn secondary" href="/admin/inventory/outbound">清空</a>
      </form>
      <form method="post" action="/admin/inventory/product/out" class="form-grid">
        <div><label>选择产品型号</label><select name="drawing_id" required>{drawing_options}</select></div>
        <div><label>出库数量</label><input name="quantity" type="number" value="1" min="1" required></div>
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
    db: Session = Depends(get_db),
) -> RedirectResponse:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing or drawing.confirmed != 1:
        raise HTTPException(status_code=404, detail="已确认图纸不存在")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="入库数量必须大于0")
    thickness = drawing.plate_thickness or drawing.product_thickness or drawing.thickness
    if not drawing.product_code or not drawing.material or thickness is None:
        raise HTTPException(status_code=400, detail="图纸缺少产品编号、材质或厚度，不能入库")
    before_total_quantity = sum(
        item.quantity
        for item in db.query(MaterialInventory)
        .filter(MaterialInventory.inventory_type != "scrap", MaterialInventory.material_code == drawing.product_code)
        .all()
    )
    after_total_quantity = before_total_quantity + quantity
    location_value = location.strip() or None
    existing_query = db.query(MaterialInventory).filter(
        MaterialInventory.inventory_type == "product",
        MaterialInventory.material_code == drawing.product_code,
    )
    if location_value is None:
        existing_query = existing_query.filter(MaterialInventory.location.is_(None))
    else:
        existing_query = existing_query.filter(MaterialInventory.location == location_value)
    item = existing_query.order_by(MaterialInventory.created_at.asc()).first()
    if item:
        item.quantity += quantity
        item.status = "available"
        item.material = drawing.material
        item.thickness = thickness
        item.diameter = drawing.max_outer_diameter
        item.length = drawing.max_outer_diameter
        item.width = drawing.max_outer_diameter
        if drawing.max_outer_diameter:
            item.usable_size = f"φ{drawing.max_outer_diameter:g}"
        item.source_product_code = drawing.product_code
    else:
        item = MaterialInventory(
            material_code=drawing.product_code,
            inventory_type="product",
            material=drawing.material,
            thickness=thickness,
            shape="circle",
            diameter=drawing.max_outer_diameter,
            length=drawing.max_outer_diameter,
            width=drawing.max_outer_diameter,
            quantity=quantity,
            location=location_value,
            usable_size=f"φ{drawing.max_outer_diameter:g}" if drawing.max_outer_diameter else None,
            status="available",
            source_product_code=drawing.product_code,
        )
        db.add(item)
    db.flush()
    db.add(
        InventoryTransactionRecord(
            inventory_id=item.id,
            transaction_type="in",
            quantity=quantity,
            before_quantity=before_total_quantity,
            after_quantity=after_total_quantity,
            operator_name=operator_name or None,
            remark="产品入库",
        )
    )
    create_center_scrap_from_drawing(drawing, item, operator_name or None, db, quantity=quantity)
    db.commit()
    return RedirectResponse("/admin/inventory", status_code=303)


@router.post("/admin/inventory/product/out")
def outbound_inventory_from_page(
    drawing_id: int = Form(...),
    quantity: int = Form(...),
    operator_name: str = Form(""),
    remark: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing or drawing.confirmed != 1 or not drawing.product_code:
        raise HTTPException(status_code=404, detail="已确认图纸不存在")
    batches = (
        db.query(MaterialInventory)
        .filter(
            MaterialInventory.inventory_type != "scrap",
            MaterialInventory.material_code == drawing.product_code,
            MaterialInventory.quantity > 0,
        )
        .order_by(MaterialInventory.created_at.asc())
        .all()
    )
    before_total_quantity = sum(item.quantity for item in batches)
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="出库数量必须大于0")
    if before_total_quantity < quantity:
        raise HTTPException(status_code=400, detail=f"库存不足，当前总库存 {before_total_quantity}")
    remaining = quantity
    transaction_item = batches[0]
    for item in batches:
        if remaining <= 0:
            break
        deduction = min(item.quantity, remaining)
        item.quantity -= deduction
        remaining -= deduction
        if item.quantity <= 0:
            item.status = "used"
        else:
            item.status = "available"
    after_total_quantity = before_total_quantity - quantity
    db.add(
        InventoryTransactionRecord(
            inventory_id=transaction_item.id,
            transaction_type="out",
            quantity=quantity,
            before_quantity=before_total_quantity,
            after_quantity=after_total_quantity,
            operator_name=operator_name or None,
            remark=remark or "产品出库",
        )
    )
    db.commit()
    return RedirectResponse("/admin/inventory", status_code=303)


@router.get("/admin/inventory/product/{product_code}", response_class=HTMLResponse)
def inventory_product_detail_page(product_code: str, db: Session = Depends(get_db)) -> HTMLResponse:
    items = (
        db.query(MaterialInventory)
        .filter(MaterialInventory.inventory_type != "scrap", MaterialInventory.material_code == product_code)
        .order_by(MaterialInventory.created_at.desc())
        .all()
    )
    total_quantity = sum(item.quantity for item in items)
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
    body = f"""
    <div class="top"><div><h1>库存明细：{html.escape(product_code)}</h1><p class="muted">当前总数量：<strong>{total_quantity}</strong></p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">返回库存汇总</a></div></div>
    <section class="card"><h2>入库批次</h2><table><thead><tr><th>产品型号</th><th>数量</th><th>库位</th><th>材质</th><th>厚度</th><th>状态</th><th>创建时间</th><th>更新时间</th></tr></thead><tbody>{rows or "<tr><td colspan='8'>暂无该产品库存。</td></tr>"}</tbody></table></section>
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
    item = db.get(MaterialInventory, inventory_id)
    if not item:
        raise HTTPException(status_code=404, detail="库存不存在")
    adjust_inventory_quantity(
        item,
        transaction_type,
        quantity,
        operator_name or None,
        remark or ("手工入库" if transaction_type == "in" else "手工出库"),
        db,
    )
    if transaction_type == "in" and item.inventory_type == "product" and item.material_code:
        drawing = (
            db.query(ProductDrawing)
            .filter(ProductDrawing.product_code == item.material_code, ProductDrawing.confirmed == 1)
            .order_by(ProductDrawing.updated_at.desc())
            .first()
        )
        if drawing:
            create_center_scrap_from_drawing(drawing, item, operator_name or None, db, quantity=quantity)
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
        <div><span class="muted">可用尺寸</span><strong>{item.usable_size or '-'}</strong></div>
      </div>
    </section>
    <section class="card"><h2>该库存流水</h2><table><thead><tr><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>操作人</th><th>备注</th><th>时间</th></tr></thead><tbody>{rows or "<tr><td colspan='7'>暂无该库存流水。</td></tr>"}</tbody></table></section>
    """
    return page("库存详情", body)


@router.get("/admin/inventory/transactions", response_class=HTMLResponse)
def inventory_transactions_page(db: Session = Depends(get_db)) -> HTMLResponse:
    records = db.query(InventoryTransactionRecord).order_by(InventoryTransactionRecord.created_at.desc()).limit(500).all()
    inventory_ids = [r.inventory_id for r in records]
    inventory_map = {
        item.id: item
        for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()
    } if inventory_ids else {}
    rows = ""
    for r in records:
        item = inventory_map.get(r.inventory_id)
        if not item or item.inventory_type == "scrap":
            continue
        product_code = item.material_code or item.source_product_code if item else "-"
        product_link = (
            f"<a href='/admin/inventory/product/{quote(str(product_code), safe='')}'>{product_code}</a>"
            if item and item.inventory_type != "scrap" and product_code != "-"
            else product_code
        )
        before_quantity = "-" if r.transaction_type == "confirm" else r.before_quantity
        after_quantity = "-" if r.transaction_type == "confirm" else r.after_quantity
        quantity_label = r.after_quantity if r.transaction_type == "confirm" and r.quantity == 0 else r.quantity
        rows += f"<tr><td>{product_link}</td><td>{transaction_label(r.transaction_type)}</td><td>{quantity_label}</td><td>{before_quantity}</td><td>{after_quantity}</td><td>{r.operator_name or '-'}</td><td>{r.remark or '-'}</td><td>{r.created_at}</td></tr>"
    body = f"""
    <div class="top"><div><h1>库存流水</h1><p class="muted">只查看产品库存的入库/出库记录；余料记录请到余料流水查看。</p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">返回库存管理</a><a class="btn secondary" href="/admin/scraps/transactions">余料流水</a></div></div>
    <section class="card"><table><thead><tr><th>产品型号/来源</th><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>操作人</th><th>备注</th><th>时间</th></tr></thead><tbody>{rows or "<tr><td colspan='8'>暂无库存流水。</td></tr>"}</tbody></table></section>
    """
    return page("库存流水", body)


@router.get("/admin/scraps/transactions", response_class=HTMLResponse)
def scrap_transactions_page(db: Session = Depends(get_db)) -> HTMLResponse:
    records = db.query(InventoryTransactionRecord).order_by(InventoryTransactionRecord.created_at.desc()).limit(500).all()
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
        before_quantity = "-" if r.transaction_type == "confirm" else r.before_quantity
        after_quantity = "-" if r.transaction_type == "confirm" else r.after_quantity
        quantity_label = r.after_quantity if r.transaction_type == "confirm" and r.quantity == 0 else r.quantity
        rows += f"<tr><td>{item.material}</td><td>{item.thickness}</td><td>{item.usable_size or '-'}</td><td>{scrap_location_label(item)}</td><td>{transaction_label(r.transaction_type)}</td><td>{quantity_label}</td><td>{before_quantity}</td><td>{after_quantity}</td><td>{r.operator_name or '-'}</td><td>{r.remark or '-'}</td><td>{r.created_at}</td></tr>"
    body = f"""
    <div class="top"><div><h1>余料流水</h1><p class="muted">查看余料确认入库、出库等流转记录。</p></div><div class="actions"><a class="btn secondary" href="/admin/scraps">返回余料记录</a><a class="btn secondary" href="/admin/inventory/transactions">库存流水</a></div></div>
    <section class="card"><table><thead><tr><th>材质</th><th>厚度</th><th>可用尺寸</th><th>库位</th><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>操作人</th><th>备注</th><th>时间</th></tr></thead><tbody>{rows or "<tr><td colspan='11'>暂无余料流水。</td></tr>"}</tbody></table></section>
    """
    return page("余料流水", body)


@router.get("/admin/scraps/pending", response_class=HTMLResponse)
def pending_scraps_page(db: Session = Depends(get_db)) -> HTMLResponse:
    items = (
        db.query(MaterialInventory)
        .filter(MaterialInventory.inventory_type == "scrap", MaterialInventory.status == "pending")
        .order_by(MaterialInventory.created_at.desc())
        .all()
    )
    rows = "".join(
        f"""
        <tr>
          <td>{item.source_product_code or '-'}</td><td>{item.quantity}</td><td>{item.material}</td><td>{item.thickness}</td><td>{item.diameter or '-'}</td><td>{item.usable_size or '-'}</td><td>{item.location or '-'}</td>
          <td>
            <form method='post' action='/admin/scraps/{item.id}/confirm' style='display:flex;gap:6px;align-items:center;margin:0'>
              <input name='actual_quantity' type='number' min='0' value='{item.quantity}' style='width:75px'>
              <input name='actual_diameter' type='number' step='0.01' value='{item.diameter or ''}' style='width:90px'>
              <input name='location' value='{'' if item.location in ('待入库', '未入库') else item.location or ''}' placeholder='库位' style='width:100px' required>
              <input name='operator_name' placeholder='确认人' style='width:90px'>
              <button class='btn secondary' type='submit'>确认入库</button>
            </form>
          </td>
        </tr>
        """
        for item in items
    )
    body = f"""
    <div class="top"><div><h1>待入库余料</h1><p class="muted">产品入库后自动生成的中心余料先进入待确认，测量实际尺寸和库位后再变为可用。</p></div><div class="actions"><a class="btn secondary" href="/admin/inventory">库存管理</a><a class="btn secondary" href="/admin/scraps">余料记录</a></div></div>
    <section class="card"><table><thead><tr><th>来源产品</th><th>数量</th><th>材质</th><th>厚度</th><th>理论直径</th><th>可用尺寸</th><th>当前库位</th><th>确认入库</th></tr></thead><tbody>{rows or "<tr><td colspan='8'>暂无待入库余料。</td></tr>"}</tbody></table></section>
    """
    return page("待入库余料", body)


@router.post("/admin/scraps/{inventory_id}/confirm")
def confirm_pending_scrap_from_page(
    inventory_id: int,
    actual_quantity: int = Form(...),
    actual_diameter: str = Form(""),
    location: str = Form(""),
    operator_name: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    item = db.get(MaterialInventory, inventory_id)
    if not item:
        raise HTTPException(status_code=404, detail="余料不存在")
    if item.inventory_type != "scrap":
        raise HTTPException(status_code=400, detail="该库存不是余料")
    if actual_quantity < 0:
        raise HTTPException(status_code=400, detail="实际数量不能小于0")
    if not location.strip():
        raise HTTPException(status_code=400, detail="确认入库时必须填写库位")
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
    body = f"""
    <div class="top"><div><h1>余料出库</h1><p class="muted">先查询可用余料，再按规格和库位汇总出库。</p></div><div class="actions"><a class="btn secondary" href="/admin/scraps">返回余料查询</a></div></div>
    <section class="card">
      <form method="get" action="/admin/scraps/outbound" class="actions" style="justify-content:flex-start">
        <select name="drawing_id">{confirmed_drawing_options(db, selected_id=int(drawing_id) if drawing_id.isdigit() else None, include_blank=True)}</select>
        <input name="material" value="{html.escape(material.strip())}" placeholder="材质" style="width:110px">
        <input name="thickness" value="{html.escape(thickness.strip())}" placeholder="厚度" style="width:90px">
        <input name="required_diameter" value="{html.escape(required_diameter.strip())}" placeholder="所需直径" style="width:100px">
        <input name="location" value="{html.escape(location.strip())}" placeholder="库位" style="width:110px">
        <button class="btn" type="submit">查询可出库余料</button>
        <a class="btn secondary" href="/admin/scraps/outbound">清空</a>
      </form>
    </section>
    {f'<section class="card"><strong>当前按图纸匹配：</strong>{selected_drawing.product_code or "-"}，需要直径 ≥ {(drawing_required_diameter + 2.0):g}，厚度 {drawing_required_thickness or "-"}，材质 {selected_drawing.material or "-"}</section>' if selected_drawing and drawing_required_diameter is not None else ''}
    <section class="card">
      <form method="post" action="/admin/scraps/outbound" class="form-grid">
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
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="出库数量必须大于0")
    parts = scrap_group_key.split("||")
    if len(parts) != 4:
        raise HTTPException(status_code=400, detail="余料规格参数错误")
    material_value, thickness_text, usable_size_value, location_value = parts
    thickness_value = optional_float(thickness_text)
    query = (
        db.query(MaterialInventory)
        .filter(
            MaterialInventory.inventory_type == "scrap",
            MaterialInventory.status == "available",
            MaterialInventory.quantity > 0,
            MaterialInventory.material == material_value,
        )
    )
    if usable_size_value == "-":
        query = query.filter(MaterialInventory.usable_size.is_(None))
    else:
        query = query.filter(MaterialInventory.usable_size == usable_size_value)
    batches = query.order_by(MaterialInventory.created_at.asc()).all()
    if thickness_value is not None:
        batches = [item for item in batches if item.thickness == thickness_value]
    batches = [item for item in batches if scrap_location_label(item) == location_value]
    before_quantity = sum(item.quantity for item in batches)
    if before_quantity < quantity:
        raise HTTPException(status_code=400, detail=f"余料数量不足，当前数量 {before_quantity}")
    remaining = quantity
    transaction_item = batches[0]
    for item in batches:
        if remaining <= 0:
            break
        deduction = min(item.quantity, remaining)
        item.quantity -= deduction
        remaining -= deduction
        if item.quantity <= 0:
            item.status = "used"
    after_quantity = before_quantity - quantity
    db.add(
        InventoryTransactionRecord(
            inventory_id=transaction_item.id,
            transaction_type="out",
            quantity=quantity,
            before_quantity=before_quantity,
            after_quantity=after_quantity,
            operator_name=operator_name or None,
            remark=remark or "余料出库",
        )
    )
    db.commit()
    return RedirectResponse("/admin/scraps", status_code=303)


@router.get("/admin/drawings", response_class=HTMLResponse)
def drawings_page(db: Session = Depends(get_db)) -> HTMLResponse:
    drawings = db.query(ProductDrawing).order_by(ProductDrawing.created_at.desc()).all()
    rows = drawing_rows(drawings)
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
    <section class="card"><h2>图纸记录</h2><table><thead><tr><th>ID</th><th>产品编号</th><th>产品名称</th><th>材质</th><th>厚度</th><th>最大外径</th><th>状态</th><th>操作</th></tr></thead><tbody>{rows}</tbody></table></section>
    """
    return page("图纸识别", body)


def drawing_rows(drawings: list[ProductDrawing], show_id: bool = True) -> str:
    id_cell = lambda drawing: f"<td>{drawing.id}</td>" if show_id else ""
    rows = "".join(
        f"<tr>{id_cell(d)}<td>{d.product_code or '-'}</td><td>{d.product_name or '-'}</td><td>{d.material or '-'}</td><td>{d.thickness or '-'}</td><td>{d.max_outer_diameter or '-'}</td><td>{'已确认' if d.confirmed else '待确认'}</td><td><a class='btn secondary' href='/admin/drawings/{d.id}'>查看</a></td></tr>"
        for d in drawings
    )
    colspan = 8 if show_id else 7
    return rows or f"<tr><td colspan='{colspan}'>暂无图纸记录。</td></tr>"


@router.get("/admin/drawings/confirmed", response_class=HTMLResponse)
def confirmed_drawings_page(q: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    query = db.query(ProductDrawing).filter(ProductDrawing.confirmed == 1)
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            (ProductDrawing.product_code.ilike(like))
            | (ProductDrawing.product_name.ilike(like))
            | (ProductDrawing.material.ilike(like))
        )
    drawings = query.order_by(ProductDrawing.updated_at.desc()).all()
    body = f"""
    <div class="top"><div><h1>已确认图纸</h1><p class="muted">这些图纸已经人工确认，可直接用于产品入库。</p></div><div class="actions"><a class="btn secondary" href="/admin/drawings">全部图纸</a><a class="btn secondary" href="/admin/drawings/pending">待确认图纸</a></div></div>
    <section class="card">
      <form method="get" action="/admin/drawings/confirmed" class="actions" style="justify-content:flex-start;margin-bottom:14px">
        <input name="q" value="{html.escape(keyword)}" placeholder="搜索产品编号、名称或材质" style="max-width:320px">
        <button class="btn" type="submit">搜索</button>
        <a class="btn secondary" href="/admin/drawings/confirmed">清空</a>
      </form>
      <table><thead><tr><th>产品编号</th><th>产品名称</th><th>材质</th><th>厚度</th><th>最大外径</th><th>状态</th><th>操作</th></tr></thead><tbody>{drawing_rows(drawings, show_id=False)}</tbody></table>
    </section>
    """
    return page("已确认图纸", body)


@router.get("/admin/drawings/pending", response_class=HTMLResponse)
def pending_drawings_page(db: Session = Depends(get_db)) -> HTMLResponse:
    drawings = db.query(ProductDrawing).filter(ProductDrawing.confirmed == 0).order_by(ProductDrawing.created_at.desc()).all()
    body = f"""
    <div class="top"><div><h1>待确认图纸</h1><p class="muted">这些图纸需要人工检查并保存确认结果。</p></div><div class="actions"><a class="btn secondary" href="/admin/drawings">全部图纸</a><a class="btn secondary" href="/admin/drawings/confirmed">已确认图纸</a></div></div>
    <section class="card"><table><thead><tr><th>ID</th><th>产品编号</th><th>产品名称</th><th>材质</th><th>厚度</th><th>最大外径</th><th>状态</th><th>操作</th></tr></thead><tbody>{drawing_rows(drawings)}</tbody></table></section>
    """
    return page("待确认图纸", body)


@router.post("/admin/drawings/upload")
def upload_drawing_from_page(file: UploadFile = File(...), db: Session = Depends(get_db)) -> RedirectResponse:
    upload_drawing(file=file, db=db)
    return RedirectResponse("/admin/drawings", status_code=303)


@router.get("/admin/drawings/{drawing_id}", response_class=HTMLResponse)
def drawing_detail_page(drawing_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    body = f"""
    <div class="top">
      <div><h1>图纸详情</h1><p class="muted">请人工确认识别结果，确认后可进行产品入库。</p></div>
      <div class="actions">
        <a class="btn secondary" href="/admin/drawings">返回列表</a>
        <a class="btn secondary" href="/admin/drawings/{drawing.id}/preview" target="_blank">查看图纸预览</a>
        <form method="post" action="/admin/drawings/{drawing.id}/rerun" style="margin:0">
          <button class="btn secondary" type="submit">重新识别当前图纸</button>
        </form>
        <a class="btn secondary" href="/admin/inventory/inbound">产品入库</a>
      </div>
    </div>
    <section class="card">
      <h2>人工确认识别结果</h2>
      <form method="post" action="/admin/drawings/{drawing.id}/confirm" class="form-grid">
        <div><label>产品型号</label><input name="product_code" value="{drawing.product_code or ''}"></div>
        <div><label>产品名称</label><input name="product_name" value="{drawing.product_name or ''}"></div>
        <div><label>材质</label><input name="material" value="{drawing.material or ''}" placeholder="例如 50#"></div>
        <div><label>外径</label><input name="max_outer_diameter" type="number" step="0.01" value="{drawing.max_outer_diameter or ''}" placeholder="mm"></div>
        <div><label>内径</label><input name="min_inner_diameter" type="number" step="0.01" value="{drawing.min_inner_diameter or ''}" placeholder="mm"></div>
        <div><label>产品厚度</label><input name="product_thickness" type="number" step="0.001" value="{drawing.product_thickness or ''}" placeholder="含复合材料总厚"></div>
        <div><label>钢板厚度</label><input name="plate_thickness" type="number" step="0.001" value="{drawing.plate_thickness or ''}" placeholder="基板厚度"></div>
        <div><label>齿数 z</label><input name="teeth_count" type="number" value="{drawing.teeth_count or ''}"></div>
        <div><label>模数 m</label><input name="module" type="number" step="0.001" value="{drawing.module or ''}"></div>
        <div><label>压力角 α</label><input name="pressure_angle" type="number" step="0.01" value="{drawing.pressure_angle or ''}" placeholder="常见20°"></div>
        <div><label>变位系数 x</label><input name="profile_shift_coefficient" type="number" step="0.001" value="{drawing.profile_shift_coefficient or ''}"></div>
        <div><label>公法线长度 L</label><input name="common_normal_length" type="number" step="0.001" value="{drawing.common_normal_length or ''}" placeholder="mm"></div>
        <div><label>跨齿数 n</label><input name="span_teeth_count" type="number" value="{drawing.span_teeth_count or ''}"></div>
        <div><label>量棒直径 dp</label><input name="pin_diameter" type="number" step="0.001" value="{drawing.pin_diameter or ''}" placeholder="mm"></div>
        <div><label>棒间距 M</label><input name="pin_span" type="number" step="0.001" value="{drawing.pin_span or ''}" placeholder="mm"></div>
        <div><label>中心余料尺寸</label><input name="expected_scrap_size" value="{drawing.expected_scrap_size or ''}" placeholder="中间割下来的圆料，例如 φ77.5"></div>
        <div style="align-self:end"><button class="btn" type="submit">保存确认结果</button></div>
      </form>
    </section>
    <section class="card"><h2>原始解析JSON</h2><pre>{drawing.parse_result_json}</pre></section>
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
      <div><h1>图纸预览</h1><p class="muted">图纸ID：{drawing.id}　产品型号：{drawing.product_code or '-'}</p></div>
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
    apply_recognition_to_drawing(drawing)
    db.commit()
    return RedirectResponse(f"/admin/drawings/{drawing.id}", status_code=303)


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
        f"<tr><td>{r.source_product_code or '-'}</td><td>{scrap_map.get(r.scrap_inventory_id).quantity if scrap_map.get(r.scrap_inventory_id) else '-'}</td><td>{scrap_map.get(r.scrap_inventory_id).status if scrap_map.get(r.scrap_inventory_id) else '-'}</td><td>{scrap_location_label(scrap_map.get(r.scrap_inventory_id))}</td><td>{scrap_map.get(r.scrap_inventory_id).usable_size if scrap_map.get(r.scrap_inventory_id) else '-'}</td><td>{r.theoretical_size or '-'}</td><td>{r.actual_size or '-'}</td><td>{r.operator_name or '-'}</td><td>{r.registered_at}</td></tr>"
        for r in filtered_records
    )
    body = f"""
    <div class='top'><div><h1>余料记录</h1><p class='muted'>查看切割后生成的新余料来源、数量与尺寸。</p></div><div class="actions"><a class="btn" href="/admin/scraps/outbound">余料出库</a><a class="btn secondary" href="/admin/scraps/transactions">余料流水</a><a class="btn secondary" href="/admin/scraps/pending">待入库余料</a></div></div>
    <section class="card">
      <form method="get" action="/admin/scraps" class="actions" style="justify-content:flex-start">
        <select name="drawing_id">{confirmed_drawing_options(db, selected_id=int(drawing_id) if drawing_id.isdigit() else None, include_blank=True)}</select>
        <input name="material" value="{html.escape(material.strip())}" placeholder="材质" style="width:110px">
        <input name="thickness" value="{html.escape(thickness.strip())}" placeholder="厚度" style="width:90px">
        <input name="required_diameter" value="{html.escape(required_diameter.strip())}" placeholder="所需直径" style="width:100px">
        <input name="location" value="{html.escape(location.strip())}" placeholder="库位" style="width:110px">
        <button class="btn" type="submit">搜索余料</button>
        <a class="btn secondary" href="/admin/scraps">清空</a>
      </form>
    </section>
    {f'<section class="card"><strong>当前按图纸匹配：</strong>{selected_drawing.product_code or "-"}，需要直径 ≥ {(drawing_required_diameter + 2.0):g}，厚度 {drawing_required_thickness or "-"}，材质 {selected_drawing.material or "-"}</section>' if selected_drawing and drawing_required_diameter is not None else ''}
    <section class='card'><h2>按规格汇总</h2><table><thead><tr><th>材质</th><th>厚度</th><th>可用尺寸</th><th>库位</th><th>总数量</th></tr></thead><tbody>{spec_rows or "<tr><td colspan='5'>暂无余料。</td></tr>"}</tbody></table></section>
    <section class='card'><h2>余料明细</h2><table><thead><tr><th>来源产品</th><th>数量</th><th>状态</th><th>库位</th><th>可用尺寸</th><th>理论尺寸</th><th>实际尺寸</th><th>登记人</th><th>时间</th></tr></thead><tbody>{rows or "<tr><td colspan='9'>暂无余料记录。</td></tr>"}</tbody></table></section>
    """
    return page("余料记录", body)
