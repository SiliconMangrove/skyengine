# Job Shop + MAPF Joint Task - Algorithm Evaluation Benchmark Guide

## Overview

你现在拥有一个**完整的算法评测平台**，可以：
- ✅ 支持多个调度/路由算法的同时评估
- ✅ 在多个问题规模和实例上进行实验
- ✅ 自动收集关键性能指标（makespan、flowtime、AGV 利用率等）
- ✅ 生成对比报告和可视化图表
- ✅ 导出结果到 CSV、JSON、Excel、Markdown

## Architecture

```
BenchmarkManager
  ├── EvaluationRunner (执行单次运行)
  │   ├── 环境初始化 (GridFactoryEnv)
  │   ├── 算法/求解器 (Coordinator)
  │   └── 指标抽取 (Metrics)
  │
  ├── MetricsAnalyzer (分析结果)
  │   ├── 算法间对比统计
  │   └── 生成摘要表格
  │
  └── BenchmarkReporter (生成报告)
      ├── Markdown 报告
      ├── 图表可视化
      └── Excel 导出
```

## Quick Start

### 1. 最简单的方式：单个算法快速评估

```python
from sky_executor.grid_factory.factory.Benchmark import (
    AlgorithmConfig,
    EvaluationRunner,
    BenchmarkScenario,
)
from sky_executor.grid_factory.factory.Component.Coordinator.coordinator import Coordinator
from sky_executor.grid_factory.factory.grid_factory_env import GridFactoryEnv

# 定义场景
scenario = BenchmarkScenario(
    name="test_scenario",
    grid_size=16,
    num_machines=8,
    num_jobs=6,
    num_agvs=4,
    max_episode_steps=100,
    seed=42,
)

# 定义算法
algo = AlgorithmConfig(
    name="Baseline Coordinator",
    solver_factory=Coordinator,
    description="Default greedy coordinator",
)

# 运行评估
runner = EvaluationRunner(env_factory=GridFactoryEnv)
metrics = runner.run(scenario, algo, verbose=True)

# 查看结果
print(f"Makespan: {metrics.total_makespan}")
print(f"Flowtime: {metrics.total_flowtime}")
print(f"AGV Utilization: {metrics.agv_utilization:.1%}")
```

### 2. 多算法对比

```python
from sky_executor.grid_factory.factory.Benchmark import (
    BenchmarkManager,
    AlgorithmConfig,
    ScenarioBuilder,
    ProblemScale,
    MetricsAnalyzer,
)

# 定义多个算法
algorithms = [
    AlgorithmConfig(
        name="Greedy",
        solver_factory=lambda: Coordinator(),
        description="Baseline greedy scheduler",
    ),
    # TODO: 添加你的其他算法
    # AlgorithmConfig(
    #     name="L2D-DRL",
    #     solver_factory=lambda: L2DJobSolver(...),
    #     description="Deep RL job scheduler",
    # ),
]

# 定义多个测试场景
scales = [ProblemScale.SMALL, ProblemScale.MEDIUM]
seeds = [42, 123, 456]
scenarios = ScenarioBuilder.create_suite(scales, seeds)

# 运行完整的 benchmark
manager = BenchmarkManager(
    env_factory=GridFactoryEnv,
    results_dir="./benchmark_results",
)

results_by_algo = manager.run_benchmark(
    algorithms,
    scenarios,
    export_format="both",  # CSV + JSON
)

# 分析结果
analyzer = MetricsAnalyzer(manager.all_results)
analyzer.print_summary()
```

### 3. 生成报告和图表

```python
from sky_executor.grid_factory.factory.Benchmark import BenchmarkReporter

reporter = BenchmarkReporter("./benchmark_results")

# 生成 Markdown 报告
reporter.generate_report(results_by_algo, output_name="benchmark_report")

# 生成对比图表
reporter.plot_comparison(results_by_algo, output_name="comparison")

# 导出 Excel（如果安装了 openpyxl）
from sky_executor.grid_factory.factory.Benchmark import ExcelExporter
ExcelExporter.export(results_by_algo, "benchmark_results/results.xlsx")
```

## 核心类详解

### BenchmarkScenario

定义一个测试场景（问题实例）。

```python
scenario = BenchmarkScenario(
    name="test",
    grid_size=16,              # 网格大小
    num_machines=8,            # 机器数量
    num_jobs=6,                # 任务数量
    min_ops_per_job=2,         # 最少操作数
    max_ops_per_job=4,         # 最多操作数
    min_proc_time=2,           # 最少加工时间
    max_proc_time=7,           # 最多加工时间
    num_agvs=4,                # AGV 数量
    max_episode_steps=500,     # 最大步数
    seed=42,                   # 随机种子
)
```

### AlgorithmConfig

定义一个算法/求解器。

```python
algo = AlgorithmConfig(
    name="MyAlgorithm",
    solver_factory=lambda: MyCoordinator(...),  # 返回求解器实例
    description="My custom scheduler",
    hyperparams={
        "param1": value1,
        "param2": value2,
    },
)
```

### EvaluationMetrics

单次运行的评估结果。

```python
metrics = EvaluationMetrics(
    algorithm_name="...",
    scenario_name="...",
    total_makespan=1234.5,           # 总完成时间
    total_flowtime=5678.9,           # 总流时间
    avg_job_flowtime=100.0,          # 平均任务流时间
    agv_utilization=0.75,            # AGV 利用率
    agv_avg_distance=123.4,          # AGV 平均行驶距离
    machine_utilization=0.85,        # 机器利用率
    solution_time=5.2,               # 求解时间（秒）
    episode_length=500,              # 运行长度（步）
    success=True,
    error_msg="",
)
```

## 预定义的问题规模

```python
from sky_executor.grid_factory.factory.Benchmark import ProblemScale

ProblemScale.TINY      # 2 machines, 2 jobs, 2 AGVs (最小)
ProblemScale.SMALL     # 4 machines, 3 jobs, 6 AGVs
ProblemScale.MEDIUM    # 8 machines, 6 jobs, 8 AGVs
ProblemScale.LARGE     # 12 machines, 10 jobs, 12 AGVs (最大)
```

快速创建一套测试场景：

```python
scenarios = ScenarioBuilder.create_suite(
    scales=[ProblemScale.SMALL, ProblemScale.MEDIUM],
    seeds=[42, 123, 456],  # 3 个不同的种子，每个规模 3 个实例
)
# 总共 2 × 3 = 6 个测试场景
```

## 关键性能指标（KPIs）

### Job Shop 指标

| 指标 | 含义 | 越小越好 |
|------|------|--------|
| `total_makespan` | 所有工作的总完成时间 | ✓ |
| `total_flowtime` | 所有工作在系统中的总时间 | ✓ |
| `avg_job_flowtime` | 平均每个工作的流时间 | ✓ |
| `total_ops_completed` | 完成的操作数量 | ✗ |
| `machine_utilization` | 机器的平均利用率 | ✗ |

### AGV / MAPF 指标

| 指标 | 含义 | 越小越好 |
|------|------|--------|
| `agv_avg_distance` | AGV 的平均行驶距离 | ✓ |
| `agv_utilization` | AGV 的平均利用率（忙碌时间比例） | ✗ |
| `agv_avg_idle_time` | AGV 的平均闲置时间 | ✓ |

### 系统指标

| 指标 | 含义 |
|------|------|
| `solution_time` | 从环境初始化到完成的总时间（秒） |
| `episode_length` | 运行的时间步数 |
| `success` | 是否成功完成 |

## 实战示例

### 例子 1：验证新算法

```python
# 你实现了一个新的求解器 NewScheduler
from my_algorithms import NewScheduler

new_algo = AlgorithmConfig(
    name="NewScheduler",
    solver_factory=NewScheduler,
    description="My new scheduler",
)

# 在小规模上快速测试
scenario = ScenarioBuilder.from_scale(ProblemScale.SMALL, seed=42)
runner = EvaluationRunner(env_factory=GridFactoryEnv)
metrics = runner.run(scenario, new_algo, verbose=True)

if metrics.success:
    print(f"✓ 新算法成功！Makespan = {metrics.total_makespan}")
else:
    print(f"✗ 新算法失败: {metrics.error_msg}")
```

### 例子 2：对比论文中的方法

```python
# 假设你有多篇论文中的算法实现
algorithms = [
    AlgorithmConfig(
        name="Baseline (FIFO)",
        solver_factory=lambda: FIFOScheduler(),
        description="Simple FIFO scheduling",
    ),
    AlgorithmConfig(
        name="Paper A: SPT",
        solver_factory=lambda: SPTScheduler(),
        description="Shortest Processing Time first",
    ),
    AlgorithmConfig(
        name="Paper B: L2D",
        solver_factory=lambda: L2DScheduler(),
        description="Learning to Dispatch (L2D)",
    ),
]

# 在标准测试集上对比
scenarios = ScenarioBuilder.create_suite(
    scales=[ProblemScale.SMALL, ProblemScale.MEDIUM, ProblemScale.LARGE],
    seeds=list(range(10)),  # 10 个不同的种子
)

manager = BenchmarkManager(env_factory=GridFactoryEnv)
results = manager.run_benchmark(algorithms, scenarios)

# 生成论文级的对比报告
reporter = BenchmarkReporter("./paper_results")
reporter.generate_report(results, "comparison")
reporter.plot_comparison(results)
```

### 例子 3：参数敏感性分析

```python
# 测试求解器的不同超参数配置

hyperparams = [
    {"strategy": "greedy"},
    {"strategy": "priority", "priority_weight": 0.5},
    {"strategy": "priority", "priority_weight": 0.9},
]

algorithms = [
    AlgorithmConfig(
        name=f"Scheduler (param={hp})",
        solver_factory=lambda hp=hp: MyScheduler(**hp),
        description=f"Config: {hp}",
        hyperparams=hp,
    )
    for hp in hyperparams
]

# 运行对比
results = manager.run_benchmark(algorithms, scenarios)
```

## 导出结果

### CSV 导出

自动生成时间戳的 CSV 文件，包含所有详细结果。

```
benchmark_20240415_143022.csv

algorithm_name,scenario_name,total_makespan,total_flowtime,...
Greedy,small_seed42,234.5,567.8,...
Greedy,small_seed123,245.3,578.9,...
```

### JSON 导出

```json
{
  "timestamp": "20240415_143022",
  "results": {
    "Greedy": [
      {
        "algorithm_name": "Greedy",
        "scenario_name": "small_seed42",
        "total_makespan": 234.5,
        ...
      }
    ]
  }
}
```

### Markdown 报告

自动生成可读的对比报告：

```markdown
# Benchmark Report

## Summary

| Algorithm | Avg Makespan | Avg Flowtime | ... |
|-----------|-------------|------------|-----|
| Greedy    | 240.1       | 572.3      | ... |
| L2D       | 215.3       | 501.2      | ... |

## Detailed Results

### Greedy
...
```

## 高级配置

### 自定义指标收集

如果你想添加自定义指标，修改 `EvaluationRunner._extract_metrics` 方法：

```python
def custom_extract_metrics(env, metrics, scenario):
    # 添加你自己的指标计算
    metrics.my_custom_metric = compute_something(env)
    return metrics
```

### 支持自定义环境

如果你的环境与 `GridFactoryEnv` 接口不同，创建一个 adapter：

```python
class MyEnvAdapter:
    def __init__(self, **kwargs):
        self.env = MyEnv(**kwargs)
    
    def reset(self, seed=None):
        return self.env.reset(seed)
    
    def step(self, action):
        return self.env.step(action)
    
    @property
    def pogema_env(self):
        return self.env  # 确保有这个属性
```

## 故障排查

### 问题：环境初始化失败

- 确保 `GridFactoryEnv` 能正常创建
- 检查所有配置参数的有效性
- 运行 `sky_test_new_env.py` 确认基础环境工作

### 问题：指标为 0 或 NaN

- 确保环境正确运行了足够步数
- 检查 `pogema_env` 是否有 `jobs`、`agv_stats` 等属性
- 验证算法是否返回了有效的动作

### 问题：内存溢出

- 减少 `max_episode_steps`
- 减少并行评估的数量
- 使用 smaller 的问题规模进行测试

## 下一步

1. **集成你的算法**：为 L2D、MAPF-GPT 等创建 AlgorithmConfig
2. **扩展指标**：根据你的论文需求添加自定义 KPI
3. **自动化 CI/CD**：将 benchmark 集成到持续集成流程中
4. **定期评测**：建立评测基准线（baseline），追踪进度

## 相关文件

- 核心模块：`sky_executor/grid_factory/factory/Benchmark/`
  - `benchmark.py` - 核心评测框架
  - `reporter.py` - 报告和可视化
  - `__init__.py` - 模块接口

- 测试脚本：
  - `test/pogema_test/run_benchmark.py` - 完整 benchmark 示例
  - `test/pogema_test/sky_test_benchmark.py` - 集成示例

## 许可和引用

如果你在论文中使用了这个 benchmark 框架，请引用：

```
@inproceedings{SkyEngine2024,
  title={SkyEngine: A Joint Job Shop + MAPF Benchmark for Integrated Scheduling},
  author={...},
  year={2024},
}
```
