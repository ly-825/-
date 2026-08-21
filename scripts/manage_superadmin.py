#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.roles import SUPERADMIN
from app.auth.service import (
    _create_activation_account,
    _normalized_username,
    regenerate_activation,
    unbind_wechat,
)
from app.database import SessionLocal
from app.models import Account


def bootstrap_superadmin(
    db: Session,
    username: str,
    display_name: str,
    now=None,
) -> tuple[Account, str]:
    if db.query(Account).filter(Account.role == SUPERADMIN).first():
        raise ValueError("主管理员已存在")
    try:
        return _create_activation_account(
            db, username, display_name, SUPERADMIN, now=now
        )
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("主管理员已存在") from exc


def _sole_superadmin(db: Session, username: str) -> Account:
    superadmins = db.query(Account).filter(Account.role == SUPERADMIN).all()
    normalized = _normalized_username(username)
    if len(superadmins) != 1 or superadmins[0].username != normalized:
        raise ValueError("主管理员账号不存在或不唯一")
    return superadmins[0]


def reset_superadmin_wechat(
    db: Session,
    username: str,
    now=None,
) -> tuple[Account, str]:
    account = _sole_superadmin(db, username)
    if account.wechat_openid:
        code = unbind_wechat(db, account, now=now)
    else:
        code = regenerate_activation(db, account, now=now)
    return account, code


def main() -> None:
    parser = argparse.ArgumentParser(description="管理唯一主管理员的微信绑定")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser("bootstrap", help="创建唯一主管理员")
    bootstrap.add_argument("--username", default="admin")
    bootstrap.add_argument("--display-name", default="主管理员")

    reset = subparsers.add_parser("reset-wechat", help="重置主管理员微信绑定")
    reset.add_argument("--username", required=True)

    args = parser.parse_args()
    with SessionLocal() as db:
        if args.command == "bootstrap":
            account, code = bootstrap_superadmin(
                db, args.username, args.display_name
            )
        else:
            account, code = reset_superadmin_wechat(db, args.username)

    print(f"主管理员账号：{account.username}")
    print("一次性激活码（30 分钟内有效）：")
    print(code)


if __name__ == "__main__":
    main()
