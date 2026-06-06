# DockerProxy 路径修复方案

## 问题背景

`DockerProxy` 在 backend 容器内通过 Docker Socket 执行 `docker compose` 命令，按需启停 engine 容器。
当前存在两个路径问题：

1. compose 文件路径错误（已修复）
2. engine 容器内 volume 挂载失败（当前问题）

## 问题根因

### Docker Socket 下的路径解析机制

```
┌─────────────────────────────────────────────────────┐
│ 宿主机 (Host)                                        │
│                                                      │
│  /data1/home/wuhao/project/finalpro/SkyEngine/      │
│    ├── docker-compose-online.yaml                    │
│    ├── sim_server.py                                 │
│    ├── sky_executor/                                 │
│    └── ...                                           │
│                                                      │
│  ┌──────────────────────┐   ┌──────────────────────┐ │
│  │ backend 容器          │   │ Docker Daemon        │ │
│  │                      │   │                      │ │
│  │ docker compose CLI   │──▶│ 接收指令, 创建容器    │ │
│  │ 通过 /var/run/       │   │                      │ │
│  │ docker.sock          │   │ volume 源路径在       │ │
│  │                      │   │ 宿主机文件系统上解析   │ │
│  └──────────────────────┘   └──────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

关键点：`docker compose` CLI 在**容器内**运行，但 Docker Daemon 在**宿主机**上运行。
compose 文件中的相对路径 volume（如 `./sim_server.py`）最终由 Daemon 在**宿主机文件系统**上解析。

### 错误链路

```yaml
# docker-compose-online.yaml 中的 volume 定义
volumes:
  - ./sim_server.py:/app/sim_server.py:ro   # ./ → 相对于 compose 文件目录
```

1. CLI（容器内）读取 compose 文件，解析出 `./sim_server.py`
2. CLI 将 `./sim_server.py` 解析为绝对路径（相对于 compose 文件所在目录）
3. 如果 compose 文件路径为 `/opt/skyengine/docker-compose-online.yaml`
   → `./sim_server.py` 解析为 `/opt/skyengine/sim_server.py`
4. Daemon 在宿主机上查找 `/opt/skyengine/sim_server.py` → **不存在**
5. Daemon 自动创建一个**空目录** `/opt/skyengine/sim_server.py`
6. engine 容器启动，`python -u sim_server.py` 发现是目录而非文件
   → 报错 `can't find '__main__' module in '/app/sim_server.py'`

## 修复方案

### 核心思路

用 `--project-directory` 将 compose 的相对路径基目录指向宿主机上 SkyEngine 的实际路径。
这样 `./sim_server.py` 就会解析为 `/data1/.../SkyEngine/sim_server.py`（宿主机上确实存在）。

### 改动 1: `docker-compose.yml` — 挂载整个 SkyEngine 目录

```yaml
volumes:
  # ... 其他 volume 不变 ...
  # SkyEngine 仓库 — 挂载整个目录供 DockerProxy 使用
  - ${SKYENGINE_DIR:-/data1/home/wuhao/project/finalpro/SkyEngine}:/opt/skyengine:ro

environment:
  - PYTHONUNBUFFERED=1
  # compose 文件的容器内路径（供 CLI 读取）
  - SKYENGINE_COMPOSE_PATH=/opt/skyengine/docker-compose-online.yaml
  # compose 文件的宿主机项目目录（供 --project-directory 解析相对路径）
  - SKYENGINE_PROJECT_DIR=${SKYENGINE_DIR:-/data1/home/wuhao/project/finalpro/SkyEngine}
  # engine 服务的可达地址（容器内通过宿主机网关访问）
  - ENGINE_URL=http://172.30.0.1:8080
  - CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-0}
```

说明：
- **volume 挂载**：整个 SkyEngine 目录挂到容器内 `/opt/skyengine`，供 CLI 读取 compose 文件
- **SKYENGINE_COMPOSE_PATH**：容器内路径，CLI 用它找到 compose 文件
- **SKYENGINE_PROJECT_DIR**：宿主机路径，通过 `--project-directory` 告诉 compose 在哪里解析 `./` 相对路径
- **ENGINE_URL**：backend 在 bridge 网络，`localhost` 指向自己，需用网关 IP `172.30.0.1` 访问宿主机上映射的 engine 端口

### 改动 2: `DockerProxy.py` — 读取环境变量 + 传递 `--project-directory`

```python
# __init__ 中新增
self._project_dir: str = os.getenv("SKYENGINE_PROJECT_DIR", "")

# _compose 方法中增加 --project-directory
async def _compose(self, *args: str, env: dict | None = None) -> str:
    cmd = ["docker", "compose", "-p", self._project_name]
    if self._project_dir:
        cmd += ["--project-directory", self._project_dir]
    cmd += ["-f", self._compose_file, *args]
    # ... 后续不变
```

### 修复后的路径解析链路

```
CLI (容器内):
  -f /opt/skyengine/docker-compose-online.yaml    ← 从容器内读取 compose 文件
  --project-directory /data1/.../SkyEngine        ← 告诉 compose 相对路径的基目录

compose 解析 ./sim_server.py:
  → /data1/home/wuhao/project/finalpro/SkyEngine/sim_server.py  ← 宿主机路径

Daemon (宿主机):
  查找 /data1/home/wuhao/project/finalpro/SkyEngine/sim_server.py  ← 存在 ✓
  挂载到 engine 容器 /app/sim_server.py  ← 正常 ✓
```

## 修改清单

| 文件 | 改动 |
|------|------|
| `docker-compose.yml` | volume 改为挂载整个 SkyEngine 目录；增加 `SKYENGINE_PROJECT_DIR`、`ENGINE_URL` 环境变量 |
| `application/backend/core/DockerProxy.py` | `__init__` 增加 `_project_dir`；`_compose` 增加 `--project-directory` 参数 |
