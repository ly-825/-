from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.time_utils import china_now


class MaterialInventory(Base):
    __tablename__ = "material_inventory"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    material_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    raw_plate_model: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    inventory_type: Mapped[str] = mapped_column(String(20), index=True)
    material: Mapped[str] = mapped_column(String(100), index=True)
    thickness: Mapped[float] = mapped_column(Float, index=True)
    product_thickness: Mapped[float | None] = mapped_column(Float, nullable=True)
    plate_thickness: Mapped[float | None] = mapped_column(Float, nullable=True)
    shape: Mapped[str] = mapped_column(String(20), index=True)
    diameter: Mapped[float | None] = mapped_column(Float, nullable=True)
    length: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[float | None] = mapped_column(Float, nullable=True)
    usable_size: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    paper_material: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="available", index=True)
    source_product_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_drawing_id: Mapped[int | None] = mapped_column(ForeignKey("product_drawings.id"), nullable=True, index=True)
    qr_code: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now)


class InventoryTransactionRecord(Base):
    __tablename__ = "inventory_transaction_records"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    inventory_id: Mapped[int] = mapped_column(ForeignKey("material_inventory.id"), index=True)
    transaction_type: Mapped[str] = mapped_column(String(20), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    before_quantity: Mapped[int] = mapped_column(Integer)
    after_quantity: Mapped[int] = mapped_column(Integer)
    reversed_transaction_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    operator_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    outbound_purpose: Mapped[str | None] = mapped_column(String(50), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now)


class RawPlateSpecification(Base):
    __tablename__ = "raw_plate_specifications"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    spec_name: Mapped[str] = mapped_column(String(100), index=True)
    material: Mapped[str] = mapped_column(String(100), index=True)
    length: Mapped[float] = mapped_column(Float, index=True)
    width: Mapped[float] = mapped_column(Float, index=True)
    thickness: Mapped[float] = mapped_column(Float, index=True)
    density: Mapped[float] = mapped_column(Float, default=7.85)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, default=1, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now)


class PaperSpecification(Base):
    __tablename__ = "paper_specifications"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    paper_type: Mapped[str] = mapped_column(String(20), index=True)
    model: Mapped[str] = mapped_column(String(100), index=True)
    material_name: Mapped[str] = mapped_column(String(100), index=True)
    thickness: Mapped[float] = mapped_column(Float, index=True)
    inner_diameter: Mapped[float | None] = mapped_column(Float, nullable=True)
    outer_diameter: Mapped[float | None] = mapped_column(Float, nullable=True)
    length: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[float | None] = mapped_column(Float, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, default=1, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now)


class PaperInventoryBatch(Base):
    __tablename__ = "paper_inventory_batches"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    specification_id: Mapped[int] = mapped_column(ForeignKey("paper_specifications.id"), index=True)
    batch_code: Mapped[str] = mapped_column(String(100), index=True)
    paper_type: Mapped[str] = mapped_column(String(20), index=True)
    model: Mapped[str] = mapped_column(String(100), index=True)
    material_name: Mapped[str] = mapped_column(String(100), index=True)
    thickness: Mapped[float] = mapped_column(Float, index=True)
    inner_diameter: Mapped[float | None] = mapped_column(Float, nullable=True)
    outer_diameter: Mapped[float | None] = mapped_column(Float, nullable=True)
    length: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    location: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="available", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now)


class PaperInventoryTransaction(Base):
    __tablename__ = "paper_inventory_transactions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    inventory_id: Mapped[int] = mapped_column(ForeignKey("paper_inventory_batches.id"), index=True)
    transaction_type: Mapped[str] = mapped_column(String(20), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    before_quantity: Mapped[int] = mapped_column(Integer)
    after_quantity: Mapped[int] = mapped_column(Integer)
    reversed_transaction_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    operator_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now)


class ProductDrawing(Base):
    __tablename__ = "product_drawings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    product_code: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    product_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    product_category: Mapped[str | None] = mapped_column(String(50), index=True, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dxf_file_url: Mapped[str] = mapped_column(String(500))
    preview_file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    preview_status: Mapped[str] = mapped_column(String(20), default="pending")
    preview_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
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
    teeth_count_text: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tooth_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    module: Mapped[float | None] = mapped_column(Float, nullable=True)
    module_text: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pressure_angle: Mapped[float | None] = mapped_column(Float, nullable=True)
    profile_shift_coefficient: Mapped[float | None] = mapped_column(Float, nullable=True)
    span_teeth_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    common_normal_length: Mapped[float | None] = mapped_column(Float, nullable=True)
    common_normal_length_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pin_diameter: Mapped[float | None] = mapped_column(Float, nullable=True)
    pin_span: Mapped[float | None] = mapped_column(Float, nullable=True)
    parse_result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parse_status: Mapped[str] = mapped_column(String(20), default="pending")
    confirmed: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[int] = mapped_column(Integer, default=1, index=True)
    previous_drawing_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    replaced_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now)


class ScrapGenerationRecord(Base):
    __tablename__ = "scrap_generation_records"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    source_product_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_drawing_id: Mapped[int | None] = mapped_column(ForeignKey("product_drawings.id"), nullable=True, index=True)
    source_inventory_id: Mapped[int | None] = mapped_column(ForeignKey("material_inventory.id"), nullable=True)
    scrap_inventory_id: Mapped[int | None] = mapped_column(ForeignKey("material_inventory.id"), nullable=True)
    theoretical_size: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actual_size: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operator_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=china_now)


class MobileRequestRecord(Base):
    __tablename__ = "mobile_request_records"
    __table_args__ = (
        UniqueConstraint(
            "operation_type",
            "client_request_id",
            name="uq_mobile_request_operation_client",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    operation_type: Mapped[str] = mapped_column(String(80), index=True)
    client_request_id: Mapped[str] = mapped_column(String(100), index=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now, index=True)


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    action: Mapped[str] = mapped_column(String(50), index=True)
    object_type: Mapped[str] = mapped_column(String(50), index=True)
    object_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    operator_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    before_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now, index=True)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    wechat_openid: Mapped[str | None] = mapped_column(
        String(100), unique=True, nullable=True, index=True
    )
    activation_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    activation_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=china_now, onupdate=china_now
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    session_version: Mapped[int] = mapped_column(Integer)
    client_type: Mapped[str] = mapped_column(String(20), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=china_now)


class PcLoginRequest(Base):
    __tablename__ = "pc_login_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True
    )
    browser_secret_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    device_summary: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id"), nullable=True, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now)
