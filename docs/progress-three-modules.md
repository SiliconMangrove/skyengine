# SkyEngine 三模块进展(PPT 素材)

> 时间:2026-06-16 | 分支:0610stable-update

---

## 模块一:3D 建模与前端更新

### 技术栈
- Vue 3.5 + Vite 7 + Pinia
- **3D**:Three.js 0.184(程序化建模,无外部模型文件)
- **图表**:ECharts 6.0 + vue-echarts 8.0

### 3D 可视化能力(`FactoryVisualization3D.vue`,980 行)
- WebGLRenderer + OrbitControls,支持阴影 / ACES 色调映射 / 雾效
- **可渲染对象**:机器(状态指示灯)、AGV(位置插值动画 + 状态色)、区域底板、障碍墙体、dock 圆柱 / 路径球、厂房背景(立柱 / 围墙)
- **资产工厂**(`utils/assets.js`,874 行):10 个 create 函数(createMachine / createAGV / createBollard / createRack / createCabinet / createObstacleWall / createPerimeterWall / createFactoryBackground...),全部 Three.js 基础几何体程序化构建
- **编辑模式**:raycaster 拾取 + 拖拽 snap-to-grid + 碰撞检测
- **双主题**:clean / factory,动态切换灯光与雾效

### 近期完成的更新
| 工作项 | 状态 |
|---|---|
| 非正方形地图网格方正化(与 pogema 正方形网格对齐) | **已实施**(`FactoryVisualization3D.vue:66-80`) |
| 3D 数字资产扩充(assets.js) | **已实施** |
| 帧传输功能(transfer frame) | **已实施** |
| online 容器栈兜底清理(atexit,SIGTERM/Ctrl+C 触发) | **已实施**(`server.py:17-50`) |
| factory 主题背景层半格偏移 | **已修复**(被方正化方案取代) |

### 视图与组件架构
- **工厂视图**:`DockerFactoryManage`(容器化)/ `GridFactoryManage`(MAPF)/ `StaticFactoryManage`(静态,如北满钢铁)/ `PacketFactoryManage`(包工厂)
- **功能组件**:AgentPanel(智能体分析)/ ControlPanel / ConfigPanel / MetricsPanel(ECharts)/ FactoryAssetPanel / FactoryPlayerSSE(SSE 播放)
- **2D 备选**:`FactoryVisualization.vue`(Canvas 2D,功能同构)

### 容器化部署
- 前端:node:22-alpine,npm ci + npm run dev 热更新,bind mount 源码即时生效
- 后端:`stop_grace_period: 30s`,挂 docker.sock 按需启停仿真容器

---

## 模块二:数据集生成 FJSP + MAPF 实例

### 数据集现状(`dataset/`)
| 目录 | 内容 | 规模 | 格式 |
|---|---|---|---|
| `fjsp/` | 自生成大实例 | 2 个(J10P5M6 / J20P10M10) | JSON |
| `fjsp-instances/` | 6 大经典 FJSP benchmark | **336 实例**(txt + json 各一份) | kacem / fattahi / brandimarte / barnes / behnke / dauzere / hurink |
| `mapf/` | MAPF 地图 | 2 个 YAML | 网格字符串(`.`/`#`) |
| `map_dataset/pogema-benchmark-main/` | POGEMA 全量地图 | **5 大类**(random / mazes / warehouse / movingai / puzzles) | YAML + JSON 算法结果 |

### 实例生成机制(`dataset/helper.py`,780 行)
**输入**:FJSP 实例 + MAPF 地图 → **输出**:统一的 `grid_factory.json` 配置

核心流程 `select_and_convert(fjsp, map_name, num_agvs)`:
1. 加载 FJSP 工件工序数据(`load_fjsp_json` / `load_fjsp_benchmark`)
2. 加载 MAPF 地图(`load_mapf_yaml`)
3. `_place_on_grid()` 在地图空地上均匀分布机器
4. `init_agvs()` 以**交通枢纽策略**放置 AGV(离所有机器曼哈顿距离最小)
5. `convert_to_grid_factory()` 组装为联合配置

**FJSP 与 MAPF 的联合方式**:
- FJSP 工序 → `jobs.job_list`(每个工序含 `machine_options_with_time:[[machine_id, proc_time], ...]` 标准多机器候选)
- MAPF 地图 → `topology.zones` 中的 obstacle 区域
- 机器位置从地图自由格中选取,AGV 枢纽放置
- 两部分通过共享 `gridWidth/gridHeight` 网格坐标系关联

### 联合实例数据结构(`config/grid_factory.json`)
```
topology    : gridWidth/Height, zones(obstacle), machines(location/size), waypoints
agvs        : id, initialLocation, velocity, capacity
jobs        : job_list[{operations, arrival_time, due_time, priority}]
renderConfig: 可视化配色
metadata    : 机器数/AGV 数汇总
```

### 算法消费(`application/backend/joint_sim/`)
- 配置解析:`io/use_io.py` 的 `create_env_from_config()` → `GridFactoryEnv`
- **三层调度架构**(每步由 Coordinator 统一调用):
  - **JobSolver**:`PDRScheduler.py`(SPT / LPT / MWKR / MOPNR / FDD 启发式规则)
  - **Assigner**:greedy / nearest / load_balance / random(AGV-任务分配)
  - **RouteSolver**:A* 路径规划
- 底层:基于 Pogema 的 `grid_factory_env.py` + `assign_env.py`

### API 接口(`server.py`)
| 端点 | 方法 | 功能 |
|---|---|---|
| `/dataset/list` | GET | 列出所有 FJSP 实例(分类)+ MAPF 地图 |
| `/dataset/generate` | POST | `{fjsp_category, fjsp_instance, map_category, map_name, num_agvs}` → 返回 grid_factory 配置 |
| `/factory/connect` | POST | 加载配置连接工厂(`factory_type=grid_factory_new`) |

### 待完善
- `fjsp/` 自生成大实例仅 2 个;`mapf/` 仅 2 个 YAML —— 大规模批量实例生成能力有限

---

## 模块三:指标、事件与分析模块

### 后端事件体系:四类 SSE 流(`server.py`)
| 端点 | 数据来源 | 说明 |
|---|---|---|
| `/stream/state` | `get_state_events` / `state_stream` 透传 | 网格状态、机器状态、运输 |
| `/stream/metrics` | `get_metrics_events` / `metrics_stream` 透传 | machine/agv/job 三类指标 + keyMetrics |
| `/stream/events` | `events_stream` 透传 | **业务事件**(machine_start_op / transfer_started,仅 DockerProxy) |
| `/stream/control` | `get_control_events` 轮询 2s | 控制状态 |

**三种 Proxy 差异**:
- **StaticFactoryProxy**:硬编码 mock 数据(开发调试用)
- **DockerProxy**:httpx 流式拉取 engine 容器 SSE 再转发(真实仿真链路)
- **BaseFactoryProxy**:默认实现调 snapshot

### 前端聚合(`stores/monitor.js` + `utils/factoryConnection.js`)
- **monitor store**:`eventQueue`(50 条)、`chartData`({machine,agv,job})、`keyMetrics`、`metricsTimeline`(500 条时序)、`buildRAGContext()`(从 timeline 取最近 20 条算 efficiency/utilization 的 current/avg/min/max)
- **FactoryConnectionManager**:state/metrics/control 三条 SSE 连接 + eventTypes/handlers 按类型分发

### 图表组件(`MetricsPanel.vue`)
- vue-echarts + echarts 按需引入(LineChart / BarChart)
- 当前硬编码 3 图:机器负载分布(bar)、AGV 任务统计(bar)、任务延迟趋势(line)
- 卡片由 `metricsMeta` 写死 6 种指标元数据

### Agent/RAG 分析模块(`AgentPanel.vue` + `utils/rag.js`)
- **7 个预设模板**:连通测试、AGV 效率、瓶颈检测、机器负载、运输路径、日志摘要、运行趋势
- **链路**:`runAnalysis()` → `monitorStore.buildRAGContext(currentState)` 组装上下文 → `queryRAG(prompt, context, onChunk)` 流式接收 → **marked.js 渲染 Markdown**
- **RAG 后端**:vLLM OpenAI 兼容 API(`/v1/chat/completions`,stream 模式,默认模型 `qwen3.6-35b`)
- **URL 注入**:`index.html` 的 `window.__RAG_BACKEND_URL__`(运行时注入) > Vite `/rag` proxy
- **System prompt** 定义了 6 项分析能力 + grid_state/machines/active_transfers 数据格式

### 设计文档与状态
| 文档 | 主题 | 状态 |
|---|---|---|
| `docs/sim-server-metrics-events-design.md` | L1-L4 四层架构(广播模型 / SSE 多订阅 / metrics 注册表 / events diff / 热力图) | **设计稿,部分实施** |
| `docs/front-update/backend-online-stack-cleanup.md` | online 容器栈 atexit 清理 | **已实施** |
| `docs/front-update/grid-square-up-alignment.md` | 非正方形地图网格方正化 | **已实施** |

### 当前进展与待落地
**已落地**:RAG 对话链路(流式 + Markdown)、StaticFactory mock 数据链路、DockerProxy SSE 透传、online 栈清理、网格方正化、3D 资产扩充

**待落地**(设计文档提出):
- L1 传输层广播模型修复
- L3 events 端到端(前端 `connectEvents` 缺失、`pushSimMetrics/pushSimEvent` 在 `DockerFactoryManage.vue` 已调用但 monitor store 未定义)
- **L4 metrics registry 配置化**(`metricsRegistry.js` 未创建,当前加新图需改 4 处:模板 / computed / openBigChart / metricsMeta)

---

## 三模块关系一览(一页 slide)

```
        ┌─────────────────────────────────────┐
        │  模块二:数据集 FJSP+MAPF            │
        │  (helper.py / 336 benchmark / POGEMA)│
        └────────────────┬────────────────────┘
                         │ /dataset/generate
                         ▼
   ┌──────────────────────────────────────────────┐
   │  joint_sim 三层调度                            │
   │  JobSolver(PDR) + Assigner + RouteSolver(A*)  │
   └────────────────┬─────────────────────────────┘
                    │ SSE: state/metrics/events/control
                    ▼
        ┌────────────────────────┐    ┌──────────────────────┐
        │ 模块一:3D 前端         │◄──►│ 模块三:指标/事件/分析 │
        │ (Three.js 可视化 +     │    │ (monitor store +     │
        │  资产工厂 + 编辑模式)  │    │  ECharts + RAG 大模型)│
        └────────────────────────┘    └──────────────────────┘
```
