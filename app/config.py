from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "DXF Material Matching MVP"
    database_url: str = "sqlite:///./data/app.db"
    upload_dir: str = "./data/uploads"
    qrcode_dir: str = "./data/qrcodes"
    dashscope_api_key: str | None = None
    qwen_model: str = "qwen-plus"
    qwen_fallback_model: str = "qwen-max"
    thickness_tolerance: float = 0.05
    machining_margin: float = 2.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
