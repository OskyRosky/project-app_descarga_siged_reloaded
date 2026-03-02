FROM mcr.microsoft.com/playwright/python:v1.46.0-jammy
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY backend/app /app/app
EXPOSE 8000
ENV SIGED_HEADLESS=1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
