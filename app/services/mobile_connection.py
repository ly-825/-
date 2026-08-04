import base64
import ipaddress
import io
import json
import socket
from urllib.parse import urlsplit

import qrcode
from fastapi import HTTPException


CONNECTION_PAYLOAD_VERSION = 1


def normalize_mobile_base_url(value: str) -> str:
    text = (value or "").strip().rstrip("/")
    parsed = urlsplit(text)
    if parsed.scheme != "http" or not parsed.hostname or parsed.path not in ("", "/"):
        raise HTTPException(
            status_code=400,
            detail="请输入厂内 HTTP 地址，例如 http://192.168.1.20:8000",
        )
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="连接地址必须使用厂内局域网 IP") from exc
    if not address.is_private or address.is_loopback or address.is_link_local:
        raise HTTPException(status_code=400, detail="连接地址必须使用厂内局域网 IP")
    try:
        port = parsed.port or 80
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="端口必须在 1 到 65535 之间") from exc
    if port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="端口必须在 1 到 65535 之间")
    return f"http://{address.compressed}:{port}"


def discover_lan_ipv4_addresses() -> list[str]:
    candidates: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.add(info[4][0])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            candidates.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(
        value
        for value in candidates
        if (address := ipaddress.ip_address(value)).is_private
        and not address.is_loopback
        and not address.is_link_local
    )


def build_connection_payload(base_url: str) -> dict[str, object]:
    return {
        "version": CONNECTION_PAYLOAD_VERSION,
        "base_url": normalize_mobile_base_url(base_url),
    }


def build_connection_qr_data_uri(base_url: str) -> str:
    content = json.dumps(
        build_connection_payload(base_url),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    image = qrcode.make(content)
    output = io.BytesIO()
    image.save(output, format="PNG")
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
