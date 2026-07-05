# 异常事件注入器（Injector）调研与实现方案

> **背景**：PPT P7 声称有"独立 Injector 组件 + 掩码向量 + 四类异常事件"，但 `finalpro/SkyEngine` 代码里完全不存在。本文档调研现有架构，给出最小侵入的 Injector 实现方案。
>
> **目标读者**：自己（实现工作底稿）+ 师弟（后续维护参考）
> **状态**：方案已定，待实施

---

## Part A · 现状全景

### A.1 一个关键认知：项目里有两套"事件系统"

| 系统 | 文件 | 性质 | 状态 |
|---|---|---|---|
| **DiffEmitter** | `sky_executor/utils/diff.py` | **被动观测**：从帧 diff 派生事件，不改变环境状态 | ✅ 完整实现 |
| **event_registry** | `sky_executor/utils/registry/registry.py` | **主动注入预留**：`@register_event` 装饰器 + 工厂方法 | ❌ 空壳，全项目零使用 |

**结论**：PPT 声称的 Injector 属于第二类（主动注入），现状是只有占位代码，没有任何具体实现。`event/` 目录是空文件，`event_registry` 字典里没有任何注册条目。

但 DiffEmitter 这套被动观测系统已经非常完整（13 种事件 type + 规范化 envelope + JSONL 落档），**Injector 注入的状态变化会被 DiffEmitter 自然地观测到**——这是个免费红利，后面会详细说。

### A.2 组件生命周期现状

- `@register_component("factory")` 装饰器**唯一**使用者是 `GridFactoryEnv` 本身
- 组件实例化不是统一工厂创建，而是 `SimulationManager.create()` 和 `Coordinator.__init__()` 里**手动组装**
- `MetricsHub` 是最接近"挂载式组件"的参考实现：通过 `on_episode_start()` / `on_step_end()` 钩子挂在 `GridFactoryEnv` 上
- `callback/` 和 `factory_core/` 目录都是空文件

**结论**：没有正式的 callback/lifecycle 框架。Injector 不能"挂"在一个通用 lifecycle 上，需要**手动接入 `_run_loop`**。

### A.3 特征向量 / mask 现状

`FeatureExtractor`（`feature_extractor.py`）已实现，输出格式：
- pairwise: `[n_idle, n_task, 8]` + `env_stats [7]`
- raw: AGV `[n_agv, 10]` + Task `[n_task, 9]` + Machine `[n_machine, 8]` + Job `[n_job, 8]` + Map `[4]`

**已有的 mask**（`feature_extractor.py:741-752`）：
- `agv_mask` / `task_mask` / `pair_mask` 都是 **padding mask**（标识有效槽位 vs 填充槽位），不是"组件故障 mask"

**缺失的字段**：
- Machine 节点特征 `[8D]` 里有 `is_idle` 但**没有 `is_failed` / `is_available`**
- AGV 节点特征 `[10D]` 里有 `is_idle` / `has_task` 但**没有 `is_failed`**
- Machine 类（`structure.py:62-77`）**完全没有 status 字段**
- AGV 类（`structure.py:161-174`）**完全没有 status 字段**

**结论**：mask 基础设施（padding + bool mask）已经存在，可以复用。但 Machine/AGV 的故障状态字段完全缺失，需要新增。

### A.4 状态机现状

| 组件 | 现有状态 | 文件:行号 | 缺失状态 |
|---|---|---|---|
| Machine | WORKING / IDLE（在 `_build_frame` 里硬编码推导） | `sim_server.py:645,649` | **FAILED** |
| Operation | PENDING / PROCESSING / FINISHED | `structure.py:36` | **CANCELED** |
| Job | is_completed (bool property) | `structure.py:57-59` | **is_canceled** |
| AGV | active/idle（由 `current_task is None` 推导） | `assign_env.py:376` | **FAILED** / **DISABLED** |

**关键发现**：Machine/AGV 的 status **不是对象上的持久属性**，而是 `_build_frame` 里临时推导的字符串。这意味着 Injector 要让 status 持久化，需要改 `Machine` 类。

---

## Part B · 关键代码引用

### B.1 事件系统核心

| 文件:行号 | 作用 |
|---|---|
| `sky_executor/utils/diff.py:76-97` | `TYPE_REGISTRY` — 13 种合法事件 type |
| `sky_executor/utils/diff.py:158-176` | `make_envelope()` — 规范化事件信封 |
| `sky_executor/utils/diff.py:382-471` | `DiffEmitter` 类 — 调度器 |
| `sky_executor/utils/diff.py:422-438` | `DiffEmitter.diff()` — 每步帧间对比 |
| `sky_executor/utils/diff.py:452-471` | `DiffEmitter.emit_lifecycle()` — lifecycle 事件出口 |
| `sky_executor/utils/registry/registry.py:8-29` | `event_registry` + `register_event`（空壳） |
| `sky_executor/utils/registry/factory.py:25-29` | `get_event_class_by_id()`（空壳） |

### B.2 仿真主循环

`sim_server.py:804-919` `SimulationManager._run_loop()`，一次 step 的完整流程：

```
1. _pause_event.wait()                    # L808 等待暂停解除
2. _drain_pending_inserts()               # L816 排空插单队列（线程安全）
3. coordinator.decide(self.obs)           # L822 三层决策
4. env.step(actions)                      # L836 环境推进
   4a. pogema_env.job_step(job_actions)   # grid_factory_env.py:188
   4b. pogema_env.task_step(task_actions) # grid_factory_env.py:191
       - machine_process()                # assign_env.py:163-237
       - AGV 卸货 _deliver()              # assign_env.py:524-563
   4c. pogema_env.step(agent_actions)     # grid_factory_env.py:194 (move_agents)
   4d. metrics_hub.on_step_end()          # grid_factory_env.py:206
5. _build_frame()                         # sim_server.py:841
6. _diff.diff(frame, step)                # sim_server.py:849
7. _broadcast(state/metrics)              # sim_server.py:862-866
8. 终止判断                               # sim_server.py:871
9. _stop_event.wait(0.5)                  # sim_server.py:904
```

### B.3 insert_jobs 端点完整调用链（参考实现）

| 步骤 | 文件:行号 | 函数 |
|---|---|---|
| HTTP 入口 | `sim_server.py:957-970` | `insert_jobs(body)` |
| Manager 方法 | `sim_server.py:733-768` | `SimulationManager.insert_jobs(body)` |
| Body 解析 | `sim_server.py:202-220` | `_parse_insert_body(body)` |
| FJSP 校验 | `sim_server.py:223-275` | `_validate_fjsp_instance(jobs_data, extensions, num_machines)` |
| 转 Job 对象 | `sim_server.py:278-297` | `_fjsp_to_job(candidates_per_op, job_id, release, due)` |
| 入队（线程安全） | `sim_server.py:752-760` | `self._pending_inserts.append(job)` + `_insert_lock` |
| Drain（run_loop 内） | `sim_server.py:776-802` | `_drain_pending_inserts()` |

**线程安全模式**：
- `self._pending_inserts: collections.deque` + `self._insert_lock: threading.Lock()`
- HTTP handler 在锁内分配 job_id + 入队
- `_run_loop` 在锁内排空 queue + 更新 pogema_env

> **关键**：Injector 端点要**完全复刻**这个模式。

### B.4 组件注册机制

| 文件:行号 | 作用 |
|---|---|
| `registry.py:8` | `component_registry = {}` |
| `registry.py:12-18` | `@register_component(id)` 装饰器 |
| `scanner.py:39-46` | `scan_and_register_components()` pkgutil 自动扫描 |
| `scanner.py:48-98` | `selective_scan_and_register_components()` 带 include/exclude |
| `factory.py:11-15` | `create_component_by_id(id, *args)` 工厂方法 |
| `grid_factory_env.py:32` | `@register_component("factory")` 唯一使用者 |

### B.5 状态对象定义

| 文件:行号 | 类 | 关键字段 |
|---|---|---|
| `structure.py:19-46` | `Operation` | `status: str = "PENDING"`（PENDING/PROCESSING/FINISHED） |
| `structure.py:49-59` | `Job` | `completion_time: float = -1.0`, `is_completed` property |
| `structure.py:62-77` | `Machine` | `current_op`, `total_work_time`, `processed_ops_count` — **无 status 字段** |
| `structure.py:123-158` | `RoutingTask` | `task_id`, `job_id`, `op_id`, `source`, `destination` |
| `structure.py:161-174` | `AGV` | `id`, `pos`, `current_task`, `finished_tasks` — **无 status 字段** |

### B.6 Frame 构建

`sim_server.py:607-706` `_build_frame()`，每步构建一个完整 frame dict：

```python
{
    "timestamp": "T+{step}s",
    "env_timeline": int,
    "grid_state": {"positions_xy": [...], "is_active": [...]},
    "machines": [{"id", "location", "status", "current_op", "queue_length"}],
    "jobs": [{"job_id", "release", "due", "is_completed", "completion_time", "progress", "ops"}],
    "active_transfers": [{"task_id", "job_id", "op_id", "source", "destination"}],
}
```

> **注意**：Machine status 在 L645/L649 硬编码为 `"WORKING"` 或 `"IDLE"`，根据 `current_op is None` 推导。这是改造的关键点。

---

## Part C · Injector 设计建议

### C.1 目录结构

放在 `sky_executor/grid_factory/factory/Component/Injector/`，与 Assigner、JobSolver、RouteSolver 平级：

```
sky_executor/grid_factory/factory/Component/Injector/
    __init__.py
    injector.py                       # Injector 基类
    injectors/
        __init__.py
        machine_fail_injector.py      # Phase 1
        job_cancel_injector.py        # Phase 1
        agv_fail_injector.py          # Phase 2
        env_pause_injector.py         # Phase 2
        schedule_injector.py          # Phase 2: 时间表触发
        rate_injector.py              # Phase 2: 概率触发
```

**理由**：Injector 是有状态的环境组件，不是 diff 规则（不进 `utils/diff.py`），也不是被 Coordinator 调用的求解器（不进 `Component/Assigner/`）。它需要被 `_run_loop` 直接调用。

### C.2 核心抽象

```python
# sky_executor/grid_factory/factory/Component/Injector/injector.py

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
import numpy as np

if TYPE_CHECKING:
    from sky_executor.grid_factory.factory.grid_factory_env import GridFactoryEnv


class Injector(ABC):
    """异常事件注入器基类。
    
    挂在 SimulationManager 上，每步在 _drain_pending_inserts 之后、
    coordinator.decide 之前调用 apply()。
    
    设计要点：
    - 与 DiffEmitter 是互补关系：Injector 改变状态，DiffEmitter 观测变化
    - 外部 API 触发通过 inject() 入队，run_loop 内 drain 后调用 apply
    - mask 输出供 RL 训练消费
    """
    
    @abstractmethod
    def apply(self, env: "GridFactoryEnv", step: int) -> list[dict]:
        """每步调用，返回本次触发的事件列表。
        
        Returns:
            list of event dict, 每个 dict 包含:
            - event_type: "machine_fail" | "machine_recover" | "agv_fail" | ...
            - target_id: int (machine_id 或 agv_id 或 job_id)
            - duration: int (持续步数, 0=瞬时)
            - payload: dict (附加信息)
        """
        ...
    
    @abstractmethod
    def reset(self):
        """episode 边界清理。"""
        ...
    
    def inject(self, event: dict):
        """外部 API 触发：把事件加入待执行队列。
        线程安全，由 /sim/inject_event 端点调用。
        默认实现：append 到 self._pending（需在子类 __init__ 中创建）。"""
        with self._lock:
            self._pending.append(event)
    
    def get_masks(self) -> dict:
        """输出 mask 向量供 RL 训练消费。
        默认实现：返回空 dict，子类可覆盖。"""
        return {}
```

### C.3 四种事件类型的实现策略

#### (1) Machine Fail / Recover — **Phase 1**

**改造点**：

a. `structure.py:62-77` `Machine.__init__` 加：
```python
self.status: str = "IDLE"  # IDLE / WORKING / FAILED / RECOVERING
self.fail_step: int = -1   # 故障发生步数（用于恢复时机判断）
```

b. `assign_env.py:171` `machine_process()` 循环开头加：
```python
for m in machines:
    if m.status == "FAILED":
        continue  # 跳过故障机器
    # ... 原有加工逻辑
```

c. `sim_server.py:645,649` `_build_frame` 改为读取真实状态：
```python
# 原: status = "WORKING" if m.current_op else "IDLE"
status = m.status if m.status == "FAILED" else ("WORKING" if m.current_op else "IDLE")
```

**故障时 in-progress op 的处理策略**（推荐策略 A · 冻结）：

| 策略 | 描述 | 适用场景 |
|---|---|---|
| **A. 冻结**（推荐默认） | op 保持 PROCESSING，machine_process_time 暂停计数，recover 后断点续 | 短时故障（维护、换刀） |
| B. 丢弃 | op 标记 PENDING 回退，current_op 清空，需重新调度 | 不可恢复故障 |
| C. 作废 | op 标记 CANCELED，job 整体重新调度 | 灾难性故障 |

策略 A 实现**最简单**：`machine_process()` 跳过 FAILED 机器即可，op 状态自然冻结。

**mask 输出**：`machine_available_mask: np.ndarray[n_machine]`，1=正常 0=故障

#### (2) Job Cancel — **Phase 1**

**改造点**：

a. `structure.py:49-59` `Job.__init__` 加：
```python
self.is_canceled: bool = False
```

b. `structure.py:36` `Operation` status 枚举加 `CANCELED`

c. Cancel 时执行：
```python
def cancel_job(env, job_id):
    job = env.jobs[job_id]
    job.is_canceled = True
    for op in job.ops:
        if op.status == "PENDING":
            op.status = "CANCELED"
        # PROCESSING 的 op：让正在加工的 machine 停下
        elif op.status == "PROCESSING":
            for m in env.machines:
                if m.current_op is op:
                    m.current_op = None
                    m.status = "IDLE"
            op.status = "CANCELED"
    # 移除该 job 的所有 pending transfers
    env.pending_transfers = [t for t in env.pending_transfers if t.job_id != job_id]
```

**mask 输出**：`job_active_mask: np.ndarray[n_job]`，1=未取消 0=已取消

#### (3) AGV Fail / Recover — **Phase 2**

**特殊困难**：pogema 的 `move_agents(action)` 会移动所有 AGV，没有原生"禁用某个 AGV"机制。

**变通方案**（按优先级）：
1. 设 `self.grid.is_active[agent_idx] = False`（最干净，但可能影响 pogema 内部逻辑，需验证）
2. 给 disabled AGV 强制 WAIT action（但如果 pogema 有碰撞推挤可能仍会移动）
3. `move_agents` 之后回退 disabled AGV 位置（hack 但有效）

建议先用方案 1 验证，跑通就好。

**实现要点**：
- 在 `PogemaLifeLongWithAssign` 加 `self.disabled_agvs: set[int]`
- `task_step()`（`assign_env.py:302-389`）跳过 disabled AGV：不分配新任务、不卸货
- Fail 时正在执行的 transfer 保持挂起

#### (4) Env Pause — **Phase 2**

**现状**：`SimulationManager.pause()`（`sim_server.py:572-575`）已实现全局暂停，通过 `_pause_event` 机制。

**Injector 实现**：作为定时 pause（N 步后暂停 M 步再恢复），调用现有 `manager.pause()` / `manager.resume()`。**部分暂停**（暂停某些 machine/AGV）才是 Injector 的真正价值——但这与 machine_fail 重叠，所以 env_pause 可以作为 Injector 的"语义糖"，实际就是"全体 machine_fail + 全体 AGV_fail"的组合。

### C.4 触发模式对比

| 模式 | 优点 | 缺点 | 复杂度 | 优先级 |
|---|---|---|---|---|
| **trigger-based**（外部 API） | 最灵活，可控，与 insert_jobs 对称 | 需外部驱动 | **低**（镜像 insert_jobs） | **Phase 1** |
| schedule-based（时间表） | 可复现，适合训练 | 需预定义时间表 | 中 | Phase 2 |
| rate-based（概率随机） | RL 训练增强鲁棒性 | 不可复现，需种子管理 | 中 | Phase 2 |
| rule-based（条件触发） | 智能化，响应式 | 规则设计复杂 | 高 | Phase 3 |

### C.5 mask 向量设计

**输出格式**（对齐 FeatureExtractor 的 padding + bool mask 模式）：

```python
injector_masks = {
    "machine_available_mask": np.ndarray[n_machine],  # bool, True=正常
    "agv_available_mask":     np.ndarray[n_agv],      # bool, True=正常
    "job_active_mask":        np.ndarray[n_job],      # bool, True=未取消
    "step_active":            bool,                    # False=环境暂停
}
```

**与现有 mask 的关系**：
- 现有 `agv_mask` / `task_mask` / `pair_mask` 是 **padding mask**（有效 vs 填充）
- Injector 的 mask 是 **语义 mask**（有效槽位是否可用）
- 两者正交：`final_mask = padding_mask & semantic_mask`

**集成方式**（分阶段）：
- **Phase 1**：只在 `_build_frame()` 末尾追加 `frame["injector_masks"] = self.injector.get_masks()`，不改 FeatureExtractor
- **Phase 2**：在 `ComponentExtractor.encode_machines()` 加第 9 维 `is_available`（8D → 9D）
- **Phase 3**：mask 接入 RL 训练的 pair_features，故障组件权重置零

### C.6 配置 schema

**schedule-based 模式**（写入 episode config）：
```json
{
    "config": { ... },
    "fjsp_algorithm": "pso",
    "mapf_algorithm": "astar",
    "injector": {
        "mode": "schedule",
        "events": [
            {
                "type": "machine_fail",
                "target_id": 2,
                "trigger_step": 50,
                "duration": 10,
                "payload": {"reason": "maintenance"}
            },
            {
                "type": "job_cancel",
                "target_id": 3,
                "trigger_step": 30
            }
        ]
    }
}
```

**rate-based 模式**：
```json
{
    "injector": {
        "mode": "rate",
        "seed": 42,
        "rules": [
            {"type": "machine_fail", "rate": 0.01, "min_duration": 5, "max_duration": 20},
            {"type": "agv_fail", "rate": 0.005, "min_duration": 3, "max_duration": 15}
        ]
    }
}
```

### C.7 端点设计：`/sim/inject_event`

**完全镜像 `/sim/insert_jobs`**（`sim_server.py:957-970`）：

```python
@app.post("/sim/inject_event")
async def inject_event(body: dict = Body(...)):
    """触发异常事件注入。
    
    body = {
        "event_type": "machine_fail" | "agv_fail" | "env_pause" | "job_cancel",
        "target_id": int,                  # 必填
        "duration": int = 0,               # 可选, 0=瞬时, >0=持续 N 步
        "payload": dict = {}               # 可选
    }
    
    返回: {"status": "ok", "event_id": "...", "queued": true}
    
    错误:
    - {"status": "error", "message": "episode 未运行"}
    - {"status": "error", "message": "target_id 越界"}
    - {"status": "error", "message": "未知 event_type"}
    """
    try:
        return manager.inject_event(body)
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except RuntimeError as e:
        return {"status": "error", "message": str(e)}
```

**SimulationManager.inject_event** 实现：

```python
def inject_event(self, body: dict) -> dict:
    if self.env is None or not self.running:
        raise RuntimeError("episode 未运行，无法注入事件")
    
    event = self._validate_inject_body(body)  # 校验 event_type/target_id
    
    with self._inject_lock:
        self._pending_injects.append(event)
    
    return {"status": "ok", "event_id": event["id"], "queued": True}
```

线程安全模式完全复刻 `_pending_inserts`：deque + Lock，在 `_run_loop` 开头 drain。

### C.8 集成点

Injector 在 `_run_loop` 中的介入位置：

```
当前流程:
  _drain_pending_inserts()       # L816
  coordinator.decide(self.obs)   # L822
  env.step(actions)              # L836

新增后:
  _drain_pending_inserts()       # L816 (不变)
  _drain_pending_injects()       # <<<< 新增：排空注入队列
  injector.apply(env, step)      # <<<< 新增：执行 schedule/rate 触发
  coordinator.decide(self.obs)   # L822 (看到更新后的故障状态)
  env.step(actions)              # L836
```

**关键决策**：Injector 在 `coordinator.decide` **之前**执行。这样 Coordinator 看到的是已更新的故障状态，能在调度时避开故障机器/AGV。

### C.9 diff.py TYPE_REGISTRY 扩展

`diff.py:76-97` 新增事件 type：

```python
TYPE_REGISTRY = {
    # ... 现有 13 种 ...
    
    # injector 异常事件（Phase 1 起加入）
    "machine_failed":      (CATEGORY_MACHINE_OP, LEVEL_ERROR),
    "machine_recovered":   (CATEGORY_MACHINE_OP, LEVEL_SUCCESS),
    "agv_failed":          (CATEGORY_AGV,        LEVEL_ERROR),
    "agv_recovered":       (CATEGORY_AGV,        LEVEL_SUCCESS),
    "job_canceled":        (CATEGORY_JOB,        LEVEL_WARNING),
    "env_paused":          (CATEGORY_LIFECYCLE,  LEVEL_WARNING),
    "env_resumed":         (CATEGORY_LIFECYCLE,  LEVEL_INFO),
}
```

同时在 `DiffEmitter` 增加 `emit_injection` 方法（镜像 `emit_lifecycle` L452-471），让 Injector 主动发出事件信封。

### C.10 测试策略

参考 `test/online_server_test/skytest_server.py`：

1. **单元测试**（`test/injector_test/`）：
   - 创建最小环境，手动注入 machine_fail，验证 Machine.status 变化
   - 验证 fail 期间 machine_process 跳过
   - 验证 recover 后恢复加工
   - 验证 mask 向量正确性

2. **集成测试**（扩展 `skytest_server.py`）：
   - 新增 Phase: `test_inject_event` — play → POST /sim/inject_event → 验证 SSE 事件流
   - 验证线程安全：快速连续注入多个事件

3. **端到端测试**（扩展 `test_lifecycle.py`）：
   - 配置 schedule-based injector，跑完整 episode
   - 验证 Job 完成时间受影响但最终全部完成
   - 验证 events.jsonl 包含注入事件

---

## Part D · 实现路线图

### Phase 1 · 最小可用（2-3 天）

**目标**：trigger-based 模式 + machine_fail + job_cancel + `/sim/inject_event` 端点 + mask 输出。**这个 Phase 跑通即可支撑 PPT 演示。**

| # | 任务 | 文件 | 详情 |
|---|---|---|---|
| 1 | Machine 加 status 字段 | `structure.py:62-77` | `self.status: str = "IDLE"` |
| 2 | machine_process 跳过 FAILED | `assign_env.py:171` | 循环开头加 `if m.status == "FAILED": continue` |
| 3 | Operation 加 CANCELED | `structure.py:36` | status 注释加 CANCELED |
| 4 | Job 加 is_canceled | `structure.py:49-59` | `self.is_canceled: bool = False` |
| 5 | _build_frame 输出真实 status | `sim_server.py:645,649` | 从 `m.status` 读取替代硬编码 |
| 6 | _build_frame 输出 mask | `sim_server.py:607-706` | 末尾追加 `frame["injector_masks"]` |
| 7 | Injector 基类 | 新建 `Component/Injector/injector.py` | 见 C.2 |
| 8 | MachineFailInjector | 新建 `Component/Injector/injectors/machine_fail_injector.py` | |
| 9 | JobCancelInjector | 新建 `Component/Injector/injectors/job_cancel_injector.py` | |
| 10 | TYPE_REGISTRY 扩展 | `diff.py:76-97` | 新增 7 种 type |
| 11 | DiffEmitter.emit_injection | `diff.py` | 镜像 emit_lifecycle |
| 12 | `/sim/inject_event` 端点 | `sim_server.py` | 镜像 insert_jobs |
| 13 | `_drain_pending_injects()` | `sim_server.py` | 镜像 `_drain_pending_inserts` |
| 14 | _run_loop 集成 | `sim_server.py:816` 后 | 加 drain + apply 两步 |
| 15 | 冒烟测试 | `test/injector_test/smoke.py` | 验证端到端跑通 |

### Phase 2 · 完整版（3-4 天）

| 任务 | 详情 |
|---|---|
| AGV Fail / Recover | disabled_agvs 集合，task_step 跳过 |
| Env Pause（定时） | 扩展 SimulationManager.pause 支持定时恢复 |
| schedule-based 触发 | 从 config 解析事件时间表 |
| rate-based 触发 | numpy random 按概率触发 |
| mask 接入 FeatureExtractor | encode_machines 扩展 is_available 维度（8D→9D） |
| 配置 schema | episode config 支持 injector 字段 |
| 回归测试 | 验证现有功能不受影响 |

### Phase 3 · 可选增强（2-3 天）

| 任务 | 详情 |
|---|---|
| mask 接入 RL 训练 | pair_features 中故障组件权重置零 |
| rule-based 触发 | 条件满足时自动注入（如 machine 利用率 > 90% 时 fail） |
| 事件可视化 | 前端事件流新增 injector 事件图标 |
| JSONL 日志分析 | events.jsonl 中 injector 事件被 AnalysisService 消费 |
| event_registry 利用 | 把每种异常事件注册为 `@register_event` 类 |

---

## Part E · 风险与注意事项

### E.1 线程安全（已验证的模式，低风险）

`/sim/inject_event` HTTP handler 在 uvicorn event loop，`_run_loop` 在 daemon thread。

**解决方案**：完全复刻 `_pending_inserts` 的 deque + Lock 模式。insert_jobs 已经稳定运行，复制即可。

### E.2 状态一致性（中等风险）

**Machine Fail 后 in-progress job 怎么办**：见 C.3(1) 策略表，默认采用**冻结策略**。策略 A 实现最简单（machine_process 跳过），且符合实际制造业（短时维护后断点续）。

**Job Cancel 时正在搬运的 AGV**：建议物料作废，AGV 变空闲。需要清理 `pending_transfers` / `buffered_tasks` / `active_transfers` 中该 job 的所有任务。

### E.3 与现有 DiffEmitter 的关系（重要 · 互补）

**关键认知**：Injector 与 DiffEmitter 是**互补关系**，不是替代。

- DiffEmitter 是**被动观测**：从帧 diff 派生事件
- Injector 是**主动注入**：改变环境状态
- Injector 注入后 → Machine.status 变 FAILED → 下一帧 _build_frame 输出 status=FAILED → DiffEmitter 的 `machine_op_rule` 检测到 WORKING→FAILED 跃迁 → 自动发出对应事件

**这意味着**：Injector 不需要自己发事件信封，DiffEmitter 会自然处理。但为了让事件类型更精确（区分"机器空闲"和"机器故障"），需要更新 `machine_op_rule`（`diff.py:186-211`）增加 status 字段判断。

Phase 1 可以先用 `emit_injection` 手动发，不依赖 diff 规则更新。

### E.4 event_registry 的利用

`event_registry`（`registry.py:9`）当前为空，是预留的设计意图。

- Phase 1 不用，直接在 Injector 内部 if-else 分发
- Phase 2 / 3 把每种异常事件注册为 `@register_event("machine_fail")` 类，通过 `get_event_class_by_id` 动态创建

这与 `@register_component` 模式一致，符合项目架构意图。

### E.5 pogema 底层限制（AGV Fail 的特殊风险）

pogema 的 `move_agents(action)` 会移动所有 AGV，**没有原生的"禁用某个 AGV"机制**。

变通方案见 C.3(3)，建议先用 `grid.is_active[agent_idx] = False` 验证，跑通即可。如果引发 pogema 内部异常，回退到"move 后强制位置回退"的 hack。

**这就是为什么 AGV Fail 在 Phase 2 而不是 Phase 1**。

### E.6 FeatureExtractor 向后兼容性

如果给 `encode_machines` 的输出从 8D 扩展到 9D（加 `is_available`），所有下游模型（plain_assigner_model、expert_assigner_model、tower_assinger_model）的输入维度都要同步修改。

**建议**：
- Phase 1 不改 FeatureExtractor，只在 frame 里输出 mask dict
- Phase 2 加一个 `encode_machines_with_health()` 方法返回 9D，原方法保持不变
- 通过配置开关选择，保证旧模型不破坏

---

## 附录 · 与 PPT 声明的对照

| PPT 声明 | 本方案对应 | Phase |
|---|---|---|
| "独立的 Injector 组件" | C.1 目录结构 + C.2 基类 | Phase 1 |
| "生成掩码向量" | C.5 mask 向量设计 | Phase 1（frame 输出） / Phase 2（FeatureExtractor） |
| "machine fail" | C.3 (1) | Phase 1 |
| "AGV fail" | C.3 (3) | Phase 2（pogema 限制） |
| "env pause" | C.3 (4) | Phase 2 |
| "job cancel" | C.3 (2) | Phase 1 |
| "掩码向量作为额外特征向量接入 RL" | C.5 集成方式第 3 步 | Phase 3 |

**最小可演示集**（Phase 1）：machine_fail + job_cancel + `/sim/inject_event` 端点 + frame 输出 mask。这套已经足够支撑 PPT 上的核心声明。
