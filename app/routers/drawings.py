from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ProductDrawing
from app.schemas import DrawingConfirm, DrawingOut, DrawingUploadOut
from app.services.drawing_parameters import common_normal_value_from_text, first_int_value, normalize_tooth_type, plain_float_value
from app.services.drawing_upload import delete_uploaded_drawing, save_uploaded_drawing
from app.services.drawing_version import apply_drawing_version
from app.services.inventory_service import ensure_drawing_can_be_changed
from app.services.operation_log import drawing_snapshot, record_operation_log

router = APIRouter()


@router.post("/upload", response_model=DrawingUploadOut, summary="上传DXF并自动识别")
def upload_drawing(file: UploadFile = File(...), db: Session = Depends(get_db)) -> DrawingUploadOut:
    drawing, duplicated = save_uploaded_drawing(file, db)
    record_operation_log(
        db,
        "drawing_upload",
        "drawing",
        drawing.id,
        None,
        "API重复图纸上传" if duplicated else "API上传图纸",
        after_data=drawing_snapshot(drawing),
    )
    db.commit()
    return DrawingUploadOut(drawing=drawing, duplicated=duplicated)


@router.get("/{drawing_id}", response_model=DrawingOut, summary="查看图纸识别结果")
def get_drawing(drawing_id: int, db: Session = Depends(get_db)) -> ProductDrawing:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    return drawing


@router.delete("/{drawing_id}", summary="删除图纸")
def delete_drawing(drawing_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")
    ensure_drawing_can_be_changed(drawing, db)
    before_data = drawing_snapshot(drawing)
    delete_uploaded_drawing(drawing_id, db)
    record_operation_log(db, "drawing_delete", "drawing", drawing_id, None, "API删除图纸", before_data=before_data)
    db.commit()
    return {"id": drawing_id, "message": "图纸已删除"}


@router.post("/{drawing_id}/confirm", response_model=DrawingOut, summary="人工确认图纸识别结果")
def confirm_drawing(drawing_id: int, payload: DrawingConfirm, db: Session = Depends(get_db)) -> ProductDrawing:
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="图纸不存在")

    was_confirmed = drawing.confirmed == 1
    before_data = drawing_snapshot(drawing)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(drawing, key, value)
    drawing.tooth_type = normalize_tooth_type(drawing.tooth_type)
    if drawing.teeth_count_text:
        drawing.teeth_count = first_int_value(drawing.teeth_count_text)
    if drawing.module_text:
        drawing.module = plain_float_value(drawing.module_text)
    if drawing.common_normal_length_text:
        drawing.common_normal_length = common_normal_value_from_text(drawing.common_normal_length_text, drawing.tooth_type)
    drawing.thickness = drawing.product_thickness or drawing.plate_thickness or drawing.thickness
    drawing.confirmed = 1
    apply_drawing_version(drawing, db, force_increment=was_confirmed)
    record_operation_log(db, "drawing_confirm", "drawing", drawing.id, None, "API确认图纸", before_data=before_data, after_data=drawing_snapshot(drawing))
    db.commit()
    db.refresh(drawing)
    return drawing
