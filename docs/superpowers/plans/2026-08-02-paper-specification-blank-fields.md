# Paper Specification Blank Fields Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow paper-roll and paper-sheet specifications to save when fields belonging to the inactive paper type are blank.

**Architecture:** Keep the existing paper specification normalization service as the source of dimensional rules. The browser will disable inactive controls, while both create and edit endpoints will normalize optional form strings into `float | None` before calling the service.

**Tech Stack:** Python 3.12, FastAPI forms, server-rendered HTML/JavaScript, SQLAlchemy, unittest

## Global Constraints

- Do not change the database schema or historical paper inventory data.
- Apply the same behavior to both specification creation and editing.
- Keep existing paper-roll and paper-sheet validation rules unchanged.

---

### Task 1: Make inactive paper dimensions safe to submit

**Files:**
- Modify: `app/paper_admin_pages.py:42-207`
- Test: `tests/test_paper_material_management.py:250-330`

**Interfaces:**
- Consumes: `normalize_paper_specification(paper_type, model, material_name, thickness, inner_diameter, outer_diameter, length, width) -> dict[str, object]`
- Produces: `_optional_form_float(value: str | float | None, label: str) -> float | None`

- [ ] **Step 1: Write failing regression tests**

Add tests that call the create endpoint with the same empty inactive dimensions sent by the browser, cover the symmetric paper-sheet case, and verify the rendered script disables inactive controls:

```python
def test_specification_endpoints_accept_blank_inactive_dimensions(self) -> None:
    with self.Session() as db:
        create_paper_specification(
            paper_type="roll",
            model="TNX26801.2A",
            material_name="绿色（DLZ001-1）",
            thickness=0.5,
            inner_diameter="52.5",
            outer_diameter="82.2",
            length="",
            width="",
            remark="",
            db=db,
        )
        create_paper_specification(
            paper_type="sheet",
            model="",
            material_name="白纸",
            thickness=0.5,
            inner_diameter="",
            outer_diameter="",
            length="400",
            width="400",
            remark="",
            db=db,
        )
        specs = db.query(PaperSpecification).order_by(PaperSpecification.id).all()

    self.assertEqual(specs[0].model, "TNX26801.2A")
    self.assertIsNone(specs[0].length)
    self.assertIsNone(specs[0].width)
    self.assertEqual(specs[1].model, "0.5×400×400")
    self.assertIsNone(specs[1].inner_diameter)
    self.assertIsNone(specs[1].outer_diameter)

def test_specification_script_disables_inactive_fields(self) -> None:
    with self.Session() as db:
        html = paper_specifications_page(db=db).body.decode("utf-8")

    self.assertIn("input.disabled = !isRoll", html)
    self.assertIn("input.disabled = isRoll", html)
```

- [ ] **Step 2: Run the tests and confirm the current implementation fails**

Run:

```bash
.venv/bin/python -m unittest \
  tests.test_paper_material_management.PaperSpecificationAndInboundPagesTest.test_specification_endpoints_accept_blank_inactive_dimensions \
  tests.test_paper_material_management.PaperSpecificationAndInboundPagesTest.test_specification_script_disables_inactive_fields -v
```

Expected: FAIL because empty strings reach numeric comparison and the script does not set `disabled`.

- [ ] **Step 3: Add optional form-number normalization**

Add this helper near `_apply_spec_values`:

```python
def _optional_form_float(value: str | float | None, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{label}必须是数字") from exc
```

Change `inner_diameter`, `outer_diameter`, `length`, and `width` in both POST endpoint signatures to `str = Form("")`. Before calling `normalize_paper_specification`, convert them with `_optional_form_float`, using labels `内径`, `外径`, `长度`, and `宽度`.

- [ ] **Step 4: Disable inactive browser controls**

In `PAPER_SPEC_SCRIPT`, set disabled state in the existing field loops:

```javascript
input.disabled = !isRoll;
```

for `.paper-roll-fields`, and:

```javascript
input.disabled = isRoll;
```

for `.paper-sheet-fields`.

- [ ] **Step 5: Run focused and full regression tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_paper_material_management -v
.venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 6: Verify the real HTTP form path**

Start the app on an isolated temporary port/database and submit the reported roll values with blank length and width:

```bash
DATABASE_URL='sqlite:////tmp/hztns-paper-fix.db' \
UPLOAD_DIR='/tmp/hztns-paper-uploads' \
DRAWING_PREVIEW_DIR='/tmp/hztns-paper-previews' \
QRCODE_DIR='/tmp/hztns-paper-qrcodes' \
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18123

curl -i -X POST 'http://127.0.0.1:18123/admin/paper-specifications' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'paper_type=roll' \
  --data-urlencode 'model=TNX26801.2A' \
  --data-urlencode 'material_name=绿色（DLZ001-1）' \
  --data-urlencode 'thickness=0.5' \
  --data-urlencode 'inner_diameter=52.5' \
  --data-urlencode 'outer_diameter=82.2' \
  --data-urlencode 'length=' \
  --data-urlencode 'width=' \
  --data-urlencode 'remark='
```

Expected: `HTTP/1.1 303 See Other` with `location: /admin/paper-specifications`.

- [ ] **Step 7: Commit the verified fix**

```bash
git add app/paper_admin_pages.py tests/test_paper_material_management.py
git commit -m "fix: accept blank inactive paper dimensions"
```
