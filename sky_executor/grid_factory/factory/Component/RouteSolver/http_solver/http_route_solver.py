"""
HTTP RouteSolver — 通过 HTTP 调用 MAPF 微服务

Online 模式：适配 A*、MAPF-GPT 等逐帧决策算法。
直接序列化 env 返回的 obs 并转发给 MAPF 服务，由 GridConfig 的 observation_type 控制格式。
"""

import numpy as np
import requests
import os
from dataclasses import asdict, is_dataclass
from enum import Enum

from sky_executor.grid_factory.factory.Component.RouteSolver.route_solver_factory import (
    RouteSolverFactory,
)
from sky_executor.grid_factory.factory.Component.RouteSolver.template_solver.route_solver import (
    RouteSolver,
)


@RouteSolverFactory.register_solver("http")
class HTTPRouteSolver(RouteSolver):
    """通过 HTTP 调用 MAPF 服务的在线路由求解器"""

    def __init__(
        self,
        service_url: str = os.getenv("MAPF_SERVICE_URL", "http://localhost:8001"),
        timeout: float = float(os.getenv("HTTP_TIMEOUT", "6")),
        accepts_task_observation: bool = False,
        **kwargs,
    ):
        """
        Args:
            service_url: MAPF 服务地址
            timeout: HTTP 请求超时秒数
        """
        super().__init__()
        self.service_url = service_url.rstrip("/")
        self.timeout = timeout
        self.accepts_task_observation = bool(accepts_task_observation)

    @staticmethod
    def _serialize_obs(obs):
        """递归序列化 obs 为 JSON 兼容结构（支持 numpy / dict / list / tuple）"""
        if isinstance(obs, np.ndarray):
            return obs.tolist()
        if isinstance(obs, np.integer):
            return int(obs)
        if isinstance(obs, np.floating):
            return float(obs)
        if isinstance(obs, Enum):
            return HTTPRouteSolver._serialize_obs(obs.value)
        if hasattr(obs, "model_dump"):
            return HTTPRouteSolver._serialize_obs(obs.model_dump())
        if hasattr(obs, "dict") and obs.__class__.__module__.startswith("pydantic"):
            return HTTPRouteSolver._serialize_obs(obs.dict())
        if is_dataclass(obs):
            return HTTPRouteSolver._serialize_obs(asdict(obs))
        if isinstance(obs, dict):
            return {
                str(k): HTTPRouteSolver._serialize_obs(v)
                for k, v in obs.items()
            }
        if isinstance(obs, (list, tuple, set, frozenset)):
            return [HTTPRouteSolver._serialize_obs(o) for o in obs]
        if isinstance(obs, (int, float, bool)):
            return obs
        if obs is None:
            return None
        if hasattr(obs, "__dict__"):
            return HTTPRouteSolver._serialize_obs({
                key: value
                for key, value in vars(obs).items()
                if not key.startswith("_")
            })
        return obs

    def plan(self, obs, task_observation=None) -> list:
        payload = {"obs": self._serialize_obs(obs)}
        if self.accepts_task_observation and task_observation is not None:
            payload["task_observation"] = self._serialize_obs(task_observation)
        resp = requests.post(
            f"{self.service_url}/plan",
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["actions"]
