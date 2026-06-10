# SkyEngine 双仓库近期进展报告

> 报告日期: 2026-06-06  
> 涵盖周期: 2025-11 ~ 2026-06

---

## 一、仓库概览

| | **finalpro/SkyEngine** | **grouppro/skyengine** |
|---|---|---|
| **定位** | 算法/执行器层 — 核心仿真引擎 + 算法研究 | 服务层 — 前后端平台 + 可视化 + 容器化调度 |
| **当前分支** | `server0603` | `0601system-update` |
| **总提交** | ~80 次 (11月至今) | ~30 次 (3月至今) |
| **最近活跃** | 5月31日 ~ 6月6日 | 5月20日 ~ 6月6日 |
| **技术栈** | Python, PyTorch, Pogema, FastAPI, Docker | Vue.js 3, Three.js, FastAPI, Docker, Pinia |

---

## 二、finalpro/SkyEngine — 算法引擎层

### 2.1 项目结构

```
SkyEngine/
├── sky_executor/           # 核心仿真引擎
│   ├── grid_factory/       # 网格工厂环境
│   │   └── factory/
│   │       ├── Component/  # 可插拔组件 (Assigner, JobSolver, RouteSolver)
│   │       ├── Metrics/    # 性能指标收集
│   │       ├── DataCollector/  # 数据采集
│   │       ├── Utils/      # 特征提取、转移时间估算
│   │       └── Benchmark/  # 基准测试工具
│   ├── factory_template/   # 工厂生命周期模板 (event, callback, factory_core)
│   └── utils/registry/     # 组件注册系统
├── trainer/                # RL 训练器
│   ├── dqn_trainer.py      # Deep Q-Network
│   ├── ppo_trainer.py      # Proximal Policy Optimization
│   ├── grpo_trainer.py     # Group Relative Policy Optimization
│   ├── reinforce_trainer.py # REINFORCE
│   └── distiller.py        # 知识蒸馏
├── experiment/             # 实验研究
│   ├── moe_grpo/           # MoE + GRPO 训练
│   ├── moe_ppo/            # MoE + PPO 训练
│   ├── distill_expert_to_nn/ # 专家策略蒸馏
│   ├── grpo_learned/       # GRPO 学习表征
│   └── pre_experiment/     # 预实验数据分析
├── dataset/                # 基准数据集
│   ├── fjsp-instances/     # FJSP 调度实例
│   ├── mapf/               # MAPF 路径规划实例
│   └── JSPLIB-master/      # 作业车间调度库
├── docker-compose.yaml     # 实验模式部署
├── docker-compose-online.yaml  # 在线服务模式部署
├── run.py                  # 标准运行入口
└── sim_server.py           # 在线仿真控制服务
```

### 2.2 近期进展时间线

#### 2025-11: 基础框架搭建
- 网格工厂环境重构，集成 Pogema 仿真环境
- 作业车间调度算法研究 (Job Shop)
- 双塔模型 (Duel Tower) 初始实现
- 规则基调度算法 (Priority, Greedy 等)
- 指标监控系统初版: Metrics calculator、Data collector

#### 2025-12: 实验体系建立
- 基准测试结果更新与指标计算增强
- AGV 堵塞监控与可视化
- 基于 Monitor 的指标采集系统迁移
- 预实验 (pre-experiment) 框架搭建
- 电池装配产线 (Packet Factory) 适配

#### 2026-01 ~ 03: 算法深化
- GPT 模块更新 (MAPF-GPT 路由)
- 系统文档完善 (SkyEngine system overview)
- Docker Compose 联调: engine / FJSP solver / MAPF solver 三服务架构验证通过
- `run.py` 标准化运行入口完成
- 仿真状态推进与任务完成判定调试

#### 2026-05 ~ 06: 稳定化与训练框架
- **特征工程完成**: 统一特征提取器 (8D pairwise + node features)
- **训练框架完善**: 新增 DQN、PPO、GRPO、REINFORCE 四种训练器 + 知识蒸馏框架
- **实验模块清理**: 移除 37,242 行冗余代码 (旧 solver、过时实验、废弃文档)
- **MoE (Mixture of Experts)**: GRPO/PPO 两种 MoE 训练框架就绪
- **数据集管理**: 统一 dataset helper，支持多格式转换
- **网络结构更新**: Trainer 支持新网络架构
- **Mask 机制**: 添加动作 mask 支持合法动作约束

### 2.3 核心技术进展

| 模块 | 进展 | 状态 |
|------|------|------|
| Assigner 组件 | 12+ 分配策略 (FIFO, Hungarian, Greedy, NN, MoE 等) | ✅ 稳定 |
| JobSolver | Priority、HTTP、Rule-based 三种调度器 | ✅ 稳定 |
| RouteSolver | A*、Greedy、HTTP 三种路径规划器 | ✅ 稳定 |
| 特征提取 | 8D pairwise + node features 统一表示 | ✅ 完成 |
| RL 训练 | DQN / PPO / GRPO / REINFORCE | ✅ 可用 |
| 知识蒸馏 | 专家策略 → 神经网络 | ✅ 可用 |
| MoE 框架 | GRPO + PPO 两种 MoE 训练 | ✅ 可用 |
| Docker 部署 | 实验模式 + 在线服务模式双配置 | ✅ 验证通过 |
| 数据集 | FJSP / MAPF / JSPLIB 多源支持 | ✅ 统一 |

---

## 三、grouppro/skyengine — 服务层平台

### 3.1 项目结构

```
skyengine/
├── application/
│   ├── backend/                # FastAPI 后端
│   │   ├── server.py           # 主服务 (SSE + REST)
│   │   └── core/               # 工厂代理系统
│   │       ├── BaseFactoryProxy.py    # 代理协议抽象基类
│   │       ├── ProxyFactory.py        # 代理注册工厂
│   │       ├── RouteRegistry.py       # 路由注册中心
│   │       ├── StaticFactoryProxy.py  # 静态演示代理
│   │       ├── PacketFactoryProxy.py  # 电池产线代理
│   │       └── DockerProxy.py         # 容器化仿真代理 (新)
│   └── frontend/               # Vue.js 3 前端
│       └── src/
│           ├── components/     # UI 组件
│           │   ├── FactoryVisualization3D.vue  # Three.js 3D 可视化 (新)
│           │   ├── DraggablePanel.vue          # 可拖拽浮动面板 (新)
│           │   ├── FactoryTabsPanel.vue        # 标签面板系统 (新)
│           │   ├── AgentPanel.vue              # Agent 分析面板 (新)
│           │   ├── FactoryAssetPanel.vue       # 资产编辑面板 (新)
│           │   ├── ControlPanel.vue            # 播放控制
│           │   ├── ConfigPanel.vue             # 配置管理
│           │   ├── MetricsPanel.vue            # 指标面板
│           │   └── EventPanel.vue              # 日志面板
│           ├── views/factory/  # 工厂管理视图
│           │   ├── GridFactoryManage.vue       # 网格工厂
│           │   ├── DockerFactoryManage.vue     # 容器化工坊 (新)
│           │   └── StaticFactoryManage.vue     # 静态演示
│           ├── stores/         # Pinia 状态管理
│           └── utils/          # 工具 (SSE, API, 3D 资产)
├── executor/                   # 算法执行器层
│   ├── grid_factory/           # 网格工厂环境
│   ├── packet_factory/         # 电池产线环境
│   └── factory_template/       # 工厂模板
├── config/                     # 工厂配置 JSON
├── dataset/                    # 基准数据集
└── docker-compose.yml          # 容器编排
```

### 3.2 近期进展时间线

#### 2025-10 ~ 11: 算法层重构
- 作业车间环境重构，Pogema 集成
- 任务分配器与环境分离
- Dockerfile 初始版本

#### 2026-03: 平台化 v1.0
- **公开发布 v1.0.0** (3月10日)
- PacketFactoryProxy 代理类实现
- 前端配置上传、工厂列表刷新等基础功能完善
- 项目文档 (CLAUDE.md, README.md, 快速入门指南)
- A* 路由测试通过
- joint_sim 更新

#### 2026-05: 3D 可视化与资产系统
- **Three.js 3D 可视化**: 完整的 3D 工厂场景渲染
  - 机器、AGV、区域、路径点、装饰物渲染
  - 工厂车间背景 (立柱、横梁、墙面、照明灯)
  - 轨道控制器、射线拾取、编辑模式拖拽
- **资产编辑系统**: 增删改查、模板添加、碰撞校验、导出配置
- **浮动面板系统**: 可拖拽面板 + 标签页切换
- **代码清理**: 移除 232,844 行废弃代码 (POGEMA 算法、旧路由等)

#### 2026-06: 容器化调度与分析模块
- **DockerProxy**: 容器化仿真管理，通过 Docker Compose 按需启停 SkyEngine 服务栈
- **DockerFactoryManage**: 容器化工坊视图，三步式操作 (设定策略 → 重置 → 启动)
- **AgentPanel**: Agent 智能分析面板
  - AGV 实时状态卡片
  - 6 个预设分析模板 (效率分析、瓶颈检测、负载均衡等)
  - 自定义问题输入 + 分析结果展示框
- **暗色主题统一**: 全部面板统一为玻璃拟态暗色风格
- **3D 坐标修复**: gridToWorld 半格偏移 bug 修复

### 3.3 核心技术进展

| 模块 | 进展 | 状态 |
|------|------|------|
| 工厂代理系统 | 5 种代理 (Static/Packet/Grid/Docker + 基类) | ✅ 稳定 |
| 3D 可视化 | Three.js 全场景渲染 + 编辑模式 | ✅ 完成 |
| 容器化调度 | DockerProxy + Compose 编排 | ✅ 可用 |
| SSE 实时推送 | 状态/指标/控制三通道 | ✅ 稳定 |
| 资产管理 | 模板化 CRUD + 碰撞校验 | ✅ 完成 |
| Agent 分析 | 预设模板 + 实时数据驱动 | ✅ 基础完成 |
| 前端主题 | 暗色玻璃拟态统一 | ✅ 完成 |
| 工厂视图 | Grid/Docker/Static/Packet 四种 | ✅ 完成 |

---

## 四、两仓库协作关系

```
┌─────────────────────────────────────────────────────────┐
│  grouppro/skyengine  (服务层)                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  Vue.js 前端  │  │ FastAPI 后端  │  │ DockerProxy  │  │
│  │  Three.js 3D  │←→│  SSE/REST    │←→│ 容器管理     │  │
│  └──────────────┘  └──────┬───────┘  └──────┬───────┘  │
│                           │                   │          │
│         executor/         │     docker compose │          │
│         (本地执行)         │                    │          │
└───────────────────────────┼───────────────────┼──────────┘
                            │                   │
                   直接调用  │                   │ HTTP API
                            ▼                   ▼
┌─────────────────────────────────────────────────────────┐
│  finalpro/SkyEngine  (算法层)                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ Grid Factory │  │ FJSP Solver  │  │ MAPF Solver  │  │
│  │ 仿真引擎     │  │ 作业排程     │  │ 路径规划     │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│  ┌──────────────┐  ┌──────────────┐                      │
│  │   Trainer    │  │  Experiment  │                      │
│  │ DQN/PPO/GRPO │  │ MoE/蒸馏     │                      │
│  └──────────────┘  └──────────────┘                      │
└─────────────────────────────────────────────────────────┘
```

- **grouppro** 负责用户交互、可视化、工厂生命周期管理
- **finalpro** 负责仿真执行、算法求解、训练推理
- 两者通过 Docker Compose 的内部网络 (HTTP API) 通信
- grouppro 的 `executor/` 目录包含 finalpro 的轻量子集用于本地模式

---

## 五、待办与下一步

### finalpro/SkyEngine
- [ ] MoE 训练完整实验流程验证
- [ ] 专家蒸馏效果评估
- [ ] 大规模 benchmark (JSPLIB 全集)
- [ ] GPU 训练稳定性测试

### grouppro/skyengine
- [ ] AgentPanel 接入 AI 分析引擎 (当前为规则基分析)
- [ ] 多工厂并发仿真支持
- [ ] 3D 可视化性能优化 (大规模场景)
- [ ] 移动端适配
- [ ] 用户权限与多租户
