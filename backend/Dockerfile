# =========================
# STAGE 1 — Build Frontend
# =========================
FROM node:20-alpine AS frontend-build

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ .
RUN npm run build

# =========================
# STAGE 2 — Backend
# =========================
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    gcc g++ postgresql-client libpq-dev \
    poppler-utils tesseract-ocr \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender-dev \
    libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# Remove any __pycache__ that got copied from host
RUN find /app -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
RUN find /app -name "*.pyc" -delete 2>/dev/null || true

# Copy frontend build
COPY --from=frontend-build /frontend/dist ./frontend/dist

RUN mkdir -p data/uploads/branding data/processed logs

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
