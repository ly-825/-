# Authentication and Access Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add owner-only PC authentication, employee WeChat binding, revocable sessions, backend-enforced role boundaries, and verified actor audit data without changing the current employee mini-program feature set.

**Architecture:** Store accounts and hashed sessions in SQLite. PC requests authenticate with a secure cookie plus owner password and TOTP; mini-program requests authenticate with a bearer session created after WeChat `code2session` and one-time employee activation. FastAPI router dependencies enforce owner versus employee access, while audit logging reads the verified actor from request context.

**Tech Stack:** FastAPI 0.115.6, SQLAlchemy 2.0.36, SQLite, argon2-cffi, pyotp, requests, WeChat Mini Program JavaScript, unittest, Node test runner.

## Global Constraints

- Work only in the current `backend` repository; do not use similarly named sibling copies.
- Preserve all pre-existing uncommitted changes, especially `app/mobile_connection_pages.py`, `app/services/mobile_connection.py`, `miniprogram/pages/connection/*`, `miniprogram/utils/connection.js`, and `deploy/`.
- Owner can use the PC backend remotely; employees cannot access PC or drawing-management interfaces.
- All employees have the same permissions and retain every feature currently registered in `miniprogram/app.json`.
- Never store passwords, activation codes, session tokens, WeChat `AppSecret`, or TOTP secret in Git or logs.
- Keep existing `client_request_id` replay protection unchanged.
- Use TDD and commit after every task.

---

## File Map

- `app/models.py`: account and session persistence models.
- `app/config.py`: authentication and WeChat settings.
- `app/database.py`: SQLite WAL and busy-timeout configuration.
- `app/auth/security.py`: password, activation-code, session-token, and TOTP primitives.
- `app/auth/service.py`: account lifecycle and session use cases.
- `app/auth/dependencies.py`: FastAPI owner and employee authorization dependencies.
- `app/auth/context.py`: verified actor context used by audit logging.
- `app/auth/pages.py`: owner login/logout and employee-management HTML routes.
- `app/auth/api.py`: mini-program activation, login, logout, and current-account API.
- `app/services/wechat_auth.py`: WeChat `code2session` boundary.
- `app/main.py`: router wiring and protected route groups.
- `app/services/operation_log.py`: verified actor attribution.
- `app/routers/mobile.py`: remove employee access to sensitive drawing-management endpoints and provide safe product options.
- `miniprogram/pages/auth/login.*`: employee activation/login screen.
- `miniprogram/utils/auth.js`: WeChat login and session storage.
- `miniprogram/utils/api.js`: bearer header and `401` handling.
- `miniprogram/app.js`, `miniprogram/app.json`: authentication bootstrap and route registration.
- `scripts/create_owner.py`: secure one-time owner bootstrap.

### Task 1: Account Models and SQLite Runtime Settings

**Files:**
- Modify: `app/models.py`
- Modify: `app/config.py`
- Modify: `app/database.py`
- Modify: `app/schema_migrations.py`
- Create: `tests/test_auth_models.py`

**Interfaces:**
- Produces: `Account`, `AuthSession`, and SQLite connections configured for WAL and a 5000 ms busy timeout.
- Produces settings: `auth_pepper`, `owner_totp_secret`, `wechat_app_id`, `wechat_app_secret`, `pc_session_hours`, `mobile_session_days`.

- [ ] **Step 1: Write failing model and SQLite tests**

```python
def test_account_username_and_wechat_openid_are_unique():
    assert {c.name for c in Account.__table__.columns} >= {
        "username", "display_name", "role", "password_hash",
        "wechat_openid", "activation_code_hash", "activation_expires_at",
        "is_active", "session_version",
    }

def test_sqlite_uses_wal_and_busy_timeout(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'auth.db'}")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/bin/python -m unittest tests.test_auth_models -v`

Expected: import failure for `Account` or `build_engine`.

- [ ] **Step 3: Add focused persistence models**

Add models with these exact fields:

```python
class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    wechat_openid: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True, index=True)
    activation_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    activation_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now)

class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    session_version: Mapped[int] = mapped_column(Integer)
    client_type: Mapped[str] = mapped_column(String(20), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=china_now)
```

Validate `role` in services as exactly `owner` or `employee`; validate `client_type` as exactly `pc` or `miniprogram`.

- [ ] **Step 4: Configure SQLite through one engine factory**

Refactor `app/database.py` so `build_engine(database_url: str) -> Engine` registers a connect event that executes:

```python
cursor.execute("PRAGMA foreign_keys=ON")
cursor.execute("PRAGMA busy_timeout=5000")
cursor.execute("PRAGMA journal_mode=WAL")
```

Retain `check_same_thread=False`. Add account/session timestamps to `TIMESTAMP_COLUMNS`, and make `ensure_runtime_schema` idempotently create their indexes for an existing database.

- [ ] **Step 5: Run focused and schema regression tests**

Run: `.venv/bin/python -m unittest tests.test_auth_models tests.test_mobile_idempotency -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/config.py app/database.py app/schema_migrations.py tests/test_auth_models.py
git commit -m "feat: add account and session persistence"
```

### Task 2: Security Primitives and Owner Bootstrap

**Files:**
- Modify: `requirements.txt`
- Create: `app/auth/__init__.py`
- Create: `app/auth/security.py`
- Create: `app/auth/service.py`
- Create: `scripts/create_owner.py`
- Create: `tests/test_auth_service.py`

**Interfaces:**
- Produces: `hash_password`, `verify_password`, `hash_secret`, `new_session_token`, `verify_totp`.
- Produces: `create_owner`, `authenticate_owner`, `create_employee`, `activate_employee`, `create_session`, `resolve_session`, `disable_account`, `unbind_wechat`.

- [ ] **Step 1: Add failing service tests**

Cover these exact behaviors. The first test must contain this complete assertion pattern:

```python
def test_password_hash_is_not_plaintext_and_verifies(self) -> None:
    encoded = hash_password("correct horse battery staple")
    self.assertNotEqual(encoded, "correct horse battery staple")
    self.assertTrue(verify_password(encoded, "correct horse battery staple"))
    self.assertFalse(verify_password(encoded, "wrong password"))
```

Use a temporary SQLite database and a fixed clock injected into service functions. Additional tests must assert that only an activation-code hash is stored, expired and reused codes are rejected, only a session-token hash is stored, incrementing `session_version` invalidates the old token, and owner login rejects either an incorrect password or incorrect TOTP.

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m unittest tests.test_auth_service -v`

Expected: import failures for `app.auth.security` and `app.auth.service`.

- [ ] **Step 3: Add dependencies and primitives**

Append exact dependencies:

```text
argon2-cffi==25.1.0
pyotp==2.9.0
httpx==0.28.1
```

Implement:

```python
def hash_secret(raw: str, pepper: str) -> str:
    return hashlib.sha256(f"{pepper}:{raw}".encode()).hexdigest()

def new_session_token() -> str:
    return secrets.token_urlsafe(32)

def verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)
```

Use `argon2.PasswordHasher` for passwords and `hmac.compare_digest` for secret hashes.

- [ ] **Step 4: Implement service use cases**

Implement these exact interfaces: `create_owner(db: Session, username: str, display_name: str, password: str) -> Account`, `authenticate_owner(db: Session, username: str, password: str, totp_code: str, now: datetime | None = None) -> tuple[Account, str]`, `create_employee(db: Session, username: str, display_name: str, now: datetime | None = None) -> tuple[Account, str]`, `activate_employee(db: Session, username: str, activation_code: str, wechat_openid: str, now: datetime | None = None) -> tuple[Account, str]`, `create_session(db: Session, account: Account, client_type: str, expires_at: datetime) -> str`, `resolve_session(db: Session, raw_token: str, client_type: str, now: datetime | None = None) -> Account | None`, `disable_account(db: Session, account: Account) -> None`, and `unbind_wechat(db: Session, account: Account) -> str`.

Activation codes are eight random decimal digits, expire after 24 hours, and are never returned again after the creation/regeneration response. Disabling or unbinding increments `session_version`.

- [ ] **Step 5: Add owner bootstrap command**

`scripts/create_owner.py` must prompt with `getpass`, reject weak passwords shorter than 12 characters, create the owner through `create_owner`, and print the TOTP provisioning URI derived from `OWNER_TOTP_SECRET`. It must refuse to overwrite an existing owner.

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m unittest tests.test_auth_service -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt app/auth scripts/create_owner.py tests/test_auth_service.py
git commit -m "feat: add secure account lifecycle"
```

### Task 3: PC Login and Owner Route Protection

**Files:**
- Create: `app/auth/context.py`
- Create: `app/auth/dependencies.py`
- Create: `app/auth/pages.py`
- Modify: `app/main.py`
- Modify: `app/admin_pages.py`
- Create: `tests/test_pc_auth.py`

**Interfaces:**
- Produces dependencies `require_owner_account(request, db) -> Account` and `require_mobile_account(request, db) -> Account`.
- Produces routes `GET/POST /auth/login` and `POST /auth/logout`.

- [ ] **Step 1: Write failing HTTP authorization tests**

Use FastAPI `TestClient` with a temporary database override. A representative anonymous-access test is:

```python
def test_admin_redirects_anonymous_user_to_login(self) -> None:
    response = self.client.get("/admin", follow_redirects=False)
    self.assertEqual(response.status_code, 303)
    self.assertEqual(response.headers["location"], "/auth/login")
```

Additional tests must assert that `/health` is public, owner login sets a `Secure; HttpOnly; SameSite=Strict` cookie, an employee bearer token cannot open `/admin`, logout revokes the database session, and wrong password versus wrong TOTP produce identical response text.

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m unittest tests.test_pc_auth -v`

Expected: anonymous `/admin` currently returns `200`, so the protection test fails.

- [ ] **Step 3: Implement dependencies and verified actor context**

Authentication order:

```python
def raw_request_token(request: Request, cookie_name: str) -> tuple[str | None, str | None]:
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return authorization[7:].strip(), "miniprogram"
    cookie = request.cookies.get(cookie_name)
    return (cookie, "pc") if cookie else (None, None)
```

`require_owner_account` accepts only active `owner` accounts with `pc` sessions. `require_mobile_account` accepts active `employee` accounts with `miniprogram` sessions. Set and reset a `ContextVar[Account | None]` around each request so logs can identify the verified actor.

- [ ] **Step 4: Implement PC login pages**

Login accepts `username`, `password`, and six-digit `totp_code`. On success set the configured cookie with `httponly=True`, `secure=True`, `samesite="strict"`, and `max_age=pc_session_hours * 3600`. On failure show the same message, `账号或验证信息不正确`, without identifying which field failed.

Add logout to the shared PC navigation. Do not expose the session token in HTML or query parameters.

- [ ] **Step 5: Protect route groups in `app/main.py`**

Register public auth routes first. Include these routers with `dependencies=[Depends(require_owner_account)]`:

```text
drawings.router
inventory.router
admin_pages.router
paper_admin_pages.router
mobile_connection_pages.router
```

Keep `/health` and `/auth/login` public. Redirect `/` to `/admin` so the normal owner dependency decides whether login is required. Disable `/docs`, `/redoc`, and `/openapi.json` when a new `production=True` setting is active.

- [ ] **Step 6: Run tests and existing admin smoke tests**

Run: `.venv/bin/python -m unittest tests.test_pc_auth tests.test_admin_navigation_and_drawing_confirm tests.test_mobile_connection -v`

Expected: PASS after tests are updated to provide an owner dependency override where they intentionally exercise protected HTTP routes.

- [ ] **Step 7: Commit**

```bash
git add app/auth/context.py app/auth/dependencies.py app/auth/pages.py app/main.py app/admin_pages.py tests/test_pc_auth.py tests/test_admin_navigation_and_drawing_confirm.py tests/test_mobile_connection.py
git commit -m "feat: protect PC backend with owner login"
```

### Task 4: PC Employee Management

**Files:**
- Modify: `app/auth/pages.py`
- Modify: `app/admin_pages.py`
- Modify: `app/services/operation_log.py`
- Create: `tests/test_employee_admin.py`

**Interfaces:**
- Produces owner-only routes under `/admin/employees`.
- Consumes account lifecycle functions from Task 2.

- [ ] **Step 1: Write failing employee-management tests**

Test create, list, disable, enable, unbind, and regenerate activation code. Assert that activation codes appear only in the immediate POST response and never in list HTML or operation logs.

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m unittest tests.test_employee_admin -v`

Expected: `404` for `/admin/employees`.

- [ ] **Step 3: Implement employee routes**

Create exact endpoints:

```text
GET  /admin/employees
POST /admin/employees
POST /admin/employees/{account_id}/disable
POST /admin/employees/{account_id}/enable
POST /admin/employees/{account_id}/unbind-wechat
POST /admin/employees/{account_id}/regenerate-activation
```

The page displays display name, username, enabled state, WeChat binding state, and created time. It never displays OpenID. Destructive actions require a POST form and confirmation.

- [ ] **Step 4: Attribute audit logs to the verified owner**

Update `record_operation_log` so an authenticated request actor overrides a user-supplied operator name for audit attribution. Record account ID and role in `after_data` for employee lifecycle actions, but never record credentials or tokens.

- [ ] **Step 5: Add navigation and run tests**

Add `员工管理` and `退出登录` to the PC navigation. Run:

`.venv/bin/python -m unittest tests.test_employee_admin tests.test_pc_auth -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/auth/pages.py app/admin_pages.py app/services/operation_log.py tests/test_employee_admin.py
git commit -m "feat: manage employee mini-program access"
```

### Task 5: WeChat Activation and Mini-Program Sessions

**Files:**
- Create: `app/services/wechat_auth.py`
- Create: `app/auth/api.py`
- Modify: `app/main.py`
- Modify: `miniprogram/app.js`
- Modify: `miniprogram/app.json`
- Modify: `miniprogram/utils/api.js`
- Create: `miniprogram/utils/auth.js`
- Create: `miniprogram/pages/auth/login.js`
- Create: `miniprogram/pages/auth/login.json`
- Create: `miniprogram/pages/auth/login.wxml`
- Create: `miniprogram/pages/auth/login.wxss`
- Create: `tests/test_wechat_auth.py`
- Create: `tests/miniprogram_auth.test.js`

**Interfaces:**
- Produces `exchange_code_for_openid(code: str) -> str`.
- Produces `/api/auth/wechat/activate`, `/api/auth/wechat/login`, `/api/auth/logout`, and `/api/auth/me`.
- Produces mini-program storage key `tns_auth_session`.

- [ ] **Step 1: Write failing backend and JavaScript tests**

Backend tests mock the WeChat HTTP response and cover valid activation, already-bound login, reused code, disabled employee, and the rule that upstream `session_key` is never returned or logged.

JavaScript tests use a fake `wx` storage object. The authorization test must be:

```javascript
test('authorizationHeader returns Bearer token from storage', () => {
  const wx = { getStorageSync: (key) => key === 'tns_auth_session' ? 'session-123' : '' }
  assert.deepEqual(auth.authorizationHeader(wx), { Authorization: 'Bearer session-123' })
})
```

Additional tests must assert that a `401` clears `tns_auth_session` and calls `wx.reLaunch` with `/pages/auth/login`, and that activation sends the `wx.login` code together with username and activation code.

- [ ] **Step 2: Run and confirm failure**

Run:

```bash
.venv/bin/python -m unittest tests.test_wechat_auth -v
node --test tests/miniprogram_auth.test.js
```

Expected: missing module failures.

- [ ] **Step 3: Implement the WeChat boundary**

Call `https://api.weixin.qq.com/sns/jscode2session` with `appid`, `secret`, `js_code`, and `grant_type=authorization_code`. Require a non-empty `openid`; map WeChat error codes to a generic `微信登录失败，请重试` response. Never return or log `session_key`.

- [ ] **Step 4: Implement authentication API**

Use request models:

```python
class EmployeeActivationIn(BaseModel):
    username: str
    activation_code: str
    wx_code: str

class WechatLoginIn(BaseModel):
    wx_code: str
```

Successful activation/login returns:

```json
{"token":"raw-session-token","account":{"display_name":"张三","role":"employee"}}
```

The raw token is returned only in this response. `/api/auth/me` returns no OpenID.

- [ ] **Step 5: Implement mini-program login bootstrap**

Register `pages/auth/login` as the first page. `auth.js` wraps `wx.login`, storage, activation, and normal login. `api.js` adds:

```javascript
header: {
  'content-type': 'application/json',
  ...auth.authorizationHeader(wx)
}
```

On `401`, clear the session and `wx.reLaunch({ url: '/pages/auth/login' })`. On successful launch, re-launch the first business tab.

- [ ] **Step 6: Run tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_wechat_auth -v
node --test tests/miniprogram_auth.test.js tests/miniprogram_connection.test.js tests/miniprogram_material_api.test.js
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/wechat_auth.py app/auth/api.py app/main.py miniprogram/app.js miniprogram/app.json miniprogram/utils/api.js miniprogram/utils/auth.js miniprogram/pages/auth tests/test_wechat_auth.py tests/miniprogram_auth.test.js
git commit -m "feat: bind employee accounts to WeChat"
```

### Task 6: Remove Employee Access to Sensitive Drawing Data

**Files:**
- Modify: `app/routers/mobile.py`
- Modify: `miniprogram/utils/api.js`
- Modify: `miniprogram/pages/inventory/inbound.js`
- Modify: `miniprogram/pages/inventory/outbound.js`
- Modify: `app/services/operation_log.py`
- Create: `tests/test_role_boundaries.py`

**Interfaces:**
- Produces a safe product-selection response that excludes file paths, previews, hashes, and raw drawing fields.
- Consumes verified account context from Task 3.

- [ ] **Step 1: Write failing role-boundary tests**

Use an employee session and an owner session against the same temporary database. A representative sensitive-route assertion is:

```python
def test_employee_cannot_list_drawings(self) -> None:
    response = self.client.get(
        "/api/mobile/drawings",
        headers={"Authorization": f"Bearer {self.employee_token}"},
    )
    self.assertEqual(response.status_code, 403)
```

Additional tests must cover upload, download, confirm, delete, product-option field exclusion, owner access, and verified employee-name audit attribution.

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m unittest tests.test_role_boundaries -v`

Expected: current mobile drawing endpoints are reachable without role checks.

- [ ] **Step 3: Apply owner-only dependencies to sensitive mobile routes**

Protect `/api/mobile/drawings/upload`, `/drawings`, `/drawings/pending`, `/drawings/{id}`, delete, confirm, and rerun with `Depends(require_owner_account)`. Keep employee business APIs under `Depends(require_mobile_account)`.

Create or reuse a safe product-option endpoint returning only:

```python
class ProductOptionOut(BaseModel):
    id: int
    product_code: str
    product_name: str | None
    material: str | None
    product_thickness: float | None
    plate_thickness: float | None
```

Do not include `dxf_file_url`, `preview_file_url`, `file_hash`, or recognition text.

- [ ] **Step 4: Move mini-program inventory selectors to the safe endpoint**

Replace `confirmedDrawings()` calls in the registered inbound/outbound pages with `productOptions()`. Remove unused drawing-management exports from `miniprogram/utils/api.js`; do not delete unregistered legacy files in this task.

- [ ] **Step 5: Verify audit actor attribution**

Ensure mobile writes use the authenticated employee from context for `OperationLog.operator_name`, even when a request payload contains another name.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
.venv/bin/python -m unittest tests.test_role_boundaries tests.test_mobile_idempotency tests.test_pc_mobile_material_parity -v
node --test tests/miniprogram_auth.test.js tests/miniprogram_material_api.test.js
```

Expected: PASS.

```bash
git add app/routers/mobile.py app/services/operation_log.py miniprogram/utils/api.js miniprogram/pages/inventory/inbound.js miniprogram/pages/inventory/outbound.js tests/test_role_boundaries.py
git commit -m "feat: enforce drawing data boundaries"
```

### Task 7: Authentication Regression and Secret Scan

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: tests only if a documented compatibility expectation needs a fixture update.

**Interfaces:**
- Produces documented production authentication settings and a clean regression baseline.

- [ ] **Step 1: Document exact environment keys without values**

Add:

```text
PRODUCTION=false
AUTH_PEPPER=
OWNER_TOTP_SECRET=
WECHAT_APP_ID=
WECHAT_APP_SECRET=
PC_SESSION_HOURS=12
MOBILE_SESSION_DAYS=30
```

Document generation commands:

```bash
openssl rand -hex 32
.venv/bin/python -c 'import pyotp; print(pyotp.random_base32())'
```

- [ ] **Step 2: Run the full backend and mini-program suites**

Run:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/*.test.js
```

Expected: all tests PASS.

- [ ] **Step 3: Scan tracked files for credentials**

Run:

```bash
git grep -n -E '(WECHAT_APP_SECRET=.+|AUTH_PEPPER=.+|OWNER_TOTP_SECRET=.+|Bearer [A-Za-z0-9_-]{20,})' -- ':!docs/superpowers/plans/*'
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add .env.example README.md
git commit -m "docs: document production authentication setup"
```
