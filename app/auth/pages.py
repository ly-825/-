import html

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.service import authenticate_owner, revoke_session
from app.config import settings
from app.database import get_db


router = APIRouter()


def login_page(error: str = "") -> HTMLResponse:
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>老板登录</title><style>
body{{font-family:system-ui,sans-serif;background:#f5f7fb;margin:0;display:grid;place-items:center;min-height:100vh;color:#172033}}
main{{width:min(380px,calc(100% - 40px));background:#fff;padding:28px;border:1px solid #d9dfeb;border-radius:12px}}
label{{display:block;margin:14px 0 6px}}input{{box-sizing:border-box;width:100%;padding:11px;border:1px solid #b8c1d1;border-radius:8px}}
button{{width:100%;margin-top:20px;padding:12px;border:0;border-radius:8px;background:#0f1f46;color:#fff;font-weight:700}}
.error{{color:#b42318;background:#fef3f2;padding:10px;border-radius:8px}}
</style></head><body><main><h1>库存系统</h1><p>老板账号登录</p>{error_html}
<form method="post" action="/auth/login">
<label>账号</label><input name="username" autocomplete="username" required>
<label>密码</label><input name="password" type="password" autocomplete="current-password" required>
<label>动态验证码</label><input name="totp_code" inputmode="numeric" pattern="[0-9]{{6}}" maxlength="6" required>
<button type="submit">登录</button></form></main></body></html>"""
    )


@router.get("/auth/login", response_class=HTMLResponse)
def owner_login_page() -> HTMLResponse:
    return login_page()


@router.post("/auth/login")
def owner_login(
    username: str = Form(...),
    password: str = Form(...),
    totp_code: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        _, token = authenticate_owner(db, username, password, totp_code)
    except ValueError:
        response = login_page("账号或验证信息不正确")
        response.status_code = 401
        return response
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        settings.auth_cookie_name,
        token,
        max_age=settings.pc_session_hours * 3600,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    return response


@router.post("/auth/logout")
def owner_logout(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    raw_token = request.cookies.get(settings.auth_cookie_name, "")
    revoke_session(db, raw_token, "pc")
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie(
        settings.auth_cookie_name,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return response
