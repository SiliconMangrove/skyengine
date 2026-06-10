from dataclasses import dataclass, field, asdict
import json
import os


@dataclass
class RunRecord:
    """运行记录 — 对应 SQLite runs 表"""
    id: str
    factory_type: str
    algorithm: str = ''
    config_name: str = ''
    start_time: str = ''
    end_time: str = ''
    duration_seconds: float = 0
    status: str = 'running'
    total_steps: int = 0
    final_efficiency: float = 0
    final_utilization: float = 0
    event_count: int = 0
    error_count: int = 0
    data_file: str = ''


@dataclass
class RunData:
    """单次运行完整数据 — 存储为 JSON 文件"""
    meta: dict
    metrics_timeline: list = field(default_factory=list)
    events: list = field(default_factory=list)
    key_snapshots: list = field(default_factory=list)

    def save(self, filepath: str):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, filepath: str) -> 'RunData':
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls(**data)
