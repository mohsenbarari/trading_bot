# --- STAGE 1: Build Frontend ---
# از یک ایمیج Node.js برای ساخت پروژه Vue استفاده می‌کنیم
FROM node:20-alpine AS builder
WORKDIR /app

# ابتدا فقط فایل‌های package.json را کپی می‌کنیم تا از کش داکر استفاده بهینه شود
COPY frontend/package*.json ./
RUN npm install

# سپس تمام کدهای فرانت‌اند را کپی می‌کنیم
COPY frontend/ .

# پروژه Vue را بیلد می‌کنیم. خروجی در پوشه /app/dist قرار می‌گیرد
RUN npm run build

# --- STAGE 2: Final Application ---
# از ایمیج پایتون اصلی خود برای ساخت برنامه نهایی استفاده می‌کنیم
FROM python:3.11-slim-bullseye

RUN apt-get update && apt-get upgrade -y && apt-get install -y libpq-dev build-essential && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# نصب نیازمندی‌های پایتون
RUN pip install --upgrade pip setuptools wheel
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کپی کردن کدهای بک‌اند
# ما پوشه frontend را کپی نمی‌کنیم چون دیگر به سورس‌کد آن نیاز نداریم
COPY api/ ./api/
COPY bot/ ./bot/
COPY core/ ./core/
COPY migrations/ ./migrations/
COPY models/ ./models/
COPY templates/ ./templates/
COPY alembic.ini .
COPY main.py .
COPY manage.py .
COPY run_bot.py .
COPY schemas.py .


# --- جادوی اصلی ---
# فایل‌های بیلد شده از مرحله اول را به یک پوشه جدید در ایمیج نهایی کپی می‌کنیم
COPY --from=builder /app/dist /app/mini_app_dist