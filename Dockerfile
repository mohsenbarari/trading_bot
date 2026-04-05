# --- STAGE 1: Build Frontend ---
# از یک ایمیج Node.js برای ساخت پروژه Vue استفاده می‌کنیم
FROM node:20-alpine AS builder
WORKDIR /app

COPY frontend/package*.json ./
RUN npm install
COPY frontend/ .
RUN npm run build

FROM python:3.11-slim-bullseye
RUN apt-get update && apt-get upgrade -y && apt-get install -y libpq-dev build-essential libmagic1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --upgrade pip setuptools wheel
# Copy pre-downloaded packages (downloaded on fast German server)
COPY pip_packages/ /tmp/pip_packages/
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/tmp/pip_packages/ -r requirements.txt \
    && rm -rf /tmp/pip_packages/
COPY api/ ./api/
COPY bot/ ./bot/
COPY core/ ./core/
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY models/ ./models/
COPY templates/ ./templates/
COPY fonts/ ./fonts/
COPY alembic.ini .
COPY main.py .
COPY manage.py .
COPY run_bot.py .
COPY schemas.py .
COPY seed_fake_data.py .
COPY scripts/ ./scripts/

COPY --from=builder /mini_app_dist /app/mini_app_dist