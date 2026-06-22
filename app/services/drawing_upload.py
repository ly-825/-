from hashlib import sha256
from pathlib import Path
import re
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ProductDrawing
from app.services.drawing_preview import generate_drawing_preview
from app.services.dxf_parser import parse_dxf
from app.services.qwen_service import recognize_drawing


def _safe_original_filename(filename: str) -> str:
    name = Path(filename).name
    name = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", name)
    return name or "drawing.dxf"


def backfill_missing_file_hashes(db: Session) -> None:
    drawings = db.query(ProductDrawing).filter(ProductDrawing.file_hash.is_(None)).all()
    changed = False
    seen_hashes = {
        item.file_hash
        for item in db.query(ProductDrawing).filter(ProductDrawing.file_hash.is_not(None)).all()
        if item.file_hash
    }
    for drawing in drawings:
        file_path = Path(drawing.dxf_file_url)
        if not file_path.exists() or not file_path.is_file():
            continue
        file_hash = sha256(file_path.read_bytes()).hexdigest()
        if file_hash in seen_hashes:
            continue
        drawing.file_hash = file_hash
        seen_hashes.add(file_hash)
        changed = True
    if changed:
        db.commit()


def save_uploaded_drawing(file: UploadFile, db: Session) -> tuple[ProductDrawing, bool]:
    safe_filename = _safe_original_filename(file.filename or "")
    if not safe_filename.lower().endswith(".dxf"):
        raise HTTPException(status_code=400, detail="请上传DXF文件")

    file_bytes = file.file.read()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail=f"DXF文件不能超过{settings.max_upload_size_mb}MB")
    file_hash = sha256(file_bytes).hexdigest()
    backfill_missing_file_hashes(db)
    existing = db.query(ProductDrawing).filter(ProductDrawing.file_hash == file_hash).first()
    if existing:
        if not existing.preview_file_url:
            generate_drawing_preview(existing)
            db.commit()
            db.refresh(existing)
        return existing, True

    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    file_name = f"{uuid4().hex}_{safe_filename}"
    file_path = Path(settings.upload_dir) / file_name
    file_path.write_bytes(file_bytes)

    try:
        candidates = parse_dxf(str(file_path))
        recognized = recognize_drawing(candidates)
        parse_status = "parsed"
    except Exception as exc:
        candidates = {}
        recognized = {"error": str(exc), "need_manual_review": True, "confidence": 0}
        parse_status = "failed"

    gear = candidates.get("gear_candidates", {})
    drawing = ProductDrawing(
        product_code=recognized.get("product_code"),
        product_name=recognized.get("product_name"),
        dxf_file_url=str(file_path),
        file_hash=file_hash,
        material=recognized.get("material"),
        thickness=recognized.get("thickness") or recognized.get("product_thickness"),
        max_outer_diameter=recognized.get("max_outer_diameter"),
        min_inner_diameter=recognized.get("inner_related_diameter"),
        bounding_length=recognized.get("bounding_length") or candidates.get("geometry_summary", {}).get("bounding_box", {}).get("width"),
        bounding_width=recognized.get("bounding_width") or candidates.get("geometry_summary", {}).get("bounding_box", {}).get("height"),
        expected_scrap_size=recognized.get("expected_scrap_usable_size"),
        product_thickness=recognized.get("product_thickness") or gear.get("product_thickness"),
        plate_thickness=recognized.get("plate_thickness") or gear.get("plate_thickness"),
        teeth_count=recognized.get("teeth_count") or gear.get("teeth_count"),
        module=recognized.get("module") or gear.get("module"),
        pressure_angle=recognized.get("pressure_angle") or gear.get("pressure_angle"),
        profile_shift_coefficient=recognized.get("profile_shift_coefficient") or gear.get("profile_shift_coefficient"),
        span_teeth_count=recognized.get("span_teeth_count") or gear.get("span_teeth_count"),
        common_normal_length=recognized.get("common_normal_length") or gear.get("common_normal_length"),
        pin_diameter=recognized.get("pin_diameter") or gear.get("pin_diameter"),
        pin_span=recognized.get("pin_span") or gear.get("pin_span"),
        parse_result_json={"candidates": candidates, "recognized": recognized},
        parse_status=parse_status,
        confirmed=0,
    )
    db.add(drawing)
    db.commit()
    db.refresh(drawing)
    generate_drawing_preview(drawing)
    db.commit()
    db.refresh(drawing)
    return drawing, False


def delete_uploaded_drawing(drawing_id: int, db: Session) -> ProductDrawing:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")

    file_path = Path(drawing.dxf_file_url)
    db.delete(drawing)
    db.commit()
    if file_path.exists() and file_path.is_file():
        file_path.unlink()
    return drawing
