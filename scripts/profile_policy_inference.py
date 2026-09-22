"""Compare CPU/CUDA decision latency on identical real training observations."""
from __future__ import annotations

import argparse
import io
import json
import multiprocessing as mp
import statistics
import time
import zipfile
from pathlib import Path

import torch

from experiment.algorithm_platform_plugins.dfjsp_t.ppo import _algorithm_types
from experiment.rl_platform import SkyEngineTrainingEnv


def concurrent_worker(connection, start_event, data: bytes, device: str) -> None:
    torch.set_num_threads(1)
    _, policy_type, _, _ = _algorithm_types()
    payload, samples = torch.load(io.BytesIO(data), map_location="cpu", weights_only=False)
    policy = policy_type(**{**payload["policy_parameters"], "device": device})
    policy.load_state_dict(payload["policy"])
    histories: list = [None if sample["history"] is None else sample["history"].to(device) for sample in samples]
    with torch.inference_mode():
        for sample, history in zip(samples, histories):
            policy.model(sample["graph"], sample["candidates"], history)
        if device != "cpu":
            torch.cuda.synchronize(device)
        connection.send("ready")
        start_event.wait()
        started: float = time.perf_counter()
        for _ in range(10):
            for sample, history in zip(samples, histories):
                output = policy.model(sample["graph"], sample["candidates"], history)
                lp, value, _ = policy.model.statistics(output, sample["candidates"], sample["scope"], sample["selected"])
                float(lp)
                float(value)
        if device != "cpu":
            torch.cuda.synchronize(device)
        connection.send(time.perf_counter() - started)
    connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--cuda", default="cuda:0")
    parser.add_argument("--workers", type=int, default=0)
    args = parser.parse_args()
    _, policy_type, _, _ = _algorithm_types()
    torch.set_num_threads(1)
    with zipfile.ZipFile(args.checkpoint) as archive:
        payload: dict = torch.load(io.BytesIO(archive.read("checkpoint.pt")), map_location="cpu", weights_only=False)
    kwargs: dict = {**payload["policy_parameters"], "device": "cpu"}
    policy = policy_type(**kwargs)
    policy.load_state_dict(payload["policy"])
    samples: list[dict] = []
    instances: list[dict] = [json.loads(line) for line in Path(args.dataset).read_text().splitlines()[:3]]
    for instance in instances:
        env = SkyEngineTrainingEnv(instance)
        policy.reset()
        observation, _ = env.reset(seed=instance["seed"])
        count: int = 0
        started: float = time.perf_counter()
        for _ in range(100):
            action = policy.act(observation, deterministic=True)
            sample = policy.last_action_data
            if sample is not None and sample["candidates"]["scopes"].numel() > 1:
                samples.append(sample)
                count += 1
            observation, _, done, truncated, _ = env.step(action)
            if count >= 4 or done or truncated:
                break
        env.close()
        print(json.dumps({"instance": instance["instance_id"], "samples": count,
                          "collection_seconds": time.perf_counter() - started}), flush=True)
    if not samples:
        raise RuntimeError("No representative candidate decisions collected")
    for device in ("cpu", args.cuda):
        candidate = policy_type(**{**kwargs, "device": device})
        candidate.load_state_dict(payload["policy"])
        histories: list = [None if sample["history"] is None else sample["history"].to(device) for sample in samples]
        with torch.inference_mode():
            for _ in range(5):
                for sample, history in zip(samples, histories):
                    candidate.model(sample["graph"], sample["candidates"], history)
            if device != "cpu":
                torch.cuda.synchronize(device)
            rounds: list[float] = []
            for _ in range(30):
                started = time.perf_counter()
                for sample, history in zip(samples, histories):
                    output = candidate.model(sample["graph"], sample["candidates"], history)
                    log_prob, value, _ = candidate.model.statistics(output, sample["candidates"], sample["scope"], sample["selected"])
                    float(log_prob)
                    float(value)
                if device != "cpu":
                    torch.cuda.synchronize(device)
                rounds.append((time.perf_counter() - started) * 1000 / len(samples))
        print(json.dumps({"device": device, "samples": len(samples),
                          "median_ms_per_decision": statistics.median(rounds), "min_ms_per_decision": min(rounds),
                          "candidate_counts": [sample["candidates"]["scopes"].numel() for sample in samples]}), flush=True)
        del candidate
    if args.workers:
        buffer = io.BytesIO()
        torch.save((payload, samples), buffer)
        data: bytes = buffer.getvalue()
        context = mp.get_context("spawn")
        for device in ("cpu", args.cuda):
            start_event = context.Event()
            processes: list = []
            connections: list = []
            for _ in range(args.workers):
                parent, child = context.Pipe()
                process = context.Process(target=concurrent_worker, args=(child, start_event, data, device))
                process.start()
                child.close()
                connections.append(parent)
                processes.append(process)
            for connection in connections:
                connection.recv()
            start_event.set()
            times: list[float] = [connection.recv() for connection in connections]
            for process in processes:
                process.join()
            for connection in connections:
                connection.close()
            print(json.dumps({"device": device, "workers": args.workers,
                              "concurrent_median_ms_per_decision": statistics.median(times) * 1000 / (10 * len(samples)),
                              "aggregate_decisions_per_second": args.workers * 10 * len(samples) / max(times)}), flush=True)


if __name__ == "__main__":
    main()
