#!/usr/bin/env python3
import argparse
import secrets
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backup import validate_bundle  # noqa: E402
from app.time_utils import china_now  # noqa: E402


def restore_bundle(
    bundle: Path,
    target: Path,
    *,
    replace: bool = False,
    confirmation: str = "",
) -> Path:
    validate_bundle(bundle)
    bundle = Path(bundle)
    target = Path(target)
    preserved: Path | None = None
    if target.exists():
        if not replace:
            raise FileExistsError(f"目标已存在：{target}")
        if confirmation != "RESTORE":
            raise ValueError("覆盖恢复必须使用 --confirm RESTORE")
        preserved = target.parent / f"pre-restore-{china_now().strftime('%Y-%m-%d_%H%M%S')}"
        if preserved.exists():
            raise FileExistsError(f"恢复前保留目录已存在：{preserved}")
        target.rename(preserved)

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.parent / f".restore-partial-{secrets.token_hex(6)}"
    try:
        partial.mkdir()
        shutil.copy2(bundle / "app.db", partial / "app.db")
        shutil.copytree(bundle / "uploads", partial / "uploads")
        shutil.copy2(bundle / "manifest.json", partial / "manifest.json")
        validate_bundle(partial)
        partial.rename(target)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        if preserved and preserved.exists() and not target.exists():
            preserved.rename(target)
        raise
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="验证或恢复 SQLite 备份包")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    validate_bundle(args.bundle)
    if args.target is None:
        print(f"备份验证通过：{args.bundle}")
        return
    restored = restore_bundle(
        args.bundle,
        args.target,
        replace=args.replace,
        confirmation=args.confirm,
    )
    print(f"备份已恢复到：{restored}")


if __name__ == "__main__":
    try:
        main()
    except (FileExistsError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
