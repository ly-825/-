# Paper Model Sorting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every paper specification and live-inventory view group paper rolls before paper sheets and sort each type by model using natural numeric order instead of thickness or dimensions.

**Architecture:** Keep sorting centralized in `app/services/paper_inventory.py`. `paper_specification_sort_key()` will serve specification lists and inbound options, while `paper_inventory_groups()` will serve inventory, outbound, and inventory export; their keys will share the same type/model-first prefix and retain material/thickness/dimensions only as stable tie-breakers.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, `unittest`/pytest.

## Global Constraints

- Paper rolls sort before paper sheets.
- Within one paper type, model uses natural ascending order.
- Thickness and dimensions must not outrank model.
- Paper transactions remain newest-first and are outside this change.
- Inventory quantities, prices, FIFO behavior, and grouping identity must not change.
- No database migration or customer-data rewrite is required.

---

### Task 1: Unify paper model sorting across specifications and inventory

**Files:**
- Modify: `tests/test_paper_material_management.py:267-355`
- Modify: `tests/test_paper_exports.py:119-137`
- Modify: `app/services/paper_inventory.py:74-146`

**Interfaces:**
- Consumes: `natural_sort_key(value: object) -> tuple` from `app.services.drawing_search`.
- Produces: `paper_specification_sort_key(spec: PaperSpecification) -> tuple` with type/model-first ordering.
- Produces: `paper_inventory_groups(batches: list[PaperInventoryBatch]) -> list[dict[str, Any]]` whose returned groups use the same type/model-first ordering.
- Preserves: `paper_inventory_group_key(batch: PaperInventoryBatch) -> tuple` exactly; grouping identity is not a sorting concern.

- [ ] **Step 1: Replace the thickness-first service test with a failing type/model-first test**

Replace `test_paper_sorting_is_globally_thickness_first` with a test that deliberately gives earlier models larger thicknesses and dimensions:

```python
def test_paper_sorting_groups_types_and_uses_natural_model_order(self) -> None:
    specs = [
        PaperSpecification(
            id=1,
            paper_type="roll",
            model="TNX20.2A",
            material_name="纸圈20",
            thickness=0.2,
            inner_diameter=20,
            outer_diameter=40,
        ),
        PaperSpecification(
            id=2,
            paper_type="sheet",
            model="0.4×500×300",
            material_name="纸张04",
            thickness=0.4,
            length=500,
            width=300,
        ),
        PaperSpecification(
            id=3,
            paper_type="roll",
            model="TNX3.2A",
            material_name="纸圈3",
            thickness=3.0,
            inner_diameter=80,
            outer_diameter=120,
        ),
        PaperSpecification(
            id=4,
            paper_type="sheet",
            model="0.5×400×400",
            material_name="纸张05",
            thickness=0.5,
            length=400,
            width=400,
        ),
        PaperSpecification(
            id=5,
            paper_type="roll",
            model="3969.01",
            material_name="数字纸圈",
            thickness=9.0,
            inner_diameter=500,
            outer_diameter=600,
        ),
    ]
    expected_models = [
        "3969.01",
        "TNX3.2A",
        "TNX20.2A",
        "0.4×500×300",
        "0.5×400×400",
    ]

    ordered_specs = sorted(specs, key=paper_specification_sort_key)
    batches = [
        PaperInventoryBatch(
            specification_id=spec.id,
            batch_code=f"P-{spec.id}",
            paper_type=spec.paper_type,
            model=spec.model,
            material_name=spec.material_name,
            thickness=spec.thickness,
            inner_diameter=spec.inner_diameter,
            outer_diameter=spec.outer_diameter,
            length=spec.length,
            width=spec.width,
            quantity=1,
            unit_price=Decimal("1.00"),
            status="available",
        )
        for spec in specs
    ]

    self.assertEqual([spec.model for spec in ordered_specs], expected_models)
    self.assertEqual(
        [group["model"] for group in paper_inventory_groups(batches)],
        expected_models,
    )
```

- [ ] **Step 2: Update the inventory-export expectation to require paper-roll-first ordering**

In `test_paper_inventory_export_has_live_price_range_and_sorted_rows`, change the row assertions to:

```python
self.assertEqual(rows[0][0:4], ["纸圈", "Tnx236.2A", "蓝纸", "0.5×80×120"])
self.assertEqual(rows[0][6:8], ["10.00", "12.50"])
self.assertEqual(rows[1][0:4], ["纸张", "0.4×500×300", "白纸", "0.4×500×300"])
self.assertNotIn("99.00", str(rows))
```

- [ ] **Step 3: Add a failing page-level consistency test**

Add this test to `PaperInventoryWorkflowPagesTest` before changing production code:

```python
def test_paper_pages_share_type_and_model_sort_order(self) -> None:
    specs = [
        PaperSpecification(
            paper_type="roll",
            model="TNX20.2A",
            material_name="纸圈20",
            thickness=0.2,
            inner_diameter=20,
            outer_diameter=40,
            is_active=1,
        ),
        PaperSpecification(
            paper_type="sheet",
            model="0.1×100×100",
            material_name="纸张01",
            thickness=0.1,
            length=100,
            width=100,
            is_active=1,
        ),
        PaperSpecification(
            paper_type="roll",
            model="TNX3.2A",
            material_name="纸圈3",
            thickness=3.0,
            inner_diameter=80,
            outer_diameter=120,
            is_active=1,
        ),
    ]
    expected_models = ["TNX3.2A", "TNX20.2A", "0.1×100×100"]
    with self.Session() as db:
        db.add_all(specs)
        db.flush()
        db.add_all(
            [
                PaperInventoryBatch(
                    specification_id=spec.id,
                    batch_code=f"P-{spec.id}",
                    paper_type=spec.paper_type,
                    model=spec.model,
                    material_name=spec.material_name,
                    thickness=spec.thickness,
                    inner_diameter=spec.inner_diameter,
                    outer_diameter=spec.outer_diameter,
                    length=spec.length,
                    width=spec.width,
                    quantity=1,
                    unit_price=Decimal("1.00"),
                    status="available",
                )
                for spec in specs
            ]
        )
        db.commit()

        for render in (
            paper_specifications_page,
            paper_inbound_page,
            paper_inventory_page,
            paper_outbound_page,
        ):
            html = render(db=db).body.decode("utf-8")
            positions = [html.index(model) for model in expected_models]
            self.assertEqual(positions, sorted(positions))
```

- [ ] **Step 4: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_paper_material_management.py::PaperInventoryServiceTest::test_paper_sorting_groups_types_and_uses_natural_model_order \
  tests/test_paper_material_management.py::PaperInventoryWorkflowPagesTest::test_paper_pages_share_type_and_model_sort_order \
  tests/test_paper_exports.py::PaperExportTest::test_paper_inventory_export_has_live_price_range_and_sorted_rows
```

Expected: all three tests fail because current keys put thickness before type/model, producing sheet-first or thickness-first rows.

- [ ] **Step 5: Change both public sorting keys to type/model-first**

In `app/services/paper_inventory.py`, use this key for specifications:

```python
def paper_specification_sort_key(spec: PaperSpecification) -> tuple:
    if spec.paper_type == "roll":
        dimensions = (spec.inner_diameter or 0, spec.outer_diameter or 0)
    else:
        dimensions = (spec.length or 0, spec.width or 0)
    return (
        0 if spec.paper_type == "roll" else 1,
        natural_sort_key(spec.model),
        natural_sort_key(spec.material_name),
        spec.thickness,
        *dimensions,
    )
```

Change only the final `sorted()` key in `paper_inventory_groups()` to:

```python
key=lambda group: (
    0 if group["paper_type"] == "roll" else 1,
    natural_sort_key(group["model"]),
    natural_sort_key(group["material_name"]),
    group["thickness"],
    group["inner_diameter"] or group["length"] or 0,
    group["outer_diameter"] or group["width"] or 0,
),
```

Do not alter `paper_inventory_group_key()`.

- [ ] **Step 6: Run the focused tests and verify GREEN**

Run the same focused pytest command from Step 4.

Expected: `3 passed`.

- [ ] **Step 7: Run the complete paper test suite**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_paper_material_management.py tests/test_paper_exports.py
```

Expected: all tests pass; deprecation warnings from existing `ezdxf`/SQLite dependencies are allowed, but there must be zero failures and zero errors.

- [ ] **Step 8: Run the complete repository test suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass with zero failures and zero errors.

- [ ] **Step 9: Commit the implementation**

```bash
git add app/services/paper_inventory.py tests/test_paper_material_management.py tests/test_paper_exports.py
git commit -m "fix: sort paper materials by type and model"
```

- [ ] **Step 10: Push the commits and verify the remote branch**

```bash
git push origin main
git rev-parse HEAD
git ls-remote origin refs/heads/main
```

Expected: the local `HEAD` SHA and remote `refs/heads/main` SHA are identical.
