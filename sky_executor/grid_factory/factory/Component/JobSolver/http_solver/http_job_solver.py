"""
HTTP JobSolver — 通过 HTTP 调用 FJSP 微服务

调用 FJSP Docker 容器的 Flask HTTP API（/health, /init, /plan, /reset）。
与 FJSP 的 best_solver_server.py / de_solver_server.py / pso_solver_server.py 对接。

"""

import requests
import os
from sky_executor.grid_factory.factory.Component.JobSolver.job_solver_factory import (
    JobSolverFactory,
)
from sky_executor.grid_factory.factory.Component.JobSolver.template_solver.job_solver import (
    JobSolver,
)

# 通信字段：
# jobs: jobs[job_id][op_id] = [(duration, machine), ...]
# machines: machines[machine_id]={loc:(x,y);current_op:op_id}


@JobSolverFactory.register_solver("http")
class HTTPJobSolver(JobSolver):
    """通过 HTTP 调用 FJSP 服务的作业调度求解器"""

    def __init__(
        self,
        service_url: str = os.getenv("FJSP_SERVICE_URL", "http://localhost:8001"),
        algorithm: str = "best",
        config: dict = None,
        timeout: float = float(os.getenv("HTTP_TIMEOUT", 600)),
        **kwargs,
    ):
        """
        Args:
            service_url: FJSP 服务地址
            algorithm: 算法名称（best/de/pso），用于日志
            config: 传递给 FJSP 服务端的算法参数
            timeout: HTTP 请求超时秒数
        """
        super().__init__()
        self.service_url = service_url.rstrip("/")
        self.algorithm = algorithm
        self.solver_config = config or {}
        self.timeout = timeout

    def _serialize_obs(self, obs: dict) -> dict:
        """将 SkyEngine 的 Job/Machine 对象序列化为 FJSP 服务端可识别的 JSON

        输出格式:
            jobs[job_id][op_id] = [(duration, machine), ...]
            machines[machine_id] = {loc: (x, y), current_op: op_id | None}
        """
        jobs_data = {}
        for job in obs["jobs"]:
            ops_data = []
            for op in job.ops:
                # machine_options_with_time: List[Tuple[machine_id, proc_time]]
                if op.machine_options_with_time:
                    ops_data.append(
                        [(pt, mid) for mid, pt in op.machine_options_with_time]
                    )
                else:
                    ops_data.append([])
            jobs_data[job.job_id] = ops_data

        machines_data = {}
        for m in obs["machines"]:
            machines_data[m.id] = {
                "loc": list(m.location),
                "current_op": (
                    m.current_op.op_id if m.current_op is not None else None
                ),
            }
        # 后续server的主要格式要求
        return {"jobs": jobs_data, "machines": machines_data}

    def plan(self, obs: dict) -> dict:
        obs_json = self._serialize_obs(obs)
        payload = {
            "obs": obs_json,
            "config": self.solver_config,
        }

        resp = requests.post(
            f"{self.service_url}/plan",
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        # Resolve the service protocol here; the environment accepts physical coordinates.
        locations: dict = {machine.id: machine.location for machine in obs["machines"]}
        for task in data.get("transfer_requests", []):
            destination_id: int = int(task["candidate_machines"][0])
            task["destination_machine_id"] = destination_id
            task["destination"] = list(locations[destination_id])
            task["source"] = [-1, -1]  # Environment uses the workpiece's actual location.
        # FJSP 服务端返回的 transfer_requests 已经是 dict 列表
        # 直接返回，让 Coordinator / GridFactoryEnv 处理
        return {
            "machine_actions": data.get("machine_actions", []),
            "transfer_requests": data.get("transfer_requests", []),
        }

    def reset(self) -> None:
        """Clear server-side online schedule state before a new episode."""
        resp = requests.post(
            f"{self.service_url}/reset",
            timeout=self.timeout,
        )
        resp.raise_for_status()

    def replan(self, problem: dict) -> dict:
        if self.algorithm.lower() != "pso":
            raise RuntimeError(f"urgent insertion does not support {self.algorithm}")
        resp = requests.post(
            f"{self.service_url}/replan",
            json={"problem": problem, "config": self.solver_config},
            timeout=self.timeout,
        )
        if not resp.ok:
            try:
                message = resp.json().get("message")
            except ValueError:
                message = resp.text
            raise RuntimeError(message or f"replan failed with HTTP {resp.status_code}")
        resp.raise_for_status()
        return resp.json()
