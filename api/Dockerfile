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
# Uploaded receipts and proofs (routes/portal_files.py). The directory is
# created in the image, owned by appuser, so the named volume compose
# mounts here inherits that ownership when it is first created.
ENV UPLOAD_DIR=/data/uploads
# Unprivileged runtime user: the api binds 8000 (not a privileged port) and
# writes only under /data/uploads, so root buys nothing and costs blast
# radius.
RUN useradd --system --no-create-home --uid 10001 appuser \
    && mkdir -p /data/uploads \
    && chown -R appuser:appuser /app /data/uploads
USER appuser
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
