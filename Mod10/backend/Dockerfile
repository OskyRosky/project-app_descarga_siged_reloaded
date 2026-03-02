# Base oficial de Playwright (multi-arch; incluye navegadores y deps)
FROM mcr.microsoft.com/playwright/python:v1.46.0-jammy

WORKDIR /app

# 1) Requisitos de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2) Asegurar que el paquete 'playwright' esté instalado en este entorno de Python
#    (la base lo trae, pero lo re-instalamos para evitar sorpresas)
RUN pip install --no-cache-dir "playwright==1.46.0" \
 && python - <<'PY'
import sys
import importlib
m = importlib.import_module("playwright")
print("Playwright OK:", getattr(m, "__version__", "unknown"), "| Python:", sys.version)
PY

# 3) Código del backend
COPY app ./app

# 4) Config
ENV PYTHONUNBUFFERED=1
ENV SIGED_HEADLESS=1
EXPOSE 8000

# 5) Arranque del API
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]