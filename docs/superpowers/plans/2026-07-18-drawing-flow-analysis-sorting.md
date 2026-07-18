# Drawing Flow Analysis and Sorting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add dynamic drawing-parameter result cells, switchable product inbound/outbound analysis, and safe ascending/descending sorting on five common summary lists without page-level horizontal scrolling.

**Architecture:** Introduce a small allow-listed in-memory sorting service used after existing aggregation, extend the current outbound analysis service with a generic product-flow entry point while retaining its compatibility wrapper, and keep page rendering in `admin_pages.py`. Extract inventory aggregation into a focused service so HTML pages and Excel exports consume the same grouped and sorted rows.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, SQLite, server-rendered HTML/CSS, `unittest`, Playwright/Chromium for responsive verification.

## Global Constraints

- Keep `/admin/reports/product-outbound` working; use `flow_type=in|out` and default to `out`.
- Dynamic drawing parameters replace the existing status cell; they must not add a table column.
- Ordinary text filters do not trigger the dynamic parameter cell.
- Only allow-listed `sort_by` values are accepted; invalid sort inputs fall back to each page's current default order.
- Natural text ordering must place `TNX2` before `TNX10`; numeric values sort numerically; empty values always appear last.
- Apply user-selectable sorting only to raw-plate specifications, confirmed drawings, product inventory, raw-plate inventory, and scrap inventory.
- Transaction and per-order detail lists remain time-descending.
- At 1366x768 and 390x844, page-level `scrollWidth` must equal `clientWidth`.
- Do not add a database table, schema migration, or new third-party dependency.

---

### Task 1: Allow-Listed Summary Sorting Service

**Files:**
- Create: `app/services/list_sorting.py`
- Create: `tests/test_list_sorting.py`

**Interfaces:**
- Consumes: `natural_sort_key(value: object) -> tuple` from `app.services.drawing_search`.
- Produces: `sort_records(records, sort_by, sort_dir, key_map) -> tuple[list, str, str]` and `sort_select_options(options, selected) -> str`.

- [ ] **Step 1: Write failing tests for natural, numeric, descending, empty-last, and invalid input behavior**

```python
import unittest

from app.services.drawing_search import natural_sort_key
from app.services.list_sorting import sort_records


class ListSortingTest(unittest.TestCase):
    def test_sorts_natural_text_in_both_directions(self):
        rows = [{"code": "TNX10"}, {"code": "TNX2"}, {"code": "TNX1"}]
        asc, by, direction = sort_records(rows, "code", "asc", {"code": lambda row: natural_sort_key(row["code"])})
        desc, _, _ = sort_records(rows, "code", "desc", {"code": lambda row: natural_sort_key(row["code"])})
        self.assertEqual([row["code"] for row in asc], ["TNX1", "TNX2", "TNX10"])
        self.assertEqual([row["code"] for row in desc], ["TNX10", "TNX2", "TNX1"])
        self.assertEqual((by, direction), ("code", "asc"))

    def test_sorts_numbers_and_keeps_empty_values_last(self):
        rows = [{"value": None}, {"value": 10}, {"value": 2}]
        result, _, _ = sort_records(rows, "value", "desc", {"value": lambda row: row["value"]})
        self.assertEqual([row["value"] for row in result], [10, 2, None])

    def test_invalid_sort_returns_original_order_and_empty_selection(self):
        rows = [{"value": 2}, {"value": 1}]
        result, by, direction = sort_records(rows, "__bad__", "sideways", {"value": lambda row: row["value"]})
        self.assertEqual(result, rows)
        self.assertEqual((by, direction), ("", ""))
```

- [ ] **Step 2: Run tests and verify the missing module failure**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_list_sorting`

Expected: `ModuleNotFoundError: No module named 'app.services.list_sorting'`.

- [ ] **Step 3: Implement the minimal allow-listed sorter**

```python
from collections.abc import Callable, Iterable, Mapping
from html import escape
from typing import Any


def sort_records(
    records: Iterable[Any],
    sort_by: str,
    sort_dir: str,
    key_map: Mapping[str, Callable[[Any], Any]],
) -> tuple[list[Any], str, str]:
    rows = list(records)
    field = (sort_by or "").strip()
    direction = (sort_dir or "").strip().lower()
    if field not in key_map or direction not in {"asc", "desc"}:
        return rows, "", ""
    key_fn = key_map[field]
    valued = [row for row in rows if key_fn(row) not in (None, "")]
    empty = [row for row in rows if key_fn(row) in (None, "")]
    valued.sort(key=key_fn, reverse=direction == "desc")
    return valued + empty, field, direction


def sort_select_options(options: Mapping[str, str], selected: str) -> str:
    return "".join(
        f"<option value='{escape(value)}' {'selected' if value == selected else ''}>{escape(label)}</option>"
        for value, label in options.items()
    )
```

- [ ] **Step 4: Run the focused tests**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_list_sorting`

Expected: all tests pass.

- [ ] **Step 5: Commit the sorting service**

```bash
git add app/services/list_sorting.py tests/test_list_sorting.py
git commit -m '增加列表安全排序服务'
```

---

### Task 2: Confirmed Drawing Dynamic Parameter Cell and Sorting

**Files:**
- Modify: `app/admin_pages.py:3703-3800`
- Modify: `app/services/excel_export.py:290-335`
- Modify: `tests/test_product_catalog_search.py`
- Modify: `tests/test_drawing_search_sorting.py`

**Interfaces:**
- Consumes: `sort_records` and `sort_select_options` from Task 1.
- Produces: `drawing_filter_values(drawing, filters) -> list[str]` and `drawing_rows(drawings, parameter_filters=None) -> str`.

- [ ] **Step 1: Add failing page tests for one parameter, multiple parameters, ordinary text, natural sorting, and invalid sorting**

```python
def test_confirmed_drawing_replaces_status_with_actual_filtered_parameters(self):
    with self.Session() as db:
        db.add(ProductDrawing(product_code="DYN-1", dxf_file_url="/tmp/dyn.dxf", pressure_angle=30, module=2, confirmed=1, is_active=1))
        db.commit()
        single = confirmed_drawings_page(pressure_angle="30", db=db).body.decode("utf-8")
        multiple = confirmed_drawings_page(pressure_angle="30", module="2", db=db).body.decode("utf-8")
        text_only = confirmed_drawings_page(q="DYN", db=db).body.decode("utf-8")
    self.assertIn("<th>筛选参数</th>", single)
    self.assertIn("压力角 30°", single)
    self.assertIn("模数 2", multiple)
    self.assertIn("<th>状态</th>", text_only)
    self.assertIn("已确认", text_only)

def test_confirmed_drawings_support_allow_list_sorting(self):
    with self.Session() as db:
        db.add_all([
            ProductDrawing(product_code="TNX10", product_name="Ten", dxf_file_url="/tmp/10.dxf", confirmed=1, is_active=1),
            ProductDrawing(product_code="TNX2", product_name="Two", dxf_file_url="/tmp/2.dxf", confirmed=1, is_active=1),
            ProductDrawing(product_code="TNX1", product_name="One", dxf_file_url="/tmp/1.dxf", confirmed=1, is_active=1),
        ])
        db.commit()
        desc = confirmed_drawings_page(sort_by="product_code", sort_dir="desc", db=db).body.decode("utf-8")
        fallback = confirmed_drawings_page(sort_by="__bad__", sort_dir="desc", db=db).body.decode("utf-8")
    self.assertLess(desc.index(">TNX10</td>"), desc.index(">TNX2</td>"))
    self.assertLess(desc.index(">TNX2</td>"), desc.index(">TNX1</td>"))
    self.assertLess(fallback.index(">TNX1</td>"), fallback.index(">TNX2</td>"))
    self.assertLess(fallback.index(">TNX2</td>"), fallback.index(">TNX10</td>"))
```

- [ ] **Step 2: Run focused tests and verify they fail because the status column is fixed and sort controls are absent**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_product_catalog_search tests.test_drawing_search_sorting`

Expected: failures for missing `筛选参数`, missing parameter values, and missing descending order.

- [ ] **Step 3: Add dynamic display helpers and route parameters**

```python
DRAWING_PARAMETER_LABELS = {
    "product_thickness": "总成品厚度",
    "plate_thickness": "钢板厚度",
    "outer_diameter": "外径",
    "inner_diameter": "内径",
    "teeth_count": "齿数",
    "module": "模数",
    "pressure_angle": "压力角",
    "common_normal_length": "公法线",
    "pin_diameter": "量棒直径",
    "pin_span": "跨棒距",
}


def drawing_filter_values(drawing: ProductDrawing, filters: dict[str, str]) -> list[str]:
    values = {
        "product_thickness": drawing.product_thickness,
        "plate_thickness": drawing.plate_thickness,
        "outer_diameter": drawing.max_outer_diameter,
        "inner_diameter": drawing.min_inner_diameter,
        "teeth_count": f"{drawing.tooth_type or ''}{drawing.teeth_count_text or drawing.teeth_count or ''}",
        "module": drawing.module_text or drawing.module,
        "pressure_angle": f"{drawing.pressure_angle:g}°" if drawing.pressure_angle is not None else None,
        "common_normal_length": drawing.common_normal_length_text or drawing.common_normal_length,
        "pin_diameter": drawing.pin_diameter,
        "pin_span": drawing.pin_span,
    }
    return [
        f"{DRAWING_PARAMETER_LABELS[name]} {values[name]}"
        for name in DRAWING_PARAMETER_LABELS
        if filters.get(name, "").strip() and values[name] not in (None, "")
    ]
```

Add `sort_by: str = ""` and `sort_dir: str = ""` to `confirmed_drawings_page`, construct `parameter_filters`, and call `sort_records` only when the requested field is allow-listed. Preserve the current natural code/version order when no valid explicit sort is selected.

- [ ] **Step 4: Render the unchanged six-column table and compact sorting controls**

Use the fifth header as `筛选参数` only when `any(parameter_filters.values())`; otherwise use `状态`. Render each value as `<span class="parameter-line">压力角 30°</span>` inside the existing fifth cell. Add `sort_by` and `sort_dir` controls to the existing GET form and include both values in the product-catalog export parameters. In `_product_catalog_rows`, apply the same confirmed-drawing sort key map after filtering so the exported row order matches the page.

- [ ] **Step 5: Run focused tests and the drawing regression suite**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_product_catalog_search tests.test_drawing_search_sorting tests.test_admin_navigation_and_drawing_confirm`

Expected: all tests pass.

- [ ] **Step 6: Commit the drawing behavior**

```bash
git add app/admin_pages.py app/services/excel_export.py tests/test_product_catalog_search.py tests/test_drawing_search_sorting.py
git commit -m '显示图纸搜索参数并支持排序'
```

---

### Task 3: Generic Product Inbound/Outbound Analysis Service

**Files:**
- Modify: `app/services/product_outbound_analysis.py`
- Modify: `app/services/excel_export.py:15-30, 350-390`
- Modify: `tests/test_product_outbound_analysis.py`

**Interfaces:**
- Produces: `normalize_flow_type(value: str | None) -> str`, `analyze_product_flow(db, product_code="", period="recent_365", start_date="", end_date="", customer="", purpose="", flow_type="out") -> dict`, and `product_flow_analysis_export_rows(db, filters) -> tuple[list[str], list[list[object]]]`.
- Preserves: `analyze_product_outbound(db, product_code="", period="recent_365", start_date="", end_date="", customer="", purpose="") -> dict` as an outbound-only wrapper for existing callers and tests.

- [ ] **Step 1: Add failing service tests for inbound isolation, reversed-record exclusion, monthly totals, and inbound export headings**

```python
def test_analyzes_only_non_reversed_product_inbound_records(self):
    with self.Session() as db:
        product = MaterialInventory(material_code="IN-1", inventory_type="product", material="65Mn", thickness=1.2, shape="circle", quantity=10)
        raw = MaterialInventory(material_code="RAW", inventory_type="raw_plate", material="65Mn", thickness=1.2, shape="rectangle", quantity=10)
        db.add_all([product, raw])
        db.flush()
        db.add_all([
            InventoryTransactionRecord(inventory_id=product.id, transaction_type="in", quantity=10, before_quantity=0, after_quantity=10, created_at=datetime(2026, 4, 1, 9, 0)),
            InventoryTransactionRecord(inventory_id=product.id, transaction_type="out", quantity=3, before_quantity=10, after_quantity=7, created_at=datetime(2026, 4, 2, 9, 0)),
            InventoryTransactionRecord(inventory_id=product.id, transaction_type="in", quantity=5, before_quantity=0, after_quantity=5, reversed_transaction_id=99, created_at=datetime(2026, 4, 3, 9, 0)),
            InventoryTransactionRecord(inventory_id=raw.id, transaction_type="in", quantity=7, before_quantity=0, after_quantity=7, created_at=datetime(2026, 4, 4, 9, 0)),
        ])
        db.commit()
        result = analyze_product_flow(db, flow_type="in", start_date="2026-04-01", end_date="2026-04-30")
    self.assertEqual(result["summary"]["total_quantity"], 10)
    self.assertEqual(result["summary"]["transaction_count"], 1)
    self.assertEqual(result["summary"]["product_count"], 1)
    self.assertEqual(result["detail_rows"][0]["product_code"], "IN-1")
```

- [ ] **Step 2: Run the analysis tests and verify `analyze_product_flow` is missing**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_product_outbound_analysis`

Expected: import or attribute failure for `analyze_product_flow`.

- [ ] **Step 3: Refactor the existing analyzer around `flow_type`**

```python
def normalize_flow_type(value: str | None) -> str:
    return "in" if (value or "").strip().lower() == "in" else "out"


def analyze_product_flow(
    db: Session,
    product_code: str = "",
    period: str = "recent_365",
    start_date: str = "",
    end_date: str = "",
    customer: str = "",
    purpose: str = "",
    flow_type: str = "out",
) -> dict:
    normalized_flow = normalize_flow_type(flow_type)
    start, end, range_label = product_outbound_period_range(period, start_date, end_date)
    records = (
        db.query(InventoryTransactionRecord)
        .filter(
            InventoryTransactionRecord.transaction_type == normalized_flow,
            InventoryTransactionRecord.reversed_transaction_id.is_(None),
            InventoryTransactionRecord.created_at >= start,
            InventoryTransactionRecord.created_at < end,
        )
        .order_by(InventoryTransactionRecord.created_at.desc(), InventoryTransactionRecord.id.desc())
        .all()
    )
    inventory_ids = [record.inventory_id for record in records]
    inventory_map = {
        item.id: item
        for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()
    } if inventory_ids else {}
    product_filter = product_code.strip()
    customer_filter = customer.strip() if normalized_flow == "out" else ""
    purpose_filter = purpose.strip() if normalized_flow == "out" else ""
    detail_rows = []
    monthly = {}
    customers = set()
    products = set()
    total_quantity = 0
    sales_quantity = 0
    for record in records:
        item = inventory_map.get(record.inventory_id)
        if not item or item.inventory_type != "product":
            continue
        code = item.material_code or item.source_product_code or "未编号"
        if product_filter and product_filter != code:
            continue
        customer_name = record.customer_name or ""
        if customer_filter and customer_filter not in customer_name:
            continue
        if purpose_filter == "sales" and not _record_is_sales(record):
            continue
        if purpose_filter not in ("", "sales") and record.outbound_purpose != purpose_filter:
            continue
        month = record.created_at.strftime("%Y-%m") if record.created_at else "-"
        group = monthly.setdefault(month, {"month": month, "quantity": 0, "sales_quantity": 0, "transaction_count": 0, "products": set(), "customers": set()})
        group["quantity"] += record.quantity
        group["transaction_count"] += 1
        group["products"].add(code)
        total_quantity += record.quantity
        products.add(code)
        if normalized_flow == "out" and _record_is_sales(record):
            group["sales_quantity"] += record.quantity
            sales_quantity += record.quantity
        if customer_name:
            group["customers"].add(customer_name)
            customers.add(customer_name)
        detail_rows.append({
            "transaction_id": record.id,
            "time": _fmt_time(record.created_at),
            "product_code": code,
            "quantity": record.quantity,
            "customer_name": customer_name or "-",
            "purpose": record.outbound_purpose or ("sales" if normalized_flow == "out" else "in"),
            "purpose_label": outbound_purpose_label(record.outbound_purpose) if normalized_flow == "out" else "入库",
            "location": item.location or "-",
            "material": item.material or "-",
            "thickness": item.thickness,
            "operator_name": record.operator_name or "-",
            "remark": record.remark or "-",
        })
    monthly_rows = [{
        "month": row["month"],
        "quantity": row["quantity"],
        "sales_quantity": row["sales_quantity"],
        "transaction_count": row["transaction_count"],
        "product_count": len(row["products"]),
        "customer_count": len(row["customers"]),
    } for row in sorted(monthly.values(), key=lambda value: value["month"])]
    months = _month_span(start, end)
    base_quantity = sales_quantity if normalized_flow == "out" else total_quantity
    monthly_avg = round(base_quantity / months, 1) if months else 0
    recent_rows = monthly_rows[-3:]
    recent_avg = round(sum((row["sales_quantity"] if normalized_flow == "out" else row["quantity"]) for row in recent_rows) / len(recent_rows), 1) if recent_rows else 0
    base_avg = max(monthly_avg, recent_avg)
    suggested_year_quantity = int(round(base_avg * 12)) if base_avg else 0
    summary = {
        "range_label": range_label,
        "product_code": product_filter or "全部产品",
        "total_quantity": total_quantity,
        "sales_quantity": sales_quantity,
        "transaction_count": len(detail_rows),
        "customer_count": len(customers),
        "product_count": len(products),
        "month_count": months,
        "monthly_avg": monthly_avg,
        "recent_3_month_avg": recent_avg,
        "peak_month_quantity": max(((row["sales_quantity"] if normalized_flow == "out" else row["quantity"]) for row in monthly_rows), default=0),
        "suggested_year_quantity": suggested_year_quantity,
        "safety_stock_10": int(round(suggested_year_quantity * 1.1)) if suggested_year_quantity else 0,
        "safety_stock_20": int(round(suggested_year_quantity * 1.2)) if suggested_year_quantity else 0,
    }
    return {"flow_type": normalized_flow, "summary": summary, "monthly_rows": monthly_rows, "detail_rows": detail_rows, "start": start, "end": end}
```

Implement the body by moving the current outbound loop, not by duplicating it. For inbound, fill shared row fields and use `customer_name="-"`, `purpose_label="入库"`. Add `product_count`, generic `monthly_avg`, and generic monthly `quantity`; retain all existing outbound summary keys.

- [ ] **Step 4: Keep the compatibility wrapper and add flow-aware export**

```python
def analyze_product_outbound(
    db: Session,
    product_code: str = "",
    period: str = "recent_365",
    start_date: str = "",
    end_date: str = "",
    customer: str = "",
    purpose: str = "",
) -> dict:
    return analyze_product_flow(
        db,
        product_code=product_code,
        period=period,
        start_date=start_date,
        end_date=end_date,
        customer=customer,
        purpose=purpose,
        flow_type="out",
    )


def product_flow_analysis_export_rows(db: Session, filters: dict):
    result = analyze_product_flow(
        db,
        product_code=filters.get("product_code") or "",
        period=filters.get("period") or "recent_365",
        start_date=filters.get("start_date") or "",
        end_date=filters.get("end_date") or "",
        customer=filters.get("customer") or "",
        purpose=filters.get("purpose") or "",
        flow_type=filters.get("flow_type") or "out",
    )
    rows = [[
        row["transaction_id"], row["time"], row["product_code"], row["quantity"],
        row["location"], row["material"], row["thickness"], row["operator_name"],
        row["remark"], result["summary"]["range_label"],
    ] for row in result["detail_rows"]]
    if result["flow_type"] == "in":
        return ["流水号", "入库时间", "产品型号", "入库数量", "库位", "材质", "厚度", "操作人", "备注", "时间范围"], rows
    outbound_rows = [[
        row["transaction_id"], row["time"], row["product_code"], row["quantity"],
        row["customer_name"], row["purpose_label"], row["location"], row["material"],
        row["thickness"], row["operator_name"], row["remark"], result["summary"]["range_label"],
    ] for row in result["detail_rows"]]
    return ["流水号", "出库时间", "产品型号", "出库数量", "客户/去向", "用途", "库位", "材质", "厚度", "操作人", "备注", "时间范围"], outbound_rows
```

In `build_export_rows`, return the title `产品入库分析` for `flow_type=in` and `产品出库分析` otherwise.

- [ ] **Step 5: Run service and export tests**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_product_outbound_analysis tests.test_product_catalog_search`

Expected: all tests pass, including existing outbound results.

- [ ] **Step 6: Commit the flow analysis service**

```bash
git add app/services/product_outbound_analysis.py app/services/excel_export.py tests/test_product_outbound_analysis.py
git commit -m '扩展产品出入库分析服务'
```

---

### Task 4: Product Flow Analysis Page and Detail Placement

**Files:**
- Modify: `app/admin_pages.py:1040-1080, 1410-1430, 1820-1840, 3048-3165`
- Modify: `tests/test_product_outbound_analysis.py`
- Modify: `tests/test_admin_navigation_and_drawing_confirm.py`

**Interfaces:**
- Consumes: `analyze_product_flow` and `normalize_flow_type` from Task 3.
- Preserves: `/admin/reports/product-outbound` URL.

- [ ] **Step 1: Add failing HTML tests for mode controls, mode-specific filters, detail order, labels, and export parameters**

```python
def test_product_analysis_switches_between_inbound_and_outbound_layouts(self):
    with self.Session() as db:
        inbound_html = product_outbound_analysis_page(flow_type="in", db=db).body.decode("utf-8")
        outbound_html = product_outbound_analysis_page(flow_type="out", db=db).body.decode("utf-8")
    self.assertIn('name="flow_type"', inbound_html)
    self.assertIn("产品出入库分析", inbound_html)
    self.assertNotIn('name="customer"', inbound_html)
    self.assertNotIn('name="purpose"', inbound_html)
    self.assertNotIn("备货建议", inbound_html)
    self.assertIn('name="customer"', outbound_html)
    self.assertIn('name="purpose"', outbound_html)
    self.assertIn("备货建议", outbound_html)

def test_detail_table_is_immediately_after_query_before_stats(self):
    with self.Session() as db:
        html = product_outbound_analysis_page(flow_type="out", db=db).body.decode("utf-8")
    self.assertLess(html.index("逐单明细"), html.index("销售出库量"))
    self.assertLess(html.index("逐单明细"), html.index("备货建议"))
```

Use a context-managed session in the actual second test.

- [ ] **Step 2: Run focused tests and verify the page is outbound-only**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_product_outbound_analysis tests.test_admin_navigation_and_drawing_confirm`

Expected: failures for missing flow switch, inbound layout, renamed navigation, and detail ordering.

- [ ] **Step 3: Add the switch and conditional form fields**

Add `flow_type: str = "out"` to the route. Render a compact segmented control with two submit buttons carrying `name="flow_type"` and values `in`/`out`, plus a hidden `flow_type` field in the detailed query form. For inbound mode, omit customer and purpose HTML entirely. Include `flow_type` in `export_params`.

- [ ] **Step 4: Reorder sections and render mode-specific summaries**

Build the page in this order: title/actions, mode/query card, detail table, stats, outbound-only recommendation, monthly summary. Use inbound headings `入库时间` and `入库数量`; use outbound headings unchanged. Update visible navigation text from `产品出库分析` to `产品出入库分析` while preserving hrefs.

- [ ] **Step 5: Run page and navigation tests**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_product_outbound_analysis tests.test_admin_navigation_and_drawing_confirm tests.test_outbound_customer_report`

Expected: all tests pass.

- [ ] **Step 6: Commit the analysis page**

```bash
git add app/admin_pages.py tests/test_product_outbound_analysis.py tests/test_admin_navigation_and_drawing_confirm.py
git commit -m '增加产品出入库分析切换'
```

---

### Task 5: Raw-Plate Specification Sorting

**Files:**
- Modify: `app/admin_pages.py:2105-2135`
- Modify: `tests/test_inventory_grouping_pages.py`

**Interfaces:**
- Consumes: `sort_records` and `sort_select_options` from Task 1.

- [ ] **Step 1: Add failing tests for material and thickness ordering in both directions and default fallback**

```python
def test_raw_plate_specifications_support_selected_sorting(self):
    with self.Session() as db:
        db.add_all([
            RawPlateSpecification(spec_name="S10", material="Q235", length=1000, width=500, thickness=10),
            RawPlateSpecification(spec_name="S2", material="65Mn", length=1000, width=500, thickness=2),
        ])
        db.commit()
        material_html = raw_plate_specifications_page(sort_by="material", sort_dir="asc", db=db).body.decode("utf-8")
        thickness_html = raw_plate_specifications_page(sort_by="thickness", sort_dir="desc", db=db).body.decode("utf-8")
    self.assertLess(material_html.index(">65Mn</td>"), material_html.index(">Q235</td>"))
    self.assertLess(thickness_html.index(">10</td>"), thickness_html.index(">2</td>"))
```

- [ ] **Step 2: Run the focused test and verify route arguments are missing**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_inventory_grouping_pages.InventoryGroupingPagesTest.test_raw_plate_specifications_support_selected_sorting`

Expected: failure because `sort_by` and `sort_dir` are not accepted.

- [ ] **Step 3: Add route parameters, allow-listed keys, and compact controls**

Query specs with the current default ordering, then call `sort_records` with keys for `spec_name`, `material`, `length`, `width`, `thickness`, `density`, `status`, and `created_at`. Add a GET sorting form above the existing POST creation form; do not nest forms. Invalid sort inputs leave the existing active/created ordering untouched.

- [ ] **Step 4: Run focused and inventory page tests**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_inventory_grouping_pages`

Expected: all tests pass.

- [ ] **Step 5: Commit raw-plate specification sorting**

```bash
git add app/admin_pages.py tests/test_inventory_grouping_pages.py
git commit -m '支持板料规格自选排序'
```

---

### Task 6: Shared Inventory Summaries and Sorting for Three Inventory Pages

**Files:**
- Create: `app/services/inventory_summaries.py`
- Modify: `app/admin_pages.py:1740-1945, 4188-4300`
- Modify: `app/services/excel_export.py:350-380`
- Modify: `tests/test_inventory_grouping_pages.py`
- Modify: `tests/test_product_catalog_search.py`

**Interfaces:**
- Produces: `product_summary_rows(items)`, `raw_plate_summary_rows(items, spec_names)`, and `scrap_summary_rows(records, scrap_map)` returning dictionaries with stable sort fields.
- Consumes: `sort_records` from Task 1.

- [ ] **Step 1: Add failing aggregation tests that prove totals remain unchanged while ordering changes**

```python
def test_product_raw_and_scrap_summaries_sort_without_changing_totals(self):
    with self.Session() as db:
        db.add_all([
            MaterialInventory(material_code="TNX2", inventory_type="product", material="65Mn", thickness=1.2, shape="circle", quantity=2, status="available"),
            MaterialInventory(material_code="TNX10", inventory_type="product", material="65Mn", thickness=1.2, shape="circle", quantity=10, status="available"),
        ])
        db.commit()
        quantity_html = inventory_page(sort_by="quantity", sort_dir="desc", db=db).body.decode("utf-8")
        code_html = inventory_page(sort_by="code", sort_dir="asc", db=db).body.decode("utf-8")
    self.assertLess(quantity_html.index(">TNX10</td>"), quantity_html.index(">TNX2</td>"))
    self.assertLess(code_html.index(">TNX2</td>"), code_html.index(">TNX10</td>"))

def test_raw_plate_summary_sorts_by_total_quantity_descending(self):
    with self.Session() as db:
        db.add_all([
            MaterialInventory(material_code="R2", inventory_type="raw_plate", material="Q235", thickness=2, length=1000, width=500, shape="rectangle", quantity=2, status="available"),
            MaterialInventory(material_code="R10", inventory_type="raw_plate", material="65Mn", thickness=3, length=2000, width=1000, shape="rectangle", quantity=10, status="available"),
        ])
        db.commit()
        html = raw_plates_page(sort_by="quantity", sort_dir="desc", db=db).body.decode("utf-8")
    self.assertLess(html.index(">65Mn</td>"), html.index(">Q235</td>"))
    self.assertIn("<strong>10</strong>", html)
    self.assertIn("<strong>2</strong>", html)

def test_scrap_summary_sorts_by_total_quantity_descending(self):
    with self.Session() as db:
        small = MaterialInventory(inventory_type="scrap", material="Q235", thickness=2, diameter=50, usable_size="φ50", shape="round", quantity=2, status="available")
        large = MaterialInventory(inventory_type="scrap", material="65Mn", thickness=3, diameter=80, usable_size="φ80", shape="round", quantity=10, status="available")
        db.add_all([small, large])
        db.flush()
        db.add_all([
            ScrapGenerationRecord(source_product_code="P2", scrap_inventory_id=small.id),
            ScrapGenerationRecord(source_product_code="P10", scrap_inventory_id=large.id),
        ])
        db.commit()
        html = scraps_page(sort_by="quantity", sort_dir="desc", db=db).body.decode("utf-8")
    self.assertLess(html.index(">65Mn</td>"), html.index(">Q235</td>"))
    self.assertIn("<strong>10</strong>", html)
    self.assertIn("<strong>2</strong>", html)
```

- [ ] **Step 2: Run inventory page tests and verify selected sorting has no effect**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_inventory_grouping_pages`

Expected: failures for unsupported route parameters or unchanged ordering.

- [ ] **Step 3: Extract existing grouping loops without changing their output values**

```python
def product_summary_rows(items: list[MaterialInventory]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for item in items:
        code = item.material_code or item.source_product_code or "未编号"
        group = grouped.setdefault(code, {
            "code": code,
            "material": item.material,
            "product_thicknesses": set(),
            "plate_thicknesses": set(),
            "quantity": 0,
            "batch_count": 0,
            "locations": set(),
            "paper_materials": set(),
            "latest": item.updated_at or item.created_at,
        })
        group["quantity"] += item.quantity
        group["batch_count"] += 1
        group["product_thicknesses"].add(item.product_thickness or item.thickness)
        group["plate_thicknesses"].add(item.plate_thickness or item.thickness)
        if item.location:
            group["locations"].add(item.location)
        if item.paper_material:
            group["paper_materials"].add(item.paper_material)
        item_time = item.updated_at or item.created_at
        if item_time and (not group["latest"] or item_time > group["latest"]):
            group["latest"] = item_time
    return list(grouped.values())


def raw_plate_summary_rows(items: list[MaterialInventory], spec_names: dict[tuple, str]) -> list[dict]:
    grouped: dict[tuple, dict] = {}
    for item in items:
        key = (item.material, item.length, item.width, item.thickness)
        group = grouped.setdefault(key, {
            "spec_name": spec_names.get(key) or "临时规格",
            "material": item.material,
            "length": item.length,
            "width": item.width,
            "thickness": item.thickness,
            "quantity": 0,
            "batch_count": 0,
            "locations": set(),
            "latest": item.updated_at or item.created_at,
        })
        group["quantity"] += item.quantity
        group["batch_count"] += 1
        if item.location:
            group["locations"].add(item.location)
        item_time = item.updated_at or item.created_at
        if item_time and (not group["latest"] or item_time > group["latest"]):
            group["latest"] = item_time
    return list(grouped.values())


def scrap_summary_rows(records: list[ScrapGenerationRecord], scrap_map: dict[int, MaterialInventory]) -> list[dict]:
    grouped: dict[tuple, dict] = {}
    for record in records:
        item = scrap_map.get(record.scrap_inventory_id)
        if not item or item.status != "available":
            continue
        size_label = item.usable_size or (f"φ{item.diameter:g}" if item.diameter is not None else "-")
        key = (item.material, item.thickness, size_label)
        group = grouped.setdefault(key, {
            "material": item.material,
            "thickness": item.thickness,
            "diameter": item.diameter,
            "usable_size": size_label,
            "locations": set(),
            "quantity": 0,
            "batch_count": 0,
            "latest": item.updated_at or item.created_at,
        })
        group["quantity"] += item.quantity
        group["batch_count"] += 1
        if item.location:
            group["locations"].add(item.location)
        item_time = item.updated_at or item.created_at
        if item_time and (not group["latest"] or item_time > group["latest"]):
            group["latest"] = item_time
    return list(grouped.values())
```

- [ ] **Step 4: Add page sorting controls and preserve filters in GET submissions**

Add `sort_by` and `sort_dir` to `inventory_page`, `raw_plates_page`, and `scraps_page`. Append controls to each existing filter form so all filters submit together. Use allow-listed keys from the approved spec. Pass sorting parameters to each export link.

- [ ] **Step 5: Make the three inventory exports consume the same grouped rows and order**

Use the new summary functions in `build_export_rows` for `product_inventory`, `raw_plate_inventory`, and `scrap_inventory`. Apply the same sort key map and return one Excel row per displayed summary group, with headings matching the visible summary fields. This changes only export grouping/order, not stored inventory.

- [ ] **Step 6: Run inventory and export tests**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_inventory_grouping_pages tests.test_product_catalog_search`

Expected: all tests pass; page totals and exported totals match.

- [ ] **Step 7: Commit inventory summary sorting**

```bash
git add app/services/inventory_summaries.py app/admin_pages.py app/services/excel_export.py tests/test_inventory_grouping_pages.py tests/test_product_catalog_search.py
git commit -m '支持库存汇总自选排序'
```

---

### Task 7: Responsive Layout, Full Regression, and Browser Verification

**Files:**
- Modify: `app/admin_pages.py` shared CSS and only the affected HTML classes
- Modify: tests touched by Tasks 2-6 if browser findings expose a missed layout contract

**Interfaces:**
- Verifies all prior tasks; introduces no new business interface.

- [ ] **Step 1: Add/adjust CSS for compact parameter lines, sorting controls, and flow switch**

```css
.parameter-lines { display:grid; gap:3px; min-width:0; }
.parameter-line { display:block; overflow-wrap:anywhere; line-height:1.35; }
.sort-controls { display:flex; flex:0 1 auto; gap:8px; align-items:center; flex-wrap:wrap; }
.sort-controls select { width:auto; min-width:112px; max-width:180px; }
.flow-switch { display:inline-flex; gap:4px; flex-wrap:wrap; }

@media (max-width:700px) {
  .sort-controls { width:100%; }
  .sort-controls select, .sort-controls .btn { flex:1 1 120px; min-width:0; }
  .parameter-lines { text-align:left; }
}
```

Reuse existing button and segmented-state classes where they already satisfy the layout; do not add decorative containers.

- [ ] **Step 2: Run the complete automated suite**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover tests`

Expected: all tests pass with zero failures. The known Python 3.12 SQLite datetime adapter deprecation warning may remain.

- [ ] **Step 3: Run compilation and diff checks**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q app && git diff --check`

Expected: exit code 0 and no output from `git diff --check`.

- [ ] **Step 4: Start a temporary server on an unused port and verify affected pages with Playwright**

Verify these URLs at 1366x768 and 390x844:

```text
/admin/drawings/confirmed?pressure_angle=30&module=2
/admin/reports/product-outbound?flow_type=in
/admin/reports/product-outbound?flow_type=out
/admin/raw-plate-specifications?sort_by=thickness&sort_dir=desc
/admin/inventory?sort_by=quantity&sort_dir=desc
/admin/raw-plates?sort_by=thickness&sort_dir=asc
/admin/scraps?sort_by=quantity&sort_dir=desc
```

For each page, assert:

```javascript
document.documentElement.scrollWidth === document.documentElement.clientWidth
```

Also assert the active flow/sort values are visible, action buttons are present, and no browser console errors occur.

- [ ] **Step 5: Independently review the final diff and address findings**

Request a reviewer to compare the implementation against `docs/superpowers/specs/2026-07-18-drawing-flow-analysis-sorting-design.md`. Fix every critical or important finding, rerun the focused test for each fix, then rerun the complete suite.

- [ ] **Step 6: Commit responsive and verification fixes**

```bash
git add app/admin_pages.py tests/test_product_catalog_search.py tests/test_drawing_search_sorting.py tests/test_product_outbound_analysis.py tests/test_admin_navigation_and_drawing_confirm.py tests/test_inventory_grouping_pages.py tests/test_list_sorting.py
git commit -m '完善分析与排序页面响应式布局'
```

- [ ] **Step 7: Push when GitHub credentials permit**

Run: `git push origin main`

Expected: remote `main` advances to the final local commit. If GitHub still returns 403, preserve all local commits and report the authentication blocker without rewriting history.
