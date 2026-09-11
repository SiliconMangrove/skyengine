"""
@Project ：SkyEngine
@File    ：benchmark_generator.py
@IDE     ：PyCharm
@Author  ：Skyrimforest
@Date    ：2025/12/16

Benchmark Problem Generator
使用 MachineConfig 和 JobConfig 生成标准的 Job Shop 问题
"""

import numpy as np
from typing import List, Tuple, Optional
from dataclasses import dataclass

try:
    from pogema import GridConfig
except ImportError:
    GridConfig = None

from sky_executor.grid_factory.factory.Utils.structure import (
    Job,
    Operation,
    MachineConfig,
    JobConfig,
)


@dataclass
class BenchmarkProblem:
    """基准问题的完整定义 (包含Job Shop问题和可选的Grid环境配置)"""
    jobs: List[Job]
    machine_config: MachineConfig
    job_config: JobConfig
    grid_config_dict: Optional[dict] = None  # 存储Grid配置为字典而不是对象，避免验证问题
    
    @property
    def num_jobs(self) -> int:
        """工件数"""
        return len(self.jobs)
    
    @property
    def num_machines(self) -> int:
        """机器数"""
        return self.machine_config.num_machines
    
    @property
    def num_operations(self) -> int:
        """总工序数"""
        return sum(len(job.ops) for job in self.jobs)
    
    @property
    def total_processing_time(self) -> float:
        """所有工序的总处理时间"""
        return sum(sum(op.proc_time for op in job.ops) for job in self.jobs)
    
    @property
    def grid_size(self) -> Optional[int]:
        """网格大小（如果配置了grid_config）"""
        return self.grid_config_dict.get("size") if self.grid_config_dict else None
    
    @property
    def num_agents(self) -> Optional[int]:
        """代理数量（如果配置了grid_config）"""
        return self.grid_config_dict.get("num_agents") if self.grid_config_dict else None
    
    def get_grid_config(self) -> Optional['GridConfig']:
        """从字典创建GridConfig对象（需要时调用）"""
        if not self.grid_config_dict or not GridConfig:
            return None
        try:
            return GridConfig(**self.grid_config_dict)
        except Exception as e:
            print(f"[Warning] Failed to create GridConfig: {e}")
            return None
    
    def __repr__(self) -> str:
        base = (
            f"BenchmarkProblem("
            f"jobs={self.num_jobs}, "
            f"machines={self.num_machines}, "
            f"operations={self.num_operations}, "
            f"total_proc_time={self.total_processing_time:.2f}"
        )
        
        if self.grid_config_dict:
            base += f", grid_size={self.grid_size}, agents={self.num_agents}"
        
        base += ")"
        return base


class BenchmarkGenerator:
    """基准问题生成器 (支持Job Shop问题 + 可选的Grid环境)"""
    
    def __init__(
        self,
        machine_config: MachineConfig,
        job_config: JobConfig,
        grid_config: Optional['GridConfig'] = None
    ):
        """
        初始化生成器
        
        Args:
            machine_config: 机器配置
            job_config: 工件配置
            grid_config: Pogema网格环境配置（可选）
        """
        self.machine_config = machine_config
        self.job_config = job_config
        self.grid_config = grid_config
    
    def generate(self) -> BenchmarkProblem:
        """
        生成一个基准问题
        
        Returns:
            BenchmarkProblem 对象
        """
        np.random.seed(self.job_config.seed)
        
        jobs = []
        
        for job_id in range(self.job_config.num_jobs):
            # 随机确定该工件的工序数
            num_ops = np.random.randint(
                self.job_config.min_ops_per_job,
                self.job_config.max_ops_per_job + 1
            )
            
            # 生成工序列表
            operations = []
            
            for op_idx in range(num_ops):
                # 随机选择可选机器
                num_choices = min(
                    self.job_config.machine_choices,
                    self.machine_config.num_machines
                )
                machine_options = list(np.random.choice(
                    self.machine_config.num_machines,
                    size=num_choices,
                    replace=False
                ))
                
                # 随机生成处理时间
                proc_time = np.random.uniform(
                    self.job_config.min_proc_time,
                    self.job_config.max_proc_time
                )
                
                # 创建 Operation 对象
                operation = Operation(
                    job_id=job_id,
                    op_id=op_idx,
                    machine_options=machine_options,
                    proc_time=proc_time,
                    release=0.0,
                    due=None,
                )
                
                operations.append(operation)
            
            # 创建 Job 对象
            job = Job(
                job_id=job_id,
                ops=operations,
                release=0.0,
                due=None,
                completion_time=-1.0
            )
            
            jobs.append(job)
        
        return BenchmarkProblem(
            jobs=jobs,
            machine_config=self.machine_config,
            job_config=self.job_config,
            grid_config_dict=None  # 不存储GridConfig对象，避免验证问题
        )
    
    def generate_multiple(self, num_instances: int) -> List[BenchmarkProblem]:
        """
        生成多个基准问题（使用不同的随机种子）
        
        Args:
            num_instances: 生成的问题数量
        
        Returns:
            BenchmarkProblem 列表
        """
        problems = []
        
        for i in range(num_instances):
            # 为每个实例使用不同的种子
            config_copy = self.job_config.copy(
                update={"seed": self.job_config.seed + i}
            )
            
            generator = BenchmarkGenerator(self.machine_config, config_copy, None)
            problem = generator.generate()
            problems.append(problem)
        
        return problems


class StandardBenchmarkSuite:
    """标准基准测试套件 (包含Job Shop和Grid环境配置)"""
    
    # 预定义的标准规模 - SMALL
    SMALL = {
        "machine_config": MachineConfig(num_machines=3, seed=42),
        "job_config": JobConfig(
            num_jobs=5,
            min_ops_per_job=2,
            max_ops_per_job=3,
            min_proc_time=2,
            max_proc_time=8,
            machine_choices=2,
            total_machines=3,
            seed=42
        ),
        "grid_config": {
            "size": 8,
            "num_agents": 2,
            "density": 0.2,
            "obs_radius": 5,
            "seed": 42
        } if GridConfig else None
    }
    
    # 预定义的标准规模 - MEDIUM
    MEDIUM = {
        "machine_config": MachineConfig(num_machines=5, seed=42),
        "job_config": JobConfig(
            num_jobs=10,
            min_ops_per_job=3,
            max_ops_per_job=5,
            min_proc_time=2,
            max_proc_time=10,
            machine_choices=3,
            total_machines=5,
            seed=42
        ),
        "grid_config": {
            "size": 16,
            "num_agents": 4,
            "density": 0.2,
            "obs_radius": 6,
            "seed": 42
        } if GridConfig else None
    }
    
    # 预定义的标准规模 - LARGE
    LARGE = {
        "machine_config": MachineConfig(num_machines=8, seed=42),
        "job_config": JobConfig(
            num_jobs=20,
            min_ops_per_job=4,
            max_ops_per_job=6,
            min_proc_time=2,
            max_proc_time=12,
            machine_choices=4,
            total_machines=8,
            seed=42
        ),
        "grid_config": {
            "size": 32,
            "num_agents": 8,
            "density": 0.2,
            "obs_radius": 7,
            "seed": 42
        } if GridConfig else None
    }
    
    @classmethod
    def get_benchmark(cls, size: str) -> BenchmarkProblem:
        """
        获取标准规模的基准问题
        
        Args:
            size: 'small', 'medium', 或 'large'
        
        Returns:
            BenchmarkProblem 对象
        """
        size_key = size.upper()
        
        if size_key not in ['SMALL', 'MEDIUM', 'LARGE']:
            raise ValueError(f"Unknown size: {size}. Must be 'small', 'medium', or 'large'")
        
        config = getattr(cls, size_key)
        
        # 构建GridConfig
        grid_cfg = None
        if config["grid_config"] and GridConfig:
            grid_cfg = GridConfig(**config["grid_config"])
        
        generator = BenchmarkGenerator(
            config['machine_config'],
            config['job_config'],
            grid_cfg
        )
        
        return generator.generate()
    
    @classmethod
    def get_all_benchmarks(cls) -> dict:
        """
        获取所有标准规模的基准问题
        
        Returns:
            {'small': BenchmarkProblem, 'medium': BenchmarkProblem, 'large': BenchmarkProblem}
        """
        return {
            'small': cls.get_benchmark('small'),
            'medium': cls.get_benchmark('medium'),
            'large': cls.get_benchmark('large'),
        }


# 便捷函数

def create_benchmark_problem(
    num_jobs: int,
    num_machines: int,
    min_ops: int = 2,
    max_ops: int = 4,
    min_proc_time: float = 2.0,
    max_proc_time: float = 8.0,
    machine_choices: int = 2,
    seed: int = 42,
    # Grid配置参数
    with_grid: bool = False,
    grid_size: int = 16,
    num_agents: int = 4,
    grid_density: float = 0.2,
    grid_obs_radius: int = 5,
) -> BenchmarkProblem:
    """
    快速创建一个基准问题（便捷函数）
    
    Args:
        num_jobs: 工件数
        num_machines: 机器数
        min_ops: 最少工序数
        max_ops: 最多工序数
        min_proc_time: 最短处理时间
        max_proc_time: 最长处理时间
        machine_choices: 每个工序可选机器数
        seed: 随机种子
        # Grid环境配置
        with_grid: 是否添加Pogema网格配置
        grid_size: 网格大小 (边长)
        num_agents: 网格中的代理数量
        grid_density: 网格中的障碍物密度 [0, 1]
        grid_obs_radius: 代理的观察半径
    
    Returns:
        BenchmarkProblem 对象
    """
    machine_config = MachineConfig(
        num_machines=num_machines,
        seed=seed
    )
    
    job_config = JobConfig(
        num_jobs=num_jobs,
        min_ops_per_job=min_ops,
        max_ops_per_job=max_ops,
        min_proc_time=min_proc_time,
        max_proc_time=max_proc_time,
        machine_choices=min(machine_choices, num_machines),
        total_machines=num_machines,
        seed=seed
    )
    
    # 构建GridConfig（如果需要）
    grid_config = None
    if with_grid and GridConfig:
        grid_config = GridConfig(
            size=grid_size,
            num_agents=num_agents,
            density=grid_density,
            obs_radius=grid_obs_radius,
            seed=seed
        )
    
    generator = BenchmarkGenerator(machine_config, job_config, grid_config)
    return generator.generate()


if __name__ == "__main__":
    print("=" * 80)
    print("Benchmark Problem Generator 示例 (含GridConfig)")
    print("=" * 80)
    
    # 示例1：仅使用Job Shop配置
    print("\n1️⃣  仅使用Job Shop配置（无Grid）")
    print("-" * 80)
    problem1 = create_benchmark_problem(
        num_jobs=10,
        num_machines=5,
        min_ops=2,
        max_ops=4,
        seed=42
    )
    print(f"✅ {problem1}")
    print(f"   总处理时间: {problem1.total_processing_time:.2f}")
    
    # 示例2：同时使用Job Shop和Grid配置
    print("\n2️⃣  使用Job Shop + Pogema Grid配置")
    print("-" * 80)
    problem2 = create_benchmark_problem(
        num_jobs=10,
        num_machines=5,
        min_ops=2,
        max_ops=4,
        seed=42,
        with_grid=True,
        grid_size=16,
        num_agents=4,
        grid_density=0.2,
        grid_obs_radius=5
    )
    print(f"✅ {problem2}")
    if problem2.grid_config:
        print(f"   Grid配置: size={problem2.grid_size}, agents={problem2.num_agents}")
        print(f"   Grid详细: density={problem2.grid_config.density}, obs_radius={problem2.grid_config.obs_radius}")
    
    # 示例3：使用生成器 + Grid配置
    print("\n3️⃣  使用生成器创建基准问题（含Grid）")
    print("-" * 80)
    machine_cfg = MachineConfig(num_machines=4, seed=123)
    job_cfg = JobConfig(
        num_jobs=8,
        min_ops_per_job=3,
        max_ops_per_job=5,
        total_machines=4,
        seed=123
    )
    grid_cfg = GridConfig(
        size=12,
        num_agents=3,
        density=0.2,
        obs_radius=4,
        seed=123
    ) if GridConfig else None
    
    generator = BenchmarkGenerator(machine_cfg, job_cfg, grid_cfg)
    problem3 = generator.generate()
    print(f"✅ {problem3}")
    
    # 示例4：生成多个问题（含Grid）
    print("\n4️⃣  生成多个基准问题（含Grid）")
    print("-" * 80)
    problems = generator.generate_multiple(num_instances=3)
    for i, problem in enumerate(problems):
        print(f"   问题 {i+1}: {problem}")
    
    # 示例5：使用标准套件（含Grid）
    print("\n5️⃣  使用标准基准测试套件（含Grid）")
    print("-" * 80)
    benchmarks = StandardBenchmarkSuite.get_all_benchmarks()
    for size, problem in benchmarks.items():
        print(f"   {size.upper()}: {problem}")
        if problem.grid_config:
            print(f"      └─ Grid: size={problem.grid_size}, agents={problem.num_agents}")
    
    print("\n" + "=" * 80)
    print("示例完成！")
    print("=" * 80)
