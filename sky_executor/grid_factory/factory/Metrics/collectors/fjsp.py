"""
FJSP 侧指标采集：
- machine_utilization: 机器平均利用率
- machine_non_processing_time_mean: 平均非加工时间（粗粒度，= total_t - work_time）
- machine_load_variance: 负载方差（总体方差）
- operation_queue_waiting_time_mean: 工序到达机器后排队等待加工的平均时间
- processing_time_deviation_mean/max: 已采样工序相对名义加工时间的偏差
"""


def _mean(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


def _var(values: list) -> float:
    if len(values) < 2:
        return 0.0
    m = sum(values) / len(values)
    return sum((v - m) ** 2 for v in values) / len(values)


def collect(penv, t: int) -> dict:
    """
    从 PogemaLifeLongWithAssign 上读取 FJSP 相关指标。

    Parameters
    ----------
    penv : PogemaLifeLongWithAssign
    t : int  — 当前 env_timeline
    """
    machines = penv.machines
    n = len(machines)
    assert t >= 0, f"t must be non-negative, got {t}"
    total_t = max(t, 1)

    work_times = [m.total_work_time for m in machines]
    # 粗粒度非加工时间：= total_t - work_time
    # 不区分原因（无任务 / AGV 未送 / 排队 / 未调度）
    non_processing_times = [total_t - wt for wt in work_times]

    # operation_queue_waiting_time: 物料到达机器后 → 开始加工
    # 这是机器前排队等待，不是 AGV 运输延迟
    waiting_times = []
    processing_deviations = []
    for job in penv.jobs:
        for op in job.ops:
            if op.arrive_machine_at >= 0 and op.start_process_at >= 0:
                wait = op.start_process_at - op.arrive_machine_at
                if wait > 0:
                    waiting_times.append(wait)
            nominal = getattr(op, "nominal_proc_time", None)
            sampled = getattr(op, "sampled_proc_time", None)
            if nominal is not None and sampled is not None and float(nominal) > 0:
                processing_deviations.append(
                    abs(float(sampled) - float(nominal)) / float(nominal)
                )

    return {
        "machine_utilization": sum(work_times) / max(n * total_t, 1),
        "machine_non_processing_time_mean": sum(non_processing_times) / max(n, 1),
        "machine_load_variance": _var(work_times),
        "operation_queue_waiting_time_mean": _mean(waiting_times),
        "processing_time_deviation_mean": _mean(processing_deviations),
        "processing_time_deviation_max": max(processing_deviations, default=0.0),
    }
