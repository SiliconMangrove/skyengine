"""Versioned manifests and explicit registration for DFJSP-T plugins."""

from __future__ import annotations

from experiment.algorithm_platform.models import (
    AlgorithmInterface,
    AlgorithmManifest,
    ArtifactKind,
    DomainCapability,
    DomainManifest,
    PLATFORM_PROTOCOL_VERSION,
    SchemaRef,
)
from experiment.algorithm_platform.registry import PlatformRegistry, default_registry

from .cp_sat import DFJSPTCPsatSolver
from .domain import DFJSPTDomainAdapter
from .ppo import DFJSPTPPOTrainable, FrozenDFJSPTPPOPolicy
from .rolling_ga import RollingHorizonGA
from .rules import DispatchRulePolicy


PLUGIN_VERSION = "1.0.0"
DOMAIN_ID = "dfjsp_t"


_COMMON_RULE_PROPERTIES = {
    "dispatch_batch_size": {
        "type": "integer",
        "minimum": 1,
        "default": 1,
    },
    "machine_load_weight": {
        "type": "number",
        "minimum": 0.0,
        "default": 1.0,
    },
    "transport_weight": {
        "type": "number",
        "minimum": 0.0,
        "default": 1.0,
    },
}


DFJSPT_DOMAIN_MANIFEST = DomainManifest(
    domain_id=DOMAIN_ID,
    name="动态柔性作业车间与运输",
    version=PLUGIN_VERSION,
    protocol_version=PLATFORM_PROTOCOL_VERSION,
    adapter_entrypoint=(
        "experiment.algorithm_platform_plugins.dfjsp_t.domain:"
        "DFJSPTDomainAdapter"
    ),
    capabilities=frozenset(
        {
            DomainCapability.ONLINE_EXECUTION,
            DomainCapability.BATCH_EVALUATION,
            DomainCapability.TRAINING_DATA,
            DomainCapability.SNAPSHOT_RESTORE,
            DomainCapability.DETERMINISTIC_REPLAY,
        }
    ),
    problem_schema=SchemaRef(
        name="dfjsp_t.instance",
        version="1",
        uri="dataset/dfjsp_t/validate_schema.py",
    ),
    observation_schema=SchemaRef(
        name="dfjsp_t.online_observation",
        version="2",
    ),
    action_schema=SchemaRef(
        name="dfjsp_t.dispatch_action",
        version="2",
    ),
    solution_schema=SchemaRef(
        name="dfjsp_t.online_dispatch_trace",
        version="1",
    ),
    metric_schema={
        "type": "object",
        "properties": {
            "C_max_E": {"type": "number", "minimum": 0.0},
            "C_max": {"type": "number", "minimum": 0.0},
            "unfinished_jobs": {"type": "number", "minimum": 0.0},
            "unfinished_urgent_jobs": {"type": "number", "minimum": 0.0},
            "success_rate": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "total_tardiness": {"type": "number", "minimum": 0.0},
            "weighted_tardiness": {"type": "number", "minimum": 0.0},
        },
    },
    parameter_schema={
        "type": "object",
        "properties": {
            "scenario_root": {"type": "string"},
            "route_solver": {"type": "string", "default": "astar"},
            "assigner": {"type": "string", "default": "nearest"},
            "validation_scenarios": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["scenario_id", "uri", "digest"],
                    "properties": {
                        "scenario_id": {"type": "string", "minLength": 1},
                        "uri": {"type": "string", "minLength": 1},
                        "digest": {"type": "string", "minLength": 1},
                        "metadata": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
                "default": [],
            },
        },
        "additionalProperties": False,
    },
    metadata={
        "formal_environment": "sky_executor.session.SimulationSession",
        "future_orders_visible": False,
        "urgent_priority_threshold": 200,
        "replay_state": "runtime_frame_and_state_hash",
        "snapshot_format": "deterministic_formal_action_history",
        "corpus_scenario": {
            "format": "jsonl",
            "digest": "file_sha256",
            "metadata": ["corpus", "split", "instance_ids"],
        },
    },
)


CP_SAT_MANIFEST = AlgorithmManifest(
    algorithm_id="cp_sat_reference",
    name="DFJSP-T CP-SAT 精确参考算法",
    version=PLUGIN_VERSION,
    protocol_version=PLATFORM_PROTOCOL_VERSION,
    interfaces=frozenset({AlgorithmInterface.BATCH}),
    entrypoints={
        AlgorithmInterface.BATCH: (
            "experiment.algorithm_platform_plugins.dfjsp_t.cp_sat:"
            "DFJSPTCPsatSolver"
        )
    },
    supported_domains=frozenset({DOMAIN_ID}),
    parameter_schema={
        "type": "object",
        "properties": {
            "time_limit_seconds": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "default": 300.0,
            },
            "num_workers": {
                "type": "integer",
                "minimum": 1,
                "default": 1,
            },
            "log_search_progress": {
                "type": "boolean",
                "default": False,
            },
        },
        "additionalProperties": False,
    },
    output_artifact_kinds=frozenset(
        {ArtifactKind.SOLUTION, ArtifactKind.REPORT}
    ),
    metadata={
        "family": "exact_reference",
        "solver": "OR-Tools CP-SAT",
        "model_scope": "nominal_dfjsp_t_with_aggregate_agv_capacity",
        "training_data_usage": "none",
    },
)


CTDE_PPO_MANIFEST = AlgorithmManifest(
    algorithm_id="ctde_ppo",
    name="DFJSP-T 分层 CTDE-PPO",
    version=PLUGIN_VERSION,
    protocol_version=PLATFORM_PROTOCOL_VERSION,
    interfaces=frozenset(
        {AlgorithmInterface.TRAINABLE, AlgorithmInterface.ONLINE}
    ),
    entrypoints={
        AlgorithmInterface.TRAINABLE: (
            "experiment.algorithm_platform_plugins.dfjsp_t.ppo:"
            "DFJSPTPPOTrainable"
        ),
        AlgorithmInterface.ONLINE: (
            "experiment.algorithm_platform_plugins.dfjsp_t.ppo:"
            "FrozenDFJSPTPPOPolicy"
        ),
    },
    supported_domains=frozenset({DOMAIN_ID}),
    parameter_schema={
        "type": "object",
        "properties": {
            "episodes": {"type": "integer", "minimum": 1, "default": 1000},
            "num_envs": {"type": "integer", "minimum": 1, "default": 4},
            "checkpoint_interval_steps": {"type": "integer", "minimum": 0, "default": 0},
            "max_steps": {"type": "integer", "minimum": 1, "default": 1000},
            "evaluation_episodes": {
                "type": "integer",
                "minimum": 1,
                "default": 1,
            },
            "evaluation_max_steps": {
                "type": "integer",
                "minimum": 1,
                "default": 1000,
            },
            "validation_interval": {
                "type": "integer",
                "minimum": 1,
                "default": 100,
            },
            "device": {"type": "string", "default": "auto"},
            "gpu_ids": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0},
                "default": [],
            },
            "distributed": {"type": "boolean", "default": False},
            "mapf_algorithm": {"type": "string", "default": "astar"},
            "node_dims": {
                "type": "object",
                "additionalProperties": {
                    "type": "integer",
                    "minimum": 1,
                },
            },
            "hidden_dim": {"type": "integer", "minimum": 16, "default": 128},
            "route_actions": {"type": "integer", "minimum": 1, "default": 5},
            "max_production_actions": {
                "type": "integer",
                "minimum": 1,
                "default": 32,
            },
            "max_logistics_actions": {
                "type": "integer",
                "minimum": 1,
                "default": 32,
            },
            "learning_rate": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "default": 0.0003,
            },
            "gamma": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 1.0,
            },
            "gae_lambda": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.95,
            },
            "clip_ratio": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "default": 0.2,
            },
            "ppo_epochs": {"type": "integer", "minimum": 1, "default": 4},
            "value_coef": {"type": "number", "minimum": 0.0, "default": 0.5},
            "entropy_coef": {"type": "number", "minimum": 0.0, "default": 0.01},
            "minibatch_size": {
                "type": "integer",
                "minimum": 1,
                "default": 64,
            },
            "reward_horizon": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "default": 1000.0,
            },
            "shaping_scale": {"type": "number", "default": 0.1},
        },
        "additionalProperties": False,
    },
    input_artifact_kinds=frozenset(
        {ArtifactKind.MODEL, ArtifactKind.CHECKPOINT}
    ),
    minimum_input_artifacts={AlgorithmInterface.ONLINE: 1},
    output_artifact_kinds=frozenset(
        {
            ArtifactKind.MODEL,
            ArtifactKind.CHECKPOINT,
            ArtifactKind.SOLUTION,
            ArtifactKind.REPORT,
        }
    ),
    metadata={
        "family": "reinforcement_learning",
        "trainer": "CTDE-PPO",
        "training_environment": "formal_headless",
        "frozen_online_inference": True,
        "benchmark_training": False,
    },
)


SPT_MANIFEST = AlgorithmManifest(
    algorithm_id="spt_rule",
    name="最短加工时间规则",
    version=PLUGIN_VERSION,
    protocol_version=PLATFORM_PROTOCOL_VERSION,
    interfaces=frozenset({AlgorithmInterface.ONLINE}),
    entrypoints={
        AlgorithmInterface.ONLINE: (
            "experiment.algorithm_platform_plugins.dfjsp_t.rules:"
            "DispatchRulePolicy"
        )
    },
    supported_domains=frozenset({DOMAIN_ID}),
    parameter_schema={
        "type": "object",
        "properties": _COMMON_RULE_PROPERTIES,
        "additionalProperties": False,
    },
    output_artifact_kinds=frozenset({ArtifactKind.SOLUTION}),
    metadata={
        "family": "dispatch_rule",
        "rule": "SPT",
        "training_data_usage": "none",
    },
)


EDD_MANIFEST = AlgorithmManifest(
    algorithm_id="edd_rule",
    name="最早交期规则",
    version=PLUGIN_VERSION,
    protocol_version=PLATFORM_PROTOCOL_VERSION,
    interfaces=frozenset({AlgorithmInterface.ONLINE}),
    entrypoints={
        AlgorithmInterface.ONLINE: (
            "experiment.algorithm_platform_plugins.dfjsp_t.rules:"
            "DispatchRulePolicy"
        )
    },
    supported_domains=frozenset({DOMAIN_ID}),
    parameter_schema={
        "type": "object",
        "properties": _COMMON_RULE_PROPERTIES,
        "additionalProperties": False,
    },
    output_artifact_kinds=frozenset({ArtifactKind.SOLUTION}),
    metadata={
        "family": "dispatch_rule",
        "rule": "EDD",
        "training_data_usage": "none",
    },
)


WEIGHTED_RULE_MANIFEST = AlgorithmManifest(
    algorithm_id="weighted_dispatch_rule",
    name="可调权重派工规则",
    version=PLUGIN_VERSION,
    protocol_version=PLATFORM_PROTOCOL_VERSION,
    interfaces=frozenset({AlgorithmInterface.ONLINE}),
    entrypoints={
        AlgorithmInterface.ONLINE: (
            "experiment.algorithm_platform_plugins.dfjsp_t.rules:"
            "DispatchRulePolicy"
        )
    },
    supported_domains=frozenset({DOMAIN_ID}),
    parameter_schema={
        "type": "object",
        "properties": {
            **_COMMON_RULE_PROPERTIES,
            "processing_time_weight": {"type": "number", "default": 1.0},
            "due_date_weight": {"type": "number", "default": 1.0},
            "remaining_work_weight": {"type": "number", "default": 0.0},
            "priority_weight": {"type": "number", "default": 0.0},
        },
        "additionalProperties": False,
    },
    output_artifact_kinds=frozenset({ArtifactKind.SOLUTION}),
    metadata={
        "family": "dispatch_rule",
        "rule": "WEIGHTED",
        "tunable": True,
        "training_data_usage": "none",
    },
)


ROLLING_GA_MANIFEST = AlgorithmManifest(
    algorithm_id="rolling_ga",
    name="滚动时域遗传调度算法",
    version=PLUGIN_VERSION,
    protocol_version=PLATFORM_PROTOCOL_VERSION,
    interfaces=frozenset({AlgorithmInterface.ONLINE}),
    entrypoints={
        AlgorithmInterface.ONLINE: (
            "experiment.algorithm_platform_plugins.dfjsp_t.rolling_ga:"
            "RollingHorizonGA"
        )
    },
    supported_domains=frozenset({DOMAIN_ID}),
    parameter_schema={
        "type": "object",
        "properties": {
            "population_size": {
                "type": "integer",
                "minimum": 4,
                "default": 48,
            },
            "generations": {
                "type": "integer",
                "minimum": 1,
                "default": 40,
            },
            "horizon_operations": {
                "type": "integer",
                "minimum": 1,
                "default": 32,
            },
            "lookahead_per_job": {
                "type": "integer",
                "minimum": 1,
                "default": 4,
            },
            "dispatch_batch_size": {
                "type": "integer",
                "minimum": 1,
                "default": 1,
            },
            "crossover_rate": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.85,
            },
            "mutation_rate": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.15,
            },
            "tournament_size": {
                "type": "integer",
                "minimum": 2,
                "default": 3,
            },
            "elite_size": {
                "type": "integer",
                "minimum": 1,
                "default": 2,
            },
        },
        "additionalProperties": False,
    },
    output_artifact_kinds=frozenset(
        {ArtifactKind.SOLUTION, ArtifactKind.REPORT}
    ),
    metadata={
        "family": "rolling_horizon_metaheuristic",
        "objective": ["C_max_E", "C_max", "total_tardiness"],
        "future_orders_visible": False,
        "training_data_usage": "none",
    },
)


def register_dfjsp_t_plugins(
    registry: PlatformRegistry = default_registry,
) -> None:
    """Register the formal domain and all built-in reference algorithms."""

    registry.register_domain(
        DFJSPT_DOMAIN_MANIFEST,
        lambda reference: DFJSPTDomainAdapter(reference),
    )
    registry.register_algorithm(
        SPT_MANIFEST,
        {
            AlgorithmInterface.ONLINE: lambda reference: DispatchRulePolicy(
                "SPT",
                reference.parameters,
            )
        },
    )
    registry.register_algorithm(
        EDD_MANIFEST,
        {
            AlgorithmInterface.ONLINE: lambda reference: DispatchRulePolicy(
                "EDD",
                reference.parameters,
            )
        },
    )
    registry.register_algorithm(
        WEIGHTED_RULE_MANIFEST,
        {
            AlgorithmInterface.ONLINE: lambda reference: DispatchRulePolicy(
                "WEIGHTED",
                reference.parameters,
            )
        },
    )
    registry.register_algorithm(
        ROLLING_GA_MANIFEST,
        {
            AlgorithmInterface.ONLINE: lambda reference: RollingHorizonGA(
                reference.parameters
            )
        },
    )
    registry.register_algorithm(
        CP_SAT_MANIFEST,
        {
            AlgorithmInterface.BATCH: lambda reference: DFJSPTCPsatSolver(
                reference.parameters
            )
        },
    )
    registry.register_algorithm(
        CTDE_PPO_MANIFEST,
        {
            AlgorithmInterface.TRAINABLE: (
                lambda reference: DFJSPTPPOTrainable(reference.parameters)
            ),
            AlgorithmInterface.ONLINE: (
                lambda reference: FrozenDFJSPTPPOPolicy(
                    reference.parameters
                )
            ),
        },
    )
