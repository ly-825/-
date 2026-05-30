from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def ensure_runtime_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    with engine.begin() as connection:
        if "product_drawings" in tables:
            drawing_columns = {column["name"] for column in inspector.get_columns("product_drawings")}
            if "file_hash" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN file_hash VARCHAR(64)"))
            if "version" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN version INTEGER DEFAULT 1"))
            if "is_active" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN is_active INTEGER DEFAULT 1"))
            if "previous_drawing_id" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN previous_drawing_id INTEGER"))
            if "replaced_by_id" not in drawing_columns:
                connection.execute(text("ALTER TABLE product_drawings ADD COLUMN replaced_by_id INTEGER"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_product_drawings_file_hash ON product_drawings (file_hash)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_product_drawings_is_active ON product_drawings (is_active)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_product_drawings_previous_drawing_id ON product_drawings (previous_drawing_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_product_drawings_replaced_by_id ON product_drawings (replaced_by_id)"))
        if "material_inventory" in tables:
            inventory_columns = {column["name"] for column in inspector.get_columns("material_inventory")}
            if "source_drawing_id" not in inventory_columns:
                connection.execute(text("ALTER TABLE material_inventory ADD COLUMN source_drawing_id INTEGER"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_material_inventory_source_drawing_id ON material_inventory (source_drawing_id)"))
        if "inventory_transaction_records" in tables:
            transaction_columns = {column["name"] for column in inspector.get_columns("inventory_transaction_records")}
            if "reversed_transaction_id" not in transaction_columns:
                connection.execute(text("ALTER TABLE inventory_transaction_records ADD COLUMN reversed_transaction_id INTEGER"))
            if "idempotency_key" not in transaction_columns:
                connection.execute(text("ALTER TABLE inventory_transaction_records ADD COLUMN idempotency_key VARCHAR(100)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_inventory_transaction_records_reversed_transaction_id ON inventory_transaction_records (reversed_transaction_id)"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_inventory_transaction_records_idempotency_key ON inventory_transaction_records (idempotency_key)"))
        if "scrap_generation_records" in tables:
            scrap_generation_columns = {column["name"] for column in inspector.get_columns("scrap_generation_records")}
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
