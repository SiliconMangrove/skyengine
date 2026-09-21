"""Process-isolated, synchronous on-policy rollout collection."""

from __future__ import annotations

import io
import multiprocessing as mp
import random
import time
import traceback
from contextlib import suppress
from multiprocessing.connection import Connection, wait
from typing import Any, Callable, Mapping, Sequence

from .api import Trajectory
from .config import load_object
from .rollout import RolloutCollector


def _sampling_worker(
    connection: Connection, stop_event: Any, policy_spec: Mapping[str, Any],
    reward_spec: Mapping[str, Any], env_factory: Callable, policy_wrapper: Callable | None,
    metrics_reader: Callable,
) -> None:
    """Keep policy tensors on CPU and never send simulator objects to the parent."""
    try:
        import numpy as np
        import torch

        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        policy = load_object(policy_spec["target"], **dict(policy_spec["kwargs"], device="cpu"))
        reward = load_object(reward_spec["target"], **reward_spec["kwargs"])
        collector = RolloutCollector(policy_wrapper(policy) if policy_wrapper else policy, reward, training_only=True)
        connection.send(("ready",))

        def check_cancelled() -> None:
            if stop_event.is_set():
                raise RuntimeError("parallel sampling cancelled")

        while not stop_event.is_set():
            command, payload = connection.recv()
            if command == "close":
                return
            job, weights, max_steps = payload
            policy.load_state_dict(torch.load(io.BytesIO(weights), map_location="cpu", weights_only=True))
            del weights, payload
            seed: int = job["algorithm_seed"]
            random.seed(seed)
            np.random.seed(seed % (2**32))
            torch.manual_seed(seed)
            started: float = time.monotonic()
            env = env_factory(job["instance"])
            try:
                trajectory = collector.collect(
                    env, max_steps, episode_id=str(job["episode"]), seed=job["seed"],
                    progress_callback=lambda steps: connection.send(("progress", job["episode"], steps)),
                    cancel_check=check_cancelled,
                )
                metrics: dict[str, float] = metrics_reader(env)
            finally:
                env.close()
            trajectory.metadata.update({"episode": job["episode"], "metrics": metrics,
                                        "collection_seconds": time.monotonic() - started})
            # Explicit bytes avoid multiprocessing tensor shared-memory handles
            # and their dependency on worker lifetime during deserialization.
            buffer = io.BytesIO()
            torch.save(trajectory, buffer)
            connection.send(("result", job["episode"], buffer.getvalue()))
            trajectory.transitions.clear()
            policy.last_action_data = None
            del trajectory, buffer, env, job
    except EOFError:
        return
    except Exception:
        with suppress(BrokenPipeError, OSError):
            connection.send(("error", traceback.format_exc()))
    finally:
        connection.close()


class ParallelRolloutCollector:
    """Persistent CPU workers; one frozen policy version per collection batch."""

    def __init__(self, num_envs: int, policy_spec: Mapping[str, Any], reward_spec: Mapping[str, Any],
                 env_factory: Callable, policy_wrapper: Callable | None, metrics_reader: Callable):
        self.num_envs: int = num_envs
        self._specs: tuple = (policy_spec, reward_spec, env_factory, policy_wrapper, metrics_reader)
        self._context = mp.get_context("spawn")
        self._stop_event = self._context.Event()
        self._connections: list[Connection] = []
        self._processes: list[Any] = []
        self._ready: bool = False

    def __enter__(self) -> ParallelRolloutCollector:
        try:
            for _ in range(self.num_envs):
                parent, child = self._context.Pipe()
                process = self._context.Process(
                    target=_sampling_worker, args=(child, self._stop_event, *self._specs), daemon=True,
                )
                process.start()
                child.close()
                self._connections.append(parent)
                self._processes.append(process)
        except Exception:
            self.close()
            raise
        return self

    def collect(self, jobs: Sequence[Mapping[str, Any]], policy: Any, max_steps: int,
                progress_callback: Callable[[int, int], None], cancel_check: Callable[[], None]) -> list[Trajectory]:
        import torch

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

        buffer = io.BytesIO()
        torch.save({name: tensor.detach().cpu() for name, tensor in policy.state_dict().items()}, buffer)
        weights: bytes = buffer.getvalue()
        active: dict[Connection, int] = {}
        for index, job in enumerate(jobs):
            connection = self._connections[index]
            try:
                connection.send(("collect", (job, weights, max_steps)))
            except (BrokenPipeError, OSError) as error:
                process = self._processes[index]
                process.join(timeout=1.0)
                raise RuntimeError(
                    f"sampling worker {index} (pid={process.pid}) disconnected while receiving "
                    f"episode {job['episode']}; exit code={process.exitcode}. See backend logs."
                ) from error
            active[connection] = index
        del weights, buffer
        results: dict[int, Trajectory] = {}
        while active:
            cancel_check()
            for connection in wait(list(active), timeout=0.25):
                index = active[connection]
                try:
                    message = connection.recv()
                except (EOFError, OSError) as error:
                    raise RuntimeError(f"sampling worker {index} exited before returning its trajectory") from error
                if message[0] == "error":
                    raise RuntimeError(f"sampling worker {index} failed:\n{message[1]}")
                if message[0] == "progress":
                    progress_callback(message[1], message[2])
                else:
                    # These bytes come exclusively from our own spawned worker.
                    results[index] = torch.load(io.BytesIO(message[2]), map_location="cpu", weights_only=False)
                    del active[connection]
                del message
            for connection, index in active.items():
                process = self._processes[index]
                if process.exitcode is not None and not connection.poll():
                    raise RuntimeError(f"sampling worker {index} exited with code {process.exitcode}")
        return [results[index] for index in range(len(jobs))]

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
