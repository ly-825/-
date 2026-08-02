import asyncio
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from fastapi.dependencies.utils import request_body_to_args
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import FormData

import app.services.paper_inventory as paper_inventory_service
from app import paper_admin_pages
from app.database import Base
from app.models import PaperInventoryBatch, PaperInventoryTransaction, PaperSpecification
from app.paper_admin_pages import (
    create_paper_inbound,
    create_paper_specification,
    outbound_paper_from_page,
    paper_group_detail_page,
    paper_inbound_page,
    paper_inventory_page,
    paper_outbound_page,
    paper_specifications_page,
    paper_transactions_page,
    reverse_paper_transaction_from_page,
    toggle_paper_specification,
    update_paper_specification,
)
from app.admin_pages import page
from app.schema_migrations import ensure_runtime_schema
from app.services.paper_inventory import (
    normalize_paper_specification,
    outbound_paper_fifo,
    paper_inventory_groups,
    paper_specification_sort_key,
    reverse_paper_transaction,
)


class PaperMaterialSchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def test_paper_models_use_three_independent_tables_and_decimal_price(self) -> None:
        table_names = set(inspect(self.engine).get_table_names())
        self.assertIn("paper_specifications", table_names)
        self.assertIn("paper_inventory_batches", table_names)
        self.assertIn("paper_inventory_transactions", table_names)

        with self.Session() as db:
            spec = PaperSpecification(
                paper_type="roll",
                model="Tnx236.2A",
                material_name="蓝纸",
                thickness=0.5,
                inner_diameter=80,
                outer_diameter=120,
                is_active=1,
            )
            db.add(spec)
            db.flush()
            batch = PaperInventoryBatch(
                specification_id=spec.id,
                batch_code="PAPER-001",
                paper_type="roll",
                model=spec.model,
                material_name=spec.material_name,
                thickness=spec.thickness,
                inner_diameter=spec.inner_diameter,
                outer_diameter=spec.outer_diameter,
                quantity=20,
                unit_price=Decimal("12.30"),
                status="available",
            )
            db.add(batch)
            db.flush()
            db.add(
                PaperInventoryTransaction(
                    inventory_id=batch.id,
                    transaction_type="in",
                    quantity=20,
                    before_quantity=0,
                    after_quantity=20,
                )
            )
            db.commit()
            db.refresh(batch)

            self.assertEqual(batch.unit_price, Decimal("12.30"))
            self.assertEqual(batch.specification_id, spec.id)

    def test_runtime_schema_creates_paper_tables_idempotently(self) -> None:
        engine = create_engine("sqlite:///:memory:")

        ensure_runtime_schema(engine)
        ensure_runtime_schema(engine)

        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        self.assertTrue(
            {
                "paper_specifications",
                "paper_inventory_batches",
                "paper_inventory_transactions",
            }.issubset(tables)
        )
        batch_indexes = {index["name"] for index in inspector.get_indexes("paper_inventory_batches")}
        self.assertIn("ix_paper_inventory_batches_model", batch_indexes)
        self.assertIn("ix_paper_inventory_batches_status", batch_indexes)


class PaperInventoryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def test_normalization_generates_sheet_model_and_validates_roll_diameters(self) -> None:
        sheet = normalize_paper_specification(
            "sheet", "ignored", "白纸", 0.5, None, None, 400, 400
        )

        self.assertEqual(sheet["model"], "0.5×400×400")
        self.assertIsNone(sheet["inner_diameter"])
        with self.assertRaisesRegex(HTTPException, "外径必须大于内径"):
            normalize_paper_specification(
                "roll", "Tnx236.2A", "蓝纸", 0.5, 120, 80, None, None
            )

    def test_group_price_range_excludes_zero_stock_batches(self) -> None:
        base_time = datetime(2026, 7, 1, 8, 0)
        batches = [
            PaperInventoryBatch(
                specification_id=1,
                batch_code="P-1",
                paper_type="roll",
                model="Tnx236.2A",
                material_name="蓝纸",
                thickness=0.5,
                inner_diameter=80,
                outer_diameter=120,
                quantity=2,
                unit_price=Decimal("10.00"),
                status="available",
                created_at=base_time,
                updated_at=base_time,
            ),
            PaperInventoryBatch(
                specification_id=1,
                batch_code="P-2",
                paper_type="roll",
                model="Tnx236.2A",
                material_name="蓝纸",
                thickness=0.5,
                inner_diameter=80,
                outer_diameter=120,
                quantity=5,
                unit_price=Decimal("12.50"),
                status="available",
                created_at=base_time + timedelta(minutes=1),
                updated_at=base_time + timedelta(minutes=1),
            ),
            PaperInventoryBatch(
                specification_id=1,
                batch_code="P-OLD",
                paper_type="roll",
                model="Tnx236.2A",
                material_name="蓝纸",
                thickness=0.5,
                inner_diameter=80,
                outer_diameter=120,
                quantity=0,
                unit_price=Decimal("99.00"),
                status="used",
                created_at=base_time - timedelta(minutes=1),
                updated_at=base_time - timedelta(minutes=1),
            ),
        ]

        groups = paper_inventory_groups(batches)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["quantity"], 7)
        self.assertEqual(groups[0]["price_min"], Decimal("10.00"))
        self.assertEqual(groups[0]["price_max"], Decimal("12.50"))
        self.assertEqual(groups[0]["unit"], "圈")

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

    def test_paper_inventory_keeps_3969_3960_and_3970_separate(self) -> None:
        def batch(
            specification_id: int,
            batch_code: str,
            model: str,
            inner_diameter: float,
            outer_diameter: float,
            quantity: int,
            unit_price: str,
        ) -> PaperInventoryBatch:
            return PaperInventoryBatch(
                specification_id=specification_id,
                batch_code=batch_code,
                paper_type="roll",
                model=model,
                material_name="黑色（DC0017）",
                thickness=0.5,
                inner_diameter=inner_diameter,
                outer_diameter=outer_diameter,
                quantity=quantity,
                unit_price=Decimal(unit_price),
                status="available",
            )

        batches = [
            batch(101, "P-3969-1", "3969.01", 55, 115, 100, "0.66"),
            batch(102, "P-3960-1", "3960.02", 67, 127, 200, "0.75"),
            batch(103, "P-3970-1", "3970.01", 80, 132, 300, "0.70"),
            batch(101, "P-3969-2", "3969.01", 55, 115, 50, "0.68"),
        ]

        groups = paper_inventory_groups(batches)

        self.assertEqual(
            [group["model"] for group in groups],
            ["3969.01", "3960.02", "3970.01"],
        )
        self.assertEqual([group["quantity"] for group in groups], [150, 200, 300])
        self.assertEqual([group["batch_count"] for group in groups], [2, 1, 1])
        self.assertEqual(groups[0]["price_min"], Decimal("0.66"))
        self.assertEqual(groups[0]["price_max"], Decimal("0.68"))

    def test_paper_sorting_is_globally_thickness_first(self) -> None:
        specs = [
            PaperSpecification(
                id=1,
                paper_type="roll",
                model="ROLL-2.0",
                material_name="厚纸圈",
                thickness=2.0,
                inner_diameter=50,
                outer_diameter=80,
            ),
            PaperSpecification(
                id=2,
                paper_type="sheet",
                model="0.5×400×400",
                material_name="大纸张",
                thickness=0.5,
                length=400,
                width=400,
            ),
            PaperSpecification(
                id=3,
                paper_type="roll",
                model="ROLL-0.5-80",
                material_name="大纸圈",
                thickness=0.5,
                inner_diameter=80,
                outer_diameter=120,
            ),
            PaperSpecification(
                id=4,
                paper_type="roll",
                model="ROLL-0.5-60",
                material_name="小纸圈",
                thickness=0.5,
                inner_diameter=60,
                outer_diameter=100,
            ),
            PaperSpecification(
                id=5,
                paper_type="sheet",
                model="0.5×300×400",
                material_name="小纸张",
                thickness=0.5,
                length=300,
                width=400,
            ),
        ]
        expected_models = [
            "ROLL-0.5-60",
            "ROLL-0.5-80",
            "0.5×300×400",
            "0.5×400×400",
            "ROLL-2.0",
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
        ordered_groups = paper_inventory_groups(batches)

        self.assertEqual([spec.model for spec in ordered_specs], expected_models)
        self.assertEqual([group["model"] for group in ordered_groups], expected_models)

    def test_fifo_is_atomic_and_reversal_restores_only_affected_batch(self) -> None:
        with self.Session() as db:
            spec = PaperSpecification(
                paper_type="roll",
                model="Tnx236.2A",
                material_name="蓝纸",
                thickness=0.5,
                inner_diameter=80,
                outer_diameter=120,
                is_active=1,
            )
            db.add(spec)
            db.flush()
            first = PaperInventoryBatch(
                specification_id=spec.id,
                batch_code="P-1",
                paper_type="roll",
                model=spec.model,
                material_name=spec.material_name,
                thickness=spec.thickness,
                inner_diameter=80,
                outer_diameter=120,
                quantity=2,
                unit_price=Decimal("10.00"),
                status="available",
                created_at=datetime(2026, 7, 1, 8, 0),
            )
            second = PaperInventoryBatch(
                specification_id=spec.id,
                batch_code="P-2",
                paper_type="roll",
                model=spec.model,
                material_name=spec.material_name,
                thickness=spec.thickness,
                inner_diameter=80,
                outer_diameter=120,
                quantity=5,
                unit_price=Decimal("12.50"),
                status="available",
                created_at=datetime(2026, 7, 1, 8, 1),
            )
            db.add_all([first, second])
            db.commit()

            records = outbound_paper_fifo(spec.id, 4, None, "一车间", "张三", "领用", db)
            db.flush()

            self.assertEqual((first.quantity, second.quantity), (0, 3))
            self.assertEqual([record.quantity for record in records], [2, 2])
            before_failed_outbound = (first.quantity, second.quantity)
            with self.assertRaisesRegex(HTTPException, "库存不足"):
                outbound_paper_fifo(spec.id, 4, None, None, None, None, db)
            self.assertEqual((first.quantity, second.quantity), before_failed_outbound)

            original_price = first.unit_price
            reversal = reverse_paper_transaction(records[0].id, "李四", "撤回测试", db)
            db.flush()

            self.assertEqual(first.quantity, 2)
            self.assertEqual(second.quantity, 3)
            self.assertEqual(first.unit_price, original_price)
            self.assertEqual(reversal.reversed_transaction_id, records[0].id)


class PaperSpecificationAndInboundPagesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def test_navigation_has_independent_paper_material_section(self) -> None:
        html = page("测试", "").body.decode("utf-8")

        steel = html.index("<summary>钢板材料管理</summary>")
        paper = html.index("<summary>纸材材料管理</summary>")
        links = [
            html.index('href="/admin/paper-specifications">纸材规格'),
            html.index('href="/admin/paper-materials/inbound">纸材入库'),
            html.index('href="/admin/paper-materials/outbound">纸材出库'),
            html.index('href="/admin/paper-materials">纸材库存'),
            html.index('href="/admin/paper-materials/transactions">纸材流水'),
        ]

        self.assertLess(steel, paper)
        self.assertEqual(links, sorted(links))

    def test_specification_page_switches_fields_and_generates_sheet_model(self) -> None:
        with self.Session() as db:
            create_paper_specification(
                paper_type="sheet",
                model="不能采用这个名字",
                material_name="白纸",
                thickness=0.5,
                inner_diameter=None,
                outer_diameter=None,
                length=400,
                width=400,
                remark="",
                db=db,
            )
            create_paper_specification(
                paper_type="roll",
                model="Tnx236.2A",
                material_name="蓝纸",
                thickness=0.6,
                inner_diameter=80,
                outer_diameter=120,
                length=None,
                width=None,
                remark="",
                db=db,
            )
            html = paper_specifications_page(db=db).body.decode("utf-8")
            models = {spec.model for spec in db.query(PaperSpecification).all()}
            roll = db.query(PaperSpecification).filter_by(paper_type="roll").one()
            update_paper_specification(
                spec_id=roll.id,
                paper_type="sheet",
                model="ignored-again",
                material_name="蓝纸",
                thickness=0.8,
                inner_diameter=None,
                outer_diameter=None,
                length=500,
                width=300,
                is_active=1,
                remark="改为纸张",
                db=db,
            )
            toggle_paper_specification(roll.id, db=db)
            db.refresh(roll)

        self.assertEqual(models, {"0.5×400×400", "Tnx236.2A"})
        self.assertIn('name="paper_type"', html)
        self.assertIn('class="paper-roll-fields"', html)
        self.assertIn('class="paper-sheet-fields"', html)
        self.assertIn("0.5×400×400", html)
        self.assertEqual(roll.model, "0.8×500×300")
        self.assertEqual(roll.is_active, 0)

    def test_specification_forms_accept_blank_inactive_dimensions(self) -> None:
        route_paths = (
            "/admin/paper-specifications",
            "/admin/paper-specifications/{spec_id}/edit",
        )
        payloads = (
            FormData(
                [
                    ("paper_type", "roll"),
                    ("model", "TNX26801.2A"),
                    ("material_name", "绿色（DLZ001-1）"),
                    ("thickness", "0.5"),
                    ("inner_diameter", "52.5"),
                    ("outer_diameter", "82.2"),
                    ("length", ""),
                    ("width", ""),
                    ("remark", ""),
                ]
            ),
            FormData(
                [
                    ("paper_type", "sheet"),
                    ("model", ""),
                    ("material_name", "白纸"),
                    ("thickness", "0.5"),
                    ("inner_diameter", ""),
                    ("outer_diameter", ""),
                    ("length", "400"),
                    ("width", "400"),
                    ("remark", ""),
                ]
            ),
        )

        for route_path in route_paths:
            route = next(
                route
                for route in paper_admin_pages.router.routes
                if route.path == route_path and "POST" in route.methods
            )
            for payload in payloads:
                with self.subTest(route=route_path, paper_type=payload["paper_type"]):
                    _, errors = asyncio.run(
                        request_body_to_args(route.dependant.body_params, payload, False)
                    )
                    self.assertEqual(errors, [])

    def test_specification_script_disables_inactive_fields(self) -> None:
        with self.Session() as db:
            html = paper_specifications_page(db=db).body.decode("utf-8")

        self.assertIn("input.disabled = !isRoll", html)
        self.assertIn("input.disabled = isRoll", html)

    def test_specification_endpoints_normalize_blank_inactive_dimensions(self) -> None:
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
        self.assertEqual(specs[0].inner_diameter, 52.5)
        self.assertIsNone(specs[0].length)
        self.assertIsNone(specs[0].width)
        self.assertEqual(specs[1].model, "0.5×400×400")
        self.assertIsNone(specs[1].inner_diameter)
        self.assertIsNone(specs[1].outer_diameter)

    def test_inbound_saves_quantity_price_snapshot_and_transaction(self) -> None:
        with self.Session() as db:
            spec = PaperSpecification(
                paper_type="roll",
                model="Tnx236.2A",
                material_name="蓝纸",
                thickness=0.5,
                inner_diameter=80,
                outer_diameter=120,
                is_active=1,
            )
            db.add(spec)
            db.commit()

            inbound_html = paper_inbound_page(db=db).body.decode("utf-8")
            create_paper_inbound(
                specification_id=spec.id,
                batch_code="PAPER-20260721",
                quantity=20,
                unit_price="12.30",
                location="P-A01",
                operator_name="张三",
                remark="采购入库",
                _lock=None,
                db=db,
            )
            batch = db.query(PaperInventoryBatch).one()
            transaction = db.query(PaperInventoryTransaction).one()

        self.assertIn("元/圈", inbound_html)
        self.assertEqual(batch.quantity, 20)
        self.assertEqual(batch.unit_price, Decimal("12.30"))
        self.assertEqual(batch.model, "Tnx236.2A")
        self.assertEqual(transaction.after_quantity, 20)


class PaperInventoryWorkflowPagesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def _seed_roll_batches(self, db):
        spec = PaperSpecification(
            paper_type="roll",
            model="Tnx236.2A",
            material_name="蓝纸",
            thickness=0.5,
            inner_diameter=80,
            outer_diameter=120,
            is_active=1,
        )
        db.add(spec)
        db.flush()
        base_time = datetime(2026, 7, 1, 8, 0)
        first = PaperInventoryBatch(
            specification_id=spec.id,
            batch_code="PAPER-A",
            paper_type="roll",
            model=spec.model,
            material_name=spec.material_name,
            thickness=spec.thickness,
            inner_diameter=80,
            outer_diameter=120,
            quantity=2,
            unit_price=Decimal("10.00"),
            location="P1",
            status="available",
            created_at=base_time,
            updated_at=base_time,
        )
        second = PaperInventoryBatch(
            specification_id=spec.id,
            batch_code="PAPER-B",
            paper_type="roll",
            model=spec.model,
            material_name=spec.material_name,
            thickness=spec.thickness,
            inner_diameter=80,
            outer_diameter=120,
            quantity=5,
            unit_price=Decimal("12.50"),
            location="P2",
            status="available",
            created_at=base_time + timedelta(minutes=1),
            updated_at=base_time + timedelta(minutes=1),
        )
        empty = PaperInventoryBatch(
            specification_id=spec.id,
            batch_code="PAPER-EMPTY",
            paper_type="roll",
            model=spec.model,
            material_name=spec.material_name,
            thickness=spec.thickness,
            inner_diameter=80,
            outer_diameter=120,
            quantity=0,
            unit_price=Decimal("99.00"),
            location="P3",
            status="used",
            created_at=base_time - timedelta(minutes=1),
            updated_at=base_time - timedelta(minutes=1),
        )
        db.add_all([first, second, empty])
        db.flush()
        for batch in (first, second):
            db.add(
                PaperInventoryTransaction(
                    inventory_id=batch.id,
                    transaction_type="in",
                    quantity=batch.quantity,
                    before_quantity=0,
                    after_quantity=batch.quantity,
                    operator_name="采购员",
                )
            )
        db.commit()
        return spec, first, second, empty

    def test_inventory_page_renders_different_models_as_separate_rows(self) -> None:
        model_dimensions = [
            ("3969.01", 55, 115),
            ("3960.02", 67, 127),
            ("3970.01", 80, 132),
        ]
        with self.Session() as db:
            specs = [
                PaperSpecification(
                    paper_type="roll",
                    model=model,
                    material_name="黑色（DC0017）",
                    thickness=0.5,
                    inner_diameter=inner_diameter,
                    outer_diameter=outer_diameter,
                    is_active=1,
                )
                for model, inner_diameter, outer_diameter in model_dimensions
            ]
            db.add_all(specs)
            db.flush()
            db.add_all(
                [
                    PaperInventoryBatch(
                        specification_id=spec.id,
                        batch_code=f"P-{spec.model}",
                        paper_type=spec.paper_type,
                        model=spec.model,
                        material_name=spec.material_name,
                        thickness=spec.thickness,
                        inner_diameter=spec.inner_diameter,
                        outer_diameter=spec.outer_diameter,
                        quantity=index * 100,
                        unit_price=Decimal("0.66") + Decimal(index - 1) / 20,
                        status="available",
                    )
                    for index, spec in enumerate(specs, start=1)
                ]
            )
            db.commit()

            inventory_html = paper_inventory_page(db=db).body.decode("utf-8")
            specification_ids = [spec.id for spec in specs]

        self.assertEqual(inventory_html.count(">3969.01<"), 1)
        self.assertEqual(inventory_html.count(">3960.02<"), 1)
        self.assertEqual(inventory_html.count(">3970.01<"), 1)
        for specification_id in specification_ids:
            self.assertIn(
                f"/admin/paper-materials/detail?specification_id={specification_id}",
                inventory_html,
            )

    def test_inventory_price_range_and_detail_only_use_live_batches(self) -> None:
        with self.Session() as db:
            spec, first, second, empty = self._seed_roll_batches(db)

            inventory_html = paper_inventory_page(db=db).body.decode("utf-8")
            detail_html = paper_group_detail_page(specification_id=spec.id, db=db).body.decode("utf-8")

        self.assertIn("¥10.00～¥12.50", inventory_html)
        self.assertNotIn("¥99.00", inventory_html)
        self.assertIn("7 圈", inventory_html)
        self.assertIn("/admin/paper-materials/detail?specification_id=", inventory_html)
        self.assertIn("PAPER-A", detail_html)
        self.assertIn("PAPER-B", detail_html)
        self.assertIn("¥10.00", detail_html)
        self.assertIn("¥12.50", detail_html)

    def test_outbound_page_and_transaction_reversal_complete_fifo_flow(self) -> None:
        with self.Session() as db:
            spec, first, second, empty = self._seed_roll_batches(db)

            outbound_html = paper_outbound_page(specification_id=str(spec.id), db=db).body.decode("utf-8")
            self.assertLess(outbound_html.index("<h2>当前可用规格</h2>"), outbound_html.index("<h2>确认出库</h2>"))
            self.assertIn(f'name="specification_id" value="{spec.id}"', outbound_html)

            outbound_paper_from_page(
                specification_id=spec.id,
                quantity=4,
                location="",
                customer_name="一车间",
                operator_name="张三",
                remark="生产领用",
                _lock=None,
                db=db,
            )
            self.assertEqual((first.quantity, second.quantity), (0, 3))
            transaction_html = paper_transactions_page(db=db).body.decode("utf-8")
            self.assertIn("Tnx236.2A", transaction_html)
            self.assertIn("一车间", transaction_html)
            self.assertIn("0.5×80×120", transaction_html)
            self.assertIn('class="wide-transaction-table"', transaction_html)
            self.assertIn(
                ".table-scroll table.wide-transaction-table { min-width:1400px;",
                transaction_html,
            )

            first_out = (
                db.query(PaperInventoryTransaction)
                .filter_by(inventory_id=first.id, transaction_type="out")
                .one()
            )
            reverse_paper_transaction_from_page(
                transaction_id=first_out.id,
                operator_name="李四",
                remark="撤回领用",
                _lock=None,
                db=db,
            )
            self.assertEqual(first.quantity, 2)
            self.assertEqual(first.unit_price, Decimal("10.00"))


if __name__ == "__main__":
    unittest.main()
