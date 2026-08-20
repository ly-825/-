from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.auth.roles import PC_ADMIN_ROLES
from app.auth.security import hash_secret, new_session_token, secrets_match
from app.auth.service import _clock, _required_pepper, create_session
from app.config import settings
from app.models import Account, PcLoginRequest
from app.services.operation_log import record_operation_log


@dataclass(frozen=True)
class LoginChallenge:
    request_token: str
    browser_secret: str
    expires_at: datetime


def _request(db: Session, request_token: str) -> PcLoginRequest:
    row = db.query(PcLoginRequest).filter(
        PcLoginRequest.request_token_hash
        == hash_secret(request_token, _required_pepper())
    ).first()
    if row is None:
        raise ValueError("登录请求不存在")
    return row


def _require_admin(account: Account) -> None:
    if not account.is_active or account.role not in PC_ADMIN_ROLES:
        raise PermissionError("无权确认电脑登录")


def _expire_if_needed(db: Session, row: PcLoginRequest, now: datetime) -> None:
    if row.expires_at <= now and row.status in {"pending", "scanned"}:
        row.status = "expired"
        db.commit()
    if row.expires_at <= now or row.status == "expired":
        raise ValueError("登录请求已过期")


def create_login_challenge(
    db: Session,
    device_summary: str | None,
    source_ip: str | None,
    now: datetime | None = None,
) -> LoginChallenge:
    current_time = _clock(now)
    db.query(PcLoginRequest).filter(
        PcLoginRequest.expires_at < current_time - timedelta(days=1),
        PcLoginRequest.status.in_(("expired", "denied", "consumed")),
    ).delete(synchronize_session=False)
    request_token = new_session_token()
    browser_secret = new_session_token()
    row = PcLoginRequest(
        request_token_hash=hash_secret(request_token, _required_pepper()),
        browser_secret_hash=hash_secret(browser_secret, _required_pepper()),
        status="pending",
        device_summary=(device_summary or "未知浏览器")[:200],
        source_ip=(source_ip or "")[:64] or None,
        expires_at=current_time
        + timedelta(seconds=settings.pc_login_request_seconds),
        created_at=current_time,
    )
    db.add(row)
    db.commit()
    return LoginChallenge(request_token, browser_secret, row.expires_at)


def scan_login_request(
    db: Session,
    request_token: str,
    account: Account,
    now: datetime | None = None,
) -> dict[str, str]:
    _require_admin(account)
    current_time = _clock(now)
    row = _request(db, request_token)
    _expire_if_needed(db, row, current_time)
    if row.status == "scanned":
        return {
            "status": "scanned",
            "device_summary": row.device_summary or "未知浏览器",
            "requested_at": row.created_at.isoformat(),
            "expires_at": row.expires_at.isoformat(),
        }
    updated = db.query(PcLoginRequest).filter(
        PcLoginRequest.id == row.id,
        PcLoginRequest.status == "pending",
        PcLoginRequest.expires_at > current_time,
    ).update({"status": "scanned"}, synchronize_session=False)
    if updated != 1:
        raise ValueError("登录请求不可扫描")
    record_operation_log(
        db,
        "pc_login_scanned",
        "pc_login_request",
        row.id,
        after_data={"status": "scanned", "account_id": account.id},
    )
    db.commit()
    return {
        "status": "scanned",
        "device_summary": row.device_summary or "未知浏览器",
        "requested_at": row.created_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
    }


def decide_login_request(
    db: Session,
    request_token: str,
    account: Account,
    approved: bool,
    now: datetime | None = None,
) -> str:
    _require_admin(account)
    current_time = _clock(now)
    row = _request(db, request_token)
    _expire_if_needed(db, row, current_time)
    new_status = "approved" if approved else "denied"
    values: dict[str, object] = {"status": new_status}
    if approved:
        values.update({
            "approved_account_id": account.id,
            "approved_at": current_time,
        })
    updated = db.query(PcLoginRequest).filter(
        PcLoginRequest.id == row.id,
        PcLoginRequest.status.in_(("pending", "scanned")),
        PcLoginRequest.expires_at > current_time,
    ).update(values, synchronize_session=False)
    if updated != 1:
        raise ValueError("登录请求已处理")
    record_operation_log(
        db,
        "pc_login_approved" if approved else "pc_login_denied",
        "pc_login_request",
        row.id,
        after_data={"status": new_status, "account_id": account.id},
    )
    db.commit()
    return new_status


def poll_login_request(
    db: Session,
    request_token: str,
    browser_secret: str,
    now: datetime | None = None,
) -> str:
    current_time = _clock(now)
    row = _request(db, request_token)
    if not secrets_match(
        browser_secret, row.browser_secret_hash, _required_pepper()
    ):
        raise ValueError("浏览器验证失败")
    if row.expires_at <= current_time and row.status in {"pending", "scanned"}:
        row.status = "expired"
        db.commit()
        return "expired"
    return row.status


def consume_login_request(
    db: Session,
    request_token: str,
    browser_secret: str,
    now: datetime | None = None,
) -> tuple[Account, str]:
    current_time = _clock(now)
    row = _request(db, request_token)
    if not secrets_match(
        browser_secret, row.browser_secret_hash, _required_pepper()
    ):
        raise ValueError("浏览器验证失败")
    _expire_if_needed(db, row, current_time)
    if row.status != "approved" or row.approved_account_id is None:
        raise ValueError("登录请求不可使用")
    account = db.get(Account, row.approved_account_id)
    if account is None or not account.is_active or account.role not in PC_ADMIN_ROLES:
        raise ValueError("确认账号已失效")
    updated = db.query(PcLoginRequest).filter(
        PcLoginRequest.id == row.id,
        PcLoginRequest.status == "approved",
        PcLoginRequest.expires_at > current_time,
    ).update(
        {"status": "consumed", "consumed_at": current_time},
        synchronize_session=False,
    )
    if updated != 1:
        raise ValueError("登录请求不可使用")
    token = create_session(
        db,
        account,
        "pc",
        current_time + timedelta(hours=settings.pc_session_hours),
    )
    return account, token
