import json
import tempfile
import unittest
from pathlib import Path

from application.backend.analysis.service import AnalysisService, collect_insertion_requests


class AnalysisServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.archive_dir = Path(self.temp_dir.name)
        self.service = AnalysisService(self.archive_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_collect_insertion_requests_keeps_latest_phase(self):
        frames = [
            {"insertion_requests": [
                {"request_id": "insert-0001", "accepted_step": 2, "phase": "queued"},
            ]},
            {"insertion_requests": [
                {"request_id": "insert-0001", "accepted_step": 2, "phase": "completed"},
            ]},
        ]
        records = collect_insertion_requests(frames)
        self.assertEqual(records, [
            {"request_id": "insert-0001", "accepted_step": 2, "phase": "completed"},
        ])

    def test_saved_run_contains_replayable_insertion_records(self):
        payload = {
            "factory_id": "grid_factory_new",
            "algorithm": "pso+astar+nearest",
            "frames": [
                {"env_timeline": "T+2s", "jobs": [], "insertion_requests": [
                    {"request_id": "insert-0001", "accepted_step": 2, "phase": "queued"},
                ]},
                {"env_timeline": "T+9s", "jobs": [], "insertion_requests": [
                    {"request_id": "insert-0001", "accepted_step": 2, "phase": "completed"},
                ]},
            ],
            "metricsTimeline": [],
            "events": [{"type": "job_insertion_phase_changed", "step": 2}],
        }
        result = self.service.save_run(payload)
        saved = self.service.get_run(result["id"])
        self.assertEqual(saved["schema_version"], "1.1")
        self.assertEqual(saved["insertionRequests"][0]["phase"], "completed")
        self.assertEqual(saved["summary"]["insertion_count"], 1)
        self.assertEqual(saved["summary"]["insertion_completed"], 1)

    def test_save_run_rejects_empty_frames(self):
        with self.assertRaisesRegex(ValueError, "at least one state frame"):
            self.service.save_run({
                "factory_id": "grid_factory_new",
                "frames": [],
                "metricsTimeline": [],
                "events": [],
            })

    def test_list_runs_hides_legacy_empty_archives(self):
        empty_path = self.archive_dir / "empty.json"
        empty_path.write_text(json.dumps({
            "id": "empty",
            "factory_id": "grid_factory_new",
            "frames": [],
            "metricsTimeline": [],
            "events": [],
        }), encoding="utf-8")

        self.assertEqual(self.service.list_runs(), [])


if __name__ == "__main__":
    unittest.main()
