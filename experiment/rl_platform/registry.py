"""算法仓库注册表。

算法包可以在导入时调用 ``register_algorithm``，向 GUI 暴露自己的参数 schema。
平台不填充任何算法或超参数默认值。
"""

from __future__ import annotations

from typing import Any, Mapping

_ALGORITHMS: dict[str, dict[str, Any]] = {}
_DISCOVERED = False


def register_algorithm(name: str, target: str, hyperparameters: Mapping[str, Any] | None = None, **metadata: Any) -> None:
    _ALGORITHMS[target] = {"name": name, "target": target, "hyperparameters": dict(hyperparameters or {}), **metadata}


def list_algorithms() -> list[dict[str, Any]]:
    _discover()
    return list(_ALGORITHMS.values())


def get_algorithm(target: str) -> dict[str, Any] | None:
    _discover()
    return _ALGORITHMS.get(target)


def _discover() -> None:
    global _DISCOVERED
    if _DISCOVERED:
        return
    _DISCOVERED = True
    try:
        import importlib.metadata as metadata
        entries = metadata.entry_points()
        entries = entries.select(group="skyengine.algorithms") if hasattr(entries, "select") else entries.get("skyengine.algorithms", [])
        for entry in entries:
            entry.load()()
    except (ImportError, ModuleNotFoundError):
        pass
    try:
        import dfjsp_t_rl
        dfjsp_t_rl.register(register_algorithm)
    except ModuleNotFoundError:
        pass
