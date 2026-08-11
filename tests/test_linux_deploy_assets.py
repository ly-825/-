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

        self.assertIn("${TENAISHI_SITE_DOMAIN}", nginx)
        self.assertIn("${TENAISHI_API_DOMAIN}", nginx)
        self.assertNotIn("tnsautoparts.com", nginx)
        self.assertNotIn("115.120.248.123", nginx)
        self.assertIn("proxy_pass http://127.0.0.1:8000", nginx)
        self.assertIn("proxy_set_header X-Real-IP $remote_addr", nginx)
        self.assertIn("proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for", nginx)
        self.assertIn("proxy_set_header X-Forwarded-Proto $scheme", nginx)
        self.assertIn("client_max_body_size 100m", nginx)
        self.assertGreaterEqual(nginx.count("location ~ /\\."), 2)
        self.assertNotIn("auth_basic", nginx)
        http_block = nginx.split("server {", 2)[1]
        self.assertNotIn("Strict-Transport-Security", http_block)
        self.assertGreaterEqual(nginx.count("Strict-Transport-Security"), 2)

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
        self.assertNotIn("115.120.248.123", smoke)


if __name__ == "__main__":
    unittest.main()
