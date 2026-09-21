"""Declarative experiment configuration loading.

Configuration files describe immutable experiment intent.  Executable
algorithm and domain factories are resolved only through ``PlatformRegistry``;
no import target is accepted from configuration.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import yaml

from .models import (
    Aggregation,
    AlgorithmInterface,
    AlgorithmRef,
    ArtifactKind,
    ArtifactRef,
    Budget,
    ComparisonMode,
    ConstraintOperator,
    ConstraintSpec,
    Direction,
    DomainRef,
    ExperimentSpec,
    ObjectiveComponent,
    ObjectiveSpec,
    RunPurpose,
    SearchDimension,
    SearchDimensionKind,
    ScenarioRef,
    TuningSpec,
)
from .serialization import to_jsonable


def load_experiment_spec(path: str | Path) -> ExperimentSpec:
    """Load one JSON or YAML experiment specification."""

    config_path = Path(path)
    suffix = config_path.suffix.lower()
    if suffix not in {".json", ".yaml", ".yml"}:
        raise ValueError("experiment configuration must be JSON or YAML")
    with config_path.open("r", encoding="utf-8") as stream:
        value = (
            yaml.safe_load(stream)
            if suffix in {".yaml", ".yml"}
            else json.load(stream)
        )
    return experiment_spec_from_mapping(value)


def experiment_spec_from_mapping(value: Mapping[str, object]) -> ExperimentSpec:
    """Parse the public mapping representation into immutable core models."""

    schema_version = value.get("schema_version", 1)
    if schema_version != 1:
        raise ValueError(f"unsupported experiment schema_version: {schema_version!r}")
    allowed_fields = {
        "schema_version",
        "experiment_id",
        "name",
        "purpose",
        "base_seed",
        "repetitions",
        "domain",
        "algorithms",
        "scenarios",
        "objective",
        "budget",
        "tuning",
        "metadata",
    }
    unknown_fields = tuple(sorted(set(value) - allowed_fields))
    if unknown_fields:
        raise ValueError(f"unknown experiment fields: {unknown_fields}")

    domain_value = _mapping(value["domain"])
    objective_value = _mapping(value["objective"])
    algorithms = tuple(
        _algorithm_ref(_mapping(item)) for item in _sequence(value["algorithms"])
    )
    scenarios = tuple(
        _scenario_ref(_mapping(item)) for item in _sequence(value["scenarios"])
    )
    tuning_value = value.get("tuning")

    return ExperimentSpec(
        experiment_id=_string(value["experiment_id"], "experiment_id"),
        name=_string(value["name"], "name"),
        domain=DomainRef(
            domain_id=_string(domain_value["id"], "domain.id"),
            version=_string(domain_value["version"], "domain.version"),
            parameters=dict(_mapping(domain_value.get("parameters", {}))),
        ),
        algorithms=algorithms,
        scenarios=scenarios,
        objective=_objective_spec(objective_value),
        purpose=RunPurpose(_string(value["purpose"], "purpose")),
        budget=_budget(_mapping(value.get("budget", {}))),
        repetitions=_integer(value.get("repetitions", 1), "repetitions"),
        base_seed=_integer(value.get("base_seed", 0), "base_seed"),
        tuning=(
            None
            if tuning_value is None
            else _tuning_spec(_mapping(tuning_value))
        ),
        metadata=dict(_mapping(value.get("metadata", {}))),
    )


def experiment_spec_to_mapping(spec: ExperimentSpec) -> dict[str, object]:
    """Serialize one experiment using the same public shape accepted by parser."""

    value: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": spec.experiment_id,
        "name": spec.name,
        "purpose": spec.purpose.value,
        "base_seed": spec.base_seed,
        "repetitions": spec.repetitions,
        "domain": {
            "id": spec.domain.domain_id,
            "version": spec.domain.version,
            "parameters": to_jsonable(spec.domain.parameters),
        },
        "algorithms": tuple(
            _algorithm_ref_to_mapping(algorithm)
            for algorithm in spec.algorithms
        ),
        "scenarios": tuple(
            _scenario_ref_to_mapping(scenario) for scenario in spec.scenarios
        ),
        "objective": {
            "mode": spec.objective.mode.value,
            "components": tuple(
                {
                    "name": component.name,
                    "metric": component.metric,
                    "direction": component.direction.value,
                    "weight": component.weight,
                    "aggregation": component.aggregation.value,
                }
                for component in spec.objective.components
            ),
            "constraints": tuple(
                {
                    "name": constraint.name,
                    "metric": constraint.metric,
                    "operator": constraint.operator.value,
                    "threshold": constraint.threshold,
                    "aggregation": constraint.aggregation.value,
                    "tolerance": constraint.tolerance,
                }
                for constraint in spec.objective.constraints
            ),
            "feasibility_first": spec.objective.feasibility_first,
        },
        "budget": to_jsonable(spec.budget),
        "metadata": to_jsonable(spec.metadata),
    }
    if spec.tuning is not None:
        value["tuning"] = {
            "optimizer": _algorithm_ref_to_mapping(spec.tuning.optimizer),
            "search_space": {
                name: {
                    "kind": dimension.kind.value,
                    "choices": to_jsonable(dimension.choices),
                    "low": dimension.low,
                    "high": dimension.high,
                    "step": dimension.step,
                    "logarithmic": dimension.logarithmic,
                }
                for name, dimension in spec.tuning.search_space.items()
            },
            "max_trials": spec.tuning.max_trials,
            "batch_size": spec.tuning.batch_size,
            "training_scenarios": tuple(
                _scenario_ref_to_mapping(scenario)
                for scenario in spec.tuning.training_scenarios
            ),
            "benchmark_scenarios": tuple(
                _scenario_ref_to_mapping(scenario)
                for scenario in spec.tuning.benchmark_scenarios
            ),
        }
    return value


def _algorithm_ref_to_mapping(reference: AlgorithmRef) -> dict[str, object]:
    return {
        "id": reference.algorithm_id,
        "version": reference.version,
        "interface": reference.interface.value,
        "budget": (
            None if reference.budget is None else to_jsonable(reference.budget)
        ),
        "parameters": to_jsonable(reference.parameters),
        "input_artifacts": tuple(
            to_jsonable(artifact) for artifact in reference.input_artifacts
        ),
    }


def _scenario_ref_to_mapping(reference: ScenarioRef) -> dict[str, object]:
    return {
        "id": reference.scenario_id,
        "uri": reference.uri,
        "digest": reference.digest,
        "metadata": to_jsonable(reference.metadata),
    }


def _algorithm_ref(value: Mapping[str, object]) -> AlgorithmRef:
    artifacts = tuple(
        _artifact_ref(_mapping(item))
        for item in _sequence(value.get("input_artifacts", ()))
    )
    return AlgorithmRef(
        algorithm_id=_string(value["id"], "algorithms[].id"),
        version=_string(value["version"], "algorithms[].version"),
        interface=AlgorithmInterface(
            _string(value["interface"], "algorithms[].interface")
        ),
        budget=(
            None
            if value.get("budget") is None
            else _budget(_mapping(value["budget"]))
        ),
        parameters=dict(_mapping(value.get("parameters", {}))),
        input_artifacts=artifacts,
    )


def _scenario_ref(value: Mapping[str, object]) -> ScenarioRef:
    return ScenarioRef(
        scenario_id=_string(value["id"], "scenarios[].id"),
        uri=_string(value["uri"], "scenarios[].uri"),
        digest=_string(value["digest"], "scenarios[].digest"),
        metadata=dict(_mapping(value.get("metadata", {}))),
    )


def _tuning_spec(value: Mapping[str, object]) -> TuningSpec:
    search_space = {
        name: _search_dimension(_mapping(dimension), name)
        for name, dimension in _mapping(value["search_space"]).items()
    }
    return TuningSpec(
        optimizer=_algorithm_ref(_mapping(value["optimizer"])),
        search_space=search_space,
        max_trials=_integer(value["max_trials"], "tuning.max_trials"),
        batch_size=_integer(value.get("batch_size", 1), "tuning.batch_size"),
        training_scenarios=tuple(
            _scenario_ref(_mapping(item))
            for item in _sequence(value.get("training_scenarios", ()))
        ),
        benchmark_scenarios=tuple(
            _scenario_ref(_mapping(item))
            for item in _sequence(value.get("benchmark_scenarios", ()))
        ),
    )


def _search_dimension(
    value: Mapping[str, object],
    name: str,
) -> SearchDimension:
    choices = tuple(_sequence(value.get("choices", ())))
    return SearchDimension(
        kind=SearchDimensionKind(
            _string(value["kind"], f"tuning.search_space.{name}.kind")
        ),
        choices=choices,
        low=_optional_search_number(
            value.get("low"), f"tuning.search_space.{name}.low"
        ),
        high=_optional_search_number(
            value.get("high"), f"tuning.search_space.{name}.high"
        ),
        step=_optional_search_number(
            value.get("step"), f"tuning.search_space.{name}.step"
        ),
        logarithmic=_boolean(
            value.get("logarithmic", False),
            f"tuning.search_space.{name}.logarithmic",
        ),
    )


def _objective_spec(value: Mapping[str, object]) -> ObjectiveSpec:
    components = tuple(
        ObjectiveComponent(
            name=_string(component["name"], "objective.components[].name"),
            metric=_string(
                component.get("metric", component["name"]),
                "objective.components[].metric",
            ),
            direction=Direction(
                _string(
                    component.get("direction", "minimize"),
                    "objective.components[].direction",
                )
            ),
            weight=_number(
                component.get("weight", 1.0),
                "objective.components[].weight",
            ),
            aggregation=Aggregation(
                _string(
                    component.get("aggregation", "mean"),
                    "objective.components[].aggregation",
                )
            ),
        )
        for component in (
            _mapping(item) for item in _sequence(value["components"])
        )
    )
    constraints = tuple(
        ConstraintSpec(
            metric=_string(constraint["metric"], "objective.constraints[].metric"),
            operator=ConstraintOperator(
                _string(
                    constraint["operator"],
                    "objective.constraints[].operator",
                )
            ),
            threshold=_number(
                constraint["threshold"],
                "objective.constraints[].threshold",
            ),
            aggregation=Aggregation(
                _string(
                    constraint.get("aggregation", "max"),
                    "objective.constraints[].aggregation",
                )
            ),
            tolerance=_number(
                constraint.get("tolerance", 0.0),
                "objective.constraints[].tolerance",
            ),
            name=(
                None
                if constraint.get("name") is None
                else _string(
                    constraint["name"],
                    "objective.constraints[].name",
                )
            ),
        )
        for constraint in (
            _mapping(item) for item in _sequence(value.get("constraints", ()))
        )
    )
    return ObjectiveSpec(
        mode=ComparisonMode(_string(value["mode"], "objective.mode")),
        components=components,
        constraints=constraints,
        feasibility_first=_boolean(
            value.get("feasibility_first", True),
            "objective.feasibility_first",
        ),
    )


def _budget(value: Mapping[str, object]) -> Budget:
    return Budget(
        wall_time_seconds=_optional_number(
            value.get("wall_time_seconds"), "budget.wall_time_seconds"
        ),
        decision_time_seconds=_optional_number(
            value.get("decision_time_seconds"), "budget.decision_time_seconds"
        ),
        max_iterations=_optional_integer(
            value.get("max_iterations"), "budget.max_iterations"
        ),
        max_evaluations=_optional_integer(
            value.get("max_evaluations"), "budget.max_evaluations"
        ),
        max_steps=_optional_integer(value.get("max_steps"), "budget.max_steps"),
        cpu_cores=_optional_number(value.get("cpu_cores"), "budget.cpu_cores"),
        gpu_count=_optional_integer(value.get("gpu_count"), "budget.gpu_count"),
        memory_bytes=_optional_integer(
            value.get("memory_bytes"), "budget.memory_bytes"
        ),
    )


def _artifact_ref(value: Mapping[str, object]) -> ArtifactRef:
    return ArtifactRef(
        digest=_string(value["digest"], "input_artifacts[].digest"),
        kind=ArtifactKind(_string(value["kind"], "input_artifacts[].kind")),
        media_type=_string(
            value["media_type"], "input_artifacts[].media_type"
        ),
        size_bytes=_integer(
            value["size_bytes"], "input_artifacts[].size_bytes"
        ),
        uri=_string(value["uri"], "input_artifacts[].uri"),
        metadata=dict(_mapping(value.get("metadata", {}))),
    )


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("configuration value must be an object")
    return value


def _sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError("configuration value must be an array")
    return tuple(value)


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{path} must be a string")
    return value


def _integer(value: object, path: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{path} must be an integer")
    return value


def _number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{path} must be a number")
    return float(value)


def _boolean(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{path} must be a boolean")
    return value


def _optional_number(value: object, path: str) -> float | None:
    return None if value is None else _number(value, path)


def _optional_integer(value: object, path: str) -> int | None:
    return None if value is None else _integer(value, path)


def _optional_search_number(value: object, path: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{path} must be a number")
    return value
