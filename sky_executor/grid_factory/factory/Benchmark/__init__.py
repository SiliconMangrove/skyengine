"""Benchmark module for algorithm evaluation."""

try:
    from .benchmark import (
        BenchmarkScenario,
        AlgorithmConfig,
        CoordinatorConfig,
        EvaluationMetrics,
        ProblemScale,
        ScenarioBuilder,
        EvaluationRunner,
        BenchmarkManager,
        MetricsAnalyzer,
        quick_benchmark,
    )
except ImportError as e:
    print(f"[Warning] Failed to import benchmark module: {e}")

try:
    from .reporter import BenchmarkReporter, ExcelExporter
except ImportError as e:
    print(f"[Warning] Failed to import reporter module: {e}")

try:
    from .problem_generator import (
        JobConfig,
        MachineConfig,
        OperationConfig,
        ProblemInstance,
        ProblemGenerator,
        ProblemTemplates,
        create_problem,
        create_problems_for_comparison,
    )
except ImportError as e:
    print(f"[Warning] problem_generator module not found: {e}")

# 导入新的benchmark_generator模块（如果可用）
try:
    from .benchmark_generator import (
        BenchmarkGenerator,
        BenchmarkProblem,
        StandardBenchmarkSuite,
        create_benchmark_problem,
    )
except ImportError as e:
    print(f"[Warning] Failed to import benchmark_generator: {e}")

__all__ = [
    # Benchmark framework
    "BenchmarkScenario",
    "AlgorithmConfig",
    "CoordinatorConfig",
    "EvaluationMetrics",
    "ProblemScale",
    "ScenarioBuilder",
    "EvaluationRunner",
    "BenchmarkManager",
    "MetricsAnalyzer",
    "quick_benchmark",
    "BenchmarkReporter",
    "ExcelExporter",
    # Problem generation (old)
    "JobConfig",
    "MachineConfig",
    "OperationConfig",
    "ProblemInstance",
    "ProblemGenerator",
    "ProblemTemplates",
    "create_problem",
    "create_problems_for_comparison",
    # Problem generation (new)
    "BenchmarkGenerator",
    "BenchmarkProblem",
    "StandardBenchmarkSuite",
    "create_benchmark_problem",
]
