
FROM node:20-alpine AS builder
WORKDIR /app


COPY frontend/package*.json ./
RUN npm install

COPY frontend/ .

RUN npm run build

FROM python:3.11-slim-bullseye

RUN apt-get update && apt-get upgrade -y && apt-get install -y libpq-dev build-essential && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --upgrade pip setuptools wheel
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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


COPY --from=builder /mini_app_dist /app/mini_app_dist