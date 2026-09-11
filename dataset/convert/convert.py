from xml.sax import parse

import yaml


def parse_brandimarte_data(filepath):
    with open(filepath, 'r', encoding="utf-8") as f:
        lines = f.readlines()

    job_count, machine_count, agv_count = map(int, lines[0].strip().split())

    jobs = []
    # 第2行：工序-机器数据
    job_lines = lines[1:1 + job_count]
    for job_idx, item in enumerate(job_lines):
        ops_raw = list(map(int, item.strip().split()))
        ops = []
        idx = 1
        for _ in range(ops_raw[0]):  # 重复这么多次读取工序
            choose_machine_count = ops_raw[idx]
            idx += 1
            machines = []
            for _ in range(choose_machine_count):
                machine_id = ops_raw[idx]
                duration = ops_raw[idx + 1]
                machines.append((machine_id, duration))
                idx += 2
            ops.append(machines)
        jobs.append((job_idx, ops))

    # 机器分布（假设数量是和op_count一样多）
    machine_lines = lines[2:2 + job_count]
    machine_positions = []
    for line in machine_lines:
        x, y = map(int, line.strip().split())
        machine_positions.append((x, y))

    # AGV数据
    agv_lines = lines[-agv_count:]
    agvs = []
    for line in agv_lines:
        x, y, v = map(int, line.strip().split())
        agvs.append((x, y, v))

    return {
        "job_count": job_count,
        "machine_count": machine_count,
        "agv_count": agv_count,
        "jobs": jobs,
        "machine_positions": machine_positions,
        "agvs": agvs
    }


def generate_map_config(parsed_data):
    machines = [
        {
            "machine": {
                "id": idx + 1,
                "type": "packet_factory.Machine",
                "coordinate": [x, y]
            }
        }
        for idx, (x, y) in enumerate(parsed_data['machine_positions'])
    ]

    agvs = [
        {
            "agv": {
                "type": "packet_factory.Agv",
                "coordinate": [x, y],
                "velocity": v,
                "capacity": 12
            }
        }
        for (x, y, v) in parsed_data['agvs']
    ]

    map_config = {
        "config": {
            "width": 20,
            "height": 30,
            "block_count": 0,
            "point_count": 0,
            "machine_count": len(machines),
            "path_count": 0,
            "agv_count": len(agvs),
            "blocks": [],
            "points": [],
            "machines": machines,
            "paths": [],
            "agvs": agvs
        }
    }
    return map_config

def generate_job_config(parsed_data):
    jobs_yaml = []
    for job_id, operations in parsed_data["jobs"]:
        job_entry = {
            "job": {
                "id": job_id,
                "operations": []
            }
        }
        for op_idx, machine_options in enumerate(operations):
            op_entry = {
                "id": op_idx,
                "candidates": [
                    {"machine": m, "duration": d}
                    for m, d in machine_options
                ]
            }
            job_entry["job"]["operations"].append(op_entry)
        jobs_yaml.append(job_entry)

    job_config = {
        "job_config": {
            "job_count": parsed_data["job_count"],
            "machine_count": parsed_data["machine_count"],
            "jobs": jobs_yaml
        }
    }

    return job_config


def write_yaml_config(data, out_path):
    with open(out_path, "w") as f:
        yaml.dump(data, f, sort_keys=False)


# 示例用法
if __name__ == "__main__":
    parsed = parse_brandimarte_data("../agv-instances/brandimarte/simple_agv.txt")
    print(parsed)
    map_config = generate_map_config(parsed)
    job_config = generate_job_config(parsed)
    print(map_config)
    print(job_config)
    write_yaml_config(map_config, "map_config_test.yaml")
    write_yaml_config(job_config, "job_config_test.yaml")