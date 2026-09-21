"""Compile immutable experiment definitions into reproducible run matrices."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol

from .models import (
    AlgorithmInterface,
    AlgorithmRef,
    ArtifactKind,
    ArtifactRef,
    Budget,
    ComparisonMode,
    DomainCapability,
    ExecutionPlan,
    ExperimentSpec,
    RunPurpose,
    RunSeeds,
    RunSpec,
    SearchDimension,
    SearchDimensionKind,
    ScenarioRef,
    TrialSpec,
)
from .objectives import ObjectiveEngine
from .registry import PlatformRegistry, default_registry
from .schema import validate_schema_value


RunBuilder = Callable[[TrialSpec, ScenarioRef, int], RunSpec]


class WorkflowExpander(Protocol):
    """Expand one workflow into its initial immutable set of runs.

    Train and tune orchestrators can replace the default matrix expander with a
    workflow-specific implementation.  Later candidates remain runtime state;
    every candidate is compiled into a new immutable plan fragment before it is
    scheduled.
    """

    def __call__(
        self,
        experiment: ExperimentSpec,
        trials: tuple[TrialSpec, ...],
        build_run: RunBuilder,
    ) -> Sequence[RunSpec]:
        ...


class ExperimentCompiler:
    """Validate an experiment and deterministically expand executable work."""

    def __init__(
        self,
        registry: PlatformRegistry = default_registry,
        workflow_expanders: Mapping[RunPurpose, WorkflowExpander] | None = None,
    ) -> None:
        self._registry = registry
        self._workflow_expanders: dict[RunPurpose, WorkflowExpander] = {
            purpose: _expand_matrix for purpose in RunPurpose
        }
        if workflow_expanders:
            self._workflow_expanders.update(workflow_expanders)

    def compile(self, experiment: ExperimentSpec) -> ExecutionPlan:
        """Compile a complete plan with stable trial, run, and plan digests."""

        self.validate(experiment)
        if experiment.purpose is RunPurpose.TUNE:
            raise ValueError(
                "tuning experiments must be executed by TuningOrchestrator"
            )
        trials = self._build_trials(experiment)

        def build_run(
            trial: TrialSpec,
            scenario: ScenarioRef,
            repetition: int,
        ) -> RunSpec:
            return self._build_run(experiment, trial, scenario, repetition)

        expander = self._workflow_expanders[experiment.purpose]
        runs = tuple(expander(experiment, trials, build_run))
        self._validate_expansion(experiment, trials, runs)
        digest = _content_digest(
            {
                "experiment": experiment,
                "trials": trials,
                "runs": runs,
            }
        )
        return ExecutionPlan(
            experiment=experiment,
            trials=trials,
            runs=runs,
            compiled_at=datetime.now(timezone.utc),
            plan_digest=digest,
        )

    def validate(self, experiment: ExperimentSpec) -> None:
        """Validate experiment intent without compiling executable runs."""

        self._validate(experiment)

    def digest(self, experiment: ExperimentSpec) -> str:
        """Return a stable validated digest for standard or dynamic workflows."""

        self.validate(experiment)
        return _content_digest(experiment)

    def _validate(self, experiment: ExperimentSpec) -> None:
        if not experiment.experiment_id.strip():
            raise ValueError("experiment_id must not be empty")
        if not experiment.name.strip():
            raise ValueError("experiment name must not be empty")
        if not experiment.algorithms:
            raise ValueError("experiment must contain at least one algorithm")
        if not experiment.scenarios:
            raise ValueError("experiment must contain at least one scenario")
        if experiment.repetitions <= 0:
            raise ValueError("experiment repetitions must be positive")
        if not experiment.objective.components:
            raise ValueError("experiment objective must contain a component")
        if (
            experiment.objective.mode is ComparisonMode.SINGLE
            and len(experiment.objective.components) != 1
        ):
            raise ValueError("a single objective must contain exactly one component")
        ObjectiveEngine(experiment.objective)
        _validate_budget(experiment.budget, "experiment budget")
        if experiment.purpose is RunPurpose.TUNE:
            if experiment.tuning is None:
                raise ValueError("tuning experiments require a tuning definition")
            if len(experiment.algorithms) != 1:
                raise ValueError("tuning experiments require exactly one target algorithm")
            self._validate_tuning(experiment)
        elif experiment.tuning is not None:
            raise ValueError("only tuning experiments may contain a tuning definition")

        domain_manifest = self._registry.domain_manifest(
            experiment.domain.domain_id,
            experiment.domain.version,
        )
        validate_schema_value(
            experiment.domain.parameters,
            domain_manifest.parameter_schema,
            path="domain.parameters",
        )
        _validate_objective_metrics(
            experiment,
            domain_manifest.metric_schema,
        )
        scenario_ids = [scenario.scenario_id for scenario in experiment.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("scenario_id values must be unique within an experiment")
        for scenario in experiment.scenarios:
            if not scenario.scenario_id.strip():
                raise ValueError("scenario_id must not be empty")
            if not scenario.uri.strip():
                raise ValueError(f"scenario {scenario.scenario_id!r} has an empty uri")
            if not scenario.digest.strip():
                raise ValueError(
                    f"scenario {scenario.scenario_id!r} has an empty digest"
                )

        trial_ids: set[str] = set()
        for algorithm in experiment.algorithms:
            if (
                experiment.purpose is RunPurpose.TRAIN
                and algorithm.interface is not AlgorithmInterface.TRAINABLE
            ):
                raise ValueError(
                    "train experiments require the trainable interface"
                )
            effective_budget = algorithm.budget or experiment.budget
            _validate_budget(
                effective_budget,
                f"algorithm {algorithm.algorithm_id!r} budget",
            )
            if algorithm.interface is AlgorithmInterface.SEARCH_OPTIMIZER:
                raise ValueError(
                    "search_optimizer is an experiment-level controller and cannot "
                    "be placed in ExperimentSpec.algorithms"
                )
            manifest = self._registry.algorithm_manifest(
                algorithm.algorithm_id,
                algorithm.version,
            )
            validate_schema_value(
                algorithm.parameters,
                manifest.parameter_schema,
                path=f"algorithms.{algorithm.algorithm_id}.parameters",
            )
            if algorithm.interface not in manifest.interfaces:
                raise ValueError(
                    f"algorithm {algorithm.algorithm_id!r} version "
                    f"{algorithm.version!r} does not implement "
                    f"{algorithm.interface.value!r}"
                )
            if algorithm.interface is AlgorithmInterface.PLUGIN and manifest.runtime is None:
                raise ValueError(
                    "plugin algorithms require a runtime plugin reference"
                )
            if manifest.runtime is not None:
                runtime_manifest = self._registry.runtime_manifest(
                    manifest.runtime.runtime_id,
                    manifest.runtime.version,
                )
                if algorithm.interface not in runtime_manifest.interfaces:
                    raise ValueError(
                        f"runtime {manifest.runtime.runtime_id!r} does not support "
                        f"interface {algorithm.interface.value!r}"
                    )
            if (
                algorithm.interface
                in {AlgorithmInterface.BATCH, AlgorithmInterface.ITERATIVE}
                and ArtifactKind.SOLUTION not in manifest.output_artifact_kinds
            ):
                raise ValueError(
                    f"{algorithm.interface.value} algorithm manifests must declare "
                    "the solution output artifact kind"
                )
            if algorithm.interface is AlgorithmInterface.TRAINABLE:
                trained_kinds = {
                    ArtifactKind.MODEL,
                    ArtifactKind.PARAMETERS,
                    ArtifactKind.CHECKPOINT,
                }
                if experiment.purpose in {RunPurpose.TRAIN, RunPurpose.TUNE}:
                    if not trained_kinds.intersection(
                        manifest.output_artifact_kinds
                    ):
                        raise ValueError(
                            "trainable algorithm manifests must declare a model, "
                            "parameters, or checkpoint output"
                        )
                elif not algorithm.input_artifacts:
                    raise ValueError(
                        "frozen trainable evaluation requires input artifacts"
                    )
            if (
                manifest.supported_domains
                and experiment.domain.domain_id not in manifest.supported_domains
            ):
                raise ValueError(
                    f"algorithm {algorithm.algorithm_id!r} does not support domain "
                    f"{experiment.domain.domain_id!r}"
                )
            unsupported_inputs = tuple(
                artifact.kind
                for artifact in algorithm.input_artifacts
                if artifact.kind not in manifest.input_artifact_kinds
            )
            if unsupported_inputs:
                raise ValueError(
                    f"algorithm {algorithm.algorithm_id!r} does not accept input "
                    f"artifact kinds: {unsupported_inputs}"
                )
            minimum_inputs = manifest.minimum_input_artifacts.get(
                algorithm.interface,
                0,
            )
            if len(algorithm.input_artifacts) < minimum_inputs:
                raise ValueError(
                    f"algorithm {algorithm.algorithm_id!r} requires at least "
                    f"{minimum_inputs} input artifacts for "
                    f"{algorithm.interface.value!r} execution"
                )
            _validate_domain_capabilities(
                algorithm.interface,
                experiment.purpose,
                domain_manifest.capabilities,
            )
            trial_id = _stable_id(
                "trial",
                {
                    "experiment_id": experiment.experiment_id,
                    "algorithm": algorithm,
                    "objective": experiment.objective,
                    "budget": effective_budget,
                },
            )
            if trial_id in trial_ids:
                raise ValueError(
                    "algorithm variants must be unique within an experiment"
                )
            trial_ids.add(trial_id)

    def _validate_tuning(self, experiment: ExperimentSpec) -> None:
        tuning = experiment.tuning
        if tuning is None:
            raise ValueError("tuning definition is missing")
        if tuning.optimizer.interface is not AlgorithmInterface.SEARCH_OPTIMIZER:
            raise ValueError("tuning optimizer must use the search_optimizer interface")
        optimizer_manifest = self._registry.algorithm_manifest(
            tuning.optimizer.algorithm_id,
            tuning.optimizer.version,
        )
        validate_schema_value(
            tuning.optimizer.parameters,
            optimizer_manifest.parameter_schema,
            path="tuning.optimizer.parameters",
        )
        if AlgorithmInterface.SEARCH_OPTIMIZER not in optimizer_manifest.interfaces:
            raise ValueError("registered tuning optimizer lacks search_optimizer support")
        if (
            optimizer_manifest.supported_domains
            and experiment.domain.domain_id
            not in optimizer_manifest.supported_domains
        ):
            raise ValueError(
                "tuning optimizer does not support domain "
                f"{experiment.domain.domain_id!r}"
            )
        unsupported_optimizer_inputs = tuple(
            artifact.kind
            for artifact in tuning.optimizer.input_artifacts
            if artifact.kind not in optimizer_manifest.input_artifact_kinds
        )
        if unsupported_optimizer_inputs:
            raise ValueError(
                "tuning optimizer does not accept input artifact kinds: "
                f"{unsupported_optimizer_inputs}"
            )
        minimum_optimizer_inputs = optimizer_manifest.minimum_input_artifacts.get(
            AlgorithmInterface.SEARCH_OPTIMIZER,
            0,
        )
        if len(tuning.optimizer.input_artifacts) < minimum_optimizer_inputs:
            raise ValueError(
                "tuning optimizer does not provide its required input artifacts"
            )
        if ArtifactKind.CHECKPOINT not in optimizer_manifest.output_artifact_kinds:
            raise ValueError("search optimizer manifests must declare checkpoint output")
        _validate_budget(
            tuning.optimizer.budget
            or Budget(max_evaluations=tuning.max_trials),
            "tuning optimizer budget",
        )
        if tuning.max_trials <= 0:
            raise ValueError("tuning max_trials must be positive")
        if tuning.batch_size <= 0 or tuning.batch_size > tuning.max_trials:
            raise ValueError("tuning batch_size must be between one and max_trials")
        if not tuning.search_space:
            raise ValueError("tuning search_space must not be empty")
        for name, dimension in tuning.search_space.items():
            if not name.strip():
                raise ValueError("search dimension names must not be empty")
            _validate_search_dimension(name, dimension)

        tuning_ids = {scenario.scenario_id for scenario in experiment.scenarios}
        tuning_digests = {scenario.digest for scenario in experiment.scenarios}
        if any(
            str(scenario.metadata.get("split", "")).lower() == "benchmark"
            for scenario in experiment.scenarios
        ):
            raise ValueError(
                "sealed benchmark scenarios cannot participate in parameter selection"
            )
        target = experiment.algorithms[0]
        target_manifest = self._registry.algorithm_manifest(
            target.algorithm_id,
            target.version,
        )
        _validate_search_space_schema(
            tuning.search_space,
            target_manifest.parameter_schema,
        )
        if target.interface is AlgorithmInterface.TRAINABLE:
            if not tuning.training_scenarios:
                raise ValueError(
                    "trainable tuning requires independent training scenarios"
                )
            if len(tuning.training_scenarios) != 1:
                raise ValueError(
                    "trainable tuning requires exactly one training corpus "
                    "scenario; the corpus may contain multiple instances"
                )
            reusable_kinds = {
                ArtifactKind.MODEL,
                ArtifactKind.PARAMETERS,
                ArtifactKind.CHECKPOINT,
            }.intersection(target_manifest.output_artifact_kinds).intersection(
                target_manifest.input_artifact_kinds
            )
            if not reusable_kinds:
                raise ValueError(
                    "trainable tuning requires at least one trained artifact kind "
                    "declared as both output and input"
                )
        elif tuning.training_scenarios:
            raise ValueError(
                "training_scenarios are only valid for trainable tuning targets"
            )

        training_ids: set[str] = set()
        training_digests: set[str] = set()
        for scenario in tuning.training_scenarios:
            if not scenario.scenario_id.strip() or not scenario.uri.strip():
                raise ValueError("training scenarios require non-empty id and uri")
            if not scenario.digest.strip():
                raise ValueError("training scenarios require a digest")
            if str(scenario.metadata.get("split", "")).lower() == "benchmark":
                raise ValueError("sealed benchmark scenarios cannot be used for training")
            if scenario.scenario_id in training_ids:
                raise ValueError("training scenario IDs must be unique")
            if scenario.scenario_id in tuning_ids:
                raise ValueError(
                    "training scenario IDs must be disjoint from tuning scenarios"
                )
            if scenario.digest in tuning_digests:
                raise ValueError(
                    "training scenarios must be disjoint from tuning scenarios"
                )
            training_ids.add(scenario.scenario_id)
            training_digests.add(scenario.digest)

        if not tuning.benchmark_scenarios:
            raise ValueError("tuning requires sealed benchmark scenarios")
        benchmark_ids: set[str] = set()
        for scenario in tuning.benchmark_scenarios:
            if not scenario.scenario_id.strip() or not scenario.uri.strip():
                raise ValueError("benchmark scenarios require non-empty id and uri")
            if not scenario.digest.strip():
                raise ValueError("benchmark scenarios require a digest")
            if str(scenario.metadata.get("split", "")).lower() != "benchmark":
                raise ValueError(
                    "benchmark scenarios must declare metadata.split='benchmark'"
                )
            if scenario.scenario_id in benchmark_ids:
                raise ValueError("benchmark scenario IDs must be unique")
            if (
                scenario.scenario_id in tuning_ids
                or scenario.scenario_id in training_ids
            ):
                raise ValueError(
                    "benchmark scenario IDs must be disjoint from training and "
                    "tuning scenarios"
                )
            if scenario.digest in tuning_digests:
                raise ValueError(
                    "benchmark scenarios must be disjoint from tuning scenarios"
                )
            if scenario.digest in training_digests:
                raise ValueError(
                    "benchmark scenarios must be disjoint from training scenarios"
                )
            benchmark_ids.add(scenario.scenario_id)

    def _build_trials(
        self,
        experiment: ExperimentSpec,
    ) -> tuple[TrialSpec, ...]:
        return tuple(
            TrialSpec(
                trial_id=_stable_id(
                    "trial",
                    {
                        "experiment_id": experiment.experiment_id,
                        "algorithm": algorithm,
                        "objective": experiment.objective,
                        "budget": algorithm.budget or experiment.budget,
                    },
                ),
                experiment_id=experiment.experiment_id,
                algorithm=algorithm,
                objective=experiment.objective,
                budget=algorithm.budget or experiment.budget,
                metadata={"workflow": experiment.purpose.value},
            )
            for algorithm in experiment.algorithms
        )

    def _build_run(
        self,
        experiment: ExperimentSpec,
        trial: TrialSpec,
        scenario: ScenarioRef,
        repetition: int,
    ) -> RunSpec:
        seeds = _derive_run_seeds(experiment, trial.algorithm, scenario, repetition)
        run_material = {
            "trial_id": trial.trial_id,
            "experiment_id": experiment.experiment_id,
            "domain": experiment.domain,
            "algorithm": trial.algorithm,
            "scenario": scenario,
            "objective": experiment.objective,
            "purpose": experiment.purpose,
            "budget": trial.budget,
            "seeds": seeds,
            "repetition": repetition,
            "input_artifacts": trial.algorithm.input_artifacts,
        }
        return RunSpec(
            run_id=_stable_id("run", run_material),
            trial_id=trial.trial_id,
            experiment_id=experiment.experiment_id,
            domain=experiment.domain,
            algorithm=trial.algorithm,
            scenario=scenario,
            objective=experiment.objective,
            purpose=experiment.purpose,
            budget=trial.budget,
            seeds=seeds,
            repetition=repetition,
            input_artifacts=trial.algorithm.input_artifacts,
            metadata={"workflow": experiment.purpose.value},
        )

    @staticmethod
    def _validate_expansion(
        experiment: ExperimentSpec,
        trials: tuple[TrialSpec, ...],
        runs: tuple[RunSpec, ...],
    ) -> None:
        trial_ids = {trial.trial_id for trial in trials}
        run_ids = [run.run_id for run in runs]
        if not runs:
            raise ValueError("workflow expansion produced no runs")
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("workflow expansion produced duplicate run IDs")
        trial_by_id = {trial.trial_id: trial for trial in trials}
        scenarios_by_id = {
            scenario.scenario_id: scenario for scenario in experiment.scenarios
        }
        for run in runs:
            _validate_identifier(run.run_id, "run_id")
            if run.experiment_id != experiment.experiment_id:
                raise ValueError("workflow expansion returned a run for another experiment")
            if run.trial_id not in trial_ids:
                raise ValueError("workflow expansion returned a run for an unknown trial")
            trial = trial_by_id[run.trial_id]
            if run.algorithm != trial.algorithm:
                raise ValueError("workflow expansion changed the trial algorithm")
            if run.domain != experiment.domain:
                raise ValueError("workflow expansion changed the experiment domain")
            if run.objective != trial.objective:
                raise ValueError("workflow expansion changed the trial objective")
            if run.budget != trial.budget:
                raise ValueError("workflow expansion changed the trial budget")
            if run.purpose is not experiment.purpose:
                raise ValueError("workflow expansion changed the run purpose")
            if run.repetition < 0:
                raise ValueError("workflow expansion produced a negative repetition")
            scenario = scenarios_by_id.get(run.scenario.scenario_id)
            if scenario is None or run.scenario != scenario:
                raise ValueError("workflow expansion returned an unknown scenario")


def _expand_matrix(
    experiment: ExperimentSpec,
    trials: tuple[TrialSpec, ...],
    build_run: RunBuilder,
) -> tuple[RunSpec, ...]:
    """Default algorithm-variant × scenario × repetition expansion."""

    return tuple(
        build_run(trial, scenario, repetition)
        for trial in trials
        for scenario in experiment.scenarios
        for repetition in range(experiment.repetitions)
    )


def _validate_domain_capabilities(
    interface: AlgorithmInterface,
    purpose: RunPurpose,
    capabilities: frozenset[DomainCapability],
) -> None:
    if (
        interface is AlgorithmInterface.ONLINE
        and DomainCapability.ONLINE_EXECUTION not in capabilities
    ):
        raise ValueError("online algorithms require domain online_execution support")
    if (
        interface in {AlgorithmInterface.BATCH, AlgorithmInterface.ITERATIVE}
        and DomainCapability.BATCH_EVALUATION not in capabilities
    ):
        raise ValueError(
            f"{interface.value} algorithms require domain batch_evaluation support"
        )
    if (
        interface is AlgorithmInterface.TRAINABLE
        and DomainCapability.TRAINING_DATA not in capabilities
    ):
        raise ValueError("trainable algorithms require domain training_data support")
    if (
        purpose is RunPurpose.REPLAY
        and DomainCapability.DETERMINISTIC_REPLAY not in capabilities
    ):
        raise ValueError("replay runs require domain deterministic_replay support")


def _validate_objective_metrics(
    experiment: ExperimentSpec,
    metric_schema: Mapping[str, object],
) -> None:
    properties = metric_schema.get("properties")
    if not isinstance(properties, Mapping):
        return
    referenced = {
        *(component.metric for component in experiment.objective.components),
        *(constraint.metric for constraint in experiment.objective.constraints),
    }
    unknown = tuple(sorted(referenced - set(properties)))
    if unknown:
        raise ValueError(
            "objective references metrics not declared by the domain: "
            f"{unknown}"
        )


def _validate_budget(budget: Budget, label: str) -> None:
    positive_values = {
        "wall_time_seconds": budget.wall_time_seconds,
        "decision_time_seconds": budget.decision_time_seconds,
        "max_iterations": budget.max_iterations,
        "max_evaluations": budget.max_evaluations,
        "max_steps": budget.max_steps,
        "cpu_cores": budget.cpu_cores,
        "memory_bytes": budget.memory_bytes,
    }
    invalid = tuple(
        name
        for name, value in positive_values.items()
        if value is not None and value <= 0
    )
    if invalid:
        raise ValueError(f"{label} values must be positive: {invalid}")
    if budget.gpu_count is not None and budget.gpu_count < 0:
        raise ValueError(f"{label} gpu_count must be non-negative")


def _validate_search_dimension(
    name: str,
    dimension: SearchDimension,
) -> None:
    label = f"search dimension {name!r}"
    if dimension.kind is SearchDimensionKind.CATEGORICAL:
        if not dimension.choices:
            raise ValueError(f"{label} requires at least one choice")
        canonical_choices = tuple(
            _canonical_json(choice) for choice in dimension.choices
        )
        if len(canonical_choices) != len(set(canonical_choices)):
            raise ValueError(f"{label} choices must be unique")
        if any(
            value is not None
            for value in (dimension.low, dimension.high, dimension.step)
        ) or dimension.logarithmic:
            raise ValueError(
                f"{label} cannot combine choices with numeric bounds"
            )
        return

    if dimension.choices:
        raise ValueError(f"{label} cannot define categorical choices")
    if dimension.low is None or dimension.high is None:
        raise ValueError(f"{label} requires low and high bounds")
    if not math.isfinite(float(dimension.low)) or not math.isfinite(
        float(dimension.high)
    ):
        raise ValueError(f"{label} bounds must be finite")
    if dimension.low >= dimension.high:
        raise ValueError(f"{label} requires low < high")
    if dimension.step is not None:
        if not math.isfinite(float(dimension.step)) or dimension.step <= 0:
            raise ValueError(f"{label} step must be finite and positive")
    if dimension.logarithmic and dimension.low <= 0:
        raise ValueError(f"{label} logarithmic bounds must be positive")

    if dimension.kind is SearchDimensionKind.INTEGER:
        if type(dimension.low) is not int or type(dimension.high) is not int:
            raise TypeError(f"{label} requires integer bounds")
        if dimension.step is not None and type(dimension.step) is not int:
            raise TypeError(f"{label} requires an integer step")
        return
    if dimension.kind is not SearchDimensionKind.FLOAT:
        raise ValueError(f"unsupported search dimension kind: {dimension.kind}")


def _validate_search_space_schema(
    search_space: Mapping[str, SearchDimension],
    parameter_schema: Mapping[str, object],
) -> None:
    if not parameter_schema:
        return
    properties = parameter_schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise TypeError("trusted parameter schema properties must be an object")
    additional = parameter_schema.get("additionalProperties", True)
    for name, dimension in search_space.items():
        dimension_schema = properties.get(name)
        if dimension_schema is None:
            if additional is False:
                raise ValueError(
                    f"search dimension {name!r} is not an algorithm parameter"
                )
            if not isinstance(additional, Mapping):
                continue
            dimension_schema = additional
        if dimension.kind is SearchDimensionKind.CATEGORICAL:
            for index, choice in enumerate(dimension.choices):
                validate_schema_value(
                    choice,
                    dimension_schema,
                    path=f"tuning.search_space.{name}.choices[{index}]",
                )
            continue
        validate_schema_value(
            dimension.low,
            dimension_schema,
            path=f"tuning.search_space.{name}.low",
        )
        validate_schema_value(
            dimension.high,
            dimension_schema,
            path=f"tuning.search_space.{name}.high",
        )


def _validate_identifier(value: str, name: str) -> None:
    if value in {"", ".", ".."} or any(
        character
        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
        for character in value
    ):
        raise ValueError(
            f"{name} may contain only letters, digits, dash, underscore, and dot"
        )


def _derive_run_seeds(
    experiment: ExperimentSpec,
    algorithm: AlgorithmRef,
    scenario: ScenarioRef,
    repetition: int,
) -> RunSeeds:
    shared_scope = {
        "base_seed": experiment.base_seed,
        "experiment_id": experiment.experiment_id,
        "scenario_id": scenario.scenario_id,
        "scenario_digest": scenario.digest,
        "repetition": repetition,
    }
    return RunSeeds(
        algorithm=_seed_from(
            {
                **shared_scope,
                "stream": "algorithm",
                "algorithm": {
                    "algorithm_id": algorithm.algorithm_id,
                    "version": algorithm.version,
                    "interface": algorithm.interface,
                },
            }
        ),
        environment=_seed_from({**shared_scope, "stream": "environment"}),
        instance=_seed_from({**shared_scope, "stream": "instance"}),
        exogenous={},
    )


def _seed_from(material: object) -> int:
    digest = hashlib.sha256(_canonical_json(material).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF


def _stable_id(prefix: str, material: object) -> str:
    digest = hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def _content_digest(material: object) -> str:
    digest = hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _canonical_json(value: object) -> str:
    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonicalize(value: object) -> object:
    if isinstance(value, ArtifactRef):
        return {
            "digest": value.digest,
            "kind": value.kind.value,
            "media_type": value.media_type,
            "size_bytes": value.size_bytes,
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonicalize(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        canonical_items = [_canonicalize(item) for item in value]
        return sorted(
            canonical_items,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported deterministic content type: {type(value).__name__}")


def compile_experiment(
    experiment: ExperimentSpec,
    registry: PlatformRegistry = default_registry,
    workflow_expanders: Mapping[RunPurpose, WorkflowExpander] | None = None,
) -> ExecutionPlan:
    """Convenience entry point for compiling one experiment."""

    return ExperimentCompiler(registry, workflow_expanders).compile(experiment)
