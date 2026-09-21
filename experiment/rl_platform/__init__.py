"""可插拔的 SkyEngine 强化学习训练平台。"""

from pathlib import Path
import sys

# Algorithm repositories live beside ``skyengine`` in the development tree and
# under ``/app`` in the container. Resolve both layouts so CLI, GUI schema
# loading, and worker subprocesses use the same plugin target.
_module_path = Path(__file__).resolve()
_algorithm_roots = (
    _module_path.parents[2] / "skyengine-DFJSPT",
    _module_path.parents[3] / "skyengine-DFJSPT",
)
for _algorithm_root in _algorithm_roots:
    if _algorithm_root.is_dir() and str(_algorithm_root) not in sys.path:
        sys.path.insert(0, str(_algorithm_root))
        break

from .api import ActionSpec, DFJSPTState, TransitionInfo, Transition, Trajectory, PolicyPlugin, RewardPlugin, PathPlannerPlugin, TrainerPlugin
from .config import load_training_config, load_object, load_callable
from .dataset import JSONLDataset
from .env_adapter import SkyEngineTrainingEnv
from .vector_env import VectorEnv, SubprocessVectorEnv
from .rollout import RolloutCollector
from .registry import register_algorithm, list_algorithms

__all__ = [
    "ActionSpec", "DFJSPTState", "TransitionInfo", "Transition", "Trajectory", "PolicyPlugin", "RewardPlugin",
    "PathPlannerPlugin", "TrainerPlugin", "load_training_config", "load_object", "load_callable", "JSONLDataset",
    "SkyEngineTrainingEnv", "VectorEnv", "SubprocessVectorEnv", "RolloutCollector",
    "register_algorithm", "list_algorithms",
]
