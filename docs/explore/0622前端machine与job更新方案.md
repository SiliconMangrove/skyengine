# 0622 前端 Machine 与 Job 更新方案

> 范围：模块①（Machine & Job 富 frame）在前端的落地形态——Machine 用 Hover 富信息 Tooltip 跟随机器渲染、Job 用 AGV 风格卡片列表塞进 AgentPanel 顶部。
> 关联：
> - `docs/explore/0622更新设计.md`（StaticFactory 静态剧本三模块总设计）
> - `docs/explore/0619更新设计.md`（DockerProxy frame schema 原始定义，模块① §1.2）
> - `application/frontend/src/components/FactoryVisualization3D.vue`（Hover Tooltip 实现）
> - `application/frontend/src/components/AgentPanel.vue`（Job 卡片列表实现）
> 日期：2026-06-22

---

## 0. 目标与决策

### 0.1 目标
把模块①的富 frame（`machines[*].current_op` + `queue_length` + 顶层 `jobs[]`）用合理的可视化方式暴露给用户，避免两个极端：
- 极简：只渲染名称色块，丢失 op 进度 / 队列 / Job 关联等关键信息
- 极繁：所有信息常驻显示，画面挤满卡片挡视线

### 0.2 关键决策
经过方案对比（Hover Tooltip / 侧栏联动 / 3D 进度环 / 组合方案），最终选定：

| 模块 | 展示位置 | 触发方式 | 视觉形态 |
| --- | --- | --- | --- |
| Machine | `FactoryVisualization3D` 画布上、紧贴机器名 | 鼠标 Hover | 常态色点 + 名称；Hover 富信息卡片 |
| Job | `AgentPanel` 顶部（在 AGV 区上方） | 常驻可折叠 | AGV 风格卡片列表 |

**核心原则：渐进暴露**。常态信息密度低（不挡视线），按需展开详细数据。

---

## 一、Machine：Hover 富信息 Tooltip

### 1.1 两态设计

**常态**（默认）：
```
   M1  ●       ← 名称 + 状态色点（6px 圆点）
```

色点按机器状态变色：
- WORKING → 蓝色发光（`#64b5ff`，带 box-shadow）
- IDLE → 灰色（`#888`）
- BROKEN → 红色脉冲（`#ff6464`，`@keyframes pulse` 呼吸动画）
- BLOCKED → 橙色发光（`#ffb450`）
- 无数据 → 不显示点

**Hover 态**（鼠标悬停在名称命中区）：
```
╭─ M1 ───────────────╮
│ M1      [● WORKING] │   ← 头部：名称 + 状态徽章
│                     │
│ 当前 Op   Job1·Op1  │   ← 当前工序
│                     │
│ Op 进度       4/15  │   ← 进度区
│ ▓▓▓░░░░░░░░░░░░     │   ← 可视化进度条
│ ─────────────────── │
│  2    3    2/3      │   ← 三栏统计
│ 队列  已完工 Job进度 │
╰─────────────────────╯
```

Tooltip 内容字段：

| 区块 | 字段 | 来源 |
| --- | --- | --- |
| 头部 | name, statusLabel, statusClass | `dyn.id` / `dyn.status` |
| 当前 Op | job_id, op_id | `dyn.current_op.{job_id, op_id}` |
| Op 进度 | step_done / proc_time / opPct | `dyn.current_op.step_done / proc_time` |
| 队列 | queueLength | `dyn.queue_length` |
| 已完工 | finishedOps | 从 `jobs[*].ops[*]` 反查 `status==FINISHED && assigned_machine==本机` |
| Job 进度 | index_in_job + 1 / total_in_job | `dyn.current_op.{index_in_job, total_in_job}` |

### 1.2 实现要点

#### a) 3D→2D 坐标投影（每帧重算）

```js
const vec = group.position.clone()
vec.y += GRID_SIZE * 0.7        // 抬高一点，标签飘在机器上方
vec.project(camera)             // 世界坐标 → NDC (-1..1)
if (vec.z > 1) return           // 在相机背后，跳过
x: (vec.x * 0.5 + 0.5) * cw     // NDC → 像素
y: (-vec.y * 0.5 + 0.5) * ch
```

每帧 `animate()` 里调一次 `updateScreenLabels()`，相机转动时标签自动跟随。

#### b) 三级兜底查表（修复 mesh id ↔ frame key 错位）

发现的 bug：mesh 用 config id 作 key（如 `MACHINE_1_1`），但 StaticFactory frame 用 `M1/M2/M3`，永远对不上。

```js
const dyn = machineStatesDict[id]              // 1) 精确匹配
  || machineStatesDict[`M${id}`]              // 2) 字符串前缀
  || machineStateList[curIndex]               // 3) 按 mesh 迭代顺序 index 对齐
  || {}
```

第三级 `index 对齐` 是兜底的兜底：只要 mesh 数量和 frame machine 数量对得上，按顺序一一对应。`updateMachineStates`（机器主体着色）也用同一套兜底。

#### c) Hover 不抖动的两个关键

**问题**：鼠标悬停 → tooltip 显示 → tooltip 占据空间 → 触发 mouseleave → tooltip 消失 → 抖动循环。

**修复 a：tooltip 不吃鼠标事件**
```css
.machine-tooltip { pointer-events: none; }
```
鼠标"穿"过 tooltip，hover 状态由底下的命中区决定。

**修复 b：扩大命中区但视觉不变**
```css
.label-hitbox {
  padding: 4px 6px;
  margin: -4px -6px;   /* 负 margin 抵消 padding，视觉位置不变 */
}
```
小目标（10px 字体）原生命中中区太小；padding 扩大命中区，负 margin 把视觉位置拉回原位。

#### d) Hover 状态管理

```js
const hoveredMachineId = ref(null)

function onLabelEnter(l) {
  if (l.kind === 'machine' && l.machineId != null) {
    hoveredMachineId.value = l.machineId
  }
}
function onLabelLeave(l) {
  if (l.kind === 'machine' && l.machineId === hoveredMachineId.value) {
    hoveredMachineId.value = null
  }
}
```

模板 `v-if="l.machineId === hoveredMachineId"` 控制显隐，响应式自动渲染。

#### e) 已完工 Op 数反查

```js
const finishedOpsByMachine = {}
jobs.forEach(job => {
  ;(job.ops || []).forEach(op => {
    if (op.status !== 'FINISHED' || op.assigned_machine == null) return
    const key = `M${op.assigned_machine}`
    finishedOpsByMachine[key] = (finishedOpsByMachine[key] || 0) + 1
  })
})
```

每帧重算（jobs 数据量小，开销可忽略），统计实时准确。

### 1.3 样式规范

**Tooltip 主样式**：
- 宽 `min-width: 200px`
- 背景：`linear-gradient(180deg, rgba(20,26,48,0.95), rgba(10,14,28,0.95))`
- 边框：`1px solid rgba(100,180,255,0.3)`（按状态变色，BROKEN 红 / BLOCKED 橙）
- 圆角：`8px`
- 阴影：`0 8px 24px rgba(0,0,0,0.5)`
- `backdrop-filter: blur(10px)` 玻璃质感
- 入场动画：`@keyframes mtFadeIn 0.12s ease-out`

**进度条**：
- 高 `6px`，圆角 `3px`
- 背景：`rgba(255,255,255,0.06)`
- 填充：`linear-gradient(90deg, #64b5ff, #4a90e2)` 蓝渐变
- `transition: width 0.3s ease` 平滑过渡

**状态徽章配色**：
| 状态 | 背景色 | 文字色 |
| --- | --- | --- |
| WORKING | `rgba(100,181,255,0.22)` | `#64b5ff` |
| IDLE | `rgba(160,160,160,0.18)` | `#a0a0a0` |
| BROKEN | `rgba(255,100,100,0.22)` | `#ff6464` |
| BLOCKED | `rgba(255,180,80,0.22)` | `#ffb450` |

---

## 二、Job：AgentPanel 顶部卡片列表

### 2.1 展示形态

在 AgentPanel 原有 AGV 区上方新增一个 Job 区，结构镜像 AGV 区：

```
▼ 📦 Job 状态           2/3  ⚠️ 1     ← 可折叠 header + 计数器
┌──────────────────────────┐
│ 📦 Job 1         [3/3]   │   ← 已完成卡片
│ 进度 ▓▓▓▓▓▓ 3/3          │
│ Op  [0][0][3]            │   ← PENDING/PROCESSING/FINISHED 计数
│ 交期 t20    完工 t52     │
├──────────────────────────┤
│ 📦 Job 2     [2/3 ⚠️]    │   ← 超期卡片（红色边）
│ 进度 ▓▓▓░░░░ 2/3         │
│ Op  [0][1][2]            │
│ 交期 t20 (超期)          │
└──────────────────────────┘
```

### 2.2 卡片字段

| 字段 | 显示 | 数据源 |
| --- | --- | --- |
| Job 标识 | `📦 Job {job_id}` | `job.job_id` |
| 状态标签 | `已完成` / `{done}/{total}` / `超期` | `job.is_completed` / `job.progress` / `overdue` |
| 进度条 | 可视化填充 + `done/total` 文字 | `job.progress.{done,total}` |
| Op 状态 | 三色块 `[P][R][F]` 计数 | `job.ops` 按 status 分组 |
| 交期 | `t{due}` 超期标红 | `job.due` + `stepNum > due` 判定 |
| 完工时间 | `t{completion_time}` | `job.completion_time` |

### 2.3 Op 三色块（密度压缩技巧）

```
[0] [1] [2]    ← 灰 / 蓝 / 绿
PEN PRO FIN
```

每个色块显示对应状态的 op 数量，一眼看出 Job 处在哪个阶段，比纯文字 PENDING/PROCESSING/FINISHED 信息密度高。

```js
const opCount = (job.ops || []).reduce((acc, op) => {
  const k = (op.status || 'PENDING').toLowerCase()
  acc[k] = (acc[k] || 0) + 1
  return acc
}, { pending: 0, processing: 0, finished: 0 })
```

### 2.4 汇总计数器

Header 右侧显示：
```
2/3  ⚠️ 1
```
- `2/3`：已完成 / 总数
- `⚠️ 1`：超期数（红色，仅 >0 时显示）

```js
const jobSummary = computed(() => {
  let done = 0, total = 0, overdue = 0
  jobList.value.forEach(j => {
    total += 1
    if (j.is_completed) done += 1
    if (j.overdue) overdue += 1
  })
  return { done, total, overdue }
})
```

### 2.5 静态预览数据降级

未开仿真时也能看到效果，加 `JOB_PREVIEW` 常量：

```js
const liveJobs = store.currentState?.jobs || []
const rawJobs = liveJobs.length > 0 ? liveJobs : JOB_PREVIEW
const stepNum = envTimelineStep() ?? 25   // 默认 25 便于演示超期
```

`JOB_PREVIEW` 包含 3 条样例 Job（进行中超期 / 进行中 / 已完成），覆盖所有视觉分支。

### 2.6 与 AGV 区的协调

- 默认折叠：`jobCollapsed = ref(true)`，与 AGV 区一致
- 点击 header 切换：`@click="jobCollapsed = !jobCollapsed"`
- 视觉样式完全复用 `.agent-card` / `.agent-header` / `.agent-counter`，只加 Job 专属的 `.job-card` 修饰类

---

## 三、Store 联动选中

Job 卡片点击调用 `store.selectJob(jobId)`，与 Machine 双向联动：

```js
// factory.js
const selectedJobId = ref(null)
const selectedMachineKey = ref(null)

function selectJob(jobId) {
  selectedJobId.value = selectedJobId.value === jobId ? null : jobId
}
function selectMachine(key) {
  selectedMachineKey.value = selectedMachineKey.value === key ? null : key
}
```

后续扩展方向（未实现）：
- Hover machine 时，对应 Job 卡片高亮（`selectedJobId === op.job_id`）
- 点击 Job 卡片，画布上对应 machine 边框高亮
- 这是模块①"双向联动"的预留接口

---

## 四、与旧方案的对比

| 维度 | 旧方案（tab + 写死面板） | 新方案（Hover Tooltip + 卡片） |
| --- | --- | --- |
| Machine 入口 | 独立 `machines` tab，切换才能看 | 画布直接 Hover，零切换 |
| Job 入口 | 独立 `jobs` tab | AgentPanel 顶部常驻 |
| 信息密度 | 固定高密度 | 渐进暴露，常态低密度 |
| 视觉遮挡 | 列表永远占空间 | 仅 Hover 时浮出，不挡视线 |
| 与画布联动 | 弱（独立 tab） | 强（Tooltip 紧贴机器） |
| 视觉风格 | 自成一派 | 复用 AGV 卡片样式，统一 |

---

## 五、设计纪律

1. **数据契约单一权威**：所有字段来自模块① frame schema（0619 §1.2），前端不自创字段
2. **前端零分支**：StaticFactory / DockerFactory 共用同一套 Tooltip + Job 卡片，差异只在 store 归一化层
3. **降级而非崩溃**：
   - `current_op == null` → 显示"空闲"
   - 无 frame → Job 用预览数据，Machine 不显示色点
   - mesh key 对不上 → 三级 fallback
4. **渐进暴露**：常态色点 + 名称 → Hover 才显示富信息，避免视觉过载
5. **样式复用**：Job 卡片复用 AGV 的 `.agent-card` 基类，只加 Job 修饰类

---

## 六、后续可演进方向

### 6.1 已埋点未实现
- machine ↔ Job 双向高亮联动（`selectedMachineKey` / `selectedJobId` 已就位）
- Tooltip 点击固定（再点别处才消失），便于查看动态变化的进度

### 6.2 待评审
1. 是否给 Tooltip 加"查看完整 Job 时间轴"链接，跳到独立详情页
2. BROKEN 状态除了脉冲点，是否在 Tooltip 里加故障原因 / 持续时长
3. Job 卡片是否支持批量勾选 → 批量操作（重排、取消等）
4. Tooltip 在边缘机器上是否会溢出画布（需要加 viewport 边界判定，自动翻转方向）

### 6.3 复刻到 DockerProxy
前端这套**完全不用动**，只改 sim_server 的 `_build_frame` 让它按同 schema 产 machines + jobs。具体：
- `machines[*]` 字段对齐：`status` / `current_op` 对象 / `queue_length`
- 顶层 `jobs[]` 字段对齐：`job_id` / `progress` / `ops[*]`（含 status / proc_time / assigned_machine / arrive_machine_at / start_process_at / finish_process_at / wait_for_machine_time）
- 前端 `normalizeSnapshot` 已做 list/dict 双形态兼容，sim_server 输出 list 也能直接吃
