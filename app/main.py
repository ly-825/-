from pathlib import Path

from fastapi import FastAPI

from app import admin_pages
from app.config import settings
from app.database import Base, engine
from app.home import render_home
from app.routers import drawings, inventory

Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="杭州特耐时 DXF智能用料系统",
    description="上传DXF图纸，自动识别产品用料尺寸，并匹配库存原料和余料。",
    version="0.1.0",
)

app.include_router(drawings.router, prefix="/api/drawings", tags=["图纸识别"])
app.include_router(inventory.router, prefix="/api/inventory", tags=["库存管理"])
app.include_router(admin_pages.router, tags=["中文后台"])


@app.get("/", summary="中文首页", include_in_schema=False)
def home():
    return render_home()


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
