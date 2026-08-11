import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BackupResult:
    path: Path
    size: int


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
