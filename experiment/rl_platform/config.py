"""训练配置和插件动态加载。"""

from __future__ import annotations

import importlib
import inspect
import json
import os
from pathlib import Path
from typing import Any


def resolve_device(value: str | None = None) -> str:
    """Resolve the platform device hint without choosing an algorithm."""
    requested = str(value or "auto").strip().lower()
    if requested != "auto":
        return requested
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def configure_acceleration(device: str) -> None:
    """Enable safe framework-level GPU acceleration before plugins are built."""
    if not device.startswith("cuda"):
        return
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError(f"配置要求使用 {device}，但当前 worker 没有可用 CUDA")
    torch.set_float32_matmul_precision("high")
    torch.backends.cudnn.benchmark = True


def load_training_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        if path.suffix.lower() in {".yaml", ".yml"}:
            import yaml
            data = yaml.safe_load(handle)
        else:
            data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("训练配置必须是对象")
    return data


def _resolve_object(target: str) -> Any:
    """加载 ``package.module:Object`` 或 ``package.module.Object``，不调用对象。"""
    if ":" in target:
        module_name, object_name = target.split(":", 1)
    else:
        module_name, object_name = target.rsplit(".", 1)
    return getattr(importlib.import_module(module_name), object_name)


def load_object(target: str, **kwargs: Any) -> Any:
    obj = _resolve_object(target)
    return obj(**kwargs) if kwargs else obj()


def load_callable(target: str) -> Any:
    """Resolve a function/class without invoking it."""
    return _resolve_object(target)


def build_plugins(config: dict[str, Any]) -> tuple[Any, Any, Any]:
    """按配置构造 policy/reward/trainer；缺少任一插件时直接报错。"""
    missing = [name for name in ("policy", "reward", "trainer") if not isinstance(config.get(name), dict)]
    if missing:
        raise ValueError(f"训练配置缺少插件段: {missing}")
    policy_section, reward_section, trainer_section = (config[name] for name in ("policy", "reward", "trainer"))
    if not policy_section.get("target"):
        raise ValueError("policy.target 不能为空")
    if not reward_section.get("target"):
        raise ValueError("reward.target 不能为空")
    if not trainer_section.get("target"):
        raise ValueError("trainer.target 不能为空")
    runtime = config.get("training") or {}
    gpu_ids = runtime.get("gpu_ids")
    if gpu_ids and "CUDA_VISIBLE_DEVICES" not in os.environ:
        os.environ["CUDA_VISIBLE_DEVICES"] = ",".join(str(item) for item in gpu_ids) if isinstance(gpu_ids, (list, tuple)) else str(gpu_ids)
    device = resolve_device(runtime.get("device"))
    configure_acceleration(device)
    distributed = bool(runtime.get("distributed", False))

    def inject_runtime(target: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        resolved = _resolve_object(target)
        signature = inspect.signature(resolved)
        accepts_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())
        for name, value in (("device", device), ("gpu_ids", gpu_ids), ("distributed", distributed)):
            if value is None or name in kwargs:
                continue
            if accepts_kwargs or name in signature.parameters:
                kwargs[name] = value
        return kwargs

    policy_kwargs = inject_runtime(policy_section["target"], dict(policy_section.get("kwargs") or {}))
    policy = load_object(policy_section["target"], **policy_kwargs)
    reward = load_object(reward_section["target"], **(reward_section.get("kwargs") or {}))
    trainer_kwargs = dict(trainer_section.get("kwargs") or {})
    trainer_kwargs.setdefault("policy", policy)
    # The GUI edits values in the top-level training section. Forward only
    # trainer-owned keys; unrelated episode/environment settings stay out.
    training = config.get("training") or {}
    for key in ("learning_rate", "gamma", "gae_lambda", "clip_ratio", "epochs", "value_coef", "entropy_coef", "minibatch_size"):
        if key in training:
            trainer_kwargs.setdefault(key, training[key])
    trainer_kwargs = inject_runtime(trainer_section["target"], trainer_kwargs)
    # A trainer may intentionally expose no constructor (the minimal example
    # does this), so filter platform-injected values against its signature.
    trainer_type = _resolve_object(trainer_section["target"])
    signature = inspect.signature(trainer_type)
    accepts_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())
    if not accepts_kwargs:
        trainer_kwargs = {key: value for key, value in trainer_kwargs.items() if key in signature.parameters}
    trainer = trainer_type(**trainer_kwargs)
    return policy, reward, trainer
