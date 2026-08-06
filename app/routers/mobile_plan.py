from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.material_matching import effective_drawing_thickness
from app.services.plan_material_service import list_plan_drawings, match_plan_materials


router = APIRouter(prefix="/plans", tags=["mobile-plans"])


def _drawing_data(drawing) -> dict[str, object]:
    return {
        "id": drawing.id,
        "product_code": drawing.product_code,
        "product_name": drawing.product_name,
        "product_category": drawing.product_category,
        "material": drawing.material,
        "thickness": effective_drawing_thickness(drawing),
        "outer_diameter": drawing.max_outer_diameter,
        "inner_diameter": drawing.min_inner_diameter,
        "teeth_count": drawing.teeth_count,
        "tooth_type": drawing.tooth_type,
        "version": drawing.version,
    }


@router.get("/drawings")
def mobile_plan_drawings(
    q: str = "",
    material: str = "",
    thickness: str = "",
    outer_diameter: str = "",
    inner_diameter: str = "",
    teeth_count: str = "",
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    drawings = list_plan_drawings(
        db,
        q=q,
        material=material,
        thickness=thickness,
        outer_diameter=outer_diameter,
        inner_diameter=inner_diameter,
        teeth_count=teeth_count,
    )
    return [_drawing_data(drawing) for drawing in drawings]


@router.get("/match")
def mobile_plan_match(
    drawing_id: int,
    quantity: int,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return match_plan_materials(db, drawing_id=drawing_id, quantity=quantity)
