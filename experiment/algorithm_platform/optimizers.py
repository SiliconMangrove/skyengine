"""Built-in experiment-level parameter search optimizers.

These implementations operate on algorithm parameters, not on domain
solutions.  A produced :class:`Candidate` therefore becomes one Trial in the
tuning workflow and may only be scored by the authoritative ``TrialResult``
returned through ``tell``.  Numeric ``step`` values describe an additive
lattice; ``logarithmic`` changes random sampling and numeric crossover to log
space while grid search still enumerates every discrete lattice value.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import (
    AlgorithmInterface,
    AlgorithmManifest,
    AlgorithmRef,
    ArtifactKind,
    ArtifactRef,
    Budget,
    Candidate,
    ComparisonMode,
    Direction,
    ObjectiveSpec,
    PLATFORM_PROTOCOL_VERSION,
    ParameterSet,
    ParameterValue,
    SearchDimension,
    SearchDimensionKind,
    TrialResult,
    TrialStatus,
)
from .objectives import ComparisonOutcome, ObjectiveEngine
from .protocols import ArtifactPublisher
from .serialization import to_jsonable


if TYPE_CHECKING:
    from .registry import PlatformRegistry


BUILTIN_OPTIMIZER_VERSION = "1.0.0"
GRID_SEARCH_OPTIMIZER_ID = "platform.grid_search"
RANDOM_SEARCH_OPTIMIZER_ID = "platform.random_search"
GENETIC_SEARCH_OPTIMIZER_ID = "platform.genetic_search"


@dataclass(frozen=True, slots=True)
class _Axis:
    """Finite parameter axis addressable without materializing all values."""

    count: int
    value_at: Callable[[int], ParameterValue]


class _PermutationIndexSampler:
    """Lazy Fisher-Yates sampler for a potentially large Cartesian product."""

    def __init__(self, cardinality: int) -> None:
        self.remaining = cardinality
        self.swaps: dict[int, int] = {}

    def take(self, rng: random.Random) -> int | None:
        if self.remaining == 0:
            return None
        selected_slot = rng.randrange(self.remaining)
        last_slot = self.remaining - 1
        selected_index = self.swaps.get(selected_slot, selected_slot)
        replacement = self.swaps.get(last_slot, last_slot)
        if selected_slot != last_slot:
            self.swaps[selected_slot] = replacement
        self.swaps.pop(last_slot, None)
        self.remaining -= 1
        return selected_index

    def snapshot(self) -> Mapping[str, object]:
        return {
            "remaining": self.remaining,
            "swaps": dict(sorted(self.swaps.items())),
        }


class _ParameterSpace:
    """Validated, stably ordered access to typed search dimensions."""

    def __init__(self, dimensions: Mapping[str, SearchDimension]) -> None:
        if not dimensions:
            raise ValueError("search_space must not be empty")
        self.names = tuple(sorted(dimensions))
        self.dimensions = {name: dimensions[name] for name in self.names}
        for name in self.names:
            if not name:
                raise ValueError("search dimension names must not be empty")
            _validate_dimension(name, self.dimensions[name])

    def grid_axes(self, points_per_dimension: int) -> tuple[_Axis, ...]:
        return tuple(
            _grid_axis(self.dimensions[name], points_per_dimension)
            for name in self.names
        )

    def random_axes(self) -> tuple[_Axis, ...] | None:
        axes: list[_Axis] = []
        for name in self.names:
            axis = _random_axis(self.dimensions[name])
            if axis is None:
                return None
            axes.append(axis)
        return tuple(axes)

    def from_index(self, index: int, axes: Sequence[_Axis]) -> dict[str, ParameterValue]:
        values: dict[str, ParameterValue] = {}
        for name, axis in reversed(tuple(zip(self.names, axes, strict=True))):
            index, offset = divmod(index, axis.count)
            values[name] = axis.value_at(offset)
        return {name: values[name] for name in self.names}

    def sample(self, rng: random.Random) -> dict[str, ParameterValue]:
        return {
            name: _sample_dimension(self.dimensions[name], rng)
            for name in self.names
        }

    def breed(
        self,
        left: ParameterSet,
        right: ParameterSet,
        rng: random.Random,
        *,
        crossover_rate: float,
        mutation_rate: float,
    ) -> dict[str, ParameterValue]:
        if rng.random() >= crossover_rate:
            source = left if rng.random() < 0.5 else right
            child = {name: source[name] for name in self.names}
        else:
            child = {
                name: _crossover_value(
                    self.dimensions[name],
                    left[name],
                    right[name],
                    rng,
                )
                for name in self.names
            }
        for name in self.names:
            if rng.random() < mutation_rate:
                child[name] = _sample_dimension(self.dimensions[name], rng)
        return child


class _SearchOptimizerBase:
    """Shared ask/tell state machine and authoritative objective handling."""

    optimizer_id = "platform.search"

    def __init__(self) -> None:
        self._initialized = False

    def initialize(
        self,
        search_space: Mapping[str, SearchDimension],
        objective: ObjectiveSpec,
        budget: Budget,
        seed: int,
        artifact_publisher: ArtifactPublisher,
    ) -> None:
        if self._initialized:
            raise RuntimeError("a search optimizer instance can only be initialized once")
        if budget.max_evaluations is not None and budget.max_evaluations <= 0:
            raise ValueError("budget.max_evaluations must be positive")

        self._space = _ParameterSpace(search_space)
        self._objective = objective
        self._objective_engine = ObjectiveEngine(objective)
        self._budget = budget
        self._max_evaluations = budget.max_evaluations
        self._seed = seed
        self._rng = random.Random(seed)
        self._artifact_publisher = artifact_publisher
        self._issued_count = 0
        self._pending: dict[str, Candidate[ParameterSet]] = {}
        self._completed: dict[str, TrialResult] = {}
        self._seen_candidate_ids: set[str] = set()
        self._source_exhausted = False
        self._initialize_search()
        self._initialized = True

    def ask(self, count: int) -> tuple[Candidate[ParameterSet], ...]:
        self._require_initialized()
        if count <= 0:
            raise ValueError("ask count must be positive")

        remaining = self._remaining_budget()
        requested = count if remaining is None else min(count, remaining)
        candidates: list[Candidate[ParameterSet]] = []
        duplicate_attempts = 0
        duplicate_limit = max(100, requested * 100)
        while len(candidates) < requested:
            parameters = self._next_parameters()
            if parameters is None:
                break
            candidate_id = _candidate_id(parameters)
            if candidate_id in self._seen_candidate_ids:
                duplicate_attempts += 1
                if duplicate_attempts >= duplicate_limit:
                    self._source_exhausted = True
                    break
                continue

            candidate = Candidate(
                candidate_id=candidate_id,
                value=parameters,
                metadata={
                    "optimizer_id": self.optimizer_id,
                    "sequence": self._issued_count,
                    **self._candidate_metadata(),
                },
            )
            self._seen_candidate_ids.add(candidate_id)
            self._pending[candidate_id] = candidate
            self._issued_count += 1
            candidates.append(candidate)
            duplicate_attempts = 0
        return tuple(candidates)

    def tell(self, results: Sequence[TrialResult]) -> None:
        self._require_initialized()
        received_ids: set[str] = set()
        validated: list[TrialResult] = []
        for result in results:
            candidate_id = result.candidate_id
            if candidate_id is None:
                raise ValueError("TrialResult.candidate_id is required by SearchOptimizer.tell")
            if candidate_id in received_ids:
                raise ValueError(f"duplicate candidate result in tell: {candidate_id}")
            if candidate_id in self._completed:
                raise ValueError(f"candidate was already reported: {candidate_id}")
            if candidate_id not in self._pending:
                raise ValueError(f"candidate was not issued by this optimizer: {candidate_id}")
            candidate = self._pending[candidate_id]
            if _canonical_parameters(result.parameters) != _canonical_parameters(
                candidate.value
            ):
                raise ValueError(f"candidate parameters changed before tell: {candidate_id}")
            if result.status is TrialStatus.SUCCEEDED:
                self._validate_result(result)
            elif result.status not in {
                TrialStatus.FAILED,
                TrialStatus.CANCELLED,
            }:
                raise ValueError(
                    "SearchOptimizer.tell requires a terminal TrialResult"
                )
            received_ids.add(candidate_id)
            validated.append(result)

        for result in validated:
            candidate_id = result.candidate_id
            if candidate_id is None:
                raise AssertionError("validated TrialResult has no candidate_id")
            self._pending.pop(candidate_id)
            self._completed[candidate_id] = result
        self._results_received(tuple(validated))

    def should_stop(self) -> bool:
        self._require_initialized()
        budget_exhausted = (
            self._max_evaluations is not None
            and self._issued_count >= self._max_evaluations
        )
        return (budget_exhausted or self._source_exhausted) and not self._pending

    def snapshot(self) -> ArtifactRef:
        self._require_initialized()
        best_ids = tuple(
            result.candidate_id
            for result in self._best_results()
            if result.candidate_id is not None
        )
        return self._artifact_publisher.publish_json(
            {
                "schema_version": 1,
                "optimizer_id": self.optimizer_id,
                "seed": self._seed,
                "search_space": self._space.dimensions,
                "objective": self._objective,
                "budget": self._budget,
                "issued_count": self._issued_count,
                "completed_count": len(self._completed),
                "source_exhausted": self._source_exhausted,
                "pending": [
                    {
                        "candidate_id": candidate.candidate_id,
                        "parameters": candidate.value,
                        "metadata": candidate.metadata,
                    }
                    for candidate in self._pending.values()
                ],
                "completed": [
                    _trial_summary(result) for result in self._completed.values()
                ],
                "best_candidate_ids": best_ids,
                "rng_state": self._rng.getstate(),
                "optimizer_state": self._optimizer_snapshot(),
            },
            kind=ArtifactKind.CHECKPOINT,
            metadata={
                "optimizer_id": self.optimizer_id,
                "seed": self._seed,
                "issued_count": self._issued_count,
                "completed_count": len(self._completed),
            },
        )

    def _remaining_budget(self) -> int | None:
        if self._max_evaluations is None:
            return None
        return max(0, self._max_evaluations - self._issued_count)

    def _validate_result(self, result: TrialResult) -> None:
        self._objective_engine.evaluation_from_trial(result)
        if not all(math.isfinite(value) for value in result.objective_values):
            raise ValueError("trial objective values must be finite")
        if not all(math.isfinite(value) for value in result.constraint_values.values()):
            raise ValueError("trial constraint values must be finite")
        if not all(
            math.isfinite(value) and value >= 0.0
            for value in result.constraint_violations.values()
        ):
            raise ValueError("trial constraint violations must be finite and non-negative")

    def _best_results(self) -> tuple[TrialResult, ...]:
        results = tuple(
            result
            for result in self._completed.values()
            if result.status is TrialStatus.SUCCEEDED
        )
        if not results:
            return ()
        if self._objective.mode is ComparisonMode.PARETO:
            return tuple(
                results[index]
                for index in _pareto_front_indices(results, self._objective_engine)
            )

        ranked = sorted(
            results,
            key=lambda result: (
                self._objective_engine.sort_key(
                    self._objective_engine.evaluation_from_trial(result)
                ),
                result.candidate_id or "",
            ),
        )
        best = ranked[0]
        return tuple(
            result
            for result in ranked
            if self._objective_engine.compare_trials(result, best)
            is ComparisonOutcome.EQUIVALENT
        )

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("search optimizer is not initialized")

    def _initialize_search(self) -> None:
        pass

    def _next_parameters(self) -> dict[str, ParameterValue] | None:
        raise NotImplementedError

    def _candidate_metadata(self) -> Mapping[str, object]:
        return {}

    def _results_received(self, results: Sequence[TrialResult]) -> None:
        pass

    def _optimizer_snapshot(self) -> Mapping[str, object]:
        return {}


class GridSearchOptimizer(_SearchOptimizerBase):
    """Deterministic exhaustive search over a typed Cartesian grid."""

    optimizer_id = GRID_SEARCH_OPTIMIZER_ID

    def __init__(self, *, points_per_dimension: int = 10) -> None:
        super().__init__()
        if points_per_dimension <= 0:
            raise ValueError("points_per_dimension must be positive")
        self.points_per_dimension = points_per_dimension

    def _initialize_search(self) -> None:
        self._axes = self._space.grid_axes(self.points_per_dimension)
        self._cardinality = math.prod(axis.count for axis in self._axes)
        self._next_index = 0

    def _next_parameters(self) -> dict[str, ParameterValue] | None:
        if self._next_index >= self._cardinality:
            self._source_exhausted = True
            return None
        parameters = self._space.from_index(self._next_index, self._axes)
        self._next_index += 1
        if self._next_index >= self._cardinality:
            self._source_exhausted = True
        return parameters

    def _optimizer_snapshot(self) -> Mapping[str, object]:
        return {
            "points_per_dimension": self.points_per_dimension,
            "grid_cardinality": self._cardinality,
            "next_grid_index": self._next_index,
        }


class RandomSearchOptimizer(_SearchOptimizerBase):
    """Seeded random search with exact no-replacement discrete sampling."""

    optimizer_id = RANDOM_SEARCH_OPTIMIZER_ID

    def _initialize_search(self) -> None:
        self._axes = self._space.random_axes()
        self._index_sampler = (
            _PermutationIndexSampler(math.prod(axis.count for axis in self._axes))
            if self._axes is not None
            else None
        )

    def _next_parameters(self) -> dict[str, ParameterValue] | None:
        if self._index_sampler is None:
            return self._space.sample(self._rng)
        index = self._index_sampler.take(self._rng)
        if index is None:
            self._source_exhausted = True
            return None
        if self._index_sampler.remaining == 0:
            self._source_exhausted = True
        if self._axes is None:
            raise AssertionError("discrete sampler has no axes")
        return self._space.from_index(index, self._axes)

    def _optimizer_snapshot(self) -> Mapping[str, object]:
        return {
            "discrete_without_replacement": self._index_sampler is not None,
            "index_sampler": (
                self._index_sampler.snapshot()
                if self._index_sampler is not None
                else None
            ),
        }


class GeneticSearchOptimizer(_SearchOptimizerBase):
    """Generational GA for tuning another algorithm's parameter set.

    Constraint handling and objective comparison are delegated to
    :class:`ObjectiveEngine`.  Pareto objectives use non-dominated sorting and
    crowding distance for a deterministic NSGA-II-style selection order.
    """

    optimizer_id = GENETIC_SEARCH_OPTIMIZER_ID

    def __init__(
        self,
        *,
        population_size: int = 24,
        elite_fraction: float = 0.25,
        mutation_rate: float = 0.2,
        crossover_rate: float = 0.9,
        tournament_size: int = 3,
    ) -> None:
        super().__init__()
        if population_size < 2:
            raise ValueError("population_size must be at least two")
        if not 0.0 < elite_fraction <= 1.0:
            raise ValueError("elite_fraction must be in (0, 1]")
        if not 0.0 <= mutation_rate <= 1.0:
            raise ValueError("mutation_rate must be in [0, 1]")
        if not 0.0 <= crossover_rate <= 1.0:
            raise ValueError("crossover_rate must be in [0, 1]")
        if tournament_size <= 0:
            raise ValueError("tournament_size must be positive")
        self.population_size = population_size
        self.elite_fraction = elite_fraction
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.tournament_size = tournament_size

    def _initialize_search(self) -> None:
        self._generation = 0
        self._generation_queue: deque[dict[str, ParameterValue]] = deque()
        self._generation_results: list[TrialResult] = []
        self._generation_completed_count = 0
        self._elite_archive: tuple[TrialResult, ...] = ()
        self._last_ranked_population: tuple[TrialResult, ...] = ()
        axes = self._space.random_axes()
        self._fallback_axes = axes
        self._fallback_sampler = (
            _PermutationIndexSampler(math.prod(axis.count for axis in axes))
            if axes is not None
            else None
        )
        self._fill_initial_generation()

    def _next_parameters(self) -> dict[str, ParameterValue] | None:
        if not self._generation_queue:
            if self._pending:
                return None
            if self._generation_results:
                self._fill_offspring_generation()
            elif self._generation_completed_count:
                self._generation += 1
                self._generation_completed_count = 0
                self._fill_with_random_parameters(self._generation_target())
                if not self._generation_queue:
                    self._source_exhausted = True
            if not self._generation_queue:
                return None
        return self._generation_queue.popleft()

    def _candidate_metadata(self) -> Mapping[str, object]:
        return {"generation": self._generation}

    def _results_received(self, results: Sequence[TrialResult]) -> None:
        self._generation_completed_count += len(results)
        self._generation_results.extend(
            result
            for result in results
            if result.status is TrialStatus.SUCCEEDED
        )

    def _fill_initial_generation(self) -> None:
        target = self._generation_target()
        self._fill_with_random_parameters(target)
        if not self._generation_queue:
            self._source_exhausted = True

    def _fill_offspring_generation(self) -> None:
        selection_population = {
            result.candidate_id: result
            for result in (*self._elite_archive, *self._generation_results)
        }
        ranked = _rank_for_selection(
            tuple(selection_population.values()),
            self._objective_engine,
            self._objective,
        )
        self._last_ranked_population = tuple(ranked)
        self._generation += 1
        self._generation_results = []
        self._generation_completed_count = 0
        target = self._generation_target()
        if target == 0:
            return

        elite_count = min(
            len(ranked),
            max(1, math.ceil(self.population_size * self.elite_fraction)),
        )
        self._elite_archive = tuple(ranked[:elite_count])
        parent_pool = self._elite_archive
        queued_ids: set[str] = set()
        attempts = 0
        attempt_limit = max(200, target * 200)
        while len(self._generation_queue) < target and attempts < attempt_limit:
            left = self._tournament(parent_pool)
            right = self._tournament(parent_pool)
            child = self._space.breed(
                left.parameters,
                right.parameters,
                self._rng,
                crossover_rate=self.crossover_rate,
                mutation_rate=self.mutation_rate,
            )
            candidate_id = _candidate_id(child)
            if (
                candidate_id not in self._seen_candidate_ids
                and candidate_id not in queued_ids
            ):
                self._generation_queue.append(child)
                queued_ids.add(candidate_id)
            attempts += 1

        self._fill_with_random_parameters(target, queued_ids)
        if not self._generation_queue:
            self._source_exhausted = True

    def _fill_with_random_parameters(
        self,
        target: int,
        queued_ids: set[str] | None = None,
    ) -> None:
        queued = set() if queued_ids is None else queued_ids
        attempts = 0
        attempt_limit = max(200, target * 200)
        while len(self._generation_queue) < target and attempts < attempt_limit:
            parameters = self._space.sample(self._rng)
            candidate_id = _candidate_id(parameters)
            if (
                candidate_id not in self._seen_candidate_ids
                and candidate_id not in queued
            ):
                self._generation_queue.append(parameters)
                queued.add(candidate_id)
            attempts += 1

        while (
            len(self._generation_queue) < target
            and self._fallback_sampler is not None
        ):
            index = self._fallback_sampler.take(self._rng)
            if index is None:
                break
            if self._fallback_axes is None:
                raise AssertionError("fallback sampler has no axes")
            parameters = self._space.from_index(index, self._fallback_axes)
            candidate_id = _candidate_id(parameters)
            if (
                candidate_id not in self._seen_candidate_ids
                and candidate_id not in queued
            ):
                self._generation_queue.append(parameters)
                queued.add(candidate_id)

    def _generation_target(self) -> int:
        remaining = self._remaining_budget()
        return self.population_size if remaining is None else min(self.population_size, remaining)

    def _tournament(self, population: Sequence[TrialResult]) -> TrialResult:
        count = min(self.tournament_size, len(population))
        contenders = self._rng.sample(list(population), count)
        positions = {
            result.candidate_id: index for index, result in enumerate(population)
        }
        return min(
            contenders,
            key=lambda result: positions[result.candidate_id],
        )

    def _optimizer_snapshot(self) -> Mapping[str, object]:
        return {
            "population_size": self.population_size,
            "elite_fraction": self.elite_fraction,
            "mutation_rate": self.mutation_rate,
            "crossover_rate": self.crossover_rate,
            "tournament_size": self.tournament_size,
            "generation": self._generation,
            "queued_parameters": tuple(self._generation_queue),
            "generation_result_ids": tuple(
                result.candidate_id for result in self._generation_results
            ),
            "generation_completed_count": self._generation_completed_count,
            "elite_archive_ids": tuple(
                result.candidate_id for result in self._elite_archive
            ),
            "last_ranked_population_ids": tuple(
                result.candidate_id for result in self._last_ranked_population
            ),
            "fallback_sampler": (
                self._fallback_sampler.snapshot()
                if self._fallback_sampler is not None
                else None
            ),
        }


def create_grid_search_optimizer(reference: AlgorithmRef) -> GridSearchOptimizer:
    """Factory compatible with :class:`PlatformRegistry`."""

    return GridSearchOptimizer(
        points_per_dimension=int(reference.parameters.get("points_per_dimension", 10))
    )


def create_random_search_optimizer(reference: AlgorithmRef) -> RandomSearchOptimizer:
    """Factory compatible with :class:`PlatformRegistry`."""

    return RandomSearchOptimizer()


def create_genetic_search_optimizer(reference: AlgorithmRef) -> GeneticSearchOptimizer:
    """Factory compatible with :class:`PlatformRegistry`."""

    return GeneticSearchOptimizer(
        population_size=int(reference.parameters.get("population_size", 24)),
        elite_fraction=float(reference.parameters.get("elite_fraction", 0.25)),
        mutation_rate=float(reference.parameters.get("mutation_rate", 0.2)),
        crossover_rate=float(reference.parameters.get("crossover_rate", 0.9)),
        tournament_size=int(reference.parameters.get("tournament_size", 3)),
    )


GRID_SEARCH_MANIFEST = AlgorithmManifest(
    algorithm_id=GRID_SEARCH_OPTIMIZER_ID,
    name="Grid Search Optimizer",
    version=BUILTIN_OPTIMIZER_VERSION,
    protocol_version=PLATFORM_PROTOCOL_VERSION,
    interfaces=frozenset({AlgorithmInterface.SEARCH_OPTIMIZER}),
    entrypoints={
        AlgorithmInterface.SEARCH_OPTIMIZER: (
            "experiment.algorithm_platform.optimizers:GridSearchOptimizer"
        )
    },
    parameter_schema={
        "type": "object",
        "properties": {
            "points_per_dimension": {"type": "integer", "minimum": 1, "default": 10}
        },
        "additionalProperties": False,
    },
    output_artifact_kinds=frozenset({ArtifactKind.CHECKPOINT}),
)

RANDOM_SEARCH_MANIFEST = AlgorithmManifest(
    algorithm_id=RANDOM_SEARCH_OPTIMIZER_ID,
    name="Random Search Optimizer",
    version=BUILTIN_OPTIMIZER_VERSION,
    protocol_version=PLATFORM_PROTOCOL_VERSION,
    interfaces=frozenset({AlgorithmInterface.SEARCH_OPTIMIZER}),
    entrypoints={
        AlgorithmInterface.SEARCH_OPTIMIZER: (
            "experiment.algorithm_platform.optimizers:RandomSearchOptimizer"
        )
    },
    parameter_schema={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    output_artifact_kinds=frozenset({ArtifactKind.CHECKPOINT}),
)

GENETIC_SEARCH_MANIFEST = AlgorithmManifest(
    algorithm_id=GENETIC_SEARCH_OPTIMIZER_ID,
    name="Genetic Search Optimizer",
    version=BUILTIN_OPTIMIZER_VERSION,
    protocol_version=PLATFORM_PROTOCOL_VERSION,
    interfaces=frozenset({AlgorithmInterface.SEARCH_OPTIMIZER}),
    entrypoints={
        AlgorithmInterface.SEARCH_OPTIMIZER: (
            "experiment.algorithm_platform.optimizers:GeneticSearchOptimizer"
        )
    },
    parameter_schema={
        "type": "object",
        "properties": {
            "population_size": {"type": "integer", "minimum": 2, "default": 24},
            "elite_fraction": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "maximum": 1.0,
                "default": 0.25,
            },
            "mutation_rate": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.2,
            },
            "crossover_rate": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.9,
            },
            "tournament_size": {"type": "integer", "minimum": 1, "default": 3},
        },
        "additionalProperties": False,
    },
    output_artifact_kinds=frozenset({ArtifactKind.CHECKPOINT}),
)


def builtin_search_optimizer_registrations() -> tuple[
    tuple[
        AlgorithmManifest,
        Mapping[AlgorithmInterface, Callable[[AlgorithmRef], object]],
    ],
    ...,
]:
    """Return explicit registrations without mutating a global registry."""

    return (
        (
            GRID_SEARCH_MANIFEST,
            {AlgorithmInterface.SEARCH_OPTIMIZER: create_grid_search_optimizer},
        ),
        (
            RANDOM_SEARCH_MANIFEST,
            {AlgorithmInterface.SEARCH_OPTIMIZER: create_random_search_optimizer},
        ),
        (
            GENETIC_SEARCH_MANIFEST,
            {AlgorithmInterface.SEARCH_OPTIMIZER: create_genetic_search_optimizer},
        ),
    )


def register_builtin_search_optimizers(registry: PlatformRegistry) -> None:
    """Register all built-in optimizer factories in an explicit registry."""

    for manifest, factories in builtin_search_optimizer_registrations():
        registry.register_algorithm(manifest, factories)


def _validate_dimension(name: str, dimension: SearchDimension) -> None:
    if dimension.kind is SearchDimensionKind.CATEGORICAL:
        if not dimension.choices:
            raise ValueError(f"categorical search dimension {name!r} requires choices")
        keys = tuple(_canonical_value(choice) for choice in dimension.choices)
        if len(keys) != len(set(keys)):
            raise ValueError(f"categorical search dimension {name!r} has duplicate choices")
        if (
            dimension.low is not None
            or dimension.high is not None
            or dimension.step is not None
            or dimension.logarithmic
        ):
            raise ValueError(
                f"categorical search dimension {name!r} cannot define numeric bounds"
            )
        return

    if dimension.choices:
        raise ValueError(f"numeric search dimension {name!r} cannot define choices")
    if dimension.low is None or dimension.high is None:
        raise ValueError(f"numeric search dimension {name!r} requires low and high")
    if isinstance(dimension.low, bool) or isinstance(dimension.high, bool):
        raise TypeError(f"numeric search dimension {name!r} bounds must be numbers")
    low = float(dimension.low)
    high = float(dimension.high)
    if not math.isfinite(low) or not math.isfinite(high) or low >= high:
        raise ValueError(f"search dimension {name!r} requires finite low < high")
    if dimension.kind is SearchDimensionKind.INTEGER and (
        not isinstance(dimension.low, int) or not isinstance(dimension.high, int)
    ):
        raise TypeError(f"integer search dimension {name!r} requires integer bounds")
    if dimension.logarithmic and low <= 0.0:
        raise ValueError(f"logarithmic search dimension {name!r} requires low > 0")
    if dimension.step is not None:
        if isinstance(dimension.step, bool) or not math.isfinite(float(dimension.step)):
            raise TypeError(f"search dimension {name!r} step must be a finite number")
        if float(dimension.step) <= 0.0:
            raise ValueError(f"search dimension {name!r} step must be positive")
        if (
            dimension.kind is SearchDimensionKind.INTEGER
            and not isinstance(dimension.step, int)
        ):
            raise TypeError(
                f"integer search dimension {name!r} requires an integer step"
            )
    if dimension.kind not in {
        SearchDimensionKind.FLOAT,
        SearchDimensionKind.INTEGER,
    }:
        raise ValueError(f"unsupported search dimension kind: {dimension.kind}")


def _grid_axis(dimension: SearchDimension, points: int) -> _Axis:
    if dimension.kind is SearchDimensionKind.CATEGORICAL:
        values = tuple(dimension.choices)
        return _Axis(len(values), values.__getitem__)
    if dimension.low is None or dimension.high is None:
        raise AssertionError("validated numeric dimension has no bounds")

    low = float(dimension.low)
    high = float(dimension.high)
    if dimension.kind is SearchDimensionKind.INTEGER:
        step = int(dimension.step or 1)
        count = (int(dimension.high) - int(dimension.low)) // step + 1
        base = int(dimension.low)
        return _Axis(count, lambda index: base + index * step)
    if dimension.step is not None:
        step = float(dimension.step)
        count = _additive_step_count(low, high, step)
        return _Axis(
            count,
            lambda index: _stable_float(min(high, low + index * step)),
        )
    if dimension.logarithmic:
        values = tuple(
            _project_numeric(
                math.exp(
                    math.log(low)
                    + (math.log(high) - math.log(low)) * index / max(1, points - 1)
                ),
                dimension,
            )
            for index in range(points)
        )
        values = _deduplicate_values(values)
        return _Axis(len(values), values.__getitem__)

    values = tuple(
        _stable_float(low + (high - low) * index / max(1, points - 1))
        for index in range(points)
    )
    values = _deduplicate_values(values)
    return _Axis(len(values), values.__getitem__)


def _random_axis(dimension: SearchDimension) -> _Axis | None:
    if dimension.kind is SearchDimensionKind.CATEGORICAL:
        values = tuple(dimension.choices)
        return _Axis(len(values), values.__getitem__)
    if dimension.low is None or dimension.high is None:
        raise AssertionError("validated numeric dimension has no bounds")
    if dimension.logarithmic:
        return None
    if dimension.kind is SearchDimensionKind.INTEGER:
        step = int(dimension.step or 1)
        count = (int(dimension.high) - int(dimension.low)) // step + 1
        base = int(dimension.low)
        return _Axis(count, lambda index: base + index * step)
    if dimension.step is not None:
        low = float(dimension.low)
        high = float(dimension.high)
        step = float(dimension.step)
        count = _additive_step_count(low, high, step)
        return _Axis(
            count,
            lambda index: _stable_float(min(high, low + index * step)),
        )
    return None


def _sample_dimension(
    dimension: SearchDimension,
    rng: random.Random,
) -> ParameterValue:
    if dimension.kind is SearchDimensionKind.CATEGORICAL:
        return dimension.choices[rng.randrange(len(dimension.choices))]
    if dimension.low is None or dimension.high is None:
        raise AssertionError("validated numeric dimension has no bounds")

    if dimension.logarithmic:
        raw = math.exp(
            rng.uniform(math.log(float(dimension.low)), math.log(float(dimension.high)))
        )
        return _project_numeric(raw, dimension)
    if dimension.kind is SearchDimensionKind.INTEGER:
        step = int(dimension.step or 1)
        count = (int(dimension.high) - int(dimension.low)) // step + 1
        return int(dimension.low) + rng.randrange(count) * step
    if dimension.step is not None:
        step = float(dimension.step)
        count = _additive_step_count(
            float(dimension.low), float(dimension.high), step
        )
        return _stable_float(
            min(
                float(dimension.high),
                float(dimension.low) + rng.randrange(count) * step,
            )
        )
    return _stable_float(rng.uniform(float(dimension.low), float(dimension.high)))


def _crossover_value(
    dimension: SearchDimension,
    left: ParameterValue,
    right: ParameterValue,
    rng: random.Random,
) -> ParameterValue:
    if dimension.kind is SearchDimensionKind.CATEGORICAL:
        return left if rng.random() < 0.5 else right
    left_number = float(left)
    right_number = float(right)
    alpha = rng.random()
    if dimension.logarithmic:
        raw = math.exp(
            alpha * math.log(left_number) + (1.0 - alpha) * math.log(right_number)
        )
    else:
        raw = alpha * left_number + (1.0 - alpha) * right_number
    return _project_numeric(raw, dimension)


def _project_numeric(value: float, dimension: SearchDimension) -> int | float:
    if dimension.low is None or dimension.high is None:
        raise AssertionError("validated numeric dimension has no bounds")
    low = float(dimension.low)
    high = float(dimension.high)
    value = min(high, max(low, value))
    if dimension.step is not None:
        step = float(dimension.step)
        value = low + round((value - low) / step) * step
        value = min(high, max(low, value))
    if dimension.kind is SearchDimensionKind.INTEGER:
        return min(int(dimension.high), max(int(dimension.low), round(value)))
    return _stable_float(value)


def _additive_step_count(low: float, high: float, step: float) -> int:
    return math.floor((high - low) / step + 1e-12) + 1


def _stable_float(value: float) -> float:
    return float(format(value, ".17g"))


def _deduplicate_values(values: Sequence[ParameterValue]) -> tuple[ParameterValue, ...]:
    unique: list[ParameterValue] = []
    keys: set[str] = set()
    for value in values:
        key = _canonical_value(value)
        if key not in keys:
            keys.add(key)
            unique.append(value)
    return tuple(unique)


def _candidate_id(parameters: ParameterSet) -> str:
    digest = hashlib.sha256(_canonical_parameters(parameters).encode("utf-8")).hexdigest()
    return f"candidate-{digest}"


def _canonical_parameters(parameters: ParameterSet) -> str:
    return json.dumps(
        to_jsonable(parameters),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_value(value: ParameterValue) -> str:
    return json.dumps(
        to_jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _trial_summary(result: TrialResult) -> Mapping[str, object]:
    return {
        "trial_id": result.trial_id,
        "candidate_id": result.candidate_id,
        "parameters": result.parameters,
        "objective_values": result.objective_values,
        "constraint_values": result.constraint_values,
        "constraint_violations": result.constraint_violations,
        "feasible": result.feasible,
        "metrics": result.metrics,
        "status": result.status.value,
        "failure": result.failure,
        "run_ids": tuple(run.run_id for run in result.run_results),
    }


def _pareto_front_indices(
    results: Sequence[TrialResult],
    engine: ObjectiveEngine,
) -> tuple[int, ...]:
    return tuple(
        index
        for index, candidate in enumerate(results)
        if not any(
            engine.dominates(
                engine.evaluation_from_trial(other),
                engine.evaluation_from_trial(candidate),
            )
            for other_index, other in enumerate(results)
            if other_index != index
        )
    )


def _rank_for_selection(
    results: Sequence[TrialResult],
    engine: ObjectiveEngine,
    objective: ObjectiveSpec,
) -> list[TrialResult]:
    if objective.mode is not ComparisonMode.PARETO:
        return sorted(
            results,
            key=lambda result: (
                engine.sort_key(engine.evaluation_from_trial(result)),
                result.candidate_id or "",
            ),
        )

    remaining = list(range(len(results)))
    ranked: list[TrialResult] = []
    while remaining:
        front = [
            index
            for index in remaining
            if not any(
                engine.dominates(
                    engine.evaluation_from_trial(results[other]),
                    engine.evaluation_from_trial(results[index]),
                )
                for other in remaining
                if other != index
            )
        ]
        distances = _crowding_distances(front, results, objective)
        front.sort(
            key=lambda index: (
                -distances[index],
                results[index].candidate_id or "",
            )
        )
        ranked.extend(results[index] for index in front)
        front_set = set(front)
        remaining = [index for index in remaining if index not in front_set]
    return ranked


def _crowding_distances(
    front: Sequence[int],
    results: Sequence[TrialResult],
    objective: ObjectiveSpec,
) -> dict[int, float]:
    distances = {index: 0.0 for index in front}
    if len(front) <= 2:
        return {index: math.inf for index in front}

    for component_index, component in enumerate(objective.components):
        canonical = {
            index: (
                results[index].objective_values[component_index]
                if component.direction is Direction.MINIMIZE
                else -results[index].objective_values[component_index]
            )
            for index in front
        }
        ordered = sorted(front, key=lambda index: (canonical[index], index))
        distances[ordered[0]] = math.inf
        distances[ordered[-1]] = math.inf
        span = canonical[ordered[-1]] - canonical[ordered[0]]
        if span == 0.0:
            continue
        for position in range(1, len(ordered) - 1):
            index = ordered[position]
            if not math.isinf(distances[index]):
                distances[index] += (
                    canonical[ordered[position + 1]]
                    - canonical[ordered[position - 1]]
                ) / span
    return distances


__all__ = [
    "BUILTIN_OPTIMIZER_VERSION",
    "GENETIC_SEARCH_MANIFEST",
    "GENETIC_SEARCH_OPTIMIZER_ID",
    "GRID_SEARCH_MANIFEST",
    "GRID_SEARCH_OPTIMIZER_ID",
    "GeneticSearchOptimizer",
    "GridSearchOptimizer",
    "RANDOM_SEARCH_MANIFEST",
    "RANDOM_SEARCH_OPTIMIZER_ID",
    "RandomSearchOptimizer",
    "builtin_search_optimizer_registrations",
    "create_genetic_search_optimizer",
    "create_grid_search_optimizer",
    "create_random_search_optimizer",
    "register_builtin_search_optimizers",
]
