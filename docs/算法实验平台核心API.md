# 算法实验平台核心 API

> 文档状态：当前 Python API 与插件契约（2026-09-20）  
> 协议版本：`PLATFORM_PROTOCOL_VERSION = "1.0"`  
> 验证边界：按当前源码更新；本次未运行测试、构建或算法效果验收。

## 1. 文档范围

本文对应 `experiment/algorithm_platform` 的公开 Python API，并说明已接入的 DFJSP-T 插件。完整链路为：

```text
ExperimentSpec
  → ExperimentCompiler / TuningOrchestrator
  → ExecutionPlan 或动态参数候选
  → LocalOrchestrator + RuntimeDriver
  → RunResult / EventLog / ArtifactManifest
  → TrialResult / ComparisonReport / ReplayTrace
```

与旧文档不同，当前代码已实现 `trainable` Runtime、`search_optimizer` 调优编排、统一比较、记录/确定性/分支回放、实验与 Execution 持久化、HTTP 控制面和前端工作台。

## 2. 包级公开入口

业务代码应从包级入口导入，而不是依赖内部模块：

```python
from experiment.algorithm_platform import (
    AlgorithmInterface,
    AlgorithmManifest,
    ArtifactKind,
    Budget,
    ComparisonEngine,
    ExperimentCompiler,
    ExperimentRepository,
    ExecutionRepository,
    LocalArtifactStore,
    LocalOrchestrator,
    LocalReplayOrchestrator,
    ObjectiveEngine,
    PLATFORM_PROTOCOL_VERSION,
    PlatformRegistry,
    TuningOrchestrator,
    default_registry,
    experiment_spec_from_mapping,
    load_experiment_spec,
)
```

| 模块 | 当前职责 |
|---|---|
| `models.py` | 深冻结值对象、枚举、Manifest、Experiment/Trial/Run、结果与事件 |
| `protocols.py` | Online、Batch、Iterative、Trainable、SearchOptimizer、领域与 Runtime 协议 |
| `registry.py` | 精确版本、显式 factory 的算法/领域/Runtime 注册表 |
| `schema.py` | Manifest 参数 schema 子集校验 |
| `config.py` | 严格 JSON/YAML 双向转换，不做字符串到数字/布尔的静默转换 |
| `compiler.py` | 能力、schema、目标、预算、数据分区校验及稳定 Trial/Run 编译 |
| `runtimes.py` | Online、Batch、Iterative、Trainable 四类默认 Runtime |
| `optimizers.py` | 网格、随机、遗传三种内置 SearchOptimizer |
| `tuning.py` | ask/tell 调优、训练产物冻结、候选评价和封闭 Benchmark |
| `objectives.py` | 正式 Metric 评价、约束、聚合和目标比较 |
| `comparison.py` | 平衡设计检查、描述统计、配对比较、排名与 Pareto 层 |
| `events.py` | 只追加 JSONL 事件和状态 hash |
| `artifacts.py` | SHA-256 内容寻址对象、发布器和不可变 Manifest |
| `experiments.py` | 不可变 ExperimentRepository |
| `executions.py` | Execution 生命周期持久化 |
| `replay.py` | Trace、诊断、决策提取、记录播放和 Diff |
| `replay_orchestrator.py` | 确定性复现和快照分支的实际执行 |
| `orchestrator.py` | 单进程顺序执行、隔离 Run 工作目录和结果发布 |

## 3. 核心值对象

### 3.1 Experiment、Execution、Trial、Run

- `ExperimentSpec`：实验 ID、名称、领域、算法、场景、目标、用途、预算、重复次数、base seed、可选调优定义。
- `ExecutionRecord`：一次启动的持久状态，包括 `draft/compiled/running/cancel_requested/succeeded/failed/cancelled`、用途、时间、计划摘要、结果 Manifest 和失败信息。
- `TrialSpec`：算法精确版本、一组参数、目标和有效预算。
- `RunSpec`：Trial 在一个场景、一个 repetition 和一组分离 seed 上的最小执行单元。
- `ExecutionPlan`：带 `plan_digest` 的不可变 Trial/Run 矩阵。
- `RunResult`：状态、正式 Metric、目标值、约束、可行性、制品、事件日志和失败信息。

值对象会深冻结内部 Mapping、list 和 set，防止编译摘要与运行内容失配。

调优内部的训练、候选评价和 Benchmark 使用独立 workspace execution ID 保存事件与制品，但不创建独立 `ExecutionRecord`。HTTP Run 视图因此同时给出根生命周期拥有者 `owner_execution_id`、可用于回放路由的 `route_execution_id` 和实际制品工作区 `workspace_execution_id`。Replay 则相反：它会创建真正的 `purpose=replay` 子 `ExecutionRecord`，并以源 Experiment 作为不可变研究定义。

### 3.2 Budget

`Budget` 字段包括：

```text
wall_time_seconds
decision_time_seconds
max_iterations
max_evaluations
max_steps
cpu_cores
gpu_count
memory_bytes
```

当前默认 Runtime 对工作量预算的支持：

| 接口 | 工作量限制 |
|---|---|
| online | `max_steps`，并检查 `wall_time_seconds`、`decision_time_seconds` |
| iterative | `max_iterations`、`max_evaluations`，并检查 `wall_time_seconds` |
| trainable | 平台不主动计时或计数；预算通过 `RunContext` 交给算法，`fit` 抛出 `TimeoutError` 时记录为超时 |
| batch | 不接受 max step/iteration/evaluation 限制，也不主动中断 wall time |

本地编排器不执行 CPU/GPU/内存硬隔离。只要有效 Run 预算声明 `cpu_cores`、`gpu_count` 或 `memory_bytes`，执行前就会拒绝该 Run。`wall_time_seconds` 只有 Online、Iterative 和调优主循环在平台侧主动检查；Batch 与 Trainable 不由平台计时中断。

### 3.3 ObjectiveSpec

`ObjectiveSpec` 支持：

- `single`、`weighted_sum`、`lexicographic`、`pareto`；
- 每个组件独立的 `minimize/maximize`、权重与聚合；
- `lt/le/eq/ge/gt` 约束、阈值、容差和聚合；
- `feasibility_first`。

`ObjectiveEngine` 只读取领域给出的正式 Metric，不读取 reward、loss 或算法内部 fitness。

## 4. Manifest 与注册表

### 4.1 AlgorithmManifest

关键字段：

| 字段 | 含义 |
|---|---|
| `algorithm_id`、`version` | 稳定标识与精确版本 |
| `protocol_version` | 必须等于平台协议版本 |
| `interfaces` | 算法实现的接口集合 |
| `entrypoints` | 来源展示信息，不用于从实验配置动态导入 |
| `supported_domains` | 支持的领域 ID；空集合表示不限制 |
| `parameter_schema` | 参数校验和工作台编辑依据 |
| `input_artifact_kinds` | 允许加载的不可变制品类型 |
| `minimum_input_artifacts` | 按接口声明最少输入制品数；未声明的接口默认为 0 |
| `output_artifact_kinds` | 允许发布的制品类型 |
| `runtime` | 可选精确 RuntimePlugin 引用 |

Batch 和 Iterative Manifest 必须声明 `SOLUTION`。Trainable 在训练/调优时必须声明 `MODEL`、`PARAMETERS` 或 `CHECKPOINT` 至少一种；冻结评价时必须提供允许的输入制品。

### 4.2 DomainManifest

领域能力包括：

- `online_execution`；
- `batch_evaluation`；
- `training_data`；
- `snapshot_restore`；
- `deterministic_replay`。

领域还可声明问题、观测、动作、方案 schema、Metric schema、参数 schema 和来源元数据。

### 4.3 PlatformRegistry

注册表只接受可信代码显式传入的 factory：

```python
registry.register_algorithm(manifest, {
    AlgorithmInterface.ONLINE: lambda ref: MyPolicy(ref.parameters),
})
registry.register_domain(domain_manifest, lambda ref: MyDomain(ref))
registry.register_runtime(runtime_manifest, lambda recorder: MyRuntime(recorder))
```

Manifest 接口、entrypoint 和 factory 集合必须完全一致；`minimum_input_artifacts` 只能引用该 Manifest 已声明的接口且数量不能为负；重复 ID/版本会被拒绝。创建对象时按 ID、版本和接口精确解析。

## 5. 算法协议

### 5.1 OnlineAlgorithm

```python
initialize(context)
decide(request) -> DecisionResponse
observe(feedback)
finalize() -> tuple[ArtifactRef, ...]
```

领域负责合法性与真实状态推进。Runtime 记录观测、请求、响应、校验、实际动作、状态变化、Metric 和快照。

### 5.2 BatchSolver

```python
solve(problem, context) -> Candidate[Solution]
finalize() -> tuple[ArtifactRef, ...]
```

领域校验完整方案并计算 Metric；合法方案由 Runtime 发布为 `SOLUTION`。

### 5.3 IterativeSolver

```python
initialize(problem, context)
propose(count) -> tuple[Candidate, ...]
observe(evaluated_candidates)
best() -> Candidate | None
should_stop() -> bool
finalize() -> tuple[ArtifactRef, ...]
```

同一 Run 的 `candidate_id` 必须唯一。正式目标值、约束值和可行性由领域与 `ObjectiveEngine` 写回候选。

### 5.4 TrainableAlgorithm

```python
fit(training_data, validation_data, context) -> tuple[ArtifactRef, ...]
load_artifacts(artifacts, context)
evaluate(evaluation_data, context) -> MetricSet
```

`purpose=train` 调用 `fit` 后再评价；其他用途加载冻结产物后评价。领域拥有训练、验证和评价数据装配权，算法不能把封闭 Benchmark 偷换成训练数据。

### 5.5 SearchOptimizer

```python
initialize(search_space, objective, budget, seed, artifact_publisher)
ask(count) -> tuple[Candidate[ParameterSet], ...]
tell(results: Sequence[TrialResult])
should_stop() -> bool
snapshot() -> ArtifactRef
```

参数候选对应 Trial，不对应领域内部调度解。优化器 snapshot 必须是通过本次调优发布器生成的 `CHECKPOINT`。

## 6. 领域协议

平台按能力拆分领域边界：

- `OnlineDomainAdapter.load_problem/create_session`；
- `OnlineDomainSession.reset/observe/validate/step/metrics/close`；
- `ReplayableDomainSession.snapshot/restore`；
- `BatchDomainAdapter.load_problem/validate_solution/evaluate_solution`；
- `TrainingDomainAdapter.load_training_data/load_validation_data/load_evaluation_data`。

领域产生的 `DomainEvent` 不带平台序号。`EventRecorder` 统一补充 `sequence`、`execution_id`、`run_id`、wall time、仿真时间和可选状态 hash。

Observation、Action 和 Solution 进入正式事件或 JSON 制品前必须能明确转换为 JSON；平台不使用 `repr` 静默降级。

## 7. 编译器

公开实验配置当前只有 `schema_version=1`。显式给出其他值会被拒绝；省略时按版本 1 读取。解析器对 Experiment 顶层字段使用白名单，未知顶层字段直接报错，不会静默写入 `metadata`；算法参数和领域参数再由各自 Manifest schema 校验。

`ExperimentCompiler.validate()` 当前校验：

- 实验、场景、算法和目标必填项；
- 精确算法/领域版本是否已注册；
- 算法接口与领域 capability 是否匹配；
- Manifest 参数 schema；
- 每种算法接口与调优器的最少输入制品数；
- Objective 引用的 Metric 是否由领域声明；
- 预算值与用途；
- Trainable 输入/输出制品约束；
- 调优器接口、搜索空间与目标算法参数 schema；
- 训练、调优、Benchmark 的 ID 和摘要隔离；
- 候选选择/训练场景不得使用 `split=benchmark`，封闭 Benchmark 必须声明 `metadata.split=benchmark`。

标准实验由 `compile()` 产生稳定 Trial/Run ID 和 `plan_digest`。调优实验用 `digest()` 得到稳定摘要，再交给 `TuningOrchestrator` 动态扩展。

## 8. 调优与比较

`TuningOrchestrator.execute()` 的顺序为：

```text
optimizer.ask
  → 如目标为 trainable：在 training_scenarios 训练并冻结产物
  → 以 purpose=validate 在 scenarios 评价候选
  → 聚合 TrialResult
  → optimizer.tell
  → 选择 incumbent
  → 在 benchmark_scenarios 封闭评价
  → 发布 optimizer checkpoint、事件、报告和 Manifest
```

Run 失败会聚合为带 `status=failed` 和 `RunFailure` 的 `TrialResult`。内置优化器会消费并结束该候选的 pending 状态，但只用成功结果更新最优解；因此单个候选失败不会中断后续搜索，全部候选失败且无法选出 incumbent 才终止调优。

`ComparisonEngine` 要求所有 Trial 具有相同评价单元，并产出完整性、统计摘要、排名/Pareto 层、两两配对结果和双侧符号检验。

## 9. 事件、制品和仓库

### 9.1 RunContext

算法与领域从 `RunContext` 获得：

- 不可变 `RunSpec` 与 `execution_id`；
- 算法和领域 Manifest；
- 隔离工作目录 URI 和事件日志 URI；
- `ArtifactPublisher`、只读 `ArtifactResolver` 与有序 `EventPublisher`；
- 共享的 `threading.Event` 类型 `cancel_event`，供算法在内部长循环中协作检查取消。

```python
artifact = context.artifact_publisher.publish_json(
    {"weights": [0.2, 0.3, 0.5]},
    kind=ArtifactKind.PARAMETERS,
)

context.event_publisher.emit(
    EventType.TRAINING_PROGRESS,
    {"episode": 2, "episodes": 100, "step": 300, "max_steps": 1000},
)
```

发布器自动附加 experiment、execution、trial 和 run 来源。Run 返回的制品必须属于本地内容寻址仓库，来源必须匹配当前 Run，类型必须由 Manifest 声明；`STATE_SNAPSHOT` 是 Runtime 允许的领域制品例外。

### 9.2 Run 失败隔离与协作取消

`LocalOrchestrator` 在 Runtime driver 边界把普通异常转换为 `RunStatus.FAILED`，并继续后续 Run；失败 Run 仍会发布 `RUN_FINISHED`、事件日志、Run 报告和 Manifest。标准 Execution 中只要存在 `failed` 或 `timed_out` Run，顶层 Execution 也标记为 `failed`，同时保留结果 Manifest 供诊断。制品校验、算法/领域构造、Runtime 选择等 driver 之前的错误属于 Execution 级配置或环境错误，不在此隔离边界内。

取消由同一个 `cancel_event` 从 HTTP Execution 传到编排器和 `RunContext`：

- 编排器在各 Run 之间检查；
- Online 在决策步边界检查；
- Iterative 在迭代、提案和候选评价边界检查；
- Batch 与 Trainable 在调用前后检查，算法可在 `solve/fit/evaluate` 内自行检查；
- `TuningOrchestrator` 在候选批次、候选、训练/评价和 Benchmark 边界检查；
- DFJSP-T CTDE-PPO 在训练与评价循环中主动检查。

收到信号后，Execution 先进入 `cancel_requested`，工作线程到达检查点后进入 `cancelled`。这是协作式取消，不会强杀 Python 线程或第三方求解器。

### 9.3 持久化仓库

- `LocalArtifactStore`：存放对象、校验 digest、读写 Manifest；
- `ExperimentRepository`：按不可变 `experiment_id` 保存定义；
- `ExecutionRepository`：原子更新 Execution 生命周期；
- `JsonlEventSink`：写入 durable、append-only Run 事件。

`ExecutionRepository.recover_interrupted()` 用于单进程服务启动恢复：遗留 `cancel_requested` 收口为 `cancelled`，遗留 `draft/compiled/running` 收口为带 `process_restarted` 失败信息的 `failed`。

## 10. 回放 API

`load_replay_trace()` 从事件日志构造 `ReplayTrace`，包括事件、快照、来源和分支信息。`ReplayDiagnostics` 报告事件/决策/快照数量、缺失证据、序号/时间问题以及三类回放证据就绪度。就绪只说明 Trace 中具备对应事件、计划或快照证据，不代表当前主机仍能导入算法、读取源场景或执行依赖；真正复现/分支时仍会验证这些条件。

`LocalReplayOrchestrator` 已实现：

- `reproduce(source, run, execution_id, diff_policy)`：精确重跑并返回 Diff；
- `branch(request, source_run)`：验证快照摘要，从快照恢复并切换在线算法继续运行。

`ReplayDiffer` 可比较完整事件或决策视图，并支持容差、事件过滤、忽略 payload 路径、状态 hash 开关和差异数量上限。

复现和分支持久化为源 Experiment 下 `purpose=replay` 的子 `ExecutionRecord`，不会创建内容不同但复用同一 `experiment_id` 的伪实验。详情同时返回 `source_experiment` 和 replay 来源元数据；普通实验统计与 replay 统计分开计算。

记录回放的 `factory_config` 不属于 Trace 的必要证据。源文件不可读取、摘要变化或场景为语料时它可以缺失，事件与决策回放仍可读取。

## 11. DFJSP-T 注册内容

导入后端路由时会检查并一次性注册 DFJSP-T 插件，拒绝部分注册或 Manifest 不一致：

```text
领域：dfjsp_t@1.0.0
算法：spt_rule、edd_rule、weighted_dispatch_rule、rolling_ga、
      cp_sat_reference、ctde_ppo（均为 1.0.0）
调优器：platform.grid_search、platform.random_search、
        platform.genetic_search（均为 1.0.0）
```

DFJSP-T Metric schema 当前声明 `C_max_E`、`C_max`、`unfinished_jobs`、`unfinished_urgent_jobs`、`success_rate`、`total_tardiness` 和 `weighted_tardiness`。

`ctde_ppo@1.0.0` 的 `online` 接口声明 `minimum_input_artifacts=1`，接受 `MODEL` 或 `CHECKPOINT`；其 `trainable` 接口不要求预先输入制品，并发布可冻结复用的模型/checkpoint。

领域参数只接受：

```json
{
  "scenario_root": ".",
  "route_solver": "astar",
  "assigner": "nearest",
  "validation_scenarios": []
}
```

设备、GPU 卡号和分布式标志属于具体算法参数，不属于 DFJSP-T 领域参数。

## 12. 配置示例

下面示例使用当前仓库自带 Benchmark JSONL 的真实实例 ID 和 `canonical_hash`。数据文件若被重新生成，应同步更新 URI 片段和摘要。

```yaml
schema_version: 1
experiment_id: dfjspt_compare_v1
name: DFJSP-T 基线比较
purpose: compare
base_seed: 20260920
repetitions: 3

domain:
  id: dfjsp_t
  version: 1.0.0
  parameters:
    scenario_root: .
    route_solver: astar
    assigner: nearest

algorithms:
  - id: spt_rule
    version: 1.0.0
    interface: online
    budget:
      max_steps: 5000
    parameters: {}
  - id: rolling_ga
    version: 1.0.0
    interface: online
    budget:
      max_steps: 5000
      decision_time_seconds: 10
    parameters:
      population_size: 48
      generations: 40

scenarios:
  - id: benchmark_00000
    uri: dataset/dfjsp_t_benchmark/benchmark.jsonl#dfjspt-benchmark-normal-0086000000-00000
    digest: sha256:4f8e5f6543a6ae47329262a1b2403d055429a1d67dfb5e235beb611728223bc8
    metadata:
      split: benchmark
      instance_id: dfjspt-benchmark-normal-0086000000-00000

objective:
  mode: lexicographic
  components:
    - name: urgent_makespan
      metric: C_max_E
      direction: minimize
      aggregation: mean
    - name: makespan
      metric: C_max
      direction: minimize
      aggregation: mean
```

Python 调用：

```python
from experiment.algorithm_platform import (
    ExperimentCompiler,
    LocalOrchestrator,
    default_registry,
    load_experiment_spec,
)
from experiment.algorithm_platform_plugins.dfjsp_t import register_dfjsp_t_plugins

register_dfjsp_t_plugins(default_registry)
spec = load_experiment_spec("experiment.yaml")
plan = ExperimentCompiler(default_registry).compile(spec)
report = LocalOrchestrator(
    "dataset/algorithm_platform",
    registry=default_registry,
).execute(plan, execution_id="exec-example-001")
```

同一进程已由后端完成注册时，不应再次重复调用注册函数。

## 13. 当前实现边界

- 本地编排是单进程、顺序执行，不是分布式调度。
- HTTP 后台线程只负责异步启动，不提供资源队列、故障迁移或多机执行。
- 本地执行不提供 CPU/GPU/内存硬隔离；相关预算会被拒绝。
- `distributed` 等算法参数由算法实现解释，平台不自动启动 DDP。
- 取消是协作式的；一次阻塞的 Batch 求解或 Trainable 调用无法被平台强制抢占。
- 当前 HTTP 控制面没有认证、授权或租户隔离。
- 核心代码具备训练、调优、比较和回放接口，不等于任何算法已通过收敛或效果验收。
- 本次文档更新没有运行测试、构建、真实数据执行或前端验收。
