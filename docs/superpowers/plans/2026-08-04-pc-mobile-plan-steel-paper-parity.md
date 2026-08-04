# PC 与小程序计划、钢板、纸材功能同步 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐小程序计划查料、钢板管理和纸材管理，并让 PC 后台与小程序共用同一数据库、同一业务服务和同一事务规则。

**Architecture:** 将当前散落在 PC HTML 路由中的计划匹配、钢板规格/库存和纸材规格/库存逻辑收敛到三个服务模块；PC 路由只负责表单适配与 HTML 响应，移动路由只负责 JSON 校验、幂等记录和序列化。小程序每次进入页面从移动 API 读取最新数据，每个写操作使用确认清单和持久 `client_request_id`。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、Pydantic、SQLite、pytest/unittest、微信小程序原生 WXML/WXSS/JavaScript、Node.js 测试、微信开发者工具 CLI。

## Global Constraints

- 保持现有 PC URL、表单字段、重定向目标和页面结构兼容。
- PC 与小程序不得复制计划匹配、重量换算、FIFO、撤销和规格校验规则。
- 服务函数完成校验和数据库对象变更，但不自行 `commit()`；调用路由负责把业务变更、移动幂等记录和操作日志放进同一事务提交。
- 钢板与纸材出库必须先验证总库存，再逐批扣减；不足时不得产生批次变更、流水或操作日志。
- 所有移动 POST/PUT 请求必须带 `client_request_id`；同编号同负载返回首次响应，同编号不同负载返回 409。
- 规格停用只阻止后续入库，不隐藏或改写历史库存与流水。
- 小程序写页面必须使用 `confirm-sheet`、`createPendingRequestTracker`、`retryPendingWrite` 和 `.complete()`。
- 不修改或提交用户已有文件 `docs/superpowers/plans/2026-08-04-wechat-mini-program-phase-1-foundation.md`。

---

## Task 1: 建立计划查料共用服务

**Files:**

- Create: `app/services/plan_material_service.py`
- Modify: `app/services/drawing_search.py`
- Create: `tests/test_plan_material_service.py`
- Modify: `app/admin_pages.py`
- Test: `tests/test_drawing_search_sorting.py`

- [ ] **Step 1: 写计划筛选与匹配失败测试**

在 `tests/test_plan_material_service.py` 建立内存数据库，创建一张已确认图纸、成品批次、匹配/不匹配余料和钢板，覆盖：

```python
drawings = list_plan_drawings(db, q="TNX", material="65Mn", thickness="1.2")
self.assertEqual([item.product_code for item in drawings], ["TNX-001"])

result = match_plan_materials(db, drawing_id=drawing.id, quantity=6)
self.assertEqual(result["requested_quantity"], 6)
self.assertEqual(result["product"]["quantity"], 2)
self.assertEqual(result["scrap"]["quantity"], 6)
self.assertEqual(result["recommendation_code"], "scrap")
self.assertEqual([row["id"] for row in result["scrap"]["batches"]], [scrap.id])
```

再断言数量小于 1 返回 400、图纸不存在返回 404、查询不会修改任何库存数量。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_plan_material_service.py tests/test_drawing_search_sorting.py`

Expected: 因 `app.services.plan_material_service` 不存在而失败。

- [ ] **Step 3: 实现统一的计划服务接口**

先把 `admin_pages.py` 中通用的 `float_between_filter()` 和 `apply_drawing_filters()` 移到 `app/services/drawing_search.py`，再由原 PC 图纸列表和新计划服务共同导入，避免服务层反向导入页面路由。然后在 `app/services/plan_material_service.py` 提供下列稳定接口，并复用 `material_matching` 中的尺寸匹配函数：

导出的查询接口为 `list_plan_drawings(db: Session, *, q: str = "", material: str = "", thickness: str = "", outer_diameter: str = "", inner_diameter: str = "", teeth_count: str = "") -> list[ProductDrawing]`；匹配接口为 `match_plan_materials(db: Session, *, drawing_id: int, quantity: int) -> dict[str, object]`。

核心校验和建议分支写成明确的顺序：

```python
if quantity <= 0:
    raise HTTPException(status_code=400, detail="计划数量必须大于0")
drawing = db.get(ProductDrawing, drawing_id)
if not drawing or drawing.confirmed != 1 or drawing.is_active != 1:
    raise HTTPException(status_code=404, detail="已确认图纸不存在")

if product_total >= quantity:
    recommendation_code = "product"
elif scrap_total >= quantity:
    recommendation_code = "scrap"
elif raw_plate_total > 0:
    recommendation_code = "raw_plate"
else:
    recommendation_code = "purchase"
```

`match_plan_materials()` 返回 `drawing`、`requested_quantity`、`product`、`scrap`、`raw_plate`、`recommendation_code` 和中文 `recommendation`。每类库存对象包含 `quantity`、`batch_count`、`enough`、`batches`；建议优先级固定为 `product → scrap → raw_plate → purchase`。

- [ ] **Step 4: 让 PC 计划页调用共用服务**

保留 `filtered_plan_drawings()` 作为兼容包装，但内部只转发：

```python
def filtered_plan_drawings(db: Session, **filters) -> list[ProductDrawing]:
    return list_plan_drawings(db, **filters)
```

将 `/admin/plans` 中的库存汇总和建议计算替换为 `match_plan_materials()` 的结果，不改变现有 HTML 文案、查询参数或链接。

- [ ] **Step 5: 运行计划相关测试**

Run: `pytest -q tests/test_plan_material_service.py tests/test_drawing_search_sorting.py tests/test_admin_navigation_and_drawing_confirm.py`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/services/plan_material_service.py app/services/drawing_search.py app/admin_pages.py tests/test_plan_material_service.py
git commit -m "refactor: share plan material matching"
```

---

## Task 2: 建立钢板规格与库存共用服务

**Files:**

- Create: `app/services/raw_plate_inventory.py`
- Create: `tests/test_raw_plate_inventory_service.py`
- Modify: `app/admin_pages.py`
- Test: `tests/test_steel_material_management.py`
- Test: `tests/test_inventory_grouping_pages.py`

- [ ] **Step 1: 写规格、重量换算和 FIFO 失败测试**

测试下列服务契约：

```python
spec = create_raw_plate_specification(
    db, material="65Mn", length=1270, width=130,
    thickness=3.0, density=7.85, remark="常用"
)
self.assertEqual(spec.spec_name, "3.0×130×1270")

receipt = inbound_raw_plate(
    db, specification_id=spec.id, total_weight_ton=1.0,
    location="A-01", operator_name="张三", remark="采购"
)
self.assertEqual(receipt["quantity"], 257)
self.assertAlmostEqual(receipt["single_weight_kg"], 3.888105)

result = outbound_raw_plate_fifo(
    db, material="65Mn", length=1270, width=130,
    thickness=3.0, quantity=8, location="",
    customer_name="一车间", operator_name="李四", remark="领料"
)
self.assertEqual([row["quantity"] for row in result["allocations"]], [5, 3])
```

同时覆盖：非正尺寸、重复规格、停用规格入库、小于一块的重量、批次更新操作日志、指定库位 FIFO、库存不足零变更、入/出库撤销和重复撤销拒绝。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_raw_plate_inventory_service.py`

Expected: 因服务模块不存在而失败。

- [ ] **Step 3: 实现钢板服务**

提供下列精确接口：

- `list_raw_plate_specifications(db, *, q="", material="", length="", width="", thickness="")`
- `create_raw_plate_specification(db, *, material, length, width, thickness, density, remark="")`
- `update_raw_plate_specification(db, spec_id, *, material, length, width, thickness, density, is_active, remark="")`
- `toggle_raw_plate_specification(db, spec_id)`
- `list_raw_plate_groups(db, *, q="", material="", length="", width="", thickness="", location="")`
- `list_raw_plate_batches(db, *, q="", material="", length="", width="", thickness="", location="")`
- `update_raw_plate_batch(db, batch_id, *, raw_plate_model, material_code, material, length, width, thickness, location, status, operator_name="", remark="")`
- `inbound_raw_plate(db, *, specification_id, raw_plate_model, material_code, material, total_weight_ton, length, width, thickness, density, location="", operator_name="", remark="")`
- `outbound_raw_plate_fifo(db, *, material, length, width, thickness, quantity, location="", customer_name="", operator_name="", remark="")`
- `list_raw_plate_transactions(db, *, q="", material="", transaction_type="")`
- `reverse_raw_plate_transaction(db, transaction_id, *, operator_name="", remark="")`

重量换算必须严格使用：

```python
single_weight_kg = length * width * thickness * density / 1_000_000
quantity = math.floor(total_weight_ton * 1000 / single_weight_kg)
remaining_weight_kg = total_weight_ton * 1000 - quantity * single_weight_kg
```

FIFO 查询按 `created_at.asc(), id.asc()`；先求 `available_quantity`，不足立即抛 400，再创建逐批 `InventoryTransactionRecord` 和操作日志。

- [ ] **Step 4: 将 PC 钢板路由改为薄适配层**

把规格新增/修改/启停、批次修改、入库、出库、流水和撤销路由改为调用服务。路由继续接收现有必填和可选 `Form` 字段，并在服务成功后执行一次 `db.commit()`；异常沿用 FastAPI 状态码。

- [ ] **Step 5: 运行钢板回归测试**

Run: `pytest -q tests/test_raw_plate_inventory_service.py tests/test_steel_material_management.py tests/test_inventory_grouping_pages.py`

Expected: PASS，且现有 PC 页面排序和文案断言不变。

- [ ] **Step 6: 提交**

```bash
git add app/services/raw_plate_inventory.py app/admin_pages.py tests/test_raw_plate_inventory_service.py
git commit -m "refactor: share raw plate inventory rules"
```

---

## Task 3: 补齐纸材规格与库存共用服务

**Files:**

- Modify: `app/services/paper_inventory.py`
- Modify: `app/paper_admin_pages.py`
- Create: `tests/test_paper_inventory_service_parity.py`
- Test: `tests/test_paper_material_management.py`
- Test: `tests/test_paper_exports.py`

- [ ] **Step 1: 写纸圈/纸张规格和库存失败测试**

覆盖以下服务行为：

```python
roll = create_paper_specification(
    db, paper_type="roll", model="3969.01", material_name="黑纸",
    thickness=0.5, inner_diameter=55, outer_diameter=115,
    length=None, width=None, remark=""
)
sheet = create_paper_specification(
    db, paper_type="sheet", model="", material_name="白纸",
    thickness=0.5, inner_diameter=None, outer_diameter=None,
    length=400, width=400, remark=""
)
self.assertEqual(sheet.model, "0.5×400×400")

records = outbound_paper_fifo(
    roll.id, 7, "", "二车间", "王五", "领料", db
)
self.assertEqual([record.quantity for record in records], [4, 3])
```

同时断言外径不大于内径、停用规格入库、库存不足零变更、单价保留两位小数、纸圈单位“圈”、纸张单位“张”、撤销入/出库和重复撤销拒绝。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_paper_inventory_service_parity.py`

Expected: 新服务接口缺失而失败。

- [ ] **Step 3: 扩展纸材服务接口**

在保留现有 `normalize_paper_specification()`、`paper_inventory_groups()`、`outbound_paper_fifo()` 和 `reverse_paper_transaction()` 行为的基础上，提供下列精确接口：

- `list_paper_specifications(db, *, q="", paper_type="", material_name="")`
- `create_paper_specification(db, *, paper_type, model, material_name, thickness, inner_diameter, outer_diameter, length, width, remark="")`
- `update_paper_specification(db, specification_id, *, paper_type, model, material_name, thickness, inner_diameter, outer_diameter, length, width, is_active, remark="")`
- `toggle_paper_specification(db, specification_id)`
- `list_paper_inventory(db, *, q="", paper_type="", material_name="", location="")`
- `list_paper_batches(db, specification_id, *, q="", location="", include_zero=False)`
- `inbound_paper(db, *, specification_id, batch_code, quantity, unit_price, location="", operator_name="", remark="")`
- `outbound_paper_fifo(specification_id, quantity, location, customer_name, operator_name, remark, db)`，返回 `list[PaperInventoryTransaction]`
- `list_paper_transactions(db, *, q="", paper_type="", transaction_type="")`

保持 `outbound_paper_fifo()` 返回逐批流水列表，移动路由再将这些流水序列化为 `allocations` 和总数量，从而不破坏现有 PC 调用方。事务依然由路由提交。

- [ ] **Step 4: 将 PC 纸材路由改为薄适配层**

把 `paper_admin_pages.py` 中的规格增改启停、入库、出库和撤销实现替换为服务调用，保持原 URL、字段、HTML 和导出查询兼容。

- [ ] **Step 5: 运行纸材回归测试**

Run: `pytest -q tests/test_paper_inventory_service_parity.py tests/test_paper_material_management.py tests/test_paper_exports.py`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/services/paper_inventory.py app/paper_admin_pages.py tests/test_paper_inventory_service_parity.py
git commit -m "refactor: share paper inventory rules"
```

---

## Task 4: 新增计划查料移动 API

**Files:**

- Create: `app/routers/mobile_plan.py`
- Modify: `app/main.py`
- Create: `tests/test_mobile_plan_api.py`

- [ ] **Step 1: 写 API 契约失败测试**

用 `TestClient` 覆盖：

```python
response = client.get("/api/mobile/plans/drawings", params={"q": "TNX", "material": "65Mn"})
self.assertEqual(response.status_code, 200)
self.assertEqual(response.json()[0]["product_code"], "TNX-001")

response = client.get("/api/mobile/plans/match", params={"drawing_id": drawing.id, "quantity": 10})
self.assertEqual(response.status_code, 200)
self.assertEqual(response.json()["recommendation_code"], "raw_plate")
```

补充数量 0 的 400 和不存在图纸的 404。

- [ ] **Step 2: 运行测试确认 404**

Run: `pytest -q tests/test_mobile_plan_api.py`

Expected: 两个新路径返回 404。

- [ ] **Step 3: 实现并注册路由**

```python
router = APIRouter(prefix="/plans", tags=["mobile-plans"])

@router.get("/drawings")
def plan_drawings(
    q: str = "",
    material: str = "",
    thickness: str = "",
    outer_diameter: str = "",
    inner_diameter: str = "",
    teeth_count: str = "",
    db: Session = Depends(get_db),
):
    drawings = list_plan_drawings(
        db,
        q=q,
        material=material,
        thickness=thickness,
        outer_diameter=outer_diameter,
        inner_diameter=inner_diameter,
        teeth_count=teeth_count,
    )
    return [serialize_plan_drawing(item) for item in drawings]

@router.get("/match")
def plan_match(drawing_id: int, quantity: int, db: Session = Depends(get_db)):
    return match_plan_materials(db, drawing_id=drawing_id, quantity=quantity)
```

在 `app/main.py` 以 `/api/mobile` 前缀挂载 `mobile_plan.router`。

- [ ] **Step 4: 运行测试并提交**

Run: `pytest -q tests/test_mobile_plan_api.py`

```bash
git add app/routers/mobile_plan.py app/main.py tests/test_mobile_plan_api.py
git commit -m "feat: add mobile plan material api"
```

---

## Task 5: 新增钢板移动 API 与幂等覆盖

**Files:**

- Create: `app/routers/mobile_raw_plates.py`
- Modify: `app/main.py`
- Create: `tests/test_mobile_raw_plate_api.py`
- Modify: `tests/test_mobile_idempotency.py`

- [ ] **Step 1: 写端点、跨端数据和幂等失败测试**

覆盖设计规格中的 10 个端点；至少对规格新增、规格修改、启停、批次修改、入库、出库和撤销逐类验证：首次成功、同请求重放、同编号不同负载 409。关键断言：

```python
payload = {
    "client_request_id": "raw-in-001",
    "specification_id": spec.id,
    "total_weight_ton": 1.0,
    "location": "A-01",
    "operator_name": "张三",
    "remark": "采购",
}
first = client.post("/api/mobile/raw-plates/inbound", json=payload)
second = client.post("/api/mobile/raw-plates/inbound", json=payload)
self.assertEqual(first.json(), second.json())
self.assertEqual(db.query(InventoryTransactionRecord).filter_by(transaction_type="in").count(), 1)
```

再通过 PC 服务写入后 GET 移动库存、移动 API 写入后渲染 `raw_plates_page()`，证明两端看到同一数据库状态。

- [ ] **Step 2: 运行测试确认 404**

Run: `pytest -q tests/test_mobile_raw_plate_api.py tests/test_mobile_idempotency.py`

Expected: 新钢板路径返回 404 或导入失败。

- [ ] **Step 3: 实现请求模型、序列化和路由**

路由前缀和写入模式固定为：

```python
router = APIRouter(tags=["mobile-raw-plates"])

class RawPlateInboundPayload(BaseModel):
    client_request_id: str
    specification_id: int | None = None
    material: str = ""
    length: float | None = None
    width: float | None = None
    thickness: float | None = None
    density: float = 7.85
    total_weight_ton: float
    location: str = ""
    operator_name: str = ""
    remark: str = ""
```

每个写端点使用同一流程：

```python
payload_data = payload.model_dump(mode="json", exclude={"client_request_id"})
replay = replayed_mobile_response(db, operation_type, payload.client_request_id, payload_data)
if replay is not None:
    return replay
result = service_function(db, **business_fields)
response = serialize_result(result)
remember_mobile_response(db, operation_type, payload.client_request_id, payload_data, response)
db.commit()
return response
```

库存写端点和规格写端点均在 `with inventory_write_lock():` 内完成重放检查、服务调用、幂等记录和提交；注册路径与设计规格完全一致。

- [ ] **Step 4: 运行测试并提交**

Run: `pytest -q tests/test_mobile_raw_plate_api.py tests/test_mobile_idempotency.py tests/test_steel_material_management.py`

```bash
git add app/routers/mobile_raw_plates.py app/main.py tests/test_mobile_raw_plate_api.py tests/test_mobile_idempotency.py
git commit -m "feat: add idempotent mobile raw plate api"
```

---

## Task 6: 新增纸材移动 API 与幂等覆盖

**Files:**

- Create: `app/routers/mobile_paper.py`
- Modify: `app/main.py`
- Create: `tests/test_mobile_paper_api.py`
- Modify: `tests/test_mobile_idempotency.py`

- [ ] **Step 1: 写端点、单位、跨端数据和幂等失败测试**

覆盖设计规格中的 10 个端点。纸圈和纸张各完成一次规格创建和入库；验证分组、批次、单价字符串、单位、FIFO 出库、流水、撤销以及 400/404/409/422。

```python
response = client.get(f"/api/mobile/paper-materials/{roll.id}/batches")
self.assertEqual(response.json()[0]["unit"], "圈")
self.assertEqual(response.json()[0]["unit_price"], "12.30")

response = client.post("/api/mobile/paper-materials/outbound", json={
    "client_request_id": "paper-out-001",
    "specification_id": roll.id,
    "quantity": 7,
    "location": "",
    "customer_name": "二车间",
    "operator_name": "王五",
    "remark": "领料",
})
self.assertEqual([row["quantity"] for row in response.json()["allocations"]], [4, 3])
```

验证 PC 写入后移动 GET 可见、移动写入后 `paper_inventory_page()` 可见。

- [ ] **Step 2: 运行测试确认 404**

Run: `pytest -q tests/test_mobile_paper_api.py tests/test_mobile_idempotency.py`

- [ ] **Step 3: 实现纸材路由**

使用与钢板路由相同的幂等模板和 `with inventory_write_lock():` 锁。金额统一序列化为两位小数字符串；日期统一 ISO 8601；所有实体响应返回数值 `id`、状态和适合小程序展示的 `size_text`、`unit`。

```python
@router.post("/paper-materials/outbound")
def paper_outbound(
    payload: PaperOutboundPayload,
    db: Session = Depends(get_db),
):
    with inventory_write_lock():
        return run_idempotent_write(db, "paper_outbound", payload, outbound_paper_fifo)
```

注册 `mobile_paper.router` 到 `/api/mobile`。

- [ ] **Step 4: 运行测试并提交**

Run: `pytest -q tests/test_mobile_paper_api.py tests/test_mobile_idempotency.py tests/test_paper_material_management.py`

```bash
git add app/routers/mobile_paper.py app/main.py tests/test_mobile_paper_api.py tests/test_mobile_idempotency.py
git commit -m "feat: add idempotent mobile paper api"
```

---

## Task 7: 扩展小程序 API 客户端和静态门禁

**Files:**

- Modify: `miniprogram/utils/api.js`
- Create: `tests/miniprogram_material_api.test.js`

- [ ] **Step 1: 写 API 路径和写入跟踪失败测试**

此任务暂不把尚未创建的页面注册进 `app.json`。在 Node 测试中 mock `wx.request`，验证查询参数、PUT/POST 方法以及缺少 `client_request_id` 时拒绝。

```javascript
await api.rawPlateInbound({ client_request_id: 'raw-1', specification_id: 3, total_weight_ton: 1 })
assert.equal(lastRequest.url, 'http://factory/api/mobile/raw-plates/inbound')
assert.equal(lastRequest.method, 'POST')
```

- [ ] **Step 2: 运行测试确认失败**

Run: `node --test tests/miniprogram_connection.test.js tests/miniprogram_material_api.test.js`

Expected: 新 API 方法缺失。

- [ ] **Step 3: 添加 API 方法**

在 `api.js` 导出设计规格中的全部移动接口。所有 POST/PUT 都调用 `trackedWriteData(data)`，例如：

```javascript
rawPlateSpecifications: (params = {}) => request('/api/mobile/raw-plate-specifications', { data: params }),
createRawPlateSpecification: (data) => request('/api/mobile/raw-plate-specifications', { method: 'POST', data: trackedWriteData(data) }),
updateRawPlateSpecification: (id, data) => request(`/api/mobile/raw-plate-specifications/${id}`, { method: 'PUT', data: trackedWriteData(data) }),
paperOutbound: (data) => request('/api/mobile/paper-materials/outbound', { method: 'POST', data: trackedWriteData(data) }),
planMatch: (params) => request('/api/mobile/plans/match', { data: params }),
```

- [ ] **Step 4: 运行测试并提交**

Run: `node --test tests/miniprogram_connection.test.js tests/miniprogram_material_api.test.js`

```bash
git add miniprogram/utils/api.js tests/miniprogram_material_api.test.js
git commit -m "test: define mobile material ui contracts"
```

---

## Task 8: 实现小程序计划查料页

**Files:**

- Modify: `miniprogram/pages/plan/home.js`
- Modify: `miniprogram/pages/plan/home.json`
- Modify: `miniprogram/pages/plan/home.wxml`
- Modify: `miniprogram/pages/plan/home.wxss`
- Modify: `tests/test_miniprogram_foundation.py`

- [ ] **Step 1: 增加交互静态测试**

断言页面包含型号/名称、材质、厚度、外径、内径、齿数筛选；有图纸结果选择、计划数量、建议、成品/余料/钢板汇总与批次明细；使用 `state-view`；不存在阶段占位文案；只读页面不使用 `confirm-sheet`。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_miniprogram_foundation.py -k plan`

- [ ] **Step 3: 实现加载、筛选和匹配状态机**

页面状态至少包含：

```javascript
data: {
  filters: { q: '', material: '', thickness: '', outer_diameter: '', inner_diameter: '', teeth_count: '' },
  drawings: [], selectedDrawingId: null, quantity: 1,
  result: null, loading: false, matching: false, error: ''
}
```

`onShow()` 调用 `loadDrawings()`；查询后保留筛选条件；`matchMaterials()` 校验图纸和正整数数量后调用 `api.planMatch()`。WXML 用扁平 `data-list` 展示三类库存和批次，唯一主操作为“查询可用材料”。

- [ ] **Step 4: 运行测试并提交**

Run: `pytest -q tests/test_miniprogram_foundation.py -k plan`

```bash
git add miniprogram/pages/plan tests/test_miniprogram_foundation.py
git commit -m "feat: complete mini program plan lookup"
```

---

## Task 9: 实现小程序钢板完整闭环

**Files:**

- Modify: `miniprogram/pages/materials/steel-home.*`
- Modify: `miniprogram/app.json`
- Create: `miniprogram/pages/raw-plates/specifications.*`
- Create: `miniprogram/pages/raw-plates/specification-form.*`
- Create: `miniprogram/pages/raw-plates/list.*`
- Create: `miniprogram/pages/raw-plates/detail.*`
- Create: `miniprogram/pages/raw-plates/inbound.*`
- Create: `miniprogram/pages/raw-plates/outbound.*`
- Create: `miniprogram/pages/raw-plates/transactions.*`
- Modify: `tests/test_miniprogram_foundation.py`

- [ ] **Step 1: 写七页功能门禁失败测试**

先断言 `app.json` 注册钢板 7 页，再逐页断言 `simple-header`、永久标签、加载/空/错误状态和 `onShow` 刷新。规格表单、规格启停、批次修改、入库、出库、撤销必须注册 `confirm-sheet` 并包含持久请求跟踪。入口页显示“规格、库存、入库、出库、流水”五个可点击入口。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_miniprogram_foundation.py -k raw_plate`

- [ ] **Step 3: 实现规格和库存查询/编辑**

规格列表支持筛选、新增、编辑、启停；表单按 `id` 参数区分新建/修改。库存列表展示规格汇总并进入批次明细；明细页允许修改可用批次。所有写入的确认数据明确列出材质、尺寸、密度/数量、库位和操作类型。

- [ ] **Step 4: 实现重量入库、FIFO 出库、流水撤销**

入库页支持选择启用规格或临时规格，实时显示估算但以后端响应为准。出库页从可用规格选择并填写数量、可选库位、客户/去向、操作人和备注。流水页展示库存变化、批次、操作人、客户和备注，撤销使用危险操作样式。

每个写页面采用同一模式：

```javascript
const tracker = createPendingRequestTracker('raw-plate-inbound')
const pending = tracker.begin(formData)
this.setData({ confirmOpen: true, confirmItems: buildConfirmItems(pending.payload) })
// confirmSubmit 中调用 API；成功 tracker.complete()；网络不确定时保留原请求供 retryPendingWrite()
```

- [ ] **Step 5: 运行测试并提交**

Run: `pytest -q tests/test_miniprogram_foundation.py -k raw_plate && node --test tests/miniprogram_material_api.test.js`

```bash
git add miniprogram/app.json miniprogram/pages/materials/steel-home.* miniprogram/pages/raw-plates tests/test_miniprogram_foundation.py
git commit -m "feat: complete mini program raw plate workflow"
```

---

## Task 10: 实现小程序纸材完整闭环

**Files:**

- Modify: `miniprogram/pages/materials/paper-home.*`
- Modify: `miniprogram/app.json`
- Create: `miniprogram/pages/paper/specifications.*`
- Create: `miniprogram/pages/paper/specification-form.*`
- Create: `miniprogram/pages/paper/list.*`
- Create: `miniprogram/pages/paper/detail.*`
- Create: `miniprogram/pages/paper/inbound.*`
- Create: `miniprogram/pages/paper/outbound.*`
- Create: `miniprogram/pages/paper/transactions.*`
- Modify: `tests/test_miniprogram_foundation.py`

- [ ] **Step 1: 写七页功能门禁失败测试**

先断言 `app.json` 注册纸材 7 页，再验证五入口、纸圈/纸张动态字段、规格启停、库存分组和批次、单价、单位、入库、FIFO 出库、流水和撤销；所有写入使用确认和持久请求跟踪。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_miniprogram_foundation.py -k paper`

- [ ] **Step 3: 实现规格和库存页面**

规格表单切换 `paper_type` 时：纸圈显示型号/内径/外径，纸张显示长度/宽度且型号只读由服务生成。库存列表展示类型、型号、材质、尺寸、数量、批次数、库位和价格范围；批次页展示批次号、数量、两位小数单价、库位和入库时间。

- [ ] **Step 4: 实现入库、出库、流水撤销**

入库只列启用规格，确认清单包含规格、类型、数量、单价、库位、操作人和备注。出库显示正确单位并传 `specification_id`；流水页展示批次分配与反向流水关系。使用与钢板一致的请求跟踪模式，tracker key 改为各纸材操作名。

- [ ] **Step 5: 运行测试并提交**

Run: `pytest -q tests/test_miniprogram_foundation.py -k paper && node --test tests/miniprogram_material_api.test.js`

```bash
git add miniprogram/app.json miniprogram/pages/materials/paper-home.* miniprogram/pages/paper tests/test_miniprogram_foundation.py
git commit -m "feat: complete mini program paper workflow"
```

---

## Task 11: 双端一致性、完整回归与微信开发者工具验收

**Files:**

- Create: `tests/test_pc_mobile_material_parity.py`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-04-pc-mobile-plan-steel-paper-parity-design.md`

- [ ] **Step 1: 写双端共享数据验收测试**

在同一测试数据库完成两条闭环：

```python
# PC 服务创建规格/入库 -> 移动 GET 立即可见
# 移动 API 出库/撤销 -> PC 页面库存和流水立即可见
```

钢板和纸材分别覆盖完整链路；计划服务和移动 API 对同一图纸返回相同三类库存总数与建议。

- [ ] **Step 2: 运行定向集成测试**

Run: `pytest -q tests/test_pc_mobile_material_parity.py tests/test_mobile_plan_api.py tests/test_mobile_raw_plate_api.py tests/test_mobile_paper_api.py`

Expected: PASS。

- [ ] **Step 3: 运行全部自动化检查**

Run: `pytest -q`

Run: `node --test tests/miniprogram_connection.test.js tests/miniprogram_material_api.test.js`

Run: `python -m json.tool miniprogram/app.json >/dev/null`

Run: `find miniprogram -name '*.json' -print0 | xargs -0 -n1 python -m json.tool >/dev/null`

Run: `git diff --check`

Expected: 全部退出码 0。

- [ ] **Step 4: 重启后台并验证真实数据闭环**

停止当前无 `--reload` 的后台进程后，从仓库目录重新启动 `uvicorn app.main:app --host 0.0.0.0 --port 8000`。用 `/health` 和 `/api/mobile/summary` 确认服务可访问；不得删除现有数据库。

- [ ] **Step 5: 微信开发者工具构建与人工闭环**

在微信开发者工具中预览 `miniprogram/`，确认无编译错误。使用专门测试规格分别完成：

1. 计划：筛选图纸 → 输入计划数 → 查看成品/余料/钢板与建议。
2. 钢板：新建规格 → 入库 → 库存可见 → FIFO 出库 → 流水可见 → 撤销；每步刷新 PC 对应页核对。
3. 纸材：纸圈和纸张各新建规格 → 入库 → 库存可见 → FIFO 出库 → 流水可见 → 撤销；每步刷新 PC 对应页核对。
4. PC 修改钢板和纸材规格后重新进入小程序页面，确认新值可见。

- [ ] **Step 6: 更新说明与验收记录**

README 增加三个移动模块入口、厂内同网访问说明、写入重试规则和“不含移动 Excel”的范围。设计规格的验收章节记录测试命令、结果、开发者工具构建结果与测试数据标识，不写模糊的“已验证”。

- [ ] **Step 7: 最终提交**

```bash
git add tests/test_pc_mobile_material_parity.py README.md docs/superpowers/specs/2026-08-04-pc-mobile-plan-steel-paper-parity-design.md
git commit -m "test: verify pc mobile material parity"
```

---

## Final Review Checklist

- [ ] 逐条对照设计规格第 3、4、5、6、7、8、9 节，确认每项均有实现文件和自动化/人工证据。
- [ ] 运行 `rg -n "第二阶段|第三阶段|第四阶段|后续开放|尚未开放|功能开发中" miniprogram app tests README.md`，确认三个目标模块没有占位实现或未完成标记。
- [ ] 运行 `rg -n "floor\(|created_at\.asc|reverse_.*transaction|raw_plate_matches_drawing|scrap_matches_drawing" app/routers app/admin_pages.py app/paper_admin_pages.py`，确认移动与 PC 路由没有复制服务层核心算法。
- [ ] 检查所有移动写接口都调用 `replayed_mobile_response()` 和 `remember_mobile_response()`，所有库存写接口都受 `inventory_write_lock` 保护。
- [ ] 检查金额返回类型统一为两位小数字符串、数量为整数、ID 为整数、时间为 ISO 8601、空值为 JSON `null`。
- [ ] 检查 `git status --short`，只提交本计划列出的文件，并保留用户原有未提交文件。
