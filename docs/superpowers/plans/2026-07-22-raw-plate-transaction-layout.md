# 板料流水可读性改造实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将板料流水从 13 列横向表格改成无需横向滚动的记录列表，并统一按“厚度×宽×长mm”显示尺寸。

**Architecture:** 保留现有 FastAPI 路由、SQLAlchemy 查询、筛选和撤回服务，只重组 `raw_plate_transactions_page` 生成的服务端 HTML。尺寸优先由 `MaterialInventory` 的结构化尺寸通过现有 `steel_spec_name()` 生成，记录列表使用页面现有的内联 CSS 响应式布局。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、服务端 HTML、原生 CSS、`unittest`、Playwright 浏览器验收。

## Global Constraints

- 不改变流水数据结构、查询条件、最多 500 条限制、Excel 导出格式或撤回业务规则。
- 不修改纸材、成品或余料流水页面。
- 不引入新的前端框架或第三方样式库。
- 桌面端和窄屏均不得因板料流水记录产生横向滚动。
- 标准尺寸固定使用“厚度×宽×长mm”，厚度保留一位小数。

---

### Task 1: 用测试定义板料流水记录结构与尺寸规则

**Files:**
- Modify: `tests/test_steel_material_management.py:145-178`

**Interfaces:**
- Consumes: `raw_plate_transactions_page(q: str = "", material: str = "", transaction_type: str = "", db: Session) -> HTMLResponse`
- Produces: 页面结构、尺寸、库存变化、备注与撤回表单的回归契约。

- [ ] **Step 1: 把旧宽表格测试改成记录列表测试**

在现有测试库存中故意保留旧格式 `usable_size="1140×145×2.3mm"`，并给流水增加长备注和可撤回状态：

```python
def test_steel_transactions_render_readable_records_without_horizontal_table(self) -> None:
    with self.Session() as db:
        item = MaterialInventory(
            material_code="RAW-202607210001",
            inventory_type="raw_plate",
            material="50#",
            thickness=2.3,
            length=1140,
            width=145,
            usable_size="1140×145×2.3mm",
            shape="rectangle",
            quantity=200,
            status="available",
        )
        db.add(item)
        db.flush()
        db.add(
            InventoryTransactionRecord(
                inventory_id=item.id,
                transaction_type="in",
                quantity=200,
                before_quantity=0,
                after_quantity=200,
                operator_name="李乙",
                remark="板料入库；总重量 260.48kg；单块重量 4.13kg；入库 200 块。",
            )
        )
        db.commit()

        transaction_html = raw_plate_transactions_page(db=db).body.decode("utf-8")

    self.assertNotIn('wide-transaction-table', transaction_html)
    self.assertIn('class="transaction-record-list"', transaction_html)
    self.assertIn('class="transaction-record"', transaction_html)
    self.assertIn("RAW-202607210001", transaction_html)
    self.assertIn("2.3×145×1140mm", transaction_html)
    self.assertNotIn("1140×145×2.3mm", transaction_html)
    self.assertIn("0 → 200", transaction_html)
    self.assertIn('class="transaction-note"', transaction_html)
    self.assertIn("板料入库；总重量", transaction_html)
    self.assertIn('name="operator_name"', transaction_html)
    self.assertIn('name="remark"', transaction_html)
```

- [ ] **Step 2: 运行测试并确认按预期失败**

Run:

```bash
.venv/bin/python -m unittest tests.test_steel_material_management.SteelMaterialPagesTest.test_steel_transactions_render_readable_records_without_horizontal_table -v
```

Expected: FAIL，原因是页面仍包含 `wide-transaction-table`，且没有 `transaction-record-list`。

- [ ] **Step 3: 提交失败测试前确认工作区只包含计划内文件**

Run:

```bash
git diff --check
git status --short
```

Expected: 只有 `tests/test_steel_material_management.py` 被修改，没有空白错误。

---

### Task 2: 实现结构化尺寸和可读流水记录

**Files:**
- Modify: `app/admin_pages.py:976-983`
- Modify: `app/admin_pages.py:2768-2847`
- Test: `tests/test_steel_material_management.py`

**Interfaces:**
- Consumes: `steel_spec_name(thickness: float, width: float, length: float) -> str`、`transaction_label(transaction_type: str) -> str`、现有撤回 POST 路由。
- Produces: `.transaction-record-list`、`.transaction-record`、`.transaction-record-main`、`.transaction-record-detail`、`.transaction-note` 和 `.transaction-reverse` 页面结构。

- [ ] **Step 1: 增加结构化板料尺寸显示辅助函数**

在 `raw_plate_transactions_page` 前加入：

```python
def raw_plate_transaction_size(item: MaterialInventory) -> str:
    if item.thickness is not None and item.width is not None and item.length is not None:
        return f"{steel_spec_name(item.thickness, item.width, item.length)}mm"
    return item.usable_size or "—"
```

该函数保证历史记录优先使用结构化字段，只有字段缺失时才回退到旧文本。

- [ ] **Step 2: 将 13 列表格行改为语义化记录块**

在循环内替换 `nowrap_values`、`nowrap_cells` 和 `<tr>` 拼接，生成如下结构：

```python
type_class = {"in": "is-in", "out": "is-out", "confirm": "is-confirm"}.get(record.transaction_type, "")
created_at = record.created_at.strftime("%Y-%m-%d %H:%M") if record.created_at else "—"
size_text = raw_plate_transaction_size(item)
rows += f"""
<article class="transaction-record">
  <div class="transaction-record-main">
    <div class="transaction-identity">
      <span class="transaction-kind {type_class}">{html.escape(transaction_label(record.transaction_type))}</span>
      <div><span class="transaction-label">批次编号</span><strong>{html.escape(item.material_code or "—")}</strong></div>
    </div>
    <div class="transaction-core">
      <div><span class="transaction-label">材质</span><strong>{html.escape(item.material or "—")}</strong></div>
      <div class="transaction-size"><span class="transaction-label">尺寸</span><strong>{html.escape(size_text)}</strong></div>
      <div><span class="transaction-label">变动数量</span><strong class="transaction-number">{record.quantity}</strong></div>
      <div><span class="transaction-label">库存变化</span><strong class="transaction-number">{record.before_quantity} → {record.after_quantity}</strong></div>
      <div><span class="transaction-label">时间</span><strong>{created_at}</strong></div>
    </div>
  </div>
  <div class="transaction-record-detail">
    <div class="transaction-meta"><span>库位</span><strong>{html.escape(item.location or "—")}</strong></div>
    <div class="transaction-meta"><span>客户/去向</span><strong>{html.escape(record.customer_name or "—")}</strong></div>
    <div class="transaction-meta"><span>操作人</span><strong>{html.escape(record.operator_name or "—")}</strong></div>
    <div class="transaction-note"><span>备注</span><p>{html.escape(record.remark or "—")}</p></div>
    <div class="transaction-reverse">{reverse_form}</div>
  </div>
</article>
"""
```

将列表容器替换为：

```html
<section class="transaction-record-list">
  {rows or "<div class='empty-state'>暂无板料流水。</div>"}
</section>
```

- [ ] **Step 3: 添加响应式 CSS 并移除板料宽表专用规则**

保留其他页面通用表格样式，删除 `.table-scroll table.wide-transaction-table`、`.wide-transaction-table .nowrap-cell` 和 `.wide-transaction-table .remark-cell`，新增：

```css
.transaction-record-list { display:grid; gap:14px; }
.transaction-record { border:1px solid var(--line); border-radius:16px; background:#fff; overflow:hidden; }
.transaction-record-main { display:grid; grid-template-columns:minmax(230px, .8fr) minmax(0, 2.2fr); gap:24px; padding:20px 22px; align-items:center; }
.transaction-identity { display:flex; gap:14px; align-items:center; min-width:0; }
.transaction-kind { flex:0 0 auto; min-width:58px; padding:7px 10px; border-radius:8px; text-align:center; font-weight:800; }
.transaction-kind.is-in { color:#166534; background:#dcfce7; }
.transaction-kind.is-out { color:#9f1239; background:#ffe4e6; }
.transaction-kind.is-confirm { color:#1d4ed8; background:#dbeafe; }
.transaction-core { display:grid; grid-template-columns:minmax(90px,.55fr) minmax(180px,1.25fr) minmax(90px,.6fr) minmax(130px,.85fr) minmax(140px,.9fr); gap:18px; align-items:center; }
.transaction-label,.transaction-meta > span,.transaction-note > span { display:block; margin-bottom:5px; color:var(--muted); font-size:12px; font-weight:700; }
.transaction-number { font-variant-numeric:tabular-nums; white-space:nowrap; }
.transaction-record-detail { display:grid; grid-template-columns:minmax(90px,.5fr) minmax(120px,.7fr) minmax(100px,.6fr) minmax(260px,2fr) minmax(190px,auto); gap:18px; padding:15px 22px 18px; border-top:1px solid var(--line); background:#fbfcff; align-items:start; }
.transaction-note p { margin:0; line-height:1.65; overflow-wrap:anywhere; }
.transaction-reverse form { display:grid; grid-template-columns:90px minmax(110px,1fr) auto; gap:8px; }
.transaction-reverse input { width:100% !important; min-width:0; }
@media (max-width:1100px) {
  .transaction-record-main { grid-template-columns:1fr; }
  .transaction-core { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .transaction-record-detail { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .transaction-note,.transaction-reverse { grid-column:1/-1; }
}
@media (max-width:680px) {
  .transaction-core,.transaction-record-detail { grid-template-columns:1fr 1fr; }
  .transaction-size,.transaction-note,.transaction-reverse { grid-column:1/-1; }
  .transaction-reverse form { grid-template-columns:1fr; }
}
```

- [ ] **Step 4: 运行目标测试并确认通过**

Run:

```bash
.venv/bin/python -m unittest tests.test_steel_material_management.SteelMaterialPagesTest.test_steel_transactions_render_readable_records_without_horizontal_table -v
```

Expected: PASS。

- [ ] **Step 5: 运行板料管理测试文件**

Run:

```bash
.venv/bin/python -m unittest tests.test_steel_material_management -v
```

Expected: 全部 PASS。

- [ ] **Step 6: 提交实现**

Run:

```bash
git add app/admin_pages.py tests/test_steel_material_management.py
git commit -m "fix: make raw plate transactions readable"
```

Expected: 生成仅包含板料流水布局和回归测试的提交。

---

### Task 3: 浏览器验收、完整测试与服务更新

**Files:**
- Inspect: `app/admin_pages.py`
- Test: `tests/test_steel_material_management.py`

**Interfaces:**
- Consumes: `GET /admin/raw-plates/transactions`。
- Produces: 桌面和窄屏的可读性验收证据，以及运行在本机库存服务上的最新页面。

- [ ] **Step 1: 运行静态检查和完整自动化测试**

Run:

```bash
git diff --check
.venv/bin/python -m compileall -q app tests
.venv/bin/python -m unittest discover -s tests -v
```

Expected: 无空白错误、编译成功、全部测试 PASS。

- [ ] **Step 2: 使用 Playwright 验收真实页面**

使用临时 SQLite 数据库启动应用，插入包含长备注的板料流水，在 1440px 和 390px 视口检查：

```text
页面 scrollWidth <= clientWidth
不存在 wide-transaction-table
批次 RAW-202607210001 完整可见
尺寸 2.3×145×1140mm 完整可见
库存变化 0 → 200 完整可见
长备注完整换行
撤回操作人、撤回原因和按钮均在记录边界内
```

Expected: 两种视口全部通过并保存截图用于目视复核。

- [ ] **Step 3: 更新正在运行的库存服务并验证 HTTP**

应用当前使用 `--reload` 运行在 `127.0.0.1:8001`，代码保存后等待重载完成，再运行：

```bash
curl --fail --silent --show-error --output /dev/null --write-out '%{http_code}\n' http://127.0.0.1:8001/admin/raw-plates/transactions
```

Expected: `200`，服务日志无启动错误。

- [ ] **Step 4: 确认仓库状态和提交记录**

Run:

```bash
git status --short
git log --oneline --decorate -4
```

Expected: 工作区干净，最新提交为 `fix: make raw plate transactions readable`。
