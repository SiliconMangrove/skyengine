# Quick Reference - SkyEngine Benchmark API

## 导入

```python
from sky_executor.grid_factory.factory.Benchmark import (
    # 核心类
    BenchmarkManager,          # 管理完整的 benchmark 流程
    EvaluationRunner,          # 执行单次评估
    MetricsAnalyzer,           # 分析和对比结果
    BenchmarkReporter,         # 生成报告
    ExcelExporter,             # 导出 Excel
    
    # 配置类
    BenchmarkScenario,         # 测试场景定义
    AlgorithmConfig,           # 算法配置
    EvaluationMetrics,         # 评估结果
    
    # 工具类
    ScenarioBuilder,           # 场景构建器
    ProblemScale,              # 问题规模枚举
)
from sky_executor.grid_factory.factory.grid_factory_env import GridFactoryEnv
```

## 最快速的用法 (3 行代码)

```python
from sky_executor.grid_factory.factory.Benchmark import (
    quick_benchmark, AlgorithmConfig, ProblemScale
)

# 快速测试单个算法在单个场景上的性能
metrics = quick_benchmark(
    algo=AlgorithmConfig("MyAlgo", MySolver),
    scale=ProblemScale.SMALL,
)
print(f"Makespan: {metrics.total_makespan}")
```

## 常见任务

### 任务 1: 运行单个算法的单次评估

```python
from sky_executor.grid_factory.factory.Benchmark import (
    EvaluationRunner, BenchmarkScenario, AlgorithmConfig
)

scenario = BenchmarkScenario(
    name="test",
    grid_size=16,
    num_machines=4,
    num_jobs=3,
    num_agvs=2,
)

algo = AlgorithmConfig("MyAlgo", MyCoordinator)

runner = EvaluationRunner(env_factory=GridFactoryEnv)
metrics = runner.run(scenario, algo, verbose=True)

print(metrics)  # 查看所有 KPI
```

### 任务 2: 对比多个算法

```python
from sky_executor.grid_factory.factory.Benchmark import (
    BenchmarkManager, AlgorithmConfig, ScenarioBuilder, ProblemScale
)

algorithms = [
    AlgorithmConfig("Algo1", Solver1),
    AlgorithmConfig("Algo2", Solver2),
    AlgorithmConfig("Algo3", Solver3),
]

scenarios = ScenarioBuilder.create_suite(
    scales=[ProblemScale.SMALL, ProblemScale.MEDIUM],
    seeds=[42, 123],
)

manager = BenchmarkManager(env_factory=GridFactoryEnv)
results = manager.run_benchmark(algorithms, scenarios)
```

### 任务 3: 生成对比报告

```python
from sky_executor.grid_factory.factory.Benchmark import (
    MetricsAnalyzer, BenchmarkReporter
)

# 分析结果
analyzer = MetricsAnalyzer(manager.all_results)
analyzer.print_summary()  # 打印摘要表格

# 生成报告
reporter = BenchmarkReporter(manager.results_dir)
reporter.generate_report(results, "my_report")
reporter.plot_comparison(results)
```

### 任务 4: 创建自定义算法

```python
class MyCustomSolver:
    def __init__(self, **hyperparams):
        self.config = hyperparams
    
    def decide(self, observation):
        # 你的决策逻辑
        return {
            "job_id": 0,
            "machine_id": 0,
        }

algo = AlgorithmConfig(
    name="My Custom Solver",
    solver_factory=lambda: MyCustomSolver(param1=value1),
    description="My algorithm",
    hyperparams={"param1": value1},
)
```

### 任务 5: 使用特定的随机种子重复实验

```python
scenario = BenchmarkScenario(
    name="reproducible_test",
    seed=42,  # 固定种子确保可重复
    # ... other params
)
```

## 关键参数速查

### BenchmarkScenario

| 参数 | 默认值 | 说明 |
|-----|-------|------|
| `name` | 必需 | 场景名称 |
| `grid_size` | 16 | 网格大小（16x16） |
| `num_machines` | 4 | 机器数量 |
| `num_jobs` | 3 | 工作/任务数量 |
| `num_agvs` | 2 | AGV 数量 |
| `min_ops_per_job` | 2 | 每个工作最少操作数 |
| `max_ops_per_job` | 4 | 每个工作最多操作数 |
| `min_proc_time` | 2 | 最少加工时间 |
| `max_proc_time` | 7 | 最多加工时间 |
| `max_episode_steps` | 500 | 最大时间步 |
| `seed` | 42 | 随机种子 |

### ProblemScale (预定义规模)

```python
ProblemScale.TINY      # 2M×2J×2A (最小)
ProblemScale.SMALL     # 4M×3J×6A
ProblemScale.MEDIUM    # 8M×6J×8A
ProblemScale.LARGE     # 12M×10J×12A (最大)

# M = machines, J = jobs, A = AGVs
```

## EvaluationMetrics 字段

```python
metrics.total_makespan          # 总完成时间 (越小越好)
metrics.total_flowtime          # 总流时间 (越小越好)
metrics.avg_job_flowtime        # 平均任务流时间
metrics.total_ops_completed     # 完成的操作数
metrics.agv_utilization         # AGV 利用率 (0-1)
metrics.agv_avg_distance        # AGV 平均行驶距离
metrics.agv_avg_idle_time       # AGV 平均闲置时间
metrics.machine_utilization     # 机器利用率 (0-1)
metrics.solution_time           # 求解耗时（秒）
metrics.episode_length          # 运行步数
metrics.success                 # 是否成功
metrics.error_msg               # 错误信息（如果失败）
```

## 文件输出

```
benchmark_results/
├── benchmark_20240415_143022.csv    # 详细结果 CSV
├── results_20240415_143022.json     # 详细结果 JSON
├── benchmark_report.md              # 对比报告
├── comparison_makespan.png          # 图表：Makespan 对比
├── comparison_flowtime.png          # 图表：Flowtime 对比
├── comparison_utilization.png       # 图表：Utilization 对比
└── benchmark_results.xlsx           # Excel 导出（可选）
```

## 常见问题

**Q: 如何加速 benchmark？**
A: 减少 `max_episode_steps`、`num_jobs`、`grid_size` 或运行的 `seeds` 数量。

**Q: 如何确保结果可重复？**
A: 为 BenchmarkScenario 设置 `seed` 参数。

**Q: 如何添加新的性能指标？**
A: 修改 `EvaluationRunner._extract_metrics()` 方法。

**Q: 如何支持自定义环境？**
A: 创建一个 adapter 确保它有 `reset()`、`step()` 和 `pogema_env` 属性。

**Q: 内存不足怎么办？**
A: 启用分批评估，减少并行运行数量，或使用更小的问题规模。

## 完整工作流模板

```python
#!/usr/bin/env python3

from sky_executor.grid_factory.factory.Benchmark import (
    BenchmarkManager,
    AlgorithmConfig,
    ScenarioBuilder,
    ProblemScale,
    MetricsAnalyzer,
    BenchmarkReporter,
)
from sky_executor.grid_factory.factory.grid_factory_env import GridFactoryEnv
from my_algorithms import MyAlgorithm1, MyAlgorithm2

# 1. 定义算法
algorithms = [
    AlgorithmConfig("Algorithm 1", MyAlgorithm1),
    AlgorithmConfig("Algorithm 2", MyAlgorithm2),
]

# 2. 定义测试场景
scenarios = ScenarioBuilder.create_suite(
    scales=[ProblemScale.SMALL, ProblemScale.MEDIUM],
    seeds=list(range(5)),
)

# 3. 运行 benchmark
manager = BenchmarkManager(env_factory=GridFactoryEnv)
results = manager.run_benchmark(algorithms, scenarios)

# 4. 分析结果
analyzer = MetricsAnalyzer(manager.all_results)
analyzer.print_summary()

# 5. 生成报告
reporter = BenchmarkReporter(manager.results_dir)
reporter.generate_report(results)
reporter.plot_comparison(results)

print(f"Results saved to {manager.results_dir}")
```

## 集成你的模型

参考 `integration_guide.py` 中的示例：

- `L2DJobDispatcher` - 集成 L2D 工作调度模型
- `MapfGPTRouter` - 集成 MAPF-GPT 路由模型
- `HybridScheduler` - 结合两个模型

## 更多信息

完整文档：`Benchmark/README.md`  
集成指南：`Benchmark/integration_guide.py`  
示例脚本：`test/pogema_test/run_benchmark.py`
