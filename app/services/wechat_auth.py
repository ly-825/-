import httpx

from app.config import settings


WECHAT_CODE_SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


def exchange_code_for_openid(code: str) -> str:
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        raise RuntimeError("微信小程序认证配置不完整")
    try:
        response = httpx.get(
            WECHAT_CODE_SESSION_URL,
            params={
                "appid": settings.wechat_app_id,
                "secret": settings.wechat_app_secret,
                "js_code": code,
                "grant_type": "authorization_code",
            },
            timeout=8.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ValueError("微信登录失败，请重试") from exc
    if not isinstance(payload, dict):
        raise ValueError("微信登录失败，请重试")
    openid = payload.get("openid")
    if payload.get("errcode") or not openid:
        raise ValueError("微信登录失败，请重试")
    return str(openid)
