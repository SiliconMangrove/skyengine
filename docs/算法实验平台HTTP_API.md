# 算法实验平台 HTTP API

> 适用实现：`application/backend/algorithm_platform_routes.py`  
> 文档状态：当前代码接口（2026-09-21）  
> 验证边界：本次仅依据源码整理，未发起 HTTP 请求、未运行测试或构建。

## 1. 基础约定

FastAPI Router 前缀为：

```text
/algorithm-platform
```

前端 `API_BASE_URL` 默认是 `/api`，因此浏览器通常访问：

```text
/api/algorithm-platform/...
```

请求与普通响应使用 UTF-8 JSON。制品下载返回其原始媒体类型。时间字段为 UTC ISO 8601 字符串；枚举使用小写字符串。

实验配置当前只支持 `schema_version=1`。显式传入其他版本会返回配置错误；未填写时按版本 1 读取。Experiment 顶层采用字段白名单，未知顶层字段会被拒绝，不能依赖服务端静默忽略拼写错误。

### 1.1 实验输入包络

需要实验定义的端点接受两种互斥形式。

内联 JSON/YAML：

```json
{
  "config_format": "json",
  "config": {"experiment_id": "..."}
}
```

`config_format` 可为 `json`、`yaml` 或 `yml`；`config` 可为已解析对象或对应格式的字符串。

引用已保存定义：

```json
{
  "experiment_id": "dfjspt_compare_v1"
}
```

创建不可变实验定义只接受内联形式；校验、编译和创建 Execution 接受两种形式。

### 1.2 Execution 状态

```text
draft → compiled → running → succeeded
  │        │          ├── failed
  │        │          └── cancel_requested → cancelled
  │        └────────────── cancel_requested → cancelled
  └── failed
```

创建 Execution 时，接口先持久化定义和 `compiled` 状态，再用进程内守护线程执行，立即返回 `202`。应轮询详情或列表获取终态。`POST /executions/{execution_id}/cancel` 发出协作式取消请求；返回 `cancel_requested` 不表示线程已立即停止，工作线程到达检查点后才变为 `cancelled`。

### 1.3 通用错误

FastAPI 错误体为：

```json
{"detail": "错误说明或结构化对象"}
```

| 状态码 | 含义 |
|---:|---|
| 200 | 查询成功；`/validate` 的业务校验失败也返回 200 和 `valid=false` |
| 201 | 不可变资源、复现或分支创建成功 |
| 202 | Execution 已编译并进入后台执行，或协作式取消请求已受理 |
| 404 | 数据集、Experiment、Execution、Run、Manifest、比较报告或制品不存在 |
| 409 | 不可变 ID 冲突、结果尚未发布、Run ID 存在歧义 |
| 422 | 请求结构、枚举、标识符、schema、能力或回放参数不合法 |
| 500 | 已保存的报告结构不符合服务端预期 |

### 1.4 V1 工作台模板

前端 `/training` 只提供训练、超参数探索和测试三种实验模式。数据集由目录接口显式选择；训练使用语料引用，超参数探索和测试使用带实例 ID 与摘要的场景引用。只要数据文件内容未被重新生成且本机运行依赖可用，可直接执行 `/validate`、`/compile` 和 `/executions`。

## 2. 能力目录

### 2.1 `GET /catalog`

请求：无。

成功响应 `200`：

```json
{
  "protocol_version": "1.0",
  "algorithms": [
    {
      "algorithm_id": "spt_rule",
      "name": "最短加工时间规则",
      "version": "1.0.0",
      "interfaces": ["online"],
      "parameter_schema": {},
      "input_artifact_kinds": [],
      "minimum_input_artifacts": {},
      "output_artifact_kinds": ["solution"]
    }
  ],
  "optimizers": [
    {
      "algorithm_id": "platform.genetic_search",
      "name": "Genetic Search Optimizer",
      "version": "1.0.0",
      "interfaces": ["search_optimizer"],
      "parameter_schema": {}
    }
  ],
  "domains": [
    {
      "domain_id": "dfjsp_t",
      "version": "1.0.0",
      "capabilities": [
        "online_execution",
        "batch_evaluation",
        "training_data",
        "snapshot_restore",
        "deterministic_replay"
      ]
    }
  ],
  "runtimes": [],
  "interfaces": ["batch", "online", "search_optimizer", "trainable"]
}
```

状态说明：返回进程内当前已注册 Manifest。`algorithms` 只列可训练或可测试的算法，`optimizers` 单独列超参数探索流程可选择的搜索方式。默认 Runtime 由 `LocalOrchestrator` 内建时，未必出现在 `runtimes` 数组；该数组只列显式注册的 RuntimePlugin。

### 2.2 `GET /datasets`

返回当前 `dataset/*/manifest.json` 中可被算法平台使用的数据集摘要。

```json
{
  "items": [
    {
      "dataset_id": "dfjsp_t_benchmark",
      "name": "DFJSP-T 测试集",
      "domain_id": "dfjsp_t",
      "split": "benchmark",
      "count": 200,
      "uri": "dataset/dfjsp_t_benchmark/benchmark.jsonl",
      "digest": "sha256:..."
    }
  ],
  "count": 3
}
```

### 2.3 `GET /datasets/{dataset_id}`

返回数据集摘要及实例清单。每个实例包含 `instance_id`、canonical digest、生成 seed 和 profile；未知数据集返回 `404`。

```json
{
  "dataset_id": "dfjsp_t_benchmark",
  "name": "DFJSP-T 测试集",
  "split": "benchmark",
  "count": 200,
  "uri": "dataset/dfjsp_t_benchmark/benchmark.jsonl",
  "digest": "sha256:...",
  "instances": [
    {
      "instance_id": "dfjspt-benchmark-normal-0086000000-00000",
      "digest": "sha256:...",
      "seed": 86000000,
      "profile": "normal"
    }
  ]
}
```

### 2.4 `GET /datasets/{dataset_id}/results`

返回所有成功完成的 `purpose=evaluate` Execution 中属于该数据集的 Run 结果。每项在标准 RunResult 基础上补充 `dataset_id`、`instance_id`、算法 ID/名称/版本/参数、Execution ID、Experiment ID 和完成时间。统一比较页用该接口按数据集聚合已有测试算法；回放页用它定位“数据集 + 实例 + 算法”对应的 `execution_id/run_id`。

```json
{
  "dataset": {"dataset_id": "dfjsp_t_benchmark", "count": 200},
  "items": [
    {
      "instance_id": "dfjspt-benchmark-normal-0086000000-00000",
      "algorithm_id": "spt_rule",
      "algorithm_name": "最短加工时间规则",
      "algorithm_version": "1.0.0",
      "execution_id": "exec-...",
      "run_id": "run-...",
      "status": "succeeded",
      "metrics": {"C_max_E": 40.0, "C_max": 120.0}
    }
  ],
  "count": 1
}
```

## 3. 实验定义

### 3.1 `GET /experiments`

请求：无。

成功响应 `200`：

```json
{
  "items": [
    {
      "experiment_id": "dfjspt_spt_test_v1",
      "digest": "sha256:...",
      "created_at": "2026-09-20T08:00:00+00:00",
      "spec": {},
      "latest_execution_id": "exec-...",
      "latest_status": "succeeded",
      "execution_count": 2,
      "latest_replay_execution_id": "replay-...",
      "latest_replay_status": "succeeded",
      "replay_count": 1
    }
  ],
  "count": 1
}
```

状态说明：按创建时间倒序返回不可变定义，并分别附加普通 Execution 与 replay 子 Execution 摘要。`execution_count` 不包含 Replay；`replay_count` 单独统计。没有相应 Execution 时 latest 字段为 `null`、计数为 0。

### 3.2 `POST /experiments`

请求：仅接受内联实验包络。

```json
{
  "config_format": "json",
  "config": {
    "schema_version": 1,
    "experiment_id": "dfjspt_spt_test_v1",
    "name": "DFJSP-T SPT 测试",
    "purpose": "evaluate",
    "base_seed": 20260920,
    "repetitions": 1,
    "domain": {
      "id": "dfjsp_t",
      "version": "1.0.0",
      "parameters": {"scenario_root": "."}
    },
    "algorithms": [
      {
        "id": "spt_rule",
        "version": "1.0.0",
        "interface": "online",
        "budget": {"max_steps": 5000},
        "parameters": {}
      }
    ],
    "scenarios": [
      {
        "id": "benchmark_00000",
        "uri": "dataset/dfjsp_t_benchmark/benchmark.jsonl#dfjspt-benchmark-normal-0086000000-00000",
        "digest": "sha256:4f8e5f6543a6ae47329262a1b2403d055429a1d67dfb5e235beb611728223bc8",
        "metadata": {
          "split": "benchmark",
          "instance_id": "dfjspt-benchmark-normal-0086000000-00000"
        }
      }
    ],
    "objective": {
      "mode": "single",
      "components": [
        {"name": "makespan", "metric": "C_max", "direction": "minimize"}
      ]
    }
  }
}
```

成功响应 `201`：

```json
{
  "experiment_id": "dfjspt_spt_test_v1",
  "digest": "sha256:...",
  "created_at": "2026-09-20T08:00:00+00:00",
  "spec": {"schema_version": 1}
}
```

状态说明：同一 ID、相同内容是幂等保存并返回原记录；同一 ID、不同内容返回 `409`。缺字段、格式错误或 schema 错误返回 `422`。

### 3.3 `GET /experiments/{experiment_id}`

请求：路径参数 `experiment_id`。

成功响应 `200`：与创建实验成功响应相同。

状态说明：未知或非法 ID 返回 `404`。

## 4. 校验与编译

### 4.1 `POST /validate`

请求：内联实验包络或 `experiment_id` 引用。

成功且有效 `200`：

```json
{
  "valid": true,
  "errors": [],
  "experiment": {"schema_version": 1},
  "plan_digest": "sha256:...",
  "dynamic": false
}
```

业务校验失败仍为 `200`：

```json
{
  "valid": false,
  "errors": [
    {"code": "ValueError", "message": "具体校验错误"}
  ]
}
```

状态说明：标准实验会执行完整编译；`purpose=tune` 只做完整校验并计算稳定摘要，返回 `dynamic=true`。

### 4.2 `POST /compile`

请求：内联实验包络或 `experiment_id` 引用。

标准实验成功响应 `200`：

```json
{
  "experiment": {},
  "plan_digest": "sha256:...",
  "dynamic": false,
  "plan": {
    "experiment": {},
    "trials": [],
    "runs": [],
    "compiled_at": "2026-09-20T08:00:00+00:00",
    "plan_digest": "sha256:..."
  }
}
```

调优实验成功响应 `200`：

```json
{
  "experiment": {},
  "plan_digest": "sha256:...",
  "dynamic": true,
  "plan": null
}
```

状态说明：请求或编译不合法返回 `422`。编译不会启动执行。

## 5. Execution

### 5.1 `GET /executions`

请求：无。

成功响应 `200`：

```json
{
  "items": [
    {
      "execution_id": "exec-20260920T080000-0123456789ab",
      "experiment_id": "dfjspt_compare_v1",
      "purpose": "compare",
      "status": "running",
      "created_at": "...",
      "updated_at": "...",
      "plan_digest": "sha256:...",
      "started_at": "...",
      "finished_at": null,
      "result_manifest_id": null,
      "failure": null,
      "metadata": {"mode": "execute", "dynamic": false}
    }
  ],
  "count": 1
}
```

状态说明：按创建时间倒序返回持久化状态，不依赖当前进程内线程表。

### 5.2 `POST /executions`

请求：实验包络加必填 `mode`。`mode` 为 `auto`、`execute` 或 `tune`。

```json
{
  "mode": "execute",
  "experiment_id": "dfjspt_compare_v1"
}
```

也可在同一请求内传 `config_format/config`。

成功响应 `202`：一个状态为 `compiled` 的 `ExecutionRecord`。

状态说明：

- `mode=tune` 只允许 `purpose=tune`；
- 调优实验只允许 `mode=tune` 或 `auto`；
- 服务端生成 `exec-<UTC时间>-<随机串>`；
- 随后后台线程更新为 `running`，终态为 `succeeded`、`failed` 或 `cancelled`；
- `202` 只表示已提交，不表示 Run 或算法效果成功；
- 不可变 Experiment ID 内容冲突返回 `409`；请求或编译错误返回 `422`。

Runtime driver 内单个 Run 抛出异常时，平台会将该 Run 写成带 `failure` 的 `failed` 结果并继续后续 Run；编译、制品校验或 Runtime 构造等 Run 执行前错误仍会使整个 Execution 失败。服务重启时，遗留的本进程 `draft/compiled/running` 记录会被收口为 `failed`，遗留 `cancel_requested` 会被收口为 `cancelled`。

### 5.3 `GET /executions/{execution_id}`

请求：路径参数 `execution_id`。

成功响应 `200`：

```json
{
  "execution": {"execution_id": "exec-...", "status": "succeeded"},
  "experiment": {
    "experiment_id": "dfjspt_compare_v1",
    "digest": "sha256:...",
    "created_at": "...",
    "spec": {}
  }
}
```

状态说明：未知 Execution 或其 Experiment 不存在时返回 `404`。

Replay 子 Execution 的详情还会返回源实验和来源信息：

```json
{
  "execution": {"execution_id": "replay-...", "purpose": "replay"},
  "experiment": {"experiment_id": "dfjspt_compare_v1", "spec": {}},
  "source_experiment": {"experiment_id": "dfjspt_compare_v1", "spec": {}},
  "replay": {
    "replay_mode": "deterministic",
    "parent_execution_id": "exec-source",
    "source_execution_id": "exec-source",
    "source_run_id": "run-source"
  }
}
```

Replay 复用源 Experiment 的不可变研究定义，自身作为 `purpose=replay` 的子 Execution 持久化；`replay` 即该子 Execution 保存的来源元数据。

### 5.4 `POST /executions/{execution_id}/cancel`

请求体：无。

成功响应 `202`：更新后的 `ExecutionRecord`，通常为：

```json
{
  "execution_id": "exec-...",
  "status": "cancel_requested"
}
```

状态说明：

- 只允许取消 `compiled` 或 `running` 的后台 Execution；
- 对已经是 `cancel_requested` 或 `cancelled` 的 Execution 幂等返回当前记录；
- 已 `succeeded/failed` 返回 `409`；未知 ID 返回 `404`；
- 没有当前进程协作 worker 的记录返回 `409`；
- 取消信号在 Run、在线决策、迭代搜索、调优阶段和支持该信号的算法内部检查，不会强杀线程。一次阻塞的第三方 Batch/Trainable 调用可能要在返回后才能完成取消。

### 5.5 `GET /executions/{execution_id}/metrics`

请求：路径参数 `execution_id`。

成功响应 `200`：

```json
{
  "execution_id": "exec-...",
  "status": "running",
  "items": [
    {
      "execution_id": "exec-...",
      "owner_execution_id": "exec-...",
      "route_execution_id": "exec-...",
      "workspace_execution_id": "exec-...candidate-000000.evaluate",
      "run_id": "run-...",
      "status": "succeeded",
      "metrics": {"C_max": 120.0},
      "objective_values": [120.0],
      "constraint_values": {},
      "constraint_violations": {},
      "feasible": true
    }
  ],
  "candidates": [],
  "incumbents": [],
  "benchmark": []
}
```

状态说明：运行中可从 `metric_updated` 事件形成临时项；终态从 Run 报告读取。调优 Execution 额外返回候选、incumbent 和 Benchmark；尚未完成时返回已观察的候选进度。候选评价子实验使用 `purpose=validate`。单个候选失败会作为 `status=failed` 的 Trial 结果交给优化器并继续搜索，失败候选不会参与 incumbent 选择；所有候选均失败时调优 Execution 失败。

### 5.6 `GET /executions/{execution_id}/logs`

请求：路径参数 `execution_id`。

成功响应 `200`：

```json
{
  "execution_id": "exec-...",
  "items": [
    {
      "sequence": 0,
      "event_type": "run_started",
      "execution_id": "exec-...",
      "run_id": "run-...",
      "wall_time": "...",
      "simulation_time": 0.0,
      "state_hash": null,
      "payload": {}
    }
  ],
  "count": 1
}
```

状态说明：合并主 Execution、调优子 Execution 和可发现的事件制品，按 execution/run/sequence 排序。它是结构化标准事件，不是 stdout 文本。Trainable 算法可以通过 `RunContext.event_publisher` 写入 `training_progress`；CTDE-PPO 会报告训练轮次、采样步数、总步数和实际计算设备，因此首轮轨迹尚未完成时也能在前端看到进度。

### 5.7 `GET /executions/{execution_id}/manifest`

请求：路径参数 `execution_id`。

成功响应 `200`：`ArtifactManifest`，包含 `manifest_id`、`created_at`、`artifacts` 和 `metadata`。

状态说明：Execution 还没有 `result_manifest_id` 时返回 `409`；Manifest 文件缺失返回 `404`。

### 5.8 `GET /executions/{execution_id}/comparison`

请求：路径参数 `execution_id`。

成功响应 `200`：

```json
{
  "execution_id": "exec-...",
  "experiment_id": "dfjspt_compare_v1",
  "plan_digest": "sha256:...",
  "objective": {},
  "design": {"balanced": true, "cells": []},
  "algorithms": [
    {
      "trial_id": "trial-...",
      "algorithm_id": "spt_rule",
      "rank": 1,
      "pareto_layer": null,
      "complete": true,
      "aggregate": {}
    }
  ],
  "pairwise": [],
  "pareto_front_trial_ids": [],
  "generated_at": "...",
  "complete": true
}
```

状态说明：只有执行报告含统一比较报告时可读；否则返回 `404`。服务端根据平衡设计和每个算法权威 Run 数重新计算 `complete`。

### 5.9 `GET /executions/{execution_id}/runs`

请求：路径参数 `execution_id`。

成功响应 `200`：

```json
{
  "execution_id": "exec-...",
  "status": "succeeded",
  "items": [
    {
      "execution_id": "exec-...",
      "owner_execution_id": "exec-...",
      "route_execution_id": "exec-...",
      "workspace_execution_id": "exec-...candidate-000000.evaluate",
      "run_id": "run-...",
      "status": "succeeded",
      "metrics": {},
      "objective_values": [],
      "artifacts": []
    }
  ],
  "count": 1
}
```

状态说明：已发布结果的标准 Execution 从汇总报告读取；尚未发布最终报告的 Run 会从 `run_started`、`metric_updated` 和 `run_finished` 事件补全，因此运行中或异常中断的 Run 也能出现在回放选择列表。调优 Execution 会发现训练、候选评价与 Benchmark 子 Execution 的 Run 报告。四个 Execution 字段的约定是：

- `execution_id`：为兼容列表消费，返回本次请求的根 Execution ID；
- `owner_execution_id`：拥有调优结果和生命周期的根 Execution ID；
- `route_execution_id`：调用记录回放、复现和分支 HTTP 路由时使用的 Execution ID；
- `workspace_execution_id`：真正产生事件、Manifest 与制品来源元数据的子执行工作区 ID。

标准 Run 的四者相同；调优子 Run 的前三者为根 Execution，`workspace_execution_id` 为 `.train`、`.evaluate` 或 `.benchmark-*` 子工作区。不要把子工作区 ID 当作独立的 `ExecutionRecord` 查询。

## 6. 记录回放

### 6.1 `GET /runs/{run_id}/replay`

查询参数：

| 参数 | 必填 | 值 |
|---|---|---|
| `mode` | 否 | `full`，默认；或 `decision` |
| `execution_id` | 否 | 用于限定同名 Run 所属 Execution |

示例：

```text
GET /algorithm-platform/runs/run-abc/replay?execution_id=exec-123&mode=decision
```

成功响应 `200`：

```json
{
  "trace": {
    "execution_id": "exec-123",
    "run_id": "run-abc",
    "mode": "recorded",
    "events": [],
    "snapshots": [],
    "event_log": {},
    "parent_run_id": null,
    "branch_after_sequence": null,
    "metadata": {}
  },
  "diagnostics": {
    "event_count": 0,
    "decision_count": 0,
    "snapshot_count": 0,
    "issues": [],
    "record_replay_ready": false,
    "deterministic_replay_ready": false,
    "branch_replay_ready": false
  },
  "view": "decision",
  "items": [],
  "factory_config": null
}
```

状态说明：`full` 的 `items` 是事件，`decision` 的 `items` 是按 request_id 聚合的决策证据。Run 不存在返回 `404`；同一 Run ID 命中多个 Execution 且未指定 `execution_id` 时返回 `409`。

三个 `*_replay_ready` 字段表示 Trace 中的记录、计划或快照证据是否齐全，不表示当前机器一定还能读取源数据、加载算法依赖或成功执行复现/分支。实际操作会再次校验环境、版本、参数、制品和摘要。

DFJSP-T 单实例在源文件仍可读取且内容匹配时会返回用于正式渲染的 `factory_config`。语料场景、源文件缺失、不可读或内容变化时该字段为 `null`；这不会阻止已保存事件和决策的记录回放。

## 7. 确定性复现

### 7.1 `POST /executions/{execution_id}/runs/{run_id}/replay/reproduce`

路径中的 Execution/Run 是源记录。请求体：

```json
{
  "execution_id": "replay-reproduce-manual-001",
  "policy": {
    "float_tolerance": 0.0,
    "simulation_time_tolerance": 0.0,
    "compare_sequence_numbers": true,
    "compare_payloads": true,
    "compare_state_hashes": true,
    "event_types": [],
    "ignored_payload_paths": [
      "payload.diagnostics.decision_seconds",
      "decision.response.diagnostics.decision_seconds"
    ],
    "max_differences": 1000
  }
}
```

`execution_id` 可省略，由服务端生成；必须与源 Execution 不同。

省略 `ignored_payload_paths` 时，服务端默认忽略完整事件中的 `payload.diagnostics.decision_seconds` 和决策视图中的 `decision.response.diagnostics.decision_seconds`。这两个字段反映真实运行耗时，不属于确定性语义；显式传入空数组可把它们也纳入比较。

成功响应 `201`：序列化的 `ReplayExecutionReport`，主要字段为：

```json
{
  "execution_id": "replay-reproduce-manual-001",
  "mode": "deterministic",
  "source_execution_id": "exec-123",
  "source_run_id": "run-abc",
  "trace": {},
  "result": {},
  "diagnostics": {},
  "diff": {
    "equivalent": true,
    "first_divergence": null,
    "differences": []
  },
  "run_manifest": {},
  "plan_artifact": {},
  "report_artifact": {},
  "execution_manifest": {}
}
```

状态说明：该调用同步执行，可能持续较久。它先要求源 Trace 的确定性复现证据齐全，再读取源 Execution 的不可变计划，使用同一 RunSpec 重跑并比较事件。新 ID 会持久化为源 Experiment 下 `purpose=replay` 的子 Execution，并记录 `source_execution_id/source_run_id`；它不是一个新的 Experiment。新 ID 已存在返回 `409`；证据不完整返回 `409`；源领域不支持确定性复现、策略无效或源计划不可用返回 `422` 或 `409`。

## 8. 快照分支

### 8.1 `POST /executions/{execution_id}/runs/{run_id}/replay/branch`

路径中的 Execution/Run 是源记录。请求体：

```json
{
  "execution_id": "replay-branch-001",
  "run_id": "run-branch-001",
  "snapshot_sequence": 42,
  "algorithm": {
    "id": "edd_rule",
    "version": "1.0.0",
    "interface": "online",
    "budget": {"max_steps": 5000},
    "parameters": {}
  },
  "metadata": {
    "reason": "比较序列 42 后的替代规则"
  }
}
```

成功响应 `201`：序列化的 `ReplayExecutionReport`，`mode=branch`、`diff=null`，Trace 含 `parent_run_id` 和 `branch_after_sequence`。

状态说明：

- 新 Execution ID 和新 Run ID 均必填且必须与源 ID 不同；
- `snapshot_sequence` 必须精确命中源 Trace 的快照锚点；
- 目标算法必须是已注册、支持源领域的 `online` 算法；
- 目标算法参数必须通过 Manifest schema，并满足该 `online` 接口的 `minimum_input_artifacts`；
- 分支前会验证快照 digest，再由领域恢复正式状态；
- 分支结果保存为源 Experiment 下 `purpose=replay` 的子 Execution，详情保留源 Execution/Run 和分支算法来源；
- 源 Trace 的分支证据不完整返回 `409`；ID 已存在返回 `409`；参数、算法、输入制品、快照或能力不合法返回 `422`。

## 9. 双 Run 差异诊断

### 9.1 `POST /replay/diff`

请求体：

```json
{
  "left_run_id": "run-source",
  "right_run_id": "run-branch-001",
  "left_execution_id": "exec-source",
  "right_execution_id": "replay-branch-001",
  "mode": "decision",
  "policy": {
    "float_tolerance": 0.000001,
    "simulation_time_tolerance": 0.000001,
    "compare_sequence_numbers": true,
    "compare_payloads": true,
    "compare_state_hashes": true,
    "event_types": [],
    "ignored_payload_paths": [
      "payload.diagnostics.decision_seconds",
      "decision.response.diagnostics.decision_seconds"
    ],
    "max_differences": 100
  }
}
```

执行 ID 可省略，但 Run ID 在多个 Execution 中重名时会产生 `409`。`mode` 为 `full` 或 `decision`。

成功响应 `200`：

```json
{
  "left_run_id": "run-source",
  "right_run_id": "run-branch-001",
  "view": "decisions",
  "left_item_count": 10,
  "right_item_count": 10,
  "compared_item_count": 10,
  "matched_prefix_count": 7,
  "differences": [],
  "truncated": false,
  "equivalent": true,
  "first_divergence": null
}
```

状态说明：比较的是已保存 Trace，不会重跑算法。请求策略不合法返回 `422`。

## 10. 制品下载

### 10.1 `GET /artifacts/{digest}`

请求：`digest` 必须是完整的 `sha256:<64位十六进制>`。冒号应按客户端需要做 URL 编码。

成功响应 `200`：原始二进制或 JSON 内容，并带：

```text
X-Artifact-Digest: sha256:...
Content-Type: Manifest 中记录的 media_type
```

状态说明：digest 格式不支持返回 `422`；对象不存在返回 `404`。若未从 Manifest 找到媒体类型，使用 `application/octet-stream`。

## 11. 参数调优完整示例

下面示例调优在线规则，不需要训练语料，并使用当前仓库自带验证集和 Benchmark 中的真实实例摘要。数据文件若重新生成，需要同步更新 URI 片段和 digest。不要在当前本地编排预算中填写 `cpu_cores`、`gpu_count` 或 `memory_bytes`。

```json
{
  "mode": "tune",
  "config_format": "json",
  "config": {
    "schema_version": 1,
    "experiment_id": "dfjspt_weight_tune_v1",
    "name": "DFJSP-T 派工权重调优",
    "purpose": "tune",
    "base_seed": 20260920,
    "repetitions": 3,
    "domain": {
      "id": "dfjsp_t",
      "version": "1.0.0",
      "parameters": {
        "scenario_root": ".",
        "route_solver": "astar",
        "assigner": "nearest"
      }
    },
    "algorithms": [
      {
        "id": "weighted_dispatch_rule",
        "version": "1.0.0",
        "interface": "online",
        "budget": {"max_steps": 5000},
        "parameters": {
          "processing_time_weight": 1.0,
          "due_date_weight": 1.0,
          "remaining_work_weight": 0.0,
          "priority_weight": 2.0
        }
      }
    ],
    "scenarios": [
      {
        "id": "validation_00000",
        "uri": "dataset/dfjsp_t_validation/validation.jsonl#dfjspt-validation-validation-0042060914-00000",
        "digest": "sha256:c5fe09197c64d8a0839e3f6fee45e02354e70988eb419687a6543068209b28ea",
        "metadata": {
          "split": "validation",
          "instance_id": "dfjspt-validation-validation-0042060914-00000"
        }
      }
    ],
    "objective": {
      "mode": "lexicographic",
      "components": [
        {"name": "urgent_makespan", "metric": "C_max_E", "direction": "minimize", "aggregation": "mean"},
        {"name": "makespan", "metric": "C_max", "direction": "minimize", "aggregation": "mean"}
      ],
      "constraints": [],
      "feasibility_first": true
    },
    "tuning": {
      "optimizer": {
        "id": "platform.genetic_search",
        "version": "1.0.0",
        "interface": "search_optimizer",
        "parameters": {
          "population_size": 24,
          "elite_fraction": 0.25,
          "mutation_rate": 0.2,
          "crossover_rate": 0.9,
          "tournament_size": 3
        }
      },
      "search_space": {
        "processing_time_weight": {"kind": "float", "low": 0.1, "high": 4.0, "step": 0.1},
        "due_date_weight": {"kind": "float", "low": 0.1, "high": 4.0, "step": 0.1},
        "remaining_work_weight": {"kind": "float", "low": 0.0, "high": 3.0, "step": 0.1},
        "priority_weight": {"kind": "float", "low": 0.0, "high": 8.0, "step": 0.25}
      },
      "max_trials": 48,
      "batch_size": 8,
      "training_scenarios": [],
      "benchmark_scenarios": [
        {
          "id": "benchmark_00000",
          "uri": "dataset/dfjsp_t_benchmark/benchmark.jsonl#dfjspt-benchmark-normal-0086000000-00000",
          "digest": "sha256:4f8e5f6543a6ae47329262a1b2403d055429a1d67dfb5e235beb611728223bc8",
          "metadata": {
            "split": "benchmark",
            "instance_id": "dfjspt-benchmark-normal-0086000000-00000"
          }
        }
      ]
    }
  }
}
```

运行过程：

1. `POST /validate` 确认 `valid=true`；
2. `POST /compile` 得到 `dynamic=true`；
3. `POST /executions` 得到 `202` 与 Execution ID；
4. 轮询 `GET /executions/{id}`；
5. 用 `/metrics` 查看候选、incumbent 和 Benchmark；
6. 成功后读取 `/manifest`，必要时按 digest 下载 checkpoint 或报告。

编译器将 `scenarios` 生成为 `purpose=validate` 的候选评价子实验；它拒绝候选选择或训练场景中的 `metadata.split=benchmark`，并要求 `benchmark_scenarios` 显式声明 `split=benchmark`。ID 与 digest 也必须在训练、候选选择和封闭 Benchmark 三部分之间互斥。

## 12. 实现边界

- Execution 在本机进程内顺序运行；没有分布式调度、任务队列或多机故障迁移。
- 没有 CPU/GPU/内存硬资源隔离；相关有效 Run 预算会在执行前失败。
- 后台线程不等同于资源隔离，多个 HTTP Execution 也没有统一资源仲裁。
- 取消是协作式而非强制抢占；阻塞的第三方 Batch/Trainable 调用可能要在返回后才完成取消。
- 当前没有认证、授权或租户隔离；应部署在受信任环境。
- 接口返回 `succeeded` 只表示编排完成并发布结果，不表示算法效果达到业务目标。
- 本文未以实际 HTTP 调用验收。

## 训练 Checkpoint

`GET /executions/{execution_id}/checkpoints` 返回 `items` 列表，训练过程中及执行终止后均可访问已保存记录。记录包含名称、累计采样步数、已完成轮数、创建时间、Run 和工作区标识。

下载使用 `GET /executions/{execution_id}/checkpoints/{run_id}/{name}`；交接使用 `POST /executions/{execution_id}/checkpoints/{run_id}/{name}/export`。两者均须传入列表中的 `workspace_execution_id` 查询参数。下载返回 `.pt`，export 返回可用于 `input_artifacts` 的 checkpoint 制品引用。

CTDE-PPO 参数 `checkpoint_interval_steps` 为 0 时仅保留最佳 checkpoint，正数时额外按累计采样步数间隔保存；实际在跨过间隔后的批次更新结束时落盘。页面可直接下载、用于测试或继续训练。继续训练的目标总轮数应大于已完成轮数。
