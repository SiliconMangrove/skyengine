"""
Joint JobShop + AGV data collector

This script provides utilities to collect joint dataset episodes by running an expert oracle
(Coordinator-based, duel_solver predictor, or greedy heuristics) in the GridFactoryEnv and
recording state-action trajectories in JSONL files.

Outputs per episode (JSONL):
- episode_id, seed, meta
- steps: list of step dicts containing timestamp t, jobs, machines, agvs, map (summary), actions
- episode_stats: makespan, throughput, conflicts, total_transport_time

Usage (example):
    python -m dataset.joint_data_collector --n 10 --out ./data/episodes --mode coordinator

The collector is intentionally flexible: pass an expert_factory callable that returns an object
with a .decide(obs) -> actions method (like Coordinator), or use built-in oracles.
"""

import json
import uuid
import os
from pathlib import Path
from typing import Callable, Optional, Any, Dict
from datetime import datetime
import time
import random

# Local imports (project)
try:
    from sky_executor.grid_factory.factory.grid_factory_env import GridFactoryEnv
    from sky_executor.grid_factory.factory.Benchmark import AlgorithmConfig
    from sky_executor.grid_factory.factory.Component.Coordinator.coordinator import Coordinator
except Exception:
    # Allow import failure at edit-time; runtime requires project in PYTHONPATH
    GridFactoryEnv = None
    Coordinator = None


class EpisodeRecorder:
    def __init__(self, out_dir: str):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def write_episode(self, episode: Dict[str, Any], filename: Optional[str] = None):
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"episode_{timestamp}_{episode['episode_id']}.jsonl"
        path = self.out_dir / filename
        # JSONL single-line episode record
        with open(path, "w", encoding="utf-8") as f:
            json.dump(episode, f, ensure_ascii=False)
            f.write("\n")
        return path


class ExpertOracle:
    """Wrapper for different expert oracle types."""

    def __init__(self, mode: str = "coordinator", expert_instance: Optional[Any] = None, duel_predictor: Optional[Any] = None):
        """
        mode: 'coordinator' | 'greedy' | 'duel_predictor'
        expert_instance: if provided, used as the solver (expected to have decide(obs) method)
        duel_predictor: a model object from duel_solver to estimate transport times (optional)
        """
        self.mode = mode
        self.expert = expert_instance
        self.duel_predictor = duel_predictor

    def decide(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        if self.mode == "coordinator" and self.expert is not None:
            return self.expert.decide(obs)
        elif self.mode == "greedy":
            # Simple greedy fallback: delegate to Coordinator-like if available
            if self.expert is not None:
                return self.expert.decide(obs)
            # If no Coordinator available, return empty actions (user should provide env-specific)
            return {"job_actions": [], "agent_actions": {}, "assign_actions": {}}
        elif self.mode == "duel_predictor" and self.duel_predictor is not None:
            # For duel_predictor, we assume duel_predictor has a method predict_times(system_map, candidates)
            # This is a placeholder: actual implementation must extract features from obs, call the predictor,
            # and craft actions (assignments + agent moves) based on predicted times.
            return self._duel_predictor_policy(obs)
        else:
            # Default: empty actions
            return {"job_actions": [], "agent_actions": {}, "assign_actions": {}}

    def _duel_predictor_policy(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        # Placeholder implementation: user should implement feature extraction
        # Attempt to call duel_predictor on a per-candidate basis
        try:
            system_map = obs.get("map", None)
            job_obs = obs.get("job_observation", {})
            # Build simple actions by delegating to underlying Coordinator when possible
            if self.expert is not None:
                return self.expert.decide(obs)
        except Exception:
            pass
        return {"job_actions": [], "agent_actions": {}, "assign_actions": {}}


def _serialize_state(obs: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize observation for storage; keep sizes reasonable (summaries)"""
    # This function should be adapted to the exact obs schema of your GridFactoryEnv
    out = {}
    out['t'] = obs.get('t', None) if isinstance(obs, dict) else None
    # jobs
    jobs = []
    for j in obs.get('job_observation', {}).get('jobs', []) if obs.get('job_observation') else []:
        jobs.append({
            'job_id': j.get('job_id'),
            'op_id': j.get('op_id'),
            'status': j.get('status'),
            'remaining_time': j.get('remaining_time'),
            'location': j.get('location'),
        })
    out['jobs'] = jobs

    # machines
    machines = []
    for m in obs.get('task_observation', {}).get('machines', []) if obs.get('task_observation') else []:
        machines.append({
            'machine_id': m.get('machine_id'),
            'status': m.get('status'),
            'queue_len': len(m.get('queue', [])) if m.get('queue') is not None else 0,
        })
    out['machines'] = machines

    # agvs
    agvs = []
    for a in obs.get('agent_observation', {}).get('agents', []) if obs.get('agent_observation') else []:
        agvs.append({
            'agv_id': a.get('id'),
            'pos': a.get('pos'),
            'task': a.get('task'),
            'battery': a.get('battery', None),
        })
    out['agvs'] = agvs

    # map summary
    if 'map' in obs and obs.get('map') is not None:
        mp = obs.get('map')
        out['map'] = {
            'size': mp.get('size') if isinstance(mp, dict) else None,
            'channels': mp.get('channels') if isinstance(mp, dict) else None,
        }
    else:
        out['map'] = None

    return out


def run_episode(env: Any, oracle: ExpertOracle, max_steps: int = 500, record_states: bool = True) -> Dict[str, Any]:
    """Run single episode with oracle, record state-action trajectory and episode stats."""
    episode_id = str(uuid.uuid4())[:8]
    seed = getattr(env, 'seed', None)

    obs, info = env.reset()
    t = 0
    steps = []

    for t in range(max_steps):
        # record obs
        serialized = _serialize_state(obs) if record_states else None

        # oracle returns action dict
        actions = oracle.decide(obs)

        # step environment
        obs, rewards, terminations, truncations, infos = env.step(actions)

        step_rec = {
            't': t,
            'obs': serialized,
            'actions': actions,
            'rewards': rewards,
        }
        steps.append(step_rec)

        # termination check
        if (isinstance(terminations, dict) and all(terminations.values())) or (isinstance(truncations, dict) and all(truncations.values())):
            break

    # Gather episode stats from env.pogema_env if available
    ep_stats = {}
    try:
        pog = getattr(env, 'pogema_env', None)
        if pog is not None:
            ep_stats['makespan'] = max(getattr(j, 'completion_time', 0) or 0 for j in getattr(pog, 'jobs', []) or [])
            ep_stats['throughput'] = sum(1 for j in getattr(pog, 'jobs', []) if getattr(j, 'completion_time', 0) > 0)
    except Exception:
        pass

    episode = {
        'episode_id': episode_id,
        'seed': seed,
        'created_at': datetime.now().isoformat(),
        'steps': steps,
        'episode_stats': ep_stats,
    }
    return episode


def collect_dataset(env_factory: Callable[[], Any], expert_factory: Callable[[], Any], out_dir: str, n_episodes: int = 10, seeds: Optional[list] = None, max_steps: int = 500):
    """Collect multiple episodes and save to out_dir."""
    recorder = EpisodeRecorder(out_dir)

    if seeds is None:
        seeds = [random.randint(0, 2**31 - 1) for _ in range(n_episodes)]
    else:
        # pad or truncate
        if len(seeds) < n_episodes:
            seeds = seeds + [seeds[-1]] * (n_episodes - len(seeds))
        seeds = seeds[:n_episodes]

    for i in range(n_episodes):
        seed = seeds[i]
        # build env with seed via env_factory if it accepts seed param or set attribute later
        env = env_factory()
        # attempt to set seed on env
        try:
            if hasattr(env, 'seed'):
                env.seed = seed
        except Exception:
            pass

        # build expert instance
        expert_inst = expert_factory()
        oracle = ExpertOracle(mode='coordinator', expert_instance=expert_inst)

        episode = run_episode(env, oracle, max_steps=max_steps)
        path = recorder.write_episode(episode, filename=f"episode_{i+1}_seed{seed}.jsonl")
        print(f"Saved episode {i+1}/{n_episodes} -> {path}")

    print(f"Finished collecting {n_episodes} episodes into {out_dir}")


# Simple CLI
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--n', type=int, default=5, help='Number of episodes')
    parser.add_argument('--out', type=str, default='./dataset_episodes', help='Output directory')
    parser.add_argument('--mode', type=str, default='coordinator', choices=['coordinator', 'greedy', 'duel_predictor'], help='Expert mode')
    parser.add_argument('--max_steps', type=int, default=500)
    parser.add_argument('--seed', type=int, default=None)
    args = parser.parse_args()

    # Simple env_factory using GridFactoryEnv default constructor
    def env_factory():
        if GridFactoryEnv is None:
            raise RuntimeError('GridFactoryEnv not found; ensure project is importable')
        return GridFactoryEnv()

    def expert_factory():
        if args.mode == 'coordinator':
            if Coordinator is None:
                raise RuntimeError('Coordinator not importable')
            return Coordinator()
        elif args.mode == 'greedy':
            if Coordinator is None:
                raise RuntimeError('Coordinator not importable')
            return Coordinator()  # use Coordinator as greedy fallback
        elif args.mode == 'duel_predictor':
            # attempt to import duel predictor model
            try:
                from sky_executor.grid_factory.factory.Component.JobSolver.duel_solver.solver_v1 import AdvancedAGVTimePredictor
                # instantiate with default dims (user should adapt)
                model = AdvancedAGVTimePredictor()
                return model
            except Exception as e:
                raise RuntimeError(f'Failed to import duel predictor: {e}')

    seeds = [args.seed] * args.n if args.seed is not None else None
    collect_dataset(env_factory=env_factory, expert_factory=expert_factory, out_dir=args.out, n_episodes=args.n, seeds=seeds, max_steps=args.max_steps)
