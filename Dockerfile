# 1) собираем фронтенд
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 2) приложение FastAPI 
FROM python:3.12-slim AS app
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=web /web/dist ./static
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# 3) то же + тесты
FROM app AS test
COPY backend/requirements-dev.txt backend/pytest.ini ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY backend/tests ./tests
CMD ["python", "-m", "pytest", "-q"]
