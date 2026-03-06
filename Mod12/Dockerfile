# ============================================================
# STAGE 1 — Build Frontend (Vite)
# ============================================================
FROM node:20-alpine AS frontend_builder
WORKDIR /frontend

# Instala deps con lockfile
COPY frontend/package*.json ./
RUN npm ci

# Copia el código y construye dist/
COPY frontend/ ./
# Monolítico: NO seteamos VITE_API_BASE => el frontend usará paths relativos (/progreso, /descargar, etc.)
RUN npm run build


# ============================================================
# STAGE 2 — Runtime Backend (FastAPI + Playwright + browsers)
# ============================================================
# Esta imagen YA trae browsers + deps del sistema para Playwright
FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy AS runtime

WORKDIR /app

# (Opcional pero recomendado) no correr como root en ECS
# La imagen ya tiene usuario pwuser, lo usamos para seguridad.
USER root

# Copiamos requirements primero para mejor cache
COPY backend/requirements.txt /app/requirements.txt

# Instala deps Python
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copia backend completo
COPY backend/ /app/

# Copia build del frontend (dist) a backend/app/public
# Esto debe calzar con main.py que monta StaticFiles(directory="app/public")
RUN rm -rf /app/app/public && mkdir -p /app/app/public
COPY --from=frontend_builder /frontend/dist /app/app/public

# Variables runtime
ENV SIGED_HEADLESS=1
ENV PYTHONUNBUFFERED=1

# Puerto interno del container
EXPOSE 8210

# Cambia a usuario no-root (más seguro)
USER pwuser

# Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8210"]