# Mobile Product and Scrap Batch Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add read-only product and scrap batch-detail pages to the WeChat mini program while keeping their data and write restrictions identical to the PC admin.

**Architecture:** Extend the existing /api/mobile read endpoints in app/routers/mobile.py and reuse the same MaterialInventory, InventoryTransactionRecord, ProductDrawing, and ScrapGenerationRecord rows used by the PC pages. Add two registered native mini-program pages that reload remote data on onShow; neither page registers confirmation components or exposes write actions.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Pydantic, unittest/pytest, native WeChat Mini Program JavaScript/WXML/WXSS, Node.js test runner.

## Global Constraints

- Product and scrap batch details are read-only.
- Product quantities change only through confirmed-drawing inbound, product outbound, and transaction reversal.
- Scrap quantities change only through scrap confirmation, scrap outbound, and transaction reversal.
- PC and mobile routes use the same database records; no mobile data copy or synchronization job is allowed.
- Detail pages reload on onShow and must expose loading, empty, error, and retry states.
- Existing user changes in docs/superpowers/plans/2026-08-04-wechat-mini-program-phase-1-foundation.md must remain untouched.

---

### Task 1: Mobile batch-detail read APIs

**Files:**
- Modify: app/routers/mobile.py
- Create: tests/test_mobile_batch_details.py

**Interfaces:**
- Consumes: MaterialInventory, InventoryTransactionRecord, ProductDrawing, ScrapGenerationRecord, find_scrap_batches_for_outbound(), _transaction_rows().
- Produces: product_transactions(product_code: str = "", db: Session), scrap_batch_details(group_key: str, db: Session), ScrapBatchOut, ScrapBatchDetailsOut.

- [ ] **Step 1: Write failing product-detail API tests**

Create tests/test_mobile_batch_details.py with an in-memory SQLAlchemy database. Seed two product batches for TNX-DETAIL and one unrelated product, plus transaction rows for all three. Assert:

~~~python
batches = product_batches("TNX-DETAIL", db)
transactions = product_transactions(product_code="TNX-DETAIL", db=db)

self.assertEqual({item.id for item in batches}, {first.id, second.id})
self.assertEqual({row.inventory_id for row in transactions}, {first.id, second.id})
self.assertNotIn(unrelated.id, {row.inventory_id for row in transactions})
~~~

Also assert that product_batches("UNKNOWN", db) and product_transactions(product_code="UNKNOWN", db=db) return empty lists.

- [ ] **Step 2: Run the product test and verify RED**

Run:

~~~bash
.venv/bin/python -m pytest tests/test_mobile_batch_details.py::MobileBatchDetailsTest::test_product_details_only_return_selected_product_batches_and_transactions -q
~~~

Expected: FAIL because product_transactions does not accept product_code.

- [ ] **Step 3: Implement product-level transaction filtering**

Change the product transaction endpoint without breaking the existing unfiltered call:

~~~python
@router.get("/products/transactions", response_model=list[TransactionOut])
def product_transactions(
    product_code: str = "",
    db: Session = Depends(get_db),
) -> list[TransactionOut]:
    query = db.query(MaterialInventory.id).filter(
        MaterialInventory.inventory_type == "product"
    )
    if product_code.strip():
        code = product_code.strip()
        query = query.filter(
            (MaterialInventory.material_code == code)
            | (MaterialInventory.source_product_code == code)
        )
    inventory_ids = [row[0] for row in query.all()]
    if not inventory_ids:
        return []
    records = (
        db.query(InventoryTransactionRecord)
        .filter(InventoryTransactionRecord.inventory_id.in_(inventory_ids))
        .order_by(InventoryTransactionRecord.created_at.desc())
        .limit(500)
        .all()
    )
    return _transaction_rows(records, "product", db)
~~~

- [ ] **Step 4: Run the product test and verify GREEN**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Write failing scrap-detail tests**

In the same test file, seed two available scrap batches with the same group key, their ScrapGenerationRecord rows, drawings, and transaction rows. Assert:

~~~python
result = scrap_batch_details(
    group_key="65Mn||2.0||φ80||S1",
    db=db,
)

self.assertEqual(result.total_quantity, 3)
self.assertEqual({row.id for row in result.batches}, {first.id, second.id})
self.assertEqual(result.batches[0].source_product_code, "TNX-SCRAP")
self.assertIn("V2", result.batches[0].source_drawing_label)
self.assertEqual({row.inventory_id for row in result.transactions}, {first.id, second.id})
~~~

Add separate assertions that an invalid group_key raises HTTPException with status 400 and a valid empty group returns zero quantity and empty lists.

- [ ] **Step 6: Run the scrap tests and verify RED**

Run:

~~~bash
.venv/bin/python -m pytest tests/test_mobile_batch_details.py -q
~~~

Expected: FAIL because ScrapBatchDetailsOut and scrap_batch_details do not exist.

- [ ] **Step 7: Implement scrap response models and endpoint**

Import ScrapGenerationRecord. Add response models containing only serializable, read-only fields:

~~~python
class ScrapBatchOut(BaseModel):
    id: int
    source_product_code: str | None
    source_drawing_label: str
    quantity: int
    location: str
    status: str
    usable_size: str
    theoretical_size: str | None
    actual_size: str | None
    operator_name: str | None
    registered_at: str


class ScrapBatchDetailsOut(BaseModel):
    group_key: str
    total_quantity: int
    batches: list[ScrapBatchOut]
    transactions: list[TransactionOut]
~~~

Implement GET /scraps/batches before the dynamic scrap routes. Call find_scrap_batches_for_outbound(group_key, db) so parsing, status, positive quantity, specification, and location semantics match outbound. Load at most one ScrapGenerationRecord per batch using the latest record by registered_at/id, load referenced ProductDrawing rows, format labels as “产品型号 V版本” or “图纸 #ID”, load transactions for exactly the returned batch IDs, and return:

~~~python
return ScrapBatchDetailsOut(
    group_key=group_key,
    total_quantity=sum(item.quantity for item in batches),
    batches=batch_rows,
    transactions=_transaction_rows(records, "scrap", db),
)
~~~

The endpoint must not commit, create operation logs, or create MobileRequestRecord rows.

- [ ] **Step 8: Run API tests and commit**

Run:

~~~bash
.venv/bin/python -m pytest tests/test_mobile_batch_details.py tests/test_mobile_idempotency.py tests/test_pc_mobile_material_parity.py -q
~~~

Expected: PASS.

Commit:

~~~bash
git add app/routers/mobile.py tests/test_mobile_batch_details.py
git commit -m "feat: add mobile batch detail read APIs"
~~~

---

### Task 2: Read-only product batch-detail page

**Files:**
- Modify: miniprogram/app.json
- Modify: miniprogram/utils/api.js
- Modify: miniprogram/pages/inventory/list.js
- Modify: miniprogram/pages/inventory/list.wxml
- Create: miniprogram/pages/inventory/detail.js
- Create: miniprogram/pages/inventory/detail.json
- Create: miniprogram/pages/inventory/detail.wxml
- Create: miniprogram/pages/inventory/detail.wxss
- Modify: tests/test_miniprogram_foundation.py
- Modify: tests/miniprogram_material_api.test.js

**Interfaces:**
- Consumes: GET /api/mobile/products/{product_code}/batches and GET /api/mobile/products/transactions?product_code=...
- Produces: api.productTransactions(params), pages/inventory/detail, product-detail storage payload.

- [ ] **Step 1: Write failing mini-program tests**

Add assertions that:

~~~python
self.assertIn("pages/inventory/detail", app_json["pages"])
self.assertIn('bindtap="openDetail"', inventory_list_view)
self.assertIn("product-detail", inventory_list_source)
self.assertIn("productBatches", detail_source)
self.assertIn("productTransactions", detail_source)
self.assertIn("onShow", detail_source)
self.assertIn("<state-view", detail_view)
self.assertNotIn("confirm-sheet", detail_view)
self.assertNotIn("修改", detail_view)
self.assertNotIn("删除", detail_view)
~~~

Extend tests/miniprogram_material_api.test.js so api.productTransactions({ product_code: "TNX 001" }) is expected to send GET /api/mobile/products/transactions with the product_code query data.

- [ ] **Step 2: Run the tests and verify RED**

Run:

~~~bash
.venv/bin/python -m pytest tests/test_miniprogram_foundation.py -q
node --test tests/miniprogram_material_api.test.js
~~~

Expected: FAIL because the page, navigation, and parameterized API call are absent.

- [ ] **Step 3: Add registration, API parameters, and list navigation**

Register pages/inventory/detail in app.json. Change api.js to:

~~~javascript
productTransactions: (params = {}) =>
  request('/api/mobile/products/transactions', { data: params }),
~~~

Map display values in inventory/list.js and add:

~~~javascript
openDetail(event) {
  const item = this.data.items[event.currentTarget.dataset.index]
  wx.setStorageSync('product-detail', {
    product_code: item.product_code,
    quantity: item.quantity,
  })
  wx.navigateTo({ url: '/pages/inventory/detail' })
}
~~~

Bind each inventory card with data-index and bindtap, and display the existing chevron-right icon.

- [ ] **Step 4: Implement the read-only product detail page**

detail.json registers only state-view. detail.js loads product-detail storage, rejects a missing product_code with a visible error, and uses:

~~~javascript
const [batches, transactions] = await Promise.all([
  api.productBatches(product.product_code),
  api.productTransactions({ product_code: product.product_code }),
])
~~~

Map null display fields to “-”, compute total quantity from returned batches, and set batches/transactions atomically. detail.wxml contains a simple header, loading/error/empty state-view blocks, a “批次” data-list, and a “流水” ledger list. It contains no input, button, confirm-sheet, edit, delete, or adjustment controls. detail.wxss only adds spacing needed by these two sections and reuses global design primitives.

- [ ] **Step 5: Run tests and commit**

Run:

~~~bash
.venv/bin/python -m pytest tests/test_miniprogram_foundation.py -q
node --test tests/miniprogram_material_api.test.js
~~~

Expected: PASS.

Commit:

~~~bash
git add miniprogram/app.json miniprogram/utils/api.js miniprogram/pages/inventory tests/test_miniprogram_foundation.py tests/miniprogram_material_api.test.js
git commit -m "feat: add read-only product batch details"
~~~

---

### Task 3: Read-only scrap batch-detail page

**Files:**
- Modify: miniprogram/app.json
- Modify: miniprogram/utils/api.js
- Modify: miniprogram/pages/scraps/list.js
- Modify: miniprogram/pages/scraps/list.wxml
- Create: miniprogram/pages/scraps/detail.js
- Create: miniprogram/pages/scraps/detail.json
- Create: miniprogram/pages/scraps/detail.wxml
- Create: miniprogram/pages/scraps/detail.wxss
- Modify: tests/test_miniprogram_foundation.py
- Modify: tests/miniprogram_material_api.test.js

**Interfaces:**
- Consumes: GET /api/mobile/scraps/batches?group_key=...
- Produces: api.scrapBatches(groupKey), pages/scraps/detail, scrap-detail storage payload.

- [ ] **Step 1: Write failing mini-program tests**

Add assertions equivalent to the product page:

~~~python
self.assertIn("pages/scraps/detail", app_json["pages"])
self.assertIn('bindtap="openDetail"', scrap_list_view)
self.assertIn("scrap-detail", scrap_list_source)
self.assertIn("scrapBatches", detail_source)
self.assertIn("onShow", detail_source)
self.assertIn("<state-view", detail_view)
self.assertNotIn("confirm-sheet", detail_view)
self.assertNotIn("修改", detail_view)
self.assertNotIn("删除", detail_view)
~~~

Add a Node assertion that api.scrapBatches("65Mn||2||φ80||S1") sends GET /api/mobile/scraps/batches with group_key in request data.

- [ ] **Step 2: Run tests and verify RED**

Run the same focused Python and Node commands from Task 2. Expected: FAIL because the scrap detail page and API helper are absent.

- [ ] **Step 3: Add registration, API helper, and list navigation**

Register pages/scraps/detail. Add:

~~~javascript
scrapBatches: (groupKey) =>
  request('/api/mobile/scraps/batches', { data: { group_key: groupKey } }),
~~~

Store the complete selected summary card under scrap-detail and navigate to /pages/scraps/detail. Bind each scrap card using its array index and show the existing chevron-right icon.

- [ ] **Step 4: Implement the read-only scrap detail page**

detail.js loads scrap-detail storage, requires group_key, calls api.scrapBatches(group.group_key), formats missing values as “-”, and uses the server total_quantity, batches, and transactions. detail.wxml follows the same two-section read-only structure as product detail, but batch cards show source product, source drawing, quantity, location, usable/theoretical/actual size, operator, and registered time.

The page registers only state-view and includes no write controls.

- [ ] **Step 5: Run tests and commit**

Run:

~~~bash
.venv/bin/python -m pytest tests/test_miniprogram_foundation.py -q
node --test tests/miniprogram_material_api.test.js
~~~

Expected: PASS.

Commit:

~~~bash
git add miniprogram/app.json miniprogram/utils/api.js miniprogram/pages/scraps tests/test_miniprogram_foundation.py tests/miniprogram_material_api.test.js
git commit -m "feat: add read-only scrap batch details"
~~~

---

### Task 4: Full parity and regression verification

**Files:**
- Modify only if a verification failure exposes an implementation defect in files already listed above.

**Interfaces:**
- Consumes: all completed API and mini-program page interfaces.
- Produces: fresh passing verification evidence and a clean feature diff.

- [ ] **Step 1: Run the full Python suite**

Run:

~~~bash
.venv/bin/python -m pytest -q
~~~

Expected: all tests and subtests pass; existing third-party deprecation warnings may remain unchanged.

- [ ] **Step 2: Run all Node mini-program tests**

Run:

~~~bash
node --test tests/*.test.js
~~~

Expected: all tests pass with zero failures.

- [ ] **Step 3: Run static navigation/API audit**

Verify both new page paths are registered, all literal /pages navigation targets resolve, productBatches/productTransactions/scrapBatches are used, and detail WXML files contain no input, confirm-sheet, primary-action, danger-action, or modification text.

- [ ] **Step 4: Review scope and working tree**

Run:

~~~bash
git diff --check
git status --short
git log --oneline -4
~~~

Confirm only the feature files and the pre-existing user-modified phase-1 plan are present; do not stage or alter the pre-existing plan.

- [ ] **Step 5: Manual handoff checklist**

With the backend listening on 0.0.0.0:8000 and the phone connected to the same Wi-Fi:

1. Open a product summary, compare batch IDs, quantities, locations, and transactions with /admin/inventory/product/{product_code}.
2. Open a scrap summary, compare source products, quantities, locations, dimensions, and transactions with /admin/scraps/detail.
3. Perform no writes during the comparison.
4. Confirm both detail pages contain no modification controls.
