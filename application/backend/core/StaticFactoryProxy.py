"""
@Project ：SkyEngine
@File    ：StaticFactoryProxy.py
@IDE     ：PyCharm
@Author  ：Skyrimforest
@Date    ：2025/1/19 14:50
"""

import asyncio
import json
import logging
from typing import Optional, Dict, Any
from enum import Enum
import random

from fastapi.responses import Response

logger = logging.getLogger(__name__)

# Import base proxy class and ProxyFactory
from .BaseFactoryProxy import BaseFactoryProxy, ExecutionStatus
from .ProxyFactory import ProxyFactory
from .RouteRegistry import RouteRegistry


# ============ 数据库：从 fullSystemTest.js 移植 ============

# AGV 轨迹数据 (0-54 步)
AGV_TRAJECTORIES = [
    {"step": 0, "agvs": [{"x": 5, "y": 2, "active": True}]},
    {"step": 1, "agvs": [{"x": 5, "y": 3, "active": True}]},
    {"step": 2, "agvs": [{"x": 5, "y": 4, "active": True}]},
    {"step": 3, "agvs": [{"x": 5, "y": 5, "active": True}]},
    {"step": 4, "agvs": [{"x": 5, "y": 6, "active": True}]},
    {"step": 5, "agvs": [{"x": 5, "y": 7, "active": True}]},
    {"step": 6, "agvs": [{"x": 5, "y": 8, "active": True}]},
    {"step": 7, "agvs": [{"x": 5, "y": 9, "active": True}]},
    {"step": 8, "agvs": [{"x": 5, "y": 10, "active": True}]},
    {"step": 9, "agvs": [{"x": 5, "y": 11, "active": True}]},
    {"step": 10, "agvs": [{"x": 6, "y": 11, "active": True}]},
    {"step": 11, "agvs": [{"x": 7, "y": 11, "active": True}]},
    {"step": 12, "agvs": [{"x": 8, "y": 11, "active": True}]},
    {"step": 13, "agvs": [{"x": 8, "y": 10, "active": True}]},
    {"step": 14, "agvs": [{"x": 8, "y": 9, "active": True}]},
    {"step": 15, "agvs": [{"x": 8, "y": 8, "active": True}]},
    {"step": 16, "agvs": [{"x": 8, "y": 7, "active": True}]},
    {"step": 17, "agvs": [{"x": 8, "y": 6, "active": True}]},
    {"step": 18, "agvs": [{"x": 8, "y": 5, "active": True}]},
    {"step": 19, "agvs": [{"x": 8, "y": 4, "active": True}]},
    {"step": 20, "agvs": [{"x": 9, "y": 4, "active": True}]},
    {"step": 21, "agvs": [{"x": 10, "y": 4, "active": True}]},
    {"step": 22, "agvs": [{"x": 11, "y": 4, "active": True}]},
    {"step": 23, "agvs": [{"x": 12, "y": 4, "active": True}]},
    {"step": 24, "agvs": [{"x": 13, "y": 4, "active": True}]},
    {"step": 25, "agvs": [{"x": 14, "y": 4, "active": True}]},
    {"step": 26, "agvs": [{"x": 15, "y": 4, "active": True}]},
    {"step": 27, "agvs": [{"x": 16, "y": 4, "active": True}]},
    {"step": 28, "agvs": [{"x": 15, "y": 4, "active": True}]},
    {"step": 29, "agvs": [{"x": 14, "y": 4, "active": True}]},
    {"step": 30, "agvs": [{"x": 14, "y": 5, "active": True}]},
    {"step": 31, "agvs": [{"x": 14, "y": 6, "active": True}]},
    {"step": 32, "agvs": [{"x": 14, "y": 7, "active": True}]},
    {"step": 33, "agvs": [{"x": 14, "y": 8, "active": True}]},
    {"step": 34, "agvs": [{"x": 14, "y": 9, "active": True}]},
    {"step": 35, "agvs": [{"x": 14, "y": 10, "active": True}]},
    {"step": 36, "agvs": [{"x": 14, "y": 11, "active": True}]},
    {"step": 37, "agvs": [{"x": 13, "y": 11, "active": True}]},
    {"step": 38, "agvs": [{"x": 12, "y": 11, "active": True}]},
    {"step": 39, "agvs": [{"x": 11, "y": 11, "active": True}]},
    {"step": 40, "agvs": [{"x": 10, "y": 11, "active": True}]},
    {"step": 41, "agvs": [{"x": 9, "y": 11, "active": True}]},
    {"step": 42, "agvs": [{"x": 8, "y": 11, "active": True}]},
    {"step": 43, "agvs": [{"x": 7, "y": 11, "active": True}]},
    {"step": 44, "agvs": [{"x": 6, "y": 11, "active": True}]},
    {"step": 45, "agvs": [{"x": 5, "y": 11, "active": True}]},
    {"step": 46, "agvs": [{"x": 5, "y": 10, "active": True}]},
    {"step": 47, "agvs": [{"x": 5, "y": 9, "active": True}]},
    {"step": 48, "agvs": [{"x": 5, "y": 8, "active": True}]},
    {"step": 49, "agvs": [{"x": 5, "y": 7, "active": True}]},
    {"step": 50, "agvs": [{"x": 5, "y": 6, "active": True}]},
    {"step": 51, "agvs": [{"x": 5, "y": 5, "active": True}]},
    {"step": 52, "agvs": [{"x": 5, "y": 4, "active": True}]},
    {"step": 53, "agvs": [{"x": 5, "y": 3, "active": True}]},
    {"step": 54, "agvs": [{"x": 5, "y": 2, "active": True}]}
]

# Job-Op 静态剧本（与 AGV_TRAJECTORIES / machine 阶段对齐）
#
# 设计原则（见 docs/explore/0622更新设计.md 模块①）:
# - 3 Job × 3 Op，覆盖 0-54 步，确定性，不引入 random
# - arrive/start/finish 单位均为 step，与 env_timeline 对齐
# - assigned_machine 用 int (1/2/3) → 前端 `M${id}` 拼接对应 "M1"/"M2"/"M3"
# - J1 故意超期 (due < completion_time)，J2 准时，J3 无交期，覆盖三种分支
#
# 与 machine 阶段对齐:
#   step 5-15  M1 warmup   ← J1.Op0 (5-10), J2.Op0 (10-15)
#   step 16-35 M1+M2 heavy ← J1.Op1 (16-31 on M2), J2.Op1 (31-35 on M2)
#   step 36-42 M1 BROKEN/M3 WORKING ← J3.Op0 (36-39), J3.Op1 (39-42) on M3
#   step 43-54 M1 cooldown  ← J1.Op2 (43-47), J2.Op2 (47-50), J3.Op2 (50-52)
JOB_SCRIPT = [
    {
        "job_id": 1, "release": 0, "due": 20,  # 故意超期
        "ops": [
            {"op_id": 0, "proc_time": 5,  "assigned_machine": 1,
             "arrive_machine_at": 5,  "start_process_at": 5,  "finish_process_at": 10},
            {"op_id": 1, "proc_time": 15, "assigned_machine": 2,
             "arrive_machine_at": 10, "start_process_at": 16, "finish_process_at": 31},
            {"op_id": 2, "proc_time": 4,  "assigned_machine": 1,
             "arrive_machine_at": 31, "start_process_at": 43, "finish_process_at": 47},
        ],
    },
    {
        "job_id": 2, "release": 0, "due": 60,  # 准时
        "ops": [
            {"op_id": 0, "proc_time": 5, "assigned_machine": 1,
             "arrive_machine_at": 10, "start_process_at": 10, "finish_process_at": 15},
            {"op_id": 1, "proc_time": 4, "assigned_machine": 2,
             "arrive_machine_at": 15, "start_process_at": 31, "finish_process_at": 35},
            {"op_id": 2, "proc_time": 3, "assigned_machine": 1,
             "arrive_machine_at": 35, "start_process_at": 47, "finish_process_at": 50},
        ],
    },
    {
        "job_id": 3, "release": 0, "due": None,  # 无交期
        "ops": [
            {"op_id": 0, "proc_time": 3, "assigned_machine": 3,
             "arrive_machine_at": 36, "start_process_at": 36, "finish_process_at": 39},
            {"op_id": 1, "proc_time": 3, "assigned_machine": 3,
             "arrive_machine_at": 39, "start_process_at": 39, "finish_process_at": 42},
            {"op_id": 2, "proc_time": 2, "assigned_machine": 1,
             "arrive_machine_at": 42, "start_process_at": 50, "finish_process_at": 52},
        ],
    },
]


# 事件日志
EVENTS_LOG = [
    {"step": 0, "title": "系统就绪", "message": "AGV #1 已上线，坐标 (5, 2)，等待指令", "type": "info"},
    {"step": 10, "title": "进入作业区", "message": "AGV 到达上料缓冲区 (Y=11)，M1 开始预热", "type": "success"},
    {"step": 27, "title": "折返点装载", "message": "AGV 到达最远端 (16, 4)，执行自动装载任务", "type": "task"},
    {"step": 36, "title": "设备告警", "message": "M1 主轴过载 (Load 99%)，触发安全停机", "type": "error"},
    {"step": 43, "title": "故障排除", "message": "M1 重启完成，系统恢复正常运行", "type": "success"},
    {"step": 54, "title": "任务完成", "message": "AGV 返回原点 (5, 2)，作业流程结束", "type": "success"}
]


class FactorySimulator:
    """Factory simulator generating data matching fullSystemTest.js format"""

    @staticmethod
    def generate_jobs(step: int) -> list:
        """从 JOB_SCRIPT 派生当前 step 的 jobs 快照（模块①）。

        - 单向数据流：JOB_SCRIPT → generate_jobs → jobs[]
        - generate_machine_states 反查本输出得到 current_op / queue_length
        - op status 判定：step >= finish → FINISHED；step >= start → PROCESSING；否则 PENDING
        """
        jobs_out = []
        for job in JOB_SCRIPT:
            ops_def = job["ops"]
            total = len(ops_def)
            ops_out = []
            done_count = 0
            last_finish = -1

            for op in ops_def:
                start = op["start_process_at"]
                finish = op["finish_process_at"]

                if finish >= 0 and step >= finish:
                    status = "FINISHED"
                    done_count += 1
                    step_done = op["proc_time"]
                    last_finish = max(last_finish, finish)
                elif start >= 0 and step >= start:
                    status = "PROCESSING"
                    step_done = max(0, min(op["proc_time"], step - start))
                else:
                    status = "PENDING"
                    step_done = 0

                wait = max(0, start - op["arrive_machine_at"]) if start >= 0 else 0

                ops_out.append({
                    "op_id": op["op_id"],
                    "status": status,
                    "proc_time": op["proc_time"],
                    "assigned_machine": op["assigned_machine"],
                    "arrive_machine_at": op["arrive_machine_at"],
                    "start_process_at": start,
                    "finish_process_at": finish if status != "PENDING" else -1,
                    "wait_for_machine_time": wait,
                    "step_done": step_done,
                })

            is_completed = done_count == total
            jobs_out.append({
                "job_id": job["job_id"],
                "release": job["release"],
                "due": job["due"],
                "is_completed": is_completed,
                "completion_time": last_finish if is_completed else -1,
                "progress": {"done": done_count, "total": total},
                "ops": ops_out,
            })
        return jobs_out

    @staticmethod
    def generate_machine_states(step: int) -> dict:
        """生成机器状态（含 current_op 对象 + queue_length，模块①）。

        - status / load 保留原阶段化逻辑（向后兼容）
        - current_op: 反查 generate_jobs(step)，找 PROCESSING 且 assigned_machine==本机 的 op
        - queue_length: 已到达 (arrive_machine_at <= step) 且 PENDING 且 assigned_machine==本机 的 op 数
        - 当 current_op 存在时强制 status=WORKING，避免状态/进度条矛盾
        """
        # 初始化机器
        m1 = {"id": "M1", "status": "IDLE", "load": 0}
        m2 = {"id": "M2", "status": "IDLE", "load": 0}
        m3 = {"id": "M3", "status": "IDLE", "load": 0}

        # 阶段1: 预热 (Step 5-15)
        if 5 <= step < 16:
            m1 = {"id": "M1", "status": "WORKING", "load": 10 + (step - 5) * 2}

        # 阶段2: 高负荷作业 (Step 16-35)
        if 16 <= step < 36:
            m1 = {"id": "M1", "status": "WORKING", "load": 60 + random.randint(0, 20)}
            m2 = {"id": "M2", "status": "WORKING", "load": 40 + random.randint(0, 10)}

        # 阶段3: 模拟故障 (Step 36-42)
        if 36 <= step <= 42:
            m1 = {"id": "M1", "status": "BROKEN", "load": 99}
            m2 = {"id": "M2", "status": "IDLE", "load": 0}
            m3 = {"id": "M3", "status": "WORKING", "load": 20}

        # 阶段4: 恢复与收尾 (Step 43-54)
        if step > 42:
            cooldown = max(0, 50 - (step - 42) * 5)
            m1 = {"id": "M1", "status": "WORKING", "load": cooldown}
            m2 = {"id": "M2", "status": "WORKING", "load": cooldown // 2}

        # 结束时刻归零
        if step == 54:
            m1 = {"id": "M1", "status": "IDLE", "load": 0}
            m2 = {"id": "M2", "status": "IDLE", "load": 0}

        machines = {"M1": m1, "M2": m2, "M3": m3}

        # 初始化新字段
        for m in machines.values():
            m["current_op"] = None
            m["queue_length"] = 0

        # 反查 jobs 填充 current_op / queue_length
        for job in FactorySimulator.generate_jobs(step):
            for op in job["ops"]:
                mid = op.get("assigned_machine")
                if mid is None:
                    continue
                key = f"M{mid}"
                if key not in machines:
                    continue
                if op["status"] == "PROCESSING":
                    machines[key]["current_op"] = {
                        "job_id": job["job_id"],
                        "op_id": op["op_id"],
                        "index_in_job": op["op_id"],
                        "total_in_job": len(job["ops"]),
                        "step_done": op["step_done"],
                        "proc_time": op["proc_time"],
                    }
                    # 强制对齐：有 current_op 必为 WORKING
                    machines[key]["status"] = "WORKING"
                elif op["status"] == "PENDING" and op["arrive_machine_at"] <= step:
                    machines[key]["queue_length"] += 1

        return machines

    @staticmethod
    def get_events(step: int) -> list:
        """获取事件日志"""
        return [event for event in EVENTS_LOG if event["step"] == step]

    @staticmethod
    def generate_metrics_data(step: int) -> dict:
        """生成性能指标数据（匹配前端格式）"""
        # 获取机器状态作为参考
        machines = FactorySimulator.generate_machine_states(step)
        m_load = machines["M1"]["load"]

        # 效率: 随负载升高，故障时下降
        eff_val = 0
        eff_type = "info"

        if 0 < m_load < 80:
            eff_val = 60 + random.random() * 20
            eff_type = "success"
        elif 80 <= m_load < 99:
            eff_val = 90
            eff_type = "warning"
        elif m_load == 99:
            eff_val = 0
            eff_type = "danger"

        # 利用率
        util_val = min(100, step * 2)
        if step > 40:
            util_val = max(0, 100 - (step - 40) * 10)

        return {
            "step": step,
            "machine": {
                "data": [
                    machines["M1"]["load"],
                    machines["M2"]["load"],
                    machines["M3"]["load"],
                    max(0, random.randint(0, 10)),
                    0
                ],
                "labels": ["M1", "M2", "M3", "M4", "M5"]
            },
            "agv": {
                "data": [
                    min(step * 1.5, 100),  # 耗电量
                    2 if 0 < step < 54 else 0,  # 速度
                    step // 10,  # 任务数
                    1 if 36 <= step <= 42 else 0  # 异常
                ]
            },
            "job": {
                "data": [100 + step * 5, step * 2, 0, 0, 0]
            },
            "keyMetrics": {
                "efficiency": {"value": f"{int(eff_val)}%", "type": eff_type},
                "utilization": {"value": f"{int(util_val)}%", "type": "warning" if util_val > 80 else "success"}
            }
        }


@ProxyFactory.register_proxy("static_factory")
@ProxyFactory.register_proxy("northeast_center")
class StaticFactoryProxy(BaseFactoryProxy):
    """
    Static factory proxy that uses mock simulation data.

    Provides the same interface as GridFactoryProxy but uses
    FactorySimulator for mock data generation, matching the
    frontend's expected format.
    """

    def __init__(self):
        # Call parent init
        super().__init__()

        # Execution State
        self._current_step: int = 0
        self._total_steps: int = 55  # Default total steps for mock
        self.initialized: Optional[bool] = False  # Track initialization state
    # ==================== Configuration Methods ====================

    def get_initialized(self) -> Optional[bool]:
        """Get initialization state"""
        return self.initialized
    def set_config(self, config: dict):
        """Set factory configuration (not used for static proxy)"""
        # Static proxy doesn't need configuration
        pass

    async def initialize(self):
        """Initialize factory proxy"""
        # Initialize queues
        self._state_queue = asyncio.Queue(maxsize=100)
        self._metrics_queue = asyncio.Queue(maxsize=100)
        self._control_queue = asyncio.Queue(maxsize=100)
        self._current_step = 0
        self._status = ExecutionStatus.IDLE
        self.initialized = True  # Mark as initialized

        # 注册模块③ demo 路由（模拟服务端下载）
        self._register_analysis_routes()

    def _register_analysis_routes(self):
        """模块③ 演示路由：服务端下载接口的 mock 版本。

        设计：
        - 前端把内存中的 run 快照 POST 过来
        - 后端加上 Content-Disposition: attachment 头把同样 JSON 回吐
        - 浏览器看到 attachment 头会触发下载，等价于真实落盘文件的下载链路

        这样演示链路和未来接 PacketFactory 的真实落盘下载一致，
        只差"从磁盘读"那一步，到时候把这里换成 FileResponse 即可。
        """

        @RouteRegistry.register_route("/analysis/export", method="POST")
        async def api_analysis_export(payload: dict):
            run_id = (payload or {}).get("id") or f"run_{int(__import__('time').time())}"
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(run_id))
            return Response(
                content=body,
                media_type="application/json",
                headers={
                    "Content-Disposition": f'attachment; filename="{safe_id}.json"',
                },
            )
    async def cleanup(self):
        """Cleanup factory resources"""
        # Stop if running
        if self._status == ExecutionStatus.RUNNING:
            await self.stop()

        # Clear queues
        if self._state_queue:
            while not self._state_queue.empty():
                try:
                    self._state_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

        self._status = ExecutionStatus.STOPPED

    # ==================== Control Methods ====================

    async def start(self):
        """Start/resume factory execution"""
        if self._status == ExecutionStatus.RUNNING:
            return

        self._status = ExecutionStatus.RUNNING
        # Simple execution loop for testing
        asyncio.create_task(self._run_mock())

    async def _run_mock(self):
        """简单的模拟执行循环，每 1.5 秒一步"""
        while self._status == ExecutionStatus.RUNNING:
            await asyncio.sleep(1.5)  # 匹配前端的 1.5 秒间隔
            
            # 推送状态和指标
            await self._push_state_snapshot()
            await self._push_control_status()
            
            self._current_step += 1
            
            if self._current_step >= self._total_steps:
                await self.stop()
                break

    async def pause(self):
        """Pause factory execution"""
        if self._status == ExecutionStatus.RUNNING:
            self._status = ExecutionStatus.PAUSED

    async def reset(self):
        """Reset factory to initial state"""
        if self._status == ExecutionStatus.RUNNING:
            await self.stop()

        self._current_step = 0
        self._status = ExecutionStatus.IDLE

    async def stop(self):
        """Stop factory execution completely"""
        self._status = ExecutionStatus.STOPPED

    # ==================== Streaming Methods ====================

    async def get_state_events(self) -> list:
        """
        Get state events for SSE stream.
        Returns grid_state event type.
        """
        snapshot = await self.get_state_snapshot()
        return [("grid_state", snapshot)]

    async def get_metrics_events(self) -> list:
        """
        Get metrics events for SSE stream.
        Returns grid_metrics event type.
        """
        metrics = await self.get_metrics_snapshot()
        return [("grid_metrics", metrics)]

    async def get_control_events(self) -> list:
        """
        Get control events for SSE stream.
        Returns command_response event type.
        """
        status = await self.get_control_status()
        return [("command_response", status)]

    async def get_state_snapshot(self) -> dict:
        """
        Get current factory state for SSE stream.
        优先从队列消费数据（保证不跳步），队列空时实时查询兜底

        Returns:
            Dictionary containing factory state (matching frontend format)
        """
        # 策略：优先从队列取（FIFO顺序，不跳步）
        if self._state_queue and not self._state_queue.empty():
            try:
                cached_snapshot = self._state_queue.get_nowait()
                return cached_snapshot
            except asyncio.QueueEmpty:
                pass  # 队列空，fallback到实时查询

        # Fallback：队列空时实时构建快照
        return await self._build_state_snapshot_direct()

    async def _build_state_snapshot_direct(self) -> dict:
        """直接构建状态快照，供_push和fallback使用"""
        # 使用轨迹数据
        step = min(self._current_step, len(AGV_TRAJECTORIES) - 1)
        agv_frame = AGV_TRAJECTORIES[step]
        machine_states = FactorySimulator.generate_machine_states(step)
        jobs = FactorySimulator.generate_jobs(step)
        events = FactorySimulator.get_events(step)

        # 构建状态快照（匹配前端格式）
        snapshot = {
            "timestamp": f"T+{self._current_step}s",
            "env_timeline": str(self._current_step),
            "grid_state": {
                "positions_xy": [[agv["x"], agv["y"]] for agv in agv_frame["agvs"]],
                "is_active": [agv["active"] for agv in agv_frame["agvs"]],
            },
            "machines": machine_states,  # 已经是字典格式 {"M1": {...}, "M2": {...}}
            "jobs": jobs,  # 模块①：顶层 jobs 数组
            "active_transfers": [],
            "events": events,  # 事件列表
        }

        return snapshot

    async def get_metrics_snapshot(self) -> dict:
        """
        Get current metrics for SSE stream.

        Returns:
            Dictionary containing factory metrics (matching frontend format)
        """
        # 生成指标数据
        metrics = FactorySimulator.generate_metrics_data(self._current_step)
        return metrics

    async def get_control_status(self) -> dict:
        """
        Get current control status.

        Returns:
            Dictionary containing control status
        """
        return {
            "status": self._status.value,
            "current_step": self._current_step,
            "total_steps": self._total_steps,
            "config": {},  # Static proxy doesn't use config
        }

    async def _push_state_snapshot(self):
        """Push state snapshot to queue"""
        if self._state_queue:
            try:
                state = await self._build_state_snapshot_direct()
                if not self._state_queue.full():
                    self._state_queue.put_nowait(state)
                else:
                    # 队列满时，丢弃最旧数据
                    try:
                        self._state_queue.get_nowait()
                        self._state_queue.put_nowait(state)
                    except asyncio.QueueEmpty:
                        pass
            except Exception as e:
                # 静默失败，不影响主流程
                pass

    async def _push_control_status(self):
        """Push control status to queue"""
        if self._control_queue:
            try:
                status = await self.get_control_status()
                if not self._control_queue.full():
                    self._control_queue.put_nowait(status)
            except asyncio.QueueFull:
                # Drop oldest
                try:
                    self._control_queue.get_nowait()
                    self._control_queue.put_nowait(status)
                except asyncio.QueueEmpty:
                    pass

    # ==================== Utility Methods ====================

    @property
    def status(self) -> ExecutionStatus:
        """Get current execution status"""
        return self._status

    @property
    def current_step(self) -> int:
        """Get current step number"""
        return self._current_step

    def is_running(self) -> bool:
        """Check if factory is running"""
        return self._status == ExecutionStatus.RUNNING

    def is_paused(self) -> bool:
        """Check if factory is paused"""
        return self._status == ExecutionStatus.PAUSED

    def is_idle(self) -> bool:
        """Check if factory is idle"""
        return self._status == ExecutionStatus.IDLE
