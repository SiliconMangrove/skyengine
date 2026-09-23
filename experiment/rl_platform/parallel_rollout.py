"""Process-isolated collection with a frozen policy for each rollout batch."""

from __future__ import annotations

import io
import multiprocessing as mp
import random
import time
import traceback
from contextlib import suppress
from multiprocessing.connection import Connection, wait
from typing import Any, Callable, Mapping, Sequence

from .api import Trajectory, Transition
from .config import load_object
from .cpu_affinity import bind_worker_cpu
from .rollout import RolloutCollector


class _RoundFinished(Exception):
    """Discard this job at the collection barrier, without stopping the worker."""


def _sampling_worker(
    connection: Connection, stop_event: Any, round_finished: Any, policy_spec: Mapping[str, Any],
    reward_spec: Mapping[str, Any], env_factory: Callable, policy_wrapper: Callable | None,
    metrics_reader: Callable, device: str, evaluation: bool, cpu: int | None,
) -> None:
    """Keep simulator objects local and return CPU-only results to the parent."""
    env = None
    try:
        if cpu is not None:
            bind_worker_cpu(cpu)
        import numpy as np
        import torch

        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        policy = load_object(policy_spec["target"], **dict(policy_spec["kwargs"], device=device))
        reward = load_object(reward_spec["target"], **reward_spec["kwargs"])
        collector = RolloutCollector(policy_wrapper(policy) if policy_wrapper else policy, reward, training_only=True)
        connection.send(("ready",))

        def check_cancelled() -> None:
            if stop_event.is_set():
                raise RuntimeError("parallel sampling cancelled")
            if round_finished.is_set():
                raise _RoundFinished

        steps: int = 0

        def publish_progress(value: int) -> None:
            nonlocal steps
            steps = value
            if steps % 100 == 0:
                connection.send(("progress", job["episode"], steps))

        while not stop_event.is_set():
            command, payload = connection.recv()
            if command == "close":
                return
            job, weights, max_steps = payload
            if weights is not None:
                policy.load_state_dict(torch.load(io.BytesIO(weights), map_location="cpu", weights_only=True))
            del weights, payload
            seed: int = job["algorithm_seed"]
            started: float = time.monotonic()
            random.seed(seed)
            np.random.seed(seed % (2**32))
            torch.manual_seed(seed)
            env = env_factory(job["instance"])
            steps = 0
            try:
                check_cancelled()
                if evaluation:
                    policy.reset()
                    observation, _ = env.reset(seed=job["seed"])
                    before_metrics: dict = env.metrics()
                    episode_reward: float = 0.0
                    for step in range(max_steps):
                        check_cancelled()
                        action = policy.act(observation, deterministic=True)
                        observation, raw_reward, terminated, truncated, info = env.step(action)
                        after_metrics: dict = info["metrics"]
                        episode_reward += reward.compute(Transition(None, None, raw_reward, None, terminated, truncated, {
                            "before_metrics": before_metrics, "after_metrics": after_metrics, "metrics": after_metrics,
                            "delta_t": after_metrics["timeline"] - before_metrics.get("timeline", 0.0),
                        }))
                        before_metrics = after_metrics
                        publish_progress(step + 1)
                        if terminated or truncated:
                            break
                    trajectory = Trajectory([], episode_id=str(job["episode"]), metadata={"episode_reward": episode_reward})
                else:
                    trajectory = collector.collect(
                        env, max_steps, episode_id=str(job["episode"]), seed=job["seed"],
                        progress_callback=publish_progress, progress_interval=1,
                        cancel_check=check_cancelled,
                    )
                collected_at: float = time.monotonic()
                if steps % 100:
                    connection.send(("progress", job["episode"], steps))
                metrics: dict[str, float] = metrics_reader(env)
                metrics["episode_reward"] = trajectory.metadata["episode_reward"]
                metrics.update(getattr(policy, "planning_metrics", {}))
            except _RoundFinished:
                policy.reset()
                connection.send(("discarded", job["episode"], steps, time.monotonic() - started))
                continue
            finally:
                env.close()
                env = None
            trajectory.metadata.update({"episode": job["episode"], "metrics": metrics,
                                        "collection_completed_monotonic": collected_at,
                                        "collection_seconds": time.monotonic() - started})
            # Explicit bytes avoid multiprocessing tensor shared-memory handles
            # and their dependency on worker lifetime during deserialization.
            buffer = io.BytesIO()
            torch.save(trajectory, buffer)
            connection.send(("result", job["episode"], buffer.getvalue()))
            trajectory.transitions.clear()
            policy.last_action_data = None
            del trajectory, buffer, job
    except EOFError:
        return
    except Exception:
        with suppress(BrokenPipeError, OSError):
            connection.send(("error", traceback.format_exc()))
    finally:
        if env is not None:
            env.close()
        connection.close()


class ParallelRolloutCollector:
    """Persistent workers; one frozen policy version per collection batch."""

    def __init__(self, num_envs: int, policy_spec: Mapping[str, Any], reward_spec: Mapping[str, Any],
                 env_factory: Callable, policy_wrapper: Callable | None, metrics_reader: Callable,
                 *, device: str = "cpu", evaluation: bool = False, cpu_ids: Sequence[int] | None = None):
        self.num_envs: int = num_envs
        self._specs: tuple = (policy_spec, reward_spec, env_factory, policy_wrapper, metrics_reader, device, evaluation)
        self._context = mp.get_context("spawn")
        self._stop_event = self._context.Event()
        self._round_finished = self._context.Event()
        self._connections: list[Connection] = []
        self._processes: list[Any] = []
        self._ready: bool = False
        self._cpu_ids: Sequence[int | None] = [None] * num_envs if cpu_ids is None else cpu_ids
        self.discarded_episode_ids: list[int] = []
        self.round_statistics: dict[str, Any] = {}

    def __enter__(self) -> ParallelRolloutCollector:
        try:
            for index in range(self.num_envs):
                parent, child = self._context.Pipe()
                process = self._context.Process(
                    target=_sampling_worker, args=(child, self._stop_event, self._round_finished,
                                                  *self._specs, self._cpu_ids[index]), daemon=True,
                )
                process.start()
                child.close()
                self._connections.append(parent)
                self._processes.append(process)
        except Exception:
            self.close()
            raise
        return self

    def _wait_ready(self, cancel_check: Callable[[], None]) -> None:
        cancel_check()
        if not self._ready:
            pending: dict[Connection, int] = dict(zip(self._connections, range(self.num_envs)))
            while pending:
                cancel_check()
                for connection in wait(list(pending), timeout=0.25):
                    index: int = pending[connection]
                    process = self._processes[index]
                    try:
                        message = connection.recv()
                    except (EOFError, OSError) as error:
                        process.join(timeout=1.0)
                        raise RuntimeError(
                            f"sampling worker {index} (pid={process.pid}) failed during startup "
                            f"before becoming ready; exit code={process.exitcode}. "
                            "See backend logs for the Python spawn/import traceback."
                        ) from error
                    if message[0] == "error":
                        raise RuntimeError(f"sampling worker {index} failed during initialization:\n{message[1]}")
                    if message[0] != "ready":
                        raise RuntimeError(f"sampling worker {index} sent an unexpected startup message: {message[0]}")
                    del pending[connection]
            self._ready = True

    def collect(self, jobs: Sequence[Mapping[str, Any]], policy: Any, max_steps: int,
                progress_callback: Callable[[int, int], None], cancel_check: Callable[[], None],
                *, weights: bytes | None = None, stop_after_each_worker: bool = False) -> list[Trajectory]:
        import torch

        self._wait_ready(cancel_check)
        self._round_finished.clear()
        self.discarded_episode_ids = []
        self.round_statistics = {}

        if weights is None:
            buffer = io.BytesIO()
            torch.save({name: tensor.detach().cpu() for name, tensor in policy.state_dict().items()}, buffer)
            weights = buffer.getvalue()
            del buffer
        worker_count: int = min(self.num_envs, len(jobs))
        workers: dict[Connection, int] = dict(zip(self._connections[:worker_count], range(worker_count)))
        active: dict[Connection, int] = {}
        completed_per_worker: list[int] = [0] * worker_count
        next_job: int = 0

        def dispatch(connection: Connection, index: int, job_weights: bytes | None) -> None:
            job = jobs[index]
            try:
                connection.send(("collect", (job, job_weights, max_steps)))
            except (BrokenPipeError, OSError) as error:
                process = self._processes[workers[connection]]
                process.join(timeout=1.0)
                raise RuntimeError(
                    f"sampling worker {workers[connection]} (pid={process.pid}) disconnected while receiving "
                    f"episode {job['episode']}; exit code={process.exitcode}. See backend logs."
                ) from error
            active[connection] = index

        for connection in workers:
            dispatch(connection, next_job, weights)
            next_job += 1
        del weights
        results: dict[int, Trajectory] = {}
        discarded_steps: int = 0
        discarded_seconds: float = 0.0
        stopping: bool = False
        cutoff: float = float("inf")
        while active:
            cancel_check()
            for connection in wait(list(active), timeout=0.25):
                index = active[connection]
                try:
                    message = connection.recv()
                except (EOFError, OSError) as error:
                    raise RuntimeError(f"sampling worker {workers[connection]} exited before returning its trajectory") from error
                if message[0] == "error":
                    raise RuntimeError(f"sampling worker {workers[connection]} failed during episode {jobs[index]['episode'] + 1}:\n{message[1]}")
                if message[0] == "progress":
                    progress_callback(message[1], message[2])
                elif message[0] == "discarded":
                    self.discarded_episode_ids.append(int(jobs[index]["episode"]))
                    discarded_steps += message[2]
                    discarded_seconds += message[3]
                    del active[connection]
                else:
                    # These bytes come exclusively from our own spawned worker.
                    trajectory: Trajectory = torch.load(io.BytesIO(message[2]), map_location="cpu", weights_only=False)
                    if trajectory.metadata["collection_completed_monotonic"] > cutoff:
                        # Keep completions before the barrier even if their IPC
                        # result arrives later; discard work finishing after it.
                        self.discarded_episode_ids.append(int(jobs[index]["episode"]))
                        discarded_steps += int(trajectory.metadata["simulation_steps"])
                        discarded_seconds += trajectory.metadata["collection_seconds"]
                        trajectory.transitions.clear()
                    else:
                        results[index] = trajectory
                        completed_per_worker[workers[connection]] += 1
                    del active[connection]
                    del trajectory
                del message
            if stop_after_each_worker and not stopping and all(completed_per_worker):
                stopping = True
                cutoff = time.monotonic()
                self._round_finished.set()
            if not stopping:
                for connection in workers:
                    if connection not in active and next_job < len(jobs):
                        dispatch(connection, next_job, None)
                        next_job += 1
            for connection, index in active.items():
                process = self._processes[workers[connection]]
                if process.exitcode is not None and not connection.poll():
                    raise RuntimeError(f"sampling worker {workers[connection]} exited with code {process.exitcode}")
        self.round_statistics = {
            "worker_completed_counts": completed_per_worker, "dispatched_jobs": next_job,
            "completed_jobs": len(results), "discarded_jobs": len(self.discarded_episode_ids),
            "discarded_steps": discarded_steps, "discarded_collection_seconds": discarded_seconds,
            "discarded_episode_ids": list(self.discarded_episode_ids),
        }
        return [results[index] for index in sorted(results)]

    def close(self) -> None:
        self._stop_event.set()
        for connection in self._connections:
            with suppress(BrokenPipeError, OSError):
                connection.send(("close", None))
        deadline: float = time.monotonic() + 5.0
        for process in self._processes:
            process.join(timeout=max(0.0, deadline - time.monotonic()))
            if process.is_alive():
                process.terminate()
        for process in self._processes:
            process.join(timeout=1.0)
            if process.is_alive():
                process.kill()
                process.join()
        for connection in self._connections:
            connection.close()
        self._connections.clear()
        self._processes.clear()

    def __exit__(self, exc_type, exc_value, traceback_value) -> None:
        self.close()
