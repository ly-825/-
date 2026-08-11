from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse

from app import admin_pages, mobile_connection_pages, paper_admin_pages
from app.auth import pages as auth_pages
from app.auth.dependencies import require_owner_account
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.routers import drawings, inventory, mobile, mobile_paper, mobile_plan, mobile_raw_plates
from app.schema_migrations import ensure_runtime_schema
from app.services.drawing_upload import backfill_missing_file_hashes

Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
Path(settings.drawing_preview_dir).mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(bind=engine)
ensure_runtime_schema(engine)
with SessionLocal() as db:
    backfill_missing_file_hashes(db)

def api_schema_urls(production: bool) -> dict[str, str | None]:
    if production:
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {
        "docs_url": "/docs",
        "redoc_url": "/redoc",
        "openapi_url": "/openapi.json",
    }


app = FastAPI(
    title="杭州特耐时 DXF智能用料系统",
    description="上传DXF图纸，自动识别产品用料尺寸，并匹配库存原料和余料。",
    version="0.1.0",
    **api_schema_urls(settings.production),
)

owner_dependencies = [Depends(require_owner_account)]

app.include_router(auth_pages.router, tags=["身份验证"])
app.include_router(
    drawings.router,
    prefix="/api/drawings",
    tags=["图纸识别"],
    dependencies=owner_dependencies,
)
app.include_router(
    inventory.router,
    prefix="/api/inventory",
    tags=["库存管理"],
    dependencies=owner_dependencies,
)
app.include_router(mobile.router, prefix="/api/mobile", tags=["小程序接口"])
app.include_router(mobile_plan.router, prefix="/api/mobile", tags=["小程序计划"])
app.include_router(mobile_raw_plates.router, prefix="/api/mobile", tags=["小程序钢板"])
app.include_router(mobile_paper.router, prefix="/api/mobile", tags=["小程序纸材"])
app.include_router(
    admin_pages.router,
    tags=["中文后台"],
    dependencies=owner_dependencies,
)
app.include_router(
    paper_admin_pages.router,
    tags=["纸材后台"],
    dependencies=owner_dependencies,
)
app.include_router(
    mobile_connection_pages.router,
    tags=["小程序连接"],
    dependencies=owner_dependencies,
)


@app.get("/", summary="中文首页", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse("/admin", status_code=303)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "app_name": "杭州特耐时库存系统",
        "app_version": app.version,
    }
