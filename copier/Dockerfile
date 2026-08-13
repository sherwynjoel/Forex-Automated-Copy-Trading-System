FROM python:3.12-slim
WORKDIR /app
COPY copier/pyproject.toml ./
COPY copier/src ./src
RUN pip install --no-cache-dir .
CMD ["python", "-m", "copier.main"]
