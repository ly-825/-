from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require_mobile_account
from app.auth.service import activate_employee, create_session, revoke_session
from app.config import settings
from app.database import get_db
from app.models import Account
from app.services.wechat_auth import exchange_code_for_openid
from app.time_utils import china_now


router = APIRouter(prefix="/api/auth")


class EmployeeActivationIn(BaseModel):
    username: str
    activation_code: str
    wx_code: str


class WechatLoginIn(BaseModel):
    wx_code: str


def _safe_account(account: Account) -> dict[str, str]:
    return {"display_name": account.display_name, "role": account.role}


def _auth_response(account: Account, token: str) -> dict[str, object]:
    return {"token": token, "account": _safe_account(account)}


@router.post("/wechat/activate")
def wechat_activate(
    payload: EmployeeActivationIn,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        openid = exchange_code_for_openid(payload.wx_code)
        account, token = activate_employee(
            db,
            payload.username,
            payload.activation_code,
            openid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _auth_response(account, token)


@router.post("/wechat/login")
def wechat_login(
    payload: WechatLoginIn,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        openid = exchange_code_for_openid(payload.wx_code)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="微信登录失败，请重试") from exc
    account = db.query(Account).filter(Account.wechat_openid == openid).first()
    if not account or account.role != "employee" or not account.is_active:
        raise HTTPException(status_code=401, detail="员工账号未绑定或已停用")
    token = create_session(
        db,
        account,
        "miniprogram",
        china_now() + timedelta(days=settings.mobile_session_days),
    )
    return _auth_response(account, token)


@router.get("/me")
def current_employee(
    account: Account = Depends(require_mobile_account),
) -> dict[str, str]:
    return _safe_account(account)


@router.post("/logout")
def mobile_logout(
    request: Request,
    db: Session = Depends(get_db),
    account: Account = Depends(require_mobile_account),
) -> dict[str, bool]:
    authorization = request.headers.get("Authorization", "")
    raw_token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    revoke_session(db, raw_token, "miniprogram")
    return {"ok": True}
