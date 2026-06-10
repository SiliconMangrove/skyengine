# 天工平台前端 Three.js 3D 可视化升级方案

> 策略：**Three.js 纯几何体** — 用 BoxGeometry、CylinderGeometry 等程序化几何体替代 3D 模型文件，零资产依赖。
> 目标：替换 `FactoryVisualization.vue` 的 Canvas 2D 渲染层为 Three.js WebGL 渲染，数据层（Store、SSE、API）完全不动。
> 项目路径：`/data1/home/wuhao/project/grouppro/skyengine`

---

## 一、为什么这个方案靠谱

### 1.1 Canvas 2.5D vs Three.js 真 3D

| 维度 | Canvas 2D 2.5D | Three.js 纯几何体 |
|------|----------------|-------------------|
| **视觉冲击** | 还是平面图，加了阴影 | 真 3D 透视，自由旋转 |
| **老板感受** | "换了个深色皮肤" | "数字孪生" |
| **3D 模型需求** | 不需要 | **不需要** — Box/Cylinder 就够了 |
| **代码量** | 改 6 个函数 | 新增 1 个组件 ~350 行 |
| **数据层改动** | 无 | **无** — 复用同一个 Store |
| **交互** | 保持拖拽/缩放 | OrbitControls（旋转/缩放/平移） |
| **npm 依赖** | 零 | `three` 一个包 |

### 1.2 为什么纯几何体足够

工厂场景元素天然适合几何体：

| 场景元素 | 几何体 | Three.js 构造 |
|---------|--------|--------------|
| 地面 | 平面 | `PlaneGeometry` + `GridHelper` |
| 机器 | 长方体 | `BoxGeometry(width, height, depth)` |
| AGV | 小长方体 + 灯 | `BoxGeometry` + 颜色 |
| 路径连线 | 线段 | `BufferGeometry` + `LineBasicMaterial` |
| 路径点 | 圆柱/小球 | `CylinderGeometry` / `SphereGeometry` |
| 区域 | 半透明扁平长方体 | `BoxGeometry(height=0.1)` |
| 传输任务 | 小球/粒子 | `SphereGeometry` 或 `PointsMaterial` |
| 围墙 | 细长方体 | `BoxGeometry(thin, tall, long)` |

---

## 二、整体架构

### 2.1 组件关系

```
视图层 (GridFactoryManage.vue / PacketFactoryManage.vue)
  │
  ├── ControlPanel.vue          ← 不动（只改 CSS 暗色主题）
  ├── RightSidePanel.vue         ← 不动（只改 CSS 暗色主题）
  │   ├── ConfigPanel.vue
  │   ├── MetricsPanel.vue
  │   └── EventPanel.vue
  │
  └── FactoryPlayerSSE.vue       ← 内部引用改一下
      └── FactoryVisualization3D.vue   ← 【新增】替代 FactoryVisualization.vue

数据层（完全不动）
  ├── stores/factory.js          ← 同样的 pushSnapshot / currentState
  ├── stores/monitor.js          ← 同样的 metrics
  └── utils/sse.js               ← 同样的 SSE
```

### 2.2 坐标映射

Canvas 2D 坐标映射不变，Three.js 使用 3D 世界坐标：

```
Canvas 2D:  grid(gx, gy) → pixel(cx, cy)
                     cx = gx * baseGridSize + baseGridSize/2
                     cy = gy * baseGridSize + baseGridSize/2

Three.js:   grid(gx, gy) → world(x, 0, z)
                     x = (gx - gridWidth/2)  * UNIT
                     z = (gy - gridHeight/2) * UNIT
                     y = 0 (地面)
```

以网格中心为原点，方便摄像机初始定位。

其中 `UNIT` = `baseGridSize / 2`（或自定义缩放因子）。

### 2.3 数据流

```
SSE 推送 → Store.pushSnapshot()
           → historyBuffer 新增帧
           → Store.currentState (computed)
           → FactoryVisualization3D 的 watch 响应变化
           → 更新 Three.js Mesh 的 position/material
           → renderer.render(scene, camera)
```

**与现有逻辑完全一致**，只是把 `ctx.fillText('🤖')` 换成 `agvMesh.position.set(x, y, z)`。

---

## 三、场景设计

### 3.1 场景结构

```
Scene
├── AmbientLight (环境光，均匀照亮)
├── DirectionalLight (方向光 + 阴影)
│   └── Shadow
├── GroundPlane (灰色地面接收阴影)
├── GridHelper (网格线，辅助定位)
├── ZoneGroup (区域半透明盒)
│   ├── BoxMesh (zone_1, 半透明)
│   └── ...
├── EdgeGroup (路径连线)
│   ├── Line (edge_1, 虚线蓝)
│   └── ...
├── WaypointGroup (路径点)
│   ├── CylinderMesh (dock_1, 发光蓝)
│   ├── SphereMesh (route_1, 微光绿)
│   └── ...
├── MachineGroup (机器)
│   ├── BoxMesh (machine_1, WORKING=cyan glow)
│   ├── BoxMesh (machine_2, IDLE=dark gray)
│   └── ...
└── AGVGroup (AGV)
    ├── BoxMesh (AGV_1, active=cyan)
    └── ...
```

### 3.2 元素绘制规格

#### 地面

```javascript
// 深色地面平面
const floorGeometry = new THREE.PlaneGeometry(gridWidth * UNIT, gridHeight * UNIT)
const floorMaterial = new THREE.MeshStandardMaterial({
  color: 0x0B1120,
  roughness: 0.8,
  metalness: 0.2,
})
floor.rotation.x = -Math.PI / 2  // 平放在 XZ 平面
floor.receiveShadow = true

// 网格辅助线
const gridHelper = new THREE.GridHelper(
  Math.max(gridWidth, gridHeight) * UNIT,
  Math.max(gridWidth, gridHeight),
  0x143260,
  0x0a1a30,
)
```

#### 机器（核心亮点）

```javascript
// BoxGeometry(width, height, depth)
const machineGeometry = new THREE.BoxGeometry(UNIT * 1.3, UNIT * 0.8, UNIT * 1.0)

// 状态配色方案
const MATERIALS = {
  WORKING:     new THREE.MeshStandardMaterial({
    color: 0x1a3d5c, emissive: 0x003050, roughness: 0.3, metalness: 0.6
  }),
  IDLE:        new THREE.MeshStandardMaterial({
    color: 0x1e2a38, roughness: 0.8, metalness: 0.2
  }),
  BROKEN:      new THREE.MeshStandardMaterial({
    color: 0x3d1a1a, emissive: 0x400000, roughness: 0.3, metalness: 0.4
  }),
  MAINTENANCE: new THREE.MeshStandardMaterial({
    color: 0x3d2a1a, emissive: 0x302000, roughness: 0.3, metalness: 0.4
  }),
}

// 位置
const pos = gridToWorld(gx, gy)
machineMesh.position.set(pos.x, UNIT * 0.4, pos.z)  // y 偏移半个高度
machineMesh.castShadow = true
machineMesh.receiveShadow = true
```

**效果**：带 `emissive` 发光的机器在暗色地面上非常显眼，WORKING 状态有青色自发光，BROKEN 有红色自发光。

#### AGV

```javascript
// 小型长方体
const agvGeometry = new THREE.BoxGeometry(UNIT * 0.6, UNIT * 0.15, UNIT * 0.4)

const agvActiveMat = new THREE.MeshStandardMaterial({
  color: 0x0D3B66, emissive: 0x004466,
  roughness: 0.2, metalness: 0.7,
})
const agvIdleMat = new THREE.MeshStandardMaterial({
  color: 0x1a2030, roughness: 0.8, metalness: 0.2,
})

// 位置更新（每帧 lerp，和原来同样的插值逻辑）
agvMesh.position.lerp(targetPosition, 0.15)
agvMesh.castShadow = true
```

**顶部加一个小发光球**作为状态灯：

```javascript
const lightGeometry = new THREE.SphereGeometry(UNIT * 0.08, 8, 8)
const lightMaterial = new THREE.MeshBasicMaterial({ color: 0x00FF88 })
```

#### 路径点

```javascript
// dock: 扁圆柱
const dockGeometry = new THREE.CylinderGeometry(UNIT * 0.3, UNIT * 0.3, UNIT * 0.08, 16)
// route: 小球
const routeGeometry = new THREE.SphereGeometry(UNIT * 0.15, 8, 8)
```

#### 路径连线

```javascript
const points = [start.pos, end.pos]
const lineGeometry = new THREE.BufferGeometry().setFromPoints(points)
const lineMaterial = new THREE.LineDashedMaterial({
  color: 0x006688,
  dashSize: 2,
  gapSize: 2,
})
const line = new THREE.Line(lineGeometry, lineMaterial)
```

#### 区域

```javascript
// 扁平 BoxGeometry，半透明
const zoneGeometry = new THREE.BoxGeometry(w * UNIT, 0.05, h * UNIT)
const zoneMaterial = new THREE.MeshStandardMaterial({
  color: zoneColor,
  transparent: true,
  opacity: 0.15,
  roughness: 0.9,
})
```

### 3.3 摄像机和交互

```javascript
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'

// 初始视角：45° 俯视
const camera = new THREE.PerspectiveCamera(
  50, container.clientWidth / container.clientHeight, 0.5, 500
)
camera.position.set(
  gridWidth * UNIT * 0.6,
  gridHeight * UNIT * 0.8,
  gridHeight * UNIT * 0.6,
)
camera.lookAt(0, 0, 0)

// 轨道控制
const controls = new OrbitControls(camera, renderer.domElement)
controls.enableDamping = true
controls.dampingFactor = 0.08
controls.maxPolarAngle = Math.PI / 2 - 0.1  // 限制不能翻到底部
controls.minDistance = UNIT * 5
controls.maxDistance = UNIT * 80
controls.target.set(0, 0, 0)
```

### 3.4 渲染循环

```javascript
function animate(timestamp) {
  requestAnimationFrame(animate)

  // 1. 从 Store 获取最新状态（同原来的 updatePhysics）
  updateFromStore(timestamp)

  // 2. 更新控制器
  controls.update()

  // 3. 渲染
  renderer.render(scene, camera)
}
```

### 3.5 动画插值逻辑

与原来 Canvas 版的 `updatePhysics()` 同逻辑，保留 AGV 位置 lerp：

```javascript
function updateFromStore(timestamp) {
  const state = store.currentState
  if (!state) return

  const targets = state.grid_state?.positions_xy || []
  const isActive = state.grid_state?.is_active || []

  // 同步 AGV 数量
  if (agvMeshes.length !== targets.length) {
    rebuildAGVMeshes(targets)
  }

  // 位置插值（和原来一模一样的逻辑）
  targets.forEach(([gx, gy], i) => {
    const target = gridToWorld(gx, gy)
    const mesh = agvMeshes[i]
    if (mesh) {
      mesh.position.x += (target.x - mesh.position.x) * 0.15
      mesh.position.z += (target.z - mesh.position.z) * 0.15

      // 更新材质（活跃状态变化）
      mesh.material = isActive[i] !== false ? agvActiveMat : agvIdleMat
    }
  })

  // 更新机器状态
  updateMachineStates(state.machines)

  // 自动步进逻辑（保持不变）
  if (store.isPlaying && allArrived) {
    if (waitTimeLeft > 0) {
      waitTimeLeft -= deltaTime
    } else {
      if (store.nextStep()) waitTimeLeft = STEP_WAIT_TIME
    }
  }
}
```

### 3.6 2D 标签（机器名称、AGV 编号）

用 CSS 绝对定位方式（最简单）：

```vue
<template>
  <div ref="containerRef" class="three-container">
    <!-- Three.js canvas 挂载到这里 -->
    <!-- 标签覆盖层 -->
    <div
      v-for="label in screenLabels"
      :key="label.id"
      class="world-label"
      :style="{
        left: label.screenX + 'px',
        top: label.screenY + 'px',
        color: label.color,
      }"
    >{{ label.text }}</div>
  </div>
</template>
```

```javascript
// 每帧计算屏幕坐标
function updateScreenLabels() {
  const labels = []
  machineMeshes.forEach(mesh => {
    const vec = mesh.position.clone().project(camera)
    labels.push({
      id: mesh.userData.id,
      screenX: (vec.x * 0.5 + 0.5) * canvas.width,
      screenY: (-vec.y * 0.5 + 0.5) * canvas.height,
      text: mesh.userData.name,
      color: '#B0C4DE',
    })
  })
  screenLabels.value = labels
}
```

---

## 四、文件清单与改动量

### 4.1 新增文件

| 文件 | 说明 | 代码量 |
|------|------|--------|
| `src/components/FactoryVisualization3D.vue` | 核心 3D 组件（场景、几何体、动画循环） | ~350 行 |
| `src/composables/useThreeScene.js` | Three.js 初始化/清理逻辑复用 | ~80 行 |

### 4.2 修改文件

| 文件 | 改动 | 改动量 |
|------|------|--------|
| `FactoryPlayerSSE.vue` | 引入 FactoryVisualization3D 替代 Canvas 版 | ~5 行 |
| `package.json` | 加 `"three": "^0.170.0"` | 1 行 |
| `src/styles/theme.css` | 新增暗色主题 CSS 变量（供面板用） | ~30 行 |
| `ControlPanel.css` | 暗色面板适配 | ~15 行 |
| `RightSidePanel.css` | 暗色面板适配 | ~5 行 |

### 4.3 完全不改的文件

- `stores/factory.js` — 数据层完全不动
- `stores/monitor.js` — 监控层完全不动
- `utils/sse.js` — SSE 层完全不动
- `utils/api.js` — API 层完全不动
- `scenarios/` — 测试场景完全不动
- `ConfigPanel.vue` — 配置面板不动
- `EventPanel.vue` — 事件面板不动
- `MetricsPanel.vue` — 指标面板不动
- 所有 Factory Manage 视图 — 引用的组件名不变

### 4.4 可删除/保留的文件

| 文件 | 处理 |
|------|------|
| `FactoryVisualization.vue` | 保留作 2D 对比，后续可删除 |
| `FactoryVisualization.css` | 同上 |

---

## 五、实施步骤

### Step 1：环境准备（0.5h）

```bash
cd application/frontend
npm install three
```

### Step 2：新建 3D 组件（3-4h）

创建 `FactoryVisualization3D.vue`：

1. **Template**：`<div ref="containerRef">` + label overlay div
2. **Scene 初始化**（onMounted）：
   - Scene / Camera / WebGLRenderer（antialias）
   - AmbientLight + DirectionalLight（castShadow）
   - GridHelper + GroundPlane
   - OrbitControls
3. **静态元素构建**（基于 `props.staticConfig`）：
   - `buildMachines(topology.machines)` → BoxMesh 数组
   - `buildZones(topology.zones)` → 半透明 BoxMesh 数组
   - `buildWaypoints(topology.waypoints)` → Cylinder/Sphere 数组
   - `buildEdges(topology.edges)` → Line 数组
4. **渲染循环**（requestAnimationFrame）：
   - `updateFromStore(timestamp)` — 读 Store.currentState，更新 AGV 位置/机器材质
   - `updateScreenLabels()` — 投影 3D 坐标到屏幕
   - `controls.update()`
   - `renderer.render(scene, camera)`
5. **清理**（onBeforeUnmount）：dispose geometries/materials/renderer

### Step 3：集成到页面（0.5h）

修改 `FactoryPlayerSSE.vue`，将 `<FactoryVisualization>` 替换为 `<FactoryVisualization3D>`，props 保持一致。

### Step 4：暗色面板适配（1h）

1. 新建 `src/styles/theme.css` — CSS 变量
2. `ControlPanel.css` — 背景半透明深色 + 文字浅色
3. `RightSidePanel.css` — 同适配

### Step 5：测试与调优（1-2h）

1. 加载 `grid_factory.json` 配置，确认场景正常渲染
2. 运行 `fullSystemTest` 场景，确认动画正常
3. 调整摄像机初始位置、光照强度、材质颜色
4. 性能验证（Chrome DevTools FPS counter）

---

## 六、效果预期

```
                      ┌─────────────────────────────────────────┐
俯视 45° 视角：        │  · 深色地面 + 青色发光网格线            │
                      │  · 机器：带自发光的长方体（青/红/灰）    │
      🏭              │  · AGV：青色小车在地面移动，带投影       │
     ╱ ╲              │  · 路径：蓝色虚线连接各节点              │
    ╱   ╲             │  · 区域：半透明色块覆盖地面              │
   ╱  M1 M2 ╲         │  · 侧面板：暗色玻璃 Dashboard 风格       │
  ╱  A1→  A2 ╲        │  · 鼠标拖拽旋转、滚轮缩放、右键平移     │
 ╱    M3 M4    ╲       │                                        │
╱───────────────╲      │  · 左上角图例，右下角鹰眼缩略图         │
                      └─────────────────────────────────────────┘
```

---

## 七、与现有方案对比

|  | Canvas 现状 | 2.5D Canvas | Three.js 纯几何体 |
|---|------------|-------------|------------------|
| AGV | 🤖 emoji | 2D 矢量小车 | **3D 立体小车 + 投影** |
| 机器 | 五角星 | 2D 圆角框 | **有高度的 Box + 自发光** |
| 视角 | 固定俯视 | 固定俯视 | **自由旋转/缩放** |
| 光影 | 无 | 假阴影 | **真实光照 + 阴影** |
| 代码改动 | — | 改 6 个绘图函数 | **新增 1 组件，Store 不动** |
| 3D 资产 | — | — | **零，纯代码几何体** |
| 老板评价 | "low" | "换了皮肤" | **"数字孪生"** ✅ |

---

## 八、风险与应对

| 风险 | 概率 | 应对 |
|------|------|------|
| Three.js 学习曲线 | 中 | 代码量不大（~350行），set + render 模式 |
| 性能问题 | 低 | ~50 Box + 10 AGV，现代浏览器轻松 60fps |
| 2D 标签定位偏差 | 低 | Vector3.project 成熟方案，偏移可控 |
| 移动端兼容 | 低 | Three.js WebGL 兼容性很好 |
| 与现有面板布局冲突 | 低 | 模板结构不变，只替换 Canvas 为 WebGL 容器 |

---

## 九、总结

**Three.js 纯几何体方案 = 零模型资产 + 真 3D 效果 + 数据层零改动**

核心工作量约 **5-8 小时**：
1. 一个 350 行的 `FactoryVisualization3D.vue` 新组件
2. 面板 CSS 暗色适配
3. 集成测试

不需要找任何 3D 模型文件。Box + Cylinder + Sphere + Plane + Line 这些程序化几何体足够构建一个有立体感的工厂场景。配合 `emissive` 自发光材质 + 暗色主题 + OrbitControls 自由旋转，视觉直接拉满。
