import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LinuxBackupAssetTest(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_backup_service_is_locked_down_oneshot_job(self) -> None:
        service = self.read("deploy/tenaishi-backup.service")

        self.assertIn("Type=oneshot", service)
        self.assertIn("User=tenaishi", service)
        self.assertIn("Group=tenaishi", service)
        self.assertIn("WorkingDirectory=/srv/tenaishi/app", service)
        self.assertIn("EnvironmentFile=/etc/tenaishi/tenaishi.env", service)
        self.assertIn("ExecStart=/srv/tenaishi/app/scripts/backup.sh", service)
        self.assertIn("UMask=0077", service)

    def test_backup_timer_runs_daily_in_shanghai_and_catches_missed_runs(self) -> None:
        timer = self.read("deploy/tenaishi-backup.timer")

        self.assertIn("OnCalendar=*-*-* 02:00:00", timer)
        self.assertIn("Asia/Shanghai", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("RandomizedDelaySec=300", timer)

    def test_setup_installs_and_enables_backup_timer(self) -> None:
        setup = self.read("deploy/setup-server.sh")

        self.assertIn("tenaishi-backup.service", setup)
        self.assertIn("tenaishi-backup.timer", setup)
        self.assertIn("systemctl enable --now tenaishi-backup.timer", setup)

    def test_application_service_is_non_root_local_and_single_worker(self) -> None:
        service = self.read("deploy/tenaishi.service")

        self.assertIn("User=tenaishi", service)
        self.assertIn("Group=tenaishi", service)
        self.assertIn("--host 127.0.0.1", service)
        self.assertIn("--workers 1", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ReadWritePaths=/srv/tenaishi/data /srv/tenaishi/backups", service)

    def test_setup_uses_installed_runuser_instead_of_optional_sudo(self) -> None:
        setup = self.read("deploy/setup-server.sh")

        self.assertIn("runuser -u tenaishi --", setup)
        self.assertNotIn("sudo -u", setup)

    def test_docs_define_cloud_paths_and_never_copy_live_database(self) -> None:
        deploy_docs = self.read("deploy/README.md")
        root_docs = self.read("README.md")
        combined = deploy_docs + "\n" + root_docs

        self.assertIn("/srv/tenaishi/backups", combined)
        self.assertIn("/srv/tenaishi/data/uploads", combined)
        self.assertIn("30 天", combined)
        self.assertNotRegex(combined, r"cp\s+[^\n]*app\.db")
        self.assertNotIn("复制正在运行的 data/app.db", combined)


if __name__ == "__main__":
    unittest.main()
