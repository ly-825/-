import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.backup import backup_sqlite, verify_sqlite


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


if __name__ == "__main__":
    unittest.main()
