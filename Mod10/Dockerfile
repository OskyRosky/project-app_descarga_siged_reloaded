# ---------- Etapa 1: build del frontend ----------
FROM node:20-alpine AS fe-build
WORKDIR /frontend

# 1) Dependencias de frontend
COPY frontend/package*.json ./
RUN npm ci

# 2) Código y build (mismo origen: VITE_API_BASE vacío)
COPY frontend/ ./
ENV VITE_API_BASE=
RUN npm run build

# ---------- Etapa 2: backend con Playwright ----------
FROM mcr.microsoft.com/playwright/python:v1.46.0-jammy

# Evitar prompts y logs ruidosos
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # ruta por defecto de navegadores en la imagen base
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# 1) Requisitos backend
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
 && pip install --no-cache-dir "playwright==1.46.0" \
 # Smoke test: confirmamos versión del paquete (no lanza browsers)
 && python - <<'PY'
import sys, importlib
m = importlib.import_module("playwright")
print("Playwright OK:", getattr(m, "__version__", "unknown"), "| Python:", sys.version)
PY

# 2) Código backend
COPY backend/app /app/app

# 3) Estáticos del frontend (ya construidos)
COPY --from=fe-build /frontend/dist /app/app/public

# 4) Config de descargas al host (vía volumen)
ENV SIGED_HEADLESS=1 \
    SIGED_HOST_DOWNLOADS=/host_downloads

EXPOSE 8000

# 5) Arranque
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]