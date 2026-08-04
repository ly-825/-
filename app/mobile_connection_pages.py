import html

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.admin_pages import page
from app.services.mobile_connection import (
    build_connection_qr_data_uri,
    discover_lan_ipv4_addresses,
    normalize_mobile_base_url,
)


router = APIRouter()


@router.get("/admin/mobile-connection", response_class=HTMLResponse)
def mobile_connection_page(host: str = "", port: int = 8000) -> HTMLResponse:
    addresses = discover_lan_ipv4_addresses()
    selected_host = host.strip() or (addresses[0] if addresses else "")
    base_url = ""
    qr_uri = ""
    error = ""
    if selected_host:
        try:
            base_url = normalize_mobile_base_url(f"http://{selected_host}:{port}")
            qr_uri = build_connection_qr_data_uri(base_url)
        except HTTPException as exc:
            error = str(exc.detail)
    address_options = "".join(
        f'<option value="{html.escape(address)}"></option>' for address in addresses
    )
    body = f"""
    <div class="top"><div><h1>小程序连接</h1><p class="muted">手机连接工厂 Wi-Fi 后，扫描二维码保存后台地址。</p></div></div>
    <section class="card">
      <form method="get" action="/admin/mobile-connection" class="form-grid">
        <div><label>局域网 IP</label><input name="host" list="lan-addresses" value="{html.escape(selected_host)}" placeholder="例如 192.168.31.68"><datalist id="lan-addresses">{address_options}</datalist></div>
        <div><label>端口</label><input name="port" type="number" min="1" max="65535" value="{port}"></div>
        <button class="btn" type="submit">生成连接二维码</button>
      </form>
    </section>
    <section class="card">
      {f'<p style="color:#dc2626">{html.escape(error)}</p>' if error else ''}
      {f'<h2>当前地址</h2><p><strong>{html.escape(base_url)}</strong></p><img src="{qr_uri}" alt="小程序连接二维码" width="280" height="280"><p class="muted">二维码只包含版本号和连接地址。</p>' if qr_uri else '<p class="muted">未检测到局域网地址，请手工输入电脑 IP。</p>'}
    </section>
    """
    return page("小程序连接", body)
