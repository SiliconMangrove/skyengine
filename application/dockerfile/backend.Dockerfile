FROM python:3.11-slim

WORKDIR /app

# Use Chinese mirror for apt
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy local binaries: uv + docker CLI + compose plugin
# COPY application/dockerfile/docker-bin/uv /usr/local/bin/uv
# COPY application/dockerfile/docker-bin/docker /usr/local/bin/docker
# COPY application/dockerfile/docker-bin/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose

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
