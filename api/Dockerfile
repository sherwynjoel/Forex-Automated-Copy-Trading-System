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
# The Expert Advisor the operator downloads from /downloads/MirrorFleet.mq5
# (routes/mt5.py). Plain source with no secrets in it: the key is an input
# the operator types into the terminal.
COPY mt5/MirrorFleet.mq5 /app/static/downloads/MirrorFleet.mq5
ENV STATIC_DIR=/app/static
# Unprivileged runtime user: the api binds 8000 (not a privileged port) and
# writes nothing to disk, so root buys nothing and costs blast radius.
RUN useradd --system --no-create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
