from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MaterialInventory(Base):
    __tablename__ = "material_inventory"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    material_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    inventory_type: Mapped[str] = mapped_column(String(20), index=True)
    material: Mapped[str] = mapped_column(String(100), index=True)
    thickness: Mapped[float] = mapped_column(Float, index=True)
    shape: Mapped[str] = mapped_column(String(20), index=True)
    diameter: Mapped[float | None] = mapped_column(Float, nullable=True)
    length: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[float | None] = mapped_column(Float, nullable=True)
    usable_size: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="available", index=True)
    source_product_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    qr_code: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class InventoryTransactionRecord(Base):
    __tablename__ = "inventory_transaction_records"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    inventory_id: Mapped[int] = mapped_column(ForeignKey("material_inventory.id"), index=True)
    transaction_type: Mapped[str] = mapped_column(String(20), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    before_quantity: Mapped[int] = mapped_column(Integer)
    after_quantity: Mapped[int] = mapped_column(Integer)
    operator_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProductDrawing(Base):
    __tablename__ = "product_drawings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    product_code: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    product_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dxf_file_url: Mapped[str] = mapped_column(String(500))
    material: Mapped[str | None] = mapped_column(String(100), nullable=True)
    thickness: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_outer_diameter: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_inner_diameter: Mapped[float | None] = mapped_column(Float, nullable=True)
    bounding_length: Mapped[float | None] = mapped_column(Float, nullable=True)
    bounding_width: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_scrap_size: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_thickness: Mapped[float | None] = mapped_column(Float, nullable=True)
    plate_thickness: Mapped[float | None] = mapped_column(Float, nullable=True)
    teeth_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    module: Mapped[float | None] = mapped_column(Float, nullable=True)
    pressure_angle: Mapped[float | None] = mapped_column(Float, nullable=True)
    profile_shift_coefficient: Mapped[float | None] = mapped_column(Float, nullable=True)
    span_teeth_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    common_normal_length: Mapped[float | None] = mapped_column(Float, nullable=True)
    pin_diameter: Mapped[float | None] = mapped_column(Float, nullable=True)
    pin_span: Mapped[float | None] = mapped_column(Float, nullable=True)
    parse_result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parse_status: Mapped[str] = mapped_column(String(20), default="pending")
    confirmed: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ScrapGenerationRecord(Base):
    __tablename__ = "scrap_generation_records"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    source_product_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_inventory_id: Mapped[int | None] = mapped_column(ForeignKey("material_inventory.id"), nullable=True)
    scrap_inventory_id: Mapped[int | None] = mapped_column(ForeignKey("material_inventory.id"), nullable=True)
    theoretical_size: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actual_size: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operator_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
