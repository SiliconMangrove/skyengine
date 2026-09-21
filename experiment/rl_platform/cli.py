"""通用训练平台命令行入口。"""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import build_plugins, load_object, load_callable, load_training_config
from .dataset import JSONLDataset
from .env_adapter import SkyEngineTrainingEnv
from .rollout import RolloutCollector
from .checkpoints import load_checkpoint, save_checkpoint
from .parallel_rollout import ParallelRolloutCollector
from functools import partial


def _make_env(config: dict, instance: dict):
    environment = config.get("environment", {})
    backend = str(environment.get("backend", "formal_headless"))
    if backend not in {"formal_headless", "formal"}:
        raise ValueError("训练平台只允许使用 formal_headless 正式工厂环境")
    return SkyEngineTrainingEnv(instance, mapf_algorithm=str(environment.get("mapf_algorithm", "astar")))


def train(config_path: str) -> None:
    config = load_training_config(config_path)
    policy, reward, trainer = build_plugins(config)
    dataset = JSONLDataset(config["dataset"]["path"])
    max_steps = int(config.get("environment", {}).get("max_steps", 1000))
    episodes = int(config.get("training", {}).get("episodes", 1))
    instances = list(dataset)
    if not instances:
        raise ValueError(f"训练数据集为空: {dataset.path}")
    num_envs: int = min(int(config.get("environment", {}).get("num_envs", 4)), episodes)
    if num_envs < 1:
        raise ValueError("num_envs and episodes must be positive")
    collector = ParallelRolloutCollector(
        num_envs,
        {"target": config["policy"]["target"], "kwargs": config["policy"].get("kwargs", {})},
        {"target": config["reward"]["target"], "kwargs": config["reward"].get("kwargs", {})},
        partial(_make_env, config), None, SkyEngineTrainingEnv.metrics,
    )
    checkpoint_config = config.get("checkpoint") or {}
    resume = checkpoint_config.get("resume")
    if resume:
        load_checkpoint(resume, policy, trainer)
    best_value = float("inf")
    with collector:
        for first_episode in range(0, episodes, num_envs):
            count: int = min(num_envs, episodes - first_episode)
            jobs: list[dict] = []
            for episode in range(first_episode, first_episode + count):
                instance = instances[episode % len(instances)]
                seed: int = (int(instance.get("seed", 0)) + episode) % 2_147_483_647
                jobs.append({"episode": episode, "instance": instance, "seed": seed,
                             "algorithm_seed": (int(config.get("training", {}).get("seed", 0)) + episode) % 2_147_483_647})
            print({"episode": first_episode, "status": "collecting", "num_envs": count}, flush=True)
            trajectories = collector.collect(
                jobs, policy, max_steps,
                lambda episode, steps: print({"episode": episode, "status": "collecting", "steps": steps}, flush=True),
                lambda: None,
            )
            metrics = trainer.update(trajectories)
            for trajectory in trajectories:
                trajectory.transitions.clear()
            trajectories.clear()
            del trajectory, trajectories
            completed: int = first_episode + count
            result = {"completed_episodes": completed, "num_envs": count, **dict(metrics)}
            checkpoint_path = checkpoint_config.get("path")
            interval: int = max(1, int(checkpoint_config.get("interval", 1)))
            if checkpoint_path and (completed // interval > first_episode // interval or completed == episodes):
                save_checkpoint(checkpoint_path, policy, trainer, {"episode": completed - 1, "config": config})
                result["checkpoint"] = checkpoint_path
            score = float(metrics.get("loss", float("inf")))
            best_path = checkpoint_config.get("best_path")
            if best_path and score < best_value:
                best_value = score
                save_checkpoint(best_path, policy, trainer, {"episode": completed - 1, "config": config, "best_score": score})
                result["best_checkpoint"] = best_path
            print(result)


def evaluate(config_path: str, limit: int | None = None) -> None:
    config = load_training_config(config_path)
    policy, reward, _trainer = build_plugins(config)
    dataset = JSONLDataset(config["dataset"]["path"])
    max_steps = int(config.get("environment", {}).get("max_steps", 1000))
    collector = RolloutCollector(policy, reward)
    for episode, instance in enumerate(dataset):
        if limit is not None and episode >= limit:
            break
        trajectory = collector.collect(_make_env(config, instance), max_steps, deterministic=True, episode_id=str(episode))
        print({"episode": episode, "steps": len(trajectory.transitions), "instance_id": instance.get("instance_id")})


def replay(config_path: str, limit: int | None = None) -> None:
    """使用用户提供的正式环境工厂回放策略，不启动前端。"""
    config = load_training_config(config_path)
    policy, _reward, _trainer = build_plugins(config)
    replay_config = config.get("replay") or {}
    dataset = JSONLDataset(config["dataset"]["path"])
    max_steps = int(config.get("environment", {}).get("max_steps", 1000))
    for episode, instance in enumerate(dataset):
        if limit is not None and episode >= limit:
            break
        environment = _make_env(config, instance)
        observation, _ = environment.reset(seed=instance.get("seed"))
        for _ in range(max_steps):
            mask = observation.get("action_mask", observation.get("action_masks")) if isinstance(observation, dict) else None
            action = policy.act(observation, mask, deterministic=True)
            observation, _reward, terminated, truncated, info = environment.step(action)
            if terminated or truncated:
                break
        print({"episode": episode, "instance_id": instance.get("instance_id"), "steps": _ + 1, "info": info})


def main() -> None:
    parser = argparse.ArgumentParser(description="SkyEngine 可插拔 DFJSP-T 训练平台")
    sub = parser.add_subparsers(dest="command", required=True)
    train_parser = sub.add_parser("train")
    train_parser.add_argument("config")
    evaluate_parser = sub.add_parser("evaluate")
    evaluate_parser.add_argument("config")
    evaluate_parser.add_argument("--limit", type=int, default=None)
    replay_parser = sub.add_parser("replay")
    replay_parser.add_argument("config")
    replay_parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    if args.command == "train":
        train(args.config)
    elif args.command == "evaluate":
        evaluate(args.config, args.limit)
    elif args.command == "replay":
        replay(args.config, args.limit)


if __name__ == "__main__":
    main()
