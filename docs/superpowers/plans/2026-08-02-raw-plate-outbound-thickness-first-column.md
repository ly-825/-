# Raw Plate Outbound Thickness-First Column Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display thickness as the first column in the raw-plate outbound available-specification table by swapping thickness and material.

**Architecture:** Change only the table header and matching row-cell order in the existing server-rendered outbound page. Search order, result sorting, confirmation form, inventory logic, and FIFO behavior remain unchanged.

**Tech Stack:** Python 3.12, FastAPI server-rendered HTML, unittest

## Global Constraints

- The available table order is thickness, length, width, material, quantity, batch count, location, operation.
- Keep the thickness search input first.
- Keep the current available-specification block above the confirmation form.
- Do not change sorting, database data, inventory deduction, or FIFO logic.

---

### Task 1: Swap raw-plate outbound thickness and material columns

**Files:**
- Modify: `app/admin_pages.py:2650-2680`
- Test: `tests/test_steel_material_management.py:126-142`

**Interfaces:**
- Consumes: `raw_plate_summary_rows(items, spec_names) -> list[dict[str, object]]`
- Produces: unchanged `raw_plate_outbound_page(...) -> HTMLResponse`

- [ ] **Step 1: Write the failing table-order test**

Seed one available raw plate and assert both the header and row start with thickness, length, width, material:

```python
def test_raw_plate_outbound_available_table_puts_thickness_first(self) -> None:
    with self.Session() as db:
        db.add(
            MaterialInventory(
                material_code="RAW-COLUMN-ORDER",
                inventory_type="raw_plate",
                material="65Mn",
                thickness=0.8,
                length=1251,
                width=182,
                shape="rectangle",
                quantity=1990,
                status="available",
            )
        )
        db.commit()
        html = raw_plate_outbound_page(db=db).body.decode("utf-8")

    self.assertIn(
        "<tr><th>厚mm</th><th>长mm</th><th>宽mm</th><th>材质</th>",
        html,
    )
    self.assertIn(
        "<tr><td>0.8</td><td>1251</td><td>182</td><td>65Mn</td>",
        html,
    )
```

- [ ] **Step 2: Run the focused test and confirm the old order fails**

Run:

```bash
.venv/bin/python -m unittest \
  tests.test_steel_material_management.SteelMaterialPagesTest.test_raw_plate_outbound_available_table_puts_thickness_first -v
```

Expected: FAIL because the current table begins with material.

- [ ] **Step 3: Swap the header and row-cell order**

Render each summary row in this order:

```python
f"<tr><td>{format_steel_thickness(group['thickness'])}</td>"
f"<td>{format_number(group['length'])}</td><td>{format_number(group['width'])}</td>"
f"<td>{group['material']}</td>"
```

Change the header prefix to:

```html
<tr><th>厚mm</th><th>长mm</th><th>宽mm</th><th>材质</th>
```

- [ ] **Step 4: Run focused and steel regression tests**

Run:

```bash
.venv/bin/python -m unittest \
  tests.test_steel_material_management.SteelMaterialPagesTest.test_raw_plate_outbound_available_table_puts_thickness_first -v
.venv/bin/python -m unittest tests.test_steel_material_management -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit the table layout change**

```bash
git add app/admin_pages.py tests/test_steel_material_management.py
git commit -m "fix: put raw plate outbound thickness first"
```

