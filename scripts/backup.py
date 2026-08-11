#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import secrets
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backup import backup_sqlite  # noqa: E402
from app.time_utils import china_now  # noqa: E402


TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup_bundle(
    database: Path,
    uploads: Path,
    backup_root: Path,
    *,
    created_at: datetime | None = None,
) -> Path:
    database = Path(database)
    uploads = Path(uploads)
    backup_root = Path(backup_root)
    current_time = created_at or china_now()
    timestamp = current_time.strftime("%Y-%m-%d_%H%M%S")
    completed = backup_root / timestamp
    if completed.exists():
        raise FileExistsError(completed)
    backup_root.mkdir(parents=True, exist_ok=True)
    partial = backup_root / f".partial-{timestamp}-{secrets.token_hex(4)}"
    partial.mkdir()

    try:
        database_result = backup_sqlite(database, partial / "app.db")
        if uploads.is_dir():
            shutil.copytree(uploads, partial / "uploads")
        else:
            (partial / "uploads").mkdir()
        manifest = {
            "format_version": 1,
            "created_at": current_time.isoformat(),
            "database": "app.db",
            "database_sha256": _sha256(database_result.path),
            "database_size": database_result.size,
            "uploads": "uploads",
        }
        (partial / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        partial.rename(completed)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return completed


def prune_backup_directories(
    backup_root: Path,
    retention_days: int,
    *,
    now: datetime | None = None,
) -> list[Path]:
    if retention_days < 0:
        raise ValueError("保留天数不能小于 0")
    backup_root = Path(backup_root)
    if not backup_root.is_dir():
        return []
    current_time = (now or china_now()).replace(tzinfo=None)
    cutoff = current_time - timedelta(days=retention_days)
    removed: list[Path] = []
    for child in backup_root.iterdir():
        if not child.is_dir() or not TIMESTAMP_PATTERN.fullmatch(child.name):
            continue
        timestamp = datetime.strptime(child.name, "%Y-%m-%d_%H%M%S")
        if timestamp < cutoff:
            shutil.rmtree(child)
            removed.append(child)
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="创建一致的 SQLite 备份包")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--uploads", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--retention-days", type=int, default=7)
    args = parser.parse_args()

    bundle = create_backup_bundle(
        args.database,
        args.uploads,
        args.backup_root,
    )
    removed = prune_backup_directories(args.backup_root, args.retention_days)
    print(f"Backup created: {bundle}")
    print(f"Expired backups removed: {len(removed)}")


if __name__ == "__main__":
    main()
