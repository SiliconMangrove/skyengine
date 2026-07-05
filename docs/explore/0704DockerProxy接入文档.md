# DockerProxy 接入文档

> 日期：2026-07-04
> 范围：`finalpro/SkyEngine`（容器化引擎）通过 `DockerProxy` 接入 `grouppro/skyengine`（平台层）的完整契约与启动流程
> 关联文档：`docs/explore/0701日志更新.md`（canonical schema 权威）、`docs/explore/0701日志详细设计.md`、`finalpro/SkyEngine/explore/0701diff_and_emit升级.md`

---

## 1. 架构总览

```
┌─────────────────────────────┐       ┌──────────────────────────┐       ┌─────────────────────────┐
│  前端 (Vue)                 │       │  平台后端 (FastAPI)      │       │  Engine 容器 (sim_server)│
│  DockerFactoryManage.vue    │ SSE   │  DockerProxy             │ 透传  │  finalpro/SkyEngine     │
│  ├─ FactoryPlayerSSE        │◄──────┤  ├─ state_stream()       │◄──────┤  /stream/state          │
│  ├─ factoryStore            │       │  ├─ metrics_stream()     │       │  /stream/metrics        │
│  └─ monitorStore            │       │  └─ events_stream()      │       │  /stream/events         │
│      ├─ pushSimMetrics      │       │                          │ HTTP  │                         │
│      └─ pushSimEvent        │       │  initialize()/start()    │──────►│  /sim/create + /sim/play│
└─────────────────────────────┘       └──────────────────────────┘       └─────────────────────────┘
                                          │ docker compose
                                          ▼
                                     engine / fjsp / mapf 三容器（sim-net 互联）
```

三层职责：

| 层 | 仓库 | 职责 |
|---|---|---|
| **前端展示** | `grouppro/skyengine/application/frontend` | 消费 canonical 事件/状态/指标，渲染 EventPanel / DashboardPanel / FocusPanel |
| **平台代理** | `grouppro/skyengine/application/backend/core/DockerProxy.py` | 用 `docker compose` 启停 engine 容器，零加工透传三条 SSE 流 |
| **容器化引擎** | `finalpro/SkyEngine` (分支 `0704insert`) | `sim_server.py` 跑仿真，DiffEmitter 派生 canonical 事件 |

---

## 2. 数据契约 — Canonical Event Envelope

所有事件（diff 派生 + lifecycle）走统一信封，权威定义在 `sky_executor/utils/diff.py:make_envelope`：

```python
{
    "step":      int,             # 仿真步序号
    "timestamp": "T+<step>s",     # canonical 字符串
    "category":  str,             # 见 §2.2 词表
    "type":      str,             # 业务事件类型，必须在 TYPE_REGISTRY 登记
    "level":     "info" | "success" | "warning" | "error",
    "title":     str,             # 短标题（用于聚合卡片）
    "message":   str,             # 长描述（用于日志列表）
    "payload":   dict,            # 结构化附加数据（可为空 {}）
}
```

### 2.1 SSE wire format

```
event: event
data: {"step":12,"timestamp":"T+12s","category":"machine_op","type":"machine_start_op","level":"info","title":"...","message":"...","payload":{...}}
```

### 2.2 TYPE_REGISTRY 词表

type → (category, default level)。未登记的 etype 会被 `make_envelope` 降级为 `system / warning`，避免事件流无声漂移。

| type | category | level | 来源 |
|---|---|---|---|
| `sim_started` | lifecycle | info | `emit_lifecycle` |
| `episode_completed` | lifecycle | success | `emit_lifecycle` |
| `episode_truncated` | lifecycle | warning | `emit_lifecycle` |
| `machine_start_op` | machine_op | info | R1 diff |
| `machine_idle` | machine_op | info | R1 diff |
| `transfer_started` | transfer | info | R2 diff |
| `transfer_completed` | transfer | success | R2 diff |
| `transfer_destination_resolved` | transfer | info | R2 diff |
| `op_finished` | machine_op | success | R3 diff |
| `job_completed` | job | success | R4 diff |
| `job_released` | job | info | R4 diff |
| `agv_assigned` | agv | info | R5 diff |
| `agv_freed` | agv | info | R5 diff |
| `deadline_risk` | risk | warning | R6 阈值（唯一 stateful） |

> 新增事件类型：① 在 `diff.py §A TYPE_REGISTRY` 登记；② §C 写规则函数；③ §D 进 `default_rules` 列表；④ 前端 `EventPanel.vue:eventIconMap` 加图标。

---

## 3. SSE 三流协议

三条流相互独立，每条都是 `text/event-stream`，每个连接独立订阅、断开自动 unsubscribe。

| 流 | engine 端点 | 用途 | 透传方法 |
|---|---|---|---|
| **state** | `GET /stream/state` | 完整 frame 快照（machines / jobs / grid_state / active_transfers） | `DockerProxy.state_stream` |
| **metrics** | `GET /stream/metrics` | metrics 指标时序点 + episode 汇总 + 热力图 | `DockerProxy.metrics_stream` |
| **events** | `GET /stream/events` | canonical event envelope 流 | `DockerProxy.events_stream` |

- engine 监听 `0.0.0.0:8080`
- 容器 `8080:8080` 暴露到宿主机，DockerProxy 通过 `${ENGINE_URL:-http://localhost:8080}` 访问
- 透传实现：`httpx.AsyncClient.stream("GET", url)` + `aiter_lines()`，零队列缓冲
- `data.status === "stopped"` 触发 `state_stream` 自动收尾（`_streaming=False`）

---

## 4. 控制端点

`sim_server.py` 暴露的 HTTP 控制端点：

| 端点 | 方法 | 作用 |
|---|---|---|
| `/health` | GET | 存活探测，DockerProxy `initialize` 阶段轮询 |
| `/sim/create` | POST | `manager.create(config)`，config 含 `config / fjsp_algorithm / mapf_algorithm / solver_assign` |
| `/sim/play` | POST | `manager.play()`，启动 `_run_loop` |
| `/sim/pause` | POST | 暂停 |
| `/sim/reset` | POST | 重置（episode 边界，调用 `DiffEmitter.reset()`） |
| `/sim/stop` | POST | 停止 |
| `/sim/state` | GET | 返回 `{step, running}`（轻量探测，非 frame 快照） |

---

## 5. 启动流程（DockerProxy 两阶段）

### 5.1 `initialize()` — Phase 1

```python
await DockerProxy.initialize()
# 1. docker compose -f docker-compose-online.yaml up -d engine
# 2. 轮询 http://localhost:8080/health 直到 ok（超时抛错）
# 3. status = IDLE
```

### 5.2 `start(sim_config)` — Phase 2

```python
await DockerProxy.start(sim_config)
# 1. docker compose up -d mapf fjsp          # 算法容器
# 2. POST /sim/create  {config, fjsp_algorithm, mapf_algorithm, solver_assign}
# 3. POST /sim/play
# 4. status = RUNNING，_streaming=True
```

> **关键不变量**：必须等 `/health` 通过再 `/sim/create`，否则 `_run_loop` 第一步 `coordinator.decide()` 会因算法容器未就绪失败。

### 5.3 `stop()` — 销毁

```python
await DockerProxy.stop()
# docker compose down（容器/网络/卷全部清理）
```

---

## 6. docker-compose 配置要点

`finalpro/SkyEngine/docker-compose-online.yaml`：

```yaml
services:
  engine:
    build: { context: ., dockerfile: online.dockerfile }
    ports: ["8080:8080"]
    volumes:
      - ${DATA_DIR:-./dataset}:/dataset:ro
      - ./sim_server.py:/app/sim_server.py:ro       # bind mount：改源码无需 rebuild
      - ./sky_executor:/app/sky_executor:ro
      - ./sky_logs:/app/sky_logs                     # 可写：JSONL 落盘目标（待实现）
      - ./config:/app/config:ro
    environment:
      DATA_DIR: "/dataset"
      SKY_LOG_DIR: "/app/sky_logs"
    networks: [sim-net]

  fjsp: { ... image: ${FJSP_IMAGE}, networks: [sim-net] }
  mapf: { ... image: ${MAPF_IMAGE}, networks: [sim-net] }
```

- 三容器同在 `sim-net`，通过容器名 DNS 互联（`http://fjsp:8002`）
- `sim_server.py` / `sky_executor` / `config` 走 bind mount → 改源码 `docker compose restart engine` 即可
- `sky_logs` 是可写卷，预留给 JSONL sink

---

## 7. 前端接入点

`DockerFactoryManage.vue`（factory_id = `grid_factory_new`）：

```js
// 1. 三条 SSE 在 play 之前建立（避免 play 超时阻塞 SSE）
sseConnectionId       = sseManager.connect(stateUrl,   { onMessage: ... })
metricsConnectionId   = sseManager.connect(metricsUrl, { onMessage: ... })
eventsConnectionId    = sseManager.connect(eventsUrl,  { onMessage: ... })

// 2. 事件流 → monitorStore.pushSimEvent(envelope) → canonical 字段直接进 eventQueue
// 3. 指标流 → monitorStore.pushSimMetrics(data)    → metricsTimeline 累积
// 4. 状态流 → factoryStore.pushSnapshot(frame)     → historyBuffer 入帧
```

`monitorStore` 的 `pushSimEvent` / `pushSimMetrics` 已 canonical-ready：

- `pushSimEvent(data)` 接 `{timestamp, type, message, level}`，转 canonical event 入队
- `pushSimMetrics(data)` 区分 `status==="running"`（时序点）vs `status==="stopped"`（汇总 + 热力图）

---

## 8. 已知 Gap（不阻塞接入）

| Gap | 影响 | 修复路径 |
|---|---|---|
| **JSONL 事件落盘未实现** | AnalysisService 离线归档闭环缺数据源；实时展示不受影响 | 在 `sim_server._on_diff_event` 里追加几行：把 envelope 追加写到 `${SKY_LOG_DIR}/${run_id}/events.jsonl` |
| **DockerFactoryManage episode 结束未调用 `analysisLog.finalizeFromStores()`** | episode 完成后不入库历史样本 | episode `status==="stopped"` 时调用 finalize |
| **部分 hook 在容器化侧降级为空图** | `machine_load` 等 per-machine 维度，容器化 metrics 没有该字段 | canonical 决策为「容器化为基线」，hook 内 `.?? 0` 容错即可 |

---

## 9. 接入验证（Smoke Test）

### 9.1 引擎独立验证

```bash
cd /data1/home/wuhao/project/finalpro/SkyEngine
uv run python -u sim_server.py &
curl http://localhost:8080/health                      # → {"status":"ok"}
curl -N http://localhost:8080/stream/events            # 应看到 lifecycle + diff 事件
```

### 9.2 平台接入验证

1. 启动平台后端：`uv run uvicorn application.backend.server:app --reload`
2. 前端进 `grid_factory_new` 工厂（走 DockerProxy）
3. 点「执行」→ 看 EventPanel 是否有 `sim_started` / `machine_start_op` / `transfer_started` 等事件流入
4. DashboardPanel 应有 machine_util / agv_activity 等时序曲线
5. episode 结束应看到 `episode_completed` 事件 + 热力图

### 9.3 失败排查清单

| 症状 | 排查点 |
|---|---|
| `/health` 不通 | engine 容器是否起、端口映射、`ENGINE_URL` 环境变量 |
| events 流空 | `_run_loop` 是否启动、`_events_subs` 是否有订阅者、DiffEmitter 是否 emit |
| 事件 type 显示为 system/warning | type 未在 `TYPE_REGISTRY` 登记，被 `make_envelope` 降级 |
| 算法容器连接失败 | `sim-net` 网络、容器名 DNS（`http://fjsp:8002`）、算法镜像 |
| 前端 EventPanel 不更新 | `DockerFactoryManage` 是否建立 events SSE、`pushSimEvent` 字段映射 |

---

## 10. 版本对齐快照

| 组件 | 仓库 / 分支 / 文件 | 状态 |
|---|---|---|
| `diff.py` | `finalpro/SkyEngine@0704insert` `sky_executor/utils/diff.py` | ✅ R1-R6 + lifecycle 完整 |
| `sim_server.py` | 同上 | ✅ DiffEmitter 集成，三流齐全 |
| `DockerProxy.py` | `grouppro/skyengine` `application/backend/core/DockerProxy.py` | ✅ 三流透传 + 两阶段启动 |
| `monitor.js` | `grouppro/skyengine/application/frontend/src/stores/monitor.js` | ✅ canonical pushSimEvent/pushSimMetrics |
| `DashboardPanel` + 8 hooks | `application/frontend/src/components/DashboardPanel.vue`、`hooks/metrics/` | ✅ 配置驱动 |
| `EventPanel` | `application/frontend/src/components/EventPanel.vue` | ✅ type/level 渲染 |
| `DockerFactoryManage.vue` | `application/frontend/src/views/factory/DockerFactoryManage.vue` | ✅ SSE 接入（缺 finalize） |

---

**结论**：Engine / 代理 / 前端三层契约已对齐，DockerProxy 可正式接入。剩余 gap 仅影响离线归档闭环，不阻塞实时展示。
