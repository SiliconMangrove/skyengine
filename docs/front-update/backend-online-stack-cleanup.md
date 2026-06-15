# 后端兜底清理:主栈退出时连带关闭 skyengine-online 栈

> 状态:**方案设计** (未实施)
> 影响:`application/backend/server.py`、`docker-compose.yml`
> 关键词:FastAPI lifespan、uvicorn 信号处理、孤儿容器、GPU 释放

---

## 1. 背景与问题

SkyEngine 运行时由**两个独立的 docker compose 项目**组成:

| 栈 | compose 文件 | 服务 | 谁启动 |
|---|---|---|---|
| **主栈** `skyengine` | `docker-compose.yml` | `backend` + `frontend` | 用户手动 `docker compose up -d` |
| **online 栈** `skyengine-online` | `docker-compose-online.yaml`(`.env: SKYENGINE_COMPOSE_PATH`) | `engine` / `mapf` / `fjsp` | backend 里的 `DockerProxy`(`application/backend/core/DockerProxy.py`)按需 `-p skyengine-online up/down` |

两个栈通过外部网络 `skyengine-net` 互通(主栈创建该网络,online 栈 `external: true` 引用)。

**问题**:`docker compose down` 关闭主栈、或 Ctrl+C 杀掉进程时,backend 容器收到 SIGTERM 直接退出,**没有任何机制去 `down` online 栈**。于是 `engine/mapf/fjsp` 成了孤儿容器继续运行,GPU / 显存被白占。

### 现状代码佐证

`DockerProxy.cleanup()`(即 `docker compose ... down --remove-orphans`)只在两条**用户主动触发**的路径里调用:

- `application/backend/server.py:372` —— `/switch` 切换工厂时清理上一个 proxy
- `application/backend/server.py:574` —— `/disconnect` 用户主动断开

server.py 当前的 `app = FastAPI()`(**无 lifespan**):

```python
# application/backend/server.py:26
app = FastAPI()
```

没有任何 FastAPI lifespan / signal handler / atexit。所以 SIGTERM 到来时 uvicorn 直接退出,cleanup 不会被触发。

### 实测证据

2026-06-15 观察:主栈 `skyengine-backend` / `skyengine-frontend` 均已停止,但

```
skyengine-online-mapf-1    Up 12 minutes
skyengine-online-fjsp-1    Up 12 minutes
skyengine-online-engine-1  Up 14 minutes
```

仍在运行,占着 GPU。这正是缺少兜底导致的泄漏。

---

## 2. 目标

无论主栈以何种方式退出,都自动执行一次:

```
docker compose -p skyengine-online \
  --project-directory "$SKYENGINE_PROJECT_DIR" \
  -f "$SKYENGINE_COMPOSE_PATH" \
  down --remove-orphans
```

覆盖三种退出路径:
1. `docker compose down` 主栈 → SIGTERM
2. Ctrl+C → SIGINT
3. backend 进程崩溃 / uvicorn 异常退出(只要不是 SIGKILL)

---

## 3. 方案:FastAPI lifespan 内做信号清理

### 3.1 关键前提:uvicorn 已是 PID 1

`application/dockerfile/backend.Dockerfile:38` 使用 **exec 形式** CMD:

```dockerfile
CMD [".venv/bin/uvicorn", "application.backend.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

exec 形式不会经过 shell,**uvicorn 进程本身就是容器 PID 1**,直接接收 docker / Ctrl+C 发来的 SIGTERM / SIGINT。因此:

- **不需要**额外的 shell 包裹脚本来转发信号(那正是 shell 形式 CMD / `sh -c` 才需要的)
- **不需要**手动安装 signal handler 转发信号

uvicorn 内置的信号处理会:
- 收到 SIGTERM / SIGINT → 触发 **graceful shutdown**
- graceful shutdown 会跑完 **FastAPI lifespan 的 shutdown 段**
- 然后 uvicorn 才真正退出

所以只要把 `docker compose -p skyengine-online ... down` 放进 lifespan 的 shutdown 段,信号到来时就一定会跑到。这是最自然的接入点,**零额外文件、零信号转发代码**。

### 3.2 改 `application/backend/server.py`

**新增** lifespan 上下文管理器,shutdown 段调一次 `docker compose ... down`:

```python
from contextlib import asynccontextmanager
import os

async def _cleanup_online_stack() -> None:
    """兜底:关闭 skyengine-online 栈 (engine/mapf/fjsp),释放 GPU。
    幂等 —— online 栈没起时 down 是 no-op,不会报错。
    """
    compose_file = os.getenv("SKYENGINE_COMPOSE_PATH")
    project_dir  = os.getenv("SKYENGINE_PROJECT_DIR")
    if not compose_file or not os.path.exists(compose_file):
        return  # 没配 / 文件不存在,跳过(不阻塞 backend 退出)

    cmd = ["docker", "compose", "-p", "skyengine-online"]
    if project_dir:
        cmd += ["--project-directory", project_dir]
    cmd += ["-f", compose_file, "down", "--remove-orphans", "--timeout", "8"]

    try:
        logger.info(f"[shutdown] 清理 skyengine-online 栈: {' '.join(cmd)}")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(f"[shutdown] online 栈清理非零退出 (rc={proc.returncode}): "
                           f"{stderr.decode().strip()}")
        else:
            logger.info("[shutdown] skyengine-online 栈已清理")
    except Exception as e:
        # 任何失败都不能阻塞 backend 自身退出
        logger.warning(f"[shutdown] online 栈清理失败(忽略): {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- startup ----
    # (现有 startup 逻辑如有,放这里;当前没有则留空)
    logger.info("[startup] backend lifespan started")
    try:
        yield
    finally:
        # ---- shutdown ---- SIGTERM / SIGINT / uvicorn 退出都会走这里
        # 1) 先关当前 proxy(走原有的 cleanup 流程,若有 active proxy)
        global current_factory_proxy
        if current_factory_proxy is not None:
            try:
                await current_factory_proxy.cleanup()
            except Exception as e:
                logger.warning(f"[shutdown] current_factory_proxy.cleanup 失败(忽略): {e}")
        # 2) 兜底:无论如何再 down 一次 online 栈
        #    (proxy.cleanup 可能因为某些路径已经调过 / 抛过异常而没跑到)
        await _cleanup_online_stack()


# 原来:app = FastAPI()
# 改为:
app = FastAPI(lifespan=lifespan)
```

**关键设计点**:

| 点 | 说明 |
|---|---|
| exec 形式 CMD | uvicorn = PID 1,信号直达,无需 shell / 转发 |
| 用 `asyncio.create_subprocess_exec` | 与 `DockerProxy._compose()`(`DockerProxy.py:91-119`)完全一致的调用方式,镜像内 docker compose 已验证可用 |
| `finally` 段兜底 | 即使 shutdown 中途抛异常,`_cleanup_online_stack()` 也一定跑 |
| 双重清理 | 先 `current_factory_proxy.cleanup()`(走原有逻辑),再 `_cleanup_online_stack()`(幂等兜底)。两者都是 `docker compose down`,后一个保证不会漏 |
| `--timeout 8` | online 栈内部 stop 超时,确保在主栈 `stop_grace_period` 内完成 |
| 全程 `try/except` + `logger.warning` | compose 命令失败不阻塞 backend 自身退出;幂等性保证 online 栈没起时是 no-op |
| 复用现有 env | `SKYENGINE_COMPOSE_PATH` / `SKYENGINE_PROJECT_DIR` 已在主栈 `docker-compose.yml` 注入 backend 容器,无需新配置 |

### 3.3 改 `docker-compose.yml`(backend 服务)

加 `stop_grace_period`,给 lifespan shutdown 留足时间(默认 10s 可能不够 —— cleanup 里 compose down online `--timeout 8` + 启动开销):

```yaml
  backend:
    ...
    stop_grace_period: 30s
    restart: unless-stopped
```

时间预算:`_cleanup_online_stack`(compose down online,`--timeout 8`)≈ 8–10s,加上 backend 自身停止开销,30s 裕量充足。

### 3.4 为什么不写 entrypoint 脚本(放弃了的原方案)

之前考虑过新增 `application/dockerfile/backend-entrypoint.sh` 包裹 uvicorn、用 `trap` 捕获信号。放弃,理由:

1. **当前 Dockerfile 是 exec 形式 CMD**,uvicorn 已是 PID 1,信号直达 —— shell trap 的"信号转发"价值不存在
2. shell trap 主要价值是覆盖**非 exec 形式**(uvicorn 作为 `sh -c` 的子进程)和**进程崩溃立即退出**(lifespan 来不及跑)这两种情况。前者我们已经避免了;后者用 `atexit` 也覆盖不了,只有 PID 1 trap 能覆盖,但 Python 进程崩溃时 `sh` trap 触发后仍要 `docker compose down`,做的事和 lifespan 一模一样,单点足够
3. **少一个文件、少一种语言**:lifespan 在同一个 `server.py` 里,逻辑集中、易于测试,改一处即可;shell 脚本要改 Dockerfile(COPY + chmod + 改 CMD),还要关心 `/bin/sh` 在镜像里是否可用
4. 如果未来还要做更优雅的关闭(例如先 POST `/sim/stop` 让 engine 落盘再 down),在 Python 里加一行就行,在 shell 里加要写 curl + 解析

**唯一 shell 脚本更优的场景**:uvicorn 卡死、不响应 SIGTERM(被 SIGKILL 前 lifespan 没机会跑)。这种场景概率极低,且真发生时 `stop_grace_period` 后 docker 直接 SIGKILL,shell trap 也一样来不及 —— 因为整个 backend 容器(包括 entrypoint shell)都被杀了。所以 shell 脚本在这个边界场景下也不比 lifespan 强。

结论:**lifespan 单点足够**,不引入额外脚本。

---

## 4. 实施步骤

```bash
# 1. 改 application/backend/server.py(加 lifespan + _cleanup_online_stack,见 3.2)
# 2. 改 docker-compose.yml(backend 服务加 stop_grace_period: 30s,见 3.3)

# 3. 源码改动走 bind mount(./application 挂进容器),重启 backend 容器即可生效,
#    不需要 rebuild 镜像(没动 Dockerfile、没加 Python 依赖):
docker compose restart backend

# 4.(可选)清理当前已泄漏的 online 栈(在宿主机执行,因为此刻可能没有 backend 容器)
docker compose -p skyengine-online \
  --project-directory "$(grep SKYENGINE_PROJECT_DIR .env | cut -d= -f2)" \
  -f "$(grep SKYENGINE_COMPOSE_PATH .env | cut -d= -f2)" \
  down --remove-orphans
```

---

## 5. 验证

| 步骤 | 操作 | 预期 |
|---|---|---|
| 1 | `docker compose up -d` + 前端切到 `grid_factory_new`,启动仿真 | `docker ps \| grep skyengine-online` 能看到 engine/mapf/fjsp |
| 2 | `docker compose down`(主栈,发 SIGTERM) | 主栈停 |
| 3 | `docker ps \| grep skyengine-online` | **无输出**(online 栈已被清理) |
| 4 | `docker logs skyengine-backend`(在 down 之前另开终端 tail,或日志已落盘) | 可见 `[shutdown] 清理 skyengine-online 栈...` 与 `skyengine-online 栈已清理` |
| 5 | Ctrl+C 场景:`docker compose up`(前台)+ Ctrl+C | 同 2/3/4,SIGINT 路径覆盖 |
| 6 | 反向:不启动仿真直接 `docker compose down` | cleanup 跑 no-op,日志可能 warning 但不报错,正常退出 |

---

## 6. 边界与风险

- **SIGKILL 无法捕获**:`docker kill` / `kill -9` / OOM Killer 直接 SIGKILL backend 进程,lifespan 来不及跑。**没有任何应用层方案能解决**(shell trap 也一样,因为整个容器被杀)。这类情况只能靠宿主机上 cron / supervisor 定期巡检孤儿 online 容器,或直接重启宿主机 docker daemon —— 超出本方案范围。
- **docker.sock 可用性**:backend 容器挂了 `/var/run/docker.sock`,cleanup 调宿主机 daemon 停 online 容器。若 sock 挂载缺失则 cleanup 失败,但 `try/except` 兜底,只 warning 不阻塞 backend 自身退出。
- **stop_grace_period 不足**:若 online 栈容器拒绝优雅停止(卡死),`--timeout 8` 后 SIGKILL;若总时间超过 30s,backend 自身被 SIGKILL,cleanup 中断。极端情况,概率低。
- **多个并发仿真**:当前架构同时只有一个 active proxy(`server.py` 的 `current_factory_proxy` 单例),online 栈同一时刻只有一份,cleanup 一次 down 全部,无遗漏。
- **online 栈被外部手动改过**:若有人在 backend 之外手动 `up` 了 online 服务,backend 退出时仍会 `down` 掉它们。符合"backend 拥有 online 栈生命周期"的语义,不算副作用。
- **lifespan shutdown 超时**:uvicorn 默认 `timeout-graceful-shutdown=10s`(可用 `--timeout-graceful-shutdown` 调),若 cleanup 超过该值 uvicorn 强制退出。30s 的 `stop_grace_period` 是 docker 层的额外裕量,docker 先等 uvicorn 自己优雅退出,超时再 SIGKILL。两层超时配合,正常情况下 cleanup 一定跑得完。
