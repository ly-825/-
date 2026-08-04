# WeChat Mini Program Frontend Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the approved simple, category-first visual direction to every currently registered mini-program page without changing inventory rules, request payloads, LAN connection behavior, or backend APIs.

**Architecture:** Keep the native WeChat Mini Program and its existing page JavaScript. Build the redesign from global WXSS primitives plus the existing `connection-status`, `state-view`, and `confirm-sheet` components; then migrate top-level, product, and scrap pages in independently testable batches. Static contract tests protect navigation, touch sizes, simplified hierarchy, confirmation flows, and persistent request IDs while visual QA compares the implemented materials home against the approved mock.

**Tech Stack:** Native WeChat Mini Program (WXML/WXSS/JavaScript), Python 3.11 `unittest`/pytest static contract tests, Node.js built-in tests, Lucide SVG icons retrieved through `better-icons`, WeChat Developer Tools Stable 1.06.2412050.

## Global Constraints

- Bottom navigation remains exactly `计划`, `材料`, `成品`.
- Page background is `#F8FAFC`; content is `#FFFFFF`; primary text is `#0F172A`; secondary text is `#64748B`; borders are `#E2E8F0`; primary action is `#059669`; pending is `#B45309`; danger is `#DC2626`.
- Every screen has at most one green primary action.
- All touch targets are at least `88rpx` high and fixed action areas reserve `env(safe-area-inset-bottom)`.
- Related rows share one white grouped surface with separators; ordinary content has no shadow and group radius is at most `20rpx`.
- Model numbers, sizes, quantities, money, and dates use tabular numerals and wrap without horizontal scrolling.
- Connected state is a low-emphasis single line; connection errors remain explicit and actionable.
- Existing APIs, payload fields, `client_request_id`, idempotency, FIFO, database transactions, and reversal relationships are unchanged.
- Incomplete plan, steel, paper, and analysis functionality stays visibly unavailable; the redesign must not fabricate data or write operations.
- Drawings remain outside the registered mobile navigation.
- Preserve the existing uncommitted edit in `docs/superpowers/plans/2026-08-04-wechat-mini-program-phase-1-foundation.md`.

## File Map

### Shared visual foundation

- Modify `tests/test_miniprogram_foundation.py`: add redesign contract assertions.
- Modify `miniprogram/app.wxss`: add simplified page, grouped-list, form, data-row, ledger, fixed-action, and danger styles.
- Modify `miniprogram/components/connection-status/index.wxml` and `index.wxss`: compact connected state and actionable error state.
- Modify `miniprogram/components/state-view/index.wxml` and `index.wxss`: flat grouped-surface states.
- Modify `miniprogram/components/confirm-sheet/index.wxml` and `index.wxss`: simplified field rows and fixed safe-area actions.
- Create `miniprogram/assets/icons/steel.svg`, `scrap.svg`, `paper.svg`, and `chevron-right.svg`: local Lucide assets.

### Page migrations

- Modify `miniprogram/pages/connection/index.wxml` and `index.wxss`.
- Modify `miniprogram/pages/plan/home.wxml` and `home.wxss`.
- Modify `miniprogram/pages/materials/home.wxml` and `home.wxss`.
- Modify `miniprogram/pages/materials/steel-home.wxml`, `steel-home.wxss`, `paper-home.wxml`, and `paper-home.wxss`.
- Modify `miniprogram/pages/products/home.wxml` and `home.wxss`.
- Modify all WXML/WXSS files under `miniprogram/pages/inventory/` that are registered in `app.json`.
- Modify all WXML/WXSS files under `miniprogram/pages/scraps/`.
- Modify only page JavaScript state mapping needed for loading/error presentation; do not change API calls or write payload construction.

---

### Task 1: Lock the redesign contract and shared primitives

**Files:**
- Modify: `tests/test_miniprogram_foundation.py`
- Modify: `miniprogram/app.wxss`
- Modify: `miniprogram/components/connection-status/index.wxml`
- Modify: `miniprogram/components/connection-status/index.wxss`
- Modify: `miniprogram/components/state-view/index.wxml`
- Modify: `miniprogram/components/state-view/index.wxss`
- Modify: `miniprogram/components/confirm-sheet/index.wxml`
- Modify: `miniprogram/components/confirm-sheet/index.wxss`
- Create: `miniprogram/assets/icons/steel.svg`
- Create: `miniprogram/assets/icons/scrap.svg`
- Create: `miniprogram/assets/icons/paper.svg`
- Create: `miniprogram/assets/icons/chevron-right.svg`

**Interfaces:**
- Consumes: existing component properties and events; no JavaScript signature changes.
- Produces: global classes `.simple-header`, `.simple-title`, `.group-list`, `.group-row`, `.group-row__icon`, `.group-row__title`, `.group-row__meta`, `.group-row__status`, `.group-row__chevron`, `.primary-action`, `.form-section`, `.form-field`, `.form-label`, `.data-list`, `.data-item`, `.ledger-list`, `.ledger-item`, `.danger-action`, and `.safe-action`.

- [ ] **Step 1: Write failing shared-style and component tests**

Add these methods to `MiniProgramFoundationTest`:

```python
    def test_redesign_exposes_simple_grouped_ui_primitives(self) -> None:
        wxss = self.read("miniprogram/app.wxss")
        for selector in (
            ".simple-header",
            ".group-list",
            ".group-row",
            ".primary-action",
            ".form-label",
            ".data-list",
            ".ledger-item",
            ".danger-action",
            ".safe-action",
        ):
            self.assertIn(selector, wxss)
        self.assertIn("env(safe-area-inset-bottom)", wxss)
        self.assertIn("font-variant-numeric: tabular-nums", wxss)

    def test_connected_status_is_compact_but_error_is_actionable(self) -> None:
        view = self.read("miniprogram/components/connection-status/index.wxml")
        style = self.read("miniprogram/components/connection-status/index.wxss")
        self.assertIn("connection-line", view)
        self.assertIn("内网已连接", view)
        self.assertIn("connection-error", view)
        self.assertIn("重新连接", view)
        self.assertIn("修改地址", view)
        self.assertNotIn("box-shadow", style)

    def test_local_material_icons_exist_and_are_lucide_svg(self) -> None:
        for name in ("steel", "scrap", "paper", "chevron-right"):
            source = self.read(f"miniprogram/assets/icons/{name}.svg")
            self.assertIn('viewBox="0 0 24 24"', source)
            self.assertIn('stroke="#334155"', source)
```

- [ ] **Step 2: Run the focused test and confirm failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_miniprogram_foundation.py -q
```

Expected: FAIL because the new classes, compact connection markup, and icon files do not exist.

- [ ] **Step 3: Add the Lucide assets and global primitives**

Use `better-icons get lucide:layers`, `lucide:disc`, `lucide:scroll-text`, and `lucide:chevron-right` to verify the selected library sources. Create the four local files with the retrieved paths, `fill="none"`, `stroke="#334155"`, rounded line caps/joins, and `stroke-width="2"`.

`steel.svg`:

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24"><g fill="none" stroke="#334155" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"><path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83z"/><path d="M2 12a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 12"/><path d="M2 17a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 17"/></g></svg>
```

`scrap.svg`:

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24"><g fill="none" stroke="#334155" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="2"/></g></svg>
```

`paper.svg`:

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24"><g fill="none" stroke="#334155" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"><path d="M15 12h-5m5-4h-5m9 9V5a2 2 0 0 0-2-2H4"/><path d="M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3"/></g></svg>
```

`chevron-right.svg`:

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24"><path fill="none" stroke="#334155" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m9 18l6-6l-6-6"/></svg>
```

Replace the connected/checking branch of `connection-status/index.wxml` with:

```xml
<view wx:if="{{state === 'connected' || state === 'checking'}}" class="connection-line connection-line--{{state}}">
  <view class="connection-line__dot"></view>
  <text>{{state === 'connected' ? '内网已连接' : '正在连接'}}</text>
</view>
<view wx:else class="connection-error">
  <view class="connection-error__copy">
    <view class="connection-error__title">连接失败</view>
    <view class="connection-error__address" wx:if="{{baseUrl}}">{{baseUrl}}</view>
  </view>
  <view class="connection-error__actions">
    <button class="mini-action" bindtap="retry">重新连接</button>
    <button class="mini-action" bindtap="configure">修改地址</button>
  </view>
</view>
```

Add the following complete primitive rules to `app.wxss` and remove shadows from `.card`, `.state-view`, and ordinary page content:

```css
.simple-header { margin-bottom: 32rpx; }
.simple-title { color: #0F172A; font-size: 44rpx; font-weight: 700; line-height: 1.25; }
.simple-subtitle { margin-top: 8rpx; color: #64748B; font-size: 26rpx; }
.group-list { overflow: hidden; border: 1rpx solid #E2E8F0; border-radius: 20rpx; background: #FFFFFF; }
.group-row { display: flex; align-items: center; min-height: 112rpx; box-sizing: border-box; padding: 20rpx 24rpx; border-bottom: 1rpx solid #E2E8F0; }
.group-row:last-child { border-bottom: 0; }
.group-row__icon { width: 56rpx; height: 56rpx; margin-right: 20rpx; flex: 0 0 56rpx; }
.group-row__body { min-width: 0; flex: 1; }
.group-row__title { color: #0F172A; font-size: 30rpx; font-weight: 600; }
.group-row__meta { margin-top: 4rpx; color: #64748B; font-size: 26rpx; overflow-wrap: anywhere; }
.group-row__status { color: #B45309; }
.group-row__chevron { width: 40rpx; height: 40rpx; margin-left: 16rpx; flex: 0 0 40rpx; }
.primary-action { min-height: 88rpx; margin-top: 24rpx; border: 0; border-radius: 18rpx; background: #059669; color: #FFFFFF; font-size: 30rpx; font-weight: 600; }
.primary-action::after { border: 0; }
.form-section { margin-bottom: 24rpx; padding: 28rpx; border: 1rpx solid #E2E8F0; border-radius: 20rpx; background: #FFFFFF; }
.form-field { margin-bottom: 24rpx; }
.form-field:last-child { margin-bottom: 0; }
.form-label { display: block; margin-bottom: 8rpx; color: #334155; font-size: 26rpx; font-weight: 500; }
.data-list, .ledger-list { overflow: hidden; border: 1rpx solid #E2E8F0; border-radius: 20rpx; background: #FFFFFF; }
.data-item, .ledger-item { padding: 24rpx; border-bottom: 1rpx solid #E2E8F0; }
.data-item:last-child, .ledger-item:last-child { border-bottom: 0; }
.danger-action { min-height: 88rpx; border: 1rpx solid #FECACA; background: #FFFFFF; color: #DC2626; }
.safe-action { padding-bottom: calc(24rpx + env(safe-area-inset-bottom)); }
.numeric { font-variant-numeric: tabular-nums; }
```

Restyle `state-view` to use the same flat `20rpx` group boundary. Keep skeletons and reduced-motion behavior. Restyle `confirm-sheet` so its field lines remain readable and its action area reserves the safe area; do not change component properties or emitted events.

- [ ] **Step 4: Run focused tests and confirm pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_miniprogram_foundation.py -q
```

Expected: all mini-program foundation tests PASS.

- [ ] **Step 5: Commit the shared foundation**

```bash
git add tests/test_miniprogram_foundation.py miniprogram/app.wxss miniprogram/components miniprogram/assets/icons
git commit -m "style: add simple mini program design system"
```

---

### Task 2: Redesign connection and top-level module pages

**Files:**
- Modify: `tests/test_miniprogram_foundation.py`
- Modify: `miniprogram/pages/connection/index.wxml`
- Modify: `miniprogram/pages/connection/index.wxss`
- Modify: `miniprogram/pages/plan/home.wxml`
- Modify: `miniprogram/pages/plan/home.wxss`
- Modify: `miniprogram/pages/materials/home.wxml`
- Modify: `miniprogram/pages/materials/home.wxss`
- Modify: `miniprogram/pages/materials/steel-home.wxml`
- Modify: `miniprogram/pages/materials/steel-home.wxss`
- Modify: `miniprogram/pages/materials/paper-home.wxml`
- Modify: `miniprogram/pages/materials/paper-home.wxss`
- Modify: `miniprogram/pages/products/home.wxml`
- Modify: `miniprogram/pages/products/home.wxss`

**Interfaces:**
- Consumes: `.simple-header`, `.group-list`, `.group-row`, `.primary-action`, and existing `go`, `load`, `configure`, `scanBaseUrl`, `openManual`, and `testAndSave` handlers.
- Produces: approved category-first materials home and matching top-level page hierarchy.

- [ ] **Step 1: Write failing page-hierarchy tests**

Add:

```python
    def test_top_level_pages_use_simple_headers_and_grouped_lists(self) -> None:
        pages = (
            "miniprogram/pages/plan/home.wxml",
            "miniprogram/pages/materials/home.wxml",
            "miniprogram/pages/products/home.wxml",
            "miniprogram/pages/materials/steel-home.wxml",
            "miniprogram/pages/materials/paper-home.wxml",
        )
        for page in pages:
            with self.subTest(page=page):
                view = self.read(page)
                self.assertIn("simple-header", view)
                self.assertNotIn("eyebrow", view)
        materials = self.read("miniprogram/pages/materials/home.wxml")
        self.assertIn("group-list", materials)
        self.assertIn("处理待确认余料", materials)
        self.assertEqual(materials.count("primary-action"), 1)

    def test_connection_page_keeps_one_primary_scan_action(self) -> None:
        view = self.read("miniprogram/pages/connection/index.wxml")
        self.assertEqual(view.count('class="primary-action"'), 1)
        self.assertIn("扫描电脑连接二维码", view)
        self.assertIn("手工设置地址", view)
```

- [ ] **Step 2: Run the tests and confirm failure**

Run the same focused pytest command. Expected: FAIL because the old page headings and card stacks remain.

- [ ] **Step 3: Implement the approved materials hierarchy**

Use this structure in `materials/home.wxml`:

```xml
<view class="container">
  <view class="simple-header">
    <view class="simple-title">材料</view>
    <connection-status state="{{error ? 'error' : 'connected'}}" base-url="{{baseUrl}}" bind:retry="load" bind:configure="configure" />
  </view>
  <state-view wx:if="{{loading}}" state="loading" title="正在读取材料概览" />
  <state-view wx:elif="{{error}}" state="error" title="材料概览加载失败" description="{{error}}" bind:retry="load" />
  <block wx:else>
    <view class="group-list">
      <view class="group-row" bindtap="go" data-url="/pages/materials/steel-home">
        <image class="group-row__icon" src="/assets/icons/steel.svg" mode="aspectFit" />
        <view class="group-row__body"><view class="group-row__title">钢板</view><view class="group-row__meta">库存 · 出入库</view></view>
        <image class="group-row__chevron" src="/assets/icons/chevron-right.svg" mode="aspectFit" />
      </view>
      <view class="group-row" bindtap="go" data-url="/pages/scraps/home">
        <image class="group-row__icon" src="/assets/icons/scrap.svg" mode="aspectFit" />
        <view class="group-row__body"><view class="group-row__title">余料</view><view class="group-row__meta group-row__status">{{pendingScrapCount}} 条待确认</view></view>
        <image class="group-row__chevron" src="/assets/icons/chevron-right.svg" mode="aspectFit" />
      </view>
      <view class="group-row" bindtap="go" data-url="/pages/materials/paper-home">
        <image class="group-row__icon" src="/assets/icons/paper.svg" mode="aspectFit" />
        <view class="group-row__body"><view class="group-row__title">纸材</view><view class="group-row__meta">库存 · 出入库</view></view>
        <image class="group-row__chevron" src="/assets/icons/chevron-right.svg" mode="aspectFit" />
      </view>
    </view>
    <button wx:if="{{pendingScrapCount > 0}}" class="primary-action" bindtap="go" data-url="/pages/scraps/pending">处理待确认余料</button>
  </block>
</view>
```

Use the same simple header and grouped-list structure for `products/home.wxml`. Keep `quantity`, the four working routes, and the disabled analysis row. Use simple header plus one flat unavailable-state section for plan, steel, and paper pages. Do not add routes or handlers.

In `connection/index.wxml`, keep the existing conditional states and handler names, but make scan the sole green `primary-action`; render manual setup and retries as secondary or text actions. Keep permanent labels above IP and port inputs.

- [ ] **Step 4: Run the focused test and Node connection tests**

```bash
.venv/bin/python -m pytest tests/test_miniprogram_foundation.py -q
node --test tests/miniprogram_connection.test.js
```

Expected: both commands PASS.

- [ ] **Step 5: Commit top-level pages**

```bash
git add tests/test_miniprogram_foundation.py miniprogram/pages/connection miniprogram/pages/plan miniprogram/pages/materials miniprogram/pages/products
git commit -m "style: simplify mini program module pages"
```

---

### Task 3: Redesign product inventory, inbound, and outbound pages

**Files:**
- Modify: `tests/test_miniprogram_foundation.py`
- Modify: `miniprogram/pages/inventory/list.wxml`
- Modify: `miniprogram/pages/inventory/list.wxss`
- Modify: `miniprogram/pages/inventory/list.js`
- Modify: `miniprogram/pages/inventory/list.json`
- Modify: `miniprogram/pages/inventory/inbound.wxml`
- Modify: `miniprogram/pages/inventory/inbound.wxss`
- Modify: `miniprogram/pages/inventory/inbound.js`
- Modify: `miniprogram/pages/inventory/inbound.json`
- Modify: `miniprogram/pages/inventory/outbound.wxml`
- Modify: `miniprogram/pages/inventory/outbound.wxss`
- Modify: `miniprogram/pages/inventory/outbound.js`
- Modify: `miniprogram/pages/inventory/outbound.json`

**Interfaces:**
- Consumes: existing filters, drawing/product selectors, `submit`, confirmation sheet, and request tracker behavior.
- Produces: permanent field labels, flat search/result groups, readable product rows, and one green submit action per write page.

- [ ] **Step 1: Write failing inventory layout tests**

Add:

```python
    def test_inventory_pages_use_permanent_labels_and_flat_data_lists(self) -> None:
        for name in ("list", "inbound", "outbound"):
            view = self.read(f"miniprogram/pages/inventory/{name}.wxml")
            self.assertIn("simple-header", view)
            self.assertIn("form-label", view)
            self.assertIn("data-list", view)
            self.assertNotIn("eyebrow", view)
        for name in ("inbound", "outbound"):
            view = self.read(f"miniprogram/pages/inventory/{name}.wxml")
            self.assertEqual(view.count("primary-action"), 1)
            self.assertIn("<confirm-sheet", view)
        for name in ("list", "inbound", "outbound"):
            view = self.read(f"miniprogram/pages/inventory/{name}.wxml")
            source = self.read(f"miniprogram/pages/inventory/{name}.js")
            config = json.loads(self.read(f"miniprogram/pages/inventory/{name}.json"))
            self.assertIn("<state-view", view)
            self.assertIn("error", source)
            self.assertEqual(
                config.get("usingComponents", {}).get("state-view"),
                "/components/state-view/index",
            )
```

- [ ] **Step 2: Run focused tests and confirm failure**

Expected: FAIL because labels are currently encoded only as placeholders and result items are nested cards.

- [ ] **Step 3: Migrate the three product pages**

For every input, wrap the existing input without changing its binding:

```xml
<view class="form-field">
  <view class="form-label">产品搜索</view>
  <input class="input" placeholder="型号、名称、材质或厚度" value="{{searchKeyword}}" confirm-type="search" bindinput="onKeyword" bindconfirm="onSearch" />
</view>
```

Render result collections with one `.data-list` and repeated `.data-item` rows. The product identity is the first line, material/thickness/location the second line, and quantity uses `.numeric` with unit text. Preserve all current values and bindings.

Register `/components/state-view/index` in each page JSON. Add `loading` and `error` to page data, clear `error` before each load, set the current error message in `catch`, and clear `loading` in `finally`. In WXML, show `state-view` for loading and error before the result list. Do not remove the current toast from write failures.

For inbound labels use `产品搜索`, `已选产品`, `数量`, `库位`, and `操作人`. For outbound labels use `产品搜索`, `已选产品`, `出库数量`, `指定库位`, `操作人`, and `备注`. Place the sole `.primary-action` after all form fields and keep the existing `submit`, `loading`, and `disabled` bindings. Keep `confirm-sheet` unchanged.

- [ ] **Step 4: Run inventory contract and idempotency tests**

```bash
.venv/bin/python -m pytest tests/test_miniprogram_foundation.py tests/test_mobile_idempotency.py -q
node --test tests/miniprogram_connection.test.js
```

Expected: all tests PASS; the write pages still contain `createPendingRequestTracker`, `retryPendingWrite`, and `.complete()`.

- [ ] **Step 5: Commit product query and forms**

```bash
git add tests/test_miniprogram_foundation.py miniprogram/pages/inventory/list.* miniprogram/pages/inventory/inbound.* miniprogram/pages/inventory/outbound.*
git commit -m "style: redesign product inventory forms"
```

---

### Task 4: Redesign product ledger and scrap overview/query pages

**Files:**
- Modify: `tests/test_miniprogram_foundation.py`
- Modify: `miniprogram/pages/inventory/transactions.wxml`
- Modify: `miniprogram/pages/inventory/transactions.wxss`
- Modify: `miniprogram/pages/inventory/transactions.js`
- Modify: `miniprogram/pages/inventory/transactions.json`
- Modify: `miniprogram/pages/scraps/home.wxml`
- Modify: `miniprogram/pages/scraps/home.wxss`
- Modify: `miniprogram/pages/scraps/home.js`
- Modify: `miniprogram/pages/scraps/home.json`
- Modify: `miniprogram/pages/scraps/list.wxml`
- Modify: `miniprogram/pages/scraps/list.wxss`
- Modify: `miniprogram/pages/scraps/list.js`
- Modify: `miniprogram/pages/scraps/list.json`

**Interfaces:**
- Consumes: existing transaction mapping, reverse-form handlers, scrap summary, filters, and navigation.
- Produces: vertical ledger rows, one grouped scrap menu, and readable scrap query results.

- [ ] **Step 1: Add failing ledger and scrap-list tests**

```python
    def test_read_pages_use_flat_lists_and_no_english_eyebrows(self) -> None:
        pages = (
            "miniprogram/pages/inventory/transactions.wxml",
            "miniprogram/pages/scraps/home.wxml",
            "miniprogram/pages/scraps/list.wxml",
        )
        for page in pages:
            view = self.read(page)
            self.assertIn("simple-header", view)
            self.assertNotIn("eyebrow", view)
            self.assertIn("<state-view", view)
        self.assertIn("ledger-list", self.read("miniprogram/pages/inventory/transactions.wxml"))
        self.assertIn("group-list", self.read("miniprogram/pages/scraps/home.wxml"))
        self.assertIn("data-list", self.read("miniprogram/pages/scraps/list.wxml"))
```

- [ ] **Step 2: Run the focused test and confirm failure**

Expected: FAIL on the old card and English-eyebrow markup.

- [ ] **Step 3: Implement flat ledgers and grouped scrap navigation**

Use `.ledger-list > .ledger-item` for product transactions. Keep code, type, quantity, before/after quantity, operator, remark, and created time visible. Style the reversal opener as `.danger-action`; keep the reversal fields and `confirm-sheet` behavior intact.

Use a single `.group-list` for scrap home routes: pending, inventory, outbound, ledger. Display pending and available quantities as inline secondary text instead of separate KPI cards. Use a single `.form-section` for scrap filters and `.data-list` for results; keep filter field order and bindings unchanged.

Register `state-view` in all three page JSON files. Add `loading` and `error` page state and use the same load lifecycle from Task 3 so loading, empty results, and server errors remain visually distinct.

- [ ] **Step 4: Run focused tests**

```bash
.venv/bin/python -m pytest tests/test_miniprogram_foundation.py tests/test_mobile_idempotency.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit product ledger and scrap read pages**

```bash
git add tests/test_miniprogram_foundation.py miniprogram/pages/inventory/transactions.* miniprogram/pages/scraps/home.* miniprogram/pages/scraps/list.*
git commit -m "style: simplify inventory ledgers and scrap lists"
```

---

### Task 5: Redesign scrap confirmation, outbound, and ledger pages

**Files:**
- Modify: `tests/test_miniprogram_foundation.py`
- Modify: `miniprogram/pages/scraps/pending.wxml`
- Modify: `miniprogram/pages/scraps/pending.wxss`
- Modify: `miniprogram/pages/scraps/pending.js`
- Modify: `miniprogram/pages/scraps/pending.json`
- Modify: `miniprogram/pages/scraps/outbound.wxml`
- Modify: `miniprogram/pages/scraps/outbound.wxss`
- Modify: `miniprogram/pages/scraps/outbound.js`
- Modify: `miniprogram/pages/scraps/outbound.json`
- Modify: `miniprogram/pages/scraps/transactions.wxml`
- Modify: `miniprogram/pages/scraps/transactions.wxss`
- Modify: `miniprogram/pages/scraps/transactions.js`
- Modify: `miniprogram/pages/scraps/transactions.json`

**Interfaces:**
- Consumes: existing pending-item fields, picker, submit handlers, reversal handlers, confirmation sheets, and request trackers.
- Produces: permanent labels, one primary write action per current object, flat item groups, and red reversal treatment.

- [ ] **Step 1: Add failing scrap write-page tests**

```python
    def test_scrap_write_pages_keep_labels_confirmations_and_danger_actions(self) -> None:
        for name in ("pending", "outbound", "transactions"):
            view = self.read(f"miniprogram/pages/scraps/{name}.wxml")
            self.assertIn("simple-header", view)
            self.assertIn("<confirm-sheet", view)
            self.assertIn("<state-view", view)
            self.assertNotIn("eyebrow", view)
        self.assertIn("form-label", self.read("miniprogram/pages/scraps/pending.wxml"))
        self.assertIn("form-label", self.read("miniprogram/pages/scraps/outbound.wxml"))
        self.assertIn("ledger-list", self.read("miniprogram/pages/scraps/transactions.wxml"))
        self.assertIn("danger-action", self.read("miniprogram/pages/scraps/transactions.wxml"))
```

- [ ] **Step 2: Run the test and confirm failure**

Expected: FAIL because the pages still use placeholders as labels, card-per-item treatment, and ordinary secondary buttons for reversal.

- [ ] **Step 3: Migrate pending confirmation, outbound, and ledger UI**

For each pending scrap, keep source product, material, thickness, theoretical diameter, actual quantity, actual diameter, location, and operator. Put the item in one `.data-item`; use permanent labels for every editable field and one `确认入库` action per item. The action remains secondary until the existing confirmation sheet opens; the sheet confirmation remains the single final write action.

For outbound, use one filter `.form-section`, one outbound `.form-section`, and one `.data-list`. Use permanent labels `余料规格`, `出库数量`, `操作人`, and `备注`. Keep the existing picker and submit bindings.

For scrap transactions, use `.ledger-list > .ledger-item`; keep material, usable size, location, quantity, before/after, operator, time, and reversal status visible. Use `.danger-action` for reversal and preserve the required reason check and danger confirmation sheet.

Register `state-view` in the three page JSON files. Keep each page's existing `loading` guard where present; add `error`, set it from caught request errors, and render loading/error state components outside the editable list or form so a failed refresh never exposes a write action against unknown data.

- [ ] **Step 4: Run write-safety tests**

```bash
.venv/bin/python -m pytest tests/test_miniprogram_foundation.py tests/test_mobile_idempotency.py -q
node --test tests/miniprogram_connection.test.js
```

Expected: PASS with every current write page still registered to `confirm-sheet` and persistent request trackers.

- [ ] **Step 5: Commit scrap write pages**

```bash
git add tests/test_miniprogram_foundation.py miniprogram/pages/scraps/pending.* miniprogram/pages/scraps/outbound.* miniprogram/pages/scraps/transactions.*
git commit -m "style: redesign scrap write flows"
```

---

### Task 6: Full verification and visual QA

**Files:**
- Verify: `miniprogram/`
- Verify: `tests/`
- Reference: `docs/superpowers/specs/assets/wechat-mini-program-materials-simple-direction.png`

**Interfaces:**
- Consumes: all redesigned pages and the approved visual target.
- Produces: automated regression evidence and same-viewport visual comparison evidence.

- [ ] **Step 1: Run syntax and static asset checks**

```bash
find miniprogram -name '*.json' -print0 | xargs -0 -n1 python -m json.tool >/dev/null
for file in miniprogram/app.js miniprogram/utils/*.js miniprogram/components/*/*.js miniprogram/pages/connection/*.js miniprogram/pages/plan/*.js miniprogram/pages/materials/*.js miniprogram/pages/products/*.js miniprogram/pages/inventory/*.js miniprogram/pages/scraps/*.js; do node --check "$file"; done
```

Expected: both commands exit 0 with no syntax errors.

- [ ] **Step 2: Run focused and full automated suites**

```bash
.venv/bin/python -m pytest tests/test_miniprogram_foundation.py tests/test_mobile_connection.py tests/test_mobile_idempotency.py -q
node --test tests/miniprogram_connection.test.js
.venv/bin/python -m pytest -q
```

Expected: all focused Python tests, Node tests, and the full Python suite PASS.

- [ ] **Step 3: Open the project in WeChat Developer Tools**

```bash
/Applications/wechatwebdevtools.app/Contents/MacOS/cli open --project "$PWD/miniprogram" --lang zh
```

Expected: the project opens and compiles without WXML, WXSS, SVG, or component errors.

- [ ] **Step 4: Verify core states and routes at 375px and a common Android width**

Check connection setup/error, plan unavailable state, materials loading/error/success, materials pending action, product inventory, product inbound/outbound confirmation, scrap pending confirmation, and both ledgers. Confirm no horizontal scrolling, no clipped values, one green primary action per screen, and safe-area clearance.

- [ ] **Step 5: Compare the implemented materials home to the approved target**

Capture the materials home at `390 × 844`. Compare it beside `docs/superpowers/specs/assets/wechat-mini-program-materials-simple-direction.png` in one visual QA input. Fix visible differences in hierarchy, 32rpx page margin, title size, grouped-list radius, row height, icon scale, separators, pending color, primary button, and tab-bar clearance; then capture and compare again.

- [ ] **Step 6: Confirm the worktree contains no unrelated staged edits**

```bash
git status --short
git diff --check
```

Expected: the pre-existing modification to `docs/superpowers/plans/2026-08-04-wechat-mini-program-phase-1-foundation.md` remains unstaged and unchanged by this plan; redesign changes are committed and `git diff --check` reports no whitespace errors.
