import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LinuxDeployAssetTest(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_setup_uses_stable_paths_user_packages_and_no_firewall_changes(self) -> None:
        setup = self.read("deploy/setup-server.sh")

        for value in (
            "/srv/tenaishi/app",
            "/srv/tenaishi/data",
            "/srv/tenaishi/backups",
            "/etc/tenaishi",
            "useradd --system",
            "--shell /usr/sbin/nologin",
            "python3-venv",
            "nginx",
            "rsync",
            "util-linux",
            "curl",
            "gettext-base",
            "sqlite3",
        ):
            self.assertIn(value, setup)
        self.assertNotRegex(setup, r"\b(ufw|iptables|firewall-cmd)\b")
        self.assertIn('if [ ! -d "$APP_DIR/.git" ]', setup)
        self.assertIn('if [ ! -f "$ENV_FILE" ]', setup)

    def test_service_is_hardened_local_only_and_single_worker(self) -> None:
        service = self.read("deploy/tenaishi.service")

        for value in (
            "User=tenaishi",
            "Group=tenaishi",
            "WorkingDirectory=/srv/tenaishi/app",
            "EnvironmentFile=/etc/tenaishi/tenaishi.env",
            "--host 127.0.0.1",
            "--port 8000",
            "--workers 1",
            "Restart=on-failure",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "ReadWritePaths=/srv/tenaishi/data /srv/tenaishi/backups",
            "UMask=0077",
        ):
            self.assertIn(value, service)

    def test_nginx_uses_personal_placeholders_and_secure_proxy_boundary(self) -> None:
        nginx = self.read("deploy/nginx-personal-inventory.conf")

        self.assertNotIn("${TENAISHI_SITE_DOMAIN}", nginx)
        self.assertEqual(nginx.count("server_name ${TENAISHI_API_DOMAIN};"), 2)
        for line in nginx.splitlines():
            if line.strip().startswith("server_name "):
                self.assertIn("${TENAISHI_", line)
        self.assertEqual(nginx.count("proxy_pass http://127.0.0.1:8000"), 2)
        self.assertIn("limit_req_zone $binary_remote_addr zone=tenaishi_auth:10m rate=20r/m", nginx)
        self.assertIn("pc-login/(requests|scan|decision|consume)", nginx)
        self.assertIn("client_max_body_size 16k", nginx)
        self.assertNotIn("pc-login/(requests|status|scan", nginx)
        self.assertIn("proxy_set_header X-Real-IP $remote_addr", nginx)
        self.assertIn("proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for", nginx)
        self.assertIn("proxy_set_header X-Forwarded-Proto $scheme", nginx)
        self.assertIn("client_max_body_size 100m", nginx)
        self.assertEqual(nginx.count("location ~ /\\."), 1)
        self.assertNotIn("auth_basic", nginx)
        http_block = nginx.split("server {", 2)[1]
        self.assertNotIn("Strict-Transport-Security", http_block)
        self.assertEqual(nginx.count("Strict-Transport-Security"), 1)
        self.assertIn(
            "/etc/letsencrypt/live/${TENAISHI_API_DOMAIN}/fullchain.pem",
            nginx,
        )
        self.assertIn(
            "/etc/letsencrypt/live/${TENAISHI_API_DOMAIN}/privkey.pem",
            nginx,
        )

    def test_update_is_clean_backup_first_and_fast_forward_only(self) -> None:
        update = self.read("deploy/update-server.sh")

        self.assertIn("git status --porcelain", update)
        self.assertIn("scripts/backup.sh", update)
        self.assertIn("git pull --ff-only", update)
        self.assertLess(update.index("scripts/backup.sh"), update.index("git pull --ff-only"))
        self.assertIn("pip install -r requirements.txt", update)
        self.assertIn("systemctl restart tenaishi.service", update)
        self.assertIn("http://127.0.0.1:8000/health", update)
        self.assertIn("git rev-parse HEAD", update)

    def test_smoke_test_uses_external_personal_target_and_requires_closed_8000(self) -> None:
        smoke = self.read("deploy/smoke-test.sh")

        self.assertIn(".config/tenaishi/deploy-target.env", smoke)
        self.assertIn('https://${TENAISHI_API_DOMAIN}/health', smoke)
        self.assertIn('http://${TENAISHI_PUBLIC_IP}:8000/health', smoke)
        self.assertRegex(smoke, r"if\s+curl[^\n]+8000/health")
        literal_addresses = set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", smoke))
        self.assertLessEqual(literal_addresses, {"127.0.0.1"})
        self.assertIn('/auth/login', smoke)
        self.assertIn('使用小程序扫码登录', smoke)
        self.assertNotIn('browser_secret', smoke)
        self.assertNotIn('request_token', smoke)

    def test_release_build_is_documented_as_external_and_fail_closed(self) -> None:
        readme = self.read("deploy/README.md")

        self.assertIn("scripts/build_miniprogram_release.py", readme)
        self.assertIn("$HOME/.config/tenaishi/deploy-target.env", readme)
        self.assertIn("不要直接上传 `miniprogram/`", readme)

    def test_wechat_admin_cutover_and_recovery_are_documented(self) -> None:
        readme = self.read("deploy/README.md")
        for value in (
            "python scripts/manage_superadmin.py bootstrap",
            "python scripts/manage_superadmin.py reset-wechat",
            "LEGACY_PASSWORD_LOGIN_ENABLED=true",
            "LEGACY_PASSWORD_LOGIN_ENABLED=false",
            "scripts/backup.sh",
            "PRAGMA integrity_check",
            "PRAGMA foreign_key_check",
            "/auth/legacy-login",
        ):
            self.assertIn(value, readme)
        self.assertIn("阶段 A", readme)
        self.assertIn("阶段 B", readme)


if __name__ == "__main__":
    unittest.main()
