"""
历史运行记录管理器 — SQLite 元数据 + JSON 详细数据
"""

import sqlite3
import os
from datetime import datetime
from pathlib import Path
from .models import RunRecord, RunData

# 默认存储路径
DEFAULT_DATA_DIR = os.environ.get("HISTORY_DATA_DIR", "data/history")


class HistoryManager:
    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or DEFAULT_DATA_DIR)
        self.db_path = self.data_dir / "history.db"
        self.runs_dir = self.data_dir / "runs"
        self._init_db()

    # ==================== 初始化 ====================

    def _init_db(self):
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    factory_type TEXT NOT NULL,
                    algorithm TEXT,
                    config_name TEXT,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    duration_seconds REAL,
                    status TEXT DEFAULT 'running',
                    total_steps INTEGER DEFAULT 0,
                    final_efficiency REAL,
                    final_utilization REAL,
                    event_count INTEGER DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    data_file TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS run_metrics_summary (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id),
                    avg_efficiency REAL,
                    max_efficiency REAL,
                    min_efficiency REAL,
                    avg_utilization REAL,
                    max_utilization REAL,
                    min_utilization REAL,
                    total_machines INTEGER,
                    total_agvs INTEGER,
                    total_jobs INTEGER,
                    error_events INTEGER DEFAULT 0,
                    warning_events INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_runs_start_time ON runs(start_time);
                CREATE INDEX IF NOT EXISTS idx_runs_factory_type ON runs(factory_type);
                CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
            """)

    # ==================== 写入 ====================

    def create_run(self, factory_type: str, algorithm: str = '',
                   config_name: str = '') -> RunRecord:
        run_id = f"run_{int(datetime.now().timestamp() * 1000)}"
        record = RunRecord(
            id=run_id,
            factory_type=factory_type,
            algorithm=algorithm,
            config_name=config_name,
            start_time=datetime.now().isoformat(),
            status='running',
            data_file=f"runs/{run_id}.json",
        )
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """INSERT INTO runs
                   (id, factory_type, algorithm, config_name, start_time, status, data_file)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (record.id, record.factory_type, record.algorithm,
                 record.config_name, record.start_time, record.status,
                 record.data_file)
            )
        return record

    def complete_run(self, run_id: str, metrics_summary: dict = None,
                     run_data: RunData = None):
        now = datetime.now().isoformat()

        # 保存 JSON
        if run_data:
            run_data.meta['end_time'] = now
            filepath = self.runs_dir / f"{run_id}.json"
            run_data.save(str(filepath))

        ms = metrics_summary or {}
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """UPDATE runs SET
                   status='completed', end_time=?,
                   duration_seconds=?, total_steps=?,
                   final_efficiency=?, final_utilization=?,
                   event_count=?, error_count=?
                   WHERE id=?""",
                (now,
                 ms.get('duration_seconds', 0),
                 ms.get('total_steps', 0),
                 ms.get('final_efficiency', 0),
                 ms.get('final_utilization', 0),
                 ms.get('event_count', 0),
                 ms.get('error_count', 0),
                 run_id)
            )
            if ms:
                conn.execute(
                    """INSERT OR REPLACE INTO run_metrics_summary
                       (run_id, avg_efficiency, max_efficiency, min_efficiency,
                        avg_utilization, max_utilization, min_utilization,
                        total_machines, total_agvs, total_jobs,
                        error_events, warning_events)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run_id,
                     ms.get('avg_efficiency'),
                     ms.get('max_efficiency'),
                     ms.get('min_efficiency'),
                     ms.get('avg_utilization'),
                     ms.get('max_utilization'),
                     ms.get('min_utilization'),
                     ms.get('total_machines'),
                     ms.get('total_agvs'),
                     ms.get('total_jobs'),
                     ms.get('error_events', 0),
                     ms.get('warning_events', 0))
                )

    def append_run_data(self, run_id: str, metrics: dict = None,
                        event: dict = None, snapshot: dict = None):
        filepath = self.runs_dir / f"{run_id}.json"
        if filepath.exists():
            run_data = RunData.load(str(filepath))
        else:
            run_data = RunData(meta={'run_id': run_id})

        if metrics:
            run_data.metrics_timeline.append(metrics)
        if event:
            run_data.events.append(event)
        if snapshot:
            run_data.key_snapshots.append(snapshot)

        run_data.save(str(filepath))

    # ==================== 查询 ====================

    def list_runs(self, limit: int = 20, offset: int = 0,
                  factory_type: str = None) -> list:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM runs"
            params = []
            if factory_type:
                query += " WHERE factory_type=?"
                params.append(factory_type)
            query += " ORDER BY start_time DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            return [dict(row) for row in conn.execute(query, params)]

    def get_run(self, run_id: str) -> dict:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            # 加载 JSON 详情
            data_file = self.data_dir / result['data_file']
            if data_file.exists():
                result['data'] = RunData.load(str(data_file))
            return result

    def get_run_logs(self, run_id: str, event_type: str = None,
                     limit: int = 100) -> list:
        run = self.get_run(run_id)
        if not run or 'data' not in run:
            return []
        events = run['data'].events
        if event_type:
            events = [e for e in events if e.get('type') == event_type]
        return events[-limit:]

    def compare_runs(self, run_ids: list) -> dict:
        runs = []
        for rid in run_ids:
            run = self.get_run(rid)
            if run:
                runs.append(run)

        if not runs:
            return {"total_runs": 0}

        return {
            "total_runs": len(runs),
            "best_efficiency": max((r.get('final_efficiency', 0) or 0) for r in runs),
            "best_utilization": max((r.get('final_utilization', 0) or 0) for r in runs),
            "avg_duration": sum(r.get('duration_seconds', 0) or 0 for r in runs) / len(runs),
        }

    def delete_run(self, run_id: str):
        filepath = self.runs_dir / f"{run_id}.json"
        if filepath.exists():
            filepath.unlink()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("DELETE FROM run_metrics_summary WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM runs WHERE id=?", (run_id,))

    def get_stats(self) -> dict:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute("SELECT COUNT(*) as c FROM runs").fetchone()['c']
            types = [dict(r) for r in conn.execute(
                "SELECT DISTINCT factory_type FROM runs"
            ).fetchall()]
            return {
                "total_runs": total,
                "factory_types": [t['factory_type'] for t in types],
            }
