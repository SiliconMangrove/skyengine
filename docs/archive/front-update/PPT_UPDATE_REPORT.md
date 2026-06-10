# 天工系统前端更新汇报 PPT 大纲

> 分支：`0509frontend-update` | 日期：2026-05-27

---

## Slide 1 — 封面

**天工（SkyEngine）平台前端升级汇报**

- 分支：`0509frontend-update`
- 汇报人：[姓名]
- 日期：2026-05-27

---

## Slide 2 — 更新总览

### 本次更新四大方向

| # | 方向 | 状态 | 亮点 |
|---|------|------|------|
| 1 | **3D 可视化引擎** | 已完成 | Three.js 替代 Canvas 2D，真 3D 透视 |
| 2 | **工厂资产编辑面板** | 已完成 | 资产池 + 编辑模式 + 配置导出 |
| 3 | **Docker 在线仿真代理** | 已完成 | 容器化算法按需启停 |
| 4 | **部署方案统一** | 已完成 | docker-compose 一键启动 |

### 代码量变化

- 新增代码：**+5,142 行**
- 删除废弃代码：**-233,592 行**（清理 POGEMA benchmark、弃用求解器）
- 净文件变更：**431 个文件**
- 关键新组件：3 个（FactoryVisualization3D / FactoryAssetPanel / DockerFactoryManage）

---

## Slide 3 — 方向一：Three.js 3D 可视化

### 替代方案对比

| 维度 | 旧方案（Canvas 2D 2.5D） | 新方案（Three.js 真正 3D） |
|------|--------------------------|---------------------------|
| 渲染 | Canvas 平面绘制 | WebGL 真透视 |
| 交互 | 拖拽/缩放 | OrbitControls（旋转/缩放/平移） |
| 模型 | 无需模型 | **无需模型** — 纯几何体表达 |
| 数据层 | Store/SSE | **完全不动** — 复用同一 Store |
| 依赖 | 零 | 仅 `three` 一个 npm 包 |

### 场景元素映射

| 工厂元素 | 3D 表达 |
|---------|--------|
| 地面网格 | `PlaneGeometry` + `GridHelper` |
| 机器 | `BoxGeometry` + 状态颜色 |
| AGV | 小长方体 + 运动动画 |
| 路径 | `BufferGeometry` + `Line` |
| 区域 | 半透明 `BoxGeometry` |
| 传输任务 | 粒子/小球动画 |

### 新文件
- `FactoryVisualization3D.vue`（916 行）
- 零外部模型资产依赖

---

## Slide 4 — 方向二：工厂资产编辑面板

### 改造前 vs 改造后

| 功能 | 改造前 | 改造后 |
|------|--------|--------|
| 资产展示 | 紫色统计框，纯只读 | 资产池下拉 + 预览 + 可添加 |
| 资产管理 | 无 | 编辑模式下支持增/删/重命名 |
| 配置输出 | 无 | 导出 JSON 配置文件 |
| 场景交互 | 无 | 3D 拖放布局，自动网格对齐 |
| 仿真控制 | 无约束 | 编辑模式下禁止启动仿真 |

### 支持的数字资产类型

| 类别 | 资产 | 3D 建模 |
|------|------|--------|
| 区域类 | 禁区 / 工位 / 障碍物 / 工作区 | 半透明扁平方块 |
| 机器类 | 加工机器 | 长方体 + 状态颜色 |
| 路由点类 | 停靠点 / 路由点 | 圆柱/球体 |

### 数据流

```
FactoryAssetPanel ←→ factory.js Store ←→ FactoryVisualization3D
      │                    │                      │
  添加/删除资产       状态管理              3D 渲染 + Raycaster 拖放
  编辑/导出配置       持久化                 网格对齐 + 高亮预览
```

### 新文件
- `FactoryAssetPanel.vue`（280 行）+ 独立 CSS
- `stores/factory.js` 扩展（+315 行，资产 CRUD + 导出）

---

## Slide 5 — 方向三：Docker 在线仿真代理

### 架构设计

```
用户前端 ←→ SkyEngine Backend ←→ DockerProxy ←→ Docker Engine
                                          │
                                    按 FJSP/MAPF 算法组合
                                    启动对应容器集群
```

### 支持的算法组合

| 调度算法（FJSP） | 路由算法（MAPF） |
|------------------|-----------------|
| PSO / DE / DRL / Best | A* / MAPF-GPT |

### 生命周期

```
initialize() → 创建 Docker 网络 + 启动引擎容器
    ↓
start() → 选择算法 → 启动 FJSP/MAPF 容器 → HTTP 发送配置
    ↓
stop() → 停止并清理所有容器 + 网络
```

### 技术实现
- 纯 Python，通过 `docker` SDK 直连 Docker Socket
- 两阶段启动，按需组合算法容器
- 完整的 SSE 事件流支持

### 新文件
- `DockerProxy.py`（451 行）
- `DockerFactoryManage.vue`（320 行）— Docker 工厂管理视图
- `docker-compose.yml`（58 行）— 统一部署编排

---

## Slide 6 — 方向四：部署方案 & 代码清理

### Docker 部署方案

| 服务 | 端口 | 说明 |
|------|------|------|
| `skyengine-backend` | 8233 → 8000 | FastAPI 后端 + DockerProxy |
| `skyengine-frontend` | 5180 → 5173 | Vue.js 前端 |

**一键启动**：`docker compose up -d`

### 代码清理

| 清理项 | 行数 | 说明 |
|--------|------|------|
| POGEMA benchmark 数据 | ~200K 行 | 移至独立仓库，主仓库不再追踪 |
| 弃用的 GPT 求解器 | ~1K 行 | tokenizer + inference 清理 |
| 弃用的贪心/即时求解器 | ~200 行 | RouteSolver 清理 |
| 弃用的 Transformer 求解器 | ~300 行 | JobSolver 清理 |
| 旧 Dockerfile 配置 | ~100 行 | 迁移至新 docker-compose 方案 |

---

## Slide 7 — 前端架构变更图

### 组件层级（更新后）

```
FactoryView.vue
├── FactoryManage.vue（路由入口）
│   ├── GridFactoryManage.vue     ← 网格工厂（已更新）
│   ├── StaticFactoryManage.vue   ← 静态演示工厂（已更新）
│   └── DockerFactoryManage.vue   ← Docker 在线仿真（新增）
│
├── FactoryVisualization3D.vue     ← 3D 可视化（新增，替代 Canvas 2D）
├── ControlPanel.vue               ← 仿真控制
├── RightSidePanel.vue
│   ├── ConfigPanel.vue            ← 配置管理（精简）
│   ├── FactoryAssetPanel.vue      ← 工厂资产编辑（新增）
│   ├── MetricsPanel.vue           ← 指标面板
│   └── EventPanel.vue             ← 事件面板
└── FactoryPlayerSSE.vue           ← SSE 播放器
```

### Store 扩展（factory.js）

新增功能：
- `ASSET_TEMPLATES` — 资产模板注册表
- `addAssetFromTemplate()` — 从模板创建资产
- `updateAssetPosition()` — 更新资产位置
- `renameAsset()` / `removeAsset()` — 资产 CRUD
- `exportCurrentConfig()` — 导出 JSON 配置

---

## Slide 8 — 未提交的增量更新

### 当前工作区变更（未提交）

| 文件 | 变更 | 说明 |
|------|------|------|
| `FactoryVisualization3D.vue` | -695/+净减 | 3D 组件优化精简 |
| `FactoryAssetPanel.vue` | +14 | 资产面板微调 |
| `factory.js` | +74/-净增 | Store 逻辑完善 |
| `FactoryView.vue` | +51 | 视图路由更新 |
| `GridFactoryManage.vue` | +120/-重写 | 网格工厂管理重构 |
| `StaticFactoryManage.vue` | +279/-重写 | 静态工厂管理重构 |
| `DockerFactoryManage.vue` | +3 | Docker 工厂微调 |
| `assets.js` | 新增 | 资产工具函数 |

---

## Slide 9 — 工厂类型总览（更新后）

| 工厂 ID | 名称 | 代理类型 | 状态 |
|---------|------|---------|------|
| `packet_factory` | 翼辉电池装配无人产线 | PacketFactoryProxy | 生产可用 |
| `grid_factory` | 翼辉原料分拣仓 | GridFactoryProxy | 需要 joint_sim |
| `docker_factory` | Docker 在线仿真 | DockerProxy | **新增** |
| `northeast_center` | 北满钢铁制造中心 | StaticFactoryProxy | 演示 |
| `southwest_logistics` | 西南铝业制造中心 | — | 规划中 |

---

## Slide 10 — 下一步计划

### 近期（本周）
- [ ] 提交当前未提交的前端增量更新
- [ ] 完善 Docker 工厂管理页面的算法选择 UI
- [ ] 测试 DockerProxy 完整生命周期（initialize → start → stop）

### 中期（下周）
- [ ] 3D 场景拖放编辑功能完善（Raycaster + 网格对齐）
- [ ] 指标实时看板模块
- [ ] 运输感知路径可视化

### 长期（论文阶段）
- [ ] MoE 门控权重可视化
- [ ] 实验对比面板
- [ ] 前端 Phase 2/3 更新（参见演示 demo 方案）

---

## Slide 11 — Q&A

**感谢聆听，欢迎提问**
