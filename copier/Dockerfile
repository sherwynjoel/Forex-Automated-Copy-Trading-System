FROM python:3.12-slim
WORKDIR /app
COPY copier/pyproject.toml ./
COPY copier/src ./src
RUN pip install --no-cache-dir .
# Unprivileged runtime user: the control port is 8080, nothing needs root.
RUN useradd --system --no-create-home --uid 10002 appuser \
    && chown -R appuser:appuser /app
USER appuser
CMD ["python", "-m", "copier.main"]
