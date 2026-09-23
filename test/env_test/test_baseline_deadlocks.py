"""Regressions for stale reservations, shared ports and finite-buffer admission."""
from __future__ import annotations

import unittest
from pogema import GridConfig
from sky_executor.grid_factory.factory.assign_env import PogemaLifeLongWithAssign
from sky_executor.grid_factory.factory.Utils.structure import Job, Machine, Operation, RoutingTask
from experiment.algorithm_platform_plugins.dfjsp_t.buffer_safety import safe_to_admit
from experiment.algorithm_platform_plugins.dfjsp_t.pibt import PIBTRouter


class BaselineDeadlocksTest(unittest.TestCase):
    def make_environment(self) -> PogemaLifeLongWithAssign:
        env = PogemaLifeLongWithAssign(
            GridConfig(map=[[0] * 5 for _ in range(5)], agents_xy=[(2, 2)], targets_xy=[(2, 2)],
                       obs_radius=2, on_target="restart", seed=7),
            material_handling_config={"raw_material_source": [2, 2], "finished_goods_destination": [4, 4]},
        )
        env.grid_config.possible_targets_xy = [(2, 2)]
        env.reset(seed=7)
        env.machine_reset([Machine(0, (2, 2))])
        env.job_reset([Job(0, [Operation(0, 0, [0], [(0, 2)], proc_time=2, priority=200)], priority=200)])
        return env

    def test_same_machine_urgent_arrival_releases_reservation(self):
        env = self.make_environment()
        env.job_step({"transfer_requests": [{"task_id": 0, "job_id": 0, "op_id": 0,
                      "source": (2, 2), "destination": (2, 2), "ready_time": 0, "priority": 200}]})
        self.assertEqual(env.machines[0].urgent_reservations, {(0, 0)})
        env.task_step({})
        self.assertEqual(env.machines[0].urgent_reservations, set())
        env.machine_process()
        normal = Operation(1, 0, [0], [(0, 10)], proc_time=10, nominal_proc_time=10)
        env.machines[0].input_queue.append(normal)
        env.machine_process()
        self.assertEqual(normal.status, "PROCESSING")

    def test_full_dropoff_does_not_lock_vehicle_on_port(self):
        env = self.make_environment()
        machine = env.machines[0]
        machine.buffer_capacity = 1
        machine.buffer_jobs = {9}
        task = RoutingTask(task_id=0, job_id=0, op_id=0, source=(0, 0), destination=(2, 2), ready_time=0)
        env.agv_current_task[0] = task
        env.agv_loaded[0] = True
        env.agv_task_phase[0] = "TO_DROPOFF"
        env._settle_arrival(0)
        self.assertEqual(env.agv_task_phase[0], "TO_DROPOFF")
        self.assertEqual(env.agv_handling_remaining[0], 0)
        before = tuple(env.grid.positions_xy[0])
        env.step([1])
        self.assertNotEqual(tuple(env.grid.positions_xy[0]), before)
        self.assertTrue(env.agv_loaded[0])
        self.assertEqual(machine.buffer_jobs, {9})

    def test_bankers_admission_prevents_circular_material_wait(self):
        def op(oid, mid, status="PENDING"):
            return {"op_id": oid, "status": status, "assigned_machine": mid if status == "FINISHED" else None,
                    "machine_options_with_time": [[mid, 1]]}
        state = {"machines": [{"id": 0, "buffer_capacity": 1}, {"id": 1, "buffer_capacity": 1}],
                 "jobs": [{"job_id": 0, "ops": [op(0, 0, "FINISHED"), op(1, 1)]},
                          {"job_id": 1, "ops": [op(0, 1), op(1, 0)]}]}
        claims = {0: {0}, 1: set()}
        self.assertFalse(safe_to_admit(state, claims, 1, 0, 1))
        self.assertTrue(safe_to_admit(state, claims, 0, 1, 1))
        self.assertEqual(claims, {0: {0}, 1: set()})

    def test_pibt_yields_idle_port_without_vertex_or_edge_collision(self):
        task = {"task_id": 0, "source": [2, 2], "destination": [4, 4], "kind": "finished_goods"}
        agents = [{"id": 0, "pos": [2, 1], "current_task": task, "task_phase": "TO_PICKUP", "status": "OK"},
                  {"id": 1, "pos": [2, 2], "current_task": None, "task_phase": "IDLE", "status": "OK"}]
        state = {"grid": [[0] * 5 for _ in range(5)], "moves": [[0, 0], [-1, 0], [1, 0], [0, -1], [0, 1]],
                 "agents": agents, "machines": [{"location": [2, 2], "buffer_jobs": [0], "buffer_capacity": 1}],
                 "jobs": []}
        route = PIBTRouter(7).plan(state, set())
        next_positions = []
        for agent, move in zip(agents, route):
            dx, dy = state["moves"][move["path"][0]]
            next_positions.append((agent["pos"][0] + dx, agent["pos"][1] + dy))
        self.assertEqual(next_positions[0], (2, 2))
        self.assertNotEqual(next_positions[1], (2, 2))
        self.assertNotEqual(next_positions[1], (2, 1))


if __name__ == "__main__":
    unittest.main()
