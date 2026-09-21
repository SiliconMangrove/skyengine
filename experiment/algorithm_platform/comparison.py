"""Fair, objective-aware comparison reports for algorithm executions.

The comparison layer consumes an immutable execution plan and its authoritative
run results.  It pairs algorithms only on shared scenario, repetition, and
domain random streams.  Algorithm-private seeds are deliberately excluded from
the pairing key: different algorithms may need independent internal streams,
while the problem instance and exogenous events must remain identical.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import cmp_to_key
from itertools import combinations
from statistics import median
from typing import Mapping, Sequence

from .models import (
    AlgorithmInterface,
    ComparisonMode,
    Direction,
    ExecutionPlan,
    ObjectiveSpec,
    ParameterSet,
    RunResult,
    RunSpec,
    RunStatus,
    _DeepFrozen,
)
from .objectives import ComparisonOutcome, ObjectiveEngine, ObjectiveEvaluation


@dataclass(frozen=True, slots=True)
class EvaluationCell(_DeepFrozen):
    """One common experimental condition used to pair algorithm runs."""

    scenario_id: str
    scenario_digest: str
    repetition: int
    environment_seed: int
    instance_seed: int
    exogenous_seeds: tuple[tuple[str, int], ...] = ()

    @classmethod
    def from_run(cls, run: RunSpec) -> EvaluationCell:
        return cls(
            scenario_id=run.scenario.scenario_id,
            scenario_digest=run.scenario.digest,
            repetition=run.repetition,
            environment_seed=run.seeds.environment,
            instance_seed=run.seeds.instance,
            exogenous_seeds=tuple(sorted(run.seeds.exogenous.items())),
        )


@dataclass(frozen=True, slots=True)
class NumericSummary(_DeepFrozen):
    """Finite descriptive statistics without an algorithm-specific loss."""

    count: int
    total: float | None
    mean: float | None
    sample_standard_deviation: float | None
    standard_error: float | None
    minimum: float | None
    first_quartile: float | None
    median: float | None
    third_quartile: float | None
    maximum: float | None


@dataclass(frozen=True, slots=True)
class ComparisonDesign(_DeepFrozen):
    """Evidence that every trial was evaluated on the same conditions."""

    balanced: bool
    cells: tuple[EvaluationCell, ...]
    trial_cells: Mapping[str, tuple[EvaluationCell, ...]]
    missing_cells: Mapping[str, tuple[EvaluationCell, ...]] = field(
        default_factory=dict
    )
    extra_cells: Mapping[str, tuple[EvaluationCell, ...]] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class AlgorithmComparison(_DeepFrozen):
    """Per-trial aggregate, completeness, and descriptive statistics."""

    trial_id: str
    algorithm_id: str
    algorithm_version: str
    interface: AlgorithmInterface
    parameters: ParameterSet
    expected_run_count: int
    observed_run_count: int
    authoritative_run_count: int
    feasible_run_count: int
    missing_run_ids: tuple[str, ...]
    status_counts: Mapping[str, int]
    metric_summaries: Mapping[str, NumericSummary]
    objective_summaries: Mapping[str, NumericSummary]
    aggregate: ObjectiveEvaluation | None
    rank: int | None = None
    pareto_layer: int | None = None

    @property
    def complete(self) -> bool:
        return (
            self.observed_run_count == self.expected_run_count
            and self.authoritative_run_count == self.expected_run_count
        )


@dataclass(frozen=True, slots=True)
class PairwiseComparison(_DeepFrozen):
    """Paired outcomes for two trials under the experiment objective."""

    left_trial_id: str
    right_trial_id: str
    matched_cell_count: int
    comparable_cell_count: int
    left_better: int
    equivalent: int
    right_better: int
    incomparable: int
    unavailable: int
    objective_differences: Mapping[str, NumericSummary]
    two_sided_sign_test_p_value: float


@dataclass(frozen=True, slots=True)
class ComparisonReport(_DeepFrozen):
    """Portable report for one common algorithm comparison execution."""

    execution_id: str
    experiment_id: str
    plan_digest: str
    objective: ObjectiveSpec
    design: ComparisonDesign
    algorithms: tuple[AlgorithmComparison, ...]
    pairwise: tuple[PairwiseComparison, ...]
    pareto_front_trial_ids: tuple[str, ...]
    generated_at: datetime

    @property
    def complete(self) -> bool:
        return self.design.balanced and all(item.complete for item in self.algorithms)


def summarize(values: Sequence[float]) -> NumericSummary:
    """Return deterministic descriptive statistics for finite values."""

    samples = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in samples):
        raise ValueError("comparison statistics require finite values")
    if not samples:
        return NumericSummary(
            count=0,
            total=None,
            mean=None,
            sample_standard_deviation=None,
            standard_error=None,
            minimum=None,
            first_quartile=None,
            median=None,
            third_quartile=None,
            maximum=None,
        )

    count = len(samples)
    total = sum(samples)
    mean_value = total / count
    standard_deviation: float | None = None
    standard_error: float | None = None
    if count > 1:
        variance = sum((value - mean_value) ** 2 for value in samples) / (count - 1)
        standard_deviation = math.sqrt(variance)
        standard_error = standard_deviation / math.sqrt(count)
    ordered = tuple(sorted(samples))
    return NumericSummary(
        count=count,
        total=total,
        mean=mean_value,
        sample_standard_deviation=standard_deviation,
        standard_error=standard_error,
        minimum=ordered[0],
        first_quartile=_quantile(ordered, 0.25),
        median=float(median(ordered)),
        third_quartile=_quantile(ordered, 0.75),
        maximum=ordered[-1],
    )


def pareto_layers(
    objective: ObjectiveSpec,
    evaluations: Mapping[str, ObjectiveEvaluation],
) -> tuple[tuple[str, ...], ...]:
    """Return non-dominated fronts in stable input order."""

    engine = ObjectiveEngine(objective)
    remaining = list(evaluations)
    layers: list[tuple[str, ...]] = []
    while remaining:
        front = tuple(
            candidate
            for candidate in remaining
            if not any(
                engine.dominates(evaluations[other], evaluations[candidate])
                for other in remaining
                if other != candidate
            )
        )
        layers.append(front)
        front_set = set(front)
        remaining = [candidate for candidate in remaining if candidate not in front_set]
    return tuple(layers)


class ComparisonEngine:
    """Build a fair unified report from a compiled plan and run results."""

    def __init__(self, *, equality_tolerance: float = 0.0) -> None:
        if equality_tolerance < 0.0 or not math.isfinite(equality_tolerance):
            raise ValueError("equality_tolerance must be finite and non-negative")
        self.equality_tolerance = equality_tolerance

    def build(
        self,
        plan: ExecutionPlan,
        run_results: Sequence[RunResult],
        *,
        execution_id: str,
        require_balanced_design: bool = True,
        generated_at: datetime | None = None,
    ) -> ComparisonReport:
        """Build statistics, paired outcomes, ranks, and Pareto membership."""

        result_by_id = self._index_results(plan, run_results)
        runs_by_trial = {
            trial.trial_id: tuple(
                run for run in plan.runs if run.trial_id == trial.trial_id
            )
            for trial in plan.trials
        }
        design = self._design(runs_by_trial)
        if require_balanced_design and not design.balanced:
            raise ValueError(
                "a formal comparison requires identical scenario, repetition, "
                "environment, instance, and exogenous-seed cells for every trial"
            )

        objective_engine = ObjectiveEngine(plan.experiment.objective)
        algorithms = tuple(
            self._algorithm_summary(
                trial.trial_id,
                trial.algorithm.algorithm_id,
                trial.algorithm.version,
                trial.algorithm.interface,
                trial.algorithm.parameters,
                runs_by_trial[trial.trial_id],
                result_by_id,
                objective_engine,
            )
            for trial in plan.trials
        )
        algorithms, fronts = self._rank_algorithms(
            algorithms,
            plan.experiment.objective,
        )
        pairwise = tuple(
            self._pairwise(
                left.trial_id,
                right.trial_id,
                runs_by_trial[left.trial_id],
                runs_by_trial[right.trial_id],
                result_by_id,
                objective_engine,
            )
            for left, right in combinations(plan.trials, 2)
        )
        return ComparisonReport(
            execution_id=execution_id,
            experiment_id=plan.experiment.experiment_id,
            plan_digest=plan.plan_digest,
            objective=plan.experiment.objective,
            design=design,
            algorithms=algorithms,
            pairwise=pairwise,
            pareto_front_trial_ids=(
                fronts[0]
                if plan.experiment.objective.mode is ComparisonMode.PARETO and fronts
                else ()
            ),
            generated_at=generated_at or datetime.now(timezone.utc),
        )

    @staticmethod
    def _index_results(
        plan: ExecutionPlan,
        run_results: Sequence[RunResult],
    ) -> dict[str, RunResult]:
        expected_ids = {run.run_id for run in plan.runs}
        indexed: dict[str, RunResult] = {}
        for result in run_results:
            if result.run_id not in expected_ids:
                raise ValueError(f"run result is not part of the plan: {result.run_id}")
            if result.run_id in indexed:
                raise ValueError(f"duplicate run result: {result.run_id}")
            indexed[result.run_id] = result
        return indexed

    @staticmethod
    def _design(
        runs_by_trial: Mapping[str, Sequence[RunSpec]],
    ) -> ComparisonDesign:
        trial_cells = {
            trial_id: tuple(EvaluationCell.from_run(run) for run in runs)
            for trial_id, runs in runs_by_trial.items()
        }
        for trial_id, cells in trial_cells.items():
            if len(cells) != len(set(cells)):
                raise ValueError(f"trial contains duplicate evaluation cells: {trial_id}")
        reference = next(iter(trial_cells.values()), ())
        reference_set = set(reference)
        balanced = all(set(cells) == reference_set for cells in trial_cells.values())
        missing = {
            trial_id: tuple(cell for cell in reference if cell not in set(cells))
            for trial_id, cells in trial_cells.items()
        }
        extra = {
            trial_id: tuple(cell for cell in cells if cell not in reference_set)
            for trial_id, cells in trial_cells.items()
        }
        return ComparisonDesign(
            balanced=balanced,
            cells=tuple(reference),
            trial_cells=trial_cells,
            missing_cells={key: value for key, value in missing.items() if value},
            extra_cells={key: value for key, value in extra.items() if value},
        )

    @staticmethod
    def _authoritative(result: RunResult | None) -> bool:
        return (
            result is not None
            and result.status in {RunStatus.SUCCEEDED, RunStatus.INFEASIBLE}
            and bool(result.metrics)
            and bool(result.objective_values)
        )

    def _algorithm_summary(
        self,
        trial_id: str,
        algorithm_id: str,
        algorithm_version: str,
        interface: AlgorithmInterface,
        parameters: ParameterSet,
        expected_runs: Sequence[RunSpec],
        result_by_id: Mapping[str, RunResult],
        objective_engine: ObjectiveEngine,
    ) -> AlgorithmComparison:
        results = tuple(
            result_by_id[run.run_id]
            for run in expected_runs
            if run.run_id in result_by_id
        )
        authoritative = tuple(result for result in results if self._authoritative(result))
        metric_names = tuple(
            sorted({name for result in authoritative for name in result.metrics})
        )
        metric_summaries = {
            name: summarize(
                [result.metrics[name] for result in authoritative if name in result.metrics]
            )
            for name in metric_names
        }

        per_run_evaluations = tuple(
            objective_engine.evaluate_metrics([result.metrics])
            for result in authoritative
        )
        objective_summaries = {
            component.name: summarize(
                [
                    evaluation.objective_values[index]
                    for evaluation in per_run_evaluations
                ]
            )
            for index, component in enumerate(objective_engine.spec.components)
        }
        aggregate = None
        if len(authoritative) == len(expected_runs):
            aggregate = objective_engine.evaluate_runs(authoritative)

        status_counts = {
            status.value: sum(result.status is status for result in results)
            for status in RunStatus
            if any(result.status is status for result in results)
        }
        missing_run_ids = tuple(
            run.run_id for run in expected_runs if run.run_id not in result_by_id
        )
        return AlgorithmComparison(
            trial_id=trial_id,
            algorithm_id=algorithm_id,
            algorithm_version=algorithm_version,
            interface=interface,
            parameters=parameters,
            expected_run_count=len(expected_runs),
            observed_run_count=len(results),
            authoritative_run_count=len(authoritative),
            feasible_run_count=sum(result.feasible for result in authoritative),
            missing_run_ids=missing_run_ids,
            status_counts=status_counts,
            metric_summaries=metric_summaries,
            objective_summaries=objective_summaries,
            aggregate=aggregate,
        )

    def _rank_algorithms(
        self,
        algorithms: tuple[AlgorithmComparison, ...],
        objective: ObjectiveSpec,
    ) -> tuple[tuple[AlgorithmComparison, ...], tuple[tuple[str, ...], ...]]:
        evaluations = {
            item.trial_id: item.aggregate
            for item in algorithms
            if item.aggregate is not None
        }
        typed_evaluations = {
            key: value for key, value in evaluations.items() if value is not None
        }
        if not typed_evaluations:
            return algorithms, ()

        engine = ObjectiveEngine(objective)
        if objective.mode is ComparisonMode.PARETO:
            fronts = pareto_layers(objective, typed_evaluations)
            layer_by_id = {
                trial_id: index + 1
                for index, front in enumerate(fronts)
                for trial_id in front
            }
            return (
                tuple(
                    replace(
                        item,
                        rank=layer_by_id.get(item.trial_id),
                        pareto_layer=layer_by_id.get(item.trial_id),
                    )
                    for item in algorithms
                ),
                fronts,
            )

        def compare(left_id: str, right_id: str) -> int:
            outcome = engine.compare(
                typed_evaluations[left_id],
                typed_evaluations[right_id],
            )
            if outcome is ComparisonOutcome.BETTER:
                return -1
            if outcome is ComparisonOutcome.WORSE:
                return 1
            return 0

        ordered_ids = sorted(typed_evaluations, key=cmp_to_key(compare))
        rank_by_id: dict[str, int] = {}
        rank = 0
        previous_id: str | None = None
        for trial_id in ordered_ids:
            if previous_id is None or compare(previous_id, trial_id) != 0:
                rank += 1
            rank_by_id[trial_id] = rank
            previous_id = trial_id
        return (
            tuple(
                replace(item, rank=rank_by_id.get(item.trial_id))
                for item in algorithms
            ),
            (tuple(ordered_ids[:1]),),
        )

    def _pairwise(
        self,
        left_trial_id: str,
        right_trial_id: str,
        left_runs: Sequence[RunSpec],
        right_runs: Sequence[RunSpec],
        result_by_id: Mapping[str, RunResult],
        objective_engine: ObjectiveEngine,
    ) -> PairwiseComparison:
        left_by_cell = {EvaluationCell.from_run(run): run for run in left_runs}
        right_by_cell = {EvaluationCell.from_run(run): run for run in right_runs}
        cells = tuple(cell for cell in left_by_cell if cell in right_by_cell)
        outcomes: list[ComparisonOutcome] = []
        differences: list[list[float]] = [
            [] for _ in objective_engine.spec.components
        ]
        unavailable = 0

        for cell in cells:
            left_result = result_by_id.get(left_by_cell[cell].run_id)
            right_result = result_by_id.get(right_by_cell[cell].run_id)
            if not self._authoritative(left_result) or not self._authoritative(right_result):
                unavailable += 1
                continue
            if left_result is None or right_result is None:
                raise RuntimeError("authoritative result unexpectedly missing")
            left_evaluation = objective_engine.evaluate_metrics([left_result.metrics])
            right_evaluation = objective_engine.evaluate_metrics([right_result.metrics])
            outcome = objective_engine.compare(left_evaluation, right_evaluation)
            if self.equality_tolerance > 0.0 and (
                left_evaluation.feasible == right_evaluation.feasible
                and left_evaluation.constraint_violations.keys()
                == right_evaluation.constraint_violations.keys()
                and all(
                    abs(left - right) <= self.equality_tolerance
                    for left, right in zip(
                        left_evaluation.objective_values,
                        right_evaluation.objective_values,
                        strict=True,
                    )
                )
                and all(
                    abs(
                        left_evaluation.constraint_violations[name]
                        - right_evaluation.constraint_violations[name]
                    )
                    <= self.equality_tolerance
                    for name in left_evaluation.constraint_violations
                )
            ):
                outcome = ComparisonOutcome.EQUIVALENT
            outcomes.append(outcome)
            for index, component in enumerate(objective_engine.spec.components):
                raw_difference = (
                    left_evaluation.objective_values[index]
                    - right_evaluation.objective_values[index]
                )
                differences[index].append(
                    raw_difference
                    if component.direction is Direction.MINIMIZE
                    else -raw_difference
                )

        left_better = outcomes.count(ComparisonOutcome.BETTER)
        right_better = outcomes.count(ComparisonOutcome.WORSE)
        return PairwiseComparison(
            left_trial_id=left_trial_id,
            right_trial_id=right_trial_id,
            matched_cell_count=len(cells),
            comparable_cell_count=len(outcomes),
            left_better=left_better,
            equivalent=outcomes.count(ComparisonOutcome.EQUIVALENT),
            right_better=right_better,
            incomparable=outcomes.count(ComparisonOutcome.INCOMPARABLE),
            unavailable=unavailable,
            objective_differences={
                component.name: summarize(differences[index])
                for index, component in enumerate(objective_engine.spec.components)
            },
            two_sided_sign_test_p_value=_two_sided_sign_test(
                left_better,
                right_better,
            ),
        )


def build_comparison_report(
    plan: ExecutionPlan,
    run_results: Sequence[RunResult],
    *,
    execution_id: str,
    require_balanced_design: bool = True,
) -> ComparisonReport:
    """Convenience entry point for the default comparison policy."""

    return ComparisonEngine().build(
        plan,
        run_results,
        execution_id=execution_id,
        require_balanced_design=require_balanced_design,
    )


def _quantile(ordered: Sequence[float], probability: float) -> float:
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _two_sided_sign_test(left_better: int, right_better: int) -> float:
    decisive = left_better + right_better
    if decisive == 0:
        return 1.0
    tail = min(left_better, right_better)
    probability = 2.0 * sum(
        math.comb(decisive, value) for value in range(tail + 1)
    ) / (2**decisive)
    return min(1.0, probability)
