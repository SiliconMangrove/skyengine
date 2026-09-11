"""
Algorithm Evaluation Benchmark Framework for Joint Job Shop + AGV Routing Task

This module provides a comprehensive platform for evaluating different scheduling and routing algorithms
on the combined Job Shop Scheduling + Multi-Agent Path Finding (JSS + MAPF) problem.

Core Components:
1. BenchmarkScenario: Define problem instances (grid size, machines, jobs, AGVs)
2. Algorithm & Solver: Pluggable scheduling/routing solvers (Coordinator-based)
3. EvaluationRunner: Execute a single algorithm on a scenario and collect metrics
4. BenchmarkManager: Orchestrate multiple runs across different algorithms/scenarios
5. MetricsAnalyzer: Compute KPIs and generate comparison reports
"""

from __future__ import annotations

import json
import csv
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from enum import Enum
import numpy as np
from datetime import datetime

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='[%(name)s] %(levelname)s: %(message)s')


# ============================================================================
# 1. DATA STRUCTURES & CONFIGURATION
# ============================================================================

class ProblemScale(Enum):
    """Predefined problem scales for quick scenario setup."""
    TINY = ("tiny", 2, 2, 2, 4)       # (name, machines, jobs, ops_per_job, agvs)
    SMALL = ("small", 4, 3, 3, 6)
    MEDIUM = ("medium", 8, 6, 3, 8)
    LARGE = ("large", 12, 10, 4, 12)

    def __init__(self, name: str, machines: int, jobs: int, ops_per_job: int, agvs: int):
        self.scale_name = name
        self.num_machines = machines
        self.num_jobs = jobs
        self.ops_per_job = ops_per_job
        self.num_agvs = agvs


@dataclass
class BenchmarkScenario:
    """Define a problem instance for evaluation."""
    name: str
    grid_size: int = 16
    num_machines: int = 8
    num_jobs: int = 6
    min_ops_per_job: int = 2
    max_ops_per_job: int = 4
    min_proc_time: int = 2
    max_proc_time: int = 7
    num_agvs: int = 4
    machine_density: float = 0.3
    seed: int = 42
    max_episode_steps: int = 500

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AlgorithmConfig:
    """Configuration for an algorithm/solver."""
    name: str
    solver_factory: Optional[Callable[[], Any]] = None  # returns a Coordinator-like object
    description: str = ""
    hyperparams: Dict[str, Any] = field(default_factory=dict)
    # Optional coordinator-level component strategy config. If provided, this will
    # be used to construct a Coordinator instance instead of calling solver_factory().
    coordinator_config: Optional["CoordinatorConfig"] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "hyperparams": self.hyperparams,
        }

    @classmethod
    def from_coordinator(cls, name: str, job_strategy: str = "greedy", route_strategy: str = "astar", assigner_strategy: str = "random", description: str = "", hyperparams: Optional[Dict[str, Any]] = None, component_params: Optional[Dict[str, Any]] = None):
        """Convenience constructor to create AlgorithmConfig from coordinator component strategy names."""
        if hyperparams is None:
            hyperparams = {}
        coord_cfg = CoordinatorConfig(
            job_strategy=job_strategy,
            route_strategy=route_strategy,
            assigner_strategy=assigner_strategy,
            component_params=component_params or {},
        )
        return cls(name=name, solver_factory=None, description=description, hyperparams=hyperparams, coordinator_config=coord_cfg)


@dataclass
class CoordinatorConfig:
    """Configurable coordinator composed of component strategy names.

    Example:
        CoordinatorConfig(job_strategy="greedy", route_strategy="astar", assigner_strategy="random")

    The `build()` method will import factories from the component package and construct
    a Coordinator instance wired with the chosen strategies.
    """
    job_strategy: str = "greedy"
    route_strategy: str = "astar"
    assigner_strategy: str = "random"
    component_params: Dict[str, Any] = field(default_factory=dict)

    def build(self):
        """Construct a Coordinator instance according to this config."""
        # Import here to avoid circular imports at module load time
        from sky_executor.grid_factory.factory.Component.JobSolver.job_solver_factory import JobSolverFactory
        from sky_executor.grid_factory.factory.Component.RouteSolver.route_solver_factory import RouteSolverFactory
        from sky_executor.grid_factory.factory.Component.Assigner.assigner_factory import AssignerFactory
        from sky_executor.grid_factory.factory.Component.Coordinator.coordinator import Coordinator

        job_solver = JobSolverFactory.create(self.job_strategy)
        route_solver = RouteSolverFactory.create(self.route_strategy)
        assigner = AssignerFactory.create(self.assigner_strategy)

        # Allow passing additional params if needed (not used by default factories)
        if self.component_params:
            # attempt to set attributes on created components
            for k, v in self.component_params.items():
                if hasattr(job_solver, k):
                    setattr(job_solver, k, v)
                if hasattr(route_solver, k):
                    setattr(route_solver, k, v)
                if hasattr(assigner, k):
                    setattr(assigner, k, v)

        return Coordinator(job_solver=job_solver, route_solver=route_solver, assigner=assigner)


@dataclass
class EvaluationMetrics:
    """Metrics for a single run."""
    algorithm_name: str
    scenario_name: str
    total_makespan: float = 0.0
    total_flowtime: float = 0.0
    avg_job_flowtime: float = 0.0
    total_ops_completed: int = 0
    total_ops: int = 0
    agv_utilization: float = 0.0
    agv_avg_distance: float = 0.0
    agv_avg_idle_time: float = 0.0
    machine_utilization: float = 0.0
    solution_time: float = 0.0
    episode_length: int = 0
    success: bool = True
    error_msg: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================================
# 2. SCENARIO BUILDER
# ============================================================================
class ScenarioBuilder:
    """Helper to create benchmark scenarios."""

    @staticmethod
    def from_scale(scale: ProblemScale, grid_size: int = 16, seed: int = 42) -> BenchmarkScenario:
        """Create scenario from predefined scale."""
        return BenchmarkScenario(
            name=f"{scale.scale_name}_seed{seed}",
            grid_size=grid_size,
            num_machines=scale.num_machines,
            num_jobs=scale.num_jobs,
            min_ops_per_job=scale.ops_per_job,
            max_ops_per_job=scale.ops_per_job + 1,
            num_agvs=scale.num_agvs,
            seed=seed,
        )

    @staticmethod
    def from_dict(cfg: Dict[str, Any]) -> BenchmarkScenario:
        """Create scenario from config dict."""
        return BenchmarkScenario(**cfg)

    @staticmethod
    def create_suite(scales: List[ProblemScale], seeds: List[int]) -> List[BenchmarkScenario]:
        """Create a suite of scenarios for multi-scale testing."""
        scenarios = []
        for scale in scales:
            for seed in seeds:
                scenarios.append(ScenarioBuilder.from_scale(scale, seed=seed))
        return scenarios


# ============================================================================
# 3. EVALUATION RUNNER (Single Run)
# ============================================================================
class EvaluationRunner:
    """Run a single algorithm on a scenario and collect metrics."""
    def __init__(
        self,
        env_factory: Callable[[], Any],  # returns GridFactoryEnv
        metrics_wrapper_factory: Optional[Callable[[], Any]] = None,
    ):
        """
        Args:
            env_factory: callable returning a fresh GridFactoryEnv
            metrics_wrapper_factory: optional factory for metric wrappers
        """
        self.env_factory = env_factory
        self.metrics_wrapper_factory = metrics_wrapper_factory

    def run(
        self,
        scenario: BenchmarkScenario,
        algorithm_config: AlgorithmConfig,
        verbose: bool = False,
    ) -> EvaluationMetrics:
        """
        Execute algorithm on scenario and return metrics.

        Returns:
            EvaluationMetrics containing KPIs
        """
        metrics = EvaluationMetrics(
            algorithm_name=algorithm_config.name,
            scenario_name=scenario.name,
        )

        try:
            # Create environment with scenario config
            env = self._setup_env(scenario)
            # Create solver/coordinator
            if algorithm_config.coordinator_config is not None:
                try:
                    solver = algorithm_config.coordinator_config.build()
                except Exception as e:
                    logger.error(f"Failed to build Coordinator from config for {algorithm_config.name}: {e}")
                    raise
            elif algorithm_config.solver_factory is not None:
                solver = algorithm_config.solver_factory()
            else:
                raise ValueError("AlgorithmConfig must provide either solver_factory or coordinator_config")
            # Reset environment
            obs, info = env.reset()
            if verbose:
                logger.info(
                    f"[{algorithm_config.name}] Running on {scenario.name} "
                    f"(machines={scenario.num_machines}, jobs={scenario.num_jobs}, agvs={scenario.num_agvs})"
                )
            # Simulation loop
            step = 0
            start_time = datetime.now()

            for step in range(scenario.max_episode_steps):
                # Get actions from solver
                actions = solver.decide(obs)

                # Step environment
                obs, rewards, terminations, truncations, infos = env.step(actions)

                # Check termination
                if all(terminations.values()) or all(truncations.values()):
                    break

            end_time = datetime.now()
            metrics.solution_time = (end_time - start_time).total_seconds()
            metrics.episode_length = step + 1

            # Extract metrics from environment
            metrics = self._extract_metrics(env, metrics, scenario)
            metrics.success = True

            if verbose:
                logger.info(
                    f"✓ Completed: makespan={metrics.total_makespan:.1f}, "
                    f"flowtime={metrics.total_flowtime:.1f}, "
                    f"time={metrics.solution_time:.2f}s"
                )

        except Exception as e:
            logger.error(f"✗ Error running {algorithm_config.name} on {scenario.name}: {e}")
            metrics.success = False
            metrics.error_msg = str(e)

        return metrics

    def _setup_env(self, scenario: BenchmarkScenario) -> Any:
        """Create and configure environment with scenario settings."""
        from sky_executor.grid_factory.factory.grid_factory_env import GridFactoryEnv
        from sky_executor.grid_factory.factory.Utils.structure import MachineConfig, JobConfig
        from pogema import GridConfig

        env = GridFactoryEnv(
            grid_config=GridConfig(
                num_agents=scenario.num_agvs,
                size=scenario.grid_size,
                density=0.1,
                seed=scenario.seed,
                max_episode_steps=scenario.max_episode_steps,
            ),
            machine_config=MachineConfig(
                num_machines=scenario.num_machines,
                strategy="random",
                seed=scenario.seed,
            ),
            job_config=JobConfig(
                num_jobs=scenario.num_jobs,
                min_ops_per_job=scenario.min_ops_per_job,
                max_ops_per_job=scenario.max_ops_per_job,
                min_proc_time=scenario.min_proc_time,
                max_proc_time=scenario.max_proc_time,
                total_machines=scenario.num_machines,
                seed=scenario.seed,
            ),
        )

        # Add metric wrappers if available
        if self.metrics_wrapper_factory:
            try:
                wrappers = self.metrics_wrapper_factory()
                env.add_metrics_wrapper(wrappers)
            except Exception as e:
                logger.warning(f"Failed to add metric wrappers: {e}")

        return env

    def _extract_metrics(
        self,
        env: Any,
        metrics: EvaluationMetrics,
        scenario: BenchmarkScenario,
    ) -> EvaluationMetrics:
        """Extract performance metrics from environment state."""

        pogema_env = env.pogema_env

        # === Job Shop Metrics ===
        total_makespan = 0.0
        total_flowtime = 0.0
        completed_ops = 0
        total_ops = 0

        if hasattr(pogema_env, "jobs") and pogema_env.jobs:
            for job in pogema_env.jobs:
                total_ops += len(job.ops)
                if hasattr(job, "completion_time") and job.completion_time > 0:
                    job_span = job.completion_time
                    total_makespan = max(total_makespan, job_span)

                    # Flowtime = completion_time - start_time (approximate as arrival time 0)
                    total_flowtime += job_span

                    completed_ops += len(job.ops)

        metrics.total_makespan = total_makespan
        metrics.total_flowtime = total_flowtime
        metrics.avg_job_flowtime = total_flowtime / max(scenario.num_jobs, 1)
        metrics.total_ops_completed = completed_ops
        metrics.total_ops = total_ops

        # === AGV Metrics ===
        if hasattr(pogema_env, "agv_stats") and pogema_env.agv_stats:
            total_distance = sum(stats["dist"] for stats in pogema_env.agv_stats.values())
            total_loaded = sum(stats["loaded"] for stats in pogema_env.agv_stats.values())
            total_idle = sum(stats["idle"] for stats in pogema_env.agv_stats.values())

            total_time = total_loaded + total_idle + sum(
                stats.get("empty", 0) for stats in pogema_env.agv_stats.values()
            )

            metrics.agv_avg_distance = total_distance / max(scenario.num_agvs, 1)
            metrics.agv_utilization = total_loaded / max(total_time, 1) if total_time > 0 else 0.0
            metrics.agv_avg_idle_time = total_idle / max(scenario.num_agvs, 1)

        # === Machine Metrics ===
        if hasattr(pogema_env, "machines") and pogema_env.machines:
            total_work_time = sum(
                getattr(m, "total_work_time", 0) for m in pogema_env.machines
            )
            theoretical_max = scenario.max_episode_steps * len(pogema_env.machines)
            metrics.machine_utilization = total_work_time / max(theoretical_max, 1) if theoretical_max > 0 else 0.0

        return metrics


# ============================================================================
# 4. BENCHMARK MANAGER (Multi-Run Orchestration)
# ============================================================================
class BenchmarkManager:
    """Orchestrate multiple runs across algorithms and scenarios."""

    def __init__(
        self,
        env_factory: Callable[[], Any],
        results_dir: str = "./benchmark_results",
    ):
        """
        Args:
            env_factory: callable returning GridFactoryEnv
            results_dir: directory to save results
        """
        self.env_factory = env_factory
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.runner = EvaluationRunner(env_factory)
        self.all_results: List[EvaluationMetrics] = []

    def run_benchmark(
        self,
        algorithms: List[AlgorithmConfig],
        scenarios: List[BenchmarkScenario],
        export_format: str = "csv",
    ) -> Dict[str, List[EvaluationMetrics]]:
        """
        Run benchmark across all algorithm × scenario combinations.

        Args:
            algorithms: list of algorithm configs
            scenarios: list of scenarios
            export_format: 'csv', 'json', or 'both'

        Returns:
            dict mapping algorithm name to list of metrics
        """
        results_by_algo: Dict[str, List[EvaluationMetrics]] = {}

        total_runs = len(algorithms) * len(scenarios)
        run_count = 0

        for algo in algorithms:
            algo_results = []
            logger.info(f"{'='*70}")
            logger.info(f"Evaluating: {algo.name}")
            logger.info(f"{'='*70}")

            for scenario in scenarios:
                run_count += 1
                logger.info(f"\n[{run_count}/{total_runs}] {scenario.name}...")

                metrics = self.runner.run(scenario, algo, verbose=True)
                algo_results.append(metrics)
                self.all_results.append(metrics)

            results_by_algo[algo.name] = algo_results

        # Export results
        self._export_results(results_by_algo, export_format)

        return results_by_algo

    def _export_results(
        self,
        results_by_algo: Dict[str, List[EvaluationMetrics]],
        format: str = "csv",
    ) -> None:
        """Export results to files."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if format in ("csv", "both"):
            self._export_csv(results_by_algo, timestamp)

        if format in ("json", "both"):
            self._export_json(results_by_algo, timestamp)

    def _export_csv(self, results_by_algo: Dict[str, List[EvaluationMetrics]], timestamp: str) -> None:
        """Export as CSV."""
        csv_path = self.results_dir / f"benchmark_{timestamp}.csv"

        all_rows = []
        for algo_name, metrics_list in results_by_algo.items():
            for metrics in metrics_list:
                row = metrics.to_dict()
                all_rows.append(row)

        if all_rows:
            keys = all_rows[0].keys()
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(all_rows)

            logger.info(f"✓ Exported CSV: {csv_path}")

    def _export_json(self, results_by_algo: Dict[str, List[EvaluationMetrics]], timestamp: str) -> None:
        """Export as JSON."""
        json_path = self.results_dir / f"benchmark_{timestamp}.json"

        export_data = {
            "timestamp": timestamp,
            "results": {
                algo_name: [m.to_dict() for m in metrics_list]
                for algo_name, metrics_list in results_by_algo.items()
            },
        }

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)

        logger.info(f"✓ Exported JSON: {json_path}")


# ============================================================================
# 5. METRICS ANALYZER (Results Analysis)
# ============================================================================
class MetricsAnalyzer:
    """Analyze and compare results across algorithms."""

    def __init__(self, all_results: List[EvaluationMetrics]):
        """Initialize with collected results."""
        self.all_results = all_results

    def summary_by_algorithm(self) -> Dict[str, Dict[str, float]]:
        """Compute summary statistics per algorithm."""
        results_by_algo: Dict[str, List[EvaluationMetrics]] = {}
        for m in self.all_results:
            if m.algorithm_name not in results_by_algo:
                results_by_algo[m.algorithm_name] = []
            results_by_algo[m.algorithm_name].append(m)

        summary = {}
        for algo_name, metrics_list in results_by_algo.items():
            successful = [m for m in metrics_list if m.success]
            if not successful:
                summary[algo_name] = {"status": "all_failed"}
                continue

            makespans = [m.total_makespan for m in successful]
            flowtimes = [m.total_flowtime for m in successful]
            utilizations = [m.agv_utilization for m in successful]

            summary[algo_name] = {
                "count": len(successful),
                "avg_makespan": np.mean(makespans),
                "min_makespan": np.min(makespans),
                "max_makespan": np.max(makespans),
                "avg_flowtime": np.mean(flowtimes),
                "avg_agv_util": np.mean(utilizations),
                "success_rate": len(successful) / len(metrics_list),
            }

        return summary

    def comparison_table(self) -> str:
        """Generate a human-readable comparison table."""
        summary = self.summary_by_algorithm()

        lines = [
            "\n" + "="*100,
            "BENCHMARK SUMMARY",
            "="*100,
            "",
            f"{'Algorithm':<30} {'Avg Makespan':<15} {'Avg Flowtime':<15} {'Avg AGV Util':<15} {'Success Rate':<15}",
            "-"*100,
        ]

        for algo_name in sorted(summary.keys()):
            stats = summary[algo_name]
            if "status" in stats:
                lines.append(f"{algo_name:<30} {'FAILED':<15}")
            else:
                lines.append(
                    f"{algo_name:<30} "
                    f"{stats['avg_makespan']:<15.1f} "
                    f"{stats['avg_flowtime']:<15.1f} "
                    f"{stats['avg_agv_util']:<15.3f} "
                    f"{stats['success_rate']:<15.1%}"
                )

        lines.append("="*100)
        return "\n".join(lines)

    def print_summary(self) -> None:
        """Print summary to stdout."""
        print(self.comparison_table())


# ============================================================================
# HELPER: Create Quick Benchmark
# ============================================================================
def quick_benchmark(
    algorithms: List[AlgorithmConfig],
    scales: List[ProblemScale] = None,
    seeds: List[int] = None,
    results_dir: str = "./benchmark_results",
) -> BenchmarkManager:
    """
    Quick setup for benchmark.

    Args:
        algorithms: list of AlgorithmConfig
        scales: list of ProblemScale (default: [TINY, SMALL])
        seeds: list of seeds (default: [42])
        results_dir: where to save results

    Returns:
        BenchmarkManager with results already collected
    """
    from sky_executor.grid_factory.factory.grid_factory_env import GridFactoryEnv

    if scales is None:
        scales = [ProblemScale.TINY, ProblemScale.SMALL]
    if seeds is None:
        seeds = [42]

    scenarios = ScenarioBuilder.create_suite(scales, seeds)
    manager = BenchmarkManager(env_factory=GridFactoryEnv, results_dir=results_dir)
    manager.run_benchmark(algorithms, scenarios, export_format="both")

    return manager
