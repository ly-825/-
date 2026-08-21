# Unified Mobile Inventory and Operator Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every active account the same four-tab inventory mini program, move PC account/connection links into a bottom system area, and make the authenticated account display name the unforgeable operator on every new inventory write.

**Architecture:** Continue accepting legacy `operator_name` request fields for wire compatibility, but remove them from idempotency fingerprints and replace their value at every authenticated HTTP boundary with one shared `verified_operator_name()` helper. Keep the existing inventory services and transaction-time text snapshots. Unify mini-program routing and navigation without changing account-management or PC-login authorization.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy 2, SQLite, Pydantic, native WeChat mini-program JavaScript/WXML/WXSS, Python `unittest`, Node built-in test runner.

## Global Constraints

- `superadmin`, `owner`, and `employee` receive the same existing mini-program inventory query and write capabilities.
- Only `superadmin` and `owner` may approve PC login; account-management permissions remain unchanged.
- PC business routes remain restricted to `superadmin` and `owner` PC sessions.
- Every authenticated inventory write uses the current session account's trimmed `Account.display_name`; a missing or blank name rejects the write before a service mutation.
- Legacy clients may send `operator_name`, but it never affects inventory rows, audit rows, or idempotency matching.
- New clients do not send `operator_name`; all write screens show the current account name as read-only text.
- Existing transaction rows remain unchanged; account renames affect only later transactions.
- Inventory mutation, transaction row, audit row, idempotency response, and commit behavior stay in the existing single transaction.
- No database migration or inventory-calculation change is introduced.

---

## File Structure

### New files

- `app/auth/operator.py`: authenticated operator-name resolver shared by PC and mini-program write routes.
- `tests/test_operator_identity.py`: resolver, legacy-field compatibility, forged-name, rename, and idempotency assertions.
- `tests/miniprogram_operator_identity.test.js`: static client contract proving write forms no longer collect or submit an operator name.

### Existing files to modify

- `app/auth/dependencies.py`: allow every active account role through the inventory mini-program dependency.
- `app/services/operation_log.py`: trim the authenticated display name before storing the audit snapshot.
- `app/main.py`: rename the inventory dependency collection to reflect all mobile accounts.
- `app/routers/mobile.py`: override operator identity for product and scrap writes and exclude legacy names from request fingerprints.
- `app/routers/mobile_raw_plates.py`: override operator identity for plate inbound, outbound, batch edit, and reversal.
- `app/routers/mobile_paper.py`: override operator identity for paper inbound, outbound, and reversal.
- `app/routers/inventory.py`: override operator identity for authenticated JSON adjustment and reversal routes.
- `app/admin_pages.py`: move system links to the bottom and remove editable operator fields from product, scrap, and steel forms.
- `app/paper_admin_pages.py`: remove editable operator fields from paper forms.
- `miniprogram/app.json`: add the fourth “我的” tab using existing local tab icons.
- `miniprogram/utils/auth.js`: route every role to the plan tab after login.
- `miniprogram/utils/operator.js`: expose the saved safe account name for read-only write-screen display.
- `miniprogram/pages/auth/login.js`: always enter the tab bar with `wx.switchTab`.
- `miniprogram/pages/account/home.{js,wxml,wxss}`: become the shared “我的” page; only administrators see PC scan.
- Mini-program product/scrap/plate/paper write pages: remove `operator_name` state, inputs, confirmation payloads, and API payloads while retaining historical operator display.
- Existing Python and Node tests: update role, navigation, and form expectations.

---

### Task 1: Shared authenticated operator and all-role mobile inventory boundary

**Files:**
- Create: `app/auth/operator.py`
- Modify: `app/auth/dependencies.py`
- Modify: `app/main.py`
- Modify: `app/services/operation_log.py`
- Create: `tests/test_operator_identity.py`
- Modify: `tests/test_role_boundaries.py`
- Modify: `tests/test_pc_auth.py`

**Interfaces:**
- Produces: `verified_operator_name() -> str`, reading `get_current_account()` and returning a trimmed non-empty display name.
- Produces: `require_mobile_account`, accepting roles in `ACCOUNT_ROLES` only from a valid `miniprogram` session and setting `current_account` for the full request.
- Preserves: `require_miniprogram_account` for identity endpoints and `require_pc_admin_account` for PC administration.

- [ ] **Step 1: Write failing resolver and role-boundary tests**

Create `tests/test_operator_identity.py` with direct context tests:

```python
import unittest

from fastapi import HTTPException

from app.auth.context import current_account
from app.auth.operator import verified_operator_name
from app.models import Account


class OperatorIdentityTest(unittest.TestCase):
    def test_verified_operator_name_uses_trimmed_authenticated_name(self) -> None:
        token = current_account.set(
            Account(username="tns008", display_name=" 张三 ", role="employee")
        )
        try:
            self.assertEqual(verified_operator_name(), "张三")
        finally:
            current_account.reset(token)

    def test_verified_operator_name_rejects_missing_or_blank_identity(self) -> None:
        for account in (None, Account(username="tns008", display_name="   ", role="employee")):
            token = current_account.set(account)
            try:
                with self.assertRaises(HTTPException) as caught:
                    verified_operator_name()
                self.assertEqual(caught.exception.status_code, 401 if account is None else 400)
            finally:
                current_account.reset(token)
```

Replace `test_admin_mobile_identities_cannot_use_employee_business_routes` in `tests/test_role_boundaries.py` with `test_all_account_roles_can_use_mobile_business_routes`, asserting status `200` for employee, owner, and superadmin Bearer tokens. Keep the existing anonymous `401`, employee drawing-management `403`, PC-only management, and dependency-registration tests.

- [ ] **Step 2: Run tests and verify the old boundary fails**

Run:

```bash
.venv/bin/python -m unittest tests.test_operator_identity tests.test_role_boundaries tests.test_pc_auth -v
```

Expected: import failure for `app.auth.operator` and owner/superadmin inventory requests still return `403`.

- [ ] **Step 3: Add the authoritative resolver**

Create `app/auth/operator.py`:

```python
from fastapi import HTTPException, status

from app.auth.context import get_current_account


def verified_operator_name() -> str:
    account = get_current_account()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
        )
    name = account.display_name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="当前账号未设置姓名，不能执行库存操作",
        )
    return name
```

- [ ] **Step 4: Broaden only the mobile inventory dependency**

In `app/auth/dependencies.py`, import `ACCOUNT_ROLES` and change the role check to:

```python
async def require_mobile_account(
    request: Request,
    db: Session = Depends(get_db),
) -> AsyncGenerator[Account, None]:
    account = _miniprogram_account(request, db)
    if account.role not in ACCOUNT_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问")
    context_token = current_account.set(account)
    try:
        yield account
    finally:
        current_account.reset(context_token)
```

In `app/main.py`, rename `employee_dependencies` to `mobile_account_dependencies` and use it unchanged on the plan, raw-plate, and paper mobile routers. Do not change `owner_dependencies` or any drawing-management dependency.

In `app/services/operation_log.py`, preserve the explicit service-task fallback but normalize an authenticated actor:

```python
operator_name=(actor.display_name.strip() if actor else operator_name or None),
```

Extend the direct context test to call `record_operation_log()` with `display_name=" 张三 "` and assert the pending `OperationLog.operator_name` is `张三`.

- [ ] **Step 5: Run focused authentication tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_operator_identity tests.test_role_boundaries tests.test_pc_auth tests.test_wechat_auth tests.test_pc_wechat_login -v
```

Expected: all tests pass; employees remain unable to approve PC login or manage accounts.

- [ ] **Step 6: Commit the authentication boundary**

```bash
git add app/auth/operator.py app/auth/dependencies.py app/main.py app/services/operation_log.py tests/test_operator_identity.py tests/test_role_boundaries.py tests/test_pc_auth.py
git commit -m "feat: unify mobile inventory account access"
```

---

### Task 2: Authoritative product and scrap operators on mobile APIs

**Files:**
- Modify: `app/routers/mobile.py`
- Modify: `tests/test_operator_identity.py`
- Modify: `tests/test_mobile_idempotency.py`
- Modify: `tests/test_role_boundaries.py`

**Interfaces:**
- Consumes: `verified_operator_name() -> str` from Task 1.
- Preserves: legacy Pydantic fields `operator_name: str | None = None` on all mobile payload models.
- Produces: mobile product/scrap transaction and operation-log rows whose operator is always the authenticated account name.

- [ ] **Step 1: Write failing forgery and legacy-idempotency tests**

Extend the existing authenticated mobile fixture in `tests/test_operator_identity.py` with one test per operation family:

```python
def assert_actor_on_latest_rows(self, transaction_type: str, action: str) -> None:
    with self.Session() as db:
        transaction = (
            db.query(InventoryTransactionRecord)
            .filter_by(transaction_type=transaction_type)
            .order_by(InventoryTransactionRecord.id.desc())
            .first()
        )
        log = db.query(OperationLog).filter_by(action=action).order_by(OperationLog.id.desc()).first()
        self.assertEqual(transaction.operator_name, "张三")
        self.assertEqual(log.operator_name, "张三")
```

POST forged names to `/api/mobile/products/inbound`, `/api/mobile/products/outbound`, `/api/mobile/products/transactions/{id}/reverse`, `/api/mobile/scraps/{id}/confirm`, `/api/mobile/scraps/outbound`, and `/api/mobile/scraps/transactions/{id}/reverse`; assert each resulting inventory and audit row says `张三`.

In `tests/test_mobile_idempotency.py`, send the same `client_request_id` twice with `operator_name="伪造甲"` then `operator_name="伪造乙"`; assert both responses match and only one transaction exists.

- [ ] **Step 2: Run the focused tests and verify forged values still win**

Run:

```bash
.venv/bin/python -m unittest tests.test_operator_identity tests.test_mobile_idempotency tests.test_role_boundaries -v
```

Expected: transaction assertions fail with the forged payload name, and the changed-name retry conflicts with the stored request fingerprint.

- [ ] **Step 3: Exclude the legacy name from every product/scrap fingerprint**

Import the resolver and use one exact exclusion set in `app/routers/mobile.py`:

```python
from app.auth.operator import verified_operator_name

LEGACY_OPERATOR_FIELDS = {"client_request_id", "operator_name"}


def _mobile_request_payload(payload: MobileWritePayload) -> dict:
    return payload.model_dump(mode="json", exclude=LEGACY_OPERATOR_FIELDS)
```

Replace every product/scrap `model_dump(... exclude={"client_request_id"})` used for `request_payload` with `_mobile_request_payload(payload)`. For path-based requests, merge `transaction_id` or `inventory_id` with this result.

- [ ] **Step 4: Override the operator at each product/scrap write boundary**

At the start of all six write handlers, after idempotent replay has returned when applicable and before any service mutation, assign:

```python
operator_name = verified_operator_name()
```

Pass `operator_name` to `product_inbound_from_drawing()`, direct `InventoryTransactionRecord(...)` creation, `reverse_inventory_transaction()`, and every `record_operation_log()` call. Do not pass `payload.operator_name` anywhere except Pydantic compatibility parsing. The product inbound pattern must be:

```python
operator_name = verified_operator_name()
result = product_inbound_from_drawing(
    drawing=drawing,
    quantity=payload.quantity,
    location=payload.location,
    paper_material=payload.paper_material,
    operator_name=operator_name,
    db=db,
    idempotency_key=idempotency_key,
)
```

Apply the same local value to product outbound, product reversal, scrap confirmation, scrap outbound, and scrap reversal transaction and audit rows.

- [ ] **Step 5: Test rename snapshots**

Add a test that writes once as `张三`, commits `account.display_name = "张三新名"`, writes with a new request ID, and asserts the old row remains `张三` while the new row is `张三新名`.

Run:

```bash
.venv/bin/python -m unittest tests.test_operator_identity tests.test_mobile_idempotency tests.test_role_boundaries -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit mobile product and scrap identity**

```bash
git add app/routers/mobile.py tests/test_operator_identity.py tests/test_mobile_idempotency.py tests/test_role_boundaries.py
git commit -m "fix: bind mobile product and scrap writes to account"
```

---

### Task 3: Authoritative steel and paper operators on mobile APIs

**Files:**
- Modify: `app/routers/mobile_raw_plates.py`
- Modify: `app/routers/mobile_paper.py`
- Modify: `tests/test_mobile_raw_plate_api.py`
- Modify: `tests/test_paper_material_management.py`
- Modify: `tests/test_operator_identity.py`

**Interfaces:**
- Consumes: `verified_operator_name() -> str`.
- Produces: raw-plate and paper mobile write wrappers whose fingerprints omit the legacy name and whose actions receive the verified name explicitly.

- [ ] **Step 1: Add failing API forgery tests**

For raw plates, authenticate the mobile test app and send `operator_name="伪造钢板员"` through batch update, inbound, outbound, and reversal. For paper, do the same with inbound, outbound, and reversal. After each request, load the newest transaction or operation log and assert `operator_name == authenticated_account.display_name`.

Also retry one raw-plate and one paper request with the same request ID but a different forged name and assert the stored response replays without a second transaction.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
.venv/bin/python -m unittest tests.test_mobile_raw_plate_api tests.test_paper_material_management tests.test_operator_identity -v
```

Expected: forged names are present in saved rows or idempotency treats the retry as a different request.

- [ ] **Step 3: Make both write wrappers operator-neutral**

In `app/routers/mobile_raw_plates.py`, change `_run_write()` fingerprint construction to:

```python
request_payload = {
    **(path_data or {}),
    **payload.model_dump(
        mode="json", exclude={"client_request_id", "operator_name"}
    ),
}
```

Make the identical exclusion in `app/routers/mobile_paper.py::_write()`.

- [ ] **Step 4: Replace payload operator values before services run**

Import `verified_operator_name` in both routers. For raw plates, construct service dictionaries without the client field:

```python
service_payload = payload.model_dump(
    exclude={"client_request_id", "operator_name"}
)
service_payload["operator_name"] = verified_operator_name()
```

Use `service_payload` for `update_raw_plate_batch`, `inbound_raw_plate`, and `outbound_raw_plate_fifo`; pass `verified_operator_name()` directly to `reverse_raw_plate_transaction`.

For paper inbound use the same dictionary pattern. For paper outbound and reversal, replace `payload.operator_name` with one local `operator_name = verified_operator_name()` passed to `outbound_paper_fifo()` and `reverse_paper_transaction()`.

- [ ] **Step 5: Run mobile material parity and idempotency tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_mobile_raw_plate_api tests.test_paper_material_management tests.test_mobile_idempotency tests.test_pc_mobile_material_parity tests.test_paper_inventory_service_parity -v
```

Expected: all tests pass, including unchanged service-level tests that still pass explicit operator strings directly.

- [ ] **Step 6: Commit mobile material identity**

```bash
git add app/routers/mobile_raw_plates.py app/routers/mobile_paper.py tests/test_mobile_raw_plate_api.py tests/test_paper_material_management.py tests/test_operator_identity.py
git commit -m "fix: bind mobile material writes to account"
```

---

### Task 4: Authoritative operators and read-only identity on the PC backend

**Files:**
- Modify: `app/routers/inventory.py`
- Modify: `app/admin_pages.py`
- Modify: `app/paper_admin_pages.py`
- Modify: `tests/test_operator_identity.py`
- Modify: `tests/test_inventory_grouping_pages.py`
- Modify: `tests/test_steel_material_management.py`
- Modify: `tests/test_paper_material_management.py`

**Interfaces:**
- Consumes: `verified_operator_name() -> str` from the PC authentication context already installed by `require_pc_admin_account`.
- Produces: PC product, scrap, steel, and paper writes that ignore submitted operator values and render the authenticated name as non-editable text.

- [ ] **Step 1: Write failing PC form and forgery tests**

Use an authenticated owner TestClient and POST `operator_name="伪造电脑员"` to product inbound/outbound/adjust/reversal, scrap confirm/outbound/reversal, steel batch edit/inbound/outbound/reversal, paper inbound/outbound/reversal, and `/api/inventory/{id}/adjust`. Assert all new transaction and operation-log names equal the owner display name.

For the corresponding GET pages, assert:

```python
self.assertNotIn('name="operator_name"', response.text)
self.assertIn("当前操作员：老板", response.text)
```

- [ ] **Step 2: Run focused PC tests and verify failure**

Run:

```bash
.venv/bin/python -m unittest tests.test_operator_identity tests.test_inventory_grouping_pages tests.test_steel_material_management tests.test_paper_material_management -v
```

Expected: current forms contain editable `operator_name` inputs and posted forged names reach service rows.

- [ ] **Step 3: Add one reusable read-only field renderer**

In `app/admin_pages.py`, import the resolver and add:

```python
def operator_identity_field(label: str = "操作员") -> str:
    return (
        '<div class="operator-identity">'
        f'<span>{html.escape(label)}</span>'
        f'<strong>当前操作员：{html.escape(verified_operator_name())}</strong>'
        '</div>'
    )
```

Use this function in every product, scrap, and steel write form. Import it into `app/paper_admin_pages.py` and use it in paper inbound, outbound, and reversal forms. Add `.operator-identity` styling beside existing form styles; it must have no `<input>`, `<select>`, or hidden client-controlled value.

- [ ] **Step 4: Remove form parameters and overwrite every PC operator**

Delete `operator_name: str = Form("")` from all PC write handlers in `app/admin_pages.py` and `app/paper_admin_pages.py`. Inside each handler assign `operator_name = verified_operator_name()` before calling inventory services or writing logs.

In `app/routers/inventory.py`, preserve `InventoryAdjust.operator_name` and `TransactionReverse.operator_name` for API compatibility, but ignore them:

```python
operator_name = verified_operator_name()
record = adjust_inventory_quantity(
    item,
    payload.transaction_type,
    payload.quantity,
    operator_name,
    payload.remark,
    db,
)
```

Use the same verified value for reversal and both operation logs. Confirm with `rg -n "payload\.operator_name|operator_name: str = Form" app/routers/inventory.py app/admin_pages.py app/paper_admin_pages.py` that only response display/history or compatibility declarations remain.

- [ ] **Step 5: Run the PC inventory regression tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_operator_identity tests.test_inventory_grouping_pages tests.test_steel_material_management tests.test_paper_material_management tests.test_pc_mobile_material_parity -v
```

Expected: all tests pass; historical operator columns continue to render.

- [ ] **Step 6: Commit PC operator enforcement**

```bash
git add app/routers/inventory.py app/admin_pages.py app/paper_admin_pages.py tests/test_operator_identity.py tests/test_inventory_grouping_pages.py tests/test_steel_material_management.py tests/test_paper_material_management.py
git commit -m "fix: bind PC inventory writes to account"
```

---

### Task 5: Bottom system settings and unified four-tab mini program

**Files:**
- Modify: `app/admin_pages.py`
- Modify: `tests/test_admin_navigation_and_drawing_confirm.py`
- Modify: `tests/test_mobile_connection.py`
- Modify: `miniprogram/app.json`
- Modify: `miniprogram/utils/auth.js`
- Modify: `miniprogram/pages/auth/login.js`
- Modify: `miniprogram/pages/account/home.js`
- Modify: `miniprogram/pages/account/home.wxml`
- Modify: `miniprogram/pages/account/home.wxss`
- Modify: `tests/test_miniprogram_foundation.py`
- Modify: `tests/miniprogram_auth.test.js`

**Interfaces:**
- Produces: `homeForRole(role) -> "/pages/plan/home"` for all valid account roles.
- Produces: fourth tab `pages/account/home` labeled “我的”, using existing `assets/tabbar/workbench.png` and `workbench-active.png`.
- Produces: `canApprovePcLogin(role) -> boolean`, true only for `owner` and `superadmin`.

- [ ] **Step 1: Write failing navigation and role-routing tests**

Update `tests/miniprogram_auth.test.js`:

```javascript
test('every account role enters the shared inventory tab bar', () => {
  for (const role of ['employee', 'owner', 'superadmin']) {
    assert.equal(auth.homeForRole(role), '/pages/plan/home')
  }
})

test('only administrators may approve PC login', () => {
  assert.equal(auth.canApprovePcLogin('employee'), false)
  assert.equal(auth.canApprovePcLogin('owner'), true)
  assert.equal(auth.canApprovePcLogin('superadmin'), true)
})
```

Update `tests/test_miniprogram_foundation.py` to expect the exact four-tab list `计划/材料/成品/我的`. Update PC navigation tests to compare string positions and assert `小程序连接` and `账号管理` occur inside a `系统设置` block after the last business section and before `退出登录`.

- [ ] **Step 2: Run navigation tests and verify failure**

Run:

```bash
node --test tests/miniprogram_auth.test.js
.venv/bin/python -m unittest tests.test_miniprogram_foundation tests.test_admin_navigation_and_drawing_confirm tests.test_mobile_connection -v
```

Expected: administrators still route to the isolated account page, only three tabs exist, and PC system links remain at the top.

- [ ] **Step 3: Move PC system links to the sidebar bottom**

Remove the two system links from the first `.nav-root`. Make `aside` a column flex container and give `nav` its normal content height:

```css
aside { display:flex; flex-direction:column; }
nav { flex:0 0 auto; }
```

After the business `<nav>` and before the logout form, render:

```html
<div class="sidebar-system" aria-label="系统设置">
  <div class="nav-subhead">系统设置</div>
  <a href="/admin/mobile-connection">小程序连接</a>
  <a href="/admin/accounts">账号管理</a>
</div>
```

Give `.sidebar-system` `margin-top:auto`, top border, and the same active/hover link treatment as `.nav-root`. Change the logout form to a normal small top margin so the system block and logout remain grouped at the bottom.

- [ ] **Step 4: Add the fourth tab and unified login route**

Append this exact item to `miniprogram/app.json`:

```json
{
  "pagePath": "pages/account/home",
  "text": "我的",
  "iconPath": "assets/tabbar/workbench.png",
  "selectedIconPath": "assets/tabbar/workbench-active.png"
}
```

In `miniprogram/utils/auth.js` implement and export:

```javascript
function homeForRole() {
  return '/pages/plan/home'
}

function canApprovePcLogin(role) {
  return role === 'owner' || role === 'superadmin'
}
```

In `miniprogram/pages/auth/login.js`, replace the branch between `switchTab` and `reLaunch` with one `wx.switchTab({ url })` call.

- [ ] **Step 5: Turn the administrator page into the shared “我的” page**

In `pages/account/home.js`, accept every saved account, set `canApprovePcLogin: auth.canApprovePcLogin(account.role)`, and map all role labels:

```javascript
const ROLE_LABELS = {
  superadmin: '主管理员',
  owner: '老板',
  employee: '员工'
}
```

In WXML show display name, username, and role for all roles. Wrap the existing PC-login card in `wx:if="{{canApprovePcLogin}}"`; keep logout visible to all roles. The page must call the existing server-revoking `auth.logout()` implementation.

- [ ] **Step 6: Run navigation tests**

Run:

```bash
node --test tests/miniprogram_auth.test.js tests/miniprogram_pc_login.test.js
.venv/bin/python -m unittest tests.test_miniprogram_foundation tests.test_admin_navigation_and_drawing_confirm tests.test_mobile_connection -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit navigation unification**

```bash
git add app/admin_pages.py tests/test_admin_navigation_and_drawing_confirm.py tests/test_mobile_connection.py miniprogram/app.json miniprogram/utils/auth.js miniprogram/pages/auth/login.js miniprogram/pages/account/home.js miniprogram/pages/account/home.wxml miniprogram/pages/account/home.wxss tests/test_miniprogram_foundation.py tests/miniprogram_auth.test.js
git commit -m "feat: unify inventory and account navigation"
```

---

### Task 6: Remove editable operator identity from every mini-program write screen

**Files:**
- Create: `tests/miniprogram_operator_identity.test.js`
- Create: `miniprogram/utils/operator.js`
- Modify: `miniprogram/pages/inventory/inbound.{js,wxml}`
- Modify: `miniprogram/pages/inventory/outbound.{js,wxml}`
- Modify: `miniprogram/pages/inventory/transactions.{js,wxml}`
- Modify: `miniprogram/pages/scraps/pending.{js,wxml}`
- Modify: `miniprogram/pages/scraps/outbound.{js,wxml}`
- Modify: `miniprogram/pages/scraps/transactions.{js,wxml}`
- Modify: `miniprogram/pages/raw-plates/inbound.{js,wxml}`
- Modify: `miniprogram/pages/raw-plates/outbound.{js,wxml}`
- Modify: `miniprogram/pages/raw-plates/detail.{js,wxml}`
- Modify: `miniprogram/pages/raw-plates/transactions.js`
- Modify: `miniprogram/pages/paper/inbound.{js,wxml}`
- Modify: `miniprogram/pages/paper/outbound.{js,wxml}`
- Modify: `miniprogram/pages/paper/transactions.js`

**Interfaces:**
- Produces: `currentOperatorName(wxApi) -> string` from the saved safe account for read-only confirmation display.
- Produces: no outbound mini-program write payload containing an `operator_name` property.
- Preserves: `operator_name` rendering on transaction/detail history pages returned by the server.

- [ ] **Step 1: Add a failing static client-contract test**

Create `tests/miniprogram_operator_identity.test.js`:

```javascript
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const operator = require('../miniprogram/utils/operator')

const ROOT = path.join(__dirname, '..', 'miniprogram', 'pages')
const WRITE_PAGES = [
  'inventory/inbound', 'inventory/outbound', 'inventory/transactions',
  'scraps/pending', 'scraps/outbound', 'scraps/transactions',
  'raw-plates/inbound', 'raw-plates/outbound', 'raw-plates/detail',
  'raw-plates/transactions', 'paper/inbound', 'paper/outbound',
  'paper/transactions'
]

test('inventory write pages never expose an editable operator field', () => {
  for (const page of WRITE_PAGES) {
    const view = fs.readFileSync(path.join(ROOT, `${page}.wxml`), 'utf8')
    assert.doesNotMatch(view, /data-field=["']operator_name["']/, page)
    assert.doesNotMatch(view, /name=["']operator_name["']/, page)
  }
})

test('read-only operator name comes from safe account storage', () => {
  const wx = {
    getStorageSync: (key) => key === 'tns_auth_account'
      ? { username: 'tns008', display_name: '张三', role: 'employee' }
      : ''
  }
  assert.equal(operator.currentOperatorName(wx), '张三')
})

test('write-page source never creates or submits operator_name', () => {
  for (const page of WRITE_PAGES) {
    const source = fs.readFileSync(path.join(ROOT, `${page}.js`), 'utf8')
    assert.doesNotMatch(source, /operator_name\s*:/, page)
    assert.doesNotMatch(source, /form\.operator_name/, page)
  }
})
```

- [ ] **Step 2: Run the contract test and verify every current input is found**

Run:

```bash
node --test tests/miniprogram_operator_identity.test.js
```

Expected: failures identify the existing editable product, scrap, steel, and paper operator controls.

- [ ] **Step 3: Add one shared read-only operator utility**

Create `miniprogram/utils/operator.js`:

```javascript
const auth = require('./auth')

function currentOperatorName(wxApi) {
  const account = auth.loadAccount(wxApi)
  return account ? account.display_name : ''
}

module.exports = { currentOperatorName }
```

- [ ] **Step 4: Remove operator state and payloads from product and scrap pages**

Import `const { currentOperatorName } = require('../../utils/operator')` in each touched product and scrap write page.

Remove `operator_name` from every `form` and `reverseForm`, remove its input handlers and API payload property, and replace confirmation lines with `{ label: '操作员', value: currentOperatorName(wx) || '当前账号' }`. In WXML replace inputs with:

```html
<view class="form-field">
  <view class="form-label">操作员</view>
  <view class="selected-value">{{operatorName}}</view>
</view>
```

Call the helper as `currentOperatorName(wx)`. Set `operatorName` from it in `onShow`. Keep `item.operator_name` and derived history text unchanged on detail and transaction lists.

- [ ] **Step 5: Remove operator state and payloads from steel and paper pages**

Apply the same rules to raw-plate inbound, outbound, batch edit, reversal and paper inbound, outbound, reversal. For compact one-line files, format the touched file normally before editing so the resulting source remains reviewable. API calls must receive payloads containing business fields, `remark`, and the request tracker ID, but no `operator_name`.

- [ ] **Step 6: Run all mini-program unit and static tests**

Run:

```bash
node --test tests/*.test.js
```

Expected: all Node tests pass, including legacy connection, authentication, PC-login, request-ID, and new operator contracts.

- [ ] **Step 7: Commit mini-program identity UX**

```bash
git add miniprogram/pages miniprogram/utils/operator.js tests/miniprogram_operator_identity.test.js
git commit -m "feat: make mini program operator identity read only"
```

---

### Task 7: Full verification and release artifact check

**Files:**
- Modify only if a verification failure exposes a defect in the files listed by Tasks 1–6.

**Interfaces:**
- Verifies: Python backend, Node mini-program logic, production mini-program build, whitespace, and clean Git state.

- [ ] **Step 1: Run the complete Python suite**

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Expected: every Python test passes with zero failures and zero errors.

- [ ] **Step 2: Run the complete Node suite**

```bash
node --test tests/*.test.js
```

Expected: every Node test passes.

- [ ] **Step 3: Build a release mini program into a temporary directory**

```bash
build_dir="$(mktemp -d /tmp/tenaishi-mini-release.XXXXXX)"
.venv/bin/python scripts/build_miniprogram_release.py \
  --base-url "https://inventory.hz-tns.com" \
  --output "${build_dir:?}"
test -f "${build_dir:?}/app.json"
test -f "${build_dir:?}/release-config.js"
```

Expected: build exits `0`, both files exist, and the generated configuration contains the HTTPS release base URL. Do not delete the directory until the artifact has been inspected; it is under `/tmp` and contains no production secret.

- [ ] **Step 4: Scan the final source for forbidden write paths**

Run:

```bash
rg -n "payload\.operator_name|operator_name: str = Form|data-field=[\"']operator_name" \
  app/routers app/admin_pages.py app/paper_admin_pages.py miniprogram/pages
```

Expected: matches are limited to legacy Pydantic schema declarations and read-only historical response/display fields; no authenticated handler passes a client value to an inventory service and no form renders an editable operator input.

- [ ] **Step 5: Check the diff and create the final verification commit only if needed**

```bash
git diff --check
git status --short
```

Expected: `git diff --check` exits `0`. If verification required source fixes, add only those exact files and commit them with `git commit -m "test: complete unified inventory verification"`; otherwise leave the six task commits unchanged.

---

## Production Handoff After Implementation

Implementation completion does not itself authorize production deployment or WeChat submission. After code review and merge:

1. Deploy the backend commit first and verify `/health` returns `200`.
2. Build the mini program with the confirmed production HTTPS base URL.
3. Upload a new WeChat mini-program version and generate a preview QR for administrator and employee acceptance.
4. Test one low-risk transaction per role and confirm the stored operator names.
5. Submit the verified mini-program build for WeChat review; the currently published version continues working during review because legacy `operator_name` fields remain accepted and ignored.
