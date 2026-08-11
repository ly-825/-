import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.backup import backup_sqlite, validate_bundle, verify_sqlite
from scripts.backup import create_backup_bundle, prune_backup_directories


class BackupPrimitiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_online_backup_contains_committed_rows(self) -> None:
        source = self.root / "source.db"
        target = self.root / "target.db"
        with sqlite3.connect(source) as connection:
            connection.execute("CREATE TABLE stock (id INTEGER PRIMARY KEY, quantity INTEGER)")
            connection.execute("INSERT INTO stock(quantity) VALUES (7)")
        backup_sqlite(source, target)
        with sqlite3.connect(target) as connection:
            self.assertEqual(connection.execute("SELECT quantity FROM stock").fetchone()[0], 7)

    def test_online_backup_excludes_uncommitted_writer_row(self) -> None:
        source = self.root / "source.db"
        target = self.root / "target.db"
        with sqlite3.connect(source) as connection:
            connection.execute("CREATE TABLE stock (id INTEGER PRIMARY KEY, quantity INTEGER)")
            connection.execute("INSERT INTO stock(quantity) VALUES (7)")

        writer = sqlite3.connect(source)
        try:
            writer.execute("BEGIN")
            writer.execute("INSERT INTO stock(quantity) VALUES (99)")
            backup_sqlite(source, target)
        finally:
            writer.rollback()
            writer.close()

        with sqlite3.connect(target) as connection:
            quantities = [row[0] for row in connection.execute("SELECT quantity FROM stock")]
        self.assertEqual(quantities, [7])

    def test_verify_rejects_corrupt_database(self) -> None:
        corrupt = self.root / "corrupt.db"
        corrupt.write_bytes(b"this is not sqlite")

        with self.assertRaises(ValueError):
            verify_sqlite(corrupt)

    def test_backup_refuses_existing_destination(self) -> None:
        source = self.root / "source.db"
        target = self.root / "target.db"
        with sqlite3.connect(source) as connection:
            connection.execute("CREATE TABLE stock (quantity INTEGER)")
        target.write_bytes(b"keep me")

        with self.assertRaises(FileExistsError):
            backup_sqlite(source, target)

        self.assertEqual(target.read_bytes(), b"keep me")

    def test_wal_source_produces_one_standalone_backup_file(self) -> None:
        source = self.root / "source.db"
        target = self.root / "target.db"
        with sqlite3.connect(source) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE stock (quantity INTEGER)")
            connection.execute("INSERT INTO stock(quantity) VALUES (7)")

        backup_sqlite(source, target)

        self.assertEqual(
            sorted(path.name for path in self.root.iterdir() if path.name.startswith("target")),
            ["target.db"],
        )
        with sqlite3.connect(target) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")


class BackupBundleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "data" / "app.db"
        self.uploads = self.root / "data" / "uploads"
        self.backup_root = self.root / "backups"
        self.database.parent.mkdir(parents=True)
        self.uploads.mkdir(parents=True)
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE stock (quantity INTEGER)")
            connection.execute("INSERT INTO stock(quantity) VALUES (7)")
        (self.uploads / "drawing.dxf").write_text("drawing-data", encoding="utf-8")
        self.now = datetime(2026, 8, 11, 2, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_bundle_contains_verified_database_uploads_and_manifest_hash(self) -> None:
        bundle = create_backup_bundle(
            self.database,
            self.uploads,
            self.backup_root,
            created_at=self.now,
        )

        verify_sqlite(bundle / "app.db")
        self.assertEqual((bundle / "uploads" / "drawing.dxf").read_text(), "drawing-data")
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        database_bytes = (bundle / "app.db").read_bytes()
        self.assertEqual(
            manifest,
            {
                "format_version": 1,
                "created_at": "2026-08-11T02:00:00+08:00",
                "database": "app.db",
                "database_sha256": hashlib.sha256(database_bytes).hexdigest(),
                "database_size": len(database_bytes),
                "uploads": "uploads",
            },
        )

    def test_failed_bundle_leaves_no_completed_or_partial_directory(self) -> None:
        self.database.write_bytes(b"corrupt sqlite")

        with self.assertRaises(Exception):
            create_backup_bundle(
                self.database,
                self.uploads,
                self.backup_root,
                created_at=self.now,
            )

        self.assertEqual(list(self.backup_root.iterdir()), [])

    def test_retention_removes_only_old_timestamped_direct_children(self) -> None:
        self.backup_root.mkdir()
        old_one = self.backup_root / "2026-07-01_020000"
        old_two = self.backup_root / "2026-08-01_020000"
        recent = self.backup_root / "2026-08-10_020000"
        unrelated = self.backup_root / "do-not-delete"
        outside = self.root / "2026-07-01_020000"
        for directory in (old_one, old_two, recent, unrelated, outside):
            directory.mkdir()

        removed = prune_backup_directories(
            self.backup_root,
            retention_days=7,
            now=self.now,
        )

        self.assertEqual(set(removed), {old_one, old_two})
        self.assertFalse(old_one.exists())
        self.assertFalse(old_two.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(outside.exists())


class BackupRestoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "source" / "app.db"
        self.uploads = self.root / "source" / "uploads"
        self.database.parent.mkdir(parents=True)
        self.uploads.mkdir()
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE stock (quantity INTEGER)")
            connection.execute("INSERT INTO stock(quantity) VALUES (7)")
        (self.uploads / "drawing.dxf").write_text("drawing-data", encoding="utf-8")
        self.bundle = create_backup_bundle(
            self.database,
            self.uploads,
            self.root / "backups",
            created_at=datetime(2026, 8, 11, 2, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        self.restore_script = Path(__file__).resolve().parents[1] / "scripts" / "restore_backup.py"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_restore(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.restore_script), str(self.bundle), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )

    def rewrite_manifest_hash(self) -> None:
        manifest_path = self.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        database_bytes = (self.bundle / "app.db").read_bytes()
        manifest["database_sha256"] = hashlib.sha256(database_bytes).hexdigest()
        manifest["database_size"] = len(database_bytes)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_validate_bundle_accepts_matching_manifest_and_database(self) -> None:
        manifest = validate_bundle(self.bundle)

        self.assertEqual(manifest.database, "app.db")
        self.assertEqual(manifest.uploads, "uploads")
        self.assertEqual(manifest.format_version, 1)

    def test_validate_bundle_rejects_hash_mismatch(self) -> None:
        database_path = self.bundle / "app.db"
        database_bytes = bytearray(database_path.read_bytes())
        database_bytes[-1] ^= 1
        database_path.write_bytes(database_bytes)

        with self.assertRaisesRegex(ValueError, "哈希"):
            validate_bundle(self.bundle)

    def test_validate_bundle_rejects_corrupt_database_even_with_matching_hash(self) -> None:
        (self.bundle / "app.db").write_bytes(b"not sqlite")
        self.rewrite_manifest_hash()

        with self.assertRaises(ValueError):
            validate_bundle(self.bundle)

    def test_default_restore_command_only_verifies(self) -> None:
        result = self.run_restore()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("验证通过", result.stdout)
        self.assertFalse((self.root / "restored").exists())

    def test_restore_refuses_existing_target_without_replace(self) -> None:
        target = self.root / "restored"
        target.mkdir()
        marker = target / "keep.txt"
        marker.write_text("keep", encoding="utf-8")

        result = self.run_restore("--target", str(target))

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_restore_writes_verified_bundle_to_fresh_target(self) -> None:
        target = self.root / "restored"

        result = self.run_restore("--target", str(target))

        self.assertEqual(result.returncode, 0, result.stderr)
        verify_sqlite(target / "app.db")
        self.assertEqual((target / "uploads" / "drawing.dxf").read_text(), "drawing-data")
        self.assertTrue((target / "manifest.json").is_file())

    def test_replace_requires_literal_confirmation_and_preserves_old_target(self) -> None:
        target = self.root / "restored"
        target.mkdir()
        (target / "keep.txt").write_text("old-data", encoding="utf-8")

        refused = self.run_restore("--target", str(target), "--replace")
        accepted = self.run_restore(
            "--target",
            str(target),
            "--replace",
            "--confirm",
            "RESTORE",
        )

        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        preserved = list(self.root.glob("pre-restore-*/keep.txt"))
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0].read_text(encoding="utf-8"), "old-data")
        verify_sqlite(target / "app.db")


if __name__ == "__main__":
    unittest.main()
