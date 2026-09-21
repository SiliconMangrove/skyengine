"""与具体算法无关的检查点保存。"""

from __future__ import annotations

import json
import random
from pathlib import Path


def save_checkpoint(path: str | Path, policy, trainer, metadata: dict | None = None) -> None:
    import torch
    try:
        import numpy as np
        numpy_state = np.random.get_state()
    except ImportError:
        numpy_state = None
    state = {
        "schema_version": 2,
        "policy_parameters": getattr(policy, "checkpoint_parameters", {}),
        "metadata": metadata or {},
        "policy": policy.state_dict() if hasattr(policy, "state_dict") else {},
        "trainer": trainer.state_dict() if hasattr(trainer, "state_dict") else {},
        "rng": {
            "python": random.getstate(),
            "numpy": numpy_state,
            "torch": torch.get_rng_state(),
            "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
        },
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    torch.save(state, temporary)
    temporary.replace(target)


def load_checkpoint(path: str | Path, policy, trainer) -> dict:
    import torch
    state = torch.load(Path(path), map_location="cpu", weights_only=False)
    if hasattr(policy, "load_state_dict"):
        policy.load_state_dict(state.get("policy", {}))
    if hasattr(trainer, "load_state_dict"):
        trainer.load_state_dict(state.get("trainer", {}))
    rng = state.get("rng", {})
    if rng.get("python") is not None:
        random.setstate(rng["python"])
    if rng.get("numpy") is not None:
        import numpy as np
        np.random.set_state(rng["numpy"])
    if rng.get("torch") is not None:
        torch.set_rng_state(rng["torch"])
    if rng.get("cuda") is not None and torch.cuda.is_available():
        torch.cuda.set_rng_state_all(rng["cuda"])
    return state.get("metadata", {})
