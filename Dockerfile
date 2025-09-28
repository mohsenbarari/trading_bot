# Use an official Python runtime as a parent image
FROM python:3.11-slim-bullseye

# Install system dependencies needed for database drivers
RUN apt-get update && apt-get install -y libpq-dev build-essential && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Upgrade pip and related tools first for a robust installation
RUN pip install --upgrade pip setuptools wheel

# Copy the requirements file into the container
COPY requirements.txt .

# Install the python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code into the container
COPY . .