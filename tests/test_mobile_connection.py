import json
from pathlib import Path
import subprocess
import tempfile
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
    def test_release_build_requires_external_personal_api_domain(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    str(repository / ".venv/bin/python"),
                    str(repository / "scripts/build_miniprogram_release.py"),
                    "--target-file",
                    str(Path(directory) / "missing.env"),
                    "--output",
                    str(Path(directory) / "release"),
                ],
                cwd=repository,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TENAISHI_API_DOMAIN", result.stderr)

    def test_release_build_writes_https_domain_and_rejects_ip(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.env"
            output = root / "release"
            target.write_text(
                "TENAISHI_API_DOMAIN=api.personal.example.test\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    str(repository / ".venv/bin/python"),
                    str(repository / "scripts/build_miniprogram_release.py"),
                    "--target-file",
                    str(target),
                    "--output",
                    str(output),
                ],
                cwd=repository,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            config = (output / "release-config.js").read_text(encoding="utf-8")
            self.assertIn("https://api.personal.example.test", config)

            target.write_text("TENAISHI_API_DOMAIN=203.0.113.7\n", encoding="utf-8")
            rejected = subprocess.run(
                [
                    str(repository / ".venv/bin/python"),
                    str(repository / "scripts/build_miniprogram_release.py"),
                    "--target-file",
                    str(target),
                    "--output",
                    str(root / "rejected"),
                ],
                cwd=repository,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("域名", rejected.stderr)

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
        self.assertEqual(
            payload,
            {"version": 1, "base_url": "http://10.0.0.8:8000"},
        )
        serialized = json.dumps(payload)
        self.assertNotIn("database", serialized)
        self.assertNotIn("token", serialized)

    def test_health_has_stable_connection_metadata(self) -> None:
        self.assertEqual(
            health_check(),
            {
                "status": "ok",
                "app_name": "杭州特耐时库存系统",
                "app_version": "0.1.0",
            },
        )

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
        self.assertLess(html.index("系统设置"), html.index("小程序连接</a>"))
        self.assertLess(html.index("小程序连接</a>"), html.index("退出登录"))
