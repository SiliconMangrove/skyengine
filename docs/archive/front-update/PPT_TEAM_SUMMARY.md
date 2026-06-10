# 天工（SkyEngine）组内工作总结 & Three.js 开发困难

> 分支：`0509frontend-update` | 日期：2026-05-27

---

## 一、组内工作总结

### 1.1 整体进度

本次迭代围绕 **"前端可视化升级 + 在线仿真能力建设"** 展开，从分支 `0509frontend-update` 至今，团队完成了以下工作：

| 模块 | 负责人（可填） | 状态 | 关键成果 |
|------|-------------|------|---------|
| Three.js 3D 可视化 | — | 已完成 | FactoryVisualization3D.vue（916 行）+ assets.js（539 行） |
| 工厂资产编辑面板 | — | 已完成 | FactoryAssetPanel.vue（280 行），支持增删改查 + 导出 |
| Docker 在线仿真代理 | — | 已完成 | DockerProxy.py（451 行）+ DockerFactoryManage.vue（320 行） |
| Docker 部署方案 | — | 已完成 | docker-compose.yml + 双 Dockerfile |
| Store 数据层扩展 | — | 已完成 | factory.js 新增 315 行，资产 CRUD + 导出 + 碰撞检测 |
| 代码清理 | — | 已完成 | 清理 ~23 万行废弃代码（POGEMA/GPT 求解器等） |
| UI 主题适配 | — | 已完成 | 暗色 → 浅色主题切换，全组件 CSS 适配 |
| 各工厂管理视图重构 | — | 已完成 | Static/Grid/Docker 三个管理页面重写 |

### 1.2 代码量统计

```
新增代码：  +5,142 行（核心功能代码）
删除代码： -233,592 行（废弃的 benchmark/求解器）
净文件变更：431 个文件
关键新文件：8 个
```

### 1.3 架构变更图

```
                     改造前                           改造后
               ┌─────────────┐                  ┌─────────────┐
               │ Canvas 2D   │                  │ Three.js 3D │
               │ emoji 绘制   │    ──────►       │ 纯几何体建模  │
               │ 固定俯视     │                  │ 自由旋转缩放  │
               └──────┬──────┘                  └──────┬──────┘
                      │                                │
               ┌──────┴──────┐                  ┌──────┴──────┐
               │ 只读统计面板 │    ──────►       │ 可编辑资产面板│
               │ 紫色方框展示 │                  │ 拖放+导出配置 │
               └─────────────┘                  └─────────────┘
                                                        │
                                                 ┌──────┴──────┐
                                                 │ Docker 代理  │
                                                 │ 容器化仿真   │
                                                 └─────────────┘
```

数据层（Store / SSE / API）在改造过程中 **零改动**，实现了渲染层与数据层的彻底解耦。

### 1.4 各模块完成度

| 功能点 | 完成度 | 备注 |
|--------|-------|------|
| 3D 场景渲染（地面/网格/围墙） | 100% | 浅色工业风主题 |
| 机器渲染 + 状态变色 | 100% | 复合体：主体+四柱+面板+屏幕+指示灯 |
| AGV 渲染 + 运动插值 | 100% | 复合体：底盘+四轮+货叉+箭头+状态灯 |
| 区域渲染 + 装饰物 | 100% | 警示柱/储物架/配电箱/隔离栏/围墙 |
| 路径点 + 路径连线 | 100% | dock=圆柱, route=球体, 虚线连线 |
| 编辑模式拖放 | 100% | Raycaster + 网格对齐 + 碰撞检测 |
| 资产池 CRUD | 100% | 添加/删除/重命名/位置更新 |
| 配置导出 | 100% | JSON 导出 + 文件下载 |
| Docker 容器管理 | 100% | 初始化/启动/暂停/停止全生命周期 |
| 2D 屏幕标签 | 100% | 3D→2D 投影，名称/编号浮标 |
| 指标看板 | 规划中 | 下一步 |
| 运输感知可视化 | 规划中 | 下一步 |

---

## 二、Three.js 开发过程中遇到的困难

### 困难 1：OrbitControls 与编辑拖放的交互冲突

**问题**：在编辑模式下，用户需要拖动 3D 场景中的资产来重新布局。但 OrbitControls 本身拦截了鼠标拖拽事件用于旋转相机，导致"拖资产"和"转视角"互相打架。

**具体表现**：
- 鼠标按下资产 → OrbitControls 同时响应 → 资产没拖动，相机却转了
- 拖动过程中视角漂移，资产位置计算错乱

**解决方案**（代码 `FactoryVisualization3D.vue:221-228`）：
```javascript
// pointerDown 时判断是否点中了资产
if (asset) {
    draggingAsset = asset
    isDragging = true
    controls.enabled = false  // 关键：拖资产时禁用 OrbitControls
}
// pointerUp 时恢复
controls.enabled = true
```

**额外处理**：拖动过程中还需记录鼠标的起始世界坐标 (`dragStartMouseWorld`)，用增量计算网格偏移，而不是直接用当前鼠标位置。否则拖动时资产会在鼠标和原始位置之间反复跳跃。

---

### 困难 2：2D 标签定位——3D 坐标到屏幕坐标的精确投影

**问题**：需要在 3D 场景上方显示机器名称、AGV 编号等文字标签。Three.js 没有内置文字渲染，需要将 3D 世界坐标投影到 2D 屏幕坐标，然后用 HTML 绝对定位显示。

**具体困难**：
- `Vector3.project(camera)` 返回的是 NDC 坐标（-1 到 1），需要转换为像素坐标
- canvas 元素的 `getBoundingClientRect()` 和外层容器的 `getBoundingClientRect()` 存在偏移，需要做差值修正
- 当物体在相机背面时（`vec.z > 1`），投影结果无意义，标签会出现在画面对面，需要做遮挡判断

**解决方案**（代码 `FactoryVisualization3D.vue:705-743`）：
```javascript
function updateScreenLabels() {
  // 对每个 3D 对象：
  const vec = mesh.position.clone()
  vec.y += GRID_SIZE * 0.7  // 向上偏移，标签显示在物体上方
  vec.project(camera)
  if (vec.z > 1) return     // 背面剔除

  // NDC → 像素，减去容器偏移
  labels.push({
    x: (vec.x * 0.5 + 0.5) * cw + rect.left - containerRect.left,
    y: (-vec.y * 0.5 + 0.5) * ch + rect.top - containerRect.top,
    text: mesh.userData.name,
  })
}
```

每帧调用 `updateScreenLabels()`，响应式更新 `screenLabels` 数组，Vue 自动更新 DOM。

---

### 困难 3：内存泄漏——Geometry 和 Material 的手动 dispose

**问题**：Three.js 不会自动回收 GPU 资源。每次切换工厂配置、添加/删除资产时，如果不手动 `dispose()`，GPU 内存会持续增长，最终导致页面卡顿甚至崩溃。

**具体困难**：
- 一个"机器"由 7 个 Mesh（主体 + 4 立柱 + 顶部面板 + 指示灯）组成，每个都有 Geometry 和 Material
- `buildMachines()` 在每次重建时需要遍历并清理上一轮的所有对象
- `buildZones()` 中的装饰物是 `THREE.Group`，需要 `traverse` 递归清理子对象
- `onBeforeUnmount` 需要确保整个 Scene 被清理干净

**解决方案**：

统一清理模式：
```javascript
// 单个 Mesh 清理
mesh.geometry?.dispose()
mesh.material?.dispose()

// Group 递归清理
group.traverse(child => {
    child.geometry?.dispose()
    if (Array.isArray(child.material))
        child.material.forEach(m => m.dispose())
    else
        child.material?.dispose()
})

// 组件卸载时的全量清理
onBeforeUnmount(() => {
    cancelAnimationFrame(animationId)
    resizeObserver?.disconnect()
    removeEditModeListeners()
    controls?.dispose()
    if (scene) {
        scene.traverse(child => { /* dispose all */ })
        scene.clear()
    }
    renderer?.dispose()
    renderer?.domElement?.remove()
})
```

---

### 困难 4：坐标系统对齐——网格坐标 ↔ 世界坐标 ↔ 屏幕像素

**问题**：系统中存在三套坐标系，需要在它们之间来回转换，稍有偏差就会导致资产"飘移"。

| 坐标系 | 说明 | 示例 |
|--------|------|------|
| 网格坐标 (gx, gy) | 业务层数据，左上角原点 | `(5, 3)` |
| 世界坐标 (x, y, z) | Three.js 3D 空间，中心原点 | `(1.5, 0, -2.0)` |
| 屏幕像素 (px, py) | 浏览器 DOM 定位 | `(320, 180)` |

**转换公式**：
```javascript
// 网格 → 世界（中心原点）
function gridToWorld(gx, gy) {
  return new THREE.Vector3(
    (gx - gridWidth / 2) * UNIT,   // x
    0,                              // y = 地面
    (gy - gridHeight / 2) * UNIT   // z
  )
}

// 世界 → 网格（反向对齐）
function snapToGrid(worldPos) {
  return {
    gx: Math.round(worldPos.x / UNIT + gridWidth / 2),
    gy: Math.round(worldPos.z / UNIT + gridHeight / 2),
  }
}
```

**踩坑点**：
- 网格坐标系 Y 轴向下（行），Three.js Z 轴向观察者（深度），映射时 gy → z
- `snapToGrid` 需要做边界 clamp（`Math.max(0, Math.min(gx, w-1))`），否则拖到地图外面
- 区域（zone）使用 `area: { x, y, w, h }` 表示，需要先算中心点再转世界坐标

---

### 困难 5：编辑模式下的增量重建 vs 全量重建

**问题**：当用户在资产面板中添加一个资产时，整个 3D 场景需要更新。但 `staticConfig` 是用 `watch(..., { deep: true })` 监听的，Vue 的深度 watch 无法区分"引用变了"还是"里面某个字段变了"。

**如果每次都全量重建**：所有 Mesh 销毁再创建，会有肉眼可见的闪烁，且性能差。

**如果只更新变化的部分**：需要精确知道是哪个类型的资产变了。

**解决方案——rebuildHint 机制**：

在 Store 中维护一个 `rebuildHint` 字段，资产操作时设置对应 hint：

```javascript
// factory.js Store 中
function addZone(...) {
    // ... 添加 zone
    rebuildHint = 'zone'  // 标记：只有 zone 变了
}
function addMachine(...) {
    // ... 添加 machine
    rebuildHint = 'machine'
}
```

在 3D 组件的 watch 中读取 hint，精准重建：

```javascript
watch(() => props.staticConfig, (newVal, oldVal) => {
    if (newVal !== oldVal) {
        // 引用变了（加载新配置）→ 全量重建
        buildStaticElements()
        return
    }

    // 同一引用的深度变更 → 根据 hint 精准重建
    const hint = store.rebuildHint
    if (hint === 'zone') buildZones()
    else if (hint === 'machine') buildMachines()
    else if (hint === 'waypoint') { buildWaypoints(); buildEdges() }
    else buildStaticElements()  // 兜底全量

    nextTick(() => { store.rebuildHint = null })  // 消费后清除
}, { deep: true })
```

---

### 困难 6：拖动时的"跳跃"问题

**问题**：直接用 `snapToGrid(mouseWorldPosition)` 计算拖拽目标时，鼠标在资产上点击的位置不同（中心 vs 边缘），会导致资产瞬间"跳"到鼠标对应的网格位置。

**解决方案——增量拖拽**：

```javascript
// pointerDown：记录起始状态
dragStartGridX = snapToGrid(asset.position).gx
dragStartGridY = snapToGrid(asset.position).gy
dragStartMouseWorld = point.clone()

// pointerMove：计算鼠标移动的增量，而不是绝对位置
const dx = point.x - dragStartMouseWorld.x
const dz = point.z - dragStartMouseWorld.z
const gridDX = Math.round(dx / GRID_SIZE)
const gridDZ = Math.round(dz / GRID_SIZE)

// 目标 = 起始网格 + 偏移
newGX = clamp(dragStartGridX + gridDX, 0, w - 1)
newGY = clamp(dragStartGridY + gridDZ, 0, h - 1)
```

这样不管鼠标点在资产的哪个位置，拖动都是平滑的增量移动。

---

### 困难 7：主题适配——从暗色科技风到浅色工业风

**问题**：初始方案设计了暗色科技风（深蓝背景 + 青色发光），但在实际对接工厂场景后，发现暗色主题不利于展示细节，且与工厂运营场景不符。需要中途切换到浅色工业风。

**影响范围**：
- 所有 3D 材质颜色需要重新调
- 光照强度需要大幅调整（浅色场景需要更强的环境光）
- CSS 面板从半透明暗色改为浅色风格
- 标签文字从白色阴影改为深色阴影

**解决方案**：
- 建立独立的 `assets.js` 集中管理所有颜色常量
- 暗色方案中大量使用的 `emissive`（自发光）属性在浅色场景下效果不好，需要调低 emissive 强度
- 光照从 `AmbientLight(0xffffff, 0.6)` 提高到 `1.8`，方向光从 `1.5` 提高到 `3.0`
- `MeshStandardMaterial` 的 `roughness` / `metalness` 参数全部重新调试

---

### 困难 8：响应式尺寸与 ResizeObserver

**问题**：Three.js 渲染器的尺寸需要与容器元素同步。但 Vue 组件的容器大小可能在以下时刻变化：
- 窗口 resize
- 侧边面板展开/收起
- 标签页切换后重新显示

**解决方案**：
```javascript
// 使用 ResizeObserver 而非 window.resize
resizeObserver = new ResizeObserver(onResize)
resizeObserver.observe(containerRef.value)

function onResize() {
    const w = container.clientWidth
    const h = container.clientHeight
    if (w === 0 || h === 0) return  // 隐藏时跳过
    renderer.setSize(w, h)
    camera.aspect = w / h
    camera.updateProjectionMatrix()
}
```

`window.resize` 只能捕获窗口级别变化，而 `ResizeObserver` 能精确捕获容器级别的尺寸变化，包括 CSS 动画导致的渐变。

---

## 三、经验总结

| 经验 | 说明 |
|------|------|
| **数据层与渲染层解耦是正确的** | Store/SSE/API 在整个 3D 改造过程中零改动，验证了架构设计的合理性 |
| **颜色/材质集中管理** | `assets.js` 抽离视觉常量后，主题切换变得可控 |
| **先跑通再优化** | 先用最简单的 Box + 基础颜色跑通数据流，再逐步添加细节（立柱、指示灯、装饰物） |
| **rebuildHint 模式有效** | 避免了每次小改动都全量重建，大幅提升了编辑体验 |
| **Three.js 内存管理必须重视** | GPU 资源不会自动 GC，必须手动 dispose，否则长时间运行必然内存泄漏 |

---

## 四、下一步计划

1. 指标实时看板（metrics panel 升级）
2. 运输感知路径可视化（长短距着色）
3. MoE 门控权重可视化
4. 实验对比面板
5. 性能优化（合批渲染 / InstancedMesh）
