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
    auth_pepper: str | None = None
    owner_totp_secret: str | None = None
    wechat_app_id: str | None = None
    wechat_app_secret: str | None = None
    pc_session_hours: int = 12
    pc_login_request_seconds: int = 120
    mobile_session_days: int = 30
    legacy_password_login_enabled: bool = True
    auth_cookie_name: str = "tns_session"
    production: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
