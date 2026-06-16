"""
DockerProxy — 通过 docker compose 按需启停 SkyEngine 在线仿真服务

调用 docker compose CLI 管理容器生命周期，无需 Docker SDK。
所有容器/网络/卷的定义都在 docker-compose-online.yaml 中。

两阶段启动:
  1. initialize() — docker compose up engine     → 等待就绪
  2. start()      — docker compose up mapf fjsp  → POST /sim/create → POST /sim/play
  3. stop()       — docker compose down
"""

import os
import asyncio
import json
import logging
from enum import Enum

import httpx

logger = logging.getLogger(__name__)


class ExecutionStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


# ==================== 算法 → 镜像名映射 ====================

ALGORITHM_MAP = {
    "fjsp": {
        "pso":  "skyengine-fjsp-pso:latest",
        "de":   "skyengine-fjsp-de:latest",
        "drl":  "skyengine-fjsp-drl:latest",
        "best": "skyengine-fjsp-best:latest",
    },
    "mapf": {
        "astar":    "skyengine-mapf-astar:latest",
        "mapf_gpt": "skyengine-mapf-gpt:latest",
    },
}


class DockerProxy:
    """通过 docker compose 联动 SkyEngine 在线服务的工厂代理"""

    def __init__(self):
        logger.info("[DockerProxy] __init__ 被调用")
        # ---- 普通实例属性 (与 GridFactoryProxy 风格一致) ----
        self.inner_properties: dict = {
            "algorithm": [
                {"label": "PSO调度 + A*路由 + 最近分配", "value": "pso+astar+nearest"},
                {"label": "DE调度 + A*路由 + 最近分配", "value": "de+astar+nearest"},
                {"label": "DRL调度 + A*路由 + 最近分配", "value": "drl+astar+nearest"},
                {"label": "PSO调度 + MAPF-GPT路由 + 随机分配", "value": "pso+mapf_gpt+random"},
            ],
        }
        self.status: ExecutionStatus = ExecutionStatus.IDLE
        self.current_step: int = 0
        self._total_steps: int = 0

        # ---- compose 配置 ----
        self._compose_file: str = os.getenv(
            "SKYENGINE_COMPOSE_PATH",
            "/opt/skyengine/docker-compose-online.yaml",
        )
        self._project_dir: str = os.getenv(
            "SKYENGINE_PROJECT_DIR",
            "",
        )
        self._project_name: str = "skyengine-online"
        self._engine_url: str = os.getenv(
            "ENGINE_URL",
            f"http://localhost:{os.getenv('ENGINE_PORT', '8080')}",
        )

        self._config: dict = {}
        self._algorithm: str = "pso+astar+nearest"
        self._algorithm_parts: dict = {}
        self.initialized: bool = False

        # SSE 透传
        self._streaming = False

    # ==================== docker compose 辅助 ====================

    async def _compose(self, *args: str, env: dict | None = None) -> str:
        """执行 docker compose 命令并返回输出"""
        cmd = [
            "docker", "compose",
            "-p", self._project_name,
        ]
        if self._project_dir:
            cmd += ["--project-directory", self._project_dir]
        cmd += ["-f", self._compose_file, *args]
        logger.debug(f"[DockerProxy] $ {' '.join(cmd)}")

        merged_env = {**os.environ}
        if env:
            merged_env.update(env)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode().strip()
            logger.error(f"[DockerProxy] compose 失败 (rc={proc.returncode}): {err}")
            raise RuntimeError(f"docker compose failed: {err}")

        return stdout.decode().strip()

    # ==================== 配置方法 ====================

    def set_config(self, config: dict):
        self._config = config

    def set_algorithm(self, algorithm: str) -> None:
        logger.info(f"[DockerProxy] set_algorithm: {algorithm}")
        if not algorithm:
            return
        self._algorithm = algorithm
        parts = algorithm.split("+")
        if len(parts) == 3:
            self._algorithm_parts = {
                "fjsp": parts[0],
                "mapf": parts[1],
                "assigner": parts[2],
            }
        self.inner_properties["current_algorithm"] = algorithm

    def get_algorithm(self) -> str:
        return self._algorithm

    def get_initialized(self) -> bool:
        logger.debug(f"[DockerProxy] get_initialized: {self.initialized}")
        return self.initialized

    # ==================== 生命周期方法 ====================

    async def initialize(self) -> None:
        """Phase 1: docker compose up engine + 等待就绪"""
        logger.info("[DockerProxy] Phase 1: 启动 engine ...")

        # 清理可能残留的旧容器
        try:
            await self._compose("down", "--remove-orphans")
        except Exception:
            pass

        # 启动 engine
        await self._compose("up", "-d", "engine")

        # 等待 engine 健康检查
        await self._wait_for_health()

        self.initialized = True
        self.status = ExecutionStatus.IDLE
        logger.info(f"[DockerProxy] Phase 1 完成: engine 就绪 @ {self._engine_url}")

    async def cleanup(self) -> None:
        try:
            await self._compose("down", "--remove-orphans")
        except Exception as e:
            logger.error(f"[DockerProxy] cleanup 失败: {e}")
        self.initialized = False
        self._engine_url = None

    async def start(self) -> None:
        """Phase 2: docker compose up mapf fjsp + 发送仿真配置 + 启动仿真"""
        if not self.initialized:
            raise RuntimeError("请先调用 initialize()")
        if not self._algorithm_parts:
            raise RuntimeError("未设置算法配置")

        logger.info(f"[DockerProxy] Phase 2: 启动算法容器, 算法={self._algorithm}")

        # 1. 确定镜像，通过环境变量传给 docker compose
        fjsp_algo = self._algorithm_parts.get("fjsp", "pso")
        mapf_algo = self._algorithm_parts.get("mapf", "astar")
        mapf_image = ALGORITHM_MAP["mapf"].get(mapf_algo, "skyengine-mapf-gpt:latest")
        fjsp_image = ALGORITHM_MAP["fjsp"].get(fjsp_algo, "skyengine-fjsp-pso:latest")

        compose_env = {
            "MAPF_IMAGE": mapf_image,
            "FJSP_IMAGE": fjsp_image,
        }
        logger.info(f"[DockerProxy] mapf={mapf_image}, fjsp={fjsp_image}")

        # 2. 启动算法容器
        await self._compose("up", "-d", "mapf", "fjsp", env=compose_env)

        # 2.5 等待 fjsp/mapf 的 Flask 就绪（避免 play 时 Connection refused）
        await self._wait_for_service("fjsp", 8002)
        await self._wait_for_service("mapf", 8001)

        # 3. 发送仿真配置到 engine（前端 JSON 原样转发 + 算法参数）
        sim_config = {
            "config": self._config,
            "fjsp_algorithm": fjsp_algo,
            "mapf_algorithm": mapf_algo,
            "solver_assign": self._algorithm_parts.get("assigner", "nearest"),
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(f"{self._engine_url}/sim/create", json=sim_config)
            await client.post(f"{self._engine_url}/sim/play")

        # 4. 标记流式传输就绪
        self._streaming = True
        self.status = ExecutionStatus.RUNNING
        logger.info("[DockerProxy] Phase 2 完成: 仿真已启动")

    async def pause(self) -> None:
        if self._engine_url:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(f"{self._engine_url}/sim/pause")
        self.status = ExecutionStatus.PAUSED

    async def reset(self) -> None:
        if self._engine_url:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(f"{self._engine_url}/sim/reset")
        self.status = ExecutionStatus.IDLE
        self.current_step = 0

    async def stop(self) -> None:
        self._streaming = False
        if self._engine_url:
            async with httpx.AsyncClient(timeout=5.0) as client:
                try:
                    await client.post(f"{self._engine_url}/sim/stop")
                except Exception:
                    pass
        try:
            await self._compose("down", "--remove-orphans")
        except Exception:
            pass
        self.status = ExecutionStatus.STOPPED
        self.initialized = False

    # ==================== SSE 流接口 ====================

    async def get_state_events(self) -> list:
        return [("state", {"status": self.status.value, "step": self.current_step})]

    async def get_metrics_events(self) -> list:
        return [("metrics", {"status": self.status.value})]

    async def get_control_events(self) -> list:
        return [("control", {"status": self.status.value, "algorithm": self._algorithm})]

    async def get_state_snapshot(self) -> dict:
        return {"status": self.status.value, "step": self.current_step}

    async def get_metrics_snapshot(self) -> dict:
        return {"status": self.status.value}

    async def get_control_status(self) -> dict:
        return {"status": self.status.value, "algorithm": self._algorithm}

    # ==================== 健康检查 ====================

    async def _wait_for_health(self, timeout: float = 60.0):
        logger.info(f"[DockerProxy] 等待 {self._engine_url}/health ...")
        async with httpx.AsyncClient() as client:
            for i in range(int(timeout)):
                try:
                    resp = await client.get(f"{self._engine_url}/health", timeout=2.0)
                    if resp.status_code == 200:
                        logger.info(f"[DockerProxy] engine 就绪 (耗时 {i}s)")
                        return
                except Exception:
                    pass
                await asyncio.sleep(1.0)
        raise TimeoutError(f"Engine not ready within {timeout}s")

    async def _wait_for_service(self, host: str, port: int, timeout: float = 30.0):
        """等待算法容器（fjsp/mapf）的 Flask 服务 listen 就绪。

        docker compose up -d 是异步的——容器启动了但 Flask 可能还在初始化。
        如果不等待就 /sim/play，engine 的 _run_loop 第一步 coordinator.decide()
        会撞上 Connection refused，仿真线程直接崩溃。
        """
        logger.info(f"[DockerProxy] 等待 {host}:{port} 就绪 ...")
        async with httpx.AsyncClient() as client:
            for i in range(int(timeout)):
                try:
                    # 根路径返回 404 也算 ready（只要 TCP 连上了就说明 Flask 在 listen）
                    resp = await client.get(f"http://{host}:{port}/", timeout=2.0)
                    logger.info(f"[DockerProxy] {host}:{port} 就绪 (耗时 {i}s, status={resp.status_code})")
                    return
                except Exception:
                    pass
                await asyncio.sleep(1.0)
        raise TimeoutError(f"Service {host}:{port} not ready within {timeout}s")

    # ==================== SSE 透传 ====================

    async def state_stream(self):
        """直接透传 sim_server 的 /stream/state SSE 事件，零队列缓冲"""
        if not self._engine_url or not self._streaming:
            return
        url = f"{self._engine_url}/stream/state"
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", url) as resp:
                    async for line in resp.aiter_lines():
                        if not self._streaming:
                            break
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                frame = data.get("frame")
                                if frame and "timestamp" in frame:
                                    ts = frame["timestamp"]
                                    if ts.startswith("T+"):
                                        try:
                                            self.current_step = int(ts[2:].rstrip("s"))
                                        except ValueError:
                                            pass
                                yield ("state", data)
                                if data.get("status") == "stopped":
                                    self._streaming = False
                                    self.status = ExecutionStatus.STOPPED
                                    break
                            except (json.JSONDecodeError, KeyError):
                                pass
        except Exception as e:
            logger.error(f"[DockerProxy] state_stream 结束: {e}")
            if self._streaming:
                self.status = ExecutionStatus.ERROR

    async def metrics_stream(self):
        """直接透传 sim_server 的 /stream/metrics SSE 事件"""
        if not self._engine_url or not self._streaming:
            return
        url = f"{self._engine_url}/stream/metrics"
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", url) as resp:
                    async for line in resp.aiter_lines():
                        if not self._streaming:
                            break
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                yield ("metrics", data)
                            except (json.JSONDecodeError, KeyError):
                                pass
        except Exception as e:
            logger.error(f"[DockerProxy] metrics_stream 结束: {e}")

    async def events_stream(self):
        """直接透传 sim_server 的 /stream/events SSE 事件"""
        if not self._engine_url or not self._streaming:
            return
        url = f"{self._engine_url}/stream/events"
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", url) as resp:
                    async for line in resp.aiter_lines():
                        if not self._streaming:
                            break
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                yield ("event", data)
                            except (json.JSONDecodeError, KeyError):
                                pass
        except Exception as e:
            logger.error(f"[DockerProxy] events_stream 结束: {e}")

    # ==================== 状态判断 ====================

    def is_running(self) -> bool:
        return self.status == ExecutionStatus.RUNNING

    def is_paused(self) -> bool:
        return self.status == ExecutionStatus.PAUSED

    def is_idle(self) -> bool:
        return self.status == ExecutionStatus.IDLE
