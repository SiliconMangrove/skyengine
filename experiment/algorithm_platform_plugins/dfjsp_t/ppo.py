"""Formal CTDE-PPO training and frozen online execution adapters."""

from __future__ import annotations

import io
import random
import time
from numbers import Real
from datetime import datetime, timezone
from typing import Mapping, Sequence

from experiment.algorithm_platform.models import (
    ArtifactKind,
    ArtifactRef,
    ComparisonMode,
    DecisionRequest,
    DecisionResponse,
    DecisionStatus,
    EventType,
    Feedback,
    MetricSet,
    RunContext,
    RunPurpose,
)
from experiment.algorithm_platform.objectives import ObjectiveEngine
from experiment.algorithm_platform.orchestrator import AlgorithmExecutionCancelled
from experiment.algorithm_platform.serialization import to_jsonable
from experiment.algorithm_platform.training_checkpoints import (
    checkpoint_directory, save_training_checkpoint, read_training_checkpoint,
)
from experiment.rl_platform import SkyEngineTrainingEnv
from experiment.rl_platform.parallel_rollout import ParallelRolloutCollector
from sky_executor.runtime_log import serialize_action

from .domain import DFJSPTDataSplit, DFJSPT_URGENT_PRIORITY


ALGORITHM_ID = "ctde_ppo"
CHECKPOINT_SCHEMA_VERSION = 3


class _FormalRolloutPolicy:
    """Keep unassigned transport work alive at the formal env boundary."""

    def __init__(self, policy):
        self._policy = policy

    @property
    def last_action_data(self):
        return self._policy.last_action_data

    def reset(self) -> None:
        self._policy.reset()

    def bootstrap_value(self, observation) -> float:
        return self._policy.bootstrap_value(observation)

    def act(self, observation, action_mask=None, deterministic=False):
        action = self._policy.act(
            observation,
            action_mask,
            deterministic=deterministic,
        )
        return action


class DFJSPTPPOTrainable:
    """Train CTDE-PPO in the formal headless SkyEngine environment."""

    def __init__(self, parameters: Mapping[str, object]):
        self._parameters = dict(parameters)
        self._policy = None
        self._trainer = None
        self._device = "cpu"
        self._policy_config: dict[str, object] = {}
        self._training_digests: frozenset[str] = frozenset()
        self._checkpoint_seeds: dict[str, int] = {}

    def _save_checkpoint(self, context: RunContext, name: str, completed: int, steps: int,
                         training_digests: tuple[str, ...], selection_split: str,
                         metrics: Mapping[str, float], selection, selection_key) -> None:
        import torch
        import numpy as np

        policy_parameters: dict[str, object] = {**self._policy_config, "device": "cpu"}
        metadata = {
            "algorithm_id": ALGORITHM_ID, "run_id": context.run.run_id,
            "execution_id": context.execution_id, "completed_episodes": completed,
            "total_steps": steps, "created_at": datetime.now(timezone.utc).isoformat(),
            "selection_split": selection_split,
            "selection_sort_key": None if selection_key is None else list(selection_key),
            "selection_objective_values": [] if selection is None else list(selection.objective_values),
            "parameters": dict(to_jsonable(self._parameters)),
            "training_scenario": {
                "id": context.run.scenario.scenario_id, "uri": context.run.scenario.uri,
                "digest": context.run.scenario.digest,
                "metadata": to_jsonable(context.run.scenario.metadata),
            },
        }
        payload = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION, "algorithm_id": ALGORITHM_ID,
            "policy_parameters": policy_parameters,
            "policy": {key: value.detach().cpu() for key, value in self._policy.state_dict().items()},
            "trainer": self._trainer.state_dict(), "completed_episodes": completed,
            "total_steps": steps, "training_scenario_digests": training_digests,
            "selection_split": selection_split, "selection_metrics": dict(metrics),
            "selection_objective_values": metadata["selection_objective_values"],
            "selection_sort_key": metadata["selection_sort_key"],
            "seeds": dict(self._checkpoint_seeds),
            "rng": {"python": random.getstate(), "numpy": np.random.get_state(),
                    "torch": torch.get_rng_state(),
                    "cuda": torch.cuda.get_rng_state_all() if self._device.startswith("cuda") else []},
        }
        record = save_training_checkpoint(checkpoint_directory(context.workspace_uri), name, payload, metadata)
        context.event_publisher.emit(EventType.TRAINING_PROGRESS, {
            "phase": "checkpoint_saved", "episode": completed, "total_steps": steps,
            "checkpoint": record, "message": f"已保存 {'最佳' if name == 'best' else '中间'} checkpoint，累计 {steps} 步",
        })

    def fit(
        self,
        training_data: DFJSPTDataSplit,
        validation_data: DFJSPTDataSplit | None,
        context: RunContext,
    ) -> tuple[ArtifactRef, ...]:
        if training_data.name != "training":
            raise ValueError("PPO fit requires a training data split")
        if not training_data.problems:
            raise ValueError("PPO training split must contain an instance")

        torch, policy_type, reward_type, trainer_type = _algorithm_types()
        _seed_training(torch, context.run.seeds.algorithm)
        self._device = _resolve_device(torch, self._parameters, context)
        inherited_training_digests: tuple[str, ...] = ()
        resume_payload = None
        if context.run.input_artifacts:
            (
                self._policy,
                selected_artifact,
                inherited_training_digests,
                self._policy_config,
            ) = _load_policy(
                context.run.input_artifacts,
                self._device,
                context,
            )
            if selected_artifact.kind is ArtifactKind.CHECKPOINT:
                resume_payload = torch.load(context.artifact_resolver.resolve(selected_artifact), map_location=self._device, weights_only=False)
        else:
            self._policy_config = _policy_parameters(
                self._parameters,
                self._device,
            )
            self._policy = policy_type(**self._policy_config)
        self._trainer = trainer_type(
            policy=self._policy,
            device=self._device,
            gpu_ids=list(self._parameters.get("gpu_ids", ())),
            distributed=bool(self._parameters.get("distributed", False)),
            **_trainer_parameters(self._parameters),
        )
        completed_episodes: int = 0
        total_steps: int = 0
        algorithm_seed: int = context.run.seeds.algorithm
        environment_seed: int = context.run.seeds.environment
        if resume_payload is not None:
            import numpy as np
            self._trainer.load_state_dict(resume_payload["trainer"])
            completed_episodes = int(resume_payload["completed_episodes"])
            total_steps = int(resume_payload["total_steps"])
            algorithm_seed = int(resume_payload["seeds"]["algorithm"])
            environment_seed = int(resume_payload["seeds"]["environment"])
            random.setstate(resume_payload["rng"]["python"])
            np.random.set_state(resume_payload["rng"]["numpy"])
            torch.set_rng_state(resume_payload["rng"]["torch"].cpu())
            if self._device.startswith("cuda") and resume_payload["rng"]["cuda"]:
                torch.cuda.set_rng_state_all([state.cpu() for state in resume_payload["rng"]["cuda"]])
            del resume_payload
        self._checkpoint_seeds = {"algorithm": algorithm_seed, "environment": environment_seed}

        episodes = int(self._parameters.get("episodes", 1000))
        for limit in (
            context.run.budget.max_iterations,
            context.run.budget.max_evaluations,
        ):
            if limit is not None:
                episodes = min(episodes, limit)
        if episodes <= 0:
            raise ValueError("episodes must be positive")
        if completed_episodes >= episodes:
            raise ValueError("训练总轮数必须大于 checkpoint 已完成轮数")
        max_steps = int(self._parameters.get("max_steps", 1000))
        if context.run.budget.max_steps is not None:
            max_steps = min(max_steps, context.run.budget.max_steps)
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")

        validation_interval = int(
            self._parameters.get("validation_interval", max(1, episodes // 10))
        )
        if validation_interval <= 0:
            raise ValueError("validation_interval must be positive")
        checkpoint_interval: int = int(self._parameters.get("checkpoint_interval_steps", 0))
        if checkpoint_interval < 0:
            raise ValueError("checkpoint_interval_steps must be non-negative")
        next_checkpoint: int = ((total_steps // checkpoint_interval) + 1) * checkpoint_interval if checkpoint_interval else 0
        training_digests: tuple[str, ...] = tuple(sorted({*inherited_training_digests, *(problem.scenario_digest for problem in training_data.problems)}))
        selection_data = validation_data or training_data
        started = time.monotonic()
        stats_history: list[dict[str, object]] = []
        validation_history: list[dict[str, object]] = []
        objective_engine = ObjectiveEngine(context.run.objective)
        best_key: tuple[float, ...] | None = None

        if completed_episodes:
            # The input model is also a candidate; never discard a better
            # checkpoint just because the first resumed update is worse.
            baseline_metrics = _evaluate_policy(self._policy, selection_data, self._parameters, context,
                                                seed_offset=1_000_000, seed_base=environment_seed)
            baseline = objective_engine.evaluate_metrics((baseline_metrics,))
            best_key = _objective_selection_key(objective_engine, baseline)
            self._save_checkpoint(context, "best", completed_episodes, total_steps,
                                  training_digests, selection_data.name, baseline_metrics, baseline, best_key)

        num_envs: int = min(int(self._parameters.get("num_envs", 4)), episodes - completed_episodes)
        if num_envs < 1:
            raise ValueError("num_envs must be positive")
        collector = ParallelRolloutCollector(
            num_envs,
            {"target": "dfjsp_t_rl.policy.DFJSPTPolicyPlugin", "kwargs": self._policy_config},
            {"target": "dfjsp_t_rl.reward.SMDPMakespanReward", "kwargs": _reward_parameters(self._parameters)},
            SkyEngineTrainingEnv,
            _FormalRolloutPolicy, _formal_metrics,
        )
        with collector:
            while completed_episodes < episodes:
                _raise_if_cancelled(context)
                wall_time = context.run.budget.wall_time_seconds
                if wall_time is not None and time.monotonic() - started >= wall_time:
                    break
                # Stop a batch at validation boundaries and at the total budget.
                batch_count: int = min(num_envs, episodes - completed_episodes)
                batch_count = min(batch_count, validation_interval - completed_episodes % validation_interval)
                first_episode: int = completed_episodes
                jobs: list[dict[str, object]] = []
                for episode in range(first_episode, first_episode + batch_count):
                    problem = training_data.problems[episode % len(training_data.problems)]
                    jobs.append({"episode": episode, "instance": problem.instance,
                                 "seed": _episode_seed(environment_seed, episode),
                                 "algorithm_seed": _episode_seed(algorithm_seed, episode)})
                    context.event_publisher.emit(
                        EventType.TRAINING_PROGRESS,
                        {"phase": "episode_started", "episode": episode + 1, "episodes": episodes,
                         "step": 0, "max_steps": max_steps, "num_envs": batch_count,
                         "device": self._device, "sampling_device": "cpu",
                         "message": f"开始第 {episode + 1}/{episodes} 轮，{batch_count} 个环境并行采样"},
                    )

                def publish_progress(episode: int, steps: int) -> None:
                    context.event_publisher.emit(
                        EventType.TRAINING_PROGRESS,
                        {"phase": "collecting", "episode": episode + 1, "episodes": episodes,
                         "step": steps, "max_steps": max_steps, "num_envs": batch_count,
                         "device": self._device, "sampling_device": "cpu",
                         "message": f"第 {episode + 1}/{episodes} 轮采样 {steps}/{max_steps} 步，{batch_count} 个环境并行"},
                    )

                collection_started: float = time.monotonic()
                trajectories = collector.collect(jobs, self._policy, max_steps,
                                                  publish_progress, lambda: _raise_if_cancelled(context))
                collection_seconds: float = time.monotonic() - collection_started
                transition_count: int = sum(int(item.metadata["simulation_steps"]) for item in trajectories)
                context.event_publisher.emit(
                    EventType.TRAINING_PROGRESS,
                    {"phase": "updating", "episode": first_episode + 1, "episodes": episodes,
                     "batch_end_episode": first_episode + batch_count, "num_envs": batch_count,
                     "steps": transition_count, "device": self._device,
                     "collection_seconds": collection_seconds,
                     "message": f"第 {first_episode + 1}—{first_episode + batch_count} 轮采样完成，合并 {transition_count} 步更新网络"},
                )
                _raise_if_cancelled(context)
                update_started: float = time.monotonic()
                update = self._trainer.update(trajectories)
                update_seconds: float = time.monotonic() - update_started
                for trajectory in trajectories:
                    episode = int(trajectory.metadata["episode"])
                    steps: int = int(trajectory.metadata["simulation_steps"])
                    episode_metrics = trajectory.metadata["metrics"]
                    stats_history.append(
                        {"episode": episode, "transitions": len(trajectory.transitions), "simulation_steps": steps, "num_envs": batch_count,
                         "collection_seconds": trajectory.metadata["collection_seconds"],
                         "batch_collection_seconds": collection_seconds, "batch_update_seconds": update_seconds,
                         "trainer": dict(to_jsonable(update)), "metrics": episode_metrics}
                    )
                    context.event_publisher.emit(
                        EventType.TRAINING_PROGRESS,
                        {"phase": "episode_completed", "episode": episode + 1, "episodes": episodes,
                         "steps": steps, "device": self._device, "num_envs": batch_count,
                         "collection_seconds": trajectory.metadata["collection_seconds"],
                         "batch_collection_seconds": collection_seconds, "batch_update_seconds": update_seconds,
                         "metrics": episode_metrics,
                         "message": f"第 {episode + 1}/{episodes} 轮完成，共采样 {steps} 步"},
                    )
                    trajectory.transitions.clear()
                trajectories.clear()
                del trajectory, trajectories
                self._policy.last_action_data = None
                completed_episodes += batch_count
                total_steps += transition_count
                if checkpoint_interval and total_steps >= next_checkpoint:
                    self._save_checkpoint(context, f"step-{total_steps:012d}", completed_episodes, total_steps,
                                          training_digests, "unevaluated", {}, None, None)
                    next_checkpoint = (total_steps // checkpoint_interval + 1) * checkpoint_interval
                _raise_if_cancelled(context)
                should_validate = best_key is None or completed_episodes % validation_interval == 0 or completed_episodes == episodes
                if should_validate:
                    validation_metrics = _evaluate_policy(
                        self._policy, selection_data, self._parameters, context,
                        seed_offset=1_000_000,
                        seed_base=environment_seed,
                    )
                    evaluation = objective_engine.evaluate_metrics((validation_metrics,))
                    sort_key = _objective_selection_key(objective_engine, evaluation)
                    validation_history.append(
                        {"episode": completed_episodes - 1, "metrics": validation_metrics,
                         "objective_values": evaluation.objective_values, "feasible": evaluation.feasible}
                    )
                    if best_key is None or sort_key < best_key:
                        best_key = sort_key
                        self._save_checkpoint(context, "best", completed_episodes, total_steps,
                                              training_digests, selection_data.name, validation_metrics, evaluation, sort_key)

        _raise_if_cancelled(context)
        if not stats_history:
            raise TimeoutError("training budget expired before the first episode")
        best_metadata, best_bytes = read_training_checkpoint(checkpoint_directory(context.workspace_uri) / "best.ckpt")
        best_checkpoint = torch.load(io.BytesIO(best_bytes), map_location=self._device, weights_only=False)
        self._policy.load_state_dict(best_checkpoint["policy"])
        self._trainer.load_state_dict(best_checkpoint["trainer"])
        _raise_if_cancelled(context)
        selection_metrics: dict[str, float] = dict(best_checkpoint["selection_metrics"])
        del best_checkpoint
        selection = objective_engine.evaluate_metrics((selection_metrics,))
        selection_key = _objective_selection_key(objective_engine, selection)
        policy_parameters = dict(self._policy_config)
        policy_parameters["device"] = "cpu"
        policy_state = {
            name: value.detach().cpu()
            for name, value in self._policy.state_dict().items()
        }
        provenance = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "algorithm_id": ALGORITHM_ID,
            "policy_parameters": policy_parameters,
            "policy": policy_state,
            "training_scenario_digests": tuple(
                sorted(
                    {
                        *inherited_training_digests,
                        *(
                            problem.scenario_digest
                            for problem in training_data.problems
                        ),
                    }
                )
            ),
            "selection_split": selection_data.name,
            "selection_metrics": selection_metrics,
            "selection_objective_values": selection.objective_values,
        }
        self._training_digests = frozenset(
            provenance["training_scenario_digests"]
        )

        model_buffer = io.BytesIO()
        torch.save(provenance, model_buffer)
        artifact_metadata = {
            "algorithm_id": ALGORITHM_ID,
            "format": "pytorch_state_dict",
            "selection_split": selection_data.name,
            "selection_sort_key": tuple(selection_key),
            "selection_objective_values": selection.objective_values,
        }
        model = context.artifact_publisher.publish_bytes(
            model_buffer.getvalue(),
            kind=ArtifactKind.MODEL,
            media_type="application/x-pytorch",
            metadata=artifact_metadata,
        )

        checkpoint = context.artifact_publisher.publish_bytes(
            best_bytes,
            kind=ArtifactKind.CHECKPOINT,
            media_type="application/x-pytorch",
            metadata={**artifact_metadata, **best_metadata},
        )
        report = context.artifact_publisher.publish_json(
            {
                "schema_version": 1,
                "algorithm_id": ALGORITHM_ID,
                "completed_episodes": completed_episodes,
                "total_steps": total_steps,
                "checkpoint_interval_steps": checkpoint_interval,
                "best_checkpoint_steps": best_metadata["total_steps"],
                "requested_episodes": episodes,
                "num_envs": num_envs,
                "sampling_device": "cpu",
                "max_steps": max_steps,
                "device": self._device,
                "gpu_ids": tuple(self._parameters.get("gpu_ids", ())),
                "distributed": bool(
                    self._parameters.get("distributed", False)
                ),
                "last_training_episode": stats_history[-1],
                "validation_history": tuple(validation_history),
                "selection_split": selection_data.name,
                "selection_metrics": selection_metrics,
                "selection_objective_values": selection.objective_values,
            },
            kind=ArtifactKind.REPORT,
            metadata={
                "algorithm_id": ALGORITHM_ID,
                "report_type": "training_summary",
            },
        )
        return model, checkpoint, report

    def load_artifacts(
        self,
        artifacts: Sequence[ArtifactRef],
        context: RunContext,
    ) -> None:
        torch, _, _, _ = _algorithm_types()
        self._device = _resolve_device(torch, self._parameters, context)
        self._policy, _, training_digests, self._policy_config = _load_policy(
            artifacts,
            self._device,
            context,
        )
        self._training_digests = frozenset(training_digests)

    def evaluate(
        self,
        evaluation_data: DFJSPTDataSplit,
        context: RunContext,
    ) -> MetricSet:
        _raise_if_cancelled(context)
        if self._policy is None:
            raise RuntimeError("PPO policy has not been trained or loaded")
        evaluation_digests = {
            problem.scenario_digest
            for problem in evaluation_data.problems
        }
        if (
            context.run.purpose is not RunPurpose.TRAIN
            and self._training_digests.intersection(evaluation_digests)
        ):
            raise ValueError(
                "frozen evaluation data overlaps the model training corpus"
            )
        return _evaluate_policy(
            self._policy,
            evaluation_data,
            self._parameters,
            context,
            seed_offset=3_000_000,
        )


class FrozenDFJSPTPPOPolicy:
    """Run a published PPO model through the online decision protocol."""

    def __init__(self, parameters: Mapping[str, object]):
        self._parameters = dict(parameters)
        self._context: RunContext | None = None
        self._policy = None
        self._trajectory: list[dict[str, object]] = []
        self._selected_artifact: ArtifactRef | None = None

    def initialize(self, context: RunContext) -> None:
        if not context.run.input_artifacts:
            raise ValueError("frozen PPO online execution requires a model")
        torch, _, _, _ = _algorithm_types()
        device = _resolve_device(torch, self._parameters, context)
        self._policy, self._selected_artifact, _, _ = _load_policy(
            context.run.input_artifacts,
            device,
            context,
        )
        self._policy.reset()
        self._context = context
        self._trajectory.clear()

    def decide(
        self,
        request: DecisionRequest[Mapping[str, object]],
    ) -> DecisionResponse[dict[str, object]]:
        policy_observation = {"planning_observation": request.observation["planning_observation"]}
        native_action = self._policy.act(
            policy_observation,
            deterministic=True,
        )
        action = {"native_action": serialize_action(native_action)}
        return DecisionResponse(
            request_id=request.request_id,
            status=DecisionStatus.FEASIBLE,
            action=action,
            diagnostics={
                "policy": ALGORITHM_ID,
                "deterministic": True,
                "model_digest": self._selected_artifact.digest,
                **self._policy.diagnostics,
            },
        )

    def observe(
        self,
        feedback: Feedback[
            Mapping[str, object],
            Mapping[str, object],
        ],
    ) -> None:
        self._trajectory.append(
            {
                "request_id": feedback.request_id,
                "accepted": feedback.accepted,
                "simulation_time": feedback.simulation_time,
                "state_version": feedback.state_version,
                "proposed_action": to_jsonable(feedback.proposed_action),
                "executed_action": to_jsonable(feedback.executed_action),
                "metrics": dict(feedback.metrics),
                "terminated": feedback.terminated,
                "rejection_reason": feedback.rejection_reason,
            }
        )

    def finalize(self) -> tuple[ArtifactRef, ...]:
        if self._context is None or self._selected_artifact is None:
            raise RuntimeError("frozen PPO policy was not initialized")
        trajectory = self._context.artifact_publisher.publish_json(
            {
                "schema_version": 1,
                "algorithm_id": ALGORITHM_ID,
                "model_digest": self._selected_artifact.digest,
                "deterministic": True,
                "steps": tuple(self._trajectory),
            },
            kind=ArtifactKind.SOLUTION,
            metadata={
                "algorithm_id": ALGORITHM_ID,
                "solution_type": "online_trajectory",
                "model_digest": self._selected_artifact.digest,
            },
        )
        return (trajectory,)


def _algorithm_types():
    import torch
    from dfjsp_t_rl.policy import DFJSPTPolicyPlugin
    from dfjsp_t_rl.reward import SMDPMakespanReward
    from dfjsp_t_rl.trainer import CTDEPPOTrainer

    return torch, DFJSPTPolicyPlugin, SMDPMakespanReward, CTDEPPOTrainer


def _seed_training(torch, seed: int) -> None:
    import numpy

    random.seed(seed)
    numpy.random.seed(seed % (2**32 - 1))
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _policy_parameters(
    parameters: Mapping[str, object],
    device: str,
) -> dict[str, object]:
    result = {
        "hidden_dim": int(parameters.get("hidden_dim", 128)),
        "max_production_actions": int(
            parameters.get("max_production_actions", 32)
        ),
        "max_logistics_actions": int(
            parameters.get("max_logistics_actions", 32)
        ),
        "device": device,
        "candidate_limit": int(parameters.get("candidate_limit", 24)),
        "scenario_count": int(parameters.get("scenario_count", 8)),
        "search_seconds": float(parameters.get("search_seconds", 0.0)),
        "routing_horizon": int(parameters.get("routing_horizon", 24)),
        "decision_interval": int(parameters.get("decision_interval", 5)),
        "inference_budget_ms": float(parameters.get("inference_budget_ms", 300.0)),
    }
    return result


def _reward_parameters(parameters: Mapping[str, object]) -> dict[str, object]:
    return {
        "horizon": float(parameters.get("reward_horizon", 1000.0)),
        "shaping_scale": float(parameters.get("shaping_scale", 0.1)),
    }


def _trainer_parameters(parameters: Mapping[str, object]) -> dict[str, object]:
    return {
        "learning_rate": float(parameters.get("learning_rate", 3e-4)),
        "gamma": float(parameters.get("gamma", 1.0)),
        "gae_lambda": float(parameters.get("gae_lambda", 0.95)),
        "clip_ratio": float(parameters.get("clip_ratio", 0.2)),
        "epochs": int(parameters.get("ppo_epochs", 4)),
        "value_coef": float(parameters.get("value_coef", 0.5)),
        "entropy_coef": float(parameters.get("entropy_coef", 0.01)),
        "minibatch_size": int(parameters.get("minibatch_size", 64)),
        "sequence_length": int(parameters.get("sequence_length", 32)),
    }


def _resolve_device(torch, parameters: Mapping[str, object], context: RunContext) -> str:
    requested = str(parameters.get("device", "auto"))
    gpu_ids = tuple(int(value) for value in parameters.get("gpu_ids", ()))
    gpu_budget = context.run.budget.gpu_count
    if gpu_budget is not None and len(gpu_ids) > gpu_budget:
        raise ValueError("gpu_ids exceed the run GPU budget")
    if requested == "auto":
        if gpu_budget == 0:
            return "cpu"
        if torch.cuda.is_available():
            return f"cuda:{gpu_ids[0] if gpu_ids else 0}"
        if gpu_budget is not None and gpu_budget > 0:
            raise RuntimeError("the run requests a GPU but CUDA is unavailable")
        return "cpu"
    if requested.startswith("cuda"):
        if gpu_budget == 0:
            raise ValueError("CUDA execution conflicts with a zero-GPU run budget")
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA execution was requested but CUDA is unavailable")
    if requested == "cuda" and gpu_ids:
        return f"cuda:{gpu_ids[0]}"
    return requested


def _load_policy(
    artifacts: Sequence[ArtifactRef],
    device: str,
    context: RunContext,
):
    torch, policy_type, _, _ = _algorithm_types()
    candidates = tuple(
        artifact
        for artifact in artifacts
        if artifact.kind in {ArtifactKind.MODEL, ArtifactKind.CHECKPOINT}
    )
    if not candidates:
        raise ValueError("PPO requires a MODEL or CHECKPOINT artifact")
    models = tuple(
        artifact
        for artifact in candidates
        if artifact.kind is ArtifactKind.MODEL
    )
    selected = min(models or candidates, key=_artifact_selection_key)
    payload = torch.load(
        context.artifact_resolver.resolve(selected),
        map_location=device,
        weights_only=False,
    )
    if payload["algorithm_id"] != ALGORITHM_ID:
        raise ValueError("artifact was produced by a different algorithm")
    if payload["schema_version"] != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError("控制器已升级为跨步规划，请使用第三版候选策略模型或重新训练")
    policy_parameters = dict(payload["policy_parameters"])
    policy_parameters["device"] = device
    policy = policy_type(**policy_parameters)
    policy.load_state_dict(payload["policy"])
    policy.model.eval()
    return (
        policy,
        selected,
        tuple(str(value) for value in payload["training_scenario_digests"]),
        dict(payload["policy_parameters"]),
    )


def _artifact_selection_key(artifact: ArtifactRef) -> tuple[object, ...]:
    value = artifact.metadata.get("selection_sort_key")
    if value is None:
        return (1, artifact.digest)
    return (0, *tuple(value), artifact.digest)


def _objective_selection_key(engine, evaluation) -> tuple[float, ...]:
    if engine.spec.mode is not ComparisonMode.PARETO:
        return engine.sort_key(evaluation)
    key: list[float] = []
    if engine.spec.feasibility_first:
        key.extend(
            (
                0.0 if evaluation.feasible else 1.0,
                float(evaluation.violated_constraint_count),
                evaluation.total_constraint_violation,
            )
        )
    for component, value in zip(
        engine.spec.components,
        evaluation.objective_values,
        strict=True,
    ):
        key.append(value if component.direction.value == "minimize" else -value)
    return tuple(key)


def _evaluate_policy(
    policy,
    data: DFJSPTDataSplit,
    parameters: Mapping[str, object],
    context: RunContext,
    *,
    seed_offset: int,
    seed_base: int | None = None,
) -> dict[str, float]:
    max_steps = int(parameters.get("evaluation_max_steps", parameters.get("max_steps", 1000)))
    if context.run.budget.max_steps is not None:
        max_steps = min(max_steps, context.run.budget.max_steps)
    repetitions = int(parameters.get("evaluation_episodes", 1))
    if max_steps <= 0 or repetitions <= 0:
        raise ValueError("evaluation_max_steps and evaluation_episodes must be positive")
    episode_metrics: list[dict[str, float]] = []
    for problem_index, problem in enumerate(data.problems):
        _raise_if_cancelled(context)
        for repetition in range(repetitions):
            _raise_if_cancelled(context)
            env = SkyEngineTrainingEnv(problem.instance)
            try:
                policy.reset()
                observation, _ = env.reset(
                    seed=_episode_seed(
                        context.run.seeds.environment if seed_base is None else seed_base,
                        seed_offset + problem_index * repetitions + repetition,
                    )
                )
                for _ in range(max_steps):
                    _raise_if_cancelled(context)
                    action = policy.act(observation, deterministic=True)
                    observation, _, terminated, truncated, _ = env.step(action)
                    if terminated or truncated:
                        break
                episode_metrics.append({**_formal_metrics(env), **policy.planning_metrics})
            finally:
                env.close()
    keys = sorted({key for metrics in episode_metrics for key in metrics})
    summary = {
        key: sum(metrics[key] for metrics in episode_metrics if key in metrics)
        / sum(1 for metrics in episode_metrics if key in metrics)
        for key in keys
    }
    successful = sorted(metrics["C_max"] for metrics in episode_metrics if metrics["success_rate"] == 1.0)
    summary["tail_completed_episodes"] = float(len(successful))
    if successful:
        summary["C_max_p95_completed"] = successful[min(len(successful) - 1, int(.95 * len(successful)))]
        tail = successful[min(len(successful) - 1, int(.95 * len(successful))):]
        summary["C_max_cvar95_completed"] = sum(tail) / len(tail)
    return summary


def _raise_if_cancelled(context: RunContext) -> None:
    if context.cancel_event.is_set():
        raise AlgorithmExecutionCancelled("PPO execution was cancelled")


def _formal_metrics(env: SkyEngineTrainingEnv) -> dict[str, float]:
    metrics = {
        str(name): float(value)
        for name, value in env.metrics().items()
        if isinstance(value, Real)
    }
    pogema = env.session.env.pogema_env
    jobs = list(getattr(pogema, "_all_jobs", pogema.jobs))
    completed = [job for job in jobs if job.is_completed]
    urgent = [
        job
        for job in jobs
        if int(getattr(job, "priority", 0)) >= DFJSPT_URGENT_PRIORITY
    ]
    completed_urgent = [job for job in urgent if job.is_completed]
    now = float(pogema.env_timeline)
    unfinished = len(jobs) - len(completed)
    unfinished_urgent = len(urgent) - len(completed_urgent)
    c_max = max(
        (float(job.completion_time) for job in completed),
        default=now,
    )
    c_max_e = max(
        (float(job.completion_time) for job in completed_urgent),
        default=0.0,
    )
    if unfinished:
        c_max = max(c_max, now)
    if unfinished_urgent:
        c_max_e = max(c_max_e, now)
    total_tardiness = 0.0
    weighted_tardiness = 0.0
    for job in jobs:
        due = getattr(job, "due", None)
        if due is None:
            continue
        completion = (
            float(job.completion_time)
            if job.is_completed
            else now
        )
        tardiness = max(0.0, completion - float(due))
        total_tardiness += tardiness
        weighted_tardiness += tardiness * max(
            1.0,
            float(getattr(job, "priority", 0)) / 100.0,
        )
    metrics.update(
        {
            "C_max": c_max,
            "C_max_E": c_max_e,
            "completed_jobs": float(len(completed)),
            "unfinished_jobs": float(unfinished),
            "urgent_jobs": float(len(urgent)),
            "unfinished_urgent_jobs": float(unfinished_urgent),
            "success_rate": 1.0 if not unfinished else 0.0,
            "job_completion_rate": (
                float(len(completed)) / len(jobs) if jobs else 1.0
            ),
            "total_tardiness": total_tardiness,
            "weighted_tardiness": weighted_tardiness,
        }
    )
    return metrics


def _episode_seed(base: int, offset: int) -> int:
    return (int(base) + int(offset)) % 2_147_483_647


__all__ = [
    "ALGORITHM_ID",
    "DFJSPTPPOTrainable",
    "FrozenDFJSPTPPOPolicy",
]
