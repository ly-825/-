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
