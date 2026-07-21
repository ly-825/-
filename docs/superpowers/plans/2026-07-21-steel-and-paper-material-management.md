# Steel and Paper Material Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename and normalize steel material management, then add an independent paper specification, batch inventory, FIFO outbound, transaction, reversal, and export workflow.

**Architecture:** Keep the existing steel tables and routes, but centralize steel dimension formatting and numeric ordering in a small formatting service. Add three independent paper tables, a focused paper inventory service for validation/grouping/FIFO/reversal, and a separate FastAPI router for the five paper administration pages. Reuse only shared HTML shell, export transport, operation logging, and database session infrastructure.

**Tech Stack:** Python 3, FastAPI 0.115, SQLAlchemy 2.0, SQLite, `decimal.Decimal`, server-rendered HTML, `unittest`, OpenPyXL.

## Global Constraints

- Paper specifications, inventory batches, and transactions use independent tables and do not use `material_inventory` or `inventory_transaction_records`.
- Steel names use `厚度 × 宽度 × 长度`; steel thickness is normalized and displayed with exactly one decimal place.
- Steel lists order by enabled state when applicable, then numeric thickness, width, length, and material.
- Paper roll size uses `厚度 × 内径 × 外径`; paper sheet model and size use `厚度 × 长度 × 宽度`.
- Paper quantities are positive integers; roll unit is 圈 and sheet unit is 张.
- Paper unit prices are CNY per roll or sheet, stored with two decimal places on inbound batches.
- Paper outbound is FIFO by oldest batch and must fail atomically when stock is insufficient.
- Paper stock price range includes only batches whose current quantity is greater than zero.
- Existing product, scrap, steel inventory, and steel transaction rows remain in their current tables.
- Production code is written only after a focused test has been observed failing for the intended reason.

---

## File Structure

- Create `app/services/material_formats.py`: steel and paper dimension normalization, display formatting, generated names, and sort keys.
- Create `app/services/paper_inventory.py`: paper validation, grouping, FIFO outbound, reversal, and snapshots.
- Create `app/paper_admin_pages.py`: paper specification, inbound, inventory, detail, outbound, transaction, and reversal routes.
- Create `tests/test_steel_material_management.py`: steel naming, precision, ordering, outbound layout, and transaction-table regression tests.
- Create `tests/test_paper_material_management.py`: paper model, service, page, FIFO, price range, and reversal tests.
- Create `tests/test_paper_exports.py`: paper inventory and transaction export tests.
- Modify `app/models.py`: add the three paper SQLAlchemy models.
- Modify `app/schema_migrations.py`: register paper timestamps and create paper tables/indexes for existing installations.
- Modify `app/admin_pages.py`: steel integration, navigation labels/links, outbound order, and wide transaction style.
- Modify `app/services/inventory_summaries.py`: expose normalized steel names in stock summaries.
- Modify `app/services/excel_export.py`: steel ordering/formatting plus paper export modules.
- Modify `app/main.py`: register the paper administration router.
- Modify `tests/test_inventory_grouping_pages.py`: replace superseded free-form steel-model expectations with generated dimension-name expectations.

---

### Task 1: Material Dimension Formatting Boundary

**Files:**
- Create: `app/services/material_formats.py`
- Create: `tests/test_steel_material_management.py`

**Interfaces:**
- Produces: `normalize_steel_thickness(value: float | Decimal) -> float`
- Produces: `format_number(value: float | Decimal | int | None) -> str`
- Produces: `format_steel_thickness(value: float | Decimal) -> str`
- Produces: `steel_spec_name(thickness: float, width: float, length: float) -> str`
- Produces: `steel_dimension_sort_key(thickness: float | None, width: float | None, length: float | None, material: str = "") -> tuple`
- Produces: `paper_roll_size(thickness: float, inner_diameter: float, outer_diameter: float) -> str`
- Produces: `paper_sheet_model(thickness: float, length: float, width: float) -> str`

- [ ] **Step 1: Write the failing formatting tests**

```python
import unittest

from app.services.material_formats import (
    format_steel_thickness,
    paper_roll_size,
    paper_sheet_model,
    steel_dimension_sort_key,
    steel_spec_name,
)


class SteelMaterialFormattingTest(unittest.TestCase):
    def test_steel_name_keeps_one_decimal_thickness_first(self) -> None:
        self.assertEqual(steel_spec_name(3, 130, 1270), "3.0×130×1270")
        self.assertEqual(format_steel_thickness(1.84), "1.8")

    def test_steel_sort_key_is_numeric_thickness_width_length(self) -> None:
        values = [(2, 130, 1270), (1.8, 270, 1000), (1.8, 140, 1340)]
        self.assertEqual(
            sorted(values, key=lambda value: steel_dimension_sort_key(value[0], value[1], value[2])),
            [(1.8, 140, 1340), (1.8, 270, 1000), (2, 130, 1270)],
        )

    def test_paper_sizes_put_thickness_first(self) -> None:
        self.assertEqual(paper_roll_size(0.5, 80, 120), "0.5×80×120")
        self.assertEqual(paper_sheet_model(0.5, 400, 400), "0.5×400×400")
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `.venv/bin/python -m unittest tests.test_steel_material_management -v`

Expected: import failure for `app.services.material_formats`.

- [ ] **Step 3: Implement the formatting service**

```python
from decimal import Decimal, ROUND_HALF_UP

from app.services.drawing_search import natural_sort_key


def _decimal(value: float | Decimal | int) -> Decimal:
    return Decimal(str(value))


def normalize_steel_thickness(value: float | Decimal) -> float:
    return float(_decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def format_number(value: float | Decimal | int | None) -> str:
    if value is None:
        return "-"
    normalized = _decimal(value).normalize()
    return format(normalized, "f")


def format_steel_thickness(value: float | Decimal) -> str:
    return f"{normalize_steel_thickness(value):.1f}"


def steel_spec_name(thickness: float, width: float, length: float) -> str:
    return f"{format_steel_thickness(thickness)}×{format_number(width)}×{format_number(length)}"


def steel_dimension_sort_key(
    thickness: float | None,
    width: float | None,
    length: float | None,
    material: str = "",
) -> tuple:
    return (
        float("inf") if thickness is None else normalize_steel_thickness(thickness),
        float("inf") if width is None else width,
        float("inf") if length is None else length,
        natural_sort_key(material),
    )


def paper_roll_size(thickness: float, inner_diameter: float, outer_diameter: float) -> str:
    return "×".join(format_number(value) for value in (thickness, inner_diameter, outer_diameter))


def paper_sheet_model(thickness: float, length: float, width: float) -> str:
    return "×".join(format_number(value) for value in (thickness, length, width))
```

- [ ] **Step 4: Run the test and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_steel_material_management -v`

Expected: 3 tests pass.

- [ ] **Step 5: Commit the formatting boundary**

```bash
git add app/services/material_formats.py tests/test_steel_material_management.py
git commit -m "feat: add material dimension formatting"
```

---

### Task 2: Generated Steel Names and Numeric Ordering

**Files:**
- Modify: `app/admin_pages.py:1924-2642`
- Modify: `app/services/inventory_summaries.py:50-93`
- Modify: `app/services/excel_export.py:421-433`
- Modify: `tests/test_steel_material_management.py`
- Modify: `tests/test_inventory_grouping_pages.py:219-449`

**Interfaces:**
- Consumes: Task 1 formatting functions.
- Produces: generated steel names on create/update/inbound and identical ordering on specifications, stock, inbound select, outbound availability, and export.

- [ ] **Step 1: Add failing page and persistence tests**

Add tests that create `1.8×270×1000`, `1.8×140×1340`, and `3.0×130×1270` dimensions in deliberately reversed insertion order, then assert:

```python
self.assertLess(html.index(">1.8×140×1340</td>"), html.index(">1.8×270×1000</td>"))
self.assertLess(html.index(">1.8×270×1000</td>"), html.index(">3.0×130×1270</td>"))
self.assertIn('name="thickness" type="number" step="0.1"', html)
```

Call `create_raw_plate_specification` with `spec_name="ignored"` only if the function still accepts the legacy argument during the transition, refresh the row, and assert:

```python
self.assertEqual(spec.spec_name, "3.0×130×1270")
self.assertEqual(spec.thickness, 3.0)
```

Render `raw_plates_page`, `raw_plate_inbound_page`, and `raw_plate_outbound_page` with matching inventory rows and assert the same name and order appear in all three outputs. Replace tests that expect `MODEL-X`, `CUSTOM-2`, or arbitrary `S-2` display names with generated dimension names while retaining batch-code assertions.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_steel_material_management tests.test_inventory_grouping_pages -v`

Expected: failures showing free-form names, natural-text ordering, and `step="0.01"`.

- [ ] **Step 3: Normalize steel create/update/inbound paths**

In both specification POST handlers, normalize before assignment:

```python
thickness_value = normalize_steel_thickness(thickness)
spec_name_value = steel_spec_name(thickness_value, width, length)
```

Assign `spec_name_value` and `thickness_value`. Remove the editable specification-name control and replace it with explanatory copy plus a read-only preview. Change all steel thickness controls in specification, inbound, edit, and outbound forms to `step="0.1"`.

In manual steel inbound, always calculate:

```python
thickness = normalize_steel_thickness(thickness)
raw_plate_model_value = steel_spec_name(thickness, width, length)
usable_size = f"{raw_plate_model_value}mm"
```

For a selected specification, use its normalized structured values and generated name. Keep `material_code` as the independently entered batch number.

- [ ] **Step 4: Apply one numeric ordering rule everywhere**

Use these exact keys:

```python
specs.sort(
    key=lambda spec: (
        -int(bool(spec.is_active)),
        *steel_dimension_sort_key(spec.thickness, spec.width, spec.length, spec.material),
    )
)

grouped_rows.sort(
    key=lambda group: steel_dimension_sort_key(
        group["thickness"], group["width"], group["length"], group["material"]
    )
)
```

Use the specification key before building inbound options. Sort outbound summary values before rendering. Use the group key in Excel export. In `raw_plate_summary_rows`, set `spec_name` from `steel_spec_name(item.thickness, item.width, item.length)` while leaving batch codes untouched.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_steel_material_management tests.test_inventory_grouping_pages -v`

Expected: all focused tests pass.

- [ ] **Step 6: Commit the steel naming and ordering change**

```bash
git add app/admin_pages.py app/services/inventory_summaries.py app/services/excel_export.py tests/test_steel_material_management.py tests/test_inventory_grouping_pages.py
git commit -m "feat: normalize steel specifications"
```

---

### Task 3: Steel Navigation, Outbound Layout, and Readable Transactions

**Files:**
- Modify: `app/admin_pages.py:965-1099,2556-2790`
- Modify: `tests/test_steel_material_management.py`
- Modify: `tests/test_inventory_grouping_pages.py:30-48`

**Interfaces:**
- Produces: `钢板材料管理` navigation label.
- Produces: outbound section order search → available specifications → confirmation.
- Produces: `.wide-transaction-table` and `.nowrap-cell` presentation hooks.

- [ ] **Step 1: Write failing HTML-structure tests**

```python
def test_steel_navigation_and_outbound_layout(self) -> None:
    navigation = page("测试", "").body.decode("utf-8")
    outbound = raw_plate_outbound_page(db=self.db).body.decode("utf-8")
    self.assertIn("<summary>钢板材料管理</summary>", navigation)
    self.assertLess(outbound.index('name="thickness"'), outbound.index('name="material"'))
    self.assertLess(outbound.index("<h2>当前可用规格</h2>"), outbound.index("<h2>确认出库</h2>"))

def test_steel_transactions_use_wide_non_wrapping_columns(self) -> None:
    html = raw_plate_transactions_page(db=self.db).body.decode("utf-8")
    self.assertIn('<table class="wide-transaction-table">', html)
    self.assertIn('class="nowrap-cell">', html)
    self.assertIn(".wide-transaction-table { min-width:1400px;", page("测试", "").body.decode("utf-8"))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_steel_material_management tests.test_inventory_grouping_pages -v`

Expected: missing navigation label/class and reversed outbound section order.

- [ ] **Step 3: Implement the page structure and CSS**

Change the navigation summary text to `钢板材料管理`. Reorder the outbound body so the available-specification card is emitted immediately after the search card and before the confirmation card. Within the search form emit fields in this order: thickness, material, width, length, location.

Add the following shell styles:

```css
.wide-transaction-table { min-width:1400px; }
.wide-transaction-table .nowrap-cell { white-space:nowrap; overflow-wrap:normal; }
.wide-transaction-table .remark-cell { min-width:220px; white-space:normal; }
```

Render the steel transaction table with `class="wide-transaction-table"`. Apply `nowrap-cell` to batch, material, size, location, type, quantity, before, after, customer, operator, time, and action cells; apply `remark-cell` only to remarks.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_steel_material_management tests.test_inventory_grouping_pages -v`

Expected: all focused tests pass.

- [ ] **Step 5: Commit the steel UI corrections**

```bash
git add app/admin_pages.py tests/test_steel_material_management.py tests/test_inventory_grouping_pages.py
git commit -m "fix: improve steel inventory pages"
```

---

### Task 4: Independent Paper Tables and Runtime Schema

**Files:**
- Modify: `app/models.py`
- Modify: `app/schema_migrations.py`
- Create: `tests/test_paper_material_management.py`

**Interfaces:**
- Produces: `PaperSpecification`, `PaperInventoryBatch`, and `PaperInventoryTransaction` ORM models.
- Produces: paper table/index creation through both `Base.metadata.create_all` and `ensure_runtime_schema`.

- [ ] **Step 1: Write failing model and schema tests**

Create an in-memory database and assert all three table names exist. Persist a roll specification, inventory batch with `Decimal("12.30")`, and inbound transaction, then assert the relationships by foreign-key IDs and the exact two-decimal price value. Add a migration test that starts with only `runtime_schema_migrations`, calls `ensure_runtime_schema` twice, and asserts the paper tables and indexes exist without duplicate-table errors.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `.venv/bin/python -m unittest tests.test_paper_material_management -v`

Expected: imports for the three paper models fail.

- [ ] **Step 3: Add the ORM models**

Implement:

```python
class PaperSpecification(Base):
    __tablename__ = "paper_specifications"
    id = mapped_column(primary_key=True, index=True)
    paper_type = mapped_column(String(20), index=True)
    model = mapped_column(String(100), index=True)
    material_name = mapped_column(String(100), index=True)
    thickness = mapped_column(Float, index=True)
    inner_diameter = mapped_column(Float, nullable=True)
    outer_diameter = mapped_column(Float, nullable=True)
    length = mapped_column(Float, nullable=True)
    width = mapped_column(Float, nullable=True)
    remark = mapped_column(String(255), nullable=True)
    is_active = mapped_column(Integer, default=1, index=True)
    created_at = mapped_column(DateTime, default=china_now)
    updated_at = mapped_column(DateTime, default=china_now, onupdate=china_now)


class PaperInventoryBatch(Base):
    __tablename__ = "paper_inventory_batches"
    id = mapped_column(primary_key=True, index=True)
    specification_id = mapped_column(ForeignKey("paper_specifications.id"), index=True)
    batch_code = mapped_column(String(100), index=True)
    paper_type = mapped_column(String(20), index=True)
    model = mapped_column(String(100), index=True)
    material_name = mapped_column(String(100), index=True)
    thickness = mapped_column(Float, index=True)
    inner_diameter = mapped_column(Float, nullable=True)
    outer_diameter = mapped_column(Float, nullable=True)
    length = mapped_column(Float, nullable=True)
    width = mapped_column(Float, nullable=True)
    quantity = mapped_column(Integer)
    unit_price = mapped_column(Numeric(12, 2))
    location = mapped_column(String(100), nullable=True, index=True)
    status = mapped_column(String(20), default="available", index=True)
    created_at = mapped_column(DateTime, default=china_now)
    updated_at = mapped_column(DateTime, default=china_now, onupdate=china_now)


class PaperInventoryTransaction(Base):
    __tablename__ = "paper_inventory_transactions"
    id = mapped_column(primary_key=True, index=True)
    inventory_id = mapped_column(ForeignKey("paper_inventory_batches.id"), index=True)
    transaction_type = mapped_column(String(20), index=True)
    quantity = mapped_column(Integer)
    before_quantity = mapped_column(Integer)
    after_quantity = mapped_column(Integer)
    reversed_transaction_id = mapped_column(Integer, nullable=True, index=True)
    operator_name = mapped_column(String(100), nullable=True)
    customer_name = mapped_column(String(100), nullable=True)
    remark = mapped_column(String(255), nullable=True)
    created_at = mapped_column(DateTime, default=china_now)
```

Use typed `Mapped[str]`, `Mapped[float | None]`, `Mapped[int]`, `Mapped[Decimal]`, and `Mapped[datetime]` declarations matching existing model style. Import `Numeric` and `Decimal` where required by annotations.

- [ ] **Step 4: Add idempotent runtime schema creation**

Register all paper timestamp columns in `TIMESTAMP_COLUMNS`. In `ensure_runtime_schema`, create each missing paper table with the same columns and create indexes for specification type/model/material/status, batch specification/model/material/location/status, and transaction inventory/type/reversal. Refresh the initial inspector table set after table creation only when subsequent migration logic needs it.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_paper_material_management tests.test_china_time -v`

Expected: all paper model/schema and existing migration tests pass.

- [ ] **Step 6: Commit the paper schema**

```bash
git add app/models.py app/schema_migrations.py tests/test_paper_material_management.py
git commit -m "feat: add independent paper inventory tables"
```

---

### Task 5: Paper Validation, Grouping, FIFO, and Reversal Service

**Files:**
- Create: `app/services/paper_inventory.py`
- Modify: `tests/test_paper_material_management.py`

**Interfaces:**
- Produces: `normalize_paper_specification(paper_type: str, model: str, material_name: str, thickness: float, inner_diameter: float | None, outer_diameter: float | None, length: float | None, width: float | None) -> dict[str, object]`
- Produces: `paper_specification_sort_key(spec: PaperSpecification) -> tuple`
- Produces: `paper_batch_size(batch: PaperInventoryBatch) -> str`
- Produces: `paper_inventory_groups(batches: list[PaperInventoryBatch]) -> list[dict[str, object]]`
- Produces: `outbound_paper_fifo(specification_id: int, quantity: int, location: str | None, customer_name: str | None, operator_name: str | None, remark: str | None, db: Session) -> list[PaperInventoryTransaction]`
- Produces: `reverse_paper_transaction(transaction_id: int, operator_name: str | None, remark: str | None, db: Session) -> PaperInventoryTransaction`

- [ ] **Step 1: Write failing service tests**

Cover these exact behaviors:

```python
self.assertEqual(normalize_paper_specification("sheet", "ignored", "白纸", 0.5, None, None, 400, 400)["model"], "0.5×400×400")
with self.assertRaisesRegex(HTTPException, "外径必须大于内径"):
    normalize_paper_specification("roll", "Tnx236.2A", "蓝纸", 0.5, 120, 80, None, None)
self.assertEqual(group["price_min"], Decimal("10.00"))
self.assertEqual(group["price_max"], Decimal("12.50"))
```

Create two batches at distinct timestamps with quantities 2 and 5. Outbound 4 and assert the first becomes 0, second becomes 3, and two out transactions record quantities 2 and 2. Attempt an outbound larger than available and assert neither batch changes. Reverse one out transaction and assert only its batch quantity is restored and its unit price is unchanged. Exhaust the cheaper batch and assert the remaining stock group shows only the price of the nonzero batch.

- [ ] **Step 2: Run the focused service tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_paper_material_management -v`

Expected: missing `app.services.paper_inventory` imports.

- [ ] **Step 3: Implement normalization and grouping**

`normalize_paper_specification` must reject paper types outside `{"roll", "sheet"}`, blank material names, nonpositive dimensions, blank roll models, and roll outer diameter less than or equal to inner diameter. Return irrelevant dimensions as `None`. Generate sheet model with `paper_sheet_model`.

Group only batches with `quantity > 0` and key them by the full snapshot tuple. Each group contains quantity sum, batch count, locations set, oldest/latest timestamps, and `price_min`/`price_max` computed from nonzero batches. Use unit labels `圈` for roll and `张` for sheet.

- [ ] **Step 4: Implement atomic FIFO and reversal**

FIFO first loads all matching positive batches ordered by `created_at ASC, id ASC`, optionally filters exact location, computes total availability, and raises `HTTPException(400, f"纸材库存不足，当前可出库 {available} {unit}")` before mutating when insufficient. It then updates batch quantity/status and appends one transaction per affected batch.

Reversal mirrors the existing inventory reversal contract: reject missing, unsupported, or already reversed rows; reverse inbound only if current batch quantity can cover the original inbound quantity; set both records' reversal links; update status; never modify `unit_price`.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_paper_material_management -v`

Expected: all normalization, grouping, FIFO, atomicity, and reversal tests pass.

- [ ] **Step 6: Commit the paper domain service**

```bash
git add app/services/paper_inventory.py tests/test_paper_material_management.py
git commit -m "feat: add paper inventory domain service"
```

---

### Task 6: Paper Specification and Inbound Pages

**Files:**
- Create: `app/paper_admin_pages.py`
- Modify: `app/main.py`
- Modify: `app/admin_pages.py:1089-1110`
- Modify: `tests/test_paper_material_management.py`

**Interfaces:**
- Produces routes: `GET/POST /admin/paper-specifications`, `GET/POST /admin/paper-specifications/{id}/edit`, `POST /admin/paper-specifications/{id}/toggle`, `GET/POST /admin/paper-materials/inbound`.
- Produces paper navigation links through the shared `page()` shell.

- [ ] **Step 1: Write failing route/page tests**

Assert the shared shell includes a `纸材材料管理` section after `钢板材料管理` with specification, inbound, outbound, inventory, and transaction links in that order. Render the specification page and assert the type selector plus roll/sheet field containers exist. POST a sheet specification with a fake model and assert the stored model is generated. POST a roll and assert its manual model remains. POST inbound with quantity 20 and unit price `12.30`, then assert the batch snapshot, Decimal price, and inbound transaction.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_paper_material_management -v`

Expected: missing paper router/page functions and navigation links.

- [ ] **Step 3: Implement specification routes**

Use `normalize_paper_specification` for both create and edit. The form uses `paper_type` to show `.paper-roll-fields` or `.paper-sheet-fields`; a small script toggles `hidden` and `required` attributes and clears irrelevant inputs. The sheet model input is read-only and generated from current thickness/length/width in the browser only as a preview; the server remains authoritative.

Sort rows with `paper_specification_sort_key`, put enabled specifications first, and provide edit/toggle actions matching the steel page interaction.

- [ ] **Step 4: Implement inbound routes**

The GET page lists active specifications in paper sort order and emits option data attributes for type, model, material, size, and unit. The POST handler validates an active numeric specification ID, positive integer quantity, and `Decimal(unit_price).quantize(Decimal("0.01"), ROUND_HALF_UP) >= 0`; it creates a batch snapshot and an inbound transaction with before 0 and after quantity. Generate a missing batch code as `PAPER-YYYYMMDDHHMMSS`.

The confirmation form lists specification, type, material, size, quantity with 圈/张, unit price with 元/圈 or 元/张, location, operator, and remark.

- [ ] **Step 5: Register the router and navigation**

In `app/main.py` import `paper_admin_pages` and call:

```python
app.include_router(paper_admin_pages.router, tags=["纸材后台"])
```

Add the five paper links to a new navigation `<details>` block after the steel block.

- [ ] **Step 6: Run tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_paper_material_management tests.test_inventory_grouping_pages -v`

Expected: all paper specification/inbound and navigation tests pass.

- [ ] **Step 7: Commit paper specification and inbound UI**

```bash
git add app/paper_admin_pages.py app/main.py app/admin_pages.py tests/test_paper_material_management.py
git commit -m "feat: add paper specification and inbound pages"
```

---

### Task 7: Paper Inventory, Outbound, Transactions, and Detail Pages

**Files:**
- Modify: `app/paper_admin_pages.py`
- Modify: `tests/test_paper_material_management.py`

**Interfaces:**
- Produces routes: `GET /admin/paper-materials`, `GET /admin/paper-materials/detail`, `GET/POST /admin/paper-materials/outbound`, `GET /admin/paper-materials/transactions`, and `POST /admin/paper-materials/transactions/{id}/reverse`.

- [ ] **Step 1: Write failing inventory-page tests**

Create three batches for one model at prices 10.00, 12.50, and 99.00, with the last batch quantity zero. Assert inventory HTML contains `¥10.00～¥12.50`, excludes `¥99.00`, shows the correct 圈/张 quantity unit, and links to detail. Assert detail HTML contains each live batch code, unit price, location, and related transactions.

Assert outbound HTML orders search, available specifications, then confirmation; selecting a group preserves `specification_id`. Submit a cross-batch FIFO outbound and assert redirects to paper inventory. Assert transaction HTML includes model, material name, one-line size, quantities, price, customer, and reversal form.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_paper_material_management -v`

Expected: missing inventory/outbound/transaction routes and HTML.

- [ ] **Step 3: Implement inventory and detail pages**

Support filters for keyword, paper type, material name, thickness, and location. Render groups from `paper_inventory_groups` using paper sort keys. Format prices as:

```python
price_text = (
    f"¥{group['price_min']:.2f}"
    if group["price_min"] == group["price_max"]
    else f"¥{group['price_min']:.2f}～¥{group['price_max']:.2f}"
)
```

The detail query contains the full group identity, and the page lists current batches plus transactions restricted to those batch IDs.

- [ ] **Step 4: Implement outbound and transaction pages**

Outbound GET filters and groups positive batches, with available groups above the confirmation form. POST passes validated inputs to `outbound_paper_fifo`, records operation logs for each affected batch, commits once, and redirects to `/admin/paper-materials`.

Transaction GET limits to the latest 500 records ordered descending, filters by keyword/material/type/flow type, and renders a wide table. Reverse POST calls `reverse_paper_transaction`, records an operation log, commits once, and redirects to the paper transaction page.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_paper_material_management -v`

Expected: all paper UI, FIFO, detail, transaction, and reversal tests pass.

- [ ] **Step 6: Commit the complete paper workflow**

```bash
git add app/paper_admin_pages.py tests/test_paper_material_management.py
git commit -m "feat: complete paper inventory workflow"
```

---

### Task 8: Paper Exports and Full Regression Verification

**Files:**
- Modify: `app/services/excel_export.py`
- Create: `tests/test_paper_exports.py`
- Modify: `app/paper_admin_pages.py`

**Interfaces:**
- Produces export modules `paper_inventory` and `paper_transactions` through the existing `/admin/exports/{module}` transport.

- [ ] **Step 1: Write failing export tests**

Create roll and sheet batches and transactions. Assert `build_export_rows("paper_inventory", {}, db)` returns headings:

```python
["类型", "型号", "纸材名称/材质", "尺寸", "库存数量", "单位", "最低单价", "最高单价", "批次数", "库位", "最近更新时间"]
```

Assert `build_export_rows("paper_transactions", {}, db)` returns headings:

```python
["流水号", "类型", "批次编号", "纸材类型", "型号", "纸材名称/材质", "尺寸", "数量", "单位", "单价", "操作前库存", "操作后库存", "客户/去向", "操作人", "备注", "创建时间"]
```

Assert zero-stock prices do not affect inventory min/max and row order follows the paper sort key.

- [ ] **Step 2: Run export tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_paper_exports -v`

Expected: HTTP 404-style `不支持的导出模块` error from `build_export_rows`.

- [ ] **Step 3: Implement export modules and links**

Add `paper_inventory` and `paper_transactions` to `EXPORT_MODULES`. Build inventory rows from positive paper batches and `paper_inventory_groups`; build transaction rows by joining independent paper transactions to paper batches. Format prices with two decimals and times with `_fmt_time`. Add export buttons to paper inventory and paper transaction pages.

- [ ] **Step 4: Run focused export tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_paper_exports -v`

Expected: all paper export tests pass.

- [ ] **Step 5: Run formatting and static checks**

Run: `git diff --check`

Expected: exit 0 with no output.

Run: `.venv/bin/python -m compileall -q app tests`

Expected: exit 0 with no output.

- [ ] **Step 6: Run the complete automated test suite**

Run: `.venv/bin/python -m unittest discover -s tests -v`

Expected: all tests pass with zero failures and zero errors.

- [ ] **Step 7: Perform browser-level page verification**

Start the app with:

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Verify at 1440×900 and a narrow viewport:

- `/admin/raw-plate-specifications`: generated steel names, `3.0`, and numeric order.
- `/admin/raw-plates/outbound`: thickness-first search and available list above confirmation.
- `/admin/raw-plates/transactions`: readable batch and size cells with horizontal scroll when needed.
- `/admin/paper-specifications`: roll/sheet field switching and generated sheet preview.
- `/admin/paper-materials/inbound`: quantity, unit, price, and confirmation summary.
- `/admin/paper-materials`: price range and detail navigation.
- `/admin/paper-materials/outbound`: FIFO selection and confirmation.
- `/admin/paper-materials/transactions`: wide readable rows and reversal form.

- [ ] **Step 8: Commit exports and final verification fixes**

```bash
git add app/services/excel_export.py app/paper_admin_pages.py tests/test_paper_exports.py
git commit -m "feat: export paper inventory records"
```

---

## Completion Checklist

- [ ] Steel navigation is named `钢板材料管理`; paper has its own navigation group.
- [ ] Steel names and display preserve one decimal thickness and sort numerically by thickness, width, and length.
- [ ] Steel outbound and transaction layout match the approved screenshots feedback.
- [ ] Paper uses three independent tables and five complete administration pages.
- [ ] Roll and sheet model/size rules put thickness first.
- [ ] Inbound batch price, live-stock price range, FIFO, atomic insufficiency, and reversal behavior have regression tests.
- [ ] Paper inventory and paper transactions export to Excel.
- [ ] Full tests, compile check, diff check, and browser verification have fresh successful evidence.
