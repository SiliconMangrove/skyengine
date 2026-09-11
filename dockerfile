# ============================================================================
# SkyEngine - 引擎打包镜像
# 支持 CPU / GPU (CUDA 12.9) 两种模式，通过 BUILD_BASE 镜像切换
# 打包脚本：docker build -t skyengine .
# ============================================================================

ARG BASE_IMAGE=python:3.11-slim-bookworm
FROM ${BASE_IMAGE} AS base

# ---------- 系统依赖 ----------
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

# ---------- 安装 uv（快速包管理器） ----------
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ---------- 工作目录 ----------
WORKDIR /app

# ---------- 环境变量 ----------
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

# ---------- 1) 先复制依赖声明文件，利用 Docker 缓存层 ----------
COPY pyproject.toml uv.lock ./

# ---------- 2) 安装 Python 依赖 ----------
# --frozen：严格按 uv.lock 安装，保证可复现
# --no-dev：不安装开发依赖
RUN uv sync --no-dev --no-install-project

# ---------- 3) 环境配置 ----------
COPY .env.example      .env

COPY run.py /app/run.py
COPY sim_server.py /app/sim_server.py
COPY sky_executor /app/sky_executor
COPY config /app/config
COPY sky_logs /app/sky_logs

# ---------- 4) 默认命令：交互式 Python ----------
CMD ["python"]
