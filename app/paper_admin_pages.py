import html
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.admin_pages import page, safe_value
from app.database import get_db
from app.models import PaperInventoryBatch, PaperInventoryTransaction, PaperSpecification
from app.services.inventory_service import inventory_write_lock
from app.services.material_formats import paper_roll_size, paper_sheet_model
from app.services.operation_log import record_operation_log
from app.services.paper_inventory import (
    PAPER_TYPE_LABELS,
    PAPER_UNITS,
    normalize_paper_specification,
    outbound_paper_fifo,
    paper_batch_size,
    paper_inventory_groups,
    paper_inventory_snapshot,
    paper_specification_sort_key,
    reverse_paper_transaction,
)
from app.time_utils import china_now


router = APIRouter()


def locked_paper_write():
    with inventory_write_lock():
        yield


def _spec_size(spec: PaperSpecification) -> str:
    if spec.paper_type == "roll":
        return paper_roll_size(spec.thickness, spec.inner_diameter, spec.outer_diameter)
    return paper_sheet_model(spec.thickness, spec.length, spec.width)


def _apply_spec_values(spec: PaperSpecification, values: dict[str, object]) -> None:
    spec.paper_type = str(values["paper_type"])
    spec.model = str(values["model"])
    spec.material_name = str(values["material_name"])
    spec.thickness = float(values["thickness"])
    spec.inner_diameter = values["inner_diameter"]
    spec.outer_diameter = values["outer_diameter"]
    spec.length = values["length"]
    spec.width = values["width"]


def _spec_form(action: str, spec: PaperSpecification | None = None) -> str:
    paper_type = spec.paper_type if spec else "roll"
    return f"""
    <form method="post" action="{action}" class="form-grid" data-paper-spec-form>
      <div><label>纸材类型</label><select name="paper_type" data-paper-type>
        <option value="roll" {'selected' if paper_type == 'roll' else ''}>纸圈</option>
        <option value="sheet" {'selected' if paper_type == 'sheet' else ''}>纸张</option>
      </select></div>
      <div><label>纸材名称/材质</label><input name="material_name" value="{safe_value(spec.material_name if spec else '')}" placeholder="例如 蓝纸 / 摩擦纸" required></div>
      <div><label>厚度 mm</label><input name="thickness" type="number" step="0.01" min="0.01" value="{safe_value(spec.thickness if spec else '')}" required></div>
      <div class="paper-roll-fields"><label>纸圈型号</label><input name="model" value="{safe_value(spec.model if spec and paper_type == 'roll' else '')}" placeholder="例如 Tnx236.2A"></div>
      <div class="paper-roll-fields"><label>内径 mm</label><input name="inner_diameter" type="number" step="0.01" min="0.01" value="{safe_value(spec.inner_diameter if spec else '')}"></div>
      <div class="paper-roll-fields"><label>外径 mm</label><input name="outer_diameter" type="number" step="0.01" min="0.01" value="{safe_value(spec.outer_diameter if spec else '')}"></div>
      <div class="paper-sheet-fields"><label>长度 mm</label><input name="length" type="number" step="0.01" min="0.01" value="{safe_value(spec.length if spec else '')}"></div>
      <div class="paper-sheet-fields"><label>宽度 mm</label><input name="width" type="number" step="0.01" min="0.01" value="{safe_value(spec.width if spec else '')}"></div>
      {f'<div><label>状态</label><select name="is_active"><option value="1" {"selected" if spec and spec.is_active else ""}>启用</option><option value="0" {"selected" if spec and not spec.is_active else ""}>停用</option></select></div>' if spec else ''}
      <div><label>备注</label><input name="remark" value="{safe_value(spec.remark if spec else '')}" placeholder="可选"></div>
      <div style="align-self:end"><button class="btn" type="submit">保存规格</button></div>
    </form>
    """


PAPER_SPEC_SCRIPT = """
<script>
document.querySelectorAll('[data-paper-spec-form]').forEach((form) => {
  const typeSelect = form.querySelector('[data-paper-type]');
  const syncFields = () => {
    const isRoll = typeSelect.value === 'roll';
    form.querySelectorAll('.paper-roll-fields').forEach((wrap) => {
      wrap.hidden = !isRoll;
      wrap.querySelectorAll('input').forEach((input) => {
        input.required = isRoll;
        if (!isRoll) input.value = '';
      });
    });
    form.querySelectorAll('.paper-sheet-fields').forEach((wrap) => {
      wrap.hidden = isRoll;
      wrap.querySelectorAll('input').forEach((input) => {
        input.required = !isRoll;
        if (isRoll) input.value = '';
      });
    });
  };
  typeSelect.addEventListener('change', syncFields);
  syncFields();
});
</script>
"""


@router.get("/admin/paper-specifications", response_class=HTMLResponse)
def paper_specifications_page(
    q: str = "",
    paper_type: str = "",
    material_name: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    query = db.query(PaperSpecification)
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            PaperSpecification.model.ilike(like)
            | PaperSpecification.material_name.ilike(like)
            | PaperSpecification.remark.ilike(like)
        )
    if paper_type in PAPER_TYPE_LABELS:
        query = query.filter(PaperSpecification.paper_type == paper_type)
    if material_name.strip():
        query = query.filter(PaperSpecification.material_name.ilike(f"%{material_name.strip()}%"))
    specs = query.all()
    specs.sort(key=lambda spec: (-int(bool(spec.is_active)), *paper_specification_sort_key(spec)))
    rows = "".join(
        f"<tr><td>{PAPER_TYPE_LABELS[spec.paper_type]}</td><td>{html.escape(spec.model)}</td>"
        f"<td>{html.escape(spec.material_name)}</td><td>{html.escape(_spec_size(spec))}</td>"
        f"<td>{'启用' if spec.is_active else '停用'}</td><td>{html.escape(spec.remark or '-')}</td>"
        f"<td><div class='actions' style='gap:6px;justify-content:flex-start'><a class='btn secondary' href='/admin/paper-specifications/{spec.id}/edit'>修改</a>"
        f"<form method='post' action='/admin/paper-specifications/{spec.id}/toggle'><button class='btn secondary' type='submit'>{'停用' if spec.is_active else '启用'}</button></form></div></td></tr>"
        for spec in specs
    )
    body = f"""
    <div class="top"><div><h1>纸材规格</h1><p class="muted">维护纸圈与纸张规格，尺寸统一把厚度放在第一位。</p></div><div class="actions"><a class="btn secondary" href="/admin/paper-materials/inbound">纸材入库</a></div></div>
    <section class="card"><form method="get" action="/admin/paper-specifications" class="actions" style="justify-content:flex-start">
      <input name="q" value="{safe_value(keyword)}" placeholder="型号/材质/备注" style="width:220px">
      <select name="paper_type" style="width:130px"><option value="">全部类型</option><option value="roll" {'selected' if paper_type == 'roll' else ''}>纸圈</option><option value="sheet" {'selected' if paper_type == 'sheet' else ''}>纸张</option></select>
      <input name="material_name" value="{safe_value(material_name.strip())}" placeholder="纸材名称/材质" style="width:170px">
      <button class="btn" type="submit">搜索规格</button><a class="btn secondary" href="/admin/paper-specifications">清空</a>
    </form></section>
    <section class="card">{_spec_form('/admin/paper-specifications')}</section>
    <section class="card"><table class="mobile-list"><thead><tr><th>类型</th><th>型号</th><th>纸材名称/材质</th><th>尺寸</th><th>状态</th><th>备注</th><th>操作</th></tr></thead><tbody>{rows or "<tr><td colspan='7'>暂无纸材规格。</td></tr>"}</tbody></table></section>
    {PAPER_SPEC_SCRIPT}
    """
    return page("纸材规格", body)


@router.post("/admin/paper-specifications")
def create_paper_specification(
    paper_type: str = Form(...),
    model: str = Form(""),
    material_name: str = Form(...),
    thickness: float = Form(...),
    inner_diameter: float | None = Form(None),
    outer_diameter: float | None = Form(None),
    length: float | None = Form(None),
    width: float | None = Form(None),
    remark: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    values = normalize_paper_specification(
        paper_type, model, material_name, thickness, inner_diameter, outer_diameter, length, width
    )
    spec = PaperSpecification(remark=remark.strip() or None, is_active=1, **values)
    db.add(spec)
    db.commit()
    return RedirectResponse("/admin/paper-specifications", status_code=303)


@router.get("/admin/paper-specifications/{spec_id}/edit", response_class=HTMLResponse)
def edit_paper_specification_page(spec_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    spec = db.get(PaperSpecification, spec_id)
    if not spec:
        raise HTTPException(status_code=404, detail="纸材规格不存在")
    body = f"""
    <div class="top"><div><h1>修改纸材规格</h1><p class="muted">修改只影响后续入库，历史批次保留原始快照和单价。</p></div><a class="btn secondary" href="/admin/paper-specifications">返回纸材规格</a></div>
    <section class="card">{_spec_form(f'/admin/paper-specifications/{spec.id}/edit', spec)}</section>
    {PAPER_SPEC_SCRIPT}
    """
    return page("修改纸材规格", body)


@router.post("/admin/paper-specifications/{spec_id}/edit")
def update_paper_specification(
    spec_id: int,
    paper_type: str = Form(...),
    model: str = Form(""),
    material_name: str = Form(...),
    thickness: float = Form(...),
    inner_diameter: float | None = Form(None),
    outer_diameter: float | None = Form(None),
    length: float | None = Form(None),
    width: float | None = Form(None),
    is_active: int = Form(1),
    remark: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    spec = db.get(PaperSpecification, spec_id)
    if not spec:
        raise HTTPException(status_code=404, detail="纸材规格不存在")
    values = normalize_paper_specification(
        paper_type, model, material_name, thickness, inner_diameter, outer_diameter, length, width
    )
    _apply_spec_values(spec, values)
    spec.is_active = 1 if is_active else 0
    spec.remark = remark.strip() or None
    db.commit()
    return RedirectResponse("/admin/paper-specifications", status_code=303)


@router.post("/admin/paper-specifications/{spec_id}/toggle")
def toggle_paper_specification(spec_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    spec = db.get(PaperSpecification, spec_id)
    if not spec:
        raise HTTPException(status_code=404, detail="纸材规格不存在")
    spec.is_active = 0 if spec.is_active else 1
    db.commit()
    return RedirectResponse("/admin/paper-specifications", status_code=303)


@router.get("/admin/paper-materials/inbound", response_class=HTMLResponse)
def paper_inbound_page(db: Session = Depends(get_db)) -> HTMLResponse:
    specs = db.query(PaperSpecification).filter(PaperSpecification.is_active == 1).all()
    specs.sort(key=paper_specification_sort_key)
    options = "".join(
        f"<option value='{spec.id}'>{html.escape(spec.model)}｜{html.escape(spec.material_name)}｜{PAPER_TYPE_LABELS[spec.paper_type]}｜{html.escape(_spec_size(spec))}｜元/{PAPER_UNITS[spec.paper_type]}</option>"
        for spec in specs
    )
    body = f"""
    <div class="top"><div><h1>纸材入库</h1><p class="muted">选择纸材规格，按批次记录数量和实际入库单价。</p></div><div class="actions"><a class="btn secondary" href="/admin/paper-specifications">维护纸材规格</a><a class="btn secondary" href="/admin/paper-materials">返回纸材库存</a></div></div>
    <section class="card"><form method="post" action="/admin/paper-materials/inbound" class="form-grid" data-confirm-flow="true" data-confirm-title="确认纸材入库" data-confirm-note="数量与单价将保存到本次入库批次。">
      <div><label>筛选纸材规格</label><input type="search" data-select-filter="paper-inbound-spec" placeholder="输入型号、材质或尺寸"></div>
      <div><label>选择纸材规格</label><select id="paper-inbound-spec" name="specification_id" required><option value="">请选择</option>{options}</select></div>
      <div><label>批次编号</label><input name="batch_code" placeholder="不填自动生成"></div>
      <div><label>入库数量</label><input name="quantity" type="number" min="1" required></div>
      <div><label>入库单价（元/圈或元/张）</label><input name="unit_price" type="number" step="0.01" min="0" required></div>
      <div><label>库位</label><input name="location" placeholder="例如 纸材区-P01"></div>
      <div><label>操作人</label><input name="operator_name" placeholder="例如 张三"></div>
      <div><label>备注</label><input name="remark" placeholder="例如 采购入库"></div>
      <div style="align-self:end"><button class="btn" type="submit">确认入库</button></div>
    </form></section>
    """
    return page("纸材入库", body)


@router.post("/admin/paper-materials/inbound")
def create_paper_inbound(
    specification_id: int = Form(...),
    batch_code: str = Form(""),
    quantity: int = Form(...),
    unit_price: str = Form(...),
    location: str = Form(""),
    operator_name: str = Form(""),
    remark: str = Form(""),
    _lock=Depends(locked_paper_write),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    spec = db.get(PaperSpecification, specification_id)
    if not spec or not spec.is_active:
        raise HTTPException(status_code=400, detail="纸材规格不存在或已停用")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="入库数量必须大于0")
    try:
        price = Decimal(str(unit_price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise HTTPException(status_code=400, detail="入库单价格式错误") from exc
    if price < 0:
        raise HTTPException(status_code=400, detail="入库单价不能小于0")
    batch = PaperInventoryBatch(
        specification_id=spec.id,
        batch_code=batch_code.strip() or f"PAPER-{china_now().strftime('%Y%m%d%H%M%S')}",
        paper_type=spec.paper_type,
        model=spec.model,
        material_name=spec.material_name,
        thickness=spec.thickness,
        inner_diameter=spec.inner_diameter,
        outer_diameter=spec.outer_diameter,
        length=spec.length,
        width=spec.width,
        quantity=quantity,
        unit_price=price,
        location=location.strip() or None,
        status="available",
    )
    db.add(batch)
    db.flush()
    transaction = PaperInventoryTransaction(
        inventory_id=batch.id,
        transaction_type="in",
        quantity=quantity,
        before_quantity=0,
        after_quantity=quantity,
        operator_name=operator_name.strip() or None,
        remark=remark.strip() or "纸材入库",
    )
    db.add(transaction)
    record_operation_log(
        db,
        "paper_inbound",
        "paper_inventory",
        batch.id,
        operator_name.strip() or None,
        transaction.remark,
        after_data=paper_inventory_snapshot(batch),
    )
    db.commit()
    return RedirectResponse("/admin/paper-materials", status_code=303)


def _paper_batch_query(
    db: Session,
    q: str = "",
    paper_type: str = "",
    material_name: str = "",
    thickness: str = "",
    location: str = "",
):
    query = db.query(PaperInventoryBatch).filter(PaperInventoryBatch.quantity > 0)
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            PaperInventoryBatch.batch_code.ilike(like)
            | PaperInventoryBatch.model.ilike(like)
            | PaperInventoryBatch.material_name.ilike(like)
            | PaperInventoryBatch.location.ilike(like)
        )
    if paper_type in PAPER_TYPE_LABELS:
        query = query.filter(PaperInventoryBatch.paper_type == paper_type)
    if material_name.strip():
        query = query.filter(PaperInventoryBatch.material_name.ilike(f"%{material_name.strip()}%"))
    if thickness.strip():
        try:
            query = query.filter(PaperInventoryBatch.thickness == float(thickness))
        except ValueError:
            pass
    if location.strip():
        query = query.filter(PaperInventoryBatch.location.ilike(f"%{location.strip()}%"))
    return query


def _price_range(group: dict) -> str:
    if group["price_min"] == group["price_max"]:
        return f"¥{group['price_min']:.2f}"
    return f"¥{group['price_min']:.2f}～¥{group['price_max']:.2f}"


@router.get("/admin/paper-materials", response_class=HTMLResponse)
def paper_inventory_page(
    q: str = "",
    paper_type: str = "",
    material_name: str = "",
    thickness: str = "",
    location: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    batches = _paper_batch_query(db, q, paper_type, material_name, thickness, location).all()
    groups = paper_inventory_groups(batches)
    rows = "".join(
        f"<tr><td>{group['type_label']}</td><td>{html.escape(group['model'])}</td>"
        f"<td>{html.escape(group['material_name'])}</td><td>{html.escape(group['size'])}</td>"
        f"<td><strong>{group['quantity']} {group['unit']}</strong></td><td>{_price_range(group)} 元/{group['unit']}</td>"
        f"<td>{html.escape(' / '.join(sorted(group['locations'])) or '-')}</td><td>{group['batch_count']}</td>"
        f"<td><a class='btn secondary' href='/admin/paper-materials/detail?specification_id={group['specification_id']}'>查看明细</a></td></tr>"
        for group in groups
    )
    body = f"""
    <div class="top"><div><h1>纸材库存</h1><p class="muted">按纸材型号和尺寸汇总当前数量及有库存批次的单价范围。</p></div><div class="actions"><a class="btn" href="/admin/paper-materials/inbound">纸材入库</a><a class="btn secondary" href="/admin/paper-materials/outbound">纸材出库</a><a class="btn secondary" href="/admin/paper-materials/transactions">纸材流水</a></div></div>
    <section class="card"><form method="get" action="/admin/paper-materials" class="actions" style="justify-content:flex-start">
      <input name="q" value="{safe_value(q.strip())}" placeholder="批次/型号/材质/库位" style="width:220px">
      <select name="paper_type" style="width:130px"><option value="">全部类型</option><option value="roll" {'selected' if paper_type == 'roll' else ''}>纸圈</option><option value="sheet" {'selected' if paper_type == 'sheet' else ''}>纸张</option></select>
      <input name="material_name" value="{safe_value(material_name.strip())}" placeholder="纸材名称/材质" style="width:170px">
      <input name="thickness" value="{safe_value(thickness.strip())}" placeholder="厚度" style="width:110px">
      <input name="location" value="{safe_value(location.strip())}" placeholder="库位" style="width:130px">
      <button class="btn" type="submit">搜索纸材</button><a class="btn secondary" href="/admin/paper-materials">清空</a>
    </form></section>
    <section class="card"><h2>纸材库存汇总</h2><table class="mobile-list"><thead><tr><th>类型</th><th>型号</th><th>纸材名称/材质</th><th>尺寸</th><th>库存数量</th><th>单价范围</th><th>库位</th><th>批次数</th><th>操作</th></tr></thead><tbody>{rows or "<tr><td colspan='9'>暂无纸材库存。</td></tr>"}</tbody></table></section>
    """
    return page("纸材库存", body)


@router.get("/admin/paper-materials/detail", response_class=HTMLResponse)
def paper_group_detail_page(specification_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    spec = db.get(PaperSpecification, specification_id)
    if not spec:
        raise HTTPException(status_code=404, detail="纸材规格不存在")
    batches = (
        db.query(PaperInventoryBatch)
        .filter(PaperInventoryBatch.specification_id == specification_id)
        .order_by(PaperInventoryBatch.created_at.asc(), PaperInventoryBatch.id.asc())
        .all()
    )
    batch_ids = [batch.id for batch in batches]
    transactions = (
        db.query(PaperInventoryTransaction)
        .filter(PaperInventoryTransaction.inventory_id.in_(batch_ids))
        .order_by(PaperInventoryTransaction.created_at.desc())
        .all()
        if batch_ids
        else []
    )
    batch_map = {batch.id: batch for batch in batches}
    batch_rows = "".join(
        f"<tr><td>{html.escape(batch.batch_code)}</td><td>{batch.quantity} {PAPER_UNITS[batch.paper_type]}</td>"
        f"<td>¥{batch.unit_price:.2f} 元/{PAPER_UNITS[batch.paper_type]}</td><td>{html.escape(batch.location or '-')}</td>"
        f"<td>{html.escape(batch.status)}</td><td>{batch.created_at}</td></tr>"
        for batch in batches
    )
    transaction_rows = "".join(
        f"<tr><td>{html.escape(batch_map[record.inventory_id].batch_code)}</td><td>{html.escape(record.transaction_type)}</td>"
        f"<td>{record.quantity}</td><td>{record.before_quantity}</td><td>{record.after_quantity}</td>"
        f"<td>{html.escape(record.customer_name or '-')}</td><td>{html.escape(record.operator_name or '-')}</td>"
        f"<td>{html.escape(record.remark or '-')}</td><td>{record.created_at}</td></tr>"
        for record in transactions
    )
    body = f"""
    <div class="top"><div><h1>纸材明细：{html.escape(spec.model)}</h1><p class="muted">{html.escape(spec.material_name)}｜{PAPER_TYPE_LABELS[spec.paper_type]}｜{html.escape(_spec_size(spec))}</p></div><a class="btn secondary" href="/admin/paper-materials">返回纸材库存</a></div>
    <section class="card"><h2>库存批次</h2><table><thead><tr><th>批次编号</th><th>当前数量</th><th>入库单价</th><th>库位</th><th>状态</th><th>入库时间</th></tr></thead><tbody>{batch_rows or "<tr><td colspan='6'>暂无批次。</td></tr>"}</tbody></table></section>
    <section class="card"><h2>出入库流水</h2><table class="wide-transaction-table"><thead><tr><th>批次编号</th><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>客户/去向</th><th>操作人</th><th>备注</th><th>时间</th></tr></thead><tbody>{transaction_rows or "<tr><td colspan='9'>暂无流水。</td></tr>"}</tbody></table></section>
    """
    return page("纸材明细", body)


@router.get("/admin/paper-materials/outbound", response_class=HTMLResponse)
def paper_outbound_page(
    specification_id: str = "",
    q: str = "",
    paper_type: str = "",
    material_name: str = "",
    thickness: str = "",
    location: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    batches = _paper_batch_query(db, q, paper_type, material_name, thickness, location).all()
    groups = paper_inventory_groups(batches)
    selected_id = int(specification_id) if specification_id.isdigit() else None
    selected = next((group for group in groups if group["specification_id"] == selected_id), None)
    rows = "".join(
        f"<tr><td>{group['type_label']}</td><td>{html.escape(group['model'])}</td><td>{html.escape(group['material_name'])}</td>"
        f"<td>{html.escape(group['size'])}</td><td><strong>{group['quantity']} {group['unit']}</strong></td>"
        f"<td>{html.escape(' / '.join(sorted(group['locations'])) or '-')}</td>"
        f"<td><a class='btn secondary' href='/admin/paper-materials/outbound?specification_id={group['specification_id']}'>选择出库</a></td></tr>"
        for group in groups
    )
    body = f"""
    <div class="top"><div><h1>纸材出库</h1><p class="muted">选择纸材规格后，系统按最早入库批次 FIFO 扣减。</p></div><div class="actions"><a class="btn secondary" href="/admin/paper-materials">返回纸材库存</a><a class="btn secondary" href="/admin/paper-materials/transactions">纸材流水</a></div></div>
    <section class="card"><form method="get" action="/admin/paper-materials/outbound" class="actions" style="justify-content:flex-start">
      <input name="thickness" value="{safe_value(thickness.strip())}" placeholder="厚度" style="width:110px">
      <input name="q" value="{safe_value(q.strip())}" placeholder="型号/批次" style="width:180px">
      <select name="paper_type" style="width:120px"><option value="">全部类型</option><option value="roll" {'selected' if paper_type == 'roll' else ''}>纸圈</option><option value="sheet" {'selected' if paper_type == 'sheet' else ''}>纸张</option></select>
      <input name="material_name" value="{safe_value(material_name.strip())}" placeholder="纸材名称/材质" style="width:170px">
      <input name="location" value="{safe_value(location.strip())}" placeholder="库位" style="width:130px">
      <button class="btn" type="submit">查看可用规格</button><a class="btn secondary" href="/admin/paper-materials/outbound">清空</a>
    </form></section>
    <section class="card"><h2>当前可用规格</h2><table><thead><tr><th>类型</th><th>型号</th><th>纸材名称/材质</th><th>尺寸</th><th>可出库数量</th><th>库位</th><th>操作</th></tr></thead><tbody>{rows or "<tr><td colspan='7'>暂无可出库纸材。</td></tr>"}</tbody></table></section>
    <section class="card"><h2>确认出库</h2>
      <p class="muted">先在上方“当前可用规格”中点击“选择出库”。</p>
      <form method="post" action="/admin/paper-materials/outbound" class="form-grid" data-confirm-flow="true" data-confirm-title="确认纸材出库" data-confirm-note="系统会按最早入库批次 FIFO 扣减。">
        <input type="hidden" name="specification_id" value="{selected['specification_id'] if selected else ''}">
        <div><label>纸材型号</label><input value="{safe_value(selected['model'] if selected else '')}" readonly></div>
        <div><label>纸材名称/材质</label><input value="{safe_value(selected['material_name'] if selected else '')}" readonly></div>
        <div><label>尺寸</label><input value="{safe_value(selected['size'] if selected else '')}" readonly></div>
        <div><label>出库数量</label><input name="quantity" type="number" min="1" required></div>
        <div><label>指定库位，可选</label><input name="location" placeholder="不填则所有库位FIFO"></div>
        <div><label>客户/去向</label><input name="customer_name" placeholder="例如 一车间"></div>
        <div><label>操作人</label><input name="operator_name" placeholder="例如 张三"></div>
        <div><label>备注</label><input name="remark" placeholder="例如 生产领用"></div>
        <div style="align-self:end"><button class="btn" type="submit" {'disabled' if not selected else ''}>确认出库</button></div>
      </form>
    </section>
    """
    return page("纸材出库", body)


@router.post("/admin/paper-materials/outbound")
def outbound_paper_from_page(
    specification_id: int = Form(...),
    quantity: int = Form(...),
    location: str = Form(""),
    customer_name: str = Form(""),
    operator_name: str = Form(""),
    remark: str = Form(""),
    _lock=Depends(locked_paper_write),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    records = outbound_paper_fifo(
        specification_id,
        quantity,
        location,
        customer_name,
        operator_name,
        remark,
        db,
    )
    for record in records:
        batch = db.get(PaperInventoryBatch, record.inventory_id)
        record_operation_log(
            db,
            "paper_outbound",
            "paper_inventory",
            batch.id,
            operator_name.strip() or None,
            record.remark,
            after_data=paper_inventory_snapshot(batch),
        )
    db.commit()
    return RedirectResponse("/admin/paper-materials", status_code=303)


@router.get("/admin/paper-materials/transactions", response_class=HTMLResponse)
def paper_transactions_page(
    q: str = "",
    material_name: str = "",
    paper_type: str = "",
    transaction_type: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    query = db.query(PaperInventoryTransaction)
    if transaction_type in ("in", "out"):
        query = query.filter(PaperInventoryTransaction.transaction_type == transaction_type)
    records = query.order_by(PaperInventoryTransaction.created_at.desc(), PaperInventoryTransaction.id.desc()).limit(500).all()
    inventory_ids = [record.inventory_id for record in records]
    batch_map = {
        batch.id: batch
        for batch in db.query(PaperInventoryBatch).filter(PaperInventoryBatch.id.in_(inventory_ids)).all()
    } if inventory_ids else {}
    row_parts = []
    keyword = q.strip().lower()
    for record in records:
        batch = batch_map.get(record.inventory_id)
        if not batch:
            continue
        if paper_type in PAPER_TYPE_LABELS and batch.paper_type != paper_type:
            continue
        if material_name.strip() and material_name.strip() not in batch.material_name:
            continue
        searchable = " ".join(str(value or "") for value in (batch.batch_code, batch.model, batch.material_name, batch.location, record.customer_name, record.operator_name, record.remark)).lower()
        if keyword and keyword not in searchable:
            continue
        reverse_form = "-"
        if record.transaction_type in ("in", "out") and record.reversed_transaction_id is None:
            reverse_form = f"<form method='post' action='/admin/paper-materials/transactions/{record.id}/reverse' class='actions'><input name='operator_name' placeholder='操作人' style='width:90px'><input name='remark' placeholder='撤回原因' style='width:120px'><button class='btn secondary' type='submit'>撤回</button></form>"
        values = (
            batch.batch_code,
            PAPER_TYPE_LABELS[batch.paper_type],
            batch.model,
            batch.material_name,
            paper_batch_size(batch),
            f"¥{batch.unit_price:.2f}",
            record.transaction_type,
            record.quantity,
            record.before_quantity,
            record.after_quantity,
            record.customer_name or "-",
            record.operator_name or "-",
        )
        cells = "".join(f'<td class="nowrap-cell">{html.escape(str(value))}</td>' for value in values)
        row_parts.append(f"<tr>{cells}<td class='remark-cell'>{html.escape(record.remark or '-')}</td><td class='nowrap-cell'>{record.created_at}</td><td class='nowrap-cell'>{reverse_form}</td></tr>")
    rows = "".join(row_parts)
    body = f"""
    <div class="top"><div><h1>纸材流水</h1><p class="muted">查看纸材入库、FIFO 出库和撤回记录。</p></div><div class="actions"><a class="btn secondary" href="/admin/paper-materials">返回纸材库存</a><a class="btn secondary" href="/admin/paper-materials/inbound">纸材入库</a><a class="btn secondary" href="/admin/paper-materials/outbound">纸材出库</a></div></div>
    <section class="card"><form method="get" action="/admin/paper-materials/transactions" class="actions" style="justify-content:flex-start">
      <input name="q" value="{safe_value(q.strip())}" placeholder="批次/型号/客户/操作人" style="width:220px"><input name="material_name" value="{safe_value(material_name.strip())}" placeholder="纸材名称/材质" style="width:170px">
      <select name="paper_type" style="width:120px"><option value="">全部纸材</option><option value="roll" {'selected' if paper_type == 'roll' else ''}>纸圈</option><option value="sheet" {'selected' if paper_type == 'sheet' else ''}>纸张</option></select>
      <select name="transaction_type" style="width:120px"><option value="">全部流水</option><option value="in" {'selected' if transaction_type == 'in' else ''}>入库</option><option value="out" {'selected' if transaction_type == 'out' else ''}>出库</option></select>
      <button class="btn" type="submit">查询流水</button><a class="btn secondary" href="/admin/paper-materials/transactions">清空</a>
    </form></section>
    <section class="card"><table class="wide-transaction-table"><thead><tr><th>批次编号</th><th>纸材类型</th><th>型号</th><th>纸材名称/材质</th><th>尺寸</th><th>单价</th><th>类型</th><th>数量</th><th>操作前</th><th>操作后</th><th>客户/去向</th><th>操作人</th><th>备注</th><th>时间</th><th>操作</th></tr></thead><tbody>{rows or "<tr><td colspan='15'>暂无纸材流水。</td></tr>"}</tbody></table></section>
    """
    return page("纸材流水", body)


@router.post("/admin/paper-materials/transactions/{transaction_id}/reverse")
def reverse_paper_transaction_from_page(
    transaction_id: int,
    operator_name: str = Form(""),
    remark: str = Form(""),
    _lock=Depends(locked_paper_write),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    reversal = reverse_paper_transaction(transaction_id, operator_name, remark, db)
    batch = db.get(PaperInventoryBatch, reversal.inventory_id)
    record_operation_log(
        db,
        "paper_transaction_reverse",
        "paper_inventory",
        batch.id,
        operator_name.strip() or None,
        reversal.remark,
        after_data=paper_inventory_snapshot(batch),
    )
    db.commit()
    return RedirectResponse("/admin/paper-materials/transactions", status_code=303)
