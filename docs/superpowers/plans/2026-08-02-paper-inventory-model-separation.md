# Paper Inventory Model Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure different paper specification models always render as separate inventory groups while multiple batches of the same specification remain summarized together.

**Architecture:** Centralize the paper inventory identity tuple in `paper_inventory_group_key` and make `paper_inventory_groups` use it as its only grouping boundary. Cover both the service result and rendered inventory page so the stock page, outbound page, and Excel export continue to share one grouping implementation.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, unittest/pytest, server-rendered HTML.

## Global Constraints

- Different paper specifications must remain separate even when paper type, material, and thickness match.
- Multiple batches of the same specification and model must remain one summary row.
- Quantity, active batch count, locations, and price range must be calculated per specification/model group.
- Existing thickness-first sorting must remain unchanged.
- Existing batch and transaction data must not be rewritten.

---

### Task 1: Centralize and verify the paper inventory grouping boundary

**Files:**
- Modify: `app/services/paper_inventory.py:92-145`
- Test: `tests/test_paper_material_management.py:132-270`

**Interfaces:**
- Consumes: `PaperInventoryBatch` snapshot fields populated by `create_paper_inbound`.
- Produces: `paper_inventory_group_key(batch: PaperInventoryBatch) -> tuple`, used only by `paper_inventory_groups`.

- [ ] **Step 1: Write the failing grouping-key test**

Add `import app.services.paper_inventory as paper_inventory_service`, then add:

```python
def test_paper_inventory_group_key_separates_models_and_specs(self) -> None:
    self.assertTrue(hasattr(paper_inventory_service, "paper_inventory_group_key"))
    group_key = paper_inventory_service.paper_inventory_group_key
    base = dict(
        batch_code="P-1",
        paper_type="roll",
        material_name="黑色（DC0017）",
        thickness=0.5,
        quantity=1,
        unit_price=Decimal("0.66"),
        status="available",
    )
    first = PaperInventoryBatch(
        specification_id=101,
        model="3969.01",
        inner_diameter=55,
        outer_diameter=115,
        **base,
    )
    second = PaperInventoryBatch(
        specification_id=102,
        model="3960.02",
        inner_diameter=67,
        outer_diameter=127,
        **base,
    )
    same_spec_second_batch = PaperInventoryBatch(
        specification_id=101,
        model="3969.01",
        inner_diameter=55,
        outer_diameter=115,
        **{**base, "batch_code": "P-2"},
    )

    self.assertNotEqual(group_key(first), group_key(second))
    self.assertEqual(group_key(first), group_key(same_spec_second_batch))
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_paper_material_management.py::PaperInventoryServiceTest::test_paper_inventory_group_key_separates_models_and_specs -q
```

Expected: FAIL because `paper_inventory_group_key` does not exist.

- [ ] **Step 3: Add the grouping-key function and use it**

In `app/services/paper_inventory.py`, add:

```python
def paper_inventory_group_key(batch: PaperInventoryBatch) -> tuple:
    return (
        batch.specification_id,
        batch.paper_type,
        batch.model,
        batch.material_name,
        batch.thickness,
        batch.inner_diameter,
        batch.outer_diameter,
        batch.length,
        batch.width,
    )
```

Replace the inline `key = (...)` in `paper_inventory_groups` with:

```python
key = paper_inventory_group_key(batch)
```

- [ ] **Step 4: Add the user-reported regression test**

Add a test that creates three live batches with models `3969.01`, `3960.02`, and `3970.01`, the same material and thickness, but different specification IDs and diameters. Assert:

```python
groups = paper_inventory_groups(batches)
self.assertEqual([group["model"] for group in groups], ["3969.01", "3960.02", "3970.01"])
self.assertEqual([group["quantity"] for group in groups], [100, 200, 300])
self.assertEqual([group["batch_count"] for group in groups], [1, 1, 1])
```

Add a second `3969.01` batch with the same specification ID and assert that only that group's quantity and batch count become `150` and `2`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_paper_material_management.py -q
```

Expected: all paper material management tests PASS.

- [ ] **Step 6: Commit the service and tests**

```bash
git add app/services/paper_inventory.py tests/test_paper_material_management.py
git commit -m "fix: keep paper inventory models separate"
```

---

### Task 2: Verify the rendered page and complete deployment checks

**Files:**
- Modify: `tests/test_paper_material_management.py`

**Interfaces:**
- Consumes: `paper_inventory_page(...)` and the grouping behavior from Task 1.
- Produces: a rendered-page regression assertion protecting the three separate rows and detail links.

- [ ] **Step 1: Add the rendered-page regression assertions**

Using an in-memory database, insert the three specifications and their batches, render `paper_inventory_page(db=db)`, and assert:

```python
self.assertEqual(inventory_html.count(">3969.01<"), 1)
self.assertEqual(inventory_html.count(">3960.02<"), 1)
self.assertEqual(inventory_html.count(">3970.01<"), 1)
for spec in specs:
    self.assertIn(
        f"/admin/paper-materials/detail?specification_id={spec.id}",
        inventory_html,
    )
```

- [ ] **Step 2: Run the paper module and full test suite**

Run:

```bash
.venv/bin/python -m pytest tests/test_paper_material_management.py tests/test_paper_exports.py -q
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q app tests
git diff --check
```

Expected: all tests PASS, compilation succeeds, and `git diff --check` prints nothing.

- [ ] **Step 3: Commit the rendered-page regression test if it was not included in Task 1**

```bash
git add tests/test_paper_material_management.py
git commit -m "test: protect paper inventory model rows"
```

- [ ] **Step 4: Restart the inventory service without disturbing port 8000**

Keep the unrelated process on port 8000 untouched. Stop only the inventory process bound to port 8001, then start from `/Users/luck/Desktop/杭州特耐时/backend`:

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Verify:

```bash
curl -fsS http://127.0.0.1:8001/health
curl -fsS http://127.0.0.1:8001/admin/paper-materials
```

Expected: health returns `{"status":"ok"}` and the inventory page responds successfully.

- [ ] **Step 5: Push and verify the remote commit**

```bash
git push origin main
git rev-parse main
git ls-remote origin refs/heads/main
git status --short --branch
```

Expected: local and remote `main` SHAs match and the branch is not ahead of `origin/main`. If GitHub authentication is still invalid, report the exact authentication error and do not claim the push succeeded.
