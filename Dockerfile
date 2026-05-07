# Use official Python 3.11 slim image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PYTHONPATH=/app

# Install system dependencies (MINIMAL)
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev libglib2.0-0 \
    && pip install --upgrade pip \
    && rm -rf /var/lib/apt/lists/*

# Create and set working directory
WORKDIR /app

# Install Slim Python dependencies (No Torch/Docling)
COPY requirements.slim.txt .
RUN pip install --no-cache-dir -r requirements.slim.txt

# Copy project files
COPY ./app /app/

# Expose port for FastAPI
EXPOSE 8000
