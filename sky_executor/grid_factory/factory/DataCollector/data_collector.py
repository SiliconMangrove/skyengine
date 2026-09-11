"""
Data collection utilities for GridFactory / JSSP environments.

This module provides a small, flexible API to collect instances (adjacency, features,
candidate mask) from different environment types (L2D JSSP envs or GridFactoryEnv/Pogema).

Collected instances are saved as a numpy .npy file containing a list of (adj, fea) tuples
so they are compatible with the original L2D `DataGen` format where `dataset.append((adj, fea))`.

Usage example:

from sky_executor.grid_factory.factory.DataCollector.data_collector import Collector

collector = Collector(env_factory=my_env_factory)
collector.generate(n_instances=100, out_path='generatedData15_15_Seed200.npy', seed=200)

The Collector will try to read (adj, fea, candidate, mask) from env.reset(data) or
construct a simple adjacency/feature representation if the env exposes `jobs` objects.

Note: this is intentionally defensive. Adapt `extract_from_obs` if your env uses a different schema.
"""

from __future__ import annotations

import os
import json
import typing
from typing import Callable, Any, List, Tuple, Optional
import numpy as np
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class Collector:
    def __init__(self, env_factory: Callable[[], Any], seed: Optional[int] = None):
        """Create a Collector.

        Args:
            env_factory: a callable that returns a fresh env instance when called (no args),
                         or a class (then Collector will call it with no args).
            seed: optional seed used for deterministic generation (passed to env.reset when supported)
        """
        self.env_factory = env_factory
        self.seed = seed

    def _make_env(self):
        env = self.env_factory()
        return env

    def extract_from_obs(self, obs: dict) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray], Optional[np.ndarray]]:
        """Try to extract (adj, fea, candidate, mask) from an observation dict.

        Returns:
            adj: adjacency matrix (numpy array) or None
            fea: feature array (numpy array) or None
            candidate: candidate mask (numpy array) or None
            mask: optional mask (numpy array) or None
        """
        # Direct keys used by L2D implementations
        # Support multiple possible layouts to be robust.
        adj = fea = candidate = mask = None

        # If observation contains direct arrays
        if isinstance(obs, dict):
            # L2D style: obs could be (adj, fea, candidate, mask) returned directly as tuple
            if all(k in obs for k in ("adj", "fea")):
                adj = np.array(obs.get("adj"))
                fea = np.array(obs.get("fea"))
                candidate = np.array(obs.get("candidate")) if obs.get("candidate") is not None else None
                mask = np.array(obs.get("mask")) if obs.get("mask") is not None else None
                return adj, fea, candidate, mask

            # GridFactoryEnv pack_output: obs may be nested with 'task_observation' or 'job_observation'
            # Try common nested keys
            for key in ("task_observation", "job_observation", "agent_observation", "observation"):
                if key in obs:
                    return self.extract_from_obs(obs[key])

            # If obs contains 'jobs' as list of objects, attempt to build adjacency and features
            if "jobs" in obs and isinstance(obs["jobs"], (list, tuple)):
                jobs = obs["jobs"]
                try:
                    # Try to build a simple adjacency (disjunctive graph by operation order)
                    # and a features matrix containing processing times (if available) and machine id placeholder.
                    # This is a best-effort fallback and may need adaptation for your Job/Operation objects.
                    nodes = []
                    for job in jobs:
                        # Expect job.operations or job.ops
                        ops = getattr(job, "operations", None) or getattr(job, "ops", None) or []
                        for op in ops:
                            nodes.append(op)
                    n = len(nodes)
                    if n == 0:
                        raise ValueError("no operations found in jobs objects")
                    adj = np.zeros((n, n), dtype=np.int8)
                    fea = np.zeros((n, 2), dtype=np.int32)
                    # assign indices and fill features
                    for i, op in enumerate(nodes):
                        duration = getattr(op, "proc_time", None) or getattr(op, "duration", None) or 1
                        machine = getattr(op, "machine_id", None) or getattr(op, "machine", None) or -1
                        fea[i, 0] = int(duration)
                        fea[i, 1] = int(machine if machine is not None else -1)

                    # Build precedence constraints for ops within the same job (chain)
                    idx = 0
                    for job in jobs:
                        ops = getattr(job, "operations", None) or getattr(job, "ops", None) or []
                        for k in range(len(ops) - 1):
                            adj[idx + k, idx + k + 1] = 1
                        idx += len(ops)
                    return adj, fea, candidate, mask
                except Exception:
                    logger.debug("fallback jobs -> adj/fea conversion failed", exc_info=True)

        # If nothing matched, raise
        raise ValueError("Could not extract adj/fea from observation. Please adapt extract_from_obs to your env schema.")

    def collect_instance(self, env: Any, seed: Optional[int] = None) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray], Optional[np.ndarray]]:
        """Collect a single instance by resetting env with an optional seed and returning (adj, fea[, candidate, mask]).
        The exact reset signature may vary; Collector will try common patterns.
        """
        if seed is None:
            seed = self.seed
        # Try env.reset(seed=seed) or env.reset() depending on signature
        try:
            # Some envs return (obs, info)
            ret = env.reset(seed=seed)
        except TypeError:
            # Fallback no-arg reset
            ret = env.reset()
        except Exception:
            # If reset fails, re-create env and reset
            env = self._make_env()
            ret = env.reset(seed=seed) if seed is not None else env.reset()

        # normalize ret to observation dict
        obs = None
        if isinstance(ret, tuple) and len(ret) >= 1:
            obs = ret[0]
        else:
            obs = ret

        # If env expects an instance input on reset (like L2D JSSP), we expect the env_factory to support that
        # Collector currently assumes env.reset returns an observation describing a fresh instance
        return self.extract_from_obs(obs)

    def generate(self, n_instances: int, out_path: str, seed: Optional[int] = None, overwrite: bool = False) -> List[Tuple[np.ndarray, np.ndarray]]:
        """Generate and save `n_instances` problem instances to `out_path`.

        Each saved item is a tuple (adj, fea). The saved file will be a numpy array with dtype=object
        (pickled) to preserve variable-size matrices when needed, matching the L2D `DataGen` style.

        Args:
            n_instances: number of instances to collect
            out_path: output .npy filename
            seed: base seed (instances will use seed+i)
            overwrite: whether to overwrite existing file

        Returns:
            list of collected tuples (adj, fea)
        """
        if os.path.exists(out_path) and not overwrite:
            logger.info(f"File {out_path} already exists. Set overwrite=True to replace.")
            data = list(np.load(out_path, allow_pickle=True))
            return data

        env = self._make_env()
        data: List[Tuple[np.ndarray, np.ndarray]] = []
        base_seed = seed if seed is not None else self.seed if self.seed is not None else 0
        for i in range(n_instances):
            s = base_seed + i
            try:
                adj, fea, _, _ = self.collect_instance(env, seed=s)
                data.append((adj, fea))
                logger.info(f"Collected instance {i+1}/{n_instances}: adj.shape={getattr(adj, 'shape', None)}, fea.shape={getattr(fea, 'shape', None)}")
            except Exception as e:
                logger.warning(f"Failed to collect instance {i+1} with seed {s}: {e}")
                # attempt to recreate env and continue
                env = self._make_env()
                continue

        # Save with allow_pickle so original L2D loader can read it
        np.save(out_path, np.array(data, dtype=object), allow_pickle=True)
        logger.info(f"Saved {len(data)} instances to {out_path}")
        return data


# Small helper: example env factory for GridFactoryEnv
def make_grid_factory_env_factory(grid_factory_class, **kwargs) -> Callable[[], Any]:
    def factory():
        return grid_factory_class(**kwargs)

    return factory
