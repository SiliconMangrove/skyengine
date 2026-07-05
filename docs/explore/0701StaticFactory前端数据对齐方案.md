# 0701 StaticFactory 前端数据对齐方案

> 范围：StaticFactory 的仿真数据 100% 在浏览器内由前端剧本 `application/frontend/src/scenarios/fullSystemTest.js` 产出。本方案盘点"前端剧本产出 shape"与"DockerProxy 透传的容器化 SkyEngine canonical shape"之间的落差，给出文件级改动清单。
>
> 关联：
> - `docs/explore/0627帧与事件相关设计.md`（canonical 定义）
> - `docs/explore/0627日志更新.md`（对齐总原则）
> - `application/backend/core/StaticFactoryProxy.py`（**已改占位**，0701）
> - `application/frontend/src/scenarios/fullSystemTest.js`（前端剧本，真实数据源）
>
> 日期：2026-07-01

---

## 0. 背景：StaticFactory 的真实数据通路

```
StaticFactoryManage.vue:138   mode: "frontend"
   → useSimulationConfig.executeFrontend()
   → runFullSystemTest(store, monitorStore, ...)
       ├─ factoryStore.pushSnapshot(snapshot)   // state 帧
       ├─ monitorStore.pushMetrics(payload)     // metrics 点
       └─ monitorStore.pushEvent(payload)       // event
```

后端 `StaticFactoryProxy.py` 已降为占位（仅保留 `/analysis/export` mock 路由）。**所有 shape 改动都集中在前端**，主要在 `fullSystemTest.js`，连带 `monitor.js` 的 push 入口与若干 hook。

---

## 一、canonical（DockerProxy 透传 shape）

### 1.1 Frame（state 流，每步一条）
```jsonc
{
  "timestamp": "T+{step}s",
  "env_timeline": "<string>",
  "grid_state": { "positions_xy": [[x,y],...], "is_active": [bool,...] },
  "machines": [
    { "id": 1, "location": [x,y], "status": "WORKING"|"IDLE",
      "current_op": {...}|null, "queue_length": 0 }
  ],
  "jobs": [ { "job_id":.., "is_completed": bool, "progress":{done,total}, "ops":[...] } ],
  "active_transfers": [ { "task_id":.., "job_id":.., "source":[..], "destination":[..]|null } ]
}
```
关键：`machines` 是 **list**、有 `env_timeline`、有 `jobs`、frame **不带** `events` 字段。

### 1.2 Metrics（每步一条）
```jsonc
{ "status": "running", "step": 42, "metrics": { "machine_utilization": 0.42, ... }, "metrics_reward": 0.0 }
```
关键：扁平 `metrics` 子字典，**没有** `machine/agv/job/keyMetrics` 展示结构。

### 1.3 Event（仅跃迁时）
```jsonc
{ "timestamp": "T+42s", "type": "machine_start_op", "message": "M3 开始 Job2-Op1", "level": "info" }
```
关键：`type` 是业务名（`machine_start_op / transfer_started / ...`），`level` 是 `info/success/warning/error`，**没有 `title`**。

---

## 二、当前 StaticFactory 剧本 shape（fullSystemTest.js 现状）

| 维度 | 当前 shape | 与 canonical 的差异 |
|---|---|---|
| Frame machines | `{M1:{id,status,load}, M2:.., M3:..}` **dict** | ① dict vs list ② 缺 `location` / `current_op` / `queue_length` |
| Frame 顶层 | `{timestamp, grid_state, machines, active_transfers}` | 缺 `env_timeline`、缺 `jobs` |
| Frame 内 events | 无（事件走 monitor） | ✓ 已对齐（canonical frame 不带 events） |
| Metrics point | `{step, machine:{data,labels}, agv:{data}, job:{data}, keyMetrics:{...}}` | 完全不同的展示型结构 |
| Event payload | `monitor.pushEvent({title, message, type(级别), idx})` | 内部存储 shape `{id, timestamp:Date, idx, title, message, type}`，与 canonical `{timestamp, type(业务), message, level}` 不一致 |

---

## 三、文件级改动清单

### 3.1 `scenarios/fullSystemTest.js`（主战场）

**(a) `MACHINE_STATES` 改造**
- 保留 5 个阶段化负载/load 剧本逻辑（这是 StaticFactory 的"剧情"，不是数据格式问题）
- 但 `machines` 从 dict 改为 **list**，每个元素补齐 canonical 字段：
  ```js
  // 输出形如：
  [
    { id: "M1", location: [x, y], status: "WORKING"|"IDLE"|"BROKEN",
      current_op: {...}|null, queue_length: 0, load: <保留，StaticFactory 私有> }
  ]
  ```
- `location`：从 StaticFactory 拓扑静态取（M1/M2/M3 各一个固定坐标，与 3D 场景对齐）
- `current_op` / `queue_length`：需要引入 JOB_SCRIPT（与 0622 后端版同构），按 step 派生
- **id 保留字符串 `"M1"`**（canonical 只约束"list + 有 id 字段"，不强类型）

**(b) 新增 `JOB_SCRIPT` 常量 + `generateJobs(step)`**
- 移植 0622 后端 `FactorySimulator.generate_jobs` 的逻辑到 JS
- 3 Job × 3 Op，确定性，与 AGV 折返点 / machine 阶段对齐
- J1 故意超期 / J2 准时 / J3 无交期
- `generateJobs(step)` 纯函数，输出 canonical `jobs[]`

**(c) frame 推送改造**
```js
const snapshot = {
  timestamp: `T+${step}s`,
  env_timeline: String(step),              // ← 新增
  grid_state: { positions_xy, is_active },
  machines: machineList,                   // ← list（来自新 MACHINE_STATES）
  jobs: generateJobs(step),                // ← 新增
  active_transfers: [],
  // 不带 events
}
factoryStore.pushSnapshot(snapshot)
```
> 注：`stores/factory.js:148 normalizeSnapshot` 已经做 machines `list→dict` 兜底，所以 machines 改 list 后，所有 `machines["M1"]` 的旧消费点**零改动**继续工作。

**(d) `METRICS_DATA` 改造为 canonical**
- 废弃 `machine/agv/job/keyMetrics` 展示结构
- 输出 `{ status:"running", step, metrics:{...}, metrics_reward:0 }`
- `metrics` 子字典里至少包含：`machine_utilization`（hook 必用）、可枚举的 `efficiency` 等
- StaticFactory 是 mock，可从现有 `effVal/utilVal` 直接派生填入

**(e) metrics 推送改造**
```js
monitorStore.pushMetrics({
  status: 'running',
  step,
  metrics: { machine_utilization: utilVal/100, ... },
  metrics_reward: 0,
})
```

**(f) `EVENTS_LOG` 改造 + 推送**
- shape 从 `{step, title, message, type(级别)}` 改为按 canonical 触发：
  ```js
  const EVENTS_LOG = [
    { step: 0,  type: 'sim_started',        level: 'info',    message: 'AGV #1 已上线，坐标 (5, 2)，等待指令' },
    { step: 10, type: 'narrative',          level: 'info',    message: '进入作业区：AGV 到达上料缓冲区 (Y=11)，M1 开始预热' },
    { step: 27, type: 'narrative',          level: 'info',    message: '折返点装载：AGV 到达最远端 (16, 4)' },
    { step: 36, type: 'narrative',          level: 'warning', message: '设备告警：M1 主轴过载 (Load 99%)，触发安全停机' },
    { step: 43, type: 'narrative',          level: 'success', message: '故障排除：M1 重启完成，系统恢复' },
    { step: 54, type: 'episode_completed',  level: 'success', message: 'AGV 返回原点 (5, 2)，作业流程结束' },
  ]
  ```
- 原 `title` 字段**并入 message**（canonical 无 title）
- 推送时调 `monitorStore.pushEvent({ type, level, message, idx: step, timestamp: \`T+${step}s\` })`

### 3.2 `stores/monitor.js`

**(a) `pushEvent` 改造**
当前签名：`pushEvent({title, message, type, idx})` → 存 `{id, timestamp:Date, idx, title, message, type}`

改成接收 canonical 输入并存 canonical 内部表示：
```js
function pushEvent(payload) {
  const { type = 'narrative', level = 'info', message = '', idx = 0, timestamp = null } = payload
  const newEvent = {
    id: Date.now() + Math.random(),
    timestamp: timestamp || `T+${idx}s`,   // 优先用 canonical 的 "T+xs"
    idx,
    type,         // 业务名（用于 icon 映射时需扩展）
    level,        // info/success/warning/error
    message,
  }
  eventQueue.value.push(newEvent)
  ...
}
```
**注意**：废弃 `title` 字段。但 `EventPanel.vue` 模板里 `event.title` 要相应改。

**(b) `pushMetrics` 改造**
当前：拆 `data.machine/agv/job/keyMetrics` 入 `chartData/keyMetrics`，timeline 累积 `{efficiency, utilization, machine, agv, job}`。

改成接收 canonical：
```js
function pushMetrics(data) {
  // data: { status, step, metrics:{...}, metrics_reward }
  // 累积到 timeline
  metricsTimeline.value.push({
    step: data.step,
    status: data.status,
    metrics: data.metrics || {},
    metrics_reward: data.metrics_reward ?? 0,
    timestamp: Date.now(),   // 内部用，不参与 canonical
  })
  ...
}
```
废弃 `chartData.machine/agv/job` 与 `keyMetrics`（看下方 3.4 谁在消费，再决定是否真删）。

### 3.3 `components/EventPanel.vue`

模板里有：
- `event.title` → 改为直接显示 `event.message`（或把 message 拆成"首句作标题 + 余下作详情"，但成本高，建议直接 message）
- `event.type` 用于 `event-{type}` css 类与 `getEventIcon(type)` → 改为用 `event.level` 做样式，icon 映射从"业务 type"改为"level + 已知 type"复合映射

`getEventIcon` 映射表（line 126）扩展：
```js
const eventIconMap = {
  // level 兜底
  info: 'ℹ️', success: '✅', warning: '⚠️', error: '❌',
  // 已知业务 type 专属 icon（可选精修）
  sim_started: '🚀', episode_completed: '🏁',
  narrative: '📌',
  machine_start_op: '⚙️', machine_idle: '💤',
  transfer_started: '🚛', transfer_completed: '📦',
}
function getEventIcon(ev) { return eventIconMap[ev.type] || eventIconMap[ev.level] || 'ℹ️' }
```

### 3.4 `hooks/metrics/_normalize.js`

当前为兼容两种 metrics shape 而存在（sim_server 直通 vs StaticFactory 扁平字段解析）。

fullSystemTest 改 canonical 后，**两条通路产出同 shape**，归一化层失去意义。**建议直接删除 `normalizePoint/normalizeMetrics`**，各 hook 直接读 `ctx.metrics[].step / .metrics.xxx`。

但删之前要扫一遍所有 hook 的写法，把 `normalizeMetrics(ctx.metrics)` 替换为直接 `ctx.metrics`。受影响 hook：`machine_util.js / machine_load.js / efficiency.js / agv_stats.js`（前两者已读 `metrics.machine_utilization`，只是经过 normalize 一层）。

### 3.5 `hooks/metrics/machine_load.js`

当前从 `metrics.machine.data + labels` 派生（StaticFactory 独有，canonical 无）。

改从 **frame** 派生：
```js
series: (ctx) => {
  const frames = ctx.frames || []
  // 经 normalizeSnapshot 后 machines 是 dict {"M1":{...}}
  const labels = frames.length > 0
    ? Object.keys(frames[frames.length - 1]?.machines || {})
    : []
  return labels.map((label, i) => {
    const data = frames.map((f, idx) => {
      const m = (f.machines || {})[label]
      return typeof m?.load === 'number' ? { x: idx, y: m.load } : null
    }).filter(Boolean)
    return { name: label, data, color: DEFAULT_COLORS[i % DEFAULT_COLORS.length] }
  })
}
```
- y 值用 `load`（StaticFactory 原生就是 load%，保语义；canonical 的 `queue_length` 在 StaticFactory 里也派生了，可作第二选项）
- 容器化通路 frame.machines 里有 `queue_length` 没 `load`，这条 hook 在容器化通路下 series 仍为空（自然降级，与现状一致）

### 3.6 `hooks/metrics/machine_util.js` / `efficiency.js` / `agv_stats.js`

- `machine_util` / `efficiency`：去掉 `normalizeMetrics`，直接 `ctx.metrics.map(p => ({x: p.step, y: (p.metrics?.machine_utilization ?? 0) * 100}))`
- `agv_stats`：canonical metrics 没有逐项 agv 字段；这条 hook 在 StaticFactory 下原本依赖 `metrics.agv.data`，canonical 化后会失数据。**两种处理**：
  - (a) 从 frame.grid_state 派生（活跃 AGV 数 / 总数等）
  - (b) canonical metrics 里补 `agv_loaded_utilization / agv_busy_utilization`（与容器化对齐），hook 改读这些字段
  - **倾向 (b)**，与容器化通路同构

### 3.7 `stores/analysisLog.js`

**不动**。`finalizeFromStores` 已通过深拷贝从 `factoryStore.historyBuffer + monitorStore.metricsTimeline + monitorStore.eventQueue` 取数据。pushEvent/pushMetrics 改 shape 后，archive 的 run 数据自动跟着 canonical 化。

`computeSummary` 里 `metrics?.machine_utilization` 路径已经匹配 canonical，无需改。

### 3.8 其他消费点（确认无改动）

| 文件 | 现状 | 是否需改 |
|---|---|---|
| `stores/factory.js:148 normalizeSnapshot` | 已 list→dict 兜底 | ❌ 不动 |
| `MachineStatusPanel.vue` | 读 `store.currentState.machines` dict | ❌ 不动（normalize 后仍 dict） |
| `FactoryVisualization.vue / 3D.vue` | 读 machines dict | ❌ 不动 |
| `DashboardPanel.vue` | 喂 ctx 给 hook，不直接读 metrics 字段 | ❌ 不动 |
| `AnalysisOverlayPanel.vue` | 喂 archive ctx 给 hook | ❌ 不动 |
| `JobStatusPanel.vue` | 读 `frame.jobs[]` | ❌ 不动（但当前 fullSystemTest 不产 jobs，**对齐后才有数据**，需验证） |

---

## 四、待决策项（动手前需拍板）

| # | 决策点 | 选项 | 倾向 |
|---|---|---|---|
| 1 | machines id 类型 | string `"M1"` / int `1` | **string**（StaticFactory 原生，canonical 不强约束） |
| 2 | machine_load hook 的 y 值 | `load`（%）/ `queue_length`（数） | **load**（保 StaticFactory 语义，容器化侧自然降级） |
| 3 | agv_stats hook 数据源 | frame 派生活跃数 / canonical metrics 补 agv 字段 | **canonical metrics 补字段**（`agv_loaded_utilization` 等，与容器化对齐） |
| 4 | `_normalize.js` | 删 / 留 defense-in-depth | **删**（两通路 shape 统一后是死代码） |
| 5 | EventPanel 是否保留 title 显示 | 直接 message / 拆首句 | **直接 message**（canonical 无 title，拆句成本高） |
| 6 | monitor.chartData / keyMetrics（旧展示型字段）是否删 | 删 / 保留兼容 | **看消费方**：若仅老 MetricsPanel 用且已下线，删；否则保留 |

---

## 五、建议落地顺序

| 阶段 | 内容 | 验证 |
|---|---|---|
| **P1** | `fullSystemTest.js` 改 canonical metrics + env_timeline + jobs + machines list | 启动 StaticFactory → 看板图表不崩、`machine_util` 曲线正常 |
| **P2** | `monitor.js pushMetrics/pushEvent` 改 canonical 入口 | metricsTimeline shape 统一，eventQueue shape 统一 |
| **P3** | `EventPanel.vue` 适配新 event shape（去掉 title 依赖，icon 用 level+type 复合） | 事件 tab 显示正常，告警/成功着色正确 |
| **P4** | 各 hook 去掉 `_normalize` 依赖，`machine_load` 改 frame 派生 | 7 个 hook 在 StaticFactory 下都有合理输出 |
| **P5** | 删 `_normalize.js` + 清理 `monitor.js` 旧 chartData/keyMetrics（若决策 6 = 删） | 代码瘦身，无残留死代码 |
| **P6** | 验证 `analysisLog` archive 路径（跑完 episode → 浮层详情图表正常） | 离线分析样本图表复用 hook 正常 |

P1–P3 是必做闭环；P4 是 hook 维度补齐；P5 是清理；P6 是回归。

---

## 六、不做的事（明确排除）

- ❌ 后端 `StaticFactoryProxy.py` 已是占位，不再动
- ❌ server.py 不改、history 模块不接（StaticFactory 保持纯前端内存）
- ❌ 容器化 sim_server 侧改动（在 `finalpro/SkyEngine`，不在默认范围）
- ❌ 声明式 DiffEngine（同上，且非对齐必需）
- ❌ 上传日志分析入口（按钮已预留 disabled，保持）

---

## 七、对齐完成后的收益

1. **两通路 shape 统一**：StaticFactory（前端剧本）与容器化 SkyEngine（DockerProxy）产出同形 frame/metrics/event
2. **hook 单一数据源**：去 `_normalize` 中间层，hook 直接读 canonical 字段
3. **frame 富化**：补 `env_timeline` / `jobs[]` / `current_op` / `queue_length`，让 `JobStatusPanel` 在 StaticFactory 下也有数据（当前是空的）
4. **事件词汇表对齐**：StaticFactory 6 条事件按 canonical `{type, level, message}` 表达，告警类事件用 `warning` level（容器化侧目前只有 info/success，未来扩 warning/error 时天然兼容）
5. **代码瘦身**：删 `_normalize.js`、`StaticFactoryProxy.py` 死代码已减 480 行
