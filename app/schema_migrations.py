from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.time_utils import china_now


CHINA_TIME_MIGRATION = "2026_06_19_shift_utc_timestamps_to_china_time"


TIMESTAMP_COLUMNS = {
    "material_inventory": ("created_at", "updated_at"),
    "inventory_transaction_records": ("created_at",),
    "raw_plate_specifications": ("created_at", "updated_at"),
    "product_drawings": ("created_at", "updated_at"),
    "scrap_generation_records": ("registered_at",),
    "operation_logs": ("created_at",),
}


def migration_applied(connection, name: str) -> bool:
    row = connection.execute(
        text("SELECT name FROM runtime_schema_migrations WHERE name = :name"),
        {"name": name},
    ).first()
    return row is not None


def mark_migration_applied(connection, name: str) -> None:
    connection.execute(
        text("INSERT INTO runtime_schema_migrations (name, applied_at) VALUES (:name, :applied_at)"),
        {"name": name, "applied_at": china_now()},
    )


def shift_existing_utc_timestamps_to_china_time(connection, tables: list[str], table_columns: dict[str, set[str]]) -> None:
    for table_name, columns in TIMESTAMP_COLUMNS.items():
        if table_name not in tables:
            continue
        existing_columns = table_columns.get(table_name, set())
        for column_name in columns:
            if column_name not in existing_columns:
                continue
            connection.execute(
                text(f"UPDATE {table_name} SET {column_name} = datetime({column_name}, '+8 hours') WHERE {column_name} IS NOT NULL")
            )


def ensure_runtime_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    table_columns = {
        table_name: {column["name"] for column in inspector.get_columns(table_name)}
        for table_name in tables
    }
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS runtime_schema_migrations (
                name VARCHAR(100) PRIMARY KEY,
                applied_at DATETIME
            )
        """))
        if "product_drawings" in tables:
            drawing_columns = table_columns["product_drawings"]
            if "file_hash" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN file_hash VARCHAR(64)"))
            if "product_category" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN product_category VARCHAR(50)"))
            if "remark" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN remark VARCHAR(500)"))
            if "version" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN version INTEGER DEFAULT 1"))
            if "is_active" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN is_active INTEGER DEFAULT 1"))
            if "previous_drawing_id" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN previous_drawing_id INTEGER"))
            if "replaced_by_id" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN replaced_by_id INTEGER"))
            if "preview_file_url" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN preview_file_url VARCHAR(500)"))
            if "preview_status" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN preview_status VARCHAR(20) DEFAULT 'pending'"))
            if "preview_error" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN preview_error VARCHAR(500)"))
            if "teeth_count_text" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN teeth_count_text VARCHAR(50)"))
            if "tooth_type" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN tooth_type VARCHAR(10)"))
            if "module_text" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN module_text VARCHAR(50)"))
            if "common_normal_length_text" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN common_normal_length_text VARCHAR(100)"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_product_drawings_file_hash ON product_drawings (file_hash)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_product_drawings_product_category ON product_drawings (product_category)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_product_drawings_is_active ON product_drawings (is_active)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_product_drawings_previous_drawing_id ON product_drawings (previous_drawing_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_product_drawings_replaced_by_id ON product_drawings (replaced_by_id)"))
        if "material_inventory" in tables:
            inventory_columns = table_columns["material_inventory"]
            if "source_drawing_id" not in inventory_columns:
                connection.execute(text("ALTER TABLE material_inventory ADD COLUMN source_drawing_id INTEGER"))
            if "paper_material" not in inventory_columns:
                connection.execute(text("ALTER TABLE material_inventory ADD COLUMN paper_material VARCHAR(100)"))
            if "product_thickness" not in inventory_columns:
                connection.execute(text("ALTER TABLE material_inventory ADD COLUMN product_thickness FLOAT"))
            if "plate_thickness" not in inventory_columns:
                connection.execute(text("ALTER TABLE material_inventory ADD COLUMN plate_thickness FLOAT"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_material_inventory_source_drawing_id ON material_inventory (source_drawing_id)"))
        if "inventory_transaction_records" in tables:
            transaction_columns = table_columns["inventory_transaction_records"]
            if "reversed_transaction_id" not in transaction_columns:
                connection.execute(text("ALTER TABLE inventory_transaction_records ADD COLUMN reversed_transaction_id INTEGER"))
            if "idempotency_key" not in transaction_columns:
                connection.execute(text("ALTER TABLE inventory_transaction_records ADD COLUMN idempotency_key VARCHAR(100)"))
            if "customer_name" not in transaction_columns:
                connection.execute(text("ALTER TABLE inventory_transaction_records ADD COLUMN customer_name VARCHAR(100)"))
            if "outbound_purpose" not in transaction_columns:
                connection.execute(text("ALTER TABLE inventory_transaction_records ADD COLUMN outbound_purpose VARCHAR(50)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_inventory_transaction_records_reversed_transaction_id ON inventory_transaction_records (reversed_transaction_id)"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_inventory_transaction_records_idempotency_key ON inventory_transaction_records (idempotency_key)"))
        if "scrap_generation_records" in tables:
            scrap_generation_columns = table_columns["scrap_generation_records"]
            if "source_drawing_id" not in scrap_generation_columns:
                connection.execute(text("ALTER TABLE scrap_generation_records ADD COLUMN source_drawing_id INTEGER"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_scrap_generation_records_source_drawing_id ON scrap_generation_records (source_drawing_id)"))
        if "operation_logs" not in tables:
            connection.execute(text("""
                CREATE TABLE operation_logs (
                    id INTEGER PRIMARY KEY,
                    action VARCHAR(50) NOT NULL,
                    object_type VARCHAR(50) NOT NULL,
                    object_id INTEGER,
                    operator_name VARCHAR(100),
                    remark VARCHAR(255),
                    before_data JSON,
                    after_data JSON,
                    created_at DATETIME
                )
            """))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_operation_logs_action ON operation_logs (action)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_operation_logs_object_type ON operation_logs (object_type)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_operation_logs_object_id ON operation_logs (object_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_operation_logs_created_at ON operation_logs (created_at)"))
        if "raw_plate_specifications" not in tables:
            connection.execute(text("""
                CREATE TABLE raw_plate_specifications (
                    id INTEGER PRIMARY KEY,
                    spec_name VARCHAR(100) NOT NULL,
                    material VARCHAR(100) NOT NULL,
                    length FLOAT NOT NULL,
                    width FLOAT NOT NULL,
                    thickness FLOAT NOT NULL,
                    density FLOAT DEFAULT 7.85,
                    remark VARCHAR(255),
                    is_active INTEGER DEFAULT 1,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_raw_plate_specifications_spec_name ON raw_plate_specifications (spec_name)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_raw_plate_specifications_material ON raw_plate_specifications (material)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_raw_plate_specifications_thickness ON raw_plate_specifications (thickness)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_raw_plate_specifications_is_active ON raw_plate_specifications (is_active)"))
        if engine.dialect.name == "sqlite" and not migration_applied(connection, CHINA_TIME_MIGRATION):
            shift_existing_utc_timestamps_to_china_time(connection, tables, table_columns)
            mark_migration_applied(connection, CHINA_TIME_MIGRATION)
