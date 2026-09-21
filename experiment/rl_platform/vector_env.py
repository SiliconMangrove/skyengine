"""轻量多实例环境。"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence
import multiprocessing as mp


class VectorEnv:
    def __init__(self, env_factory: Callable[[Mapping[str, Any]], Any], instances: Sequence[Mapping[str, Any]]):
        self.envs = [env_factory(instance) for instance in instances]

    def reset(self, seeds: Sequence[int] | None = None):
        seeds = list(seeds) if seeds is not None else [None] * len(self.envs)
        if len(seeds) != len(self.envs):
            raise ValueError("seeds 数量必须与环境数量相同")
        return tuple(env.reset(seed=seed) for env, seed in zip(self.envs, seeds))

    def step(self, actions: Sequence[Mapping[str, Any]]):
        if len(actions) != len(self.envs):
            raise ValueError("actions 数量必须与环境数量相同")
        results = [env.step(action) for env, action in zip(self.envs, actions)]
        return tuple(zip(*results))

    def clone_states(self):
        return [env.clone_state() for env in self.envs]

    def restore_states(self, states):
        if len(states) != len(self.envs):
            raise ValueError("states 数量必须与环境数量相同")
        for env, state in zip(self.envs, states):
            env.restore_state(state)


def _remote_worker(connection, env_factory, instance):
    env = env_factory(instance)
    while True:
        command, payload = connection.recv()
        if command == "reset":
            connection.send(env.reset(seed=payload))
        elif command == "step":
            connection.send(env.step(payload))
        elif command == "metrics":
            connection.send(env.metrics())
        elif command == "close":
            connection.close()
            return
        else:
            raise ValueError(f"unknown remote environment command: {command}")


class SubprocessVectorEnv:
    """Persistent process-backed environments for CPU sampling."""

    def __init__(self, env_factory: Callable[[Mapping[str, Any]], Any], instances: Sequence[Mapping[str, Any]]):
        context = mp.get_context("spawn")
        self._connections = []
        self._processes = []
        for instance in instances:
            parent, child = context.Pipe()
            process = context.Process(target=_remote_worker, args=(child, env_factory, instance), daemon=True)
            process.start()
            child.close()
            self._connections.append(parent)
            self._processes.append(process)

    class _Proxy:
        def __init__(self, connection):
            self.connection = connection

        def reset(self, seed=None, options=None):
            del options
            self.connection.send(("reset", seed))
            return self.connection.recv()

        def step(self, action):
            self.connection.send(("step", action))
            return self.connection.recv()

        def metrics(self):
            self.connection.send(("metrics", None))
            return self.connection.recv()

    @property
    def envs(self):
        return [self._Proxy(connection) for connection in self._connections]

    def reset(self, seeds: Sequence[int | None] | None = None):
        seeds = list(seeds) if seeds is not None else [None] * len(self._connections)
        if len(seeds) != len(self._connections):
            raise ValueError("seeds 数量必须与环境数量相同")
        for connection, seed in zip(self._connections, seeds):
            connection.send(("reset", seed))
        return tuple(connection.recv() for connection in self._connections)

    def step(self, actions: Sequence[Mapping[str, Any]]):
        if len(actions) != len(self._connections):
            raise ValueError("actions 数量必须与环境数量相同")
        for connection, action in zip(self._connections, actions):
            connection.send(("step", action))
        return tuple(connection.recv() for connection in self._connections)

    def close(self):
        for connection in self._connections:
            connection.send(("close", None))
        for process in self._processes:
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
        self._connections.clear()
        self._processes.clear()
