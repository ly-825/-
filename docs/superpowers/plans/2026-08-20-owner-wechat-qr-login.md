# Owner WeChat QR Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a three-level `superadmin`/`owner`/`employee` account system in which administrators bind their own WeChat account and confirm short-lived PC login requests through the existing mini program.

**Architecture:** Keep WeChat identity exchange and all authorization decisions in FastAPI. Add a database-backed, single-use PC login state machine where the QR carries only a request token and the originating browser keeps a separate secret required to consume approval. Reuse the existing account, activation, session, audit, native mini-program, SQLite, Nginx, and deployment patterns.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy 2, SQLite, Pydantic, existing `qrcode[pil]`, native WeChat mini program JavaScript/WXML/WXSS, Node built-in test runner, Python `unittest`, Nginx, systemd.

## Global Constraints

- Continue using the existing mini program AppID; do not add WeChat Open Platform website OAuth.
- Exactly one active `superadmin` is supported; ordinary HTTP endpoints cannot create a second one.
- `superadmin` manages owners and employees; `owner` manages employees only; `employee` manages no accounts.
- Every account binds at most one WeChat OpenID, and the existing unique database constraint keeps every OpenID bound to at most one account.
- Administrative activation codes expire after 30 minutes; employee activation codes remain valid for 24 hours; every activation code is hashed, shown once, and invalid after use.
- PC login QR requests expire after 2 minutes and may be approved once and consumed once.
- Scanning alone never logs in a PC; the mini program must show request details and require an explicit confirm or deny action.
- The QR never contains a password, OpenID, activation code, browser secret, PC session token, or business data.
- PC session tokens are issued only to the browser through a secure same-site Cookie and never returned to the mini program.
- Disabling or unbinding an account increments `session_version` so existing PC and mini-program sessions stop working immediately.
- Existing employee WeChat bindings, inventory data, transactions, drawings, and uploaded files must remain unchanged.
- Keep the legacy password/TOTP emergency login enabled until real superadmin QR login succeeds in production; then disable it with configuration rather than deleting recovery code.
- Run database migration checks against a copy of production data before deployment, and create a verified production backup immediately before account cutover.

---

## File Structure

### New files

- `app/auth/roles.py`: role constants and pure authorization predicates.
- `app/auth/pc_login_service.py`: PC login request state machine and browser-session consumption.
- `app/auth/pc_login_api.py`: public browser and authenticated mini-program QR endpoints.
- `app/auth/account_pages.py`: role-aware owner and employee management HTML endpoints.
- `scripts/manage_superadmin.py`: server-only bootstrap and WeChat-reset commands.
- `tests/test_auth_roles.py`: role matrix and generic activation coverage.
- `tests/test_pc_wechat_login.py`: QR lifecycle, expiry, replay, role, Cookie, and audit coverage.
- `tests/test_owner_admin.py`: superadmin/owner account-management boundary coverage.
- `tests/miniprogram_pc_login.test.js`: mini-program scan/confirm client behavior.
- `miniprogram/utils/pc-login.js`: scan payload validation and QR endpoint client helpers.
- `miniprogram/pages/account/home.{js,json,wxml,wxss}`: admin mini-program home and scan entry.
- `miniprogram/pages/auth/pc-login-confirm.{js,json,wxml,wxss}`: explicit PC login approval page.

### Existing files to modify

- `app/models.py`: add `PcLoginRequest`.
- `app/schema_migrations.py`: add login-request timestamp metadata and idempotent production schema creation.
- `app/config.py`: add QR lifetime and legacy-login feature settings.
- `app/auth/service.py`: generic account activation/login plus role-aware lifetimes and session revocation.
- `app/auth/api.py`: allow all bound active roles to obtain a mini-program identity session.
- `app/auth/dependencies.py`: separate generic mini-program identity, employee business, and PC admin dependencies.
- `app/auth/pages.py`: retain logout and feature-gated legacy password login; remove employee management code after routing it through `account_pages.py`.
- `app/main.py`: register the new routers and use the renamed PC dependency.
- `app/admin_pages.py`: expose “账号管理” navigation.
- `miniprogram/app.js`: retain safe account metadata in global state.
- `miniprogram/app.json`: register admin and confirmation pages.
- `miniprogram/utils/auth.js`: persist safe role metadata and route each role to the correct home.
- `miniprogram/pages/auth/login.{js,wxml,wxss}`: use role-neutral copy and activation.
- `deploy/nginx-personal-inventory.conf`: rate-limit authentication and QR endpoints.
- `deploy/README.md`: document superadmin bootstrap, compatible deployment, cutover, and recovery.
- `deploy/smoke-test.sh`: verify the public QR login page without exposing secrets.
- Existing authentication, role, deployment, and mini-program tests: update names and expected role behavior without reducing security assertions.

---

### Task 1: Role vocabulary and persistent PC login request model

**Files:**
- Create: `app/auth/roles.py`
- Modify: `app/models.py`
- Modify: `app/schema_migrations.py`
- Modify: `app/config.py`
- Create: `tests/test_auth_roles.py`
- Modify: `tests/test_auth_models.py`

**Interfaces:**
- Produces: `SUPERADMIN`, `OWNER`, `EMPLOYEE`, `PC_ADMIN_ROLES`, `ACCOUNT_ROLES`, `can_manage_role(actor_role: str, target_role: str) -> bool`.
- Produces: `PcLoginRequest` SQLAlchemy model with request/browser hashes, status, request metadata, approval fields, and timestamps.
- Produces settings `pc_login_request_seconds: int = 120` and `legacy_password_login_enabled: bool = True`.

- [ ] **Step 1: Write failing role and model tests**

Add exact matrix assertions to `tests/test_auth_roles.py`:

```python
import unittest

from app.auth.roles import EMPLOYEE, OWNER, SUPERADMIN, can_manage_role


class AuthRoleTest(unittest.TestCase):
    def test_account_management_matrix(self) -> None:
        self.assertTrue(can_manage_role(SUPERADMIN, OWNER))
        self.assertTrue(can_manage_role(SUPERADMIN, EMPLOYEE))
        self.assertTrue(can_manage_role(OWNER, EMPLOYEE))
        self.assertFalse(can_manage_role(OWNER, OWNER))
        self.assertFalse(can_manage_role(OWNER, SUPERADMIN))
        self.assertFalse(can_manage_role(EMPLOYEE, EMPLOYEE))
        self.assertFalse(can_manage_role(SUPERADMIN, SUPERADMIN))
```

Extend `tests/test_auth_models.py` to require `PcLoginRequest` fields and exact defaults:

```python
self.assertEqual(settings.pc_login_request_seconds, 120)
self.assertTrue(settings.legacy_password_login_enabled)
self.assertGreaterEqual(
    {column.name for column in PcLoginRequest.__table__.columns},
    {
        "request_token_hash", "browser_secret_hash", "status",
        "device_summary", "source_ip", "approved_account_id",
        "expires_at", "approved_at", "consumed_at", "created_at",
    },
)
```

- [ ] **Step 2: Run tests and verify they fail for missing symbols**

Run:

```bash
python -m unittest tests.test_auth_roles tests.test_auth_models -v
```

Expected: import failure for `app.auth.roles` or `PcLoginRequest`.

- [ ] **Step 3: Implement role constants and model**

Create `app/auth/roles.py` with this public contract:

```python
SUPERADMIN = "superadmin"
OWNER = "owner"
EMPLOYEE = "employee"
ACCOUNT_ROLES = frozenset({SUPERADMIN, OWNER, EMPLOYEE})
PC_ADMIN_ROLES = frozenset({SUPERADMIN, OWNER})


def can_manage_role(actor_role: str, target_role: str) -> bool:
    if actor_role == SUPERADMIN:
        return target_role in {OWNER, EMPLOYEE}
    if actor_role == OWNER:
        return target_role == EMPLOYEE
    return False
```

Add `PcLoginRequest` to `app/models.py` using `String(64)` unique indexed hashes, `String(20)` indexed status, nullable `String(200)` metadata, nullable `ForeignKey("accounts.id")`, and China-time defaults. Add the new table timestamps to `TIMESTAMP_COLUMNS`, and add idempotent `CREATE TABLE IF NOT EXISTS pc_login_requests` plus indexes in `ensure_runtime_schema()` so an imported database upgrades safely even when schema creation order changes.

- [ ] **Step 4: Add settings and run focused tests**

Add to `Settings`:

```python
pc_login_request_seconds: int = 120
legacy_password_login_enabled: bool = True
```

Run:

```bash
python -m unittest tests.test_auth_roles tests.test_auth_models -v
```

Expected: all tests pass.

- [ ] **Step 5: Verify migration idempotency on a temporary SQLite database**

Run:

```bash
python -m unittest tests.test_auth_models -v
python -m unittest tests.test_python_compatibility -v
```

Expected: all tests pass; repeated schema setup reports no duplicate-table or duplicate-index error.

- [ ] **Step 6: Commit the model boundary**

```bash
git add app/auth/roles.py app/models.py app/schema_migrations.py app/config.py tests/test_auth_roles.py tests/test_auth_models.py
git commit -m "feat: add admin roles and PC login request model"
```

---

### Task 2: Generic account activation and superadmin recovery service

**Files:**
- Modify: `app/auth/service.py`
- Create: `scripts/manage_superadmin.py`
- Modify: `tests/test_auth_service.py`
- Create: `tests/test_manage_superadmin_script.py`
- Modify: `tests/test_create_owner_script.py`

**Interfaces:**
- Consumes: role constants and `can_manage_role()` from Task 1.
- Produces: `create_managed_account(db, *, actor, username, display_name, role, now=None) -> tuple[Account, str]`.
- Produces: `activate_account(db, username, activation_code, wechat_openid, now=None) -> tuple[Account, str]`.
- Produces: `login_bound_wechat(db, wechat_openid, now=None) -> tuple[Account, str]`.
- Produces: `revoke_all_sessions(db, account) -> None` and role-aware `regenerate_activation()`/`unbind_wechat()`.
- Produces CLI helpers `bootstrap_superadmin(db, username, display_name, now=None)` and `reset_superadmin_wechat(db, username, now=None)`.

- [ ] **Step 1: Write failing generic activation and management tests**

Add cases to `tests/test_auth_service.py` that use an in-memory database:

```python
superadmin = bootstrap_superadmin(db, "admin", "主管理员", now=self.now)[0]
owner, owner_code = create_managed_account(
    db, actor=superadmin, username="boss1", display_name="老板一",
    role="owner", now=self.now,
)
self.assertEqual(owner.activation_expires_at, self.now + timedelta(minutes=30))
bound_owner, token = activate_account(
    db, "boss1", owner_code, "openid-owner-1", now=self.now,
)
self.assertEqual(bound_owner.role, "owner")
self.assertIsNotNone(resolve_session(db, token, "miniprogram", now=self.now))
```

Also assert an owner can create an employee but not an owner, a duplicate OpenID is rejected with role-neutral text, an employee code lasts 24 hours, and disabling/unbinding increments `session_version`.

- [ ] **Step 2: Run the focused tests and verify missing generic services**

Run:

```bash
python -m unittest tests.test_auth_service tests.test_manage_superadmin_script -v
```

Expected: import errors for the new functions/script.

- [ ] **Step 3: Generalize account creation and activation**

In `app/auth/service.py`, keep compatibility wrappers `create_employee()` and `activate_employee()` while routing them through these rules:

```python
def activation_lifetime(role: str) -> timedelta:
    return timedelta(hours=24) if role == EMPLOYEE else timedelta(minutes=30)


def create_managed_account(db, *, actor, username, display_name, role, now=None):
    if not can_manage_role(actor.role, role):
        raise ValueError("无权创建该角色账号")
    return _create_activation_account(db, username, display_name, role, now)


def activate_account(db, username, activation_code, wechat_openid, now=None):
    current_time = _clock(now)
    account = db.query(Account).filter(
        Account.username == _normalized_username(username)
    ).first()
    valid = bool(
        account and account.role in ACCOUNT_ROLES and account.is_active
        and account.activation_code_hash and account.activation_expires_at
        and account.activation_expires_at > current_time
        and secrets_match(
            activation_code, account.activation_code_hash, _required_pepper()
        )
    )
    if not valid or account is None:
        raise ValueError("激活码无效或已过期")
    duplicate = db.query(Account).filter(
        Account.wechat_openid == wechat_openid, Account.id != account.id
    ).first()
    if duplicate:
        raise ValueError("微信已绑定其他账号")
    account.wechat_openid = wechat_openid
    account.activation_code_hash = None
    account.activation_expires_at = None
    db.commit()
    token = create_session(
        db, account, "miniprogram",
        current_time + timedelta(days=settings.mobile_session_days),
    )
    return account, token
```

Use one generic duplicate-binding message: `微信已绑定其他账号`. Make `regenerate_activation()` and `unbind_wechat()` use `activation_lifetime(account.role)` and increment `session_version` exactly once.

- [ ] **Step 4: Implement the server-only superadmin command**

Create `scripts/manage_superadmin.py` with subcommands:

```text
bootstrap --username admin --display-name 主管理员
reset-wechat --username admin
```

`bootstrap_superadmin()` must refuse creation when any `superadmin` exists, create no password/TOTP secret, hash a 30-minute activation code, and return the code once. `reset_superadmin_wechat()` must require the named account to be the sole superadmin, clear its OpenID, revoke sessions through `session_version`, create a fresh 30-minute activation code, and return it once. The command prints no OpenID, hash, session token, or database URL.

- [ ] **Step 5: Preserve the legacy owner bootstrap only as a temporary emergency path**

Update `tests/test_create_owner_script.py` only where role constants replace string literals. Do not delete `scripts/create_owner.py`; the production cutover task will feature-gate its HTTP use after QR validation.

- [ ] **Step 6: Run service and CLI tests**

Run:

```bash
python -m unittest tests.test_auth_service tests.test_manage_superadmin_script tests.test_create_owner_script -v
```

Expected: all tests pass; no activation code appears in audit data or database plaintext.

- [ ] **Step 7: Commit generic activation and recovery**

```bash
git add app/auth/service.py scripts/manage_superadmin.py tests/test_auth_service.py tests/test_manage_superadmin_script.py tests/test_create_owner_script.py
git commit -m "feat: add role-aware WeChat activation"
```

---

### Task 3: Mini-program identity API for all three roles

**Files:**
- Modify: `app/auth/dependencies.py`
- Modify: `app/auth/api.py`
- Modify: `tests/test_wechat_auth.py`
- Modify: `tests/test_role_boundaries.py`
- Modify: `tests/test_pc_auth.py`

**Interfaces:**
- Consumes: `activate_account()` and `login_bound_wechat()` from Task 2.
- Produces: `require_miniprogram_account()` for any active bound role.
- Preserves: `require_mobile_account()` as employee-only business authorization.
- Produces safe account JSON `{username, display_name, role}` with no OpenID, hashes, or session data.

- [ ] **Step 1: Write failing owner/superadmin WeChat API tests**

Add tests to `tests/test_wechat_auth.py`:

```python
def test_bound_owner_can_login_but_cannot_use_employee_business_dependency(self):
    login = self.client.post("/api/auth/wechat/login", json={"wx_code": "owner-code"})
    self.assertEqual(login.status_code, 200)
    self.assertEqual(login.json()["account"]["role"], "owner")
    self.assertNotIn("openid", login.text.lower())

def test_me_accepts_every_active_role_and_returns_safe_fields(self):
    self.assertEqual(
        set(response.json()), {"username", "display_name", "role"}
    )
```

Extend role-boundary tests so an owner/superadmin mini-program Bearer token receives `403` on employee inventory routes, while an employee still receives `200`.

- [ ] **Step 2: Run focused API tests and verify current employee-only behavior fails**

Run:

```bash
python -m unittest tests.test_wechat_auth tests.test_role_boundaries tests.test_pc_auth -v
```

Expected: owner/superadmin mini login tests fail with `401` or missing dependency.

- [ ] **Step 3: Add generic mini-program identity dependency**

Implement `require_miniprogram_account()` using the existing Bearer-token resolution and context handling, without a role restriction. Implement `require_mobile_account()` by consuming the same resolution logic and then requiring `account.role == EMPLOYEE`.

- [ ] **Step 4: Generalize WeChat activation, login, me, and logout endpoints**

Change `/api/auth/wechat/activate` to call `activate_account()`, `/api/auth/wechat/login` to call `login_bound_wechat()`, and `/api/auth/me` plus `/api/auth/logout` to depend on `require_miniprogram_account()`. Keep `/api/mobile/**` employee-only. Return only:

```python
{"username": account.username, "display_name": account.display_name, "role": account.role}
```

Use the public error `微信账号未绑定或已停用` for failed bound-account login.

- [ ] **Step 5: Run focused and regression tests**

Run:

```bash
python -m unittest tests.test_wechat_auth tests.test_role_boundaries tests.test_pc_auth -v
```

Expected: all tests pass; employee business routes remain employee-only.

- [ ] **Step 6: Commit the identity API boundary**

```bash
git add app/auth/dependencies.py app/auth/api.py tests/test_wechat_auth.py tests/test_role_boundaries.py tests/test_pc_auth.py
git commit -m "feat: support admin identities in mini program"
```

---

### Task 4: Database-backed QR login state machine and endpoints

**Files:**
- Create: `app/auth/pc_login_service.py`
- Create: `app/auth/pc_login_api.py`
- Modify: `app/main.py`
- Create: `tests/test_pc_wechat_login.py`

**Interfaces:**
- Consumes: `PcLoginRequest`, `create_session()`, `require_miniprogram_account()`, and `PC_ADMIN_ROLES`.
- Produces: `create_login_challenge(db, device_summary, source_ip, now=None) -> LoginChallenge`.
- Produces: `scan_login_request(db, request_token, account, now=None) -> dict[str, str]`.
- Produces: `decide_login_request(db, request_token, account, approved, now=None) -> str`.
- Produces: `poll_login_request(db, request_token, browser_secret, now=None) -> str`.
- Produces: `consume_login_request(db, request_token, browser_secret, now=None) -> tuple[Account, str]`.
- Produces endpoints under `/api/auth/pc-login`.

- [ ] **Step 1: Write failing lifecycle, expiry, replay, and role tests**

Create `tests/test_pc_wechat_login.py` with fixed time and secrets. Cover this exact happy path:

```python
created = client.post(
    "/api/auth/pc-login/requests",
    json={"device_summary": "Chrome · macOS"},
)
request_token = created.json()["request_token"]
browser_secret = created.json()["browser_secret"]

summary = client.post(
    "/api/auth/pc-login/scan",
    headers=admin_mobile_headers,
    json={"request_token": request_token},
)
approved = client.post(
    "/api/auth/pc-login/decision",
    headers=admin_mobile_headers,
    json={"request_token": request_token, "approved": True},
)
consumed = client.post(
    "/api/auth/pc-login/consume",
    json={"request_token": request_token, "browser_secret": browser_secret},
    follow_redirects=False,
)
self.assertEqual(consumed.status_code, 200)
self.assertIn("tns_session=", consumed.headers["set-cookie"])
```

Also test wrong browser secret, employee approval (`403`), inactive account, deny, two-minute expiry, duplicate approval, duplicate consume, QR token absent from database plaintext, browser secret absent from QR payload, and `scanned`/`approved` audit records without secrets.

- [ ] **Step 2: Run the test and verify missing router/service failure**

Run:

```bash
python -m unittest tests.test_pc_wechat_login -v
```

Expected: import failure for `pc_login_service` or `pc_login_api`.

- [ ] **Step 3: Implement cryptographic request creation and lookup**

Define:

```python
@dataclass(frozen=True)
class LoginChallenge:
    request_token: str
    browser_secret: str
    expires_at: datetime


def create_login_challenge(db, device_summary, source_ip, now=None):
    current_time = _clock(now)
    request_token = new_session_token()
    browser_secret = new_session_token()
    row = PcLoginRequest(
        request_token_hash=hash_secret(request_token, _required_pepper()),
        browser_secret_hash=hash_secret(browser_secret, _required_pepper()),
        status="pending",
        device_summary=(device_summary or "未知浏览器")[:200],
        source_ip=(source_ip or "")[:64] or None,
        expires_at=current_time + timedelta(
            seconds=settings.pc_login_request_seconds
        ),
    )
    db.add(row)
    db.commit()
    return LoginChallenge(request_token, browser_secret, row.expires_at)
```

Hash both values with `hash_secret(request_token, _required_pepper())` and `hash_secret(browser_secret, _required_pepper())`. All lookup functions must compare hashes and evaluate `expires_at` on the server clock.

- [ ] **Step 4: Implement atomic state transitions**

Use conditional SQLAlchemy updates and require `rowcount == 1`:

```python
db.query(PcLoginRequest).filter(
    PcLoginRequest.id == login_request.id,
    PcLoginRequest.status.in_(("pending", "scanned")),
    PcLoginRequest.expires_at > current_time,
).update({
    "status": "approved",
    "approved_account_id": account.id,
    "approved_at": current_time,
})
```

Only `PC_ADMIN_ROLES` can approve. Deny moves `pending`/`scanned` to `denied`. Consume moves only `approved` to `consumed`, verifies the separate browser secret, rechecks the approving account is active and still a PC admin, then creates exactly one `pc` session.

- [ ] **Step 5: Implement API schemas and secure Cookie response**

Create endpoints:

```text
POST /api/auth/pc-login/requests
POST /api/auth/pc-login/status
POST /api/auth/pc-login/scan
POST /api/auth/pc-login/decision
POST /api/auth/pc-login/consume
```

The create response contains request token, browser secret, `qr_payload` in the exact format `tns-inventory-login:v1:<request_token>`, an in-memory PNG `qr_image_data_url`, and expiry. Generate the PNG with `qrcode.make()` and `io.BytesIO`; do not write login QR files to disk. Scan/decision require `require_miniprogram_account`. Consume sets `settings.auth_cookie_name` with `HttpOnly`, `Secure`, `SameSite=Strict`, path `/`, and the configured PC lifetime; its JSON contains only `{"ok": true, "redirect_to": "/admin"}`.

- [ ] **Step 6: Register the router and run tests**

Register `pc_login_api.router` before protected business routers in `app/main.py`.

Run:

```bash
python -m unittest tests.test_pc_wechat_login tests.test_wechat_auth tests.test_pc_auth -v
```

Expected: all tests pass, and replays produce `409` or an expired/consumed state without a second session.

- [ ] **Step 7: Commit the QR protocol**

```bash
git add app/auth/pc_login_service.py app/auth/pc_login_api.py app/main.py tests/test_pc_wechat_login.py
git commit -m "feat: add single-use WeChat PC login protocol"
```

---

### Task 5: PC role dependencies and hierarchical account management

**Files:**
- Create: `app/auth/account_pages.py`
- Modify: `app/auth/dependencies.py`
- Modify: `app/auth/pages.py`
- Modify: `app/main.py`
- Modify: `app/admin_pages.py`
- Create: `tests/test_owner_admin.py`
- Modify: `tests/test_employee_admin.py`
- Modify: `tests/test_role_boundaries.py`
- Modify: `tests/test_admin_navigation_and_drawing_confirm.py`

**Interfaces:**
- Consumes: `can_manage_role()`, `create_managed_account()`, `regenerate_activation()`, `unbind_wechat()`.
- Produces: `require_pc_admin_account()` accepting only a PC Cookie for `superadmin` or `owner`.
- Produces: `/admin/accounts` and role-checked POST endpoints for owners/employees.
- Preserves: `/admin/employees` as a redirect or compatibility view without weakening authorization.

- [ ] **Step 1: Write failing role hierarchy page tests**

Create `tests/test_owner_admin.py` with two authenticated PC clients. Assert:

```python
self.assertEqual(superadmin_client.post(
    "/admin/accounts/owners",
    data={"username": "boss1", "display_name": "老板一"},
).status_code, 200)

self.assertEqual(owner_client.post(
    "/admin/accounts/owners",
    data={"username": "boss2", "display_name": "老板二"},
).status_code, 403)

self.assertEqual(owner_client.post(
    "/admin/accounts/employees",
    data={"username": "TNS009", "display_name": "李四"},
).status_code, 200)
```

Also assert neither an owner nor the superadmin HTTP UI can disable/unbind the superadmin; an owner cannot mutate another owner; a superadmin can disable/unbind an owner; all codes are shown once and excluded from audit data.

- [ ] **Step 2: Run focused tests and verify missing page/dependency behavior**

Run:

```bash
python -m unittest tests.test_owner_admin tests.test_employee_admin tests.test_role_boundaries -v
```

Expected: missing router/dependency or incorrect role authorization failures.

- [ ] **Step 3: Add the PC administrator dependency**

Implement `require_pc_admin_account()` so only `client_type == "pc"` and roles in `PC_ADMIN_ROLES` pass. Keep `require_owner_account` as a temporary alias during the refactor, then update `app/main.py` and direct test imports to the clearer name.

- [ ] **Step 4: Implement a target-role checked account management router**

Create helpers with exact behavior:

```python
def managed_account(db: Session, actor: Account, account_id: int) -> Account:
    target = db.get(Account, account_id)
    if not target:
        raise HTTPException(404, "账号不存在")
    if not can_manage_role(actor.role, target.role):
        raise HTTPException(403, "无权管理该账号")
    return target
```

Render owners only for the superadmin; render employees for both PC admin roles. Create separate POST paths for `/owners` and `/employees`, but route both through `create_managed_account()`. Apply `managed_account()` before disable, enable, unbind, regenerate, or revoke actions. Never render OpenID, hashes, session IDs, or expired activation codes.

- [ ] **Step 5: Move employee management and add navigation**

Remove duplicated employee management handlers from `app/auth/pages.py`, include `account_pages.router` in `app/main.py`, and make `/admin/employees` redirect to `/admin/accounts`. Add one “账号管理” navigation entry in `app/admin_pages.py`; the page itself adapts actions to the current role.

- [ ] **Step 6: Run authorization and navigation regression tests**

Run:

```bash
python -m unittest tests.test_owner_admin tests.test_employee_admin tests.test_role_boundaries tests.test_admin_navigation_and_drawing_confirm -v
```

Expected: all tests pass; direct forged POSTs receive `403` even if the button is hidden.

- [ ] **Step 7: Commit hierarchical account management**

```bash
git add app/auth/account_pages.py app/auth/dependencies.py app/auth/pages.py app/main.py app/admin_pages.py tests/test_owner_admin.py tests/test_employee_admin.py tests/test_role_boundaries.py tests/test_admin_navigation_and_drawing_confirm.py
git commit -m "feat: add hierarchical administrator management"
```

---

### Task 6: PC QR login page, polling, legacy-login gate, and request throttling

**Files:**
- Modify: `app/auth/pages.py`
- Modify: `app/auth/pc_login_api.py`
- Modify: `deploy/nginx-personal-inventory.conf`
- Modify: `tests/test_pc_wechat_login.py`
- Modify: `tests/test_pc_auth.py`
- Modify: `tests/test_linux_deploy_assets.py`

**Interfaces:**
- Consumes: Task 4 create/status/consume endpoints.
- Produces: `/auth/login` HTML with a system-generated QR and browser-only secret held in JavaScript memory.
- Produces: feature-gated `/auth/legacy-login` GET/POST emergency form.

- [ ] **Step 1: Write failing PC page and feature-gate tests**

Add assertions:

```python
page = client.get("/auth/login")
self.assertIn("使用小程序扫码登录", page.text)
self.assertIn("/api/auth/pc-login/requests", page.text)
self.assertNotIn('name="password"', page.text)

with patch("app.auth.pages.settings.legacy_password_login_enabled", False):
    self.assertEqual(client.get("/auth/legacy-login").status_code, 404)
    self.assertEqual(client.post("/auth/legacy-login", data={
        "username": "owner",
        "password": "strong-password-123",
        "totp_code": "000000",
    }).status_code, 404)
```

Test that enabling the flag preserves the existing password/TOTP form and successful emergency login.

- [ ] **Step 2: Run focused tests and verify the old form fails new expectations**

Run:

```bash
python -m unittest tests.test_pc_wechat_login tests.test_pc_auth tests.test_linux_deploy_assets -v
```

Expected: `/auth/login` still contains password fields and Nginx has no auth throttling.

- [ ] **Step 3: Render QR in memory and implement browser polling**

Have the page JavaScript create a request, display the server-returned QR PNG data URL or SVG-safe image generated from `qr_payload`, retain `browser_secret` only in page memory, poll status every 1.5 seconds, and call consume after `approved`. On success redirect to `/admin`; on deny/expiry show a clear message and a “刷新二维码” button. Do not put `browser_secret` in the URL, DOM text, localStorage, Cookie, or QR.

- [ ] **Step 4: Move password/TOTP login behind the emergency flag**

Keep `/auth/login` QR-only. Move the existing form and POST behavior to `/auth/legacy-login`. At the first line of both legacy handlers:

```python
if not settings.legacy_password_login_enabled:
    raise HTTPException(status_code=404, detail="页面不存在")
```

The QR page must not link to this emergency route when production disables it.

- [ ] **Step 5: Add Nginx rate limiting for public authentication endpoints**

Add an `http`-context zone at the top of the included config and an exact regex location that duplicates the secure proxy headers:

```nginx
limit_req_zone $binary_remote_addr zone=tenaishi_auth:10m rate=20r/m;

location ~ ^/(api/auth/(wechat/(activate|login)|pc-login/(requests|scan|decision|consume))|auth/(login|legacy-login))$ {
    limit_req zone=tenaishi_auth burst=10 nodelay;
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Status polling stays outside the strict regex so a legitimate browser polling every 1.5 seconds is not blocked.

- [ ] **Step 6: Run focused tests and inspect secret handling**

Run:

```bash
python -m unittest tests.test_pc_wechat_login tests.test_pc_auth tests.test_linux_deploy_assets -v
rg -n "browser_secret|request_token" app/auth/pages.py app/auth/pc_login_api.py
```

Expected: all tests pass; inspection shows browser secret used only in create/consume JavaScript payloads and never interpolated into logs or QR values.

- [ ] **Step 7: Commit the PC login experience**

```bash
git add app/auth/pages.py app/auth/pc_login_api.py deploy/nginx-personal-inventory.conf tests/test_pc_wechat_login.py tests/test_pc_auth.py tests/test_linux_deploy_assets.py
git commit -m "feat: add QR-first PC login page"
```

---

### Task 7: Mini-program activation, role routing, scan, and explicit confirmation

**Files:**
- Create: `miniprogram/utils/pc-login.js`
- Modify: `miniprogram/utils/auth.js`
- Modify: `miniprogram/app.js`
- Modify: `miniprogram/app.json`
- Modify: `miniprogram/pages/auth/login.js`
- Modify: `miniprogram/pages/auth/login.wxml`
- Modify: `miniprogram/pages/auth/login.wxss`
- Create: `miniprogram/pages/account/home.js`
- Create: `miniprogram/pages/account/home.json`
- Create: `miniprogram/pages/account/home.wxml`
- Create: `miniprogram/pages/account/home.wxss`
- Create: `miniprogram/pages/auth/pc-login-confirm.js`
- Create: `miniprogram/pages/auth/pc-login-confirm.json`
- Create: `miniprogram/pages/auth/pc-login-confirm.wxml`
- Create: `miniprogram/pages/auth/pc-login-confirm.wxss`
- Modify: `tests/miniprogram_auth.test.js`
- Create: `tests/miniprogram_pc_login.test.js`
- Modify: `tests/test_miniprogram_foundation.py`

**Interfaces:**
- Consumes: safe auth account response and Task 4 scan/decision API.
- Produces: `parseQrPayload(value: string) -> string` returning only a validated request token.
- Produces: `scanPcLogin(wxApi, request)`, `readPcLogin(request, token)`, and `decidePcLogin(request, token, approved)`.
- Produces role routing: employees go to `/pages/plan/home`; `owner` and `superadmin` go to `/pages/account/home`.

- [ ] **Step 1: Write failing mini-program utility tests**

Create `tests/miniprogram_pc_login.test.js`:

```javascript
test('valid QR payload returns only request token', () => {
  assert.equal(
    pcLogin.parseQrPayload('tns-inventory-login:v1:abc_DEF-123'),
    'abc_DEF-123'
  )
})

test('http URLs and malformed payloads are rejected', () => {
  assert.throws(() => pcLogin.parseQrPayload('https://evil.example/login'))
  assert.throws(() => pcLogin.parseQrPayload('tns-inventory-login:v2:abc'))
})
```

Add tests that approve/deny use authenticated requests, scan failure makes no decision call, and role routing stores only `{username, display_name, role}` plus the existing session token.

- [ ] **Step 2: Run Node and foundation tests and verify missing modules/pages**

Run:

```bash
node --test tests/miniprogram_auth.test.js tests/miniprogram_pc_login.test.js
python -m unittest tests.test_miniprogram_foundation -v
```

Expected: missing `pc-login.js` and page-registration failures.

- [ ] **Step 3: Implement strict QR parsing and API helpers**

`parseQrPayload()` must accept only `tns-inventory-login:v1:` followed by the URL-safe token character set and reject all URLs, whitespace, empty tokens, and extra separators. `scanPcLogin()` wraps `wx.scanCode({scanType: ['qrCode']})`, parses the result, then calls `/api/auth/pc-login/scan`.

- [ ] **Step 4: Persist safe account metadata and route by role**

Extend `miniprogram/utils/auth.js` with a separate `tns_auth_account` storage key. Save only the safe account response. Add:

```javascript
function homeForRole(role) {
  return role === 'employee' ? '/pages/plan/home' : '/pages/account/home'
}
```

Clear both session and account keys on unauthorized responses/logout. Update the login page to use “账号” rather than “工号” and accept the existing 8-digit activation format for all roles.

- [ ] **Step 5: Build administrator mini-program home**

The account home displays name and role label (`主管理员` or `老板`), a primary “扫码登录电脑后台” button, and logout. It must not call employee inventory APIs. After scan succeeds, navigate to the confirmation page with only the request token in page parameters.

- [ ] **Step 6: Build explicit confirmation page**

On load, call scan-summary and render only system name, verified domain, China-time request time, and bounded device summary. Provide two buttons. The confirm handler sends `approved: true`; deny sends `approved: false`; both lock during submission and navigate back after a clear result. No auto-approval occurs in `onLoad` or `onShow`.

- [ ] **Step 7: Register pages and run tests**

Add both new pages to `app.json` outside the tab bar. Run:

```bash
node --test tests/miniprogram_auth.test.js tests/miniprogram_pc_login.test.js tests/miniprogram_connection.test.js
python -m unittest tests.test_miniprogram_foundation -v
```

Expected: all tests pass; `app.json` remains valid JSON and the employee tab bar is unchanged.

- [ ] **Step 8: Commit the mini-program login experience**

```bash
git add miniprogram tests/miniprogram_auth.test.js tests/miniprogram_pc_login.test.js tests/test_miniprogram_foundation.py
git commit -m "feat: confirm PC login from mini program"
```

---

### Task 8: Full regression, release compilation, and security review fixes

**Files:**
- Modify only files required by failures found in this task.
- Verify: `dist/miniprogram-release/` generated output; do not commit generated output unless repository policy already tracks it.

**Interfaces:**
- Consumes: completed backend, PC, and mini-program implementation.
- Produces: a test-clean and WeChat-compiler-clean release candidate.

- [ ] **Step 1: Run the complete Python suite**

Run:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: all Python tests pass.

- [ ] **Step 2: Run the complete Node suite**

Run:

```bash
node --test tests/*.test.js
```

Expected: all Node tests pass.

- [ ] **Step 3: Build the production mini-program release**

Run:

```bash
python scripts/build_miniprogram_release.py
```

Expected: `dist/miniprogram-release` is rebuilt with the fixed production API base URL and without development connection controls or secret configuration.

- [ ] **Step 4: Compile and preview in WeChat Developer Tools**

Open `dist/miniprogram-release` in the existing WeChat Developer Tools project, compile every new page, and verify:

```text
activation → admin home → scan → request summary → deny
activation → admin home → scan → request summary → approve → PC redirect
employee login → plan tab → existing inventory operation
```

Expected: no WXML/WXSS/JavaScript compile errors and no route-not-found error.

- [ ] **Step 5: Run explicit security searches**

Run:

```bash
rg -n "wechat_openid|activation_code_hash|browser_secret_hash|token_hash" app miniprogram
rg -n "print\(|console\.log|logger\.|logging\." app/auth scripts/manage_superadmin.py miniprogram/pages/auth miniprogram/pages/account
```

Expected: secrets appear only in model/service handling, never in rendered HTML, API safe-account responses, logs, or mini-program console output.

- [ ] **Step 6: Apply minimal fixes for any verified failures, then rerun affected and full suites**

For every failure, first add or tighten a regression assertion in its owning test file, confirm the assertion fails, apply the smallest implementation correction, then rerun Steps 1–3. Expected: all suites and release build pass.

- [ ] **Step 7: Commit verified integration fixes**

```bash
git add app miniprogram scripts tests deploy requirements.txt
git commit -m "test: verify WeChat QR login integration"
```

If there are no changes after verification, record the passing command outputs in the task handoff and do not create an empty commit.

---

### Task 9: Production-compatible deployment, superadmin cutover, and recovery documentation

**Files:**
- Modify: `deploy/README.md`
- Modify: `deploy/smoke-test.sh`
- Modify: `tests/test_linux_deploy_assets.py`
- Modify: `docs/superpowers/plans/2026-08-18-inventory-production-cutover.md`

**Interfaces:**
- Consumes: `scripts/manage_superadmin.py`, legacy-login feature flag, production backup/update scripts, and mini-program release candidate.
- Produces: an exact two-phase rollout and recovery runbook; production state changes occur only after local implementation verification and explicit deployment continuation.

- [ ] **Step 1: Write failing deployment-document assertions**

Extend `tests/test_linux_deploy_assets.py` to require these literal commands or settings in `deploy/README.md`:

```text
python scripts/manage_superadmin.py bootstrap
python scripts/manage_superadmin.py reset-wechat
LEGACY_PASSWORD_LOGIN_ENABLED=true
LEGACY_PASSWORD_LOGIN_ENABLED=false
scripts/backup.sh
PRAGMA integrity_check
```

Require `deploy/smoke-test.sh` to check `/auth/login` contains `使用小程序扫码登录` without printing a QR token or browser secret.

- [ ] **Step 2: Run the deployment tests and verify documentation gaps**

Run:

```bash
python -m unittest tests.test_linux_deploy_assets -v
```

Expected: failures for missing bootstrap/cutover/recovery instructions.

- [ ] **Step 3: Document Phase A compatible deployment**

Write this ordered procedure in `deploy/README.md`:

```text
1. Create and verify a production backup.
2. Deploy code with LEGACY_PASSWORD_LOGIN_ENABLED=true.
3. Run database integrity and foreign-key checks.
4. Verify health, current owner emergency login, employee mini login, and QR page.
5. Run manage_superadmin.py bootstrap in the ECS terminal and deliver the one-time code only to the real superadmin.
6. Bind the real superadmin WeChat and complete one PC QR login.
7. Create and bind a test/real owner from the superadmin account.
```

- [ ] **Step 4: Document Phase B security cutover**

Write this ordered procedure:

```text
1. Create a second verified backup immediately before changing accounts.
2. Disable the temporary owner account and increment its session_version.
3. Remove/rotate its legacy TOTP secret from production configuration.
4. Set LEGACY_PASSWORD_LOGIN_ENABLED=false.
5. Restart the service and verify /auth/legacy-login returns 404.
6. Verify superadmin and owner QR login, owner employee management, employee inventory access, HTTPS health, and closed public port 8000.
```

Do not prescribe deletion of business rows or uploads.

- [ ] **Step 5: Document recovery and rollback**

Recovery must first run `scripts/backup.sh`, then `python scripts/manage_superadmin.py reset-wechat --username <exact-superadmin>`, and deliver the new 30-minute activation code to the real superadmin. Rollback may temporarily restore the previously deployed code and `LEGACY_PASSWORD_LOGIN_ENABLED=true`, but must not restore the retired owner credential unless the verified backup and incident decision explicitly require it.

- [ ] **Step 6: Run deployment and full tests**

Run:

```bash
python -m unittest tests.test_linux_deploy_assets -v
python -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/*.test.js
```

Expected: all tests pass.

- [ ] **Step 7: Commit the rollout runbook**

```bash
git add deploy/README.md deploy/smoke-test.sh tests/test_linux_deploy_assets.py docs/superpowers/plans/2026-08-18-inventory-production-cutover.md
git commit -m "docs: add WeChat administrator cutover runbook"
```

---

## Final Acceptance Gate

Do not claim production completion until all of the following evidence exists:

- Complete Python and Node suites pass on the final commit.
- The production mini-program release compiles in WeChat Developer Tools.
- A production-data copy passes repeated migration, `PRAGMA integrity_check`, and `PRAGMA foreign_key_check`.
- Production backup succeeds before deployment and before credential cutover.
- Real superadmin binds WeChat and completes PC QR login.
- Real owner binds a separate WeChat, completes PC QR login, and can manage an employee.
- Owner receives `403` for owner/superadmin management attempts.
- Employee receives `403` for QR approval and PC administration.
- Duplicate, expired, denied, and consumed QR requests cannot create another PC session.
- The temporary `owner` credential and its sessions are disabled only after the new path is verified.
- `/auth/legacy-login` returns `404` after cutover, HTTPS health passes, and public port `8000` remains closed.
- The new mini-program build is uploaded for WeChat review; formal publication is reported separately because review timing is external.
