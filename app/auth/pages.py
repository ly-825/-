import html

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.service import (
    authenticate_owner,
    revoke_session,
)
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
<form method="post" action="/auth/legacy-login">
<label>账号</label><input name="username" autocomplete="username" required>
<label>密码</label><input name="password" type="password" autocomplete="current-password" required>
<label>动态验证码</label><input name="totp_code" inputmode="numeric" pattern="[0-9]{{6}}" maxlength="6" required>
<button type="submit">登录</button></form></main></body></html>"""
    )


@router.get("/auth/login", response_class=HTMLResponse)
def owner_login_page() -> HTMLResponse:
    return qr_login_page()


def qr_login_page() -> HTMLResponse:
    return HTMLResponse("""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>扫码登录库存系统</title><style>
body{font-family:system-ui,sans-serif;background:#f5f7fb;margin:0;display:grid;place-items:center;min-height:100vh;color:#172033}
main{width:min(420px,calc(100% - 40px));background:#fff;padding:28px;border:1px solid #d9dfeb;border-radius:12px;text-align:center}
#qr{width:240px;height:240px;display:block;margin:18px auto}.muted{color:#667085}button{padding:11px 18px;border:0;border-radius:8px;background:#0f1f46;color:#fff;font-weight:700}
</style></head><body><main><h1>使用小程序扫码登录</h1><p class="muted">请使用已绑定的主管理员或老板微信，在杭州特耐时库存小程序中扫码并确认。</p>
<img id="qr" alt="登录二维码"><p id="status" aria-live="polite">正在生成安全二维码…</p><button id="refresh" hidden>刷新二维码</button>
<script>
(() => {
  const qr = document.getElementById('qr');
  const status = document.getElementById('status');
  const refresh = document.getElementById('refresh');
  let timer = null;
  let requestToken = '';
  let browserSecret = '';
  const post = async (path, body) => {
    const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '请求失败');
    return data;
  };
  const stop = (message) => { if (timer) clearTimeout(timer); status.textContent = message; refresh.hidden = false; };
  const poll = async () => {
    try {
      const data = await post('/api/auth/pc-login/status', {request_token:requestToken, browser_secret:browserSecret});
      if (data.status === 'approved') {
        const result = await post('/api/auth/pc-login/consume', {request_token:requestToken, browser_secret:browserSecret});
        window.location.replace(result.redirect_to);
        return;
      }
      if (data.status === 'denied') return stop('本次登录已拒绝');
      if (data.status === 'expired') return stop('二维码已过期');
      status.textContent = data.status === 'scanned' ? '请在手机上确认登录' : '等待扫码…';
      timer = setTimeout(poll, 1500);
    } catch (error) { stop(error.message); }
  };
  const create = async () => {
    refresh.hidden = true; status.textContent = '正在生成安全二维码…'; qr.removeAttribute('src');
    try {
      const data = await post('/api/auth/pc-login/requests', {device_summary:navigator.userAgent.slice(0, 200)});
      requestToken = data.request_token; browserSecret = data.browser_secret; qr.src = data.qr_image_data_url;
      status.textContent = '等待扫码…'; timer = setTimeout(poll, 1500);
    } catch (error) { stop(error.message); }
  };
  refresh.addEventListener('click', create); create();
})();
</script></main></body></html>""")


@router.get("/auth/legacy-login", response_class=HTMLResponse)
def legacy_owner_login_page() -> HTMLResponse:
    if not settings.legacy_password_login_enabled:
        raise HTTPException(status_code=404, detail="页面不存在")
    return login_page()


@router.post("/auth/legacy-login")
def owner_login(
    username: str = Form(...),
    password: str = Form(...),
    totp_code: str = Form(...),
    db: Session = Depends(get_db),
):
    if not settings.legacy_password_login_enabled:
        raise HTTPException(status_code=404, detail="页面不存在")
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
