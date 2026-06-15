# sim_server 指标 (metrics) 与事件 (event) 流设计

> 版本:v3(2026-06-15 重写)
> 状态:**设计稿** (未实施)
> v2 问题:漏掉了传输层的致命 bug——`sim_server` 的 `asyncio.Queue` 是单消费者模型,SSE 断线重连或多订阅者时丢数据。本文按"传输层 → 业务层 → 前端订阅层 → 绘制层"四层重构。

---

## 一、背景与总原则

把仿真的 per-step 指标、episode 汇总、热力图、关键事件,从 `sim_server`(engine 容器)一路推到前端并可选展示。

**四层架构**:

| 层 | 职责 | 关键文件 |
|---|---|---|
| **L1 传输层** | sim_server 内 frame/metrics/events 的生产、广播、快照;SSE 多订阅者安全 | `sim_server.py`、`DockerProxy.py` |
| **L2 业务层** | metrics 字段定义、events 派生逻辑、episode 状态机 | `sim_server.py`(MetricsHub 已具备) |
| **L3 前端订阅层** | FactoryConnectionManager 统一订阅、monitorStore adapter、工厂切换清理 | `factoryConnection.js`、`monitor.js`、`DockerFactoryManage.vue` |
| **L4 绘制层** | 指标注册表 + 配置驱动图表 + 卡片 + 热力图 | `metricsRegistry.js`(新)、`MetricsPanel.vue` |

**原则**:
1. L1 必须先修通——否则上层全是空中楼阁。
2. 后端推送格式利用现有 `MetricsHub`,不重造数据。
3. 前端统一订阅入口 = `FactoryConnectionManager`,统一存储 = `factoryStore`(frame)+ `monitorStore`(metrics/events)。
4. 字段映射在 `monitorStore` 做(新增 sim adapter),不破坏 PacketFactory 旧路径。
5. 可选展示:用户能关闭指标面板,不影响仿真。

---

## 二、L1 传输层修复(最关键)

### 2.1 致命 bug:`asyncio.Queue` 单消费者

**现状**(`finalpro/SkyEngine/sim_server.py:199`):

```python
self._state_queue: asyncio.Queue = asyncio.Queue()
self._metrics_queue: asyncio.Queue = asyncio.Queue()
```

`_run_loop` 每步 `queue.put(event)`,SSE handler `queue.get()` 消费。问题:
1. **单消费者**:第一个连接的 SSE handler 把队列抽干,第二个连接拿不到历史数据。
2. **断线丢数据**:SSE 断线期间生产的 frame 永远留在队列里(或被前一个 handler 消费掉),重连后看不到。
3. **stopped 后 break**:`stream_state` 的 `generate()` 在 `status=="stopped"` 后 `break`,导致 stopped 事件只发给当时在线的那一个订阅者。
4. **晚连接看不到初始状态**:仿真已 running 才打开页面,看不到当前 frame。

### 2.2 方案:广播 + 快照 + 节流

`SimulationManager` 改为持有"最新状态快照"+ "订阅者队列集合",每步推给所有订阅者。

```python
class SimulationManager:
    def __init__(self):
        # ...
        # L1: 广播模型
        self._latest_frame: dict | None = None        # 最新 frame 快照
        self._latest_metrics: dict | None = None       # 最新 per-step metrics 快照
        self._episode_summary: dict | None = None      # episode 末汇总(持久)
        self._heatmaps: dict | None = None             # episode 末热力图(持久)
        self._state_subs: set[asyncio.Queue] = set()   # state 订阅者
        self._metrics_subs: set[asyncio.Queue] = set() # metrics 订阅者
        self._events_subs: set[asyncio.Queue] = set()  # events 订阅者
        self._episode_status: str = "idle"  # idle | running | stopped

    def _broadcast(self, subs: set, event: tuple):
        """向所有订阅者的 queue 投递事件(非阻塞,满了就丢最旧)。"""
        for q in list(subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()  # 丢最旧
                    q.put_nowait(event)
                except Exception:
                    pass

    def subscribe(self, kind: str) -> asyncio.Queue:
        """新订阅者:拿到一个 bounded queue + 立即收到最新快照。"""
        q = asyncio.Queue(maxsize=200)
        subs = {"state": self._state_subs, "metrics": self._metrics_subs, "events": self._events_subs}[kind]
        subs.add(q)
        # 立即补发快照(晚连接者能看到当前状态)
        if kind == "state" and self._latest_frame:
            q.put_nowait(("state", {"status": self._episode_status, "frame": self._latest_frame}))
        if kind == "metrics":
            if self._episode_status == "stopped" and self._episode_summary:
                q.put_nowait(("metrics", {"status": "stopped", "episode_summary": self._episode_summary, "heatmap": self._heatmaps}))
            elif self._latest_metrics:
                q.put_nowait(("metrics", self._latest_metrics))
        return q

    def unsubscribe(self, kind: str, q: asyncio.Queue):
        subs = {"state": self._state_subs, "metrics": self._metrics_subs, "events": self._events_subs}[kind]
        subs.discard(q)
```

`_run_loop` 每步改为:
```python
frame = self._build_frame()
self._latest_frame = frame
self._latest_metrics = {"status": "running", "step": self.step, "metrics": infos.get("metrics", {}), "metrics_reward": infos.get("metrics_reward", 0.0)}
self._broadcast(self._state_subs, ("state", {"status": "running", "frame": frame}))
self._broadcast(self._metrics_subs, ("metrics", self._latest_metrics))
# events diff(见 L2)
self._diff_and_emit(frame)
```

SSE handler 改为订阅模型:
```python
@app.get("/stream/state")
async def stream_state():
    q = manager.subscribe("state")
    async def generate():
        try:
            while True:
                event_type, data = await q.get()
                yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                if data.get("status") == "stopped":
                    # 不 break:继续保留连接,前端可选择关闭。
                    # 但 stopped 后不再有新事件,前端收到后自行断开即可。
                    pass
        finally:
            manager.unsubscribe("state", q)
    return StreamingResponse(generate(), media_type="text/event-stream", headers={...})
```

> **注意**:stopped 事件广播后,所有在线订阅者都收到。新连接的订阅者在 `subscribe()` 里通过快照补发也能看到 episode_summary。这样彻底解决断线重连和晚连接问题。

### 2.3 episode 状态机

```python
# create → idle
# play   → idle → running
# stop/reset → running → stopped(广播 stopped 事件后置 _episode_status="stopped")
# play(again) → stopped → running(清空 _episode_summary/_heatmaps 快照)
```

`_episode_status` 与 `_broadcast` 配合,确保 `subscribe()` 时返回的快照状态正确。

### 2.4 DockerProxy 透传层修复

`DockerProxy.py` 的 `state_stream` / `metrics_stream` 用 `httpx` 流式拉 engine 容器的 SSE 再透传给浏览器。现状没问题(纯透传),但需确认:
- engine 容器重启后 `_streaming` flag 复位(否则后续连接以为还在流)。
- 新增 `events_stream()`(仿 `metrics_stream:318`)透传 `/stream/events`。

### 2.5 节流(可选,防止前端被 frame 淹)

若 step 频率太高(>10 fps),可在 `subscribe()` 返回的 queue 上做合并:连续多个 running frame 只保最新。一期不做,先跑通。

---

## 三、L2 业务层

### 3.1 数据源(已具备,不动)

`sky_executor.grid_factory.factory.Metrics.MetricsHub`:

| 维度 | 字段 | collector |
|---|---|---|
| FJSP | `machine_utilization`、`machine_non_processing_time_mean`、`machine_load_variance`、`operation_queue_waiting_time_mean` | `collectors/fjsp.py` |
| MAPF | `swap_conflict_count`、`tasked_stationary_count`、`agv_loaded_utilization`、`agv_busy_utilization`、`agv_travel_time_total`、`agv_waiting_time_total` | `collectors/mapf.py` |
| Coupling | `transport_delay_ratio`、`transport_blocking_delay_mean`、`machine_waiting_for_inbound_transfer_ratio` | `collectors/coupling.py` |
| Episode | `completed_makespan`、`full_makespan`、`success_rate` / `job_completion_rate` | `coupling.episode_summary()` |
| 热力图 | `transit`、`occupancy`、`obstacles` | `mapf_get_heatmaps()` |
| Reward | `metrics_reward` | `RewardCalculator.compute()` |

per-step 走 `infos['metrics']`,episode 末走 `get_episode_summary()` + `get_heatmaps()`。

### 3.2 `/stream/metrics`(配合 L1 改造)

per-step:`status:"running"` + `metrics` + `metrics_reward` + `step`。
episode 末:`status:"stopped"` + `episode_summary` + `heatmap`。广播给所有订阅者,并持久化到 `_episode_summary` / `_heatmaps` 供晚连接者补发。

### 3.3 新增 `/stream/events`

从 frame 状态变化 diff 派生事件。`SimulationManager` 加 `self._prev_frame = None`,`_diff_and_emit(frame)` 对比上一步快照:

```python
def _diff_and_emit(self, frame):
    if self._prev_frame is None:
        self._prev_frame = frame
        return
    prev = self._prev_frame

    # job 完成:某个 job 不再出现在任何 machine.current_op 和 active_transfers
    # (简化:靠 metrics 的 job_completion 变化判定,或 coordinator 回调)

    # machine 状态切换
    for m_cur, m_prev in zip(frame["machines"], prev["machines"]):
        if m_cur["current_op"] != m_prev["current_op"]:
            if m_prev["current_op"] and not m_cur["current_op"]:
                self._emit_event("machine_idle", {"machine_id": m_cur["id"]})
            if not m_prev["current_op"] and m_cur["current_op"]:
                self._emit_event("machine_start_op", {
                    "machine_id": m_cur["id"],
                    "job_id": m_cur["current_op"][0],
                    "op_id": m_cur["current_op"][1],
                })

    # transfer 开始/结束
    cur_tids = {t["task_id"] for t in frame["active_transfers"]}
    prev_tids = {t["task_id"] for t in prev["active_transfers"]}
    for tid in cur_tids - prev_tids:
        t = next(t for t in frame["active_transfers"] if t["task_id"] == tid)
        self._emit_event("transfer_started", {"task_id": tid, "job_id": t["job_id"], "source": t["source"], "destination": t["destination"]})
    for tid in prev_tids - cur_tids:
        self._emit_event("transfer_completed", {"task_id": tid})

    self._prev_frame = frame

def _emit_event(self, name: str, payload: dict):
    self._broadcast(self._events_subs, ("event", {"event": name, "step": self.step, "payload": payload}))
```

事件类型:
```python
{ "event": "machine_start_op",     "step": 45, "payload": {"machine_id": 3, "job_id": 1, "op_id": 2} }
{ "event": "machine_idle",         "step": 67, "payload": {"machine_id": 3} }
{ "event": "transfer_started",     "step": 12, "payload": {"task_id": 7, "source": [5,2], "destination": [10,6]} }
{ "event": "transfer_completed",   "step": 30, "payload": {"task_id": 7} }
{ "event": "alert",                "step": 88, "payload": {"type": "swap_conflict", "count": 3} }
```

新增路由 `GET /stream/events`(走 L1 的 `subscribe("events")`)。

### 3.4 frame 补字段(给"每机器负载 bar 图"用)

`_build_frame()` 的 `machines[i]` 当前只有 `id/location/current_op`。补 `total_work_time`、`processed_ops_count`(从 MetricsHub 或 machine 对象取)。

---

## 四、L3 前端订阅层

### 4.1 数据流(真实链路)

```
engine 容器 (sim_server.py)
   │  /stream/state  /stream/metrics  /stream/events   (L1 广播)
   ▼
backend 容器 (application/backend)
   ├─ DockerProxy.state_stream   透传 /stream/state
   ├─ DockerProxy.metrics_stream 透传 /stream/metrics
   └─ DockerProxy.events_stream  透传 /stream/events   (待新增)
   │  server.py 聚合三条 /stream/* 路由
   ▼
浏览器 (FactoryConnectionManager)
   ├─ onStateUpdate   → factoryStore.pushSnapshot        → historyBuffer(画画面)
   ├─ onMetricsUpdate → monitorStore.pushSimMetrics      → metricsTimeline / keyMetrics / heatmaps
   └─ onEventsUpdate  → monitorStore.pushSimEvent        → eventQueue(看日志)
```

**职责分工**:`factoryStore` 管"画画面"(frame),`monitorStore` 管"看指标/看日志"(metrics/events)。frame 不进 monitor。

### 4.2 FactoryConnectionManager 是唯一订阅入口

`application/frontend/src/utils/factoryConnection.js` 持有三条 SSE 连接:

```
FactoryConnectionManager
  ├─ connectState()   → /stream/state   → onStateUpdate   → factoryStore.pushSnapshot
  ├─ connectMetrics() → /stream/metrics → onMetricsUpdate → monitorStore.pushSimMetrics
  ├─ connectEvents()  → /stream/events  → onEventsUpdate  → monitorStore.pushSimEvent  (待加)
  └─ connectControl() → /stream/control
```

视图在 `onMounted` 创建实例、`onUnmounted` 调 `disconnect()`。

**当前已正确使用的视图**:
- `views/factory/FactoryManage.vue:107` — grid_factory 进程内模式
- `views/factory/PacketFactoryManage.vue:751` — PacketFactory

### 4.3 现状缺口

**缺口 1(关键):Docker 工厂模式绕过了连接器**

`views/factory/DockerFactoryManage.vue:194` 走 `executeDocker` → `scenarios/backendSystemTest.js:62`,后者**只订阅 `/stream/state`**,不订 metrics/events。后果:Docker 工厂模式下画面能动,**指标和事件一片空白**。

**方案**:`DockerFactoryManage.vue` 像 PacketFactoryManage 一样,在 `onMounted` 创建 `FactoryConnectionManager` 订阅三类 SSE。`backendSystemTest.js` **剥离订阅职责**(:61-96),只留控制命令序列(策略/重置/播放)。

**缺口 2:metrics 字段不匹配**

`monitorStore.pushMetrics`(`stores/monitor.js:59`)期望 PacketFactory 结构 `{machine,agv,job,keyMetrics}`,但 sim_server 推的是 raw snake_case 字段。需新增 `pushSimMetrics` adapter。

**缺口 3:events 端到端缺失**

后端无 `/stream/events`,DockerProxy 无 `events_stream`,`FactoryConnectionManager` 无 `connectEvents`,`utils/api.js` 无 `STREAM_EVENTS`。

**缺口 4:`isScenarioConnected` 守卫**

`factoryConnection.js:78,108,163` 仅当 `isScenarioConnected=true` 才建 metrics/control/events 连接。Docker 模式下需确保该状态为 true。

### 4.4 FactoryConnectionManager 扩展

`utils/factoryConnection.js`:
- `connections` 加 `events: null`(:13-17)
- 新增 `connectEvents()`(仿 `connectMetrics:162`,endpoint 用 `STREAM_EVENTS`)
- `init()` 调 `connectEvents()`(受 `isScenarioConnected` 守卫)
- `disconnect()`(:255)清 `events`
- `getStatus()`(:271)加 events 状态

### 4.5 utils/api.js 加常量

```js
STREAM_EVENTS: "/stream/events",   // 加在 api.js:31 之后
```

### 4.6 monitorStore 新增 sim adapter(不破坏 pushMetrics)

`stores/monitor.js` 新增:

```js
const heatmaps = ref({})
const episodeSummary = ref({})

function pushSimMetrics(data) {
    if (data.status === 'stopped') {
        episodeSummary.value = data.episode_summary || {}
        heatmaps.value = data.heatmap || {}
        return
    }
    const m = data.metrics || {}
    metricsTimeline.value.push({ step: data.step, timestamp: Date.now(), ...m })
    if (metricsTimeline.value.length > MAX_METRICS_TIMELINE)
        metricsTimeline.value = metricsTimeline.value.slice(-MAX_METRICS_TIMELINE)
    keyMetrics.value = {
        machine_utilization: { value: pct(m.machine_utilization), type: 'info' },
        agv_busy_utilization: { value: pct(m.agv_busy_utilization), type: 'success' },
        success_rate:         { value: pct(m.success_rate), type: 'warning' },
        makespan:             { value: m.full_makespan ?? '--', type: 'info' },
    }
}

function pushSimEvent(data) {
    pushEvent({
        title: data.event,
        message: JSON.stringify(data.payload),
        type: eventTypeToSeverity(data.event),
        idx: data.step,
    })
}
```

各工厂视图的 `onMetricsUpdate` handler 按来源分流:PacketFactory → `pushMetrics`;Docker/sim_server → `pushSimMetrics`。

---

## 五、L4 绘制层

> 起因:sim_server 每步推一个 flat 的 raw metrics 字典(十几个"裸"字段)。要做成多张可扩展的图表,且后续加图成本低。

### 5.1 现状:MetricsPanel.vue 的画图逻辑

| 关注点 | 实现 | 位置 |
|---|---|---|
| 图表库 | `vue-echarts`(`<v-chart>`)+ echarts 按需 `use([...])` | `MetricsPanel.vue:70-89` |
| 数据源 | `monitorStore.chartData = {machine,agv,job}`(三层,每层 `{labels,data}`) | `monitor.js:12-16` |
| option 生成 | `createMiniOption(type,data,color)` / `createBigOption(...)` | `:152, :171` |
| 图表实例 | **硬编码 3 个**:模板 3 个 `chart-item` + 3 个 computed + `openBigChart` if 分支 | `:23-51, :192-204, :211` |
| 卡片 | `metricsArray` 从 `keyMetrics` + 写死 `metricsMeta` 生成 | `:102, :108` |

### 5.2 问题:加一张图要改 4 处

按现状新增一张图(例如 `transport_delay_ratio` 时间序列),得同时改:模板加 `chart-item`、加 computed、`openBigChart` 加分支、`metricsMeta` 加条目。

### 5.3 方案:指标注册表 + 配置驱动

把所有 raw 指标的**元数据**集中到一个 registry,图表改成"给一个指标 key → 自动从 `metricsTimeline` 取时序 → 生成 option"。

**(1) 指标注册表**(新建 `application/frontend/src/utils/metricsRegistry.js`)

一处定义,卡片 / 图表 / RAG 三处复用:

```js
export const SIM_METRICS_REGISTRY = {
  machine_utilization: {
    label: '机器利用率', unit: '%', category: 'fjsp',
    chart: { type: 'line', color: '#667eea' },
    format: v => (v == null ? '--' : `${(v * 100).toFixed(1)}`),
  },
  agv_busy_utilization: {
    label: 'AGV 忙碌率', unit: '%', category: 'mapf',
    chart: { type: 'line', color: '#4CAF50' },
    format: v => (v == null ? '--' : `${(v * 100).toFixed(1)}`),
  },
  transport_delay_ratio: {
    label: '搬运延迟比', unit: '%', category: 'coupling',
    chart: { type: 'line', color: '#FF9800' },
  },
  swap_conflict_count: {
    label: 'AGV 冲突数', unit: '', category: 'mapf',
    chart: { type: 'bar', color: '#f56c6c' },
  },
  // —— 加新指标只需在此追加一条 ——
}

export const REGISTRY_BY_CATEGORY = Object.entries(SIM_METRICS_REGISTRY)
  .reduce((acc, [k, v]) => { (acc[v.category] ??= []).push(k); return acc }, {})
```

**(2) 时序取数**:`metricsTimeline`(`monitor.js:24`)每条已是 `{step, ...raw}`。给定 key:
```js
labels = metricsTimeline.map(t => t.step)
data   = metricsTimeline.map(t => t[key])
```

**(3) 配置驱动的图表**:MetricsPanel.vue 把硬编码的 3 个 chart-item 改成 `v-for`:

```js
const chartSpecs = ref([
  { metric: 'machine_utilization', title: '机器利用率趋势' },
  { metric: 'agv_busy_utilization', title: 'AGV 忙碌率趋势' },
  { metric: 'transport_delay_ratio', title: '搬运延迟比' },
])

const buildSeries = (metricKey) => {
  const reg = SIM_METRICS_REGISTRY[metricKey]
  if (!reg) return {}
  const tl = monitorStore.metricsTimeline
  return createMiniOption(reg.chart.type, tl.map(t => t[metricKey]), reg.chart.color)
}
```

```vue
<div v-for="spec in chartSpecs" :key="spec.metric" class="chart-item"
     @click="openBigChart(spec.metric)">
  <div class="chart-info">
    <span class="chart-title">{{ spec.title }}</span>
    <span class="chart-tag">{{ SIM_METRICS_REGISTRY[spec.metric].category }}</span>
  </div>
  <v-chart class="chart-canvas" :option="buildSeries(spec.metric)" autoresize />
</div>
```

`openBigChart(metricKey)` 统一走 registry + `createBigOption`,不再写 if 分支。

### 5.4 加新 chart 的步骤(改 2 处配置,零模板逻辑)

1. 在 `SIM_METRICS_REGISTRY` 加一条(label / category / chart.type / chart.color / format)。
2. 在 `chartSpecs` 加一条(指定要展示)。

### 5.5 卡片 / 图表 / RAG 共享 registry

| 消费方 | 现状 | 改成读 registry |
|---|---|---|
| 卡片 `metricsArray` | 写死 `metricsMeta`(`:108`) | `Object.entries(SIM_METRICS_REGISTRY)` 生成,label/format 统一 |
| 图表 option | 写死 3 computed | `buildSeries(metricKey)` |
| RAG `buildRAGContext`(`monitor.js:97`) | 硬编码 efficiency/utilization 统计 | 遍历 registry 按类别汇总 |

### 5.6 热力图(episode 末)

`heatmaps` ref(`pushSimMetrics` 在 `status=="stopped"` 时填充)。MetricsPanel 加一个 ECharts heatmap 组件,数据源 `heatmaps.transit` / `heatmaps.occupancy`,`heatmaps.obstacles` 作为底图掩码。

### 5.7 每机器负载 bar 图(从 frame 取)

`_build_frame` 补 `total_work_time` / `processed_ops_count` 后,frame 里的 `machines[i]` 可直接渲染水平 bar 图(横轴机器 id,纵轴 work_time)。数据源是 `factoryStore.historyBuffer` 最新帧,不走 metricsTimeline。

### 5.8 与 PacketFactory 旧路径隔离

registry 只服务 sim_server 路径(`pushSimMetrics` 喂的 `metricsTimeline`)。PacketFactory 的 `chartData:{machine,agv,job}` 结构和 `pushMetrics` 不动,旧 `MetricsPanel` 的三个 computed 保留为兼容入口(用 `v-if` 按 factoryType 切换两套渲染)。两套并存,互不污染。

### 5.9 字段映射表(raw → UI)

| sim_server 字段 | UI 含义 | 展示 |
|---|---|---|
| `machine_utilization` | 整体机器利用率 | 卡片 + 时间序列 |
| `machine_load_variance` | 负载均衡度 | 卡片 |
| `operation_queue_waiting_time_mean` | 平均排队等待 | 卡片 |
| `agv_loaded_utilization` / `agv_busy_utilization` | AGV 满载/忙碌率 | 卡片 + 时间序列 |
| `swap_conflict_count` | 冲突数 | 卡片 / 告警事件 |
| `transport_delay_ratio` | 搬运延迟比 | 时间序列 |
| `completed_makespan` / `full_makespan` | 完工时间 | 卡片(episode 末) |
| `success_rate` | 完成率 | 卡片(episode 末) |
| `heatmap.transit` / `occupancy` | AGV 热力图 | ECharts heatmap(episode 末) |
| `metrics_reward` | RL reward | 时间序列(可选) |
| frame.`machines[i].total_work_time` | 每机器负载 | bar 图(从 frame 取) |

---

## 六、改动清单(按层排序)

### L1 传输层

| # | 文件 | 改动 |
|---|---|---|
| 1 | `finalpro/SkyEngine/sim_server.py` | `asyncio.Queue` 改广播模型:`_state_subs/_metrics_subs/_events_subs` + `subscribe/unsubscribe/_broadcast`;`_latest_frame/_latest_metrics/_episode_summary/_heatmaps` 快照;`_episode_status` 状态机;SSE handler 改订阅模型 |
| 2 | `finalpro/SkyEngine/sim_server.py` | 新增 `_events_queue` + `_diff_and_emit` + `/stream/events` 路由;`_build_frame` 补 `total_work_time` |
| 3 | `application/backend/core/DockerProxy.py` | 新增 `events_stream()` 透传;确认 `_streaming` flag 在 engine 重启后复位 |
| 4 | `application/backend/server.py` | 新增 `/stream/events` 路由聚合 |

### L3 前端订阅层

| # | 文件 | 改动 |
|---|---|---|
| 5 | `application/frontend/src/utils/api.js` | 加 `STREAM_EVENTS` 常量(:31 后) |
| 6 | `application/frontend/src/utils/factoryConnection.js` | `connections` 加 `events`;新增 `connectEvents()`;`init`/`disconnect`/`getStatus` 同步 |
| 7 | `application/frontend/src/views/factory/DockerFactoryManage.vue` | `onMounted` 创建 `FactoryConnectionManager` 订阅三类 SSE,`onUnmounted` 断开;`onMetricsUpdate` 调 `pushSimMetrics` |
| 8 | `application/frontend/src/scenarios/backendSystemTest.js` | **剥离 SSE 订阅**(:61-96),只留控制命令序列 |
| 9 | `application/frontend/src/stores/monitor.js` | 新增 `pushSimMetrics` / `pushSimEvent` / `heatmaps` / `episodeSummary`;`buildRAGContext` 改读 registry |

### L4 绘制层

| # | 文件 | 改动 |
|---|---|---|
| 10 | `application/frontend/src/utils/metricsRegistry.js`(**新建**) | `SIM_METRICS_REGISTRY` + `REGISTRY_BY_CATEGORY` |
| 11 | `application/frontend/src/components/MetricsPanel.vue` | `chart-item` 改 `v-for` + `buildSeries`;卡片读 registry;`openBigChart` 统一化;加热力图组件;按 factoryType 切换 PacketFactory/sim 两套渲染 |
| 12 | `application/frontend/src/components/EventPanel.vue` | 接 `pushSimEvent` 数据源 |

---

## 七、实施分期

### 一期:L1 传输层修复 + metrics 端到端(最小可见)

**后端**:#1 #3 #4(L1 广播)+ 确认 DockerProxy 透传 metrics 已通。
**前端**:#5 #6 #7 #8 #9 #10 #11(MetricsPanel 现有图表先绑 `keyMetrics`)。
**确保**:Docker 模式 `isScenarioConnected=true`。
**验收**:Docker 工厂跑仿真,MetricsPanel 卡片实时显示 `machine_utilization` / `agv_busy_utilization`;**断线重连或晚打开页面能看到当前状态**(L1 广播生效)。

### 二期:events 端到端 + 完整图表

**后端**:#2(events diff + `/stream/events`)。
**前端**:`FactoryConnectionManager.connectEvents` 接线;`pushSimEvent`;MetricsPanel 时间序列 + 热力图 + 每机器 bar 图。
**验收**:EventPanel 看到 machine_start_op / transfer_started 事件;episode 结束热力图渲染。

---

## 八、验收标准(按层)

| 层 | 验收点 |
|---|---|
| L1 | (a) 仿真 running 时刷新页面,能看到当前 frame;(b) 两个浏览器标签同时看,都能持续收到 frame;(c) episode stopped 后新打开页面,能看到 episode_summary |
| L2 | metrics 字段齐全;events diff 不漏不重;`_diff_and_emit` 不拖慢 step |
| L3 | DockerFactoryManage 订阅 metrics/events 后,monitorStore 有数据;工厂切换时旧连接 `disconnect` 干净 |
| L4 | MetricsPanel 卡片/时间序列/热力图按 registry 渲染;加新指标只改 registry + chartSpecs |

---

## 九、风险与注意

- **L1 是地基**:不修广播模型,上层全是空中楼阁。一期必须先做 L1。
- **`asyncio.Queue(maxsize=200)`**:订阅者消费太慢(如浏览器后台标签)会丢旧 frame,但 `_latest_frame` 快照保证重连后能看到当前状态。丢中间帧可接受(动画本来就不必每帧都画)。
- **`_broadcast` 在 `_run_loop` 线程调用**:通过 `asyncio.run_coroutine_threadsafe` 调度到 self._loop 执行 `put_nowait`,或直接用 `call_soon_threadsafe`。注意线程安全。
- **字段命名**:sim_server 推 `snake_case`,前端 UI 也用 `snake_case`,不做 camelCase 转换(PacketFactory 旧路径不动)。
- **`isScenarioConnected` 守卫**:Docker 模式必须让 `/api/scenario/status` 返回 true,或调整 `factoryConnection.js:78` 守卫逻辑。
- **三流并发**:state + metrics + events 同时打开,浏览器同域 SSE 限 6 条,够用;`disconnect()` 必须三条都关。
- **事件 diff 别拖慢仿真**:`_diff_and_emit` 在 `_run_loop` 同线程,只做集合 diff,不做重计算。
- **两套 metrics 路径并存**:`pushMetrics`(PacketFactory)与 `pushSimMetrics`(sim_server)互不干扰,视图按来源分流。
- **backendSystemTest 剥离订阅后的兼容**:`executeBackend`(`useSimulationConfig.js:75`)同样走它,剥离订阅后 backend 模式也需由各自视图的 `FactoryConnectionManager` 订阅,确认 FactoryManage 已具备(已具备,`:107`)。
- **engine 镜像重建**:sim_server.py 运行在容器内,改完 finalpro 的 sim_server.py 后必须 `docker compose build engine` 才能生效。
