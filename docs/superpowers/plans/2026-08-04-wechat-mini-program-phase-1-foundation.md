# WeChat Mini Program Phase 1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first usable foundation of the factory WeChat mini program: QR/manual LAN connection, three-tab mobile navigation, polished shared UI states, and durable idempotency for inventory writes.

**Architecture:** Keep FastAPI and the existing SQLite database as the single source of truth. Add a small connection service/admin page and a generic mobile request record that is committed in the same transaction as each write. In the native WeChat mini program, isolate stored connection configuration and HTTP behavior in utilities, route startup through a connection gate, and build only the Plan/Materials/Products top-level shells in this phase.

**Tech Stack:** Python 3.11+, FastAPI 0.115.6, SQLAlchemy 2.0.36, SQLite, qrcode 8.0, native WeChat Mini Program (WXML/WXSS/JavaScript), Node.js built-in test runner, unittest/pytest.

## Execution status (2026-08-04)

| Task | Status | Evidence |
|---|---|---|
| 1. Backend connection QR | Complete | Service, admin page, health metadata, and focused tests implemented. |
| 2. Durable backend idempotency | Complete | All six current product/scrap write routes replay identical requests and reject changed payloads with 409. |
| 3. Mini-program connection lifecycle | Complete | QR/manual setup, persisted LAN URL, timeout and recovery states implemented. |
| 4. Shared mobile UI | Complete | Approved tokens and the connection, state, and confirmation components implemented. |
| 5. Three-tab shell | Complete | `计划 / 材料 / 成品`, local icons, and connection gate implemented. |
| 6. Verification and setup | Complete | Automated suite, import, repeatable migration, docs, JSON/JS checks, Developer Tools connection/error recovery, top-level pages, and write confirmation visual checks pass. |
| Push | Pending | Phase 1 remains on `codex/miniprogram-phase1` until visual acceptance is complete. |

The detailed checkboxes below preserve the original TDD execution recipe. The table above is the authoritative current progress record.

## Global Constraints

- Phones and the backend computer must be on the same factory Wi-Fi; phase 1 does not add a cloud server or public access.
- The mini program and desktop admin must share the current FastAPI services and the same `data/app.db`.
- Bottom navigation contains exactly three labeled items: `计划`, `材料`, `成品`.
- Drawings, operation logs, assistant, Excel export, permissions, and offline writes stay outside the mobile navigation.
- Every mobile write sends a non-empty `client_request_id`; the server must reject reuse with different payload data and replay the original result for the same payload.
- All touch targets are at least `88rpx`; fixed navigation and submission areas reserve the bottom safe area.
- Use the design rules in `design-system/杭州特耐时移动库存/MASTER.md` and the page overrides in its `pages/` directory.
- Use one Lucide icon family. Native tab bar assets are local 81×81 PNG files, not network images or Emoji.
- Do not migrate, rewrite, or regroup existing inventory records during phase 1.
- Use TDD for every backend and pure JavaScript behavior change; run the full Python suite and the Node tests before completion.

## File Map

### Backend

- `app/services/mobile_connection.py`: validate LAN base URLs, discover local IPv4 addresses, build QR payload and PNG data URI.
- `app/mobile_connection_pages.py`: desktop admin connection page at `/admin/mobile-connection`.
- `app/services/mobile_idempotency.py`: payload fingerprinting, replay/conflict lookup, and response persistence.
- `app/models.py`: durable `MobileRequestRecord` model.
- `app/schema_migrations.py`: repeatable creation/indexing of `mobile_request_records` for customer databases.
- `app/main.py`: include the admin connection router and return richer `/health` information.
- `app/admin_pages.py`: add the connection page to the desktop sidebar.
- `app/routers/mobile.py`: require and persist request IDs for all currently exposed product and scrap writes.

### Mini program

- `miniprogram/utils/connection.js`: pure URL/QR validation plus storage and QR scanning adapters.
- `miniprogram/utils/request-id.js`: UUID-like request IDs plus persisted pending-write tracking with deterministic test seams.
- `miniprogram/utils/api.js`: configurable base URL, timeout, typed connection errors, and enforcement that tracked writes already carry a request ID.
- `miniprogram/app.js`: load stored connection at launch; remove the hard-coded IP and unused token.
- `miniprogram/app.json`: register the connection gate and three tab pages; keep old business files on disk but hide old drawing navigation.
- `miniprogram/app.wxss`: implement the approved industrial flat design tokens and shared states.
- `miniprogram/components/connection-status/*`: compact connected/failed status bar.
- `miniprogram/components/state-view/*`: loading, empty, filtered-empty, and error state.
- `miniprogram/components/confirm-sheet/*`: field-by-field confirmation sheet used by every current product and scrap write page.
- `miniprogram/pages/connection/index.js`, `index.json`, `index.wxml`, `index.wxss`: startup connection check, QR scan, and manual setup.
- `miniprogram/pages/plan/home.js`, `home.json`, `home.wxml`, `home.wxss`: plan tab shell.
- `miniprogram/pages/materials/home.js`, `home.json`, `home.wxml`, `home.wxss`: steel/scrap/paper entry shell.
- `miniprogram/pages/products/home.js`, `home.json`, `home.wxml`, `home.wxss`: product entry shell.
- `miniprogram/pages/materials/steel-home.js`, `steel-home.json`, `steel-home.wxml`, `steel-home.wxss`: clear future-phase steel shell.
- `miniprogram/pages/materials/paper-home.js`, `paper-home.json`, `paper-home.wxml`, `paper-home.wxss`: clear future-phase paper shell.
- Existing `miniprogram/pages/scraps/*` and `miniprogram/pages/inventory/*`: remain registered as secondary pages so existing product/scrap functions keep working.
- `miniprogram/assets/tabbar/*`: six Lucide-derived inactive/active PNG assets.

### Tests and docs

- `tests/test_mobile_connection.py`: connection URL, QR payload, health metadata, admin page, and navigation tests.
- `tests/test_mobile_idempotency.py`: model/migration, replay, conflict, and transactional persistence tests.
- `tests/test_miniprogram_foundation.py`: static manifest and UI contract checks.
- `tests/miniprogram_connection.test.js`: pure JavaScript connection and request-ID tests.
- `README.md`: factory Wi-Fi startup, QR setup, IP changes, and developer-tool instructions.

---

### Task 1: Backend connection discovery and admin QR page

**Files:**
- Create: `app/services/mobile_connection.py`
- Create: `app/mobile_connection_pages.py`
- Modify: `app/main.py`
- Modify: `app/admin_pages.py`
- Create: `tests/test_mobile_connection.py`

**Interfaces:**
- Produces: `normalize_mobile_base_url(value: str) -> str`
- Produces: `discover_lan_ipv4_addresses() -> list[str]`
- Produces: `build_connection_payload(base_url: str) -> dict[str, object]`
- Produces: `build_connection_qr_data_uri(base_url: str) -> str`
- Produces: `mobile_connection_page(host: str = "", port: int = 8000) -> HTMLResponse`
- Produces: `/health -> {status, app_name, app_version}`

- [ ] **Step 1: Write failing connection service and page tests**

```python
# tests/test_mobile_connection.py
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.admin_pages import page
from app.main import health_check
from app.mobile_connection_pages import mobile_connection_page
from app.services.mobile_connection import (
    build_connection_payload,
    normalize_mobile_base_url,
)


class MobileConnectionTest(unittest.TestCase):
    def test_normalize_accepts_lan_http_and_removes_trailing_slash(self) -> None:
        self.assertEqual(
            normalize_mobile_base_url(" http://192.168.31.68:8000/ "),
            "http://192.168.31.68:8000",
        )

    def test_normalize_rejects_public_host_and_path(self) -> None:
        for value in (
            "https://example.com",
            "http://8.8.8.8:8000",
            "http://192.168.1.5:8000/admin",
        ):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                normalize_mobile_base_url(value)

    def test_payload_contains_only_version_and_base_url(self) -> None:
        payload = build_connection_payload("http://10.0.0.8:8000")
        self.assertEqual(payload, {
            "version": 1,
            "base_url": "http://10.0.0.8:8000",
        })
        serialized = json.dumps(payload)
        self.assertNotIn("database", serialized)
        self.assertNotIn("token", serialized)

    def test_health_has_stable_connection_metadata(self) -> None:
        self.assertEqual(health_check(), {
            "status": "ok",
            "app_name": "杭州特耐时库存系统",
            "app_version": "0.1.0",
        })

    @patch(
        "app.mobile_connection_pages.discover_lan_ipv4_addresses",
        return_value=["192.168.31.68", "192.168.31.69"],
    )
    def test_admin_page_lists_addresses_and_embeds_qr(self, _discover) -> None:
        response = mobile_connection_page(host="192.168.31.69", port=8000)
        html = response.body.decode("utf-8")
        self.assertIn("小程序连接", html)
        self.assertIn("http://192.168.31.69:8000", html)
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("192.168.31.68", html)
        self.assertNotIn("data/app.db", html)

    def test_sidebar_contains_connection_entry(self) -> None:
        html = page("测试", "").body.decode("utf-8")
        self.assertIn('href="/admin/mobile-connection">小程序连接</a>', html)
```

- [ ] **Step 2: Run the tests and confirm the missing modules/metadata fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_mobile_connection.py -q
```

Expected: collection fails because `app.mobile_connection_pages` and `app.services.mobile_connection` do not exist.

- [ ] **Step 3: Implement the connection service**

```python
# app/services/mobile_connection.py
import base64
import ipaddress
import io
import json
import socket
from urllib.parse import urlsplit

import qrcode
from fastapi import HTTPException


CONNECTION_PAYLOAD_VERSION = 1


def normalize_mobile_base_url(value: str) -> str:
    text = (value or "").strip().rstrip("/")
    parsed = urlsplit(text)
    if parsed.scheme != "http" or not parsed.hostname or parsed.path not in ("", "/"):
        raise HTTPException(status_code=400, detail="请输入厂内 HTTP 地址，例如 http://192.168.1.20:8000")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="连接地址必须使用厂内局域网 IP") from exc
    if not address.is_private or address.is_loopback or address.is_link_local:
        raise HTTPException(status_code=400, detail="连接地址必须使用厂内局域网 IP")
    port = parsed.port or 80
    if port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="端口必须在 1 到 65535 之间")
    return f"http://{address.compressed}:{port}"


def discover_lan_ipv4_addresses() -> list[str]:
    candidates: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.add(info[4][0])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            candidates.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(
        value
        for value in candidates
        if (address := ipaddress.ip_address(value)).is_private
        and not address.is_loopback
        and not address.is_link_local
    )


def build_connection_payload(base_url: str) -> dict[str, object]:
    return {
        "version": CONNECTION_PAYLOAD_VERSION,
        "base_url": normalize_mobile_base_url(base_url),
    }


def build_connection_qr_data_uri(base_url: str) -> str:
    content = json.dumps(build_connection_payload(base_url), ensure_ascii=False, separators=(",", ":"))
    image = qrcode.make(content)
    output = io.BytesIO()
    image.save(output, format="PNG")
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
```

- [ ] **Step 4: Implement the admin page and register it**

```python
# app/mobile_connection_pages.py
import html

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.admin_pages import page
from app.services.mobile_connection import (
    build_connection_qr_data_uri,
    discover_lan_ipv4_addresses,
    normalize_mobile_base_url,
)


router = APIRouter()


@router.get("/admin/mobile-connection", response_class=HTMLResponse)
def mobile_connection_page(host: str = "", port: int = 8000) -> HTMLResponse:
    addresses = discover_lan_ipv4_addresses()
    selected_host = host.strip() or (addresses[0] if addresses else "")
    base_url = ""
    qr_uri = ""
    error = ""
    if selected_host:
        try:
            base_url = normalize_mobile_base_url(f"http://{selected_host}:{port}")
            qr_uri = build_connection_qr_data_uri(base_url)
        except HTTPException as exc:
            error = str(exc.detail)
    address_options = "".join(
        f'<option value="{html.escape(address)}"></option>'
        for address in addresses
    )
    body = f"""
    <div class="top"><div><h1>小程序连接</h1><p class="muted">手机连接工厂 Wi-Fi 后，扫描二维码保存后台地址。</p></div></div>
    <section class="card">
      <form method="get" action="/admin/mobile-connection" class="form-grid">
        <div><label>局域网 IP</label><input name="host" list="lan-addresses" value="{html.escape(selected_host)}" placeholder="例如 192.168.31.68"><datalist id="lan-addresses">{address_options}</datalist></div>
        <div><label>端口</label><input name="port" type="number" min="1" max="65535" value="{port}"></div>
        <button class="btn" type="submit">生成连接二维码</button>
      </form>
    </section>
    <section class="card">
      {f'<p style="color:#dc2626">{html.escape(error)}</p>' if error else ''}
      {f'<h2>当前地址</h2><p><strong>{html.escape(base_url)}</strong></p><img src="{qr_uri}" alt="小程序连接二维码" width="280" height="280"><p class="muted">二维码只包含版本号和连接地址。</p>' if qr_uri else '<p class="muted">未检测到局域网地址，请手工输入电脑 IP。</p>'}
    </section>
    """
    return page("小程序连接", body)
```

Modify `app/main.py` so imports and registration are exact:

```python
from app import admin_pages, mobile_connection_pages, paper_admin_pages

app.include_router(mobile_connection_pages.router, tags=["小程序连接"])

@app.get("/health")
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "app_name": "杭州特耐时库存系统",
        "app_version": app.version,
    }
```

Add this link immediately after the desktop `后台首页` link in `app/admin_pages.py`:

```html
<a href="/admin/mobile-connection">小程序连接</a>
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mobile_connection.py -q
```

Expected: `6 passed`.

- [ ] **Step 6: Commit**

```bash
git add app/services/mobile_connection.py app/mobile_connection_pages.py app/main.py app/admin_pages.py tests/test_mobile_connection.py
git commit -m "feat: add mini program connection qr"
```

---

### Task 2: Durable mobile write idempotency

**Files:**
- Modify: `app/models.py`
- Modify: `app/schema_migrations.py`
- Create: `app/services/mobile_idempotency.py`
- Modify: `app/routers/mobile.py`
- Create: `tests/test_mobile_idempotency.py`

**Interfaces:**
- Produces: `MobileRequestRecord`
- Produces: `payload_fingerprint(payload: dict[str, object]) -> str`
- Produces: `replayed_mobile_response(db, operation_type, client_request_id, payload) -> dict | None`
- Produces: `remember_mobile_response(db, operation_type, client_request_id, payload, response) -> MobileRequestRecord`
- Consumes: existing `inventory_write_lock()` so same-process writes cannot race before SQLite unique enforcement.

- [ ] **Step 1: Write failing model, migration, replay, and conflict tests**

```python
# tests/test_mobile_idempotency.py
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import MobileRequestRecord
from app.schema_migrations import ensure_runtime_schema
from app.services.mobile_idempotency import (
    remember_mobile_response,
    replayed_mobile_response,
)


class MobileIdempotencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def test_runtime_schema_creates_mobile_request_table_idempotently(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        ensure_runtime_schema(engine)
        ensure_runtime_schema(engine)
        inspector = inspect(engine)
        self.assertIn("mobile_request_records", inspector.get_table_names())
        names = {item["name"] for item in inspector.get_unique_constraints("mobile_request_records")}
        self.assertIn("uq_mobile_request_operation_client", names)

    def test_same_request_replays_original_response(self) -> None:
        payload = {"drawing_id": 12, "quantity": 5}
        with self.Session() as db:
            remember_mobile_response(db, "product_outbound", "req-001", payload, {"after_quantity": 7})
            db.commit()
            replay = replayed_mobile_response(db, "product_outbound", "req-001", payload)
            self.assertEqual(replay, {"after_quantity": 7})
            self.assertEqual(db.query(MobileRequestRecord).count(), 1)

    def test_same_id_with_changed_payload_returns_conflict(self) -> None:
        with self.Session() as db:
            remember_mobile_response(db, "product_outbound", "req-002", {"quantity": 5}, {"after_quantity": 7})
            db.commit()
            with self.assertRaises(HTTPException) as raised:
                replayed_mobile_response(db, "product_outbound", "req-002", {"quantity": 6})
            self.assertEqual(raised.exception.status_code, 409)

    def test_blank_request_id_is_rejected(self) -> None:
        with self.Session() as db, self.assertRaises(HTTPException) as raised:
            replayed_mobile_response(db, "product_outbound", "  ", {"quantity": 1})
        self.assertEqual(raised.exception.status_code, 422)
```

- [ ] **Step 2: Run tests and confirm missing model/service failures**

Run:

```bash
.venv/bin/python -m pytest tests/test_mobile_idempotency.py -q
```

Expected: collection fails because `MobileRequestRecord` and `app.services.mobile_idempotency` do not exist.

- [ ] **Step 3: Add the durable model and repeatable runtime migration**

Add `UniqueConstraint` to the SQLAlchemy imports and append this model in `app/models.py`:

```python
class MobileRequestRecord(Base):
    __tablename__ = "mobile_request_records"
    __table_args__ = (
        UniqueConstraint("operation_type", "client_request_id", name="uq_mobile_request_operation_client"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    operation_type: Mapped[str] = mapped_column(String(80), index=True)
    client_request_id: Mapped[str] = mapped_column(String(100), index=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now, index=True)
```

Add this block inside `ensure_runtime_schema()` before the time migration:

```python
if "mobile_request_records" not in tables:
    connection.execute(text("""
        CREATE TABLE mobile_request_records (
            id INTEGER PRIMARY KEY,
            operation_type VARCHAR(80) NOT NULL,
            client_request_id VARCHAR(100) NOT NULL,
            request_fingerprint VARCHAR(64) NOT NULL,
            response_json JSON NOT NULL,
            created_at DATETIME,
            CONSTRAINT uq_mobile_request_operation_client
                UNIQUE (operation_type, client_request_id)
        )
    """))
connection.execute(text("CREATE INDEX IF NOT EXISTS ix_mobile_request_records_operation_type ON mobile_request_records (operation_type)"))
connection.execute(text("CREATE INDEX IF NOT EXISTS ix_mobile_request_records_client_request_id ON mobile_request_records (client_request_id)"))
connection.execute(text("CREATE INDEX IF NOT EXISTS ix_mobile_request_records_created_at ON mobile_request_records (created_at)"))
```

Add `"mobile_request_records": ("created_at",),` to `TIMESTAMP_COLUMNS`.

- [ ] **Step 4: Implement replay and conflict handling**

```python
# app/services/mobile_idempotency.py
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import MobileRequestRecord


def _request_id(value: str | None) -> str:
    request_id = (value or "").strip()
    if not request_id:
        raise HTTPException(status_code=422, detail="client_request_id 不能为空")
    if len(request_id) > 100:
        raise HTTPException(status_code=422, detail="client_request_id 不能超过100个字符")
    return request_id


def payload_fingerprint(payload: dict[str, object]) -> str:
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def replayed_mobile_response(
    db: Session,
    operation_type: str,
    client_request_id: str | None,
    payload: dict[str, object],
) -> dict | None:
    request_id = _request_id(client_request_id)
    existing = db.query(MobileRequestRecord).filter(
        MobileRequestRecord.operation_type == operation_type,
        MobileRequestRecord.client_request_id == request_id,
    ).first()
    if existing is None:
        return None
    if existing.request_fingerprint != payload_fingerprint(payload):
        raise HTTPException(status_code=409, detail="同一请求编号不能提交不同内容，请刷新页面后重试")
    return dict(existing.response_json)


def remember_mobile_response(
    db: Session,
    operation_type: str,
    client_request_id: str | None,
    payload: dict[str, object],
    response: dict,
) -> MobileRequestRecord:
    record = MobileRequestRecord(
        operation_type=operation_type,
        client_request_id=_request_id(client_request_id),
        request_fingerprint=payload_fingerprint(payload),
        response_json=response,
    )
    db.add(record)
    return record
```

- [ ] **Step 5: Require request IDs on all current inventory write payloads**

Use a shared Pydantic base in `app/routers/mobile.py`:

```python
class MobileWritePayload(BaseModel):
    client_request_id: str


class ProductInboundPayload(MobileWritePayload):
    drawing_id: int
    quantity: int = 1
    location: str | None = None
    paper_material: str | None = None
    operator_name: str | None = None


class ProductOutboundPayload(MobileWritePayload):
    drawing_id: int
    quantity: int
    location: str | None = None
    operator_name: str | None = None
    customer_name: str | None = None
    outbound_purpose: str | None = "sales"
    remark: str | None = None


class TransactionReversePayload(MobileWritePayload):
    operator_name: str | None = None
    remark: str | None = None


class ScrapConfirmPayload(MobileWritePayload):
    actual_quantity: int
    actual_diameter: float | None = None
    location: str
    operator_name: str | None = None


class ScrapOutboundPayload(MobileWritePayload):
    scrap_group_key: str
    quantity: int
    operator_name: str | None = None
    customer_name: str | None = None
    remark: str | None = None
```

For each of these routes, perform the exact sequence while inside `inventory_write_lock()`:

```python
request_payload = payload.model_dump(mode="json", exclude={"client_request_id"})
replayed = replayed_mobile_response(db, operation_type, payload.client_request_id, request_payload)
if replayed is not None:
    return replayed
```

Insert that block immediately inside each route's `inventory_write_lock()` and before its current first database lookup or validation. Preserve the route's current validation and inventory mutations. Immediately before the route's current `db.commit()`, build the route-specific `response_data` from the table below, call `remember_mobile_response(db, operation_type, payload.client_request_id, request_payload, response_data)`, commit once, and return `response_data`.

Use these exact operation names and response builders:

| Route | `operation_type` | `response_data` |
| --- | --- | --- |
| `product_inbound` | `product_inbound` | `InventoryItemOut.model_validate(result.item).model_dump(mode="json")` |
| `product_outbound` | `product_outbound` | existing `{message, before_quantity, after_quantity}` dict |
| `confirm_scrap` | `scrap_confirm` | `InventoryItemOut.model_validate(item).model_dump(mode="json")` |
| `scrap_outbound` | `scrap_outbound` | existing `{message, before_quantity, after_quantity}` dict |
| `reverse_product_transaction` | `product_transaction_reverse` | `_transaction_rows([reversal], "product", db)[0].model_dump(mode="json")` |
| `reverse_scrap_transaction` | `scrap_transaction_reverse` | `_transaction_rows([reversal], "scrap", db)[0].model_dump(mode="json")` |

Remove endpoint-level early replay queries against `InventoryTransactionRecord.idempotency_key`; keep transaction idempotency keys only as historical compatibility fields. Wrap both reverse routes in `inventory_write_lock()` before replay lookup.

- [ ] **Step 6: Add a route-level regression proving one inventory mutation**

Add these imports and test to `tests/test_mobile_idempotency.py`:

```python
from app.models import MaterialInventory, MobileRequestRecord, ProductDrawing
from app.routers.mobile import ProductInboundPayload, product_inbound

# Add this method inside the existing MobileIdempotencyTest class.
    def test_product_inbound_mutates_inventory_once_for_duplicate_request(self) -> None:
        with self.Session() as db:
            drawing = ProductDrawing(
                product_code="TNX-MOBILE-001",
                product_name="幂等测试产品",
                dxf_file_url="/tmp/mobile-idempotency.dxf",
                material="65Mn",
                product_thickness=1.2,
                plate_thickness=1.0,
                max_outer_diameter=100,
                confirmed=1,
                is_active=1,
            )
            db.add(drawing)
            db.commit()
            db.refresh(drawing)

            payload = ProductInboundPayload(
                client_request_id="mobile-inbound-001",
                drawing_id=drawing.id,
                quantity=3,
                location="A-01",
                operator_name="测试员",
            )
            first = product_inbound(payload, db=db)
            second = product_inbound(payload, db=db)

            self.assertEqual(first["id"], second["id"])
            total = sum(
                item.quantity
                for item in db.query(MaterialInventory).filter(
                    MaterialInventory.inventory_type == "product",
                    MaterialInventory.material_code == "TNX-MOBILE-001",
                ).all()
            )
            self.assertEqual(total, 3)
            self.assertEqual(db.query(MobileRequestRecord).count(), 1)

            changed = ProductInboundPayload(
                client_request_id="mobile-inbound-001",
                drawing_id=drawing.id,
                quantity=4,
                location="A-01",
                operator_name="测试员",
            )
            with self.assertRaises(HTTPException) as raised:
                product_inbound(changed, db=db)
            self.assertEqual(raised.exception.status_code, 409)
```

Run:

```bash
.venv/bin/python -m pytest tests/test_mobile_idempotency.py -q
```

Expected: all tests pass, including the duplicate mutation regression.

- [ ] **Step 7: Commit**

```bash
git add app/models.py app/schema_migrations.py app/services/mobile_idempotency.py app/routers/mobile.py tests/test_mobile_idempotency.py
git commit -m "feat: make mobile writes idempotent"
```

---

### Task 3: Mini-program connection utilities and request lifecycle

**Files:**
- Create: `miniprogram/utils/connection.js`
- Create: `miniprogram/utils/request-id.js`
- Modify: `miniprogram/utils/api.js`
- Modify: `miniprogram/app.js`
- Create: `tests/miniprogram_connection.test.js`

**Interfaces:**
- Produces: `normalizeBaseUrl(value) -> string`
- Produces: `parseConnectionPayload(rawValue) -> {version: 1, base_url: string}`
- Produces: `loadSavedBaseUrl(wxApi)`, `saveBaseUrl(wxApi, value)`, `scanBaseUrl(wxApi)`
- Produces: `createRequestId(nowFn, randomFn) -> string`
- Produces: `api.health()`, `api.configureBaseUrl(value)`, and typed `ConnectionError`.

- [ ] **Step 1: Write failing pure JavaScript tests**

```javascript
// tests/miniprogram_connection.test.js
const test = require('node:test')
const assert = require('node:assert/strict')

const connection = require('../miniprogram/utils/connection')
const { createRequestId } = require('../miniprogram/utils/request-id')

test('normalizes a private LAN address', () => {
  assert.equal(connection.normalizeBaseUrl(' http://192.168.31.68:8000/ '), 'http://192.168.31.68:8000')
})

test('rejects public, https, and path-bearing addresses', () => {
  assert.throws(() => connection.normalizeBaseUrl('https://192.168.1.2:8000'), /HTTP/)
  assert.throws(() => connection.normalizeBaseUrl('http://8.8.8.8:8000'), /局域网/)
  assert.throws(() => connection.normalizeBaseUrl('http://192.168.1.2:8000/admin'), /路径/)
})

test('parses only version 1 connection QR payloads', () => {
  assert.deepEqual(connection.parseConnectionPayload('{"version":1,"base_url":"http://10.0.0.8:8000"}'), {
    version: 1,
    base_url: 'http://10.0.0.8:8000'
  })
  assert.throws(() => connection.parseConnectionPayload('{"version":2,"base_url":"http://10.0.0.8:8000"}'), /版本/)
})

test('request id is stable under injected clock and random source', () => {
  assert.equal(createRequestId(() => 1722768000000, () => 0.25), 'mobile-1722768000000-40000000')
})
```

- [ ] **Step 2: Run tests and confirm missing modules fail**

Run:

```bash
node --test tests/miniprogram_connection.test.js
```

Expected: fails with `Cannot find module '../miniprogram/utils/connection'`.

- [ ] **Step 3: Implement pure validation, storage, scanning, and request IDs**

```javascript
// miniprogram/utils/request-id.js
function createRequestId(nowFn = Date.now, randomFn = Math.random) {
  const randomPart = Math.floor(randomFn() * 0x100000000).toString(16).padStart(8, '0')
  return `mobile-${nowFn()}-${randomPart}`
}

module.exports = { createRequestId }
```

```javascript
// miniprogram/utils/connection.js
const STORAGE_KEY = 'tenaishi_mobile_base_url'

function isPrivateIpv4(host) {
  const parts = host.split('.').map(Number)
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return false
  return parts[0] === 10 || (parts[0] === 192 && parts[1] === 168) || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31)
}

function normalizeBaseUrl(value) {
  const text = String(value || '').trim().replace(/\/$/, '')
  if (!text.startsWith('http://')) throw new Error('连接地址必须使用厂内 HTTP 地址')
  const matched = text.match(/^http:\/\/([^/:]+)(?::(\d{1,5}))?$/)
  if (!matched) throw new Error('连接地址不能包含路径，请填写 IP 和端口')
  if (!isPrivateIpv4(matched[1])) throw new Error('连接地址必须使用厂内局域网 IP')
  const port = Number(matched[2] || 80)
  if (port < 1 || port > 65535) throw new Error('端口必须在 1 到 65535 之间')
  return `http://${matched[1]}:${port}`
}

function parseConnectionPayload(rawValue) {
  let payload
  try {
    payload = JSON.parse(rawValue)
  } catch (error) {
    throw new Error('二维码不是杭州特耐时连接码')
  }
  if (payload.version !== 1) throw new Error('连接二维码版本不支持，请在电脑后台重新生成')
  return { version: 1, base_url: normalizeBaseUrl(payload.base_url) }
}

function loadSavedBaseUrl(wxApi = wx) {
  const value = wxApi.getStorageSync(STORAGE_KEY)
  return value ? normalizeBaseUrl(value) : ''
}

function saveBaseUrl(wxApi = wx, value) {
  const normalized = normalizeBaseUrl(value)
  wxApi.setStorageSync(STORAGE_KEY, normalized)
  return normalized
}

function scanCode(wxApi = wx) {
  return new Promise((resolve, reject) => {
    wxApi.scanCode({ scanType: ['qrCode'], success: resolve, fail: reject })
  })
}

async function scanBaseUrl(wxApi = wx) {
  const result = await scanCode(wxApi)
  const payload = parseConnectionPayload(result.result)
  return payload.base_url
}

module.exports = {
  STORAGE_KEY,
  normalizeBaseUrl,
  parseConnectionPayload,
  loadSavedBaseUrl,
  saveBaseUrl,
  scanBaseUrl
}
```

- [ ] **Step 4: Refactor the request client around configured connection state**

In `miniprogram/app.js`, replace hard-coded globals with:

```javascript
const connection = require('./utils/connection')

App({
  globalData: {
    baseUrl: '',
    connectionState: 'unknown'
  },

  onLaunch() {
    try {
      this.globalData.baseUrl = connection.loadSavedBaseUrl(wx)
    } catch (error) {
      this.globalData.baseUrl = ''
    }
  }
})
```

In `miniprogram/utils/api.js`, add:

```javascript
const { createRequestId } = require('./request-id')

class ConnectionError extends Error {
  constructor(message, code = 'CONNECTION_FAILED') {
    super(message)
    this.name = 'ConnectionError'
    this.code = code
  }
}

function configureBaseUrl(value) {
  const app = getApp()
  app.globalData.baseUrl = value.replace(/\/$/, '')
}

function baseUrl() {
  const value = getApp().globalData.baseUrl
  if (!value) throw new ConnectionError('尚未连接厂内库存系统', 'NOT_CONFIGURED')
  return value.replace(/\/$/, '')
}

function withRequestId(data = {}) {
  return { ...data, client_request_id: data.client_request_id || createRequestId() }
}
```

Replace the current `request()` implementation with:

```javascript
function request(path, options = {}) {
  let url
  try {
    url = `${baseUrl()}${path}`
  } catch (error) {
    return Promise.reject(error)
  }
  return new Promise((resolve, reject) => {
    wx.request({
      url,
      method: options.method || 'GET',
      data: options.data || {},
      timeout: 8000,
      header: { 'content-type': 'application/json' },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
          return
        }
        reject(new Error(errorMessage(res.data, '请求失败')))
      },
      fail() {
        reject(new ConnectionError('无法连接后台，请确认手机和电脑连接同一工厂 Wi-Fi'))
      }
    })
  })
}
```

Add `timeout: 8000` and the same `ConnectionError` mapping to `uploadFile()`. Add these exact exports and replace the current matching write exports:

```javascript
configureBaseUrl,
ConnectionError,
health: () => request('/health'),
productInbound: (data) => request('/api/mobile/products/inbound', { method: 'POST', data: withRequestId(data) }),
productOutbound: (data) => request('/api/mobile/products/outbound', { method: 'POST', data: withRequestId(data) }),
reverseProductTransaction: (id, data = {}) => request(`/api/mobile/products/transactions/${id}/reverse`, { method: 'POST', data: withRequestId(data) }),
confirmScrap: (id, data) => request(`/api/mobile/scraps/${id}/confirm`, { method: 'POST', data: withRequestId(data) }),
scrapOutbound: (data) => request('/api/mobile/scraps/outbound', { method: 'POST', data: withRequestId(data) }),
reverseScrapTransaction: (id, data = {}) => request(`/api/mobile/scraps/transactions/${id}/reverse`, { method: 'POST', data: withRequestId(data) })
```

- [ ] **Step 5: Run pure JavaScript tests**

Run:

```bash
node --test tests/miniprogram_connection.test.js
```

Expected: `4 passed, 0 failed`.

- [ ] **Step 6: Commit**

```bash
git add miniprogram/app.js miniprogram/utils/api.js miniprogram/utils/connection.js miniprogram/utils/request-id.js tests/miniprogram_connection.test.js
git commit -m "feat: add mini program lan connection client"
```

---

### Task 4: Shared mobile UI components and industrial design tokens

**Files:**
- Modify: `miniprogram/app.wxss`
- Create: `miniprogram/components/connection-status/index.js`
- Create: `miniprogram/components/connection-status/index.json`
- Create: `miniprogram/components/connection-status/index.wxml`
- Create: `miniprogram/components/connection-status/index.wxss`
- Create: `miniprogram/components/state-view/index.js`
- Create: `miniprogram/components/state-view/index.json`
- Create: `miniprogram/components/state-view/index.wxml`
- Create: `miniprogram/components/state-view/index.wxss`
- Create: `miniprogram/components/confirm-sheet/index.js`
- Create: `miniprogram/components/confirm-sheet/index.json`
- Create: `miniprogram/components/confirm-sheet/index.wxml`
- Create: `miniprogram/components/confirm-sheet/index.wxss`
- Create: `tests/test_miniprogram_foundation.py`

**Interfaces:**
- `connection-status`: properties `state`, `baseUrl`; emits `retry` and `configure`.
- `state-view`: properties `state`, `title`, `description`; emits `retry`.
- `confirm-sheet`: properties `open`, `title`, `lines`, `danger`, `submitting`; emits `cancel` and `confirm`.

- [ ] **Step 1: Write failing component contract tests**

```python
# tests/test_miniprogram_foundation.py
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MiniProgramFoundationTest(unittest.TestCase):
    def read(self, path: str) -> str:
        return (ROOT / path).read_text(encoding="utf-8")

    def test_shared_components_are_registered_and_accessible(self) -> None:
        state = self.read("miniprogram/components/state-view/index.wxml")
        confirm = self.read("miniprogram/components/confirm-sheet/index.wxml")
        connection = self.read("miniprogram/components/connection-status/index.wxml")
        self.assertIn("bindtap=\"retry\"", state)
        self.assertIn("确认操作", confirm)
        self.assertIn("bindtap=\"confirm\"", confirm)
        self.assertIn("重新连接", connection)

    def test_global_style_uses_approved_tokens_without_decorative_gradients(self) -> None:
        wxss = self.read("miniprogram/app.wxss")
        for value in ("#334155", "#059669", "#F8FAFC", "#0F172A", "#DC2626"):
            self.assertIn(value.lower(), wxss.lower())
        self.assertIn("min-height: 88rpx", wxss)
        self.assertNotIn("radial-gradient", wxss)
        self.assertNotIn("linear-gradient", wxss)
```

- [ ] **Step 2: Run the static tests and confirm missing component failures**

Run:

```bash
.venv/bin/python -m pytest tests/test_miniprogram_foundation.py -q
```

Expected: fails because the new component files do not exist.

- [ ] **Step 3: Replace global presentation with approved tokens**

Rewrite `miniprogram/app.wxss` around these exact global foundations, then port existing utility class names onto the new tokens so registered legacy pages remain readable:

```css
page {
  min-height: 100%;
  background: #F8FAFC;
  color: #0F172A;
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 30rpx;
  line-height: 1.5;
  font-variant-numeric: tabular-nums;
}

.container {
  min-height: 100vh;
  box-sizing: border-box;
  padding: 32rpx 32rpx calc(48rpx + env(safe-area-inset-bottom));
}

.card {
  box-sizing: border-box;
  margin-bottom: 20rpx;
  padding: 28rpx;
  border: 1rpx solid #E2E8F0;
  border-radius: 24rpx;
  background: #FFFFFF;
  box-shadow: 0 4rpx 16rpx rgba(15, 23, 42, .06);
}

.input,
.picker,
.btn,
.menu-item {
  min-height: 88rpx;
}

.btn {
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 18rpx;
  background: #059669;
  color: #FFFFFF;
  font-size: 30rpx;
  font-weight: 600;
}

.btn.secondary {
  border: 1rpx solid #CBD5E1;
  background: #FFFFFF;
  color: #334155;
}

.btn.danger {
  background: #DC2626;
  color: #FFFFFF;
}

.btn[disabled] {
  opacity: .45;
}
```

Do not retain `radial-gradient`, `linear-gradient`, 900-weight body labels, or decorative circles from the old theme.

- [ ] **Step 4: Implement the three components with explicit states and events**

Each component JSON is:

```json
{ "component": true }
```

Component JavaScript definitions:

```javascript
// connection-status/index.js
Component({
  properties: {
    state: { type: String, value: 'unknown' },
    baseUrl: { type: String, value: '' }
  },
  methods: {
    retry() { this.triggerEvent('retry') },
    configure() { this.triggerEvent('configure') }
  }
})
```

```javascript
// state-view/index.js
Component({
  properties: {
    state: { type: String, value: 'empty' },
    title: { type: String, value: '' },
    description: { type: String, value: '' }
  },
  methods: {
    retry() { this.triggerEvent('retry') }
  }
})
```

```javascript
// confirm-sheet/index.js
Component({
  properties: {
    open: { type: Boolean, value: false },
    title: { type: String, value: '确认操作' },
    lines: { type: Array, value: [] },
    danger: { type: Boolean, value: false },
    submitting: { type: Boolean, value: false }
  },
  methods: {
    cancel() { if (!this.data.submitting) this.triggerEvent('cancel') },
    confirm() { if (!this.data.submitting) this.triggerEvent('confirm') }
  }
})
```

Use these exact WXML structures:

```xml
<!-- connection-status/index.wxml -->
<view class="connection connection--{{state}}">
  <view class="connection__main">
    <view class="connection__dot"></view>
    <view>
      <view class="connection__title" wx:if="{{state === 'connected'}}">已连接</view>
      <view class="connection__title" wx:elif="{{state === 'checking'}}">连接中</view>
      <view class="connection__title" wx:else>连接失败</view>
      <view class="connection__address" wx:if="{{baseUrl}}">{{baseUrl}}</view>
    </view>
  </view>
  <view class="connection__actions" wx:if="{{state === 'error'}}">
    <button class="mini-action" bindtap="retry">重新连接</button>
    <button class="mini-action" bindtap="configure">修改地址</button>
  </view>
</view>
```

```xml
<!-- state-view/index.wxml -->
<view class="state-view" aria-live="polite">
  <block wx:if="{{state === 'loading'}}">
    <view class="skeleton"></view><view class="skeleton"></view><view class="skeleton"></view>
  </block>
  <block wx:else>
    <view class="state-title">{{title}}</view>
    <view class="state-description" wx:if="{{description}}">{{description}}</view>
    <button class="state-retry" wx:if="{{state === 'error'}}" bindtap="retry">重新加载</button>
  </block>
</view>
```

```xml
<!-- confirm-sheet/index.wxml -->
<view class="sheet-mask" wx:if="{{open}}" catchtouchmove="true">
  <view class="sheet" role="dialog" aria-label="{{title}}">
    <view class="sheet-title">{{title || '确认操作'}}</view>
    <view class="sheet-line" wx:for="{{lines}}" wx:key="label">
      <text class="sheet-label">{{item.label}}</text>
      <text class="sheet-value">{{item.value || '-'}}</text>
    </view>
    <view class="sheet-actions">
      <button class="btn secondary" disabled="{{submitting}}" bindtap="cancel">取消</button>
      <button class="btn {{danger ? 'danger' : ''}}" disabled="{{submitting}}" bindtap="confirm">{{submitting ? '提交中…' : '确认操作'}}</button>
    </view>
  </view>
</view>
```

Use this shared component sizing in each scoped WXSS file, then add only selectors used by that component:

```css
button { min-height: 88rpx; }
.connection, .state-view, .sheet { padding: 24rpx; color: #0F172A; background: #FFFFFF; }
.connection { border: 1rpx solid #E2E8F0; border-radius: 20rpx; }
.connection__actions, .sheet-actions { display: flex; gap: 16rpx; }
.mini-action, .state-retry { min-height: 88rpx; padding: 0 24rpx; color: #334155; background: #FFFFFF; border: 1rpx solid #CBD5E1; border-radius: 18rpx; }
.sheet-mask { position: fixed; inset: 0; z-index: 100; display: flex; align-items: flex-end; background: rgba(15, 23, 42, .42); }
.sheet { width: 100%; padding-bottom: calc(24rpx + env(safe-area-inset-bottom)); border-radius: 24rpx 24rpx 0 0; }
.sheet-line { display: grid; grid-template-columns: 180rpx minmax(0, 1fr); gap: 16rpx; padding: 20rpx 0; border-bottom: 1rpx solid #E2E8F0; }
.sheet-value { text-align: right; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_miniprogram_foundation.py -q
```

Expected: global style and component contract tests pass.

- [ ] **Step 6: Commit**

```bash
git add miniprogram/app.wxss miniprogram/components tests/test_miniprogram_foundation.py
git commit -m "feat: add mini program ui foundation"
```

---

### Task 5: Connection gate and three-tab navigation shells

**Files:**
- Modify: `miniprogram/app.json`
- Create: `miniprogram/pages/connection/index.js`
- Create: `miniprogram/pages/connection/index.json`
- Create: `miniprogram/pages/connection/index.wxml`
- Create: `miniprogram/pages/connection/index.wxss`
- Create: `miniprogram/pages/plan/home.js`
- Create: `miniprogram/pages/plan/home.json`
- Create: `miniprogram/pages/plan/home.wxml`
- Create: `miniprogram/pages/plan/home.wxss`
- Create: `miniprogram/pages/materials/home.js`
- Create: `miniprogram/pages/materials/home.json`
- Create: `miniprogram/pages/materials/home.wxml`
- Create: `miniprogram/pages/materials/home.wxss`
- Create: `miniprogram/pages/materials/steel-home.js`
- Create: `miniprogram/pages/materials/steel-home.json`
- Create: `miniprogram/pages/materials/steel-home.wxml`
- Create: `miniprogram/pages/materials/steel-home.wxss`
- Create: `miniprogram/pages/materials/paper-home.js`
- Create: `miniprogram/pages/materials/paper-home.json`
- Create: `miniprogram/pages/materials/paper-home.wxml`
- Create: `miniprogram/pages/materials/paper-home.wxss`
- Create: `miniprogram/pages/products/home.js`
- Create: `miniprogram/pages/products/home.json`
- Create: `miniprogram/pages/products/home.wxml`
- Create: `miniprogram/pages/products/home.wxss`
- Create: `miniprogram/assets/tabbar/plan.png`
- Create: `miniprogram/assets/tabbar/plan-active.png`
- Create: `miniprogram/assets/tabbar/materials.png`
- Create: `miniprogram/assets/tabbar/materials-active.png`
- Create: `miniprogram/assets/tabbar/products.png`
- Create: `miniprogram/assets/tabbar/products-active.png`
- Modify: `tests/test_miniprogram_foundation.py`

**Interfaces:**
- Startup page calls `api.health()` before business navigation.
- Successful scan/manual setup saves configuration, calls `api.configureBaseUrl()`, then `wx.switchTab({url: '/pages/plan/home'})`.
- Material shell links existing scrap secondary pages and phase-labeled steel/paper shells.
- Product shell links existing inventory secondary pages without exposing drawings.

- [ ] **Step 1: Extend failing manifest and page contract tests**

Append these tests to `tests/test_miniprogram_foundation.py`:

```python
def test_manifest_has_three_tabs_and_connection_first(self) -> None:
    app_json = json.loads(self.read("miniprogram/app.json"))
    self.assertEqual(app_json["pages"][0], "pages/connection/index")
    self.assertEqual(
        [(item["pagePath"], item["text"]) for item in app_json["tabBar"]["list"]],
        [
            ("pages/plan/home", "计划"),
            ("pages/materials/home", "材料"),
            ("pages/products/home", "成品"),
        ],
    )
    self.assertNotIn("pages/drawings/home", [item["pagePath"] for item in app_json["tabBar"]["list"]])

def test_materials_home_has_three_clear_business_entries(self) -> None:
    wxml = self.read("miniprogram/pages/materials/home.wxml")
    for label in ("钢板", "余料", "纸材"):
        self.assertIn(label, wxml)
    self.assertIn("待确认", wxml)

def test_connection_page_supports_scan_manual_and_recovery(self) -> None:
    source = self.read("miniprogram/pages/connection/index.js")
    view = self.read("miniprogram/pages/connection/index.wxml")
    self.assertIn("scanBaseUrl", source)
    self.assertIn("testAndSave", source)
    self.assertIn("扫描电脑连接二维码", view)
    self.assertIn("手工设置地址", view)
    self.assertIn("连接工厂 Wi-Fi", view)

def test_tabbar_assets_exist_and_are_small_png_files(self) -> None:
    names = ("plan", "materials", "products")
    for name in names:
        for suffix in ("", "-active"):
            path = ROOT / f"miniprogram/assets/tabbar/{name}{suffix}.png"
            self.assertTrue(path.exists())
            self.assertLess(path.stat().st_size, 40 * 1024)
```

- [ ] **Step 2: Run tests and confirm new navigation/page failures**

Run:

```bash
.venv/bin/python -m pytest tests/test_miniprogram_foundation.py -q
```

Expected: manifest and missing-page assertions fail.

- [ ] **Step 3: Generate one-family tab bar assets**

Use the icon IDs selected through `better-icons`:

```bash
npx --yes better-icons get lucide:clipboard-list --color '#64748B' --size 81 > /tmp/plan.svg
npx --yes better-icons get lucide:layers --color '#64748B' --size 81 > /tmp/materials.svg
npx --yes better-icons get lucide:package-check --color '#64748B' --size 81 > /tmp/products.svg
npx --yes better-icons get lucide:clipboard-list --color '#334155' --size 81 > /tmp/plan-active.svg
npx --yes better-icons get lucide:layers --color '#334155' --size 81 > /tmp/materials-active.svg
npx --yes better-icons get lucide:package-check --color '#334155' --size 81 > /tmp/products-active.svg
.venv/bin/pip install CairoSVG==2.7.1
.venv/bin/cairosvg /tmp/plan.svg -o miniprogram/assets/tabbar/plan.png
.venv/bin/cairosvg /tmp/materials.svg -o miniprogram/assets/tabbar/materials.png
.venv/bin/cairosvg /tmp/products.svg -o miniprogram/assets/tabbar/products.png
.venv/bin/cairosvg /tmp/plan-active.svg -o miniprogram/assets/tabbar/plan-active.png
.venv/bin/cairosvg /tmp/materials-active.svg -o miniprogram/assets/tabbar/materials-active.png
.venv/bin/cairosvg /tmp/products-active.svg -o miniprogram/assets/tabbar/products-active.png
file miniprogram/assets/tabbar/plan.png miniprogram/assets/tabbar/materials.png miniprogram/assets/tabbar/products.png
```

The `better-icons` commands create SVGs with width and height set to 81, so CairoSVG preserves 81×81 output. Confirm `file` reports 81×81 PNGs and the static test confirms every file is below 40KB. The committed mini program must not read the temporary SVG files; CairoSVG is a one-time developer conversion tool and is not added to `requirements.txt`.

- [ ] **Step 4: Register the exact navigation structure**

Set the beginning of `pages` to:

```json
[
  "pages/connection/index",
  "pages/plan/home",
  "pages/materials/home",
  "pages/products/home",
  "pages/materials/steel-home",
  "pages/materials/paper-home",
  "pages/inventory/list",
  "pages/inventory/inbound",
  "pages/inventory/outbound",
  "pages/inventory/transactions",
  "pages/scraps/home",
  "pages/scraps/pending",
  "pages/scraps/list",
  "pages/scraps/outbound",
  "pages/scraps/transactions"
]
```

Set `tabBar.list` to exactly:

```json
[
  {
    "pagePath": "pages/plan/home",
    "text": "计划",
    "iconPath": "assets/tabbar/plan.png",
    "selectedIconPath": "assets/tabbar/plan-active.png"
  },
  {
    "pagePath": "pages/materials/home",
    "text": "材料",
    "iconPath": "assets/tabbar/materials.png",
    "selectedIconPath": "assets/tabbar/materials-active.png"
  },
  {
    "pagePath": "pages/products/home",
    "text": "成品",
    "iconPath": "assets/tabbar/products.png",
    "selectedIconPath": "assets/tabbar/products-active.png"
  }
]
```

Use `#64748B` inactive, `#334155` active, and `#FFFFFF` background.

- [ ] **Step 5: Implement the connection page state machine**

The page data and public methods must be:

```javascript
const api = require('../../utils/api')
const connection = require('../../utils/connection')

Page({
  data: {
    state: 'checking',
    baseUrl: '',
    ip: '',
    port: '8000',
    errorTitle: '',
    errorDescription: '',
    manualOpen: false
  },

  onShow() { this.trySavedConnection() },

  async testConnection(baseUrl) {
    api.configureBaseUrl(baseUrl)
    const health = await api.health()
    if (!health || health.status !== 'ok') throw new Error('后台健康检查未通过')
    connection.saveBaseUrl(wx, baseUrl)
    getApp().globalData.connectionState = 'connected'
    wx.switchTab({ url: '/pages/plan/home' })
  },

  async trySavedConnection() {
    let saved = ''
    try { saved = connection.loadSavedBaseUrl(wx) } catch (error) { saved = '' }
    if (!saved) {
      this.setData({ state: 'setup' })
      return
    }
    this.setData({ state: 'checking', baseUrl: saved })
    try {
      await this.testConnection(saved)
    } catch (error) {
      this.setData({
        state: 'error',
        errorTitle: '无法连接厂内库存系统',
        errorDescription: '请连接工厂 Wi-Fi，并确认后台电脑已经启动。'
      })
    }
  },

  async scanBaseUrl() {
    this.setData({ state: 'checking' })
    try {
      const baseUrl = await connection.scanBaseUrl(wx)
      await this.testConnection(baseUrl)
    } catch (error) {
      this.setData({ state: 'error', errorTitle: '连接二维码无效', errorDescription: error.message })
    }
  },

  openManual() { this.setData({ manualOpen: true, state: 'setup' }) },
  onIp(event) { this.setData({ ip: event.detail.value }) },
  onPort(event) { this.setData({ port: event.detail.value }) },

  async testAndSave() {
    try {
      const baseUrl = connection.normalizeBaseUrl(`http://${this.data.ip}:${this.data.port}`)
      this.setData({ state: 'checking', baseUrl })
      await this.testConnection(baseUrl)
    } catch (error) {
      this.setData({ state: 'error', errorTitle: '手工地址无法连接', errorDescription: error.message })
    }
  }
})
```

Register `state-view` in `pages/connection/index.json` and use this WXML:

```xml
<view class="container connection-page">
  <view class="page-heading">
    <view class="page-title">连接厂内库存系统</view>
    <view class="page-subtitle">手机与后台电脑需要连接同一个工厂 Wi-Fi</view>
  </view>

  <state-view wx:if="{{state === 'checking'}}" state="loading" title="正在检查连接" description="{{baseUrl}}" />

  <view class="card" wx:if="{{state === 'setup' || state === 'error'}}">
    <view class="error-box" wx:if="{{state === 'error'}}">
      <view class="error-title">{{errorTitle}}</view>
      <view class="error-description">{{errorDescription}}</view>
    </view>
    <button class="btn" bindtap="scanBaseUrl">扫描电脑连接二维码</button>
    <button class="btn secondary" bindtap="openManual">手工设置地址</button>
  </view>

  <view class="card" wx:if="{{manualOpen}}">
    <view class="field-label">电脑局域网 IP</view>
    <input class="input" value="{{ip}}" placeholder="例如 192.168.31.68" bindinput="onIp" />
    <view class="field-label">端口</view>
    <input class="input" type="number" value="{{port}}" bindinput="onPort" />
    <button class="btn" bindtap="testAndSave">测试并保存</button>
  </view>

  <view class="card help-card">
    <view>1. 连接工厂 Wi-Fi</view>
    <view>2. 确认后台电脑已经开机并启动服务</view>
    <view>3. 网络变化后在电脑后台重新生成二维码</view>
  </view>
</view>
```

Use `padding-top: 72rpx` for `.connection-page`, `margin-bottom: 48rpx` for `.page-heading`, and `16rpx` vertical gaps inside `.help-card`; use only global color and button classes.

- [ ] **Step 6: Implement the three top-level shells**

Use this guard in every top-level page's `onShow()` before loading data:

```javascript
if (getApp().globalData.connectionState !== 'connected') {
  wx.reLaunch({ url: '/pages/connection/index' })
  return
}
```

Implement `pages/plan/home.js` and `home.wxml` exactly as the phase shell, without fake results:

```javascript
Page({
  data: { connectionState: 'connected', baseUrl: '' },
  onShow() {
    if (getApp().globalData.connectionState !== 'connected') {
      wx.reLaunch({ url: '/pages/connection/index' })
      return
    }
    this.setData({ baseUrl: getApp().globalData.baseUrl })
  },
  configure() { wx.reLaunch({ url: '/pages/connection/index' }) }
})
```

```xml
<view class="container">
  <connection-status state="connected" base-url="{{baseUrl}}" bind:configure="configure" />
  <view class="page-heading"><view class="page-title">计划查料</view><view class="page-subtitle">按计划检查成品、余料和钢板库存</view></view>
  <view class="card phase-card">
    <view class="section-title">计划匹配</view>
    <view class="meta">第二阶段开放计划筛选与库存匹配</view>
  </view>
</view>
```

Implement `pages/materials/home.js`:

```javascript
const api = require('../../utils/api')

Page({
  data: { loading: true, error: '', pendingScrapCount: 0, baseUrl: '' },
  onShow() {
    if (getApp().globalData.connectionState !== 'connected') {
      wx.reLaunch({ url: '/pages/connection/index' })
      return
    }
    this.setData({ baseUrl: getApp().globalData.baseUrl })
    this.load()
  },
  async load() {
    this.setData({ loading: true, error: '' })
    try {
      const summary = await api.summary()
      this.setData({ pendingScrapCount: summary.pending_scrap_count || 0 })
    } catch (error) {
      this.setData({ error: error.message || '材料概览加载失败' })
    } finally {
      this.setData({ loading: false })
    }
  },
  go(event) { wx.navigateTo({ url: event.currentTarget.dataset.url }) },
  configure() { wx.reLaunch({ url: '/pages/connection/index' }) }
})
```

```xml
<view class="container">
  <connection-status state="{{error ? 'error' : 'connected'}}" base-url="{{baseUrl}}" bind:retry="load" bind:configure="configure" />
  <view class="page-heading"><view class="page-title">材料</view><view class="page-subtitle">钢板、余料和纸材统一入口</view></view>
  <state-view wx:if="{{loading}}" state="loading" title="正在读取材料概览" />
  <state-view wx:elif="{{error}}" state="error" title="材料概览加载失败" description="{{error}}" bind:retry="load" />
  <view wx:else class="module-list">
    <view class="card module-card" bindtap="go" data-url="/pages/materials/steel-home"><view class="module-name">钢板</view><view class="meta">规格、入库、出库、库存和流水</view></view>
    <view class="card module-card" bindtap="go" data-url="/pages/scraps/home"><view class="module-name">余料</view><view class="warning-text">{{pendingScrapCount}} 条待确认</view></view>
    <view class="card module-card" bindtap="go" data-url="/pages/materials/paper-home"><view class="module-name">纸材</view><view class="meta">纸圈和纸张统一管理</view></view>
  </view>
</view>
```

Implement `pages/products/home.js`:

```javascript
const api = require('../../utils/api')

Page({
  data: { loading: true, error: '', quantity: 0, baseUrl: '' },
  onShow() {
    if (getApp().globalData.connectionState !== 'connected') {
      wx.reLaunch({ url: '/pages/connection/index' })
      return
    }
    this.setData({ baseUrl: getApp().globalData.baseUrl })
    this.load()
  },
  async load() {
    this.setData({ loading: true, error: '' })
    try {
      const summary = await api.summary()
      this.setData({ quantity: summary.product_available_quantity || 0 })
    } catch (error) {
      this.setData({ error: error.message || '成品概览加载失败' })
    } finally {
      this.setData({ loading: false })
    }
  },
  go(event) { wx.navigateTo({ url: event.currentTarget.dataset.url }) },
  configure() { wx.reLaunch({ url: '/pages/connection/index' }) }
})
```

```xml
<view class="container">
  <connection-status state="{{error ? 'error' : 'connected'}}" base-url="{{baseUrl}}" bind:retry="load" bind:configure="configure" />
  <view class="page-heading"><view class="page-title">成品</view><view class="page-subtitle">库存与出入库操作</view></view>
  <state-view wx:if="{{loading}}" state="loading" title="正在读取成品概览" />
  <state-view wx:elif="{{error}}" state="error" title="成品概览加载失败" description="{{error}}" bind:retry="load" />
  <block wx:else>
    <view class="card"><view class="stat-label">当前可用数量</view><view class="stat-value">{{quantity}}</view></view>
    <view class="module-list">
      <view class="card module-card" bindtap="go" data-url="/pages/inventory/list"><view class="module-name">成品库存</view></view>
      <view class="card module-card" bindtap="go" data-url="/pages/inventory/inbound"><view class="module-name">成品入库</view></view>
      <view class="card module-card" bindtap="go" data-url="/pages/inventory/outbound"><view class="module-name">成品出库</view></view>
      <view class="card module-card" bindtap="go" data-url="/pages/inventory/transactions"><view class="module-name">成品流水</view></view>
      <view class="card module-card is-disabled"><view class="module-name">出入库分析</view><view class="meta">第六阶段开放</view></view>
    </view>
  </block>
</view>
```

Each top-level page JSON uses its own title and these absolute component paths:

```json
{
  "navigationBarTitleText": "材料",
  "usingComponents": {
    "connection-status": "/components/connection-status/index",
    "state-view": "/components/state-view/index"
  }
}
```

Use `计划查料`, `材料`, and `成品` as the three respective title values. Their page WXSS files contain only `.page-heading`, `.page-title`, `.page-subtitle`, `.module-list`, `.module-card`, `.module-name`, `.warning-text`, and `.is-disabled` layout rules; use global colors and keep `.module-card` at least `120rpx` tall.

Use this complete `steel-home.js` and duplicate the same code in `paper-home.js`:

```javascript
Page({
  data: { baseUrl: '' },
  onShow() {
    if (getApp().globalData.connectionState !== 'connected') {
      wx.reLaunch({ url: '/pages/connection/index' })
      return
    }
    this.setData({ baseUrl: getApp().globalData.baseUrl })
  },
  configure() { wx.reLaunch({ url: '/pages/connection/index' }) }
})
```

Use these exact views:

```xml
<!-- steel-home.wxml -->
<view class="container">
  <connection-status state="connected" base-url="{{baseUrl}}" bind:configure="configure" />
  <view class="page-heading"><view class="page-title">钢板材料</view></view>
  <view class="card phase-card"><view class="section-title">钢板材料管理</view><view class="meta">第三阶段开放钢板材料完整功能</view></view>
</view>
```

```xml
<!-- paper-home.wxml -->
<view class="container">
  <connection-status state="connected" base-url="{{baseUrl}}" bind:configure="configure" />
  <view class="page-heading"><view class="page-title">纸材材料</view></view>
  <view class="card phase-card"><view class="section-title">纸材材料管理</view><view class="meta">第四阶段开放纸材材料完整功能</view></view>
</view>
```

Both JSON files register only `connection-status`; their navigation titles are `钢板材料` and `纸材材料`. Both WXSS files set `.phase-card { min-height: 180rpx; }` and add no new color values. Neither page contains write controls.

- [ ] **Step 7: Run static and JavaScript tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_miniprogram_foundation.py -q
node --test tests/miniprogram_connection.test.js
```

Expected: both commands exit 0; manifest contains exactly three tabs and all six PNG assets satisfy the size limit.

- [ ] **Step 8: Commit**

```bash
git add miniprogram/app.json miniprogram/pages/connection miniprogram/pages/plan miniprogram/pages/materials miniprogram/pages/products miniprogram/assets/tabbar tests/test_miniprogram_foundation.py
git commit -m "feat: add mini program three-tab shell"
```

---

### Task 6: Integration verification and factory setup documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-04-wechat-mini-program-phase-1-foundation.md` only to check completed boxes during execution.

**Interfaces:**
- Consumes: all phase 1 backend and mini-program interfaces.
- Produces: a repeatable startup and recovery procedure for the customer computer and phone.

- [x] **Step 1: Add exact factory startup and connection instructions**

Add a `微信小程序（厂内 Wi-Fi）` section to `README.md` containing these commands and actions:

```bash
cd /Users/luck/Desktop/杭州特耐时/backend
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Document this user flow:

1. Computer opens `http://127.0.0.1:8000/admin/mobile-connection`.
2. Phone connects to the same factory Wi-Fi.
3. Mini program scans the displayed QR code.
4. If router/computer IP changes, regenerate and rescan the QR code.
5. If connection fails, verify the backend process, Windows firewall port 8000, and Wi-Fi before changing business data.

Also document that WeChat Developer Tools keeps `不校验合法域名` enabled for this LAN-only development phase and that a real-device build must permit local network access.

- [x] **Step 2: Run focused backend tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mobile_connection.py tests/test_mobile_idempotency.py tests/test_miniprogram_foundation.py -q
```

Expected: all phase 1 backend/static tests pass with zero failures.

- [x] **Step 3: Run pure mini-program utility tests**

Run:

```bash
node --test tests/miniprogram_connection.test.js
```

Expected: all connection and persistent request-ID subtests pass with zero failures.

- [x] **Step 4: Run the complete existing Python regression suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass; no existing desktop steel, paper, scrap, product, drawing, export, or sorting regression fails.

- [x] **Step 5: Verify import, schema startup, and working tree**

Run:

```bash
.venv/bin/python -c "from app.main import app; print(app.version)"
.venv/bin/python -c "from app.database import engine; from app.schema_migrations import ensure_runtime_schema; ensure_runtime_schema(engine); ensure_runtime_schema(engine); print('schema-ok')"
git diff --check
```

Expected output includes `0.1.0`, `schema-ok`, and no `git diff --check` errors.

- [x] **Step 6: Perform WeChat Developer Tools visual acceptance**

Use these exact viewport checks:

1. iPhone 12/13 width: no horizontal scroll; all three tab labels fit; connection primary action is above the safe area.
2. Common Android width: 88rpx touch targets; cards do not clip model/size text.
3. Landscape: content remains operable and fixed navigation does not cover the final card.
4. With no saved URL: connection setup appears and no inventory write control is visible.
5. With valid QR: health check succeeds and switches to `计划`.
6. With backend stopped: clear failure explanation and `重新连接`/`修改地址` appear.
7. Double-tap a current product or scrap write action: the button locks and the backend produces one inventory mutation.

Capture one screenshot for the connection page and one screenshot for each of the three top-level tabs for review.

Acceptance evidence captured locally under `.codex-artifacts/miniprogram-phase1/`: connection setup, connection error/recovery, plan, materials, products, and product-inbound confirmation sheet. The simulator connected to `192.168.10.225:8000`, loaded live summary data, recovered through the new `重新连接` action after a deliberate backend stop, and reported zero console errors while switching the three tabs.

- [x] **Step 7: Commit documentation and review corrections**

```bash
git add README.md docs/superpowers/plans/2026-08-04-wechat-mini-program-phase-1-foundation.md
git commit -m "docs: add mini program factory setup"
```

- [ ] **Step 8: Push phase 1 only after all verification evidence is fresh**

```bash
git push origin main
git status --short
git rev-parse HEAD
git ls-remote origin refs/heads/main
```

Expected: working tree is clean and local `HEAD` equals the remote `main` SHA.

## Self-Review Record

- Spec coverage: connection QR/manual recovery, three-tab navigation, common states, industrial mobile styling, duplicate-submit protection, customer-database migration, docs, and real-device acceptance all map to a numbered task.
- Scope control: plan lookup, steel, paper, finished-product analysis, permissions, cloud hosting, and offline writes are not implemented in phase 1; their entry cards are explicit shells only.
- Type consistency: QR payload uses `version` and `base_url` on both Python and JavaScript; health uses `status`, `app_name`, and `app_version`; write requests use `client_request_id` everywhere.
- Data safety: idempotency record and inventory mutation share one SQLAlchemy transaction; reused IDs with altered data return HTTP 409; existing inventory rows are not rewritten.
- Placeholder scan: the plan contains no TBD/TODO markers and no unspecified error-handling steps.
