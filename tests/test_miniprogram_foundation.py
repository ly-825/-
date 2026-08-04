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
        connection = self.read(
            "miniprogram/components/connection-status/index.wxml"
        )
        self.assertIn('bindtap="retry"', state)
        self.assertIn("确认操作", confirm)
        self.assertIn('bindtap="confirm"', confirm)
        self.assertIn("重新连接", connection)

    def test_redesign_exposes_simple_grouped_ui_primitives(self) -> None:
        wxss = self.read("miniprogram/app.wxss")
        for selector in (
            ".simple-header",
            ".group-list",
            ".group-row",
            ".primary-action",
            ".form-label",
            ".data-list",
            ".ledger-item",
            ".danger-action",
            ".safe-action",
        ):
            self.assertIn(selector, wxss)
        self.assertIn("env(safe-area-inset-bottom)", wxss)
        self.assertIn("font-variant-numeric: tabular-nums", wxss)

    def test_connected_status_is_compact_but_error_is_actionable(self) -> None:
        view = self.read(
            "miniprogram/components/connection-status/index.wxml"
        )
        style = self.read(
            "miniprogram/components/connection-status/index.wxss"
        )
        self.assertIn("connection-line", view)
        self.assertIn("内网已连接", view)
        self.assertIn("connection-error", view)
        self.assertIn("重新连接", view)
        self.assertIn("修改地址", view)
        self.assertNotIn("box-shadow", style)

    def test_local_material_icons_exist_and_are_lucide_svg(self) -> None:
        for name in ("steel", "scrap", "paper", "chevron-right"):
            source = self.read(f"miniprogram/assets/icons/{name}.svg")
            self.assertIn('viewBox="0 0 24 24"', source)
            self.assertIn('stroke="#334155"', source)

    def test_global_style_uses_approved_tokens_without_decorative_gradients(
        self,
    ) -> None:
        wxss = self.read("miniprogram/app.wxss")
        for value in ("#334155", "#059669", "#F8FAFC", "#0F172A", "#DC2626"):
            self.assertIn(value.lower(), wxss.lower())
        self.assertIn("min-height: 88rpx", wxss)
        self.assertNotIn("radial-gradient", wxss)
        self.assertNotIn("linear-gradient", wxss)

    def test_manifest_has_three_tabs_and_connection_first(self) -> None:
        app_json = json.loads(self.read("miniprogram/app.json"))
        self.assertEqual(app_json["pages"][0], "pages/connection/index")
        self.assertEqual(
            [
                (item["pagePath"], item["text"])
                for item in app_json["tabBar"]["list"]
            ],
            [
                ("pages/plan/home", "计划"),
                ("pages/materials/home", "材料"),
                ("pages/products/home", "成品"),
            ],
        )
        self.assertNotIn(
            "pages/drawings/home",
            [item["pagePath"] for item in app_json["tabBar"]["list"]],
        )

    def test_materials_home_has_three_clear_business_entries(self) -> None:
        wxml = self.read("miniprogram/pages/materials/home.wxml")
        for label in ("钢板", "余料", "纸材"):
            self.assertIn(label, wxml)
        self.assertIn("待确认", wxml)

    def test_top_level_pages_use_simple_headers_and_grouped_lists(self) -> None:
        pages = (
            "miniprogram/pages/plan/home.wxml",
            "miniprogram/pages/materials/home.wxml",
            "miniprogram/pages/products/home.wxml",
            "miniprogram/pages/materials/steel-home.wxml",
            "miniprogram/pages/materials/paper-home.wxml",
        )
        for page in pages:
            with self.subTest(page=page):
                view = self.read(page)
                self.assertIn("simple-header", view)
                self.assertNotIn("eyebrow", view)
        materials = self.read("miniprogram/pages/materials/home.wxml")
        self.assertIn("group-list", materials)
        self.assertIn("处理待确认余料", materials)
        self.assertEqual(materials.count("primary-action"), 1)

    def test_connection_page_keeps_one_primary_scan_action(self) -> None:
        view = self.read("miniprogram/pages/connection/index.wxml")
        self.assertEqual(view.count('class="primary-action"'), 1)
        self.assertIn("扫描电脑连接二维码", view)
        self.assertIn("手工设置地址", view)

    def test_connection_page_supports_scan_manual_and_recovery(self) -> None:
        source = self.read("miniprogram/pages/connection/index.js")
        view = self.read("miniprogram/pages/connection/index.wxml")
        self.assertIn("scanBaseUrl", source)
        self.assertIn("testAndSave", source)
        self.assertIn("扫描电脑连接二维码", view)
        self.assertIn("手工设置地址", view)
        self.assertIn("连接工厂 Wi-Fi", view)
        self.assertIn("retrySavedConnection", source)
        self.assertIn("重新连接", view)
        self.assertIn("修改地址", view)

    def test_every_current_inventory_write_uses_confirmation_sheet(self) -> None:
        pages = (
            "miniprogram/pages/inventory/inbound",
            "miniprogram/pages/inventory/outbound",
            "miniprogram/pages/inventory/transactions",
            "miniprogram/pages/scraps/pending",
            "miniprogram/pages/scraps/outbound",
            "miniprogram/pages/scraps/transactions",
        )
        for page in pages:
            with self.subTest(page=page):
                config = json.loads(self.read(f"{page}.json"))
                view = self.read(f"{page}.wxml")
                source = self.read(f"{page}.js")
                self.assertEqual(
                    config.get("usingComponents", {}).get("confirm-sheet"),
                    "/components/confirm-sheet/index",
                )
                self.assertIn("<confirm-sheet", view)
                self.assertIn("confirmOpen", source)
                self.assertIn("confirmSubmit", source)

    def test_inventory_pages_use_permanent_labels_and_flat_data_lists(
        self,
    ) -> None:
        for name in ("list", "inbound", "outbound"):
            with self.subTest(name=name):
                view = self.read(f"miniprogram/pages/inventory/{name}.wxml")
                source = self.read(f"miniprogram/pages/inventory/{name}.js")
                config = json.loads(
                    self.read(f"miniprogram/pages/inventory/{name}.json")
                )
                self.assertIn("simple-header", view)
                self.assertIn("form-label", view)
                self.assertIn("data-list", view)
                self.assertIn("<state-view", view)
                self.assertNotIn("eyebrow", view)
                self.assertIn("error", source)
                self.assertEqual(
                    config.get("usingComponents", {}).get("state-view"),
                    "/components/state-view/index",
                )
        for name in ("inbound", "outbound"):
            view = self.read(f"miniprogram/pages/inventory/{name}.wxml")
            self.assertEqual(view.count("primary-action"), 1)
            self.assertIn("<confirm-sheet", view)

    def test_read_pages_use_flat_lists_and_no_english_eyebrows(self) -> None:
        pages = (
            "miniprogram/pages/inventory/transactions.wxml",
            "miniprogram/pages/scraps/home.wxml",
            "miniprogram/pages/scraps/list.wxml",
        )
        for page in pages:
            with self.subTest(page=page):
                view = self.read(page)
                self.assertIn("simple-header", view)
                self.assertIn("<state-view", view)
                self.assertNotIn("eyebrow", view)
        self.assertIn(
            "ledger-list",
            self.read("miniprogram/pages/inventory/transactions.wxml"),
        )
        self.assertIn(
            "group-list", self.read("miniprogram/pages/scraps/home.wxml")
        )
        self.assertIn(
            "data-list", self.read("miniprogram/pages/scraps/list.wxml")
        )

    def test_scrap_write_pages_keep_labels_confirmations_and_danger_actions(
        self,
    ) -> None:
        for name in ("pending", "outbound", "transactions"):
            with self.subTest(name=name):
                view = self.read(f"miniprogram/pages/scraps/{name}.wxml")
                config = json.loads(
                    self.read(f"miniprogram/pages/scraps/{name}.json")
                )
                self.assertIn("simple-header", view)
                self.assertIn("<confirm-sheet", view)
                self.assertIn("<state-view", view)
                self.assertNotIn("eyebrow", view)
                self.assertEqual(
                    config.get("usingComponents", {}).get("state-view"),
                    "/components/state-view/index",
                )
        self.assertIn(
            "form-label", self.read("miniprogram/pages/scraps/pending.wxml")
        )
        self.assertIn(
            "form-label", self.read("miniprogram/pages/scraps/outbound.wxml")
        )
        transactions = self.read("miniprogram/pages/scraps/transactions.wxml")
        self.assertIn("ledger-list", transactions)
        self.assertIn("danger-action", transactions)

    def test_every_current_inventory_write_uses_persistent_request_tracker(
        self,
    ) -> None:
        pages = (
            "miniprogram/pages/inventory/inbound.js",
            "miniprogram/pages/inventory/outbound.js",
            "miniprogram/pages/inventory/transactions.js",
            "miniprogram/pages/scraps/pending.js",
            "miniprogram/pages/scraps/outbound.js",
            "miniprogram/pages/scraps/transactions.js",
        )
        for page in pages:
            with self.subTest(page=page):
                source = self.read(page)
                self.assertIn("createPendingRequestTracker", source)
                self.assertIn("retryPendingWrite", source)
                self.assertIn(".complete()", source)

        api_source = self.read("miniprogram/utils/api.js")
        self.assertNotIn("createRequestId", api_source)
        self.assertIn("trackedWriteData", api_source)

    def test_tabbar_assets_exist_and_are_small_png_files(self) -> None:
        for name in ("plan", "materials", "products"):
            for suffix in ("", "-active"):
                path = (
                    ROOT
                    / f"miniprogram/assets/tabbar/{name}{suffix}.png"
                )
                self.assertTrue(path.exists())
                self.assertLess(path.stat().st_size, 40 * 1024)
