# PC 与小程序计划、钢板、纸材功能同步设计

## 1. 目标

补齐小程序中的计划查料、钢板管理和纸材管理，并保证 PC 后台与手机端在这三个模块上共用数据、业务规则和写入服务。任意一端完成操作后，另一端刷新必须立即看到相同结果。

## 2. 设计原则

1. **同一数据源**：PC 和小程序共用现有 SQLite 数据库与 SQLAlchemy 模型，不新建手机专用表或同步任务。
2. **同一业务服务**：计划匹配、规格校验、入库、FIFO 出库、流水撤销均收敛到服务层；PC HTML 路由和移动 JSON API 不得各自复制一套规则。
3. **同一字段语义**：材质、长宽厚、纸材类型、单位、库位、客户/去向、操作人和备注在两端使用相同名称和验证规则。
4. **历史可追溯**：库存流水只允许生成反向流水，不删除历史。规格停用不隐藏历史库存和流水。
5. **手机端安全写入**：每个写操作均带 `client_request_id`，先展示完整确认清单，网络结果不确定时复用原请求编号重试。

## 3. 功能范围与双端对齐

### 3.1 计划查料

PC 和小程序均支持：

- 按产品型号/名称、材质、厚度、外径、内径和齿数筛选已确认的当前版本图纸。
- 选择图纸并填写大于 0 的计划数量。
- 返回成品库存、匹配余料和匹配钢板的总数与批次明细。
- 使用相同优先级生成建议：成品足够 → 优先成品；否则余料足够 → 优先余料；否则有钢板 → 安排钢板生产；都没有 → 采购或先入库。
- 计划查料只读，不预占、不冻结、不扣减库存。

### 3.2 钢板规格

PC 和小程序均支持：

- 查询全部规格，按型号、材质和尺寸筛选。
- 新增规格：规格名、材质、长度、宽度、厚度、密度和备注。
- 修改规格，修改只影响后续入库，不回写历史批次快照。
- 启用和停用规格。停用规格不允许新入库，但原库存仍可查询和出库。

### 3.3 钢板库存

PC 和小程序均支持：

- 按规格汇总可用块数、批次数和库位，可继续查看批次明细。
- 按批次号、型号、材质、长宽厚和库位筛选。
- 修改可用批次的型号、材质、长宽厚、数量和库位；修改必须写入操作日志，不修改旧流水。
- 入库可选固定规格或手工输入临时规格。系统按总重量、长宽厚和密度换算整块数与余重。
- 出库按材质、长宽厚和可选库位进行 FIFO 扣减；库存不足时整个操作失败，不产生部分扣减。
- 查询入库、出库和撤销流水，可对未撤销的入/出库流水生成反向流水。

### 3.4 纸材规格

PC 和小程序均支持：

- 查询、新增、修改、启用和停用纸材规格。
- 纸圈字段：型号、纸材名称/材质、厚度、内径、外径和备注。
- 纸张字段：纸材名称/材质、厚度、长度、宽度和备注；型号继续由系统按尺寸生成。
- 纸圈外径必须大于内径；所有尺寸和厚度必须大于 0。
- 停用规格不允许新入库，但原库存仍可查询和出库。

### 3.5 纸材库存

PC 和小程序均支持：

- 按规格汇总类型、型号、材质、尺寸、可用数量、批次数、库位和入库单价范围。
- 查看批次编号、数量、单价、库位和入库时间。
- 入库选择启用规格，填写批次号、数量、实际单价、库位、操作人和备注。
- 出库按规格和可选库位 FIFO 扣减，单位按纸圈“圈”、纸张“张”显示。
- 库存不足时整个操作失败，不产生部分扣减。
- 查询入库、出库和撤销流水，可对未撤销的入/出库流水生成反向流水。

### 3.6 本次不做

- 小程序不提供 Excel 导入/导出，文件业务继续由 PC 处理。
- 小程序不展示操作日志、助手或高级统计报表。
- 不新建数据同步服务，不复制数据到云端。

## 4. 后端架构

### 4.1 服务层

- 新增 `app/services/plan_material_service.py`，封装图纸筛选、成品/余料/钢板匹配、总数计算、明细组装和建议生成。PC 计划页与移动 API 同时调用。
- 新增 `app/services/raw_plate_inventory.py`，封装钢板规格增改启停、库存分组/明细、重量换算入库、FIFO 出库、批次修改和流水查询。撤销继续复用 `reverse_inventory_transaction()`。
- 扩展 `app/services/paper_inventory.py`，封装纸材规格增改启停、批次入库和流水查询，并继续复用现有分组、FIFO 出库和撤销逻辑。
- 所有库存写入使用现有 `inventory_write_lock`，数据变更、流水和操作日志位于同一数据库事务。

### 4.2 移动 API

新增三个独立路由文件，并以 `/api/mobile` 为前缀注册：

- `app/routers/mobile_plan.py`
  - `GET /plans/drawings`
  - `GET /plans/match`
- `app/routers/mobile_raw_plates.py`
  - `GET/POST /raw-plate-specifications`
  - `PUT /raw-plate-specifications/{id}`
  - `POST /raw-plate-specifications/{id}/toggle`
  - `GET /raw-plates`
  - `GET/PUT /raw-plates/{id}`
  - `POST /raw-plates/inbound`
  - `POST /raw-plates/outbound`
  - `GET /raw-plates/transactions`
  - `POST /raw-plates/transactions/{id}/reverse`
- `app/routers/mobile_paper.py`
  - `GET/POST /paper-specifications`
  - `PUT /paper-specifications/{id}`
  - `POST /paper-specifications/{id}/toggle`
  - `GET /paper-materials`
  - `GET /paper-materials/{specification_id}/batches`
  - `POST /paper-materials/inbound`
  - `POST /paper-materials/outbound`
  - `GET /paper-materials/transactions`
  - `POST /paper-materials/transactions/{id}/reverse`

所有 POST/PUT 请求体包含 `client_request_id`。移动路由通过 `MobileRequestRecord` 保存请求指纹与首次响应；同编号同负载重试返回原响应，同编号不同负载返回冲突错误。

### 4.3 PC 路由改造

PC 页面路径、表单字段和重定向保持兼容，但路由内部不再直接实现计算和出入库逻辑，而是调用上述共用服务。这一改造必须保持现有 PC 测试、URL 和用户操作流不变。

## 5. 小程序信息架构

### 5.1 计划

原 `pages/plan/home` 直接升级为完整查料页，不新增标签页。页面由筛选表单、图纸结果、计划数量、系统建议、三类库存汇总和明细组成。

### 5.2 钢板

`pages/materials/steel-home` 改为五个可用入口：规格、库存、入库、出库、流水。新页面：

- `pages/raw-plates/specifications`
- `pages/raw-plates/specification-form`
- `pages/raw-plates/list`
- `pages/raw-plates/detail`
- `pages/raw-plates/inbound`
- `pages/raw-plates/outbound`
- `pages/raw-plates/transactions`

### 5.3 纸材

`pages/materials/paper-home` 改为五个可用入口：规格、库存、入库、出库、流水。新页面：

- `pages/paper/specifications`
- `pages/paper/specification-form`
- `pages/paper/list`
- `pages/paper/detail`
- `pages/paper/inbound`
- `pages/paper/outbound`
- `pages/paper/transactions`

### 5.4 交互约束

- 继续使用现有 `simple-header`、`group-list`、`data-list`、`state-view`、`confirm-sheet` 和常驻表单标签。
- 同一页只保留一个主操作。筛选、清空、返回和撤销使用次要或危险操作样式。
- 规格新增/修改/启停、批次修改、入库、出库和撤销全部弹出完整字段确认清单。
- 使用 `onShow` 重新读取远端数据，不保存可能与 PC 不一致的长期业务缓存。
- 列表页必须具备加载、空数据、错误重试和提交中状态。

## 6. 数据与事务规则

### 6.1 钢板入库换算

`single_weight_kg = length_mm × width_mm × thickness_mm × density_g_cm3 / 1_000_000`

`quantity = floor(total_weight_ton × 1000 / single_weight_kg)`

换算数量必须大于 0，响应同时返回单块重量、入库块数和余重。

### 6.2 FIFO 原子性

- 钢板按规格及可选库位查询所有批次，按 `created_at, id` 升序扣减。
- 纸材按 `specification_id` 及可选库位查询所有批次，按 `created_at, id` 升序扣减。
- 扣减前先汇总可用数量。不足时不修改任何批次。
- 每个被扣减批次生成独立流水，操作日志记录完整批次分配结果。

### 6.3 撤销

- 只有未撤销的 `in` 或 `out` 流水可撤销。
- 撤销入库时，当前库存必须不小于原入库数量。
- 撤销出库时，数量加回原批次。
- 原流水和反向流水通过 `reversed_transaction_id` 相互关联。

## 7. 错误处理

- 400：字段值无效、规格已停用、库存不足、撤销条件不满足。
- 404：规格、批次、图纸或流水不存在。
- 409：同一 `client_request_id` 被用于不同请求负载。
- 422：必填字段缺失或请求编号不合法。
- 小程序显示后端 `detail`；网络失败时保留未确定请求，禁止用新负载覆盖，用户只能重试原请求或在确认已完成后结束跟踪。

## 8. 测试与验收

### 8.1 服务测试

- 计划筛选和匹配结果与现有 PC 规则一致。
- 钢板规格和纸材规格的新增、修改、启停和验证规则。
- 钢板重量换算的整块数、余重和小于一块时的拒绝。
- 钢板和纸材的跨批次 FIFO、库存不足无部分扣减、入/出库撤销和重复撤销拒绝。

### 8.2 API 测试

- 所有端点的成功响应、参数筛选、400/404/409/422 错误。
- 每类写入均验证同请求重放、同编号不同负载冲突以及网络结果不确定后的原请求重试。
- PC 路由操作与移动 API 操作使用同一测试数据库，验证“PC 写入 → 手机 API 读取”和“手机 API 写入 → PC 页面读取”。

### 8.3 小程序测试

- `app.json` 注册全部新页面，三个主入口不再包含“后续阶段开放”文案。
- 所有写页面使用 `confirm-sheet` 和持久请求跟踪，同一页只有一个主操作。
- 列表、空状态、错误重试、页面跳转、筛选和单位显示通过静态与 Node 测试。

### 8.4 完整验收

- Python 全量测试、Node 测试、JSON 解析和 `git diff --check` 全部通过。
- 微信开发者工具预览构建通过。
- 在开发者工具中至少完成一次计划查料，并对钢板和纸材分别完成“新建规格 → 入库 → 库存可见 → 出库 → 流水可见 → 撤销”闭环。
- 闭环中每次手机写入后刷新 PC 页面，均能看到相同数据；PC 修改规格后重新进入小程序页面，也能看到新值。

## 9. 成功标准

以下条件全部满足才能宣布本功能完成：

1. 计划、钢板和纸材入口不存在占位页或禁用操作。
2. 本规格中列出的 PC 功能和手机功能逐项对齐。
3. 两端共用服务层和数据表，不存在复制的入库、FIFO、撤销或匹配规则。
4. 所有写入完成幂等、确认、事务和操作日志闭环。
5. 自动化测试、开发者工具构建和双端数据闭环验收全部通过。

## 10. 2026-08-06 实施验收记录

### 10.1 自动化与双端数据闭环

- `PYTHONPATH=. .venv/bin/pytest -q`：`155 passed`，`48 subtests passed`；另有 11 条第三方库弃用警告，无失败。
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_pc_mobile_material_parity.py tests/test_mobile_plan_api.py tests/test_mobile_raw_plate_api.py tests/test_mobile_paper_api.py`：`7 passed`。
- `node --test tests/miniprogram_connection.test.js tests/miniprogram_material_api.test.js`：`10 passed`。
- 全部小程序 JSON 通过 `.venv/bin/python -m json.tool`，全部小程序 JavaScript 通过 `node --check`，`git diff --check` 退出码为 0。
- `tests/test_pc_mobile_material_parity.py` 使用独立内存数据库验证：PC 服务入库后移动接口立即可见；移动接口 FIFO 出库后 PC 服务读取到相同余额；移动接口撤销后 PC 余额恢复；计划服务与移动接口返回完全相同的成品、余料、钢板统计和建议。测试没有修改 `data/app.db`。
- 新增前端门禁验证必填字段在确认前拦截，并验证动态写入重试始终绑定原 `specification_id`、`batch_id` 或 `transaction_id`。

### 10.2 运行服务与开发者工具

- 后台已从本仓库重新启动为 `uvicorn app.main:app --host 0.0.0.0 --port 8000`；通过 `http://192.168.10.239:8000` 实测 `/health`、`/api/mobile/summary`、`/api/mobile/plans/drawings`、`/api/mobile/raw-plate-specifications`、`/api/mobile/raw-plates`、`/api/mobile/paper-specifications` 和 `/api/mobile/paper-materials` 均返回成功。
- 微信开发者工具使用 AppID `wxc9c29ffe2999dff6` 对计划、钢板库存、钢板出库、纸材库存和纸材入库入口完成多次 `preview` 构建。最终构建结果为 `✔ preview`，总包大小 `188.1 KB / 192601 Byte`。
- 为避免污染正式库存，本次没有在 `data/app.db` 新建并删除测试规格。首次现场试运行仍需按 README 的“内部试运行”清单，由操作人员用专门测试规格在真机完成一次界面操作抽查；该步骤属于现场确认，不影响上述代码、接口、共享数据库和构建验收结果。
