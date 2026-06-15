# Factory 主题半格偏移问题

## 现象

`northeast_center`（北满钢铁）在 `StaticFactoryManage.vue` 把 `backgroundTheme` 从 `clean` 切到 `factory` 后，3D 场景里新增的厂房立柱、标线、围墙相对机器/AGV 整体偏移半格（0.5 个网格单位）。

`grid_factory` / `grid_factory_new` 使用 `factory` 主题时存在同样的视觉偏差，只是之前北满用 `clean` 主题没有这层背景，所以未暴露。

## 根因

前端 3D 场景里同时存在两套坐标约定：

### 1. 内容层（机器 / 区域 / AGV）—— cell-center 约定

`application/frontend/src/components/FactoryVisualization3D.vue:82`

```js
function gridToWorld(gx, gy) {
  const w = topology.value.gridWidth
  const h = topology.value.gridHeight
  return new THREE.Vector3((gx - w / 2 + 0.5) * GRID_SIZE, 0, (gy - h / 2 + 0.5) * GRID_SIZE)
}
```

`+0.5` 把对象落到**格子中心**，也就是落在两条网格线的正中间（半整数位置）。机器构建（`buildMachines` 第 438-444 行）走的就是这条路径。

区域构建（`buildZones` 第 459-460 行）虽然写法不同，但化简后等价：

```js
const cx = (area.x - gridWidth / 2 + area.w / 2) * U
//        = (area.x + (area.w - 1) / 2 - gridWidth / 2 + 0.5) * U
```

即"区域最左格子索引 + 区域内居中偏移"，仍然落到格子中心，与机器一致。

### 2. 厂房背景层（立柱 / 标线 / 围墙）—— grid-edge 约定

`application/frontend/src/utils/assets.js`

立柱（`createFactoryBackground` 第 757-767 行）：

```js
for (let i = 0; i <= gridWidth; i += pillarSpacing) {
  const x = (i - gridWidth / 2) * U          // ← 整数位置，落在网格线上
  addPillar(x, halfH + spread)
}
```

地面安全标线、四角标记、围墙段同理，都按 `halfW = gridWidth * U / 2` 这种"网格边缘"坐标布置，**没有 +0.5**。

`createPerimeterWall`（第 357-442 行）也用同一套 edge 坐标。

### 3. GridHelper 也是 edge 约定

`FactoryVisualization3D.vue:422`

```js
gridHelper = new THREE.GridHelper(Math.max(w, h) * GRID_SIZE, Math.max(w, h), ...)
```

`THREE.GridHelper` 的分割线对称分布在原点两侧，落在整数坐标上。机器坐在格子中心（半整数），本就和网格线差 0.5——这是正确且符合直觉的："对象在格子里，线在格子边界"。

### 为什么切到 factory 主题才暴露

`buildBackground()`（第 556-571 行）有一句硬开关：

```js
if (props.backgroundTheme !== 'factory') return
```

`clean` 主题下根本不构建厂房背景层，只有地面 + 网格线，没有"按 edge 坐标铺的大量结构件"去和"按 center 坐标的机器"做视觉对比，所以半格差不显。

`factory` 主题一旦启用，立柱、标线、围墙全是 edge 坐标，机器是 center 坐标，肉眼一对照就成"整体偏了半格"。

> 严格说这不是 bug，而是两套约定共存。但用户感知是"偏移"，所以需要统一。

## 解决方案

任选其一，按改动成本和视觉偏好决定。

### 方案 A：把厂房背景层整体偏移 +0.5（推荐，改动最小）

在 `createFactoryBackground` 和 `createPerimeterWall` 里给返回的 group 加一个统一位移，让所有 edge 构件去对齐 cell-center：

`application/frontend/src/utils/assets.js` —— `createFactoryBackground` 末尾（return 前）：

```js
bgGroup.position.set(U * 0.5, 0, U * 0.5)
return bgGroup
```

`createPerimeterWall` 末尾同理：

```js
wallGroup.position.set(U * 0.5, 0, U * 0.5)
return wallGroup
```

效果：立柱/标线/围墙整体平移半格，与机器中心对齐。视觉上立柱不再压在网格线交叉点上，而是落在格子里——这点要能接受。

注意：`GridHelper` 不要跟着偏，否则网格线和地面边缘会对不上。

### 方案 B：改回 clean 主题

`application/frontend/src/views/factory/StaticFactoryManage.vue`：

```js
const backgroundTheme = ref("clean")   // 原为 "factory"
```

北满回到裸网格地板，彻底回避偏移问题，但失去厂房外观。

### 方案 C：统一约定到 cell-center（彻底，但改动面大）

把 `createFactoryBackground` / `createPerimeterWall` 内部所有 `(i - gridWidth / 2) * U` 形式的坐标都改成 `(i - gridWidth / 2 + 0.5) * U`，让背景层从设计上就和内容层同一约定。

涉及 `assets.js` 里两处函数共约 20 个坐标计算点，需要逐个核对偶数/奇数尺寸下的对称性，工作量比方案 A 大，但语义最干净。

## 验证

改完后启动前端，进入北满钢铁工作台：

1. 切换"场景风格"在 `clean` / `factory` 之间，观察机器与立柱/围墙的相对位置。
2. `factory` 模式下切换"厂房尺寸"（1-4），确认不同尺寸下偏移都不再出现。
3. 同步检查 `grid_factory` 是否也随之修正（共用同一套渲染组件）。

## 涉及文件

| 文件 | 说明 |
|------|------|
| `application/frontend/src/components/FactoryVisualization3D.vue` | `gridToWorld`（cell-center）、`buildBackground`、`buildPerimeterWall`、`buildGroundAndGrid` |
| `application/frontend/src/utils/assets.js` | `createFactoryBackground`、`createPerimeterWall`（edge 坐标） |
| `application/frontend/src/views/factory/StaticFactoryManage.vue` | 北满主题切换入口 |
