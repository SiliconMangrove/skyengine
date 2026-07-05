# 0627 StaticFactory 对齐容器化 SkyEngine 的难点盘点

> 范围：在 0627 日志方案定稿前，把"StaticFactory 全面对齐容器化 SkyEngine"这条原则落地时会遇到的真实难点逐一查实、列清。原则已定（不一致处依容器化），但落地时发现有些对齐**有信息损失或哲学张力**，需要单独决策。
>
> 关联：
> - `docs/explore/0627日志更新.md`（主方案）
> - `docs/explore/0627帧与事件相关设计.md`（engine 侧产出现状）
>
> 日期：2026-06-27

---

## 0. 总览

| 难点 | 类型 | 严重度 |
|---|---|---|
| A. 事件语义落差（叙事 vs diff-derived） | 哲学张力 | ★★★ |
| B. metrics 字段非对称损失 | 信息损失 | ★★★ |
| C. machine_load 柱状图存废 | B 的衍生 | ★★ |
| D. proxy 模式切换（pull → push） | 实现路径 | ★★ |
| E. summary 计算位置 | 一致性 | ★ |

---

## 一、难点 A：事件语义落差（最硬）

### 1.1 事实
- **容器化 events**：`{timestamp, type, message, level}`，type 是 **diff-derived 业务跃迁**（`machine_start_op / machine_idle / transfer_started / transfer_completed`），由 `_diff_and_emit`（`sim_server.py:302`）在状态机变迁时自动产出
- **StaticFactory events**：`{step, title, message, type}`，type 是**日志级别**（`info/success/task/error`），6 条事件全是作者预编排的叙事

StaticFactory 的 `EVENTS_LOG`（`StaticFactoryProxy.py:138-145`）逐条对照：

| step | title | 能套容器化 type 吗 |
|---|---|---|
| 0 | 系统就绪 | 勉强 `sim_started`（lifecycle） |
| 10 | 进入作业区 | ✗ 无对应 |
| 27 | 折肩点装载 | ✗ 容器化的 transfer 是 AGV 搬运，语义不同 |
| 36 | 设备告警（M1 过载） | ✗ 容器化无故障模型 |
| 43 | 故障排除 | ✗ 同上 |
| 54 | 任务完成 | 勉强 lifecycle |

6 条里 4 条套不上。

### 1.2 张力
"StaticFactory 全面对齐 SkyEngine"在事件这一项**会遇到本质障碍**：容器化事件是状态变迁的副产品（语义密集），StaticFactory 事件是预编排叙事（语义松散）。硬套业务 type = 削足适履，丢掉叙事信息；且容器化根本没有故障/告警模型，StaticFactory 的告警事件无处映射。

### 1.3 选项
- **(A) 承认 StaticFactory 事件是另一类**：canonical event 加 `type:"narrative"` 兜底 + `level` 表达严重度（info/success/warning/error）。偏离"全面对齐"，但保语义。
- **(B) 硬塞业务 type**：6 条强行归类，接受语义错位。严格对齐但脏。
- **(C) 重写 StaticFactory 剧本**：让它基于状态变迁产事件（machines 状态变 → 产 `machine_start_op`），即给 StaticFactory 长出一个"伪状态机"。最对齐，工作量最大，且告警类事件仍无对应。

**倾向 (A)**。事件是给人读的，语义比格式重要。但要用户拍板，因为这跟既定原则有冲突。

---

## 二、难点 B：metrics 字段非对称损失

### 2.1 事实（已查实）
容器化 per-step metrics（`MetricsHub.on_step_end`，`finalpro/SkyEngine/sky_executor/grid_factory/factory/Metrics/hub.py:62`）共 **13 个标量字段**：

| 字段 | 来源 | 语义 |
|---|---|---|
| `machine_utilization` | fjsp | 机器平均利用率 |
| `machine_non_processing_time_mean` | fjsp | 平均非加工时间 |
| `machine_load_variance` | fjsp | 各机器 work_time 方差 |
| `operation_queue_waiting_time_mean` | fjsp | 工序排队等待均值 |
| `swap_conflict_count` | mapf | AGV 交换冲突数 |
| `tasked_stationary_count` | mapf | 有任务未移动 AGV 数 |
| `agv_loaded_utilization` | mapf | AGV 载货行驶占比 |
| `agv_busy_utilization` | mapf | AGV 非空闲占比 |
| `agv_travel_time_total` | mapf | 总行驶时间 |
| `agv_waiting_time_total` | mapf | 总空闲时间 |
| `transport_delay_ratio` | coupling | 运输延迟比 |
| `transport_blocking_delay_mean` | coupling | 运输延迟均值 |
| `machine_waiting_for_inbound_transfer_ratio` | coupling | 被 AGV 饿住的机器占比 |

Episode 结束额外 4 字段：`completed_makespan / full_makespan / success_rate / job_completion_rate`。

### 2.2 关键缺口（容器化没有的）
- **无 per-machine breakdown**：只有 `machine_load_variance`（聚合方差），**没有逐机器负载数组**
- **无独立 `efficiency` 字段**：只有 `agv_loaded_utilization / agv_busy_utilization`（语义不完全等同）
- **per-step 无 job 字段**：makespan/success_rate 只在 episode summary

### 2.3 StaticFactory 现有 metrics 形态
`generate_metrics_data`（`StaticFactoryProxy.py:312`）：
```jsonc
{ "step": N,
  "machine": { "data": [m1,m2,m3,m4,m5], "labels": ["M1".."M5"] },   // ← 逐机器负载
  "agv": { "data": [...] },
  "job": { "data": [...] },
  "keyMetrics": { "efficiency": {value,type}, "utilization": {value,type} } }
```

### 2.4 损失清单（对齐到 canonical 会丢的东西）
| StaticFactory 现有 | 对齐后 | 命运 |
|---|---|---|
| `machine.data`（5 机器逐个负载） | 容器化无对应字段 | **丢失**（见难点 C） |
| `keyMetrics.efficiency` | 容器化无独立字段 | **丢失**（或映射到 agv_loaded_utilization，语义漂移） |
| `job.data` | 容器化 per-step 无 job | **丢失** |
| `keyMetrics.utilization` | → `machine_utilization` | ✓ 保住 |

### 2.5 张力
"对齐 SkyEngine"在 metrics 这一项**不是纯收益**：容器化在 AGV 维度更富（10 个 AGV 字段），但 StaticFactory 在 per-machine / efficiency / per-step job 维度更富。**对齐是双向取舍，不是单向靠拢**。需要决定：是让 canonical 严格等于容器化（StaticFactory 丢维度），还是 canonical 在容器化基础上**扩字段**容纳 StaticFactory 的 per-machine？

---

## 三、难点 C：machine_load 柱状图的存废（B 的衍生）

### 3.1 事实
`machine_load` hook（`application/frontend/src/hooks/metrics/machine_load.js:4`）注释自己写明：
> "sim_server: 暂无此字段，series 为空（自然降级）"

这个 hook **现在只靠 StaticFactory 的 `machine.data + labels` 活着**，容器化侧本来就是空图。`static_factory.json:10` 看板配置里它挂在 StaticFactory 看板上。

### 3.2 对齐后果
如果 StaticFactory 按 3.3 去掉 `machine:{data,labels}`，这个柱状图**彻底没数据**。

### 3.3 选项
- **(a) hook 改派生源**：从 `frame.machines[].queue_length` 派生负载（两通路都能用）。但语义从"机器负载%"变成"排队长度"，图表标题要改。
- **(b) 砍掉这个柱状图**：从看板配置删掉。
- **(c) canonical metrics 扩字段**：加一个 `per_machine_load: [...]` 数组。破"canonical = 容器化输出"的纯净性，但保住可视化。

**倾向 (a)**：frame.machines 本就有 per-machine 信息（status/current_op/queue_length），从 frame 派生比给 metrics 硬塞数组更干净，且两通路同构。但要承认语义会变。

---

## 四、难点 D：proxy 模式切换幅度

### 4.1 事实
- **DockerProxy**：push 模式（`state_stream / metrics_stream / events_stream`，async generator）
- **StaticFactoryProxy**：pull 模式（`get_state_events / get_metrics_events / get_control_events`，被 server.py 轮询调用）
- server.py 用 `hasattr(proxy, "state_stream")` 判断走哪条（`server.py:118/167/216`）

### 4.2 events 独立流要求 StaticFactory 加 push
难点 A 的解决方案要求 events 走独立流。StaticFactory 现在连 events SSE 都没有（`/stream/events` 对它返回 "No event source"，`server.py:221`）。

### 4.3 选项
- **(a) 整体切 push**：StaticFactory 三条流都改成 `*_stream` async generator，与 DockerProxy 同构。server.py 的 `hasattr` 分支天然覆盖，**server.py SSE 路由零改动**。改动量：重写 StaticFactoryProxy 的流式产出。
- **(b) 只给 events 加 push**：state/metrics 留 pull，events 新增 push。server.py 要为 StaticFactory 的 events 单开处理逻辑。改动量：StaticFactory 小改 + server.py 中改。

**倾向 (a)**：长痛不如短痛，整体切 push 后三条通路（含未来 engine）都同构，server.py 不用维护 pull/push 双分支。

---

## 五、难点 E：summary 计算位置

### 5.1 事实
- 前端 `analysisLog.js:36` 有 `computeSummary()`（compute jobs/utilization/event_counts）
- 0627 方案说后端 `complete_run` 时算 summary

### 5.2 风险
两份 `computeSummary` 各自演化会漂移（前端算的 avg_utilization 与后端算的可能口径不同）。

### 5.3 选项
- **(a) 后端为权威，前端删掉**：summary 只在后端 complete_run 时算一次，写进 RunData.summary。前端读现成。
- **(b) 后端算 + 前端兜底**：后端没传 summary 时前端补算。容错好但有漂移风险。

**倾向 (a)**：单一权威，前端 analysisLog 的 computeSummary 删除。

---

## 六、综合判断：对齐的边界

"StaticFactory 全面对齐 SkyEngine"这条原则在 **frame 结构、machines list、events 字段 shape、独立 events 流** 这些点上是对的、可落地的。

但在两个维度上**不是单向靠拢，而是双向取舍**：
1. **事件语义**（难点 A）：容器化是 diff-derived 业务跃迁，StaticFactory 是预编排叙事。硬对齐丢语义。
2. **metrics 维度**（难点 B）：容器化 AGV 维度更富，StaticFactory per-machine/efficiency 维度更富。对齐会丢 StaticFactory 的部分指标。

**建议修正原则**：frame 结构 / 通道架构严格对齐容器化；**events 词汇表与 metrics 字段集走"容器化为基线 + 必要时扩字段"**，承认 StaticFactory 有容器化没有的合法维度（叙事事件、per-machine 负载），不要为了纯净性砍掉。

---

## 七、待决策清单

| # | 决策点 | 选项 | 倾向 |
|---|---|---|---|
| 1 | 事件语义（难点 A） | (A) narrative 兜底 / (B) 硬套 / (C) 伪状态机 | **A** |
| 2 | canonical metrics 边界（难点 B） | 严格容器化 / 容器化+扩字段 | **容器化+扩字段**（保 per-machine） |
| 3 | machine_load 柱状图（难点 C） | (a) frame 派生 / (b) 砍 / (c) metrics 扩字段 | **a** |
| 4 | proxy 模式（难点 D） | (a) 整体切 push / (b) 只 events 加 push | **a** |
| 5 | summary 位置（难点 E） | (a) 后端权威 / (b) 双份 | **a** |

**决策顺序建议**：先定 #1 和 #2（决定 canonical schema 最终形态），#3 是 #2 衍生，#4/#5 是实现细节最后过。

#1 和 #2 一旦定了，0627 主方案就能收敛定稿。
