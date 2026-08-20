import base64
import io
from datetime import datetime

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require_miniprogram_account
from app.auth.pc_login_service import (
    consume_login_request,
    create_login_challenge,
    decide_login_request,
    poll_login_request,
    scan_login_request,
)
from app.config import settings
from app.database import get_db
from app.models import Account


router = APIRouter(prefix="/api/auth/pc-login")


class CreateLoginRequestIn(BaseModel):
    device_summary: str | None = None


class RequestTokenIn(BaseModel):
    request_token: str


class BrowserRequestIn(RequestTokenIn):
    browser_secret: str


class DecisionIn(RequestTokenIn):
    approved: bool


def _qr_data_url(payload: str) -> str:
    image = qrcode.make(payload)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    message = str(exc)
    if "过期" in message:
        return HTTPException(status_code=410, detail=message)
    if "不存在" in message:
        return HTTPException(status_code=404, detail=message)
    if "验证失败" in message:
        return HTTPException(status_code=401, detail=message)
    return HTTPException(status_code=409, detail=message)


@router.post("/requests")
def create_request(
    payload: CreateLoginRequestIn,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str | datetime]:
    challenge = create_login_challenge(
        db,
        payload.device_summary,
        request.client.host if request.client else None,
    )
    qr_payload = f"tns-inventory-login:v1:{challenge.request_token}"
    return {
        "request_token": challenge.request_token,
        "browser_secret": challenge.browser_secret,
        "qr_payload": qr_payload,
        "qr_image_data_url": _qr_data_url(qr_payload),
        "expires_at": challenge.expires_at,
    }


@router.post("/status")
def request_status(
    payload: BrowserRequestIn,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    try:
        state = poll_login_request(
            db, payload.request_token, payload.browser_secret
        )
    except (ValueError, PermissionError) as exc:
        raise _http_error(exc) from exc
    return {"status": state}


@router.post("/scan")
def scan_request(
    payload: RequestTokenIn,
    request: Request,
    account: Account = Depends(require_miniprogram_account),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    try:
        summary = scan_login_request(db, payload.request_token, account)
    except (ValueError, PermissionError) as exc:
        raise _http_error(exc) from exc
    return {
        **summary,
        "system_name": "杭州特耐时库存系统",
        "verified_domain": request.url.hostname or "",
    }


@router.post("/decision")
def decide_request(
    payload: DecisionIn,
    account: Account = Depends(require_miniprogram_account),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    try:
        state = decide_login_request(
            db, payload.request_token, account, payload.approved
        )
    except (ValueError, PermissionError) as exc:
        raise _http_error(exc) from exc
    return {"status": state}


@router.post("/consume")
def consume_request(
    payload: BrowserRequestIn,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        _, token = consume_login_request(
            db, payload.request_token, payload.browser_secret
        )
    except (ValueError, PermissionError) as exc:
        raise _http_error(exc) from exc
    response.set_cookie(
        settings.auth_cookie_name,
        token,
        max_age=settings.pc_session_hours * 3600,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    return {"ok": True, "redirect_to": "/admin"}
