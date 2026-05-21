from pathlib import Path

import qrcode

from app.config import settings


def build_qr_code(inventory_id: int) -> str:
    return f"SCRAP-{inventory_id:06d}"


def create_qr_image(qr_code_value: str) -> str:
    Path(settings.qrcode_dir).mkdir(parents=True, exist_ok=True)
    file_path = Path(settings.qrcode_dir) / f"{qr_code_value}.png"
    img = qrcode.make(qr_code_value)
    img.save(file_path)
    return str(file_path)
