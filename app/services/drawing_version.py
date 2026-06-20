from sqlalchemy.orm import Session

from app.models import ProductDrawing


def apply_drawing_version(drawing: ProductDrawing, db: Session, force_increment: bool = False) -> ProductDrawing:
    if not drawing.product_code:
        drawing.version = (drawing.version or 1) + 1 if force_increment else drawing.version or 1
        drawing.is_active = 1
        return drawing

    latest = (
        db.query(ProductDrawing)
        .filter(
            ProductDrawing.id != drawing.id,
            ProductDrawing.product_code == drawing.product_code,
            ProductDrawing.confirmed == 1,
        )
        .order_by(ProductDrawing.version.desc(), ProductDrawing.updated_at.desc())
        .first()
    )
    if latest:
        drawing.version = max(latest.version or 1, drawing.version or 1) + (1 if force_increment else 0)
        if not force_increment:
            drawing.version = (latest.version or 1) + 1
        drawing.previous_drawing_id = latest.id
        drawing.is_active = 1
        latest.is_active = 0
        latest.replaced_by_id = drawing.id
    else:
        drawing.version = (drawing.version or 1) + 1 if force_increment else drawing.version or 1
        drawing.is_active = 1
        drawing.previous_drawing_id = None
    return drawing
