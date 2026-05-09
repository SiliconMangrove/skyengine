FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set PyPI mirror (tsinghua)
ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock ./

# Sync dependencies
RUN uv sync --frozen --no-dev

ENV PYTHONUNBUFFERED=1

# Copy application code and configs
COPY application /app/application
COPY executor /app/executor
COPY config /app/config
COPY dataset /app/dataset

EXPOSE 8000

CMD [".venv/bin/uvicorn", "application.backend.server:app", "--host", "0.0.0.0", "--port", "8000"]
