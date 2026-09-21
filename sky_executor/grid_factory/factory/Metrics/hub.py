"""
MetricsHub — 统一指标收集器

挂在 GridFactoryEnv 上（非 gym Wrapper），每 step 收集所有维度指标，
计算 RL reward，可选持久化到磁盘。

用法:
    env = GridFactoryEnv(...)
    hub = MetricsHub(env, mode='rl')       # 训练模式，纯内存
    # 或
    hub = MetricsHub(env, mode='record')   # 记录模式，内存+磁盘

    # env.reset() 内部会调用 hub.on_episode_start()
    # env.step()  内部会调用 hub.on_step_end()
"""

from sky_executor.grid_factory.factory.Metrics.collectors import (
    fjsp_collect,
    mapf_collect,
    mapf_get_heatmaps,
    coupling_collect,
    coupling_episode_summary,
)
from sky_executor.grid_factory.factory.Metrics.reward import RewardCalculator
from sky_executor.grid_factory.factory.Metrics.persistence import PersistenceManager


class MetricsHub:
    """
    Parameters
    ----------
    env : GridFactoryEnv
        持有引用，读取 pogema_env 状态
    mode : str
        'rl'      → 纯内存，不写磁盘，面向训练
        'record'  → 内存 + 写磁盘，面向离线分析
    enable_reward : bool
        是否计算 RL reward 并注入 info
    """

    def __init__(self, env, mode: str = 'rl', enable_reward: bool = True):
        self.env = env
        self.mode = mode
        self.enable_reward = enable_reward

        self._step = 0
        self._prev_positions = None
        self._history = []

        self._persistence = PersistenceManager() if mode == 'record' else None

    # ------------------------------------------------------------------
    # 生命周期钩子 — 由 GridFactoryEnv 调用
    # ------------------------------------------------------------------

    def on_episode_start(self):
        """在 reset() 中调用"""
        self._step = 0
        self._prev_positions = None
        self._history.clear()

    def on_step_end(self, observations, rewards, terminations, truncations, infos):
        """
        在 GridFactoryEnv.step() 末尾调用。
        此时 env 状态已完全更新。

        Returns
        -------
        与 gym step 相同的五元组，infos 中注入了 metrics 和 reward
        """
        self._step += 1
        penv = self.env.pogema_env
        t = penv.env_timeline

        # --- 收集各维度指标 ---
        metrics = {}
        metrics.update(fjsp_collect(penv, t))
        metrics.update(mapf_collect(penv, t, self._prev_positions))
        metrics.update(coupling_collect(penv, t))
        metrics.update(coupling_episode_summary(penv))
        metrics.update({"timeline": float(t), "makespan": float(metrics["full_makespan"]),
                        "reschedule_count": penv.reschedule_count,
                        "reassigned_operation_count": penv.reassigned_operation_count,
                        "reassigned_transport_count": penv.reassigned_transport_count,
                        "buffer_blocked_steps": sum(machine.buffer_blocked_steps for machine in penv.machines),
                        "remaining_work": sum(max(0.0, float(op.nominal_proc_time or op.proc_time) - op.processed_time)
                                              for job in penv.jobs for op in job.ops if op.status != "FINISHED")})

        # --- 记录当前位置供下一步使用 ---
        self._prev_positions = [tuple(p) for p in penv.grid.positions_xy]

        # --- 存内存历史 ---
        self._history.append({"step": self._step, **metrics})

        # --- 可选：写磁盘 ---
        if self._persistence:
            self._persistence.save_step(self._step, metrics)

        # --- 判断 episode 是否结束 ---
        done = self._check_done(terminations)

        # --- 如果结束，补充 episode 级指标 ---
        if done:
            ep_metrics = coupling_episode_summary(penv)
            metrics.update(ep_metrics)

        # --- 注入 info ---
        infos['metrics'] = metrics

        # --- 计算 RL reward ---
        if self.enable_reward:
            reward = RewardCalculator.compute(metrics, done=done)
            infos['metrics_reward'] = reward
        else:
            infos['metrics_reward'] = 0.0

        return observations, rewards, terminations, truncations, infos

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    def get_latest(self) -> dict:
        """获取最近一步的指标"""
        return self._history[-1] if self._history else {}

    def get_history(self) -> list:
        """获取全部历史指标"""
        return self._history

    def get_episode_summary(self) -> dict:
        """Episode 结束后调用，返回汇总指标（含热力图）"""
        if not self._history:
            return {}
        final = dict(self._history[-1])
        # 补充 episode 级汇总
        penv = self.env.pogema_env
        ep_summary = coupling_episode_summary(penv)
        final.update(ep_summary)

        # 补充热力图
        heatmaps = mapf_get_heatmaps(penv)
        final["agv_transit_heatmap"] = heatmaps["transit"].tolist()
        final["agv_occupancy_heatmap"] = heatmaps["occupancy"].tolist()

        if self._persistence:
            self._persistence.save_episode(final)

        return final

    def get_heatmaps(self) -> dict:
        """获取当前累积的热力图（numpy 矩阵，可直接用于可视化）"""
        penv = self.env.pogema_env
        return mapf_get_heatmaps(penv)

    def set_ideal_makespan(self, value: float):
        """
        设置理想 makespan（oracle 环境跑出的），用于计算 coupling_penalty。
        coupling_penalty = actual_makespan - ideal_makespan
        """
        self._ideal_makespan = value

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _check_done(self, terminations) -> bool:
        """判断 episode 是否结束"""
        if isinstance(terminations, dict):
            return bool(terminations.get("job_done", False))
        if isinstance(terminations, (list, tuple)):
            return all(terminations)
        return False
