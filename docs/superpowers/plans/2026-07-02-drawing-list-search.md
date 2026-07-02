# Drawing List and Mixed Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify drawing upload and confirmation lists, add natural model ordering, and make letter-number gear searches reliable across page and assistant queries.

**Architecture:** Keep the existing FastAPI server-rendered page structure. Add small reusable natural-sort and tooth-query helpers, then use them in existing drawing, inventory, plan, and assistant paths. Preserve full drawing data in the detail page while reducing list columns.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, SQLite-compatible queries, server-rendered HTML/CSS, unittest.

## Global Constraints

- One DXF upload control accepts 1 to 50 files.
- Drawing list pages show only product code, name, category/material, version, status, and action.
- Full drawing parameters remain editable and visible on the drawing detail page.
- Model ordering is case-insensitive natural ordering, for example `A2`, `A10`, `B1`.
- `IT`, `IL`, `IR`, `OT`, `OL`, and `OR` prefixes combine with tooth-count text during search.
- Existing inventory and transaction data must not be changed.

---

### Task 1: Natural sorting and mixed tooth search helpers

**Files:**
- Modify: `app/admin_pages.py`
- Modify: `app/assistant/tools/drawing.py`
- Modify: `app/assistant/tools/plan.py`
- Test: `tests/test_drawing_parameter_text_and_product_paper.py`

**Interfaces:**
- Produces: `natural_sort_key(value: object) -> tuple`
- Produces: `split_tooth_search(value: str) -> tuple[str | None, str]`
- Produces: `tooth_search_filter(value: str)` SQLAlchemy condition
- Consumes: `ProductDrawing.tooth_type`, `ProductDrawing.teeth_count`, and `ProductDrawing.teeth_count_text`

- [ ] **Step 1: Write failing mixed-search and natural-sort tests**

Create drawings containing `OT` + `48(52)`, `IT` + `41`, and product codes `A10`, `A2`, `B1`. Assert `confirmed_drawings_page(teeth_count="OT48")` only shows the OT drawing and renders codes in `A2`, `A10`, `B1` order. Add direct assistant-filter coverage for the same combined value.

- [ ] **Step 2: Run tests and verify the expected failures**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_drawing_parameter_text_and_product_paper
```

Expected: FAIL because `OT48` is compared independently against `OT` and `48(52)`, and current confirmed drawings are ordered by update time.

- [ ] **Step 3: Implement shared helpers and apply them to page queries**

Use a digit-splitting natural key:

```python
def natural_sort_key(value: object) -> tuple:
    return tuple(int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", str(value or "")))
```

Parse supported tooth prefixes and build a conjunction when both prefix and count are present. Use this condition in `apply_drawing_filters` for the dedicated tooth field and in the general keyword clause so `OT48` matches concatenated logical values.

- [ ] **Step 4: Apply equivalent matching to assistant drawing and plan tools**

In `app/assistant/tools/drawing.py` and `app/assistant/tools/plan.py`, parse tooth prefixes before numeric extraction. Require both the prefix and remaining count when both are supplied; retain existing behavior for prefix-only and count-only searches.

- [ ] **Step 5: Run focused tests**

Run the Task 1 command again. Expected: PASS.

### Task 2: Merge drawing upload controls and remove list content from upload page

**Files:**
- Modify: `app/admin_pages.py`
- Test: `tests/test_admin_navigation_and_drawing_confirm.py`

**Interfaces:**
- Consumes: POST `/admin/drawings/upload-batch` with `files: list[UploadFile]`
- Produces: one `<input type="file" name="files" multiple>` on GET `/admin/drawings`

- [ ] **Step 1: Write a failing upload-page structure test**

Assert the page contains exactly one file input named `files`, posts to `/admin/drawings/upload-batch`, accepts multiple DXF files, and does not contain `图纸记录`, the drawing search form, or `批量生成高清预览`.

- [ ] **Step 2: Run the test and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_admin_navigation_and_drawing_confirm
```

Expected: FAIL because the current page has separate single and batch upload sections plus search and records.

- [ ] **Step 3: Replace the two upload sections with one uploader**

Render one drag-and-drop file input with `multiple`, show the selected file count and names, validate `.dxf` extensions in browser JavaScript, and submit all selected files to the existing batch route. Keep the pending and confirmed navigation links.

- [ ] **Step 4: Run the focused test**

Run the Task 2 command again. Expected: PASS.

### Task 3: Compact drawing lists and constrain wide tables

**Files:**
- Modify: `app/admin_pages.py`
- Test: `tests/test_admin_navigation_and_drawing_confirm.py`

**Interfaces:**
- Produces: `drawing_rows(drawings: list[ProductDrawing]) -> str` with six cells per row
- Consumes: existing drawing detail route for complete parameter access

- [ ] **Step 1: Write failing compact-list tests**

Assert pending and confirmed page headings are exactly `产品编号`, `产品名称`, `分类/材质`, `版本`, `状态`, and `操作`. Assert detailed parameter headings are absent from those list tables but present on the drawing detail form.

- [ ] **Step 2: Run the test and verify failure**

Run the Task 2 test command. Expected: FAIL because both lists currently contain ten columns.

- [ ] **Step 3: Implement compact rows and no-overflow table styles**

Combine category and material into one cell, keep the action button in `.action-col`, add a `compact-list` class with fixed table layout, and let search actions wrap. Add `min-width:0` to the main content grid child and make buttons/cells wrap without causing page-level horizontal overflow.

- [ ] **Step 4: Mark wide operational tables for contained scrolling only where all columns are required**

Keep detailed transaction fields intact. Wrap every operational table with eight or more headings in `.table-scroll`, including plan matches, product/raw-plate/scrap summaries, batch details, transaction lists, outbound reports, and pending scraps. The container may scroll internally, but the overall page and sidebar must not gain horizontal overflow. Retain sticky action columns on tables with row actions.

- [ ] **Step 5: Run focused tests**

Run the Task 2 test command again. Expected: PASS.

### Task 4: Natural ordering for drawing choices and product inventory

**Files:**
- Modify: `app/admin_pages.py`
- Test: `tests/test_inventory_grouping_pages.py`
- Test: `tests/test_admin_navigation_and_drawing_confirm.py`

**Interfaces:**
- Consumes: `natural_sort_key`
- Produces: naturally ordered drawing options, product inventory rows, and product outbound options/rows

- [ ] **Step 1: Write failing inventory and option-order tests**

Create product codes `TNX10`, `TNX2`, and `TNX1`. Assert inventory summary rows, outbound rows, and confirmed drawing options render in `TNX1`, `TNX2`, `TNX10` order.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_inventory_grouping_pages tests.test_admin_navigation_and_drawing_confirm
```

Expected: FAIL because grouped dictionaries preserve creation order and SQL text ordering places `TNX10` before `TNX2`.

- [ ] **Step 3: Sort grouped values and drawing option sources**

Sort product-code groups with `natural_sort_key(group["code"])`. Sort current drawing records first by natural product code and then by descending version. Apply this ordering before rendering options and rows.

- [ ] **Step 4: Run focused tests**

Run the Task 4 command again. Expected: PASS.

### Task 5: Browser verification, regression suite, and delivery

**Files:**
- Modify only if visual verification reveals a scoped layout defect.

**Interfaces:**
- Consumes: running FastAPI app at `http://127.0.0.1:8000`
- Produces: verified desktop and narrow-width pages with no page-level horizontal overflow

- [ ] **Step 1: Run complete automated verification**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall app
git diff --check
```

Expected: all tests pass, compile exits 0, and diff check prints no errors.

- [ ] **Step 2: Restart the backend and inspect key pages**

Restart uvicorn on port 8000. Check `/admin/drawings`, `/admin/drawings/pending`, `/admin/drawings/confirmed`, `/admin/inventory`, and `/admin/inventory/outbound` at desktop and narrow widths. Confirm `document.documentElement.scrollWidth <= document.documentElement.clientWidth` and action controls are visible.

- [ ] **Step 3: Review the final diff against the design**

Confirm upload-page removal, six-column lists, natural ordering, mixed searches, and unchanged detail parameters. Confirm no unrelated files changed.

- [ ] **Step 4: Commit and push**

```bash
git add app tests docs/superpowers/plans/2026-07-02-drawing-list-search.md
git commit -m "优化图纸列表与混合参数搜索"
git push origin main
```
