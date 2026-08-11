import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BackupResult:
    path: Path
    size: int


@dataclass(frozen=True)
class BackupManifest:
    format_version: int
    created_at: str
    database: str
    database_sha256: str
    database_size: int
    uploads: str


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sqlite(path: Path) -> None:
    try:
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"SQLite 备份校验失败：{path}") from exc
    if result != [("ok",)]:
        raise ValueError(f"SQLite 备份校验失败：{result}")


def backup_sqlite(source: Path, destination: Path) -> BackupResult:
    source = Path(source)
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    if temporary.exists():
        raise FileExistsError(temporary)

    try:
        source_connection = sqlite3.connect(
            f"{source.resolve().as_uri()}?mode=ro",
            uri=True,
        )
        destination_connection = sqlite3.connect(temporary)
        try:
            source_connection.backup(destination_connection)
            journal_mode = destination_connection.execute(
                "PRAGMA journal_mode=DELETE"
            ).fetchone()
            if journal_mode != ("delete",):
                raise ValueError("无法将备份转换为独立 SQLite 文件")
        finally:
            destination_connection.close()
            source_connection.close()
        verify_sqlite(temporary)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return BackupResult(path=destination, size=destination.stat().st_size)


def validate_bundle(path: Path) -> BackupManifest:
    bundle = Path(path)
    try:
        payload = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        manifest = BackupManifest(**payload)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("备份清单无效") from exc
    if manifest.format_version != 1:
        raise ValueError("不支持的备份格式")
    if manifest.database != "app.db" or manifest.uploads != "uploads":
        raise ValueError("备份清单包含不安全的路径")
    database = bundle / manifest.database
    uploads = bundle / manifest.uploads
    if not database.is_file() or not uploads.is_dir():
        raise ValueError("备份文件不完整")
    if database.stat().st_size != manifest.database_size:
        raise ValueError("数据库大小与清单不一致")
    if _file_sha256(database) != manifest.database_sha256:
        raise ValueError("数据库哈希与清单不一致")
    verify_sqlite(database)
    return manifest
