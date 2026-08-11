#!/usr/bin/env python3
import argparse
import getpass

import pyotp
from sqlalchemy.orm import Session

from app.auth.service import create_owner
from app.config import settings
from app.database import SessionLocal
from app.models import Account


def validate_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("密码至少需要 12 位")
    return password


def bootstrap_owner(
    db: Session,
    *,
    username: str,
    display_name: str,
    password: str,
    totp_secret: str,
) -> tuple[Account, str]:
    if db.query(Account).filter(Account.role == "owner").first():
        raise ValueError("老板账号已存在")
    account = create_owner(
        db,
        username=username,
        display_name=display_name,
        password=validate_password(password),
    )
    provisioning_uri = pyotp.TOTP(totp_secret).provisioning_uri(
        name=account.username,
        issuer_name="杭州特耐时库存系统",
    )
    return account, provisioning_uri


def main() -> None:
    parser = argparse.ArgumentParser(description="创建唯一的老板账号")
    parser.add_argument("--username", default="owner")
    parser.add_argument("--display-name", default="老板")
    args = parser.parse_args()

    if not settings.owner_totp_secret:
        raise SystemExit("OWNER_TOTP_SECRET 未配置")
    password = getpass.getpass("老板登录密码（至少 12 位）：")
    password_confirmation = getpass.getpass("再次输入密码：")
    if password != password_confirmation:
        raise SystemExit("两次输入的密码不一致")

    with SessionLocal() as db:
        account, provisioning_uri = bootstrap_owner(
            db,
            username=args.username,
            display_name=args.display_name,
            password=password,
            totp_secret=settings.owner_totp_secret,
        )
    print(f"老板账号已创建：{account.username}")
    print("请立即在身份验证器中添加以下一次性配置地址：")
    print(provisioning_uri)


if __name__ == "__main__":
    main()
