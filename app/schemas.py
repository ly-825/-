from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class InventoryBase(BaseModel):
    material_code: str | None = None
    inventory_type: str
    material: str
    thickness: float
    product_thickness: float | None = None
    plate_thickness: float | None = None
    shape: str
    diameter: float | None = None
    length: float | None = None
    width: float | None = None
    usable_size: str | None = None
    quantity: int = 1
    location: str | None = None
    paper_material: str | None = None
    status: str = "available"
    source_product_code: str | None = None
    source_drawing_id: int | None = None


class InventoryCreate(InventoryBase):
    pass


class InventoryOut(InventoryBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)



class InventoryAdjust(BaseModel):
    transaction_type: str
    quantity: int
    operator_name: str | None = None
    remark: str | None = None


class TransactionReverse(BaseModel):
    operator_name: str | None = None
    remark: str | None = None


class InventoryTransactionOut(BaseModel):
    id: int
    inventory_id: int
    transaction_type: str
    quantity: int
    before_quantity: int
    after_quantity: int
    reversed_transaction_id: int | None = None
    operator_name: str | None
    customer_name: str | None = None
    outbound_purpose: str | None = None
    remark: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DrawingConfirm(BaseModel):
    product_code: str | None = None
    product_name: str | None = None
    product_category: str | None = None
    remark: str | None = None
    material: str | None = None
    thickness: float | None = None
    max_outer_diameter: float | None = None
    min_inner_diameter: float | None = None
    bounding_length: float | None = None
    bounding_width: float | None = None
    expected_scrap_size: str | None = None
    product_thickness: float | None = None
    plate_thickness: float | None = None
    teeth_count: int | None = None
    teeth_count_text: str | None = None
    tooth_type: str | None = None
    module: float | None = None
    module_text: str | None = None
    pressure_angle: float | None = None
    profile_shift_coefficient: float | None = None
    span_teeth_count: int | None = None
    common_normal_length: float | None = None
    common_normal_length_text: str | None = None
    pin_diameter: float | None = None
    pin_span: float | None = None

    @field_validator("*", mode="before")
    @classmethod
    def empty_string_to_none(cls, value: Any) -> Any:
        return None if value == "" else value


class DrawingOut(DrawingConfirm):
    id: int
    dxf_file_url: str
    file_hash: str | None = None
    parse_result_json: dict[str, Any] | None = None
    parse_status: str
    confirmed: int
    version: int
    is_active: int
    previous_drawing_id: int | None = None
    replaced_by_id: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DrawingUploadOut(BaseModel):
    drawing: DrawingOut
    duplicated: bool
