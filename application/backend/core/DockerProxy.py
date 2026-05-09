"""
DockerProxy — 通过 docker SDK 按需启停 SkyEngine 在线仿真服务

纯 Python 实现，无需在容器内安装 Docker CLI。
通过 docker SDK (Python) 直连 Docker Socket 管理容器/网络。

两阶段启动:
  1. initialize() — 创建网络 + 启动 engine 容器
  2. start()      — 根据算法选择启动 mapf/fjsp 容器
                    → HTTP 发送仿真配置 → 启动仿真
  3. stop()       — 停止并移除所有容器 + 网络
"""

import os
import uuid
import asyncio
import threading
import json
from enum import Enum

import httpx
import docker


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
    """通过 docker SDK 联动 SkyEngine 在线服务的工厂代理"""

    def __init__(self):
        print("[DockerProxy] __init__ 被调用")
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

        # ---- DockerProxy 专有状态 ----
        self._skyengine_dir: str = os.getenv(
            "SKYENGINE_DIR", "/opt/skyengine"
        )
        self._dataset_dir: str = os.getenv(
            "SKYENGINE_DATASET_DIR", "/opt/skyengine/dataset"
        )
        self._project_name: str | None = None

        self._client: docker.DockerClient | None = None
        self._engine_url: str | None = None
        self._network: docker.models.networks.Network | None = None

        # 容器引用
        self._engine_container = None
        self._mapf_container = None
        self._fjsp_container = None

        self._config: dict = {}
        self._algorithm: str = "pso+astar+nearest"
        self._algorithm_parts: dict = {}
        self.initialized: bool = False

        # SSE 轮询
        self._poll_thread: threading.Thread | None = None
        self._state_queue_internal: asyncio.Queue = asyncio.Queue()
        self._polling = False

    # ==================== 配置方法 ====================

    def set_config(self, config: dict):
        self._config = config

    def set_algorithm(self, algorithm: str) -> None:
        print(f"[DockerProxy] set_algorithm: {algorithm}")
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
        print(f"[DockerProxy] get_initialized: {self.initialized}")
        return self.initialized

    # ==================== 生命周期方法 ====================

    async def initialize(self) -> None:
        """Phase 1: 创建网络 + 启动 engine 容器"""
        print("[DockerProxy] Phase 1: 初始化引擎...")

        self._project_name = "skyengine-online"
        self._client = docker.from_env()

        # 0. 清理可能残留的旧容器/网络
        await self._cleanup_all()

        # 1. 创建独立网络
        self._network = await asyncio.to_thread(
            self._client.networks.create,
            f"{self._project_name}_sim-net",
            driver="bridge",
        )
        print(f"[DockerProxy] 网络 {self._network.name} 已创建")

        # 2. 启动 engine 容器
        # SKYENGINE_DIR / SKYENGINE_DATASET_DIR 是宿主机路径!
        # Docker SDK 在 HOST 上创建容器, volume 必须用宿主机路径
        sky_dir_host = os.getenv("SKYENGINE_DIR", "/opt/skyengine")
        dataset_dir_host = self._dataset_dir  # 已经是宿主机路径

        engine_volumes = {
            dataset_dir_host: {"bind": "/dataset", "mode": "ro"},
            f"{sky_dir_host}/sim_server.py": {"bind": "/app/sim_server.py", "mode": "ro"},
            f"{sky_dir_host}/sky_executor": {"bind": "/app/sky_executor", "mode": "ro"},
        }

        print(f"[DockerProxy] engine volume 挂载: {json.dumps(engine_volumes, indent=2)}")

        engine_env = {
            "SKY_LOG_LEVEL": "INFO",
            "CUDA_VISIBLE_DEVICES": os.getenv("CUDA_VISIBLE_DEVICES", "0"),
            "SEED": "42",
            "DATA_DIR": "/dataset",
            "SKY_LOG_DIR": "/app/sky_logs",
            "PYTHONUNBUFFERED": "1",
        }

        try:
            # 尝试创建 sky_logs 目录的 volume
            self._engine_container = await asyncio.to_thread(
                self._client.containers.run,
                image="skyengine:latest",
                command=["uv", "run", "python", "-u", "sim_server.py"],
                name=f"{self._project_name}-engine",
                environment=engine_env,
                volumes=engine_volumes,
                detach=True,
                network=self._network.name,
                # GPU 支持
                device_requests=[
                    docker.types.DeviceRequest(
                        device_ids=[os.getenv("CUDA_VISIBLE_DEVICES", "0")],
                        capabilities=[["gpu"]],
                    )
                ] if os.getenv("CUDA_VISIBLE_DEVICES") else None,
            )
        except Exception as e:
            print(f"[DockerProxy] engine 启动失败 (无 GPU fallback): {e}")
            # fallback: 不挂 GPU
            self._engine_container = await asyncio.to_thread(
                self._client.containers.run,
                image="skyengine:latest",
                command=["uv", "run", "python", "-u", "sim_server.py"],
                name=f"{self._project_name}-engine",
                environment=engine_env,
                volumes=engine_volumes,
                detach=True,
                network=self._network.name,
            )

        print(f"[DockerProxy] engine 容器已启动: {self._engine_container.name}")

        # 3. 发现引擎 IP + 等待就绪
        self._engine_url = await self._discover_engine()
        await self._wait_for_health(self._engine_url)

        self.initialized = True
        self.status = ExecutionStatus.IDLE
        print(f"[DockerProxy] Phase 1 完成: engine 就绪 @ {self._engine_url}")

    async def cleanup(self) -> None:
        await self._cleanup_all()

    async def start(self) -> None:
        """Phase 2: 启动 mapf/fjsp 容器 + 发送仿真配置 + 启动仿真"""
        if not self.initialized:
            raise RuntimeError("请先调用 initialize()")
        if not self._algorithm_parts:
            raise RuntimeError("未设置算法配置")

        print(f"[DockerProxy] Phase 2: 启动算法容器, 算法={self._algorithm}")

        # 1. 确定镜像名
        fjsp_algo = self._algorithm_parts.get("fjsp", "pso")
        mapf_algo = self._algorithm_parts.get("mapf", "astar")
        mapf_image = ALGORITHM_MAP["mapf"].get(mapf_algo, "skyengine-mapf-gpt:latest")
        fjsp_image = ALGORITHM_MAP["fjsp"].get(fjsp_algo, "skyengine-fjsp-pso:latest")

        common_env = {
            "SKY_LOG_LEVEL": "INFO",
            "CUDA_VISIBLE_DEVICES": os.getenv("CUDA_VISIBLE_DEVICES", "0"),
            "SEED": "42",
        }

        # 2. 启动 mapf 容器
        print(f"[DockerProxy] 启动 mapf: {mapf_image}")
        self._mapf_container = await asyncio.to_thread(
            self._client.containers.run,
            image=mapf_image,
            name=f"{self._project_name}-mapf",
            environment={**common_env, "TIME_LIMIT": "30"},
            detach=True,
            network=self._network.name,
            device_requests=[
                docker.types.DeviceRequest(
                    device_ids=[os.getenv("CUDA_VISIBLE_DEVICES", "0")],
                    capabilities=[["gpu"]],
                )
            ] if os.getenv("CUDA_VISIBLE_DEVICES") else None,
        )

        # 3. 启动 fjsp 容器
        print(f"[DockerProxy] 启动 fjsp: {fjsp_image}")
        self._fjsp_container = await asyncio.to_thread(
            self._client.containers.run,
            image=fjsp_image,
            name=f"{self._project_name}-fjsp",
            environment={**common_env, "TIME_LIMIT": "5"},
            detach=True,
            network=self._network.name,
            device_requests=[
                docker.types.DeviceRequest(
                    device_ids=[os.getenv("CUDA_VISIBLE_DEVICES", "0")],
                    capabilities=[["gpu"]],
                )
            ] if os.getenv("CUDA_VISIBLE_DEVICES") else None,
        )

        # 4. 发送仿真配置到 engine
        sim_config = {
            "fjsp_instance": self._config.get("fjsp_instance", "J10P5M6.json"),
            "mapf_instance": self._config.get("mapf_instance", ""),
            "solver_job": "http",
            "solver_route": "http",
            "solver_assign": self._algorithm_parts.get("assigner", "random"),
            "mapf_service_url": "http://mapf:8001",
            "fjsp_service_url": "http://fjsp:8002",
            "num_agv": self._config.get("num_agv", 4),
            "seed": self._config.get("seed", 42),
            "max_steps": self._config.get("max_steps", 1000),
            "obs_radius": self._config.get("obs_radius", 5),
            "obs_type": self._config.get("obs_type", "default"),
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(f"{self._engine_url}/sim/create", json=sim_config)
            await client.post(f"{self._engine_url}/sim/play")

        # 5. 启动 SSE 轮询
        self._start_polling()

        self.status = ExecutionStatus.RUNNING
        print("[DockerProxy] Phase 2 完成: 仿真已启动")

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
        self._stop_polling()
        if self._engine_url:
            async with httpx.AsyncClient(timeout=5.0) as client:
                try:
                    await client.post(f"{self._engine_url}/sim/stop")
                except Exception:
                    pass
        await self._cleanup_all()
        self.status = ExecutionStatus.STOPPED

    # ==================== SSE 流接口 ====================

    async def get_state_events(self) -> list:
        events = []
        while not self._state_queue_internal.empty():
            events.append(await self._state_queue_internal.get())
        if not events:
            events.append(("state", {"status": self.status.value, "step": self.current_step}))
        return events

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

    # ==================== 容器/网络清理 ====================

    async def _cleanup_all(self):
        """停止并移除所有容器 + 网络"""
        for container in [self._mapf_container, self._fjsp_container, self._engine_container]:
            if container:
                try:
                    await asyncio.to_thread(container.stop)
                    await asyncio.to_thread(container.remove)
                except Exception as e:
                    print(f"[DockerProxy] 清理容器失败: {e}")

        self._engine_container = None
        self._mapf_container = None
        self._fjsp_container = None

        if self._network:
            try:
                await asyncio.to_thread(self._network.remove)
            except Exception as e:
                print(f"[DockerProxy] 清理网络失败: {e}")
            self._network = None

        self._engine_url = None
        self._project_name = None
        self.initialized = False
        print("[DockerProxy] 容器栈已清理")

    # ==================== 引擎发现 ====================

    async def _discover_engine(self) -> str:
        """获取引擎容器 IP"""
        container_name = f"{self._project_name}-engine"
        for attempt in range(30):
            try:
                self._engine_container.reload()
                networks = self._engine_container.attrs["NetworkSettings"]["Networks"]
                status = self._engine_container.status
                print(f"[DockerProxy] _discover_engine 尝试 {attempt}: status={status}, networks={list(networks.keys())}")
                for net_name, net_info in networks.items():
                    ip = net_info.get("IPAddress")
                    print(f"[DockerProxy]   网络 {net_name}: IP={ip}")
                    if ip:
                        return f"http://{ip}:8080"
            except Exception as e:
                print(f"[DockerProxy] _discover_engine 尝试 {attempt} 异常: {e}")
            await asyncio.sleep(1.0)

        raise RuntimeError(f"无法发现引擎容器 IP: {container_name}")

    async def _wait_for_health(self, url: str, timeout: float = 60.0):
        print(f"[DockerProxy] _wait_for_health: 等待 {url}/health ...")
        async with httpx.AsyncClient() as client:
            for i in range(int(timeout)):
                try:
                    resp = await client.get(f"{url}/health", timeout=2.0)
                    print(f"[DockerProxy]   health 尝试 {i}: status={resp.status_code}")
                    if resp.status_code == 200:
                        print(f"[DockerProxy] engine 就绪 (耗时 {i}s)")
                        return
                except Exception as e:
                    print(f"[DockerProxy]   health 尝试 {i} 失败: {type(e).__name__}: {e}")
                await asyncio.sleep(1.0)
        raise TimeoutError(f"Engine not ready within {timeout}s")

    # ==================== SSE 轮询 ====================

    def _start_polling(self):
        self._polling = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _stop_polling(self):
        self._polling = False
        if self._poll_thread:
            self._poll_thread.join(timeout=5)
            self._poll_thread = None

    def _poll_loop(self):
        url = f"{self._engine_url}/stream/state"
        try:
            with httpx.stream("GET", url, timeout=None) as resp:
                for line in resp.iter_lines():
                    if not self._polling:
                        break
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            self._state_queue_internal.put_nowait(("state", data))
                            frame = data.get("frame")
                            if frame and "timestamp" in frame:
                                ts = frame["timestamp"]
                                if ts.startswith("T+"):
                                    try:
                                        self.current_step = int(ts[2:].rstrip("s"))
                                    except ValueError:
                                        pass
                        except Exception:
                            pass
        except Exception as e:
            print(f"[DockerProxy] SSE 轮询结束: {e}")

    # ==================== 状态判断 ====================

    def is_running(self) -> bool:
        return self.status == ExecutionStatus.RUNNING

    def is_paused(self) -> bool:
        return self.status == ExecutionStatus.PAUSED

    def is_idle(self) -> bool:
        return self.status == ExecutionStatus.IDLE
