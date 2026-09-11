"""
Integration Guide: 如何集成你的算法到评测平台

这个文件展示了如何将你的算法（L2D、MAPF-GPT、自定义启发式等）
集成到 SkyEngine Benchmark 框架中。
"""

import os
import sys
from pathlib import Path

# ============================================================================
# 示例 1: 包装现有的 Coordinator（基线）
# ============================================================================

def create_baseline_solver():
    """
    最简单的情况：使用默认的 Coordinator 作为基线
    """
    from sky_executor.grid_factory.factory.Component.Coordinator.coordinator import Coordinator
    from sky_executor.grid_factory.factory.Benchmark import AlgorithmConfig

    return AlgorithmConfig(
        name="Baseline Greedy",
        solver_factory=Coordinator,
        description="Default greedy coordinator with FIFO scheduling",
    )


# ============================================================================
# 示例 2: 实现一个自定义调度器（规则启发式）
# ============================================================================

class RuleBasedScheduler:
    """
    规则启发式调度器示例。
    你可以这样包装你的自定义算法。
    """

    def __init__(self, strategy="spt"):
        self.strategy = strategy  # "spt": Shortest Processing Time

    def decide(self, observation):
        """
        决策函数 - 这是 benchmark 框架调用的方法。
        
        参数：
            observation: 环境返回的观察值
        
        返回：
            action: dict，包含调度决策
                {
                    "job_id": <选择的工作>,
                    "machine_id": <选择的机器>,
                    "pickup_location": <AGV 接货位置>,
                    "delivery_location": <AGV 送货位置>,
                }
        """
        # 示例：选择最短加工时间的操作
        if self.strategy == "spt":
            # 从观察值中提取待处理操作
            # (具体实现取决于 observation 的格式)
            next_op = self._select_shortest_job(observation)
            return next_op

        return {"job_id": 0, "machine_id": 0}

    def _select_shortest_job(self, observation):
        """示例：选择最短处理时间的工作"""
        # TODO: 实现你的启发式逻辑
        return {"job_id": 0, "machine_id": 0}


def create_rule_based_solver(strategy="spt"):
    """创建规则启发式求解器的 AlgorithmConfig"""
    from sky_executor.grid_factory.factory.Benchmark import AlgorithmConfig

    return AlgorithmConfig(
        name=f"Rule-Based ({strategy})",
        solver_factory=lambda: RuleBasedScheduler(strategy=strategy),
        description=f"Rule-based heuristic: {strategy}",
        hyperparams={"strategy": strategy},
    )


# ============================================================================
# 示例 3: 包装 L2D 模型进行工作调度
# ============================================================================

class L2DJobDispatcher:
    """
    使用 L2D (Learning to Dispatch) 模型进行工作调度。
    """

    def __init__(self, model_path=None, device="cpu"):
        self.model_path = model_path
        self.device = device
        self.model = None

        if model_path and os.path.exists(model_path):
            self._load_model(model_path)

    def _load_model(self, model_path):
        """
        从 checkpoint 加载 L2D 模型。
        
        修改这个方法以匹配你的 L2D 实现。
        """
        # 示例（根据你的 L2D 实现调整）：
        # import torch
        # from l2d import L2DNet
        # self.model = L2DNet()
        # self.model.load_state_dict(torch.load(model_path))
        # self.model.eval()
        pass

    def decide(self, observation):
        """
        使用 L2D 模型做决策。
        """
        if self.model is None:
            # 没有加载模型，降级到贪心
            return {"job_id": 0, "machine_id": 0}

        # 提取特征从 observation
        features = self._extract_features(observation)

        # 前向传播
        # predicted_action = self.model(features)
        # action = self._convert_to_action(predicted_action)

        return {"job_id": 0, "machine_id": 0}

    def _extract_features(self, observation):
        """从环境观察中提取 L2D 需要的特征"""
        # TODO: 实现特征提取逻辑
        # 返回 torch.Tensor 或 np.ndarray
        pass

    def _convert_to_action(self, predicted_action):
        """将模型输出转换为可执行的动作"""
        # TODO: 实现转换逻辑
        return {"job_id": 0, "machine_id": 0}


def create_l2d_solver(model_path=None):
    """创建 L2D 求解器的 AlgorithmConfig"""
    from sky_executor.grid_factory.factory.Benchmark import AlgorithmConfig

    algo_name = "L2D" if model_path else "L2D (No Model)"
    description = f"Learning to Dispatch model" + (
        f": {Path(model_path).name}" if model_path else " (fallback to greedy)"
    )

    return AlgorithmConfig(
        name=algo_name,
        solver_factory=lambda: L2DJobDispatcher(model_path=model_path),
        description=description,
        hyperparams={"model_path": model_path},
    )


# ============================================================================
# 示例 4: 包装 MAPF-GPT 模型进行路由规划
# ============================================================================

class MapfGPTRouter:
    """
    使用 MAPF-GPT (Multi-Agent Path Finding with GPT) 进行 AGV 路由。
    """

    def __init__(self, model_path=None, device="cpu"):
        self.model_path = model_path
        self.device = device
        self.model = None
        self.tokenizer = None

        if model_path and os.path.exists(model_path):
            self._load_model(model_path)

    def _load_model(self, model_path):
        """
        从 checkpoint 加载 MAPF-GPT 模型。
        
        修改这个方法以匹配你的 MAPF-GPT 实现。
        """
        # 示例（根据你的 MAPF-GPT 实现调整）：
        # from transformers import AutoTokenizer, AutoModelForCausalLM
        # self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        # self.model = AutoModelForCausalLM.from_pretrained(model_path)
        pass

    def decide(self, observation):
        """
        使用 MAPF-GPT 模型做路由决策。
        """
        if self.model is None:
            # 降级到随机路由
            return {
                "pickup_location": (0, 0),
                "delivery_location": (8, 8),
            }

        # 构造 prompt
        prompt = self._construct_prompt(observation)

        # 调用模型
        # response = self.model.generate(prompt, ...)
        # action = self._parse_response(response)

        return {
            "pickup_location": (0, 0),
            "delivery_location": (8, 8),
        }

    def _construct_prompt(self, observation):
        """为 MAPF-GPT 构造 prompt"""
        # TODO: 实现 prompt 构造
        return ""

    def _parse_response(self, response):
        """解析模型的文本响应为动作"""
        # TODO: 实现响应解析
        return {
            "pickup_location": (0, 0),
            "delivery_location": (8, 8),
        }


def create_mapf_gpt_solver(model_path=None):
    """创建 MAPF-GPT 求解器的 AlgorithmConfig"""
    from sky_executor.grid_factory.factory.Benchmark import AlgorithmConfig

    algo_name = "MAPF-GPT" if model_path else "MAPF-GPT (No Model)"
    description = f"Multi-Agent Path Finding with GPT" + (
        f": {Path(model_path).name}" if model_path else " (fallback to random)"
    )

    return AlgorithmConfig(
        name=algo_name,
        solver_factory=lambda: MapfGPTRouter(model_path=model_path),
        description=description,
        hyperparams={"model_path": model_path},
    )


# ============================================================================
# 示例 5: 混合求解器 - 结合工作调度和路由规划
# ============================================================================

class HybridScheduler:
    """
    结合工作调度（L2D）和路由规划（MAPF-GPT）的混合求解器。
    这演示了双塔架构的实现。
    """

    def __init__(self, job_dispatcher=None, route_planner=None):
        self.job_dispatcher = job_dispatcher or L2DJobDispatcher()
        self.route_planner = route_planner or MapfGPTRouter()

    def decide(self, observation):
        """
        两步决策：
        1. Job Dispatcher Tower：选择要调度的工作
        2. Route Planner Tower：规划 AGV 路由
        """
        # 步骤 1：工作调度决策
        job_decision = self.job_dispatcher.decide(observation)

        # 步骤 2：路由规划决策
        route_decision = self.route_planner.decide(observation)

        # 融合两个决策
        action = {
            **job_decision,
            **route_decision,
        }

        return action


def create_hybrid_solver(job_model_path=None, route_model_path=None):
    """创建混合求解器的 AlgorithmConfig"""
    from sky_executor.grid_factory.factory.Benchmark import AlgorithmConfig

    algo_name = "Hybrid (L2D + MAPF-GPT)"
    description = (
        f"Dual-tower: L2D for job dispatch, MAPF-GPT for routing"
    )

    return AlgorithmConfig(
        name=algo_name,
        solver_factory=lambda: HybridScheduler(
            job_dispatcher=L2DJobDispatcher(job_model_path),
            route_planner=MapfGPTRouter(route_model_path),
        ),
        description=description,
        hyperparams={
            "job_model": job_model_path,
            "route_model": route_model_path,
        },
    )


# ============================================================================
# 完整示例：运行 benchmark 对比所有算法
# ============================================================================

def main():
    """
    完整的 benchmark 流程示例。
    """
    from sky_executor.grid_factory.factory.Benchmark import (
        BenchmarkManager,
        ScenarioBuilder,
        ProblemScale,
        MetricsAnalyzer,
        BenchmarkReporter,
    )
    from sky_executor.grid_factory.factory.grid_factory_env import GridFactoryEnv

    print("=" * 60)
    print("SkyEngine Algorithm Benchmark - Integration Example")
    print("=" * 60)

    # 1. 创建要对比的算法列表
    print("\n[Step 1] Creating algorithm configurations...")
    algorithms = [
        create_baseline_solver(),
        create_rule_based_solver("spt"),
        # 示例：使用 component 策略字符串创建 Coordinator
        # 这将通过 Benchmark.CoordinatorConfig 构造一个 Coordinator
        # AlgorithmConfig.from_coordinator 在 benchmark 模块中可用
        # AlgorithmConfig.from_coordinator(name, job_strategy, route_strategy, assigner_strategy)
        # 例如：
        # AlgorithmConfig.from_coordinator("Greedy+A*",("greedy","astar","random"))
        # 以下示例展示如何使用该工厂方法：
        # from sky_executor.grid_factory.factory.Benchmark import AlgorithmConfig
        # algorithms.append(AlgorithmConfig.from_coordinator("CompiledCoordinator", job_strategy="greedy", route_strategy="astar", assigner_strategy="random"))
        # 只有当你有 L2D 模型时才启用
        # create_l2d_solver(model_path="path/to/l2d_model.pt"),
        # 只有当你有 MAPF-GPT 模型时才启用
        # create_mapf_gpt_solver(model_path="path/to/mapf_gpt_model"),
        # create_hybrid_solver(),
    ]

    for algo in algorithms:
        print(f"  ✓ {algo.name}: {algo.description}")

    # 2. 创建测试场景
    print("\n[Step 2] Creating benchmark scenarios...")
    scales = [ProblemScale.SMALL, ProblemScale.MEDIUM]
    seeds = [42, 123, 456]
    scenarios = ScenarioBuilder.create_suite(scales, seeds)

    print(f"  ✓ {len(scenarios)} scenarios created")
    for scenario in scenarios[:3]:
        print(
            f"    - {scenario.name}: "
            f"{scenario.grid_size}x{scenario.grid_size}, "
            f"{scenario.num_machines} machines, "
            f"{scenario.num_jobs} jobs, "
            f"{scenario.num_agvs} AGVs"
        )
    if len(scenarios) > 3:
        print(f"    ... and {len(scenarios) - 3} more")

    # 3. 运行 benchmark
    print("\n[Step 3] Running benchmark...")
    print("  (This may take a few minutes...)\n")

    manager = BenchmarkManager(
        env_factory=GridFactoryEnv,
        results_dir="./benchmark_results",
    )

    results_by_algo = manager.run_benchmark(
        algorithms,
        scenarios,
        export_format="both",  # CSV + JSON
    )

    print(f"\n  ✓ Benchmark completed!")
    print(f"  Results saved to: {manager.results_dir}")

    # 4. 分析结果
    print("\n[Step 4] Analyzing results...")
    analyzer = MetricsAnalyzer(manager.all_results)
    analyzer.print_summary()

    # 5. 生成报告
    print("\n[Step 5] Generating reports...")
    reporter = BenchmarkReporter(str(manager.results_dir))

    # 生成 Markdown 报告
    reporter.generate_report(results_by_algo, output_name="benchmark_report")
    print(f"  ✓ Markdown report: {manager.results_dir}/benchmark_report.md")

    # 生成对比图表
    reporter.plot_comparison(results_by_algo, output_name="comparison")
    print(f"  ✓ Comparison plots: {manager.results_dir}/comparison_*.png")

    # 可选：导出 Excel
    try:
        from sky_executor.grid_factory.factory.Benchmark import ExcelExporter

        ExcelExporter.export(
            results_by_algo,
            str(manager.results_dir / "benchmark_results.xlsx"),
        )
        print(f"  ✓ Excel export: {manager.results_dir}/benchmark_results.xlsx")
    except ImportError:
        print("  ⚠ openpyxl not installed, skipping Excel export")

    print("\n" + "=" * 60)
    print("Benchmark completed successfully!")
    print("=" * 60)


# ============================================================================
# 使用说明
# ============================================================================

"""
如何使用这个集成指南：

1. 实现你的算法：
   - 继承现有的 RuleBasedScheduler、L2DJobDispatcher 或 MapfGPTRouter
   - 实现 decide(observation) 方法
   - 确保返回正确的 action 格式

2. 创建 AlgorithmConfig：
   - 使用 create_xxx_solver() 函数包装你的算法
   - 指定 name、description、hyperparams

3. 添加到 algorithms 列表：
   - 在 main() 中添加你的算法配置

4. 运行 benchmark：
   python sky_executor/grid_factory/factory/Benchmark/integration_guide.py

5. 查看结果：
   - 检查 ./benchmark_results/benchmark_report.md
   - 查看 ./benchmark_results/*.csv
   - 查看 ./benchmark_results/comparison_*.png
"""

if __name__ == "__main__":
    main()
