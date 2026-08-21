from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.auth.roles import ACCOUNT_ROLES, EMPLOYEE, OWNER, can_manage_role
from app.auth.security import (
    hash_password,
    hash_secret,
    new_activation_code,
    new_session_token,
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
    return _create_activation_account(
        db, username, display_name, EMPLOYEE, now=now
    )


def activation_lifetime(role: str) -> timedelta:
    if role not in ACCOUNT_ROLES:
        raise ValueError("不支持的账号角色")
    return timedelta(hours=24) if role == EMPLOYEE else timedelta(minutes=30)


def _create_activation_account(
    db: Session,
    username: str,
    display_name: str,
    role: str,
    now: datetime | None = None,
    *,
    commit: bool = True,
) -> tuple[Account, str]:
    normalized_username = _normalized_username(username)
    if db.query(Account).filter(Account.username == normalized_username).first():
        raise ValueError("账号已存在")
    current_time = _clock(now)
    activation_code = new_activation_code()
    account = Account(
        username=normalized_username,
        display_name=display_name.strip(),
        role=role,
        activation_code_hash=hash_secret(activation_code, _required_pepper()),
        activation_expires_at=current_time + activation_lifetime(role),
        is_active=True,
        session_version=1,
    )
    db.add(account)
    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(account)
    return account, activation_code


def create_managed_account(
    db: Session,
    *,
    actor: Account,
    username: str,
    display_name: str,
    role: str,
    now: datetime | None = None,
    commit: bool = True,
) -> tuple[Account, str]:
    if not can_manage_role(actor.role, role):
        raise ValueError("无权创建该角色账号")
    return _create_activation_account(
        db, username, display_name, role, now=now, commit=commit
    )


def activate_employee(
    db: Session,
    username: str,
    activation_code: str,
    wechat_openid: str,
    now: datetime | None = None,
) -> tuple[Account, str]:
    account = (
        db.query(Account)
        .filter(Account.username == _normalized_username(username))
        .first()
    )
    if account is None or account.role != EMPLOYEE:
        raise ValueError("激活码无效或已过期")
    return activate_account(db, username, activation_code, wechat_openid, now=now)


def activate_account(
    db: Session,
    username: str,
    activation_code: str,
    wechat_openid: str,
    now: datetime | None = None,
) -> tuple[Account, str]:
    current_time = _clock(now)
    normalized_username = _normalized_username(username)
    expected_hash = hash_secret(activation_code, _required_pepper())
    try:
        updated = db.query(Account).filter(
            Account.username == normalized_username,
            Account.role.in_(ACCOUNT_ROLES),
            Account.is_active.is_(True),
            Account.wechat_openid.is_(None),
            Account.activation_code_hash == expected_hash,
            Account.activation_expires_at > current_time,
        ).update(
            {
                "wechat_openid": wechat_openid,
                "activation_code_hash": None,
                "activation_expires_at": None,
            },
            synchronize_session=False,
        )
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("微信已绑定其他账号") from exc
    if updated != 1:
        db.rollback()
        raise ValueError("激活码无效或已过期")
    account = db.query(Account).filter(Account.username == normalized_username).one()
    token = create_session(
        db,
        account,
        "miniprogram",
        current_time + timedelta(days=settings.mobile_session_days),
    )
    return account, token


def login_bound_wechat(
    db: Session,
    wechat_openid: str,
    now: datetime | None = None,
) -> tuple[Account, str]:
    current_time = _clock(now)
    account = (
        db.query(Account)
        .filter(
            Account.wechat_openid == wechat_openid,
            Account.is_active.is_(True),
            Account.role.in_(ACCOUNT_ROLES),
        )
        .first()
    )
    if account is None:
        raise ValueError("微信账号未绑定或已停用")
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


def disable_account(db: Session, account: Account, *, commit: bool = True) -> None:
    account.is_active = False
    revoke_all_sessions(db, account, commit=False)
    if commit:
        db.commit()
    else:
        db.flush()


def enable_account(db: Session, account: Account, *, commit: bool = True) -> None:
    account.is_active = True
    if commit:
        db.commit()
    else:
        db.flush()


def regenerate_activation(
    db: Session,
    account: Account,
    now: datetime | None = None,
    *,
    commit: bool = True,
) -> str:
    if account.wechat_openid:
        raise ValueError("请先解绑微信")
    current_time = _clock(now)
    activation_code = new_activation_code()
    account.activation_code_hash = hash_secret(activation_code, _required_pepper())
    account.activation_expires_at = current_time + activation_lifetime(account.role)
    revoke_all_sessions(db, account, commit=False)
    if commit:
        db.commit()
    else:
        db.flush()
    return activation_code


def unbind_wechat(
    db: Session,
    account: Account,
    now: datetime | None = None,
    *,
    commit: bool = True,
) -> str:
    current_time = _clock(now)
    activation_code = new_activation_code()
    account.wechat_openid = None
    account.activation_code_hash = hash_secret(activation_code, _required_pepper())
    account.activation_expires_at = current_time + activation_lifetime(account.role)
    revoke_all_sessions(db, account, commit=False)
    if commit:
        db.commit()
    else:
        db.flush()
    return activation_code


def revoke_all_sessions(
    db: Session,
    account: Account,
    *,
    commit: bool = True,
) -> None:
    account.session_version += 1
    if commit:
        db.commit()
