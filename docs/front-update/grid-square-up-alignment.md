# 地图相关半格偏移：前后端网格尺寸不一致

> 本文档取代同目录 `factory-theme-half-grid-offset.md` 的结论。早期把偏移归因于"厂房背景层与内容层坐标约定不一致"——该因素恒定存在、不随地图变化，**不是本问题的根因**。真正的触发条件是地图本身的尺寸，详见下文。

## 一、现象

`grid_factory` 选不同地图时，3D 场景是否偏移半格**与具体地图强相关**：

| 地图 | 尺寸 (宽×高) | 偏移 |
|------|-------------|------|
| `01-random / validation-random-seed-001` | 19 × 19 | ❌ 不偏移 |
| `01-random / validation-random-seed-002` | 21 × 18 | ✅ 偏移半格 |

切换主题、刷新都复现，且偏移只出现在某一个方向（较短轴 Z）。

## 二、根因

### 2.1 pogema 只支持正方形网格

`application/backend/joint_sim/io/use_io.py:56-69`

```python
grid_width  = topology.get("gridWidth", 20)
grid_height = topology.get("gridHeight", 20)
grid_size = max(grid_width, grid_height)   # ← 取 max 方正化

return GridConfig(
    size=grid_size,   # ← pogema 的 GridConfig 只收单个 size，恒为正方形
    ...
)
```

pogema 的 `GridConfig` 只接受单个 `size` 参数，**只能跑正方形网格**。后端用 `max(w, h)` 强行方正：

- seed-001 (19×19) → 后端实际跑 **19×19**（天然方正，无需 padding）
- seed-002 (21×18) → 后端实际跑 **21×21**（底部补 3 行空白 padding）

### 2.2 前端按原生 w×h 渲染，未方正化

`application/frontend/src/components/FactoryVisualization3D.vue:62-74`

```js
const topology = computed(() => {
  const config = props.staticConfig || {}
  const topoData = config.topology || config
  return {
    zones: topoData.zones || [],
    machines: topoData.machines || {},
    waypoints: topoData.waypoints || {},
    edges: topoData.edges || [],
    gridWidth: topoData.gridWidth || props.defaultGridWidth,   // ← 原生 w
    gridHeight: topoData.gridHeight || props.defaultGridHeight, // ← 原生 h
  }
})
```

前端把 `gridWidth / gridHeight` 原样当作仿真尺寸。对非正方形地图，前端用 21×18、后端用 21×21，**两边对同一坐标 `gy` 映射到不同世界坐标**。

### 2.3 为什么表现为"半格"

内容坐标公式（`FactoryVisualization3D.vue:82`）：

```js
(gx - w/2 + 0.5) * GRID_SIZE
```

- seed-002 Z 方向：前端 h=18（偶）→ cell 中心落在**半整数**；GridHelper 用 `max(21,18)=21`（奇）→ 网格线也落在**半整数** → 内容中心**压在网格线上**，视觉读成"偏了半格"。
- seed-001：w=h=19 同奇偶 → 永远匹配，不偏。

> 注：GridHelper 奇偶错配只是表象放大器。**底层病灶是前后端网格尺寸语义不一致**；奇偶性决定了这个病灶是否恰好以"半格"形式肉眼可见。

### 2.4 早期假说为何被排除

"厂房背景层（立柱/围墙）用 edge 坐标、内容层用 cell-center 坐标，差 0.5"——这个 0.5 差是**恒定**的，对所有地图都一样，无法解释"随地图切换"。背景层用 `halfW=w*U/2`、`halfH=h*U/2` 各自独立，也不引入奇偶耦合。故非本问题根因。

## 三、影响评估

非正方形地图在前端按原生 w×h 渲染会带来三类问题：

1. **padding 区渲染缺失**：后端 21×21 的底部 3 行（y=18,19,20）在前端 h=18 的网格里没有对应格子。AGV 走进 padding 行时，`gridToWorld(gy=19) = (19 - 9 + 0.5) = 10.5`，落在预期网格之外。
2. **Z 坐标整体错位**：后端 21×21 语义下 `gy` 的世界坐标，与前端 21×18 语义下同一 `gy` 的世界坐标不同，所有 Z 方向内容系统性偏移。
3. **视觉半格偏移**：奇偶错配时内容压在网格线上，肉眼可见。

正方形地图（w=h）因前后端尺寸天然一致，从不暴露。

## 四、修复方案

### 方案 A（推荐）：前端 topology 方正化

在 `FactoryVisualization3D.vue` 的 `topology` computed 里做一次 `max(w,h)`，和后端 `use_io.py:58` 对齐：

```js
const topology = computed(() => {
  const config = props.staticConfig || {}
  const topoData = config.topology || config
  const gw = topoData.gridWidth  || props.defaultGridWidth
  const gh = topoData.gridHeight || props.defaultGridHeight
  const size = Math.max(gw, gh)   // ← 与后端 grid_size = max(w,h) 一致
  return {
    zones: topoData.zones || [],
    machines: topoData.machines || {},
    waypoints: topoData.waypoints || {},
    edges: topoData.edges || [],
    gridWidth: size,
    gridHeight: size,
  }
})
```

效果：
- seed-002 前端也用 21×21 → GridHelper(21,21) 与内容同奇偶 → 不偏移。
- padding 的 3 行有正常格子，AGV/障碍渲染对位。
- 正方形地图不受影响（max=w=h）。
- 无需把 `GridHelper` 换成矩形 `LineSegments`——正方形下 `GridHelper` 本就正确。

**风险**：低。改动局部，仅影响渲染层的尺寸解释；后端逻辑不动。需确认 padding 行的视觉表现（空白格）符合预期。

### 方案 B：后端显式暴露仿真尺寸

后端在 snapshot / config 里增加 `simGridSize = max(w,h)` 字段，前端直接读它，而非自己算 max。

- 优点：语义最干净，前端不猜后端逻辑。
- 代价：前后端都要改，且要在所有下发 topology 的链路上补字段。

### 方案 C（不推荐）：前端画矩形网格

把 `GridHelper` 换成按 w、h 各自独立画线的 `LineSegments`。

- 问题：**会让前后端尺寸不一致更严重**。前端忠实画 21×18 矩形，但后端 AGV 跑在 21×21 上，padding 行的 AGV 会飞出矩形或错格。治标（奇偶可见性）不治本（尺寸语义）。

## 五、验证步骤

1. 选 `01-random / validation-random-seed-002`，确认改动前偏移、改动后不偏移。
2. 选 `validation-random-seed-001`，确认改动前后都不偏移（回归）。
3. 让 AGV 跑进 padding 行（y≥18），确认在前端有正常格子可落、坐标对齐。
4. 切换厂房尺寸预设（1-4），确认各预设下都不偏移。
5. `grid_factory` 以外的工厂（如 `packet_factory`）回归，确认正方形配置未受影响。

## 六、前置确认（动手前）

修之前先追一下数据流，确认前端 `topology.gridWidth/gridHeight` 到达时**确实是原生 21×18**（而非已被某处方正过）。若链路上已有方正化、但渲染没跟上，修改点会不同。

建议核查路径：后端 `grid_factory_proxy` snapshot 组装 → SSE `/stream/state` → 前端 `factory.js` store → `FactoryPlayerSSE` → `FactoryVisualization3D` 的 `staticConfig` prop。

## 七、涉及文件

| 文件 | 角色 |
|------|------|
| `application/backend/joint_sim/io/use_io.py:56-69` | 后端 `max(w,h)` 方正化，pogema `size` 入口 |
| `application/frontend/src/components/FactoryVisualization3D.vue:62-74` | 前端 topology computed（待方正化） |
| `application/frontend/src/components/FactoryVisualization3D.vue:82` | `gridToWorld` cell-center 公式 |
| `application/frontend/src/components/FactoryVisualization3D.vue:422` | `GridHelper` 用 `max(w,h)`（方正化后自动正确） |
| `dataset/map_dataset/pogema-benchmark-main/raw_data_MAPF/01-random/maps.yaml` | 地图源数据（含非正方形地图） |
