# trading_bot/main.py (نسخه نهایی)
import logging
from pathlib import Path
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from api.routers import invitations, auth
from core.config import settings
import schemas

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trading_bot.main")

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "mini_app" / "index.html"
TEMPLATES_DIR = BASE_DIR / "templates"

app = FastAPI(title="Trading Bot Backend", version="1.0")

# --- CORS (بخش اصلاح شده) ---
# آدرس از فایل env خوانده می‌شود
origins = [settings.frontend_url]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Include routers (api) ---
app.include_router(invitations.router, prefix="/api")
app.include_router(auth.router, prefix="/api")

@app.get("/api/config", response_model=schemas.AppConfig)
async def get_app_config():
    return {"bot_username": settings.bot_username}

# --- Serve the mini app ---
@app.get("/", include_in_schema=False)
@app.get("/webapp", include_in_schema=False)
@app.get("/webapp/{path:path}", include_in_schema=False)
async def serve_webapp(request: Request):
    if INDEX_FILE.exists():
        return FileResponse(str(INDEX_FILE))
    return JSONResponse({"detail": "index.html not found"}, status_code=404)

# --- Catch-all 404 ---
@app.get("/{full_path:path}", include_in_schema=False)
async def catch_all_404(full_path: str):
    not_found_page = TEMPLATES_DIR / "404.html"
    if not_found_page.is_file():
        return FileResponse(str(not_found_page), status_code=404)
    return JSONResponse({"detail": f"Not Found: {full_path}"}, status_code=404)

@app.on_event("startup")
async def on_startup():
    logger.info("--> FastAPI application configured and ready.")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)