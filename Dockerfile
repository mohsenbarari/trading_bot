# Dockerfile (نسخه نهایی و ساده‌شده)
FROM python:3.11-slim-bullseye

RUN apt-get update && apt-get upgrade -y && apt-get install -y libpq-dev build-essential && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --upgrade pip setuptools wheel

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .