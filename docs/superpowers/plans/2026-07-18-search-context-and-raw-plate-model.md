# 搜索结果参数展示与临时板料型号 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除五类库存/图纸页面的自选排序控件，改为始终展示基础信息并突出本次搜索参数，同时支持临时板料补填独立型号且隐藏零库存记录。

**Architecture:** 新增一个纯函数服务统一生成“命中参数优先、默认参数补足”的摘要，页面层只负责提供各模块字段和值并渲染。板料库存新增独立的 `raw_plate_model` 文本字段，把型号与现有 `material_code` 批次号分开；汇总按“型号 + 材质 + 长宽厚”分组，流水继续按库存批次 ID 追溯。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、SQLite、服务端 HTML、unittest/pytest、Playwright 浏览器验收

## Global Constraints

- 板料规格、图纸、成品、板料、余料页面不再提供自选字段升降序。
- 型号、名称、数量、状态、库位和操作等基础信息不能被搜索参数替换。
- 搜索摘要显示结果记录的真实值，不直接照抄用户输入。
- 临时板料补填型号不得修改材质、长宽厚、数量、库位和历史流水。
- 零库存板料不出现在当前库存页，但仍保留在流水中。
- 桌面端和移动端均不得出现整页横向滚动。
- 不新增第三方依赖，不删除历史库存批次和流水。

---

## File Structure

- Create `app/services/search_context.py`: 参数摘要排序、去重、关键词命中检测的纯函数。
- Create `tests/test_search_context.py`: 参数摘要纯函数单元测试。
- Modify `app/models.py`: 在 `MaterialInventory` 增加 `raw_plate_model`。
- Modify `app/schema_migrations.py`: 为已有数据库幂等增加 `raw_plate_model` 列和索引。
- Modify `app/services/operation_log.py`: 库存快照纳入板料型号。
- Modify `app/services/inventory_summaries.py`: 汇总保存基础信息、默认参数及板料型号，并移除自选排序映射。
- Modify `app/admin_pages.py`: 五类页面移除排序、增加参数摘要、板料型号录入和修改、隐藏零库存板料。
- Modify `app/services/excel_export.py`: 导出忽略旧排序参数并使用确定性默认顺序，板料导出使用独立型号。
- Delete `app/services/list_sorting.py`: 删除已经没有调用方的自选排序服务。
- Delete `tests/test_list_sorting.py`: 删除已取消功能的单元测试。
- Modify `tests/test_inventory_grouping_pages.py`: 覆盖五类页面显示、默认排序、板料型号和零库存行为。
- Modify `tests/test_product_catalog_search.py`: 覆盖图纸/成品/板料/余料搜索参数摘要和默认导出顺序。
- Modify `tests/test_drawing_search_sorting.py`: 将自选排序测试改成旧参数被忽略且仍按自然顺序。
- Modify `tests/test_china_time.py`: 覆盖运行时迁移新增板料型号列的幂等性。

---

### Task 1: 参数摘要纯函数

**Files:**
- Create: `app/services/search_context.py`
- Create: `tests/test_search_context.py`

**Interfaces:**
- Produces: `ParameterValue = tuple[str, str]`
- Produces: `keyword_parameter_matches(keyword: str, values: list[ParameterValue]) -> list[ParameterValue]`
- Produces: `build_parameter_summary(matched: list[ParameterValue], defaults: list[ParameterValue], limit: int = 5) -> list[tuple[str, str, bool]]`
- Consumers: `app/admin_pages.py` 中五类列表页面。

- [ ] **Step 1: 写参数顺序、真实值、去重和关键词命中的失败测试**

```python
import unittest

from app.services.search_context import build_parameter_summary, keyword_parameter_matches


class SearchContextTest(unittest.TestCase):
    def test_matched_parameters_lead_and_defaults_are_deduplicated(self) -> None:
        result = build_parameter_summary(
            [("总成品厚度", "3"), ("材质", "65Mn")],
            [("总成品厚度", "3"), ("钢板厚度", "1.2"), ("材质", "65Mn")],
        )
        self.assertEqual(
            result,
            [("总成品厚度", "3", True), ("材质", "65Mn", True), ("钢板厚度", "1.2", False)],
        )

    def test_empty_values_are_removed_and_limit_is_respected(self) -> None:
        result = build_parameter_summary([], [("材质", ""), ("厚度", "3"), ("长度", "1000")], limit=1)
        self.assertEqual(result, [("厚度", "3", False)])

    def test_keyword_matching_returns_actual_matching_fields(self) -> None:
        result = keyword_parameter_matches("tnx1", [("型号", "TNX10.0A"), ("材质", "65Mn"), ("备注", "样品")])
        self.assertEqual(result, [("型号", "TNX10.0A")])
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `pytest -q tests/test_search_context.py`

Expected: FAIL，包含 `ModuleNotFoundError: No module named 'app.services.search_context'`。

- [ ] **Step 3: 实现最小纯函数服务**

```python
from collections.abc import Iterable


ParameterValue = tuple[str, str]
ParameterDisplay = tuple[str, str, bool]


def _present(values: Iterable[ParameterValue]) -> list[ParameterValue]:
    return [(str(label).strip(), str(value).strip()) for label, value in values if str(label).strip() and str(value).strip()]


def keyword_parameter_matches(keyword: str, values: list[ParameterValue]) -> list[ParameterValue]:
    needle = keyword.strip().casefold()
    if not needle:
        return []
    return [(label, value) for label, value in _present(values) if needle in value.casefold()]


def build_parameter_summary(
    matched: list[ParameterValue],
    defaults: list[ParameterValue],
    limit: int = 5,
) -> list[ParameterDisplay]:
    result: list[ParameterDisplay] = []
    seen: set[str] = set()
    for is_matched, values in ((True, matched), (False, defaults)):
        for label, value in _present(values):
            if label in seen:
                continue
            seen.add(label)
            result.append((label, value, is_matched))
            if len(result) >= limit:
                return result
    return result
```

- [ ] **Step 4: 运行单元测试**

Run: `pytest -q tests/test_search_context.py`

Expected: `3 passed`。

- [ ] **Step 5: 提交参数摘要服务**

```bash
git add app/services/search_context.py tests/test_search_context.py
git commit -m "feat: 增加搜索参数摘要服务"
```

---

### Task 2: 板料型号字段与幂等迁移

**Files:**
- Modify: `app/models.py`
- Modify: `app/schema_migrations.py`
- Modify: `app/services/operation_log.py`
- Modify: `tests/test_china_time.py`

**Interfaces:**
- Produces: `MaterialInventory.raw_plate_model: str | None`
- Produces: 已有数据库启动时自动执行 `ALTER TABLE material_inventory ADD COLUMN raw_plate_model VARCHAR(100)`。
- Consumers: Task 3 的入库、补型号、汇总和导出。

- [ ] **Step 1: 写模型、迁移幂等和审计快照失败测试**

在 `tests/test_china_time.py` 增加：

```python
from sqlalchemy import inspect, text
from app.services.operation_log import inventory_snapshot


def test_raw_plate_model_is_in_inventory_snapshot(self) -> None:
    item = MaterialInventory(
        inventory_type="raw_plate", material_code="BATCH-1", raw_plate_model="MODEL-1",
        material="65Mn", thickness=2, length=1000, width=500,
        shape="rectangle", quantity=3, status="available",
    )
    self.assertEqual(inventory_snapshot(item)["raw_plate_model"], "MODEL-1")


def test_runtime_migration_adds_raw_plate_model_once(self) -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE material_inventory (id INTEGER PRIMARY KEY, created_at DATETIME, updated_at DATETIME)"))
    ensure_runtime_schema(engine)
    ensure_runtime_schema(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("material_inventory")}
    self.assertIn("raw_plate_model", columns)
```

- [ ] **Step 2: 运行测试并确认字段缺失**

Run: `pytest -q tests/test_china_time.py`

Expected: FAIL，分别显示构造参数或快照键 `raw_plate_model` 缺失。

- [ ] **Step 3: 增加模型字段、迁移和快照字段**

在 `MaterialInventory` 中增加：

```python
raw_plate_model: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
```

在 `ensure_runtime_schema()` 的 `material_inventory` 分支增加：

```python
if "raw_plate_model" not in inventory_columns:
    connection.execute(text("ALTER TABLE material_inventory ADD COLUMN raw_plate_model VARCHAR(100)"))
connection.execute(text("CREATE INDEX IF NOT EXISTS ix_material_inventory_raw_plate_model ON material_inventory (raw_plate_model)"))
```

在 `inventory_snapshot()` 返回值增加：

```python
"raw_plate_model": item.raw_plate_model,
```

- [ ] **Step 4: 运行迁移和时间测试**

Run: `pytest -q tests/test_china_time.py`

Expected: 全部 PASS，迁移连续执行两次不报错。

- [ ] **Step 5: 提交数据库字段**

```bash
git add app/models.py app/schema_migrations.py app/services/operation_log.py tests/test_china_time.py
git commit -m "feat: 区分板料型号与批次号"
```

---

### Task 3: 板料入库、补型号、汇总和零库存边界

**Files:**
- Modify: `app/services/inventory_summaries.py`
- Modify: `app/admin_pages.py`
- Modify: `app/services/excel_export.py`
- Modify: `tests/test_inventory_grouping_pages.py`
- Modify: `tests/test_product_catalog_search.py`

**Interfaces:**
- Produces: `resolved_raw_plate_model(item: MaterialInventory, spec_names: dict[tuple, str]) -> str`
- Changes: `raw_plate_summary_rows()` 按 `(型号, 材质, 长, 宽, 厚)` 分组并忽略数量不大于零的记录。
- Changes: `/admin/raw-plates/inbound` 接收 `raw_plate_spec_id` 和 `raw_plate_model`。
- Changes: `/admin/raw-plates/{inventory_id}/edit` 始终允许更新 `raw_plate_model`，即使已有出库流水。
- Consumers: 板料库存页、明细页、Excel 导出。

- [ ] **Step 1: 写板料业务失败测试**

在 `tests/test_inventory_grouping_pages.py` 增加以下两个测试，并把旧的板料自选排序测试留到 Task 5 删除：

```python
def test_raw_plate_summary_prefers_saved_model_and_hides_zero_stock(self) -> None:
    with self.Session() as db:
        db.add_all([
            MaterialInventory(raw_plate_model="MODEL-X", material_code="B1", inventory_type="raw_plate", material="65Mn", thickness=2, length=1000, width=500, shape="rectangle", quantity=3, status="available"),
            MaterialInventory(raw_plate_model=None, material_code="B2", inventory_type="raw_plate", material="Q235", thickness=4, length=2000, width=1000, shape="rectangle", quantity=0, status="used"),
        ])
        db.commit()
        html = raw_plates_page(db=db).body.decode("utf-8")
    self.assertIn("MODEL-X", html)
    self.assertNotIn("B2", html)
    self.assertNotIn("临时规格", html)


def test_raw_plate_model_can_change_without_changing_dimensions_or_quantity(self) -> None:
    with self.Session() as db:
        item = MaterialInventory(material_code="BATCH-1", inventory_type="raw_plate", material="65Mn", thickness=2, length=1000, width=500, shape="rectangle", quantity=3, status="available")
        db.add(item)
        db.flush()
        db.add(InventoryTransactionRecord(inventory_id=item.id, transaction_type="out", quantity=1, before_quantity=4, after_quantity=3))
        db.commit()
        update_raw_plate_from_page(item.id, material_code="BATCH-1", raw_plate_model="MODEL-NEW", material="Q235", length=9, width=9, thickness=9, location="A1", status="available", operator_name="张三", remark="补型号", _lock=None, db=db)
        db.refresh(item)
        self.assertEqual((item.raw_plate_model, item.material, item.length, item.width, item.thickness, item.quantity), ("MODEL-NEW", "65Mn", 1000, 500, 2, 3))
```

另加测试验证选择固定规格入库时保存 `spec.spec_name`，手工入库可保存自定义型号，入库流水数量计算不变。

该测试命名为 `test_raw_plate_inbound_saves_selected_or_manual_model`：先创建 `RawPlateSpecification(spec_name="S-2", material="65Mn", length=1000, width=500, thickness=2, density=7.85)`，分别调用两次 `create_raw_plate_from_page()`；断言第一条库存的 `raw_plate_model == "S-2"`，第二条的 `raw_plate_model == "CUSTOM-2"`，并断言两条 `InventoryTransactionRecord.after_quantity` 都等于对应库存数量。

- [ ] **Step 2: 运行板料相关测试并确认失败**

Run: `pytest -q tests/test_inventory_grouping_pages.py -k "raw_plate"`

Expected: FAIL，显示页面仍展示零库存、汇总没有独立型号或更新函数不接收 `raw_plate_model`。

- [ ] **Step 3: 实现型号解析和按型号汇总**

在 `app/services/inventory_summaries.py` 增加并改造：

```python
def resolved_raw_plate_model(item: MaterialInventory, spec_names: dict[tuple, str]) -> str:
    key = (item.material, item.length, item.width, item.thickness)
    return item.raw_plate_model or spec_names.get(key) or "临时规格"


def raw_plate_summary_rows(items: list[MaterialInventory], spec_names: dict[tuple, str]) -> list[SummaryRow]:
    grouped: dict[tuple, SummaryRow] = {}
    for item in items:
        if item.quantity <= 0:
            continue
        model = resolved_raw_plate_model(item, spec_names)
        key = (model, item.material, item.length, item.width, item.thickness)
        group = grouped.setdefault(key, {"spec_name": model, "material": item.material, "length": item.length, "width": item.width, "thickness": item.thickness, "quantity": 0, "batch_count": 0, "locations": set(), "latest": _latest_time(item)})
        group["quantity"] += item.quantity
        group["batch_count"] += 1
        if item.location:
            group["locations"].add(item.location)
        group["latest"] = max(group["latest"], _latest_time(item))
    return list(grouped.values())
```

- [ ] **Step 4: 保存入库型号并允许后补型号**

将入库规格 `<select>` 改为 `name="raw_plate_spec_id"`，型号输入框使用 `name="raw_plate_model"`。提交时，若 `raw_plate_spec_id` 有效，服务端以规格主数据覆盖材质、长宽厚、密度并保存 `spec.spec_name`；否则保存清理后的手工型号或 `None`：

```python
selected_spec = db.get(RawPlateSpecification, int(raw_plate_spec_id)) if raw_plate_spec_id.isdigit() else None
if selected_spec and selected_spec.is_active:
    raw_plate_model_value = selected_spec.spec_name
    material, length, width, thickness, density = selected_spec.material, selected_spec.length, selected_spec.width, selected_spec.thickness, selected_spec.density
else:
    raw_plate_model_value = raw_plate_model.strip() or None
item = MaterialInventory(
    material_code=batch_code,
    raw_plate_model=raw_plate_model_value,
    inventory_type="raw_plate",
    material=material,
    thickness=thickness,
    shape="rectangle",
    length=length,
    width=width,
    usable_size=f"{length:g}×{width:g}×{thickness:g}mm",
    quantity=quantity,
    location=location_value,
    status="available",
)
```

修改页面增加必填的“板料型号”输入框；值使用已保存型号，旧数据则使用精确匹配的规格名。更新前禁止修改零库存批次，更新处理始终执行：

```python
model_value = raw_plate_model.strip()
if item.quantity <= 0:
    raise HTTPException(status_code=400, detail="零库存板料不能补填型号")
if not model_value:
    raise HTTPException(status_code=400, detail="板料型号不能为空")
if len(model_value) > 100:
    raise HTTPException(status_code=400, detail="板料型号不能超过100个字符")
item.raw_plate_model = model_value
```

材质和尺寸仍遵循原有“有出库流水则锁定”的分支，不能被补型号请求改动。

- [ ] **Step 5: 让当前库存和导出隐藏零库存并按型号进入明细**

板料库存查询增加 `MaterialInventory.quantity > 0`，默认排序使用：

```python
grouped_rows.sort(key=lambda row: (natural_sort_key(row["spec_name"]), natural_sort_key(row["material"]), row["thickness"] or 0))
```

明细链接增加 `model`，明细页按尺寸查询历史批次后，再用 `resolved_raw_plate_model()` 过滤相同型号。当前批次区可列出历史零库存批次，顶部当前数量只累加正库存；全零型号不会从当前库存页获得入口，但流水页仍然查询原记录。

Excel 板料库存复用相同汇总和默认排序，忽略 `sort_by`、`sort_dir`。

- [ ] **Step 6: 运行板料和导出测试**

Run: `pytest -q tests/test_inventory_grouping_pages.py tests/test_product_catalog_search.py -k "raw_plate or inventory_export"`

Expected: 全部 PASS。

- [ ] **Step 7: 提交板料型号流程**

```bash
git add app/admin_pages.py app/services/inventory_summaries.py app/services/excel_export.py tests/test_inventory_grouping_pages.py tests/test_product_catalog_search.py
git commit -m "feat: 支持临时板料补填型号"
```

---

### Task 4: 五类页面固定基础信息与自适应参数摘要

**Files:**
- Modify: `app/admin_pages.py`
- Modify: `app/services/inventory_summaries.py`
- Modify: `tests/test_inventory_grouping_pages.py`
- Modify: `tests/test_product_catalog_search.py`

**Interfaces:**
- Consumes: Task 1 的 `build_parameter_summary()` 和 `keyword_parameter_matches()`。
- Produces: `render_parameter_summary(matched, defaults) -> str` 页面 HTML helper。
- Changes: 图纸、成品、板料、余料、板料规格都使用固定基础列和一个“参数信息”列。

- [ ] **Step 1: 写五类页面参数展示失败测试**

测试数据为每类记录提供两个可区分参数，断言固定信息仍在、搜索参数真实值在 `.parameter-line.matched` 中、无搜索时默认参数仍显示。例如：

```python
product_html = inventory_page(product_thickness="3", db=db).body.decode("utf-8")
self.assertIn("<th>产品型号</th>", product_html)
self.assertIn("<th>库存数量</th>", product_html)
self.assertIn("<th>参数信息</th>", product_html)
self.assertIn('<span class="parameter-line matched"><strong>总成品厚度</strong> 3</span>', product_html)
self.assertIn("钢板厚度", product_html)

raw_html = raw_plates_page(thickness="2", db=db).body.decode("utf-8")
self.assertIn('<span class="parameter-line matched"><strong>厚度</strong> 2</span>', raw_html)

scrap_html = scraps_page(material="65Mn", db=db).body.decode("utf-8")
self.assertIn('<span class="parameter-line matched"><strong>材质</strong> 65Mn</span>', scrap_html)

spec_html = raw_plate_specifications_page(thickness="2", db=db).body.decode("utf-8")
self.assertIn('<span class="parameter-line matched"><strong>厚度</strong> 2</span>', spec_html)
```

图纸测试更新为普通关键词也显示实际命中字段，例如 `q="DYN"` 显示“产品编号 DYN-1”，同时状态仍单独显示“已确认”。

- [ ] **Step 2: 运行页面测试并确认参数信息缺失**

Run: `pytest -q tests/test_inventory_grouping_pages.py tests/test_product_catalog_search.py -k "parameter or inventory or specification"`

Expected: FAIL，页面仍使用固定的多参数列或搜索时替换状态列。

- [ ] **Step 3: 增加统一 HTML 渲染 helper 和样式**

在 `app/admin_pages.py` 增加：

```python
def render_parameter_summary(matched: list[tuple[str, str]], defaults: list[tuple[str, str]]) -> str:
    lines = "".join(
        f'<span class="parameter-line{" matched" if is_matched else ""}"><strong>{html.escape(label)}</strong> {html.escape(value)}</span>'
        for label, value, is_matched in build_parameter_summary(matched, defaults)
    )
    return f'<div class="parameter-lines">{lines or "-"}</div>'
```

CSS 增加：

```css
.parameter-line.matched { color:var(--primary); font-weight:700; border-left:3px solid var(--primary); padding-left:7px; }
.parameter-line strong { color:inherit; }
```

- [ ] **Step 4: 改造各模块结果行**

每类记录都准备两组 `(标签, 真实值)`：

- 图纸命中：产品编号、产品名称、分类、材质、备注及所有非空具名参数；默认：总成品厚度、钢板厚度、材质、齿数、模数。
- 成品命中：型号、材质、总成品厚度、钢板厚度、库位；默认：总成品厚度、钢板厚度、材质、纸材质。
- 板料命中：型号、批次、材质、长、宽、厚度、库位；默认：长、宽、厚度、材质。
- 余料命中：来源型号、材质、厚度、可用尺寸、直径、库位；默认：厚度、可用尺寸、材质。
- 板料规格命中：型号、材质、长、宽、厚度；默认：长、宽、厚度、密度。

具名筛选字段非空时直接加入命中组；普通关键词通过 `keyword_parameter_matches()` 对当前记录候选值检测。结果表固定保留数量、状态、库位、操作列，并把原来多个参数列合并成“参数信息”。所有移动端单元格补齐 `data-label`。

成品汇总增加当前有效图纸名称映射，按型号补充 `group["name"]`；余料汇总增加 `source_codes` 集合，作为固定“来源型号”信息。

成品页面把原来的含义不明确的 `thickness` 搜索输入拆为 `product_thickness` 和 `plate_thickness`。查询分别过滤 `MaterialInventory.product_thickness` 与 `MaterialInventory.plate_thickness`，旧 `thickness` 参数只作为兼容别名且不同时匹配两个字段；页面和导出链接只生成两个明确参数。函数签名为：

```python
def inventory_page(
    q: str = "",
    inventory_type: str = "",
    status: str = "",
    material: str = "",
    product_thickness: str = "",
    plate_thickness: str = "",
    thickness: str = "",
    location: str = "",
    sort_by: str = "",
    sort_dir: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
```

其中兼容 `thickness` 只过滤旧的 `MaterialInventory.thickness`；新页面不再提交该参数，避免总成品厚度和钢板厚度混查。

- [ ] **Step 5: 增加板料规格搜索而不是排序**

`raw_plate_specifications_page()` 接收 `q`、`material`、`length`、`width`、`thickness`，应用与其他列表一致的文本/数值过滤，表单只保留搜索、清空按钮。默认按 `is_active desc` 后使用型号自然排序，不显示自选排序控件。

- [ ] **Step 6: 运行参数摘要测试**

Run: `pytest -q tests/test_search_context.py tests/test_inventory_grouping_pages.py tests/test_product_catalog_search.py`

Expected: 全部 PASS。

- [ ] **Step 7: 提交列表展示改造**

```bash
git add app/admin_pages.py app/services/inventory_summaries.py tests/test_inventory_grouping_pages.py tests/test_product_catalog_search.py
git commit -m "feat: 搜索后突出显示命中参数"
```

---

### Task 5: 删除自选排序实现并固定导出顺序

**Files:**
- Modify: `app/admin_pages.py`
- Modify: `app/services/drawing_search.py`
- Modify: `app/services/inventory_summaries.py`
- Modify: `app/services/excel_export.py`
- Delete: `app/services/list_sorting.py`
- Delete: `tests/test_list_sorting.py`
- Modify: `tests/test_drawing_search_sorting.py`
- Modify: `tests/test_inventory_grouping_pages.py`
- Modify: `tests/test_product_catalog_search.py`

**Interfaces:**
- Changes: 旧 `sort_by`/`sort_dir` 查询参数由 FastAPI 忽略；所有页面和导出始终使用确定性默认顺序。
- Removes: `sort_records()`、`sort_select_options()` 和四个 `*_sort_key_map()`。

- [ ] **Step 1: 把排序测试改为“控件消失、旧参数无效、默认顺序稳定”**

```python
def test_confirmed_drawings_ignore_legacy_sort_parameters(self) -> None:
    with self.Session() as db:
        self.add_drawings(db)
        html = confirmed_drawings_page(sort_by="product_code", sort_dir="desc", db=db).body.decode("utf-8")
    self.assertNotIn('name="sort_by"', html)
    self.assertNotIn('name="sort_dir"', html)
    self.assertLess(html.index(">A2</td>"), html.index(">A10</td>"))
    self.assertLess(html.index(">A10</td>"), html.index(">B1</td>"))
```

分别为成品、板料、余料、板料规格页面断言 `name="sort_by"` 和 `name="sort_dir"` 不存在。导出测试对 `product_inventory`、`raw_plate_inventory`、`scrap_inventory`、`product_catalog` 分别传入 `{"sort_by": "quantity", "sort_dir": "desc"}`，断言返回顺序仍为各模块默认自然顺序。

- [ ] **Step 2: 运行排序相关测试并确认失败**

Run: `pytest -q tests/test_drawing_search_sorting.py tests/test_inventory_grouping_pages.py tests/test_product_catalog_search.py -k "sort or legacy"`

Expected: FAIL，当前页面仍显示排序控件且旧参数仍改变顺序。

- [ ] **Step 3: 删除页面排序控件和服务调用**

从五个页面的表单、导出链接和内部逻辑中删除 `sort_by`/`sort_dir`，删除 `sort_records()` 调用。函数签名保留这两个可选参数但不读取，以兼容旧书签和现有直接调用；页面不再产生排序参数。保留默认排序：

```python
drawings = sorted(query.all(), key=lambda drawing: (natural_sort_key(drawing.product_code), -(drawing.version or 1)))
product_groups.sort(key=lambda row: natural_sort_key(row["code"]))
raw_groups.sort(key=lambda row: (natural_sort_key(row["spec_name"]), natural_sort_key(row["material"])))
scrap_groups.sort(key=lambda row: (natural_sort_key(row["material"]), row["thickness"] or 0, natural_sort_key(row["usable_size"])))
specs.sort(key=lambda spec: (-int(bool(spec.is_active)), natural_sort_key(spec.spec_name)))
```

同时删除仅服务于该功能的 `.sort-controls` CSS；FastAPI 旧书签参数仍会被兼容参数接收，但不会改变结果。

- [ ] **Step 4: 删除无调用方代码并固定导出顺序**

删除 `app/services/list_sorting.py`、`tests/test_list_sorting.py`、`drawing_sort_key_map()`、三个汇总排序映射和相关 imports。`excel_export.py` 对产品按 `natural_sort_key(row["code"])`、板料按型号/材质自然顺序、余料按材质/厚度/尺寸、图纸按产品编号/版本直接 `sorted()`，不读取过滤字典中的旧排序值。

- [ ] **Step 5: 运行排序回归测试和静态搜索**

Run: `pytest -q tests/test_drawing_search_sorting.py tests/test_inventory_grouping_pages.py tests/test_product_catalog_search.py`

Expected: 全部 PASS。

Run: `rg -n "sort_by|sort_dir|sort_records|sort_select_options|排序参数|排序方式" app tests`

Expected: 无 `sort_records`、`sort_select_options`、`name="sort_by"`、`name="sort_dir"`；只允许分析报表中与本需求无关的固定业务顺序代码存在。

- [ ] **Step 6: 提交排序功能删除**

```bash
git add -A app tests
git commit -m "refactor: 删除库存与图纸自选排序"
```

---

### Task 6: 全量回归、响应式验收、提交与重启

**Files:**
- Modify: 仅修复本轮验证发现的直接问题。

**Interfaces:**
- Verifies: 数据迁移、页面搜索、库存汇总、导出、流水撤回和响应式布局作为一个完整流程工作。

- [ ] **Step 1: 运行格式与全量测试**

Run: `git diff --check && pytest -q`

Expected: `git diff --check` 无输出；全部测试 PASS。

- [ ] **Step 2: 在临时数据库启动服务并执行浏览器验收**

Run: `DATABASE_URL=sqlite:////tmp/warehouse-search-context.db uvicorn app.main:app --host 127.0.0.1 --port 8011`

使用 Playwright 分别检查 `1440x900`、`1024x768`、`390x844`：

- `/admin/drawings/confirmed`
- `/admin/inventory`
- `/admin/raw-plate-specifications`
- `/admin/raw-plates`
- `/admin/scraps`

每页验证 `document.documentElement.scrollWidth <= document.documentElement.clientWidth`，排序控件不存在，搜索后命中参数突出显示，基础信息与操作按钮可见。

- [ ] **Step 3: 验证真实数据库迁移前备份路径和启动行为**

Run: `python -c "from app.database import engine; from app.schema_migrations import ensure_runtime_schema; ensure_runtime_schema(engine); print('schema ok')"`

Expected: 输出 `schema ok`，再次执行结果相同。

- [ ] **Step 4: 检查提交范围并提交验证中必要修复**

Run: `git status --short && git diff --check`

Expected: 工作区为空；如果本轮浏览器验收发现并修复了直接问题，使用：

```bash
git add app tests
git commit -m "fix: 完善搜索参数与板料型号回归"
```

- [ ] **Step 5: 重启项目服务并确认健康状态**

先读取现有项目进程，只重启本仓库占用的端口；不得停止其他项目的 `8000` 或 `5173`。启动后请求本项目首页和五类页面，预期均返回 HTTP 200。

- [ ] **Step 6: 推送 Git**

Run: `git push origin main`

Expected: `origin/main` 更新到本轮最后一个提交，`git status --short` 为空。
