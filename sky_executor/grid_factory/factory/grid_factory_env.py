"""
@Project ：SkyEngine
@File    ：grid_factory_env.py
@IDE     ：PyCharm
@Author  ：Skyrim
@Date    ：2025/1/15 10:00
"""

from typing import List, Tuple, Optional
from copy import deepcopy

from pettingzoo import ParallelEnv
from pogema.grid import Grid
from pogema import GridConfig, AnimationMonitor

from sky_executor.utils.registry import register_component
from sky_executor.grid_factory.factory.Utils.structure import (
    MachineConfig,
    JobConfig,
)

from sky_executor.grid_factory.factory.assign_env import (
    PogemaLifeLongWithAssign,
)
from sky_executor.grid_factory.factory.Utils.machine import (
    generate_machines,
)
from sky_executor.grid_factory.factory.Utils.job import generate_jobs
from sky_executor.grid_factory.factory.Metrics.hub import MetricsHub
from sky_executor.grid_factory.factory.Events import ExceptionInjector
from sky_logs.logger import LOGGER
from .observation import observable_state


@register_component("factory")
class GridFactoryEnv(ParallelEnv):
    """
    基于Pogema的网格工厂环境

    功能特性:
    1. 使用Pogema作为底层网格环境
    2. 支持多智能体路径规划
    3. 集成事件系统和回调机制
    4. 支持工厂任务调度

    注意环境reset之后才能有grid相关结构
    """

    metadata = {"render_modes": ["human"], "name": "grid_factory_env"}

    def __init__(
        self,
        grid_config: Optional[GridConfig] = None,
        machine_config: Optional[MachineConfig] = None,
        job_config: Optional[JobConfig] = None,
        random_target=False,
        metrics_mode: str = 'rl',
        exception_config: Optional[dict] = None,
        processing_time_config: Optional[dict] = None,
        material_handling_config: Optional[dict] = None,
        headless: bool = False,
    ):
        """
        初始化网格工厂环境
        Args:
            grid_config: Pogema网格配置
            agent: 智能体实例
            env_config: 环境配置
        """
        super().__init__()

        # 环境状态
        self.env_timeline = 0  # 离散化的环境时间

        # Pogema环境
        self.pogema_env: PogemaLifeLongWithAssign | None = None
        self.grid_config = grid_config or self._create_default_grid_config()
        self.grid_config.collision_system = "soft"

        # 机器组件 也就是路由的起始点和终止点
        self.machine_config = machine_config or self._create_default_machine_config()

        # 任务组件
        self.job_config = job_config or self._create_default_job_config()
        self.processing_time_config = processing_time_config
        self.material_handling_config = material_handling_config
        self.headless = bool(headless)

        # 动画保存路径
        self.initialize_pogema_env(random_target)
        self.init_machines = self.initialize_machine_env()
        self.init_jobs = self.initialize_job_env()

        # 统一指标收集器
        self.metrics_hub = MetricsHub(self, mode=metrics_mode)

        # 异常注入器。未提供 exception_config 时默认关闭。
        self.exception_injector = ExceptionInjector.from_env(exception_config)

    def _create_default_grid_config(self) -> GridConfig:
        """创建默认的网格配置"""
        return GridConfig(
            num_agents=4,
            size=8,
            density=0.1,
            seed=42,
            max_episode_steps=256,
            obs_radius=5,
            on_target="restart",
        )

    def _create_default_machine_config(self):
        """创建默认的机器配置"""
        return MachineConfig(
            num_machines=8,
            strategy="random",
            seed=42,
            zones=4,
            grid_spacing=5,
            noise=1.0,
        )

    def _create_default_job_config(self):
        """创建默认的任务配置"""
        return JobConfig(
            num_jobs=6,
            min_ops_per_job=2,
            max_ops_per_job=3,
            min_proc_time=2,
            max_proc_time=7,
            machine_choices=2,
            total_machines=self.machine_config.num_machines,
            seed=42,
        )

    @property
    def machine_possible_positions(self):
        return self.grid_config.possible_targets_xy

    @property
    def current_targets(self):
        return self.pogema_env.grid.finishes_xy

    def initialize_machine_env(self):
        grid: Grid = Grid(grid_config=self.grid_config)
        grid.get_obstacles()
        machines = generate_machines(grid.get_obstacles(), self.machine_config)
        self.grid_config.possible_targets_xy = [m.location for m in machines]
        return machines

    def initialize_pogema_env(self, random_target=False):
        """初始化Pogema环境"""
        # 创建Pogema环境
        self.pogema_env = PogemaLifeLongWithAssign(
            grid_config=self.grid_config,
            random_target=random_target,
            processing_time_config=self.processing_time_config,
            material_handling_config=self.material_handling_config,
        )
        # AnimationMonitor performs bookkeeping for the visual factory.  It is
        # deliberately omitted by the training backend so formal simulation
        # keeps the same state transitions without rendering overhead.
        # Keep a single owner of mutable factory state. Gym wrappers forward
        # reads but not attribute writes; only route movement/reset through them.
        self._movement_env = self.pogema_env if self.headless else AnimationMonitor(self.pogema_env)
        LOGGER.info(
            f"[GridFactoryEnv] Pogema环境初始化成功，智能体数量: {self.grid_config.num_agents}"
        )

    def initialize_job_env(self):
        """
        初始化 Job 层任务系统：
        1. 创建机器和 Job
        2. 调用调度器生成加工计划
        3. 存储初始调度结果
        """
        LOGGER.info("[GridFactoryEnv] 初始化 Job 层任务...")

        # self.job_config = self._create_default_job_config()
        jobs = generate_jobs(self.job_config)

        return jobs

    def set_env_timeline(self, env_timeline: int):
        """设置环境时间线"""
        self.env_timeline = env_timeline

    def show_actions(self, actions):
        from sky_executor.grid_factory.factory.Utils.pic_drawer import pretty_print_step

        pretty_print_step(self.env_timeline, actions)

    def show_jobs(
        self,
    ):
        from sky_executor.grid_factory.factory.Utils.pic_drawer import pretty_print_jobs

        pretty_print_jobs(self.pogema_env.jobs)

    def step(self, actions=None):
        self.pogema_env.last_events = list(self.pogema_env._pending_events)
        self.pogema_env._pending_events.clear()
        self.pogema_env._in_step = True
        self.pogema_env.apply_reschedule((actions or {}).get("reschedule", {}))
        self.env_timeline += 1

        (
            job_actions,
            task_actions,
            agent_actions,
        ) = self.unpack_input(actions)

        with self.exception_injector.step_context(self, agent_actions) as patched_agent_actions:
            j_obs, j_reward, j_terminated, j_truncated, j_info = self.pogema_env.job_step(
                job_actions
            )
            t_obs, t_reward, t_terminated, t_truncated, t_info = self.pogema_env.task_step(
                task_actions
            )
            a_obs, a_reward, a_terminated, a_truncated, a_info = self._movement_env.step(
                patched_agent_actions
            )
            self.pogema_env._release_due_jobs(self.pogema_env.env_timeline)

        # 合并输出
        observations, rewards, terminations, truncated, info = self.pack_output(
            [j_obs, j_reward, j_terminated, j_truncated, j_info],
            [t_obs, t_reward, t_terminated, t_truncated, t_info],
            [a_obs, a_reward, a_terminated, a_truncated, a_info],
        )

        # 统一指标收集 + reward 注入
        observations, rewards, terminations, truncated, info = \
            self.metrics_hub.on_step_end(observations, rewards, terminations, truncated, info)
        self.pogema_env._in_step = False
        return observations, rewards, terminations, truncated, info

    def reset(self, seed=None):
        LOGGER.info("[GridFactoryEnv] 重置环境")

        self.set_env_timeline(0)
        self.metrics_hub.on_episode_start()
        # --- 重置 Pogema 相关 ---
        a_observations, a_infos = self._movement_env.reset(seed=seed)

        # --- 重置任务相关，使用可能位置 ---
        t_observations, t_infos = self.pogema_env.machine_reset(deepcopy(self.init_machines))

        # --- 重置任务相关，使用任务列表 ---
        j_observations, j_infos = self.pogema_env.job_reset(deepcopy(self.init_jobs))

        self.exception_injector.reset(self, seed=self.pogema_env._episode_seed)

        # --- 打包输出 ---
        obs, rwd, term, trunc, info = self.pack_output(
            [j_observations, j_infos],
            [t_observations, t_infos],
            [a_observations, a_infos],
        )

        return obs, info

    def render(self):
        """渲染环境"""
        self.pogema_env.render()

    # ---------- 获取器方法 ----------
    def get_jobs(self) -> List:
        """获取作业列表"""
        return self.pogema_env.jobs

    def job_all_done(self):
        return self.pogema_env.job_all_done()

    def get_machines(self) -> List:
        """获取机器列表"""
        return self.pogema_env.machines

    def get_agents(self) -> List:
        """获取AGV列表"""
        return self.agents

    def get_agent_positions(self) -> List[Tuple[int, int]]:
        """获取智能体位置"""
        return self.pogema_env.grid.get_agents_xy()

    def get_agent_targets(self) -> List[Tuple[int, int]]:
        """获取智能体目标"""
        return self.pogema_env.grid.finishes_xy

    def unpack_input(self, actions):
        """将输入的 actions 拆分为机器与智能体两部分"""
        # 假设 self.input_actions 是外部传入的总动作字典
        job_actions = actions.get("job_actions", {})
        task_actions = actions.get("assign_actions", {})
        agent_actions = actions.get("agent_actions", {})
        return (
            job_actions,
            task_actions,
            agent_actions,
        )

    def pack_output(self, job_info, task_info, agent_info):
        """动态合并 job 和 agent 输出"""

        def unpack(info):
            """支持 (obs, reward, term, trunc, info) 或 (obs, info)"""
            if len(info) == 5:
                obs, reward, term, trunc, inf = info
            elif len(info) == 2:
                obs, inf = info
                reward, term, trunc = {}, {}, {}
            else:
                raise ValueError(f"Unexpected tuple length: {len(info)}")
            return obs, reward, term, trunc, inf

        j_obs, j_reward, j_term, j_trunc, j_info = unpack(job_info)
        t_obs, t_reward, t_term, t_trunc, t_info = unpack(task_info)
        a_obs, a_reward, a_term, a_trunc, a_info = unpack(agent_info)

        # 提取 job_done 布尔值 — job_step 返回 {"job_done": bool}，需要解包
        job_done = self.pogema_env.job_all_done()
        public = observable_state(self.pogema_env)
        j_obs = {"jobs": public["jobs"], "machines": public["machines"], "env_timeline": self.pogema_env.env_timeline}
        t_obs = dict(public)
        padding: int = self.grid_config.obs_radius or 0
        obstacles = self.pogema_env.grid.obstacles
        obstacles = obstacles[padding:-padding, padding:-padding] if padding else obstacles
        t_obs.update({"env_timeline": self.pogema_env.env_timeline, "obstacle_grid": obstacles.copy(),
                      "grid_height": obstacles.shape[0], "grid_width": obstacles.shape[1],
                      "move_deltas": [list(move) for move in self.grid_config.MOVES],
                      "agv_task_phase": list(self.pogema_env.agv_task_phase),
                      "agv_loaded": list(self.pogema_env.agv_loaded)})
        a_obs = self.pogema_env._obs()

        # 合并为标准输出结构
        observations = {
            "job_observation": j_obs,
            "task_observation": t_obs,
            "agent_observation": a_obs,
        }

        # 将上一步 Metrics 注入 task_observation，供 FeatureExtractor 使用
        prev_metrics = self.metrics_hub.get_latest()
        if prev_metrics and isinstance(t_obs, dict):
            t_obs["prev_metrics"] = prev_metrics

        if isinstance(t_obs, dict):
            self.exception_injector.enrich_task_observation(t_obs, self.pogema_env)

        rewards = {
            "job_reward": j_reward,
            "task_reward": t_reward,
            "agent_reward": a_reward,
        }
        terminations = {
            "job_done": job_done,
            "task_done": t_term,
            "agent_done": a_term,
        }
        truncations = {
            "job_truncated": j_trunc,
            "task_truncated": t_trunc,
            "agent_truncated": a_trunc,
        }
        infos = {
            "job_info": j_info,
            "task_info": t_info,
            "agent_info": a_info,
        }
        infos["events"] = deepcopy(self.pogema_env.last_events)
        infos["event_metrics"] = self.exception_injector.get_metrics()
        infos.update(self.exception_injector.get_epochs(self.pogema_env))

        return observations, rewards, terminations, truncations, infos
