from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.context import current_account
from app.auth.roles import EMPLOYEE, PC_ADMIN_ROLES
from app.auth.service import resolve_session
from app.config import settings
from app.database import get_db
from app.models import Account


def raw_request_token(request: Request) -> tuple[str | None, str | None]:
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return authorization[7:].strip(), "miniprogram"
    cookie = request.cookies.get(settings.auth_cookie_name)
    if cookie:
        return cookie, "pc"
    return None, None


def _raise_unauthenticated(request: Request) -> None:
    if request.url.path == "/" or request.url.path.startswith("/admin"):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/auth/login"},
        )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")


async def require_pc_admin_account(
    request: Request,
    db: Session = Depends(get_db),
) -> AsyncGenerator[Account, None]:
    raw_token, client_type = raw_request_token(request)
    if not raw_token or not client_type:
        _raise_unauthenticated(request)
    account = resolve_session(db, raw_token, client_type)
    if not account:
        _raise_unauthenticated(request)
    if account.role not in PC_ADMIN_ROLES or client_type != "pc":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问")
    context_token = current_account.set(account)
    try:
        yield account
    finally:
        current_account.reset(context_token)


require_owner_account = require_pc_admin_account


def _miniprogram_account(request: Request, db: Session) -> Account:
    raw_token, client_type = raw_request_token(request)
    if not raw_token or client_type != "miniprogram":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
        )
    account = resolve_session(db, raw_token, client_type)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
        )
    return account


async def require_miniprogram_account(
    request: Request,
    db: Session = Depends(get_db),
) -> AsyncGenerator[Account, None]:
    account = _miniprogram_account(request, db)
    context_token = current_account.set(account)
    try:
        yield account
    finally:
        current_account.reset(context_token)


async def require_mobile_account(
    request: Request,
    db: Session = Depends(get_db),
) -> AsyncGenerator[Account, None]:
    account = _miniprogram_account(request, db)
    if account.role != EMPLOYEE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问")
    context_token = current_account.set(account)
    try:
        yield account
    finally:
        current_account.reset(context_token)
