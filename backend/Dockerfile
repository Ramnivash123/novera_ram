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

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# Copy built frontend from stage 1
COPY --from=frontend-build /frontend/dist ./frontend_dist

RUN mkdir -p data/uploads/branding data/processed logs

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
