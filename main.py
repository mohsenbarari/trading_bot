# main.py (نسخه نهایی و فقط برای وب)
import os
import logging
import uvicorn
from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routers import invitations, auth

# --- پیکربندی اولیه ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- تعریف اپلیکیشن FastAPI ---
app = FastAPI(title="Trading Bot API")

# --- پیکربندی‌های FastAPI ---
origins = ["http://localhost:3000", "http://localhost:8080", "https://telegram.362514.ir"]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

api_router = APIRouter(prefix="/api")
api_router.include_router(invitations.router)
api_router.include_router(auth.router)
app.include_router(api_router)

static_files_path = os.path.join(os.path.dirname(__file__), "mini_app")
if os.path.exists(static_files_path):
    app.mount("/", StaticFiles(directory=static_files_path, html=True), name="static")

logger.info("--> FastAPI application configured and ready.")

# --- نقطه شروع برای Uvicorn ---
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)