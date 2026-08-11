from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.auth.security import (
    hash_password,
    hash_secret,
    new_activation_code,
    new_session_token,
    secrets_match,
    verify_password,
    verify_totp,
)
from app.config import settings
from app.models import Account, AuthSession
from app.time_utils import china_now


def _normalized_username(value: str) -> str:
    username = value.strip().lower()
    if not username:
        raise ValueError("账号不能为空")
    return username


def _required_pepper() -> str:
    if not settings.auth_pepper:
        raise RuntimeError("AUTH_PEPPER 未配置")
    return settings.auth_pepper


def _required_totp_secret() -> str:
    if not settings.owner_totp_secret:
        raise RuntimeError("OWNER_TOTP_SECRET 未配置")
    return settings.owner_totp_secret


def _clock(now: datetime | None) -> datetime:
    return now or china_now()


def create_owner(
    db: Session,
    username: str,
    display_name: str,
    password: str,
) -> Account:
    normalized_username = _normalized_username(username)
    if len(password) < 12:
        raise ValueError("密码至少需要 12 位")
    if db.query(Account).filter(Account.username == normalized_username).first():
        raise ValueError("账号已存在")
    account = Account(
        username=normalized_username,
        display_name=display_name.strip() or "老板",
        role="owner",
        password_hash=hash_password(password),
        is_active=True,
        session_version=1,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def create_session(
    db: Session,
    account: Account,
    client_type: str,
    expires_at: datetime,
) -> str:
    if client_type not in {"pc", "miniprogram"}:
        raise ValueError("不支持的客户端类型")
    raw_token = new_session_token()
    session = AuthSession(
        token_hash=hash_secret(raw_token, _required_pepper()),
        account_id=account.id,
        session_version=account.session_version,
        client_type=client_type,
        expires_at=expires_at,
    )
    db.add(session)
    db.commit()
    return raw_token


def authenticate_owner(
    db: Session,
    username: str,
    password: str,
    totp_code: str,
    now: datetime | None = None,
) -> tuple[Account, str]:
    current_time = _clock(now)
    account = (
        db.query(Account)
        .filter(Account.username == _normalized_username(username))
        .first()
    )
    valid_account = bool(
        account
        and account.role == "owner"
        and account.is_active
        and account.password_hash
        and verify_password(account.password_hash, password)
        and verify_totp(_required_totp_secret(), totp_code, current_time)
    )
    if not valid_account or account is None:
        raise ValueError("账号或验证信息不正确")
    token = create_session(
        db,
        account,
        "pc",
        current_time + timedelta(hours=settings.pc_session_hours),
    )
    return account, token


def create_employee(
    db: Session,
    username: str,
    display_name: str,
    now: datetime | None = None,
) -> tuple[Account, str]:
    normalized_username = _normalized_username(username)
    if db.query(Account).filter(Account.username == normalized_username).first():
        raise ValueError("账号已存在")
    current_time = _clock(now)
    activation_code = new_activation_code()
    account = Account(
        username=normalized_username,
        display_name=display_name.strip(),
        role="employee",
        activation_code_hash=hash_secret(activation_code, _required_pepper()),
        activation_expires_at=current_time + timedelta(hours=24),
        is_active=True,
        session_version=1,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account, activation_code


def activate_employee(
    db: Session,
    username: str,
    activation_code: str,
    wechat_openid: str,
    now: datetime | None = None,
) -> tuple[Account, str]:
    current_time = _clock(now)
    account = (
        db.query(Account)
        .filter(Account.username == _normalized_username(username))
        .first()
    )
    activation_is_valid = bool(
        account
        and account.role == "employee"
        and account.is_active
        and account.activation_code_hash
        and account.activation_expires_at
        and account.activation_expires_at > current_time
        and secrets_match(
            activation_code,
            account.activation_code_hash,
            _required_pepper(),
        )
    )
    if not activation_is_valid or account is None:
        raise ValueError("激活码无效或已过期")
    existing_binding = (
        db.query(Account)
        .filter(Account.wechat_openid == wechat_openid, Account.id != account.id)
        .first()
    )
    if existing_binding:
        raise ValueError("微信已绑定其他员工")
    account.wechat_openid = wechat_openid
    account.activation_code_hash = None
    account.activation_expires_at = None
    db.commit()
    token = create_session(
        db,
        account,
        "miniprogram",
        current_time + timedelta(days=settings.mobile_session_days),
    )
    return account, token


def resolve_session(
    db: Session,
    raw_token: str,
    client_type: str,
    now: datetime | None = None,
) -> Account | None:
    if not raw_token:
        return None
    current_time = _clock(now)
    session = (
        db.query(AuthSession)
        .filter(
            AuthSession.token_hash == hash_secret(raw_token, _required_pepper()),
            AuthSession.client_type == client_type,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > current_time,
        )
        .first()
    )
    if not session:
        return None
    account = db.get(Account, session.account_id)
    if (
        not account
        or not account.is_active
        or account.session_version != session.session_version
    ):
        return None
    session.last_seen_at = current_time
    db.flush()
    return account


def revoke_session(
    db: Session,
    raw_token: str,
    client_type: str,
    now: datetime | None = None,
) -> bool:
    if not raw_token:
        return False
    session = (
        db.query(AuthSession)
        .filter(
            AuthSession.token_hash == hash_secret(raw_token, _required_pepper()),
            AuthSession.client_type == client_type,
            AuthSession.revoked_at.is_(None),
        )
        .first()
    )
    if not session:
        return False
    session.revoked_at = _clock(now)
    db.commit()
    return True


def disable_account(db: Session, account: Account) -> None:
    account.is_active = False
    account.session_version += 1
    db.commit()


def unbind_wechat(
    db: Session,
    account: Account,
    now: datetime | None = None,
) -> str:
    current_time = _clock(now)
    activation_code = new_activation_code()
    account.wechat_openid = None
    account.activation_code_hash = hash_secret(activation_code, _required_pepper())
    account.activation_expires_at = current_time + timedelta(hours=24)
    account.session_version += 1
    db.commit()
    return activation_code
