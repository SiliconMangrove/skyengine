"""Objective evaluation and comparison across reproducible runs.

The objective engine owns only experiment semantics.  It never reads a
training loss or algorithm-specific reward: every value is reduced from the
authoritative metrics emitted by completed domain runs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from statistics import median
from typing import Iterable, Mapping, Sequence

from .models import (
    Aggregation,
    ComparisonMode,
    ConstraintOperator,
    ConstraintSpec,
    Direction,
    MetricSet,
    ObjectiveSpec,
    ParameterSet,
    RunResult,
    RunStatus,
    TrialResult,
    _DeepFrozen,
)


class ComparisonOutcome(StrEnum):
    """Relationship between two objective evaluations."""

    BETTER = "better"
    WORSE = "worse"
    EQUIVALENT = "equivalent"
    INCOMPARABLE = "incomparable"


@dataclass(frozen=True, slots=True)
class ObjectiveEvaluation(_DeepFrozen):
    """Reduced objective and constraint values for one or more runs."""

    objective_values: tuple[float, ...]
    constraint_values: Mapping[str, float]
    constraint_violations: Mapping[str, float]
    feasible: bool

    @property
    def violated_constraint_count(self) -> int:
        return sum(value > 0.0 for value in self.constraint_violations.values())

    @property
    def total_constraint_violation(self) -> float:
        return sum(self.constraint_violations.values())


def aggregate(values: Sequence[float], mode: Aggregation) -> float:
    """Reduce observations in their run order with an explicit aggregation."""

    if not values:
        raise ValueError("cannot aggregate an empty metric sequence")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("objective metrics must be finite")

    if mode is Aggregation.MEAN:
        return sum(values) / len(values)
    if mode is Aggregation.MEDIAN:
        return float(median(values))
    if mode is Aggregation.MIN:
        return min(values)
    if mode is Aggregation.MAX:
        return max(values)
    if mode is Aggregation.SUM:
        return sum(values)
    if mode is Aggregation.LAST:
        return values[-1]
    raise ValueError(f"unsupported aggregation: {mode}")


class ObjectiveEngine:
    """Evaluate, compare, and Pareto-rank results under one objective spec."""

    def __init__(self, spec: ObjectiveSpec) -> None:
        self.spec = spec
        self._validate_spec()

    def evaluate_metrics(self, metric_sets: Sequence[MetricSet]) -> ObjectiveEvaluation:
        """Evaluate metrics from one run or an ordered group of runs."""

        if not metric_sets:
            raise ValueError("objective evaluation requires at least one run")

        objective_values = tuple(
            aggregate(
                [self._metric(metrics, component.metric) for metrics in metric_sets],
                component.aggregation,
            )
            for component in self.spec.components
        )

        constraint_values: dict[str, float] = {}
        constraint_violations: dict[str, float] = {}
        feasible = True
        for constraint in self.spec.constraints:
            name = self._constraint_name(constraint)
            value = aggregate(
                [self._metric(metrics, constraint.metric) for metrics in metric_sets],
                constraint.aggregation,
            )
            satisfied, violation = self._evaluate_constraint(constraint, value)
            constraint_values[name] = value
            constraint_violations[name] = violation
            feasible = feasible and satisfied

        return ObjectiveEvaluation(
            objective_values=objective_values,
            constraint_values=constraint_values,
            constraint_violations=constraint_violations,
            feasible=feasible,
        )

    def evaluate_runs(self, results: Sequence[RunResult]) -> ObjectiveEvaluation:
        """Aggregate authoritative metrics across runs in the supplied order."""

        unavailable = tuple(
            result.run_id
            for result in results
            if result.status not in {RunStatus.SUCCEEDED, RunStatus.INFEASIBLE}
            or not result.metrics
        )
        if unavailable:
            raise ValueError(
                "objective aggregation requires terminal runs with authoritative "
                f"metrics; unavailable runs: {unavailable}"
            )
        return self.evaluate_metrics([result.metrics for result in results])

    def evaluate_run_result(self, result: RunResult) -> RunResult:
        """Return a run result annotated with objective and constraint values."""

        evaluation = self.evaluate_metrics([result.metrics])
        return replace(
            result,
            objective_values=evaluation.objective_values,
            constraint_values=evaluation.constraint_values,
            constraint_violations=evaluation.constraint_violations,
            feasible=result.feasible and evaluation.feasible,
        )

    def aggregate_trial(
        self,
        trial_id: str,
        parameters: ParameterSet,
        results: Sequence[RunResult],
    ) -> TrialResult:
        """Build the optimizer-facing result for a fixed parameter candidate."""

        if not results:
            raise ValueError("trial aggregation requires at least one run")
        evaluation = self.evaluate_runs(results)
        named_values = {
            component.name: value
            for component, value in zip(
                self.spec.components, evaluation.objective_values, strict=True
            )
        }
        return TrialResult(
            trial_id=trial_id,
            candidate_id=None,
            parameters=parameters,
            objective_values=evaluation.objective_values,
            constraint_values=evaluation.constraint_values,
            constraint_violations=evaluation.constraint_violations,
            feasible=evaluation.feasible and all(result.feasible for result in results),
            run_results=tuple(results),
            metrics=named_values,
        )

    def compare(
        self,
        left: ObjectiveEvaluation,
        right: ObjectiveEvaluation,
    ) -> ComparisonOutcome:
        """Compare two evaluations using feasibility and configured semantics."""

        self._validate_evaluation(left)
        self._validate_evaluation(right)

        feasibility = self._compare_feasibility(left, right)
        if feasibility is not ComparisonOutcome.EQUIVALENT:
            return feasibility

        if self.spec.mode is ComparisonMode.PARETO:
            left_dominates = self.dominates(left, right)
            right_dominates = self.dominates(right, left)
            if left_dominates:
                return ComparisonOutcome.BETTER
            if right_dominates:
                return ComparisonOutcome.WORSE
            if left.objective_values == right.objective_values:
                return ComparisonOutcome.EQUIVALENT
            return ComparisonOutcome.INCOMPARABLE

        left_key = self._objective_key(left.objective_values)
        right_key = self._objective_key(right.objective_values)
        if left_key < right_key:
            return ComparisonOutcome.BETTER
        if left_key > right_key:
            return ComparisonOutcome.WORSE
        return ComparisonOutcome.EQUIVALENT

    def evaluation_from_trial(
        self,
        result: TrialResult,
    ) -> ObjectiveEvaluation:
        """Rehydrate a typed objective evaluation from an aggregated trial."""

        evaluation = ObjectiveEvaluation(
            objective_values=result.objective_values,
            constraint_values=result.constraint_values,
            constraint_violations=result.constraint_violations,
            feasible=result.feasible,
        )
        self._validate_evaluation(evaluation)
        return evaluation

    def compare_trials(
        self,
        left: TrialResult,
        right: TrialResult,
    ) -> ComparisonOutcome:
        """Compare two aggregated trials under this engine's objective."""

        return self.compare(
            self.evaluation_from_trial(left),
            self.evaluation_from_trial(right),
        )

    def dominates(
        self,
        left: ObjectiveEvaluation,
        right: ObjectiveEvaluation,
    ) -> bool:
        """Return whether left Pareto-dominates right after feasibility handling."""

        self._validate_evaluation(left)
        self._validate_evaluation(right)
        feasibility = self._compare_feasibility(left, right)
        if feasibility is ComparisonOutcome.BETTER:
            return True
        if feasibility is ComparisonOutcome.WORSE:
            return False

        left_values = self._canonical_values(left.objective_values)
        right_values = self._canonical_values(right.objective_values)
        return all(a <= b for a, b in zip(left_values, right_values, strict=True)) and any(
            a < b for a, b in zip(left_values, right_values, strict=True)
        )

    def pareto_front(
        self, evaluations: Iterable[ObjectiveEvaluation]
    ) -> tuple[ObjectiveEvaluation, ...]:
        """Return all non-dominated evaluations while preserving input order."""

        candidates = tuple(evaluations)
        for candidate in candidates:
            self._validate_evaluation(candidate)
        return tuple(
            candidate
            for index, candidate in enumerate(candidates)
            if not any(
                self.dominates(other, candidate)
                for other_index, other in enumerate(candidates)
                if other_index != index
            )
        )

    def sort_key(self, evaluation: ObjectiveEvaluation) -> tuple[float, ...]:
        """Return a minimization key for total-order objective modes."""

        self._validate_evaluation(evaluation)
        if self.spec.mode is ComparisonMode.PARETO:
            raise ValueError("Pareto objectives define a partial order, not a sort key")

        key: list[float] = []
        if self.spec.feasibility_first:
            key.extend(
                (
                    0.0 if evaluation.feasible else 1.0,
                    float(evaluation.violated_constraint_count),
                    evaluation.total_constraint_violation,
                )
            )
        key.extend(self._objective_key(evaluation.objective_values))
        return tuple(key)

    def _validate_spec(self) -> None:
        if not self.spec.components:
            raise ValueError("an objective requires at least one component")
        if self.spec.mode is ComparisonMode.SINGLE and len(self.spec.components) != 1:
            raise ValueError("single-objective mode requires exactly one component")

        component_names = [component.name for component in self.spec.components]
        if len(component_names) != len(set(component_names)):
            raise ValueError("objective component names must be unique")
        if any(not component.name or not component.metric for component in self.spec.components):
            raise ValueError("objective component names and metrics cannot be empty")
        if any(
            not math.isfinite(component.weight) or component.weight < 0.0
            for component in self.spec.components
        ):
            raise ValueError("objective weights must be finite and non-negative")
        if self.spec.mode is ComparisonMode.WEIGHTED_SUM and not any(
            component.weight > 0.0 for component in self.spec.components
        ):
            raise ValueError("a weighted objective requires at least one positive weight")

        constraint_names = [
            self._constraint_name(constraint) for constraint in self.spec.constraints
        ]
        if len(constraint_names) != len(set(constraint_names)):
            raise ValueError("constraint names must be unique")
        if any(
            not math.isfinite(constraint.threshold)
            or not math.isfinite(constraint.tolerance)
            or constraint.tolerance < 0.0
            for constraint in self.spec.constraints
        ):
            raise ValueError("constraint thresholds must be finite and tolerances non-negative")

    def _validate_evaluation(self, evaluation: ObjectiveEvaluation) -> None:
        if len(evaluation.objective_values) != len(self.spec.components):
            raise ValueError("objective evaluation does not match the objective spec")

    @staticmethod
    def _metric(metrics: MetricSet, name: str) -> float:
        if name not in metrics:
            raise KeyError(f"required metric is missing: {name}")
        return float(metrics[name])

    @staticmethod
    def _constraint_name(constraint: ConstraintSpec) -> str:
        return constraint.name or (
            f"{constraint.metric}:{constraint.operator.value}:{constraint.threshold:g}"
        )

    @staticmethod
    def _evaluate_constraint(
        constraint: ConstraintSpec, value: float
    ) -> tuple[bool, float]:
        lower = constraint.threshold - constraint.tolerance
        upper = constraint.threshold + constraint.tolerance

        if constraint.operator is ConstraintOperator.LESS_THAN:
            satisfied = value < upper
            violation = max(0.0, value - upper)
            if not satisfied and violation == 0.0:
                violation = math.ulp(max(abs(value), abs(upper), 1.0))
            return satisfied, violation
        if constraint.operator is ConstraintOperator.LESS_THAN_OR_EQUAL:
            return value <= upper, max(0.0, value - upper)
        if constraint.operator is ConstraintOperator.EQUAL:
            return lower <= value <= upper, max(0.0, abs(value - constraint.threshold) - constraint.tolerance)
        if constraint.operator is ConstraintOperator.GREATER_THAN_OR_EQUAL:
            return value >= lower, max(0.0, lower - value)
        if constraint.operator is ConstraintOperator.GREATER_THAN:
            satisfied = value > lower
            violation = max(0.0, lower - value)
            if not satisfied and violation == 0.0:
                violation = math.ulp(max(abs(value), abs(lower), 1.0))
            return satisfied, violation
        raise ValueError(f"unsupported constraint operator: {constraint.operator}")

    def _compare_feasibility(
        self,
        left: ObjectiveEvaluation,
        right: ObjectiveEvaluation,
    ) -> ComparisonOutcome:
        if not self.spec.feasibility_first:
            return ComparisonOutcome.EQUIVALENT
        if left.feasible and not right.feasible:
            return ComparisonOutcome.BETTER
        if right.feasible and not left.feasible:
            return ComparisonOutcome.WORSE
        if left.feasible:
            return ComparisonOutcome.EQUIVALENT

        left_key = (
            left.violated_constraint_count,
            left.total_constraint_violation,
        )
        right_key = (
            right.violated_constraint_count,
            right.total_constraint_violation,
        )
        if left_key < right_key:
            return ComparisonOutcome.BETTER
        if left_key > right_key:
            return ComparisonOutcome.WORSE
        return ComparisonOutcome.EQUIVALENT

    def _canonical_values(self, values: Sequence[float]) -> tuple[float, ...]:
        return tuple(
            value if component.direction is Direction.MINIMIZE else -value
            for component, value in zip(self.spec.components, values, strict=True)
        )

    def _objective_key(self, values: Sequence[float]) -> tuple[float, ...]:
        canonical = self._canonical_values(values)
        if self.spec.mode is ComparisonMode.WEIGHTED_SUM:
            return (
                sum(
                    value * component.weight
                    for component, value in zip(
                        self.spec.components, canonical, strict=True
                    )
                ),
            )
        return canonical
