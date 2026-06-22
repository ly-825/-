from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "DXF Material Matching MVP"
    database_url: str = "sqlite:///./data/app.db"
    upload_dir: str = "./data/uploads"
    drawing_preview_dir: str = "./data/previews"
    drawing_preview_converter_path: str | None = None
    drawing_preview_converter_args: str = "-auto-fit -paper=A4 -force -monochrome"
    drawing_preview_timeout_seconds: int = 90
    max_upload_size_mb: int = 50
    qrcode_dir: str = "./data/qrcodes"
    dashscope_api_key: str | None = None
    qwen_model: str = "qwen-plus"
    qwen_fallback_model: str = "qwen-max"
    thickness_tolerance: float = 0.05
    machining_margin: float = 2.0
    raw_plate_low_stock_threshold: int = 2
    admin_access_token: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
