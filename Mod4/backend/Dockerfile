# Imagen base oficial de Python 3.10
FROM python:3.10-slim

# Establece el directorio de trabajo dentro del contenedor
WORKDIR /app

# Copia los archivos del backend al contenedor
COPY . /app

# Instala dependencias necesarias del sistema (para playwright y otros)
RUN apt-get update && apt-get install -y \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Instala dependencias de Python
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Instala los navegadores para Playwright
RUN playwright install --with-deps

# Expone el puerto 8000
EXPOSE 8000

# Comando por defecto para correr la app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]