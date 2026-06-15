# FJSP `machine_options_with_time` 字段贯通修复

## 一、现象

通过 `DockerProxy` 启动 grid_factory 仿真后，前端点 Play 看不到任何动画推进，后端日志反复打印：

```
[Play] 收到请求, 代理: DockerProxy
[Play] initialized=True
```

FJSP 容器报 500：

```
File "/app/pso_solver.py", line 227, in _init_machine_extreme_global
    x[pos] = min_time_machs[np.random.randint(0, len(min_time_machs))]
ValueError: high <= 0
```

## 二、根因：`machine_options_with_time` 字段在链路上被丢

### 2.1 数据集本身是标准 FJSP（多机器多时间）

`dataset/fjsp-instances/kacem/k1.json` 等数据集中，每个 op 在不同机器上有不同 processing time：

```json
"jobs": [
  [
    [ {"machine": 0, "processing": 2},
      {"machine": 1, "processing": 5},
      {"machine": 2, "processing": 4},
      {"machine": 3, "processing": 1},
      {"machine": 4, "processing": 2} ]
  ]
]
```

`load_fjsp_json` / `load_fjsp_benchmark` 也都正确还原为 `[(processing_time, machine_id), ...]` 的候选列表。

### 2.2 罪魁：`convert_to_grid_factory` 把多候选压扁成单机器

`dataset/helper.py:453-462`（修改前）：

```python
for op_idx, alternatives in enumerate(job):
    best = min(alternatives, key=lambda a: a[0])  # 只取加工时间最短那台
    operations.append({
        "machine_id": best[1],     # ← 单 machine_id
        "duration": best[0],       # ← 单 duration
        ...
    })
```

这一步**故意丢弃**了每个 op 的所有候选机器与各自时间，只保留"最快机器"。从此 FJSP 退化成普通 JSP（每 op 固定机器）。

### 2.3 下游链路：`machine_options_with_time` 永远是空/None

容器路径（DockerProxy → sim_server.py → sky_executor）：

| 环节 | 文件 | 行为 |
|------|------|------|
| helper 输出 operations | `dataset/helper.py:453-462` | 只输出 `machine_id + duration`（单机器） |
| sim_server.py 解析 | `finalpro/SkyEngine/sim_server.py:123-131` | 封装为 `([machine_id], duration)`，`strategy="custom"` |
| sky_executor job.py custom 分支 | `sky_executor/.../Utils/job.py:56-67` | 只填 `Operation.machine_options + proc_time`，**`machine_options_with_time` 保持 None** |
| http_job_solver 序列化 | `sky_executor/.../http_solver/http_job_solver.py:60-65` | `if op.machine_options_with_time:` 为假 → 发送**空候选**给 FJSP 微服务 |
| FJSP 反序列化 | `pso_solver_server.py:deserialize_obs` | 每个 op `proc_times = {}` |
| PSO `_obs_to_data` | `online_pso_solver.py:80-92` | 每个 op 的矩阵行全是 `"-"` |
| PSO `_init_machine_extreme_global` | `pso_solver.py:191-227` | `min_time_machs` 为空 → `np.random.randint(0, 0)` → **`ValueError: high <= 0`** |

异常在 `_run_loop` 的 `coordinator.decide()` 中冒出 → 仿真线程死掉 → SSE 队列永远不产事件 → 前端"没反应"。

进程内路径（GridFactoryProxy → joint_sim）虽然不会触发这个 PSO 崩溃（因为 joint_sim 没有 http_solver，直接用进程内 solver），但同样存在 `machine_options_with_time` 字段缺失问题，无法支持标准 FJSP。

## 三、修复方案：让 `machine_options_with_time` 贯通（两套都修）

### 3.1 数据集 → 配置 JSON：保留全部候选

改 `dataset/helper.py:453-462`（grouppro 和 finalpro 两份都改），输出新字段：

```python
for op_idx, alternatives in enumerate(job):
    # 保留 op 的全部候选机器与各自处理时间（标准 FJSP）。
    # 字段格式: List[[machine_id, processing_time]]
    # 注意数据集内部存的是 (processing_time, machine_id) 元组，
    # 输出时必须互换顺序，匹配 Operation.machine_options_with_time
    # 的 List[Tuple[machine_id, proc_time]] 约定。
    operations.append({
        "machine_options_with_time": [
            [mid, pt] for pt, mid in alternatives
        ],
        "name": _OP_NAMES[op_idx % len(_OP_NAMES)],
    })
```

### 3.2 配置 JSON → Operation：按字段自动切 strategy

两条路径都需要改解析逻辑。

**容器路径** —— `finalpro/SkyEngine/sim_server.py`：

```python
custom_jobs = []
all_durations = []
use_custom_time = False  # 任一 op 含 machine_options_with_time 即为 True

for job in job_list:
    job_ops = []
    for op in job.get("operations", []):
        if "machine_options_with_time" in op:
            use_custom_time = True
            mowt = op["machine_options_with_time"]
            job_ops.append(([(m[0], m[1]) for m in mowt], mowt[0][1]))
            all_durations.extend(m[1] for m in mowt)
        else:
            # DEPRECATED: 旧格式 {machine_id, duration}
            machine_id = op.get("machine_id", 0)
            duration = op.get("duration", 1)
            machine_options = [machine_id] if isinstance(machine_id, int) else machine_id
            job_ops.append((machine_options, duration))
            all_durations.append(duration)
    custom_jobs.append(job_ops)

job_cfg = JobConfig(
    ...,
    strategy="custom_time" if use_custom_time else "custom",
    custom_jobs=custom_jobs,
)
```

**进程内路径** —— `application/backend/joint_sim/io/use_io.py`：同上逻辑（已同步）。

### 3.3 扩展 grouppro 的 joint_sim 结构

grouppro 的 `joint_sim.utils.structure.Operation` 原本**没有** `machine_options_with_time` 字段，`joint_sim.utils.job.generate_jobs` 也**没有** `custom_time` strategy。两处都需要新增（与 sky_executor 对齐）：

**`application/backend/joint_sim/utils/structure.py`** —— Operation 加字段：

```python
@dataclass
class Operation:
    job_id: int
    op_id: int
    machine_options: List[int]
    proc_time: float
    # 多机器候选 + 各自处理时间（标准 FJSP）。
    # 优先字段：generate_jobs(strategy="custom_time") 会填充此项。
    # 格式: List[Tuple[machine_id, proc_time]]
    machine_options_with_time: Optional[List[Tuple[int, int]]] = None
    release: float = 0.0
    ...
```

**`application/backend/joint_sim/utils/job.py`** —— generate_jobs 加分支：

```python
elif job_config.strategy == "custom_time":
    # 标准 FJSP：每 op 多机器候选 + 各自处理时间。
    # op_info[0] = List[Tuple[machine_id, proc_time]]
    for j, job_info in enumerate(job_config.custom_jobs):
        ops: List[Operation] = []
        for op_id, op_info in enumerate(job_info):
            mowt = op_info[0]
            op = Operation(
                job_id=j,
                op_id=op_id,
                machine_options=[m[0] for m in mowt],
                proc_time=op_info[1],
                machine_options_with_time=list(mowt),
            )
            ops.append(op)
        jobs.append(Job(job_id=j, ops=ops))
```

### 3.4 不动的部分

| 文件 | 原因 |
|------|------|
| `sky_executor/.../Utils/structure.py` | 已有 `machine_options_with_time` 字段 |
| `sky_executor/.../Utils/job.py` | 已有 `custom_time` strategy |
| `sky_executor/.../http_solver/http_job_solver.py` | 已正确读 `Operation.machine_options_with_time` |
| `joint_sim/component/JobSolver/*` | 进程内 solver 直接读 `op.machine_options + proc_time`，不读 per-machine 时间；字段加上后不会崩，只是不利用 per-machine 时间差（优化质量问题，非 bug） |

## 四、改动清单

| # | 文件 | 改动 |
|---|------|------|
| 1 | `dataset/helper.py`（grouppro） | operations 输出 `machine_options_with_time`，保留全部候选 |
| 2 | `dataset/helper.py`（finalpro） | 同上 |
| 3 | `application/backend/joint_sim/utils/structure.py` | Operation 加 `machine_options_with_time` 字段 |
| 4 | `application/backend/joint_sim/utils/job.py` | generate_jobs 加 `custom_time` strategy 分支 |
| 5 | `application/backend/joint_sim/io/use_io.py` | 优先解析新字段，按字段切 `custom_time` / `custom`，旧字段标 DEPRECATED |
| 6 | `finalpro/SkyEngine/sim_server.py` | 同 use_io.py 的解析改动 |

## 五、验证

1. **grid_factory（数据集驱动）**：选 `kacem/k1` + 任一 MAPF 地图，点生成配置 → 配置 JSON 里每个 op 应有 `machine_options_with_time` 数组（≥1 个候选）。
2. **启动仿真**：fjsp 容器日志不应再出现 `ValueError: high <= 0`。
3. **前端动画**：SSE 应持续推送 frame，AGV 移动、机器加工状态变化。
4. **回归（旧格式兼容）**：加载 `northeastFactoryMap.json`（用 `machine_id + duration`），应仍能跑——走 `custom` strategy fallback。
5. **多机器候选正确性抽查**：从 fjsp 容器日志或断点看 obs 序列化，某个有 5 个候选的 op 应输出 `[(pt, mid), ...]` 5 元素列表，而不是 `[]` 或单元素。
6. **engine 镜像重建**：sim_server.py 运行在容器内，改完 finalpro 的 sim_server.py 后必须 `docker compose build engine` 才能生效。

## 六、风险与注意

- **旧配置兼容**：`northeastFactoryMap.json`、`public/grid_factory_example.json` 等历史配置没有 `machine_options_with_time` 字段，必须保留 fallback，否则历史测试地图会直接挂掉。
- **字段顺序约定**：`machine_options_with_time` 在 JSON 里统一为 `[machine_id, processing_time]`（machine_id 在前），与 `Operation.machine_options_with_time: List[Tuple[int, int]]` 一致。helper.py 输出时要从数据集的 `(processing_time, machine_id)` 元组互换过来，**不要保持原顺序**——否则 machine_id 会被当成时间，duration 会被当成机器 id，导致 indexing 错乱。
- **strategy 自动切换**：靠"任意 op 包含 `machine_options_with_time`"来判定走 `custom_time`。**同一份配置内 op 格式必须统一**，混合（部分新字段、部分旧字段）不在支持范围——会被判为 `custom_time`，但旧字段的 op 走 fallback 后 `op_info[0]` 是 `machine_options` 列表（不是 `(mid, pt)` 元组），会被 custom_time 分支错误解析。
- **进程内 solver 不利用 per-machine 时间**：joint_sim 的进程内 solver（priority/best/PDR 等）只读 `op.machine_options + op.proc_time`，不读 `machine_options_with_time`。修复后不会崩，但调度质量不会因 per-machine 时间差而提升。如果要让进程内 solver 也支持，需要单独改造各 solver（不在本次范围）。
- **engine 镜像重建**：sim_server.py 运行在容器内，改完 finalpro 的 sim_server.py 后必须 `docker compose build engine` 才能生效，否则容器内还是旧版。
