from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import ProductDrawing
from app.schemas import DrawingConfirm, DrawingOut
from app.services.dxf_parser import parse_dxf
from app.services.qwen_service import recognize_drawing

router = APIRouter()


@router.post("/upload", response_model=DrawingOut, summary="上传DXF并自动识别")
def upload_drawing(file: UploadFile = File(...), db: Session = Depends(get_db)) -> ProductDrawing:
    if not file.filename or not file.filename.lower().endswith(".dxf"):
        raise HTTPException(status_code=400, detail="请上传DXF文件")

    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    file_name = f"{uuid4().hex}_{file.filename}"
    file_path = Path(settings.upload_dir) / file_name
    file_path.write_bytes(file.file.read())

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
    return drawing


@router.get("/{drawing_id}", response_model=DrawingOut, summary="查看图纸识别结果")
def get_drawing(drawing_id: int, db: Session = Depends(get_db)) -> ProductDrawing:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    return drawing


@router.post("/{drawing_id}/confirm", response_model=DrawingOut, summary="人工确认图纸识别结果")
def confirm_drawing(drawing_id: int, payload: DrawingConfirm, db: Session = Depends(get_db)) -> ProductDrawing:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")

    for key, value in payload.model_dump().items():
        setattr(drawing, key, value)
    drawing.confirmed = 1
    db.commit()
    db.refresh(drawing)
    return drawing

