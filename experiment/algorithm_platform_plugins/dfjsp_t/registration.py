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

from .domain import DFJSPTDomainAdapter
from .ppo import DFJSPTPPOTrainable, FrozenDFJSPTPPOPolicy
from .research_baseline import MemeticPIBTPolicy


PLUGIN_VERSION = "1.0.0"
DOMAIN_ID = "dfjsp_t"


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
            "episode_reward": {"type": "number"},
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
            "route_solver": {"type": "string", "default": "astar", "description": "仅为只输出生产派工的算法补充路由；完整原生动作直接执行"},
            "assigner": {"type": "string", "default": "nearest", "description": "仅为只输出生产派工的算法补充车辆分配"},
            "agent_observation_type": {"type": "string", "enum": ["default", "MAPF", "POMAPF"], "default": "default", "description": "车辆观测格式，与路由器选择独立；联合算法使用 planning_observation"},
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


CTDE_PPO_MANIFEST = AlgorithmManifest(
    algorithm_id="ctde_ppo",
    name="DFJSP-T 图策略与滚动联合调度",
    version="0.3.0",
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
            "dynamic_sampling": {"type": "boolean", "default": False},
            "evaluation_num_envs": {"type": "integer", "minimum": 1, "default": 2},
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
            "hidden_dim": {"type": "integer", "minimum": 16, "default": 128},
            "candidate_limit": {"type": "integer", "minimum": 6, "default": 24},
            "scenario_count": {"type": "integer", "minimum": 1, "default": 8},
            "search_seconds": {"type": "number", "minimum": 0, "default": 0.0},
            "routing_horizon": {"type": "integer", "minimum": 2, "default": 24},
            "decision_interval": {"type": "integer", "minimum": 1, "default": 5},
            "inference_budget_ms": {"type": "number", "minimum": 50, "maximum": 300, "default": 300.0},
            "sequence_length": {"type": "integer", "minimum": 1, "default": 32},
            "graph_batch_size": {"type": "integer", "minimum": 1, "default": 16},
            "input_cache_mb": {"type": "integer", "minimum": 0, "default": 256},
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
            "gamma": {"type": "number", "const": 1.0, "default": 1.0},
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
        "checkpoint_schema_version": 4,
        "action_distribution": "categorical_over_distinct_executable_transactions",
        "transport_execution": "committed_tasks_with_pibt",
    },
)


MEMETIC_PIBT_MANIFEST = AlgorithmManifest(
    algorithm_id="memetic_pibt",
    name="联合调度 MA + PIBT（有限缓冲）",
    version=PLUGIN_VERSION,
    protocol_version=PLATFORM_PROTOCOL_VERSION,
    interfaces=frozenset({AlgorithmInterface.ONLINE}),
    entrypoints={AlgorithmInterface.ONLINE: (
        "experiment.algorithm_platform_plugins.dfjsp_t.research_baseline:MemeticPIBTPolicy"
    )},
    supported_domains=frozenset({DOMAIN_ID}),
    parameter_schema={
        "type": "object",
        "properties": {
            "population_size": {"type": "integer", "minimum": 4, "default": 48},
            "generations": {"type": "integer", "minimum": 1, "default": 20},
            "local_search_steps": {"type": "integer", "minimum": 0, "default": 12},
            "horizon_operations": {"type": "integer", "minimum": 1, "default": 32},
            "lookahead_per_job": {"type": "integer", "minimum": 1, "default": 4},
            "dispatch_batch_size": {"type": "integer", "minimum": 1, "default": 4},
            "replan_interval": {"type": "integer", "minimum": 1, "default": 8},
        },
        "additionalProperties": False,
    },
    output_artifact_kinds=frozenset({ArtifactKind.REPORT}),
    metadata={
        "family": "research_adapted_joint_baseline",
        "objective": ["predicted_makespan", "predicted_flow_sum"],
        "future_orders_visible": False,
        "training_data_usage": "none",
        "native_action": True,
        "references": ["https://doi.org/10.20965/jaciii.2022.p0974", "https://kei18.github.io/pibt2/"],
        "adaptations": ["rolling visible horizon", "buffer safety admission", "factory handling and PIBT"],
    },
)


def register_dfjsp_t_plugins(
    registry: PlatformRegistry = default_registry,
) -> None:
    """Register the formal domain, MA + PIBT baseline and CTDE-PPO."""

    registry.register_domain(
        DFJSPT_DOMAIN_MANIFEST,
        lambda reference: DFJSPTDomainAdapter(reference),
    )
    registry.register_algorithm(
        MEMETIC_PIBT_MANIFEST,
        {AlgorithmInterface.ONLINE: lambda reference: MemeticPIBTPolicy(reference.parameters)},
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
