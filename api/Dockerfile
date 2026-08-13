FROM node:22-slim AS dashboard
WORKDIR /dash
COPY dashboard/package*.json ./
RUN npm ci
COPY dashboard ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY api/pyproject.toml ./
COPY api/src ./src
RUN pip install --no-cache-dir .
COPY --from=dashboard /dash/dist /app/static
ENV STATIC_DIR=/app/static
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
