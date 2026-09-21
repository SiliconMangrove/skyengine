"""训练平台的最小插件接口。

平台不规定算法、网络结构或奖励权重。第三方实现只需实现这里的接口，
即可复用数据加载、并行环境、轨迹和检查点设施。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ActionSpec:
    """描述环境动作的版本化约定。"""

    production_fields: tuple[str, ...] = ("job_id", "op_id", "machine_id")
    logistics_fields: tuple[str, ...] = ("task_id", "agv_id")
    route_fields: tuple[str, ...] = ("agv_id", "path")
    schema_version: int = 1


@dataclass(frozen=True)
class DFJSPTState:
    """Versioned graph state exchanged by the formal DFJSP-T adapter.

    The platform keeps this container algorithm agnostic.  Algorithm
    repositories may convert the arrays to tensors, numpy values, or another
    representation without making the environment depend on a trainer.
    """

    nodes: Mapping[str, Any]
    edges: Mapping[str, Any]
    action_masks: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    coupling: Mapping[str, float] = field(default_factory=dict)
    coupling_vector: Any = None
    events: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    metrics: Mapping[str, float] = field(default_factory=dict)
    timeline: float = 0.0
    schema_version: int = 1

    def as_observation(self) -> dict[str, Any]:
        return {
            "state_schema_version": self.schema_version,
            "nodes": self.nodes,
            "edges": self.edges,
            "action_masks": self.action_masks,
            "metadata": dict(self.metadata),
            "events": list(self.events),
            "coupling": dict(self.coupling),
            "coupling_vector": self.coupling_vector,
            "metrics": dict(self.metrics),
            "timeline": self.timeline,
        }


@dataclass(frozen=True)
class TransitionInfo:
    """Common metadata supplied to reward and trainer plugins."""

    before_metrics: Mapping[str, float] = field(default_factory=dict)
    after_metrics: Mapping[str, float] = field(default_factory=dict)
    events: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    delta_t: float = 0.0
    termination_reason: str | None = None
    seed: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Transition:
    observation: Any
    action: Any
    reward: Any
    next_observation: Any
    terminated: Any
    truncated: Any
    info: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Trajectory:
    transitions: list[Transition]
    episode_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "metadata": self.metadata,
            "transitions": [t.__dict__ for t in self.transitions],
        }


class PolicyPlugin(ABC):
    """策略插件；可以是神经网络、规则、搜索器或混合策略。"""

    @abstractmethod
    def act(self, observation: Any, action_mask: Any, deterministic: bool = False) -> Any:
        raise NotImplementedError


class RewardPlugin(ABC):
    """奖励插件，不包含任何平台默认权重。"""

    @abstractmethod
    def compute(self, transition: Transition) -> Any:
        raise NotImplementedError


class PathPlannerPlugin(ABC):
    """可替换的低层运输路径规划器。"""

    @abstractmethod
    def plan(self, observation: Any, transport_task: Any) -> Any:
        raise NotImplementedError


class TrainerPlugin(ABC):
    """训练更新插件，支持 on-policy、off-policy 或自定义更新。"""

    @abstractmethod
    def update(self, trajectory_batch: Sequence[Trajectory]) -> Mapping[str, Any]:
        raise NotImplementedError

    @classmethod
    def hyperparameter_schema(cls) -> Mapping[str, Any]:
        """算法仓库可覆盖此方法注册 GUI 参数；平台只读取，不提供默认参数。"""
        return {}

    def state_dict(self) -> Mapping[str, Any]:
        return {}

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        del state
