"""
可选的磁盘持久化模块：
- 训练时不启用（纯内存）
- 离线分析 / benchmark 时启用，按步保存 JSON
"""

import json
import os
from datetime import datetime


class PersistenceManager:
    """按步保存 metrics 到 JSON 文件"""

    def __init__(self, base_dir: str = None):
        if base_dir is None:
            import config
            base_dir = getattr(config, 'METRICS_DIR', './sky_logs/metrics')
        self._base_dir = base_dir
        self._experiment_dir = None

    def _ensure_experiment_dir(self):
        if self._experiment_dir is None:
            os.makedirs(self._base_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._experiment_dir = os.path.join(self._base_dir, timestamp)
            os.makedirs(self._experiment_dir, exist_ok=True)

    def save_step(self, step_num: int, metrics: dict):
        """保存某一步的指标"""
        self._ensure_experiment_dir()
        step_dir = os.path.join(self._experiment_dir, f"{step_num:04d}")
        os.makedirs(step_dir, exist_ok=True)
        path = os.path.join(step_dir, "metrics.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
        return path

    def save_episode(self, summary: dict, filename: str = "episode_summary.json"):
        """保存 episode 汇总"""
        self._ensure_experiment_dir()
        path = os.path.join(self._experiment_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        return path
