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

    def test_connection_page_supports_scan_manual_and_recovery(self) -> None:
        source = self.read("miniprogram/pages/connection/index.js")
        view = self.read("miniprogram/pages/connection/index.wxml")
        self.assertIn("scanBaseUrl", source)
        self.assertIn("testAndSave", source)
        self.assertIn("扫描电脑连接二维码", view)
        self.assertIn("手工设置地址", view)
        self.assertIn("连接工厂 Wi-Fi", view)

    def test_tabbar_assets_exist_and_are_small_png_files(self) -> None:
        for name in ("plan", "materials", "products"):
            for suffix in ("", "-active"):
                path = (
                    ROOT
                    / f"miniprogram/assets/tabbar/{name}{suffix}.png"
                )
                self.assertTrue(path.exists())
                self.assertLess(path.stat().st_size, 40 * 1024)
