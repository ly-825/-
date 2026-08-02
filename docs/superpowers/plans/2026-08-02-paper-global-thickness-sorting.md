# Paper Global Thickness Sorting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sort mixed paper rolls and paper sheets globally by numeric thickness across every specification and inventory surface.

**Architecture:** Reorder the existing shared paper specification and inventory-group sort keys so thickness precedes paper type. All affected pages and the inventory export already consume those shared functions, so no page-specific sorting is added.

**Tech Stack:** Python 3.12, SQLAlchemy, FastAPI server-rendered HTML, unittest

## Global Constraints

- Sort by thickness, paper type, corresponding dimensions, model, then material name.
- Keep enabled specifications before disabled specifications on the maintenance page.
- Keep paper transactions ordered newest first and detail batches in FIFO order.
- Do not change the database schema or inventory data.

---

### Task 1: Put thickness first in shared paper sort keys

**Files:**
- Modify: `app/services/paper_inventory.py:72-151`
- Test: `tests/test_paper_material_management.py`
- Test: `tests/test_paper_exports.py`

**Interfaces:**
- Consumes: `natural_sort_key(value: str) -> list[object]`
- Produces: `paper_specification_sort_key(spec: PaperSpecification) -> tuple`
- Produces: `paper_inventory_groups(batches: list[PaperInventoryBatch]) -> list[dict[str, object]]`

- [ ] **Step 1: Write failing mixed-type ordering tests**

Add a service test that puts a thicker roll before a thinner sheet in input order and asserts the thinner sheet sorts first:

```python
def test_paper_sorting_is_globally_thickness_first(self) -> None:
    specs = [
        PaperSpecification(
            paper_type="roll",
            model="ROLL-2.0",
            material_name="纸圈",
            thickness=2.0,
            inner_diameter=50,
            outer_diameter=80,
        ),
        PaperSpecification(
            paper_type="sheet",
            model="0.5×400×400",
            material_name="纸张",
            thickness=0.5,
            length=400,
            width=400,
        ),
    ]

    ordered = sorted(specs, key=paper_specification_sort_key)

    self.assertEqual([spec.model for spec in ordered], ["0.5×400×400", "ROLL-2.0"])
```

Extend the test with equal-thickness rolls and sheets to assert paper type and dimension tie-breakers. Add positive-stock batches for the same specifications and assert `paper_inventory_groups` returns the identical model order.

In `PaperExportTest._seed`, set the sheet thickness/model to `0.4`/`0.4×500×300`; update `test_paper_inventory_export_has_live_price_range_and_sorted_rows` to expect the paper sheet row first and the `0.5` roll row second.

- [ ] **Step 2: Run focused tests and confirm they fail for type-first ordering**

Run:

```bash
.venv/bin/python -m unittest \
  tests.test_paper_material_management.PaperInventoryServiceTest.test_paper_sorting_is_globally_thickness_first \
  tests.test_paper_exports.PaperExportTest.test_paper_inventory_export_has_live_price_range_and_sorted_rows -v
```

Expected: FAIL because the current key places every roll before every sheet.

- [ ] **Step 3: Reorder both shared sort keys**

Change the specification key to:

```python
return (
    spec.thickness,
    0 if spec.paper_type == "roll" else 1,
    *dimensions,
    natural_sort_key(spec.model),
    natural_sort_key(spec.material_name),
)
```

Change the inventory-group key to:

```python
key=lambda group: (
    group["thickness"],
    0 if group["paper_type"] == "roll" else 1,
    group["inner_diameter"] or group["length"] or 0,
    group["outer_diameter"] or group["width"] or 0,
    natural_sort_key(group["model"]),
    natural_sort_key(group["material_name"]),
),
```

- [ ] **Step 4: Run focused and paper regression tests**

Run:

```bash
.venv/bin/python -m unittest \
  tests.test_paper_material_management.PaperInventoryServiceTest.test_paper_sorting_is_globally_thickness_first \
  tests.test_paper_exports.PaperExportTest.test_paper_inventory_export_has_live_price_range_and_sorted_rows -v
.venv/bin/python -m unittest tests.test_paper_material_management tests.test_paper_exports -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit the paper sorting change**

```bash
git add app/services/paper_inventory.py tests/test_paper_material_management.py tests/test_paper_exports.py
git commit -m "fix: sort paper inventory by thickness first"
```
