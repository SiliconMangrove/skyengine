# 障碍物注入：AGV 忽略障碍物/机器的根因与修复方案

## 一、现象

`grid_factory`（以及任何走 grouppro 后端 `joint_sim` 路径的工厂）执行仿真时，AGV 完全无视配置里定义的障碍物和机器位置，在空网格上自由穿行。

## 二、根因

### 2.1 pogema 的障碍物入参方式

pogema `GridConfig` 支持三种障碍物来源（互斥）：

| 字段 | 含义 |
|------|------|
| `density` | 随机障碍物密度（0~1），按 `seed` 生成 |
| `map` | 字符串网格（`#` 障碍 / `.` 自由），预设障碍图 |
| `map_name` | pogema 内置预设地图名 |

三者都不给 → 全空网格。

### 2.2 grouppro 后端走的是"全空"路径

`application/backend/joint_sim/io/use_io.py:43-78` 的 `parse_grid_config`：

```python
return GridConfig(
    size=grid_size,
    num_agents=num_agents,
    density=0.0,          # ← 随机障碍=0
    seed=42,
    max_episode_steps=256,
    obs_radius=5,
    on_target="restart",
    agents_xy=agents_start_xy if agents_start_xy else None,
    targets_xy=(agents_start_xy if agents_start_xy else None),
)
```

- `density=0.0`：无随机障碍。
- **没传 `map`**：没用预设障碍图。
- **完全没读 `topology.zones`**：配置里画的 `type: "obstacle"` 区域被整段丢弃。

结果：pogema 拿到一个**全空正方形网格**，AGV 当然随便穿。

`use_io.py:201` 把机器注册成 `possible_targets_xy`（AGV 的**目的地**），所以机器也不挡路——AGV 会直冲机器格，因为那是目标。

### 2.3 调用链确认

`grid_factory_proxy.py:98`：
```python
self.env = create_env_from_config(self._config, random_target=False)
```
所有走 grouppro 后端的工厂（grid_factory / packet_factory 等）都进 `use_io.py`，都中招。

## 三、三个入口的障碍物处理对比

调研了三个环境入口：

### 3.1 `grouppro/.../joint_sim/io/use_io.py`（❌ 有问题）

- 路径：工厂配置 dict → `create_env_from_config` → `parse_grid_config`。
- 障碍物：**全部丢失**（`density=0`，不读 zones，不接 MAPF map）。
- 服务于：grouppro 后端的所有工厂仿真（grid_factory_proxy、packet_factory 后端等）。

### 3.2 `finalpro/SkyEngine/sim_server.py`（❌ 同样有问题）

- `sim_server.py:13` 注释明说："配置解析逻辑和 use_io.py (joint_sim) 保持一致"。
- `create_env_from_config`（36-109 行）和 use_io.py 几乎逐行复制：`density=0.0`、不读 zones、机器作 `possible_targets_xy`。
- **同一个 bug**，且是**故意保持同步**的——修 use_io.py 时必须同步修这里，否则两套行为分叉。

### 3.3 `finalpro/SkyEngine/run.py`（✓ 正确，参考实现）

- `parse_mapf_instance`（62-71 行）读 MAPF YAML 地图，返回 `{"map": map_instance}`。
- `run.py:197-207`：
  ```python
  grid_cfg = GridConfig(
      map=map,               # ← 障碍图正确注入
      num_agents=...,
      ...
  )
  ```
- 障碍物来源是 **MAPF 数据集地图**（pogema benchmark 的字符串网格），不是工厂配置的 zones。
- 这条路径的 AGV **会**绕障碍物。

### 3.4 关键差异

`run.py` 走"MAPF 实例驱动"——障碍物来自数据集地图（`dataset/map_dataset/.../maps.yaml`）。
`use_io.py` / `sim_server.py` 走"工厂配置驱动"——障碍物**本应**来自 `topology.zones[type=obstacle]`，但现在没读。

两条路径的障碍物**数据源不同**，这是设计修复时要注意的。

## 四、修复方案：只补障碍物

目标：让 AGV 绕开障碍物，**不**改机器的"目标"语义（机器仍是目的地，不挡路）。

### 4.1 障碍物数据源

修复时按工厂类型分两个来源：

| 工厂 | 障碍物来源 | 现状 |
|------|-----------|------|
| packet_factory / northeast（有 zones） | `topology.zones[type=obstacle]` | use_io.py 没读 |
| grid_factory（0 zones） | 选中的 MAPF 数据集地图 | use_io.py 没接 |

> 注：实测 `config/grid_factory.json` 与 `public/grid_factory.json` 都是 **0 zones**。grid_factory 的障碍物**只能**来自 MAPF 地图，不来自 zones。

### 4.2 方案 A：zones 驱动（适用 packet/northeast）

在 `parse_grid_config` 里把 obstacle zone 展开成 pogema `map` 字符串。

**新增辅助函数**（建议放 `use_io.py`）：

```python
def build_obstacle_map(topology, machine_positions, agent_positions):
    """
    从 topology.zones[type=obstacle] 构建 pogema 字符串地图。
    机器格与 AGV 起点格强制保持可通行（AGV 必须能到达）。
    """
    gw = topology.get("gridWidth", 20)
    gh = topology.get("gridHeight", 20)
    size = max(gw, gh)  # 与 parse_grid_config 的方正化一致

    # 1. 展开 obstacle zone 为格子集合
    blocked = set()
    for z in topology.get("zones", []):
        if z.get("type") != "obstacle":
            continue
        area = z.get("area", {})
        x0, y0 = area.get("x", 0), area.get("y", 0)
        w, h = area.get("w", 0), area.get("h", 0)
        for dy in range(h):
            for dx in range(w):
                blocked.add((x0 + dx, y0 + dy))

    # 2. 机器格、AGV 起点格不能是障碍（否则 AGV 永远到不了目标）
    for mx, my in machine_positions:
        blocked.discard((int(mx), int(my)))
    for ax, ay in agent_positions:
        blocked.discard((int(ax), int(ay)))

    # 3. 拼 size×size 字符串：行=y，列=x
    OBS, FREE = GridConfig.OBSTACLE, GridConfig.FREE  # '#' / '.'
    rows = []
    for y in range(size):
        rows.append("".join(OBS if (x, y) in blocked else FREE for x in range(size)))
    return "\n".join(rows)
```

**改 `parse_grid_config`**：把 `density=0.0` + `size=...` 换成 `map=...`：

```python
def parse_grid_config(config):
    topology = config.get("topology", {})
    agvs = config.get("agvs", [])
    agents_start_xy = [tuple(agv["initialLocation"]) for agv in agvs]

    # 机器位置（用于排除：机器格不挡路）
    machines = topology.get("machines", {})
    machine_positions = [tuple(m["location"]) for m in machines.values()]

    obstacle_map = build_obstacle_map(topology, machine_positions, agents_start_xy)

    return GridConfig(
        map=obstacle_map,          # ← 障碍图注入（提供 map 时 size/density 由 pogema 自行推导）
        num_agents=len(agvs),
        seed=42,
        max_episode_steps=256,
        obs_radius=5,
        on_target="restart",
        agents_xy=agents_start_xy if agents_start_xy else None,
        targets_xy=agents_start_xy if agents_start_xy else None,
    )
```

**注意点**：
- 提供了 `map` 后，`size` 和 `density` 由 pogema 从 map 推导，不要再显式传 `size`/`density`（避免冲突）。
- 坐标方向：pogema map 字符串的 `行=y, 列=x`，与 zone area 的 `(x,y,w,h)` 约定一致；但**需实测确认** y 轴是否翻转（行 0 是顶部还是底部）。若障碍物位置视觉上对调，把 `for y in range(size)` 反转即可。
- 机器仍是 `possible_targets_xy`（在 `create_env_from_config:201` 设置），语义不变——AGV 会开到机器格上，机器格在 map 里已强制为 `.`。

### 4.3 方案 B：MAPF 地图驱动（适用 grid_factory）

grid_factory 的障碍物来自数据集地图（`dataset/.../maps.yaml` 里的 `validation-random-seed-XXX`）。这条路径**已经**在 `run.py` 里正确实现，但 `use_io.py` / `sim_server.py` 没接。

修复需要把"选中的 MAPF 地图"从**前端选地图 → 后端 proxy → use_io.py** 这条链路打通：

1. 前端选地图后，把 `map_name`（或整张 map 字符串）随配置下发。
2. `grid_factory_proxy` 把 map 透传给 `create_env_from_config`。
3. `parse_grid_config` 检测到 config 里带 `mapf_map` 字段时，优先用 `GridConfig(map=mapf_map, ...)`，跳过 zones 构建。

> 这一步**链路较长**，建议作为方案 A 之后的二期。先把 zones 驱动补上（packet/northeast 立刻见效），grid_factory 的 MAPF 地图注入再单独排期。

### 4.4 sim_server.py 必须同步改

`sim_server.py:36-109` 的 `create_env_from_config` 是 use_io.py 的复刻（注释 line 13 明确声明保持一致）。修 use_io.py 时**必须**把同样的 `build_obstacle_map` 和 `parse_grid_config` 改动同步过去，否则 finalpro 容器内的仿真和 grouppro 后端行为分叉。

建议：把 `build_obstacle_map` 抽到一个共享工具模块，两边 import，从根上杜绝分叉。

## 五、改动清单

| 文件 | 改动 | 方案 |
|------|------|------|
| `application/backend/joint_sim/io/use_io.py` | 新增 `build_obstacle_map`；改 `parse_grid_config` 用 `map=` | A |
| `finalpro/SkyEngine/sim_server.py` | 同步 `build_obstacle_map` + `create_env_from_config` 改动 | A |
| （可选）共享工具模块 | 抽出 `build_obstacle_map` 供两边 import，避免分叉 | A |
| `grid_factory_proxy.py` + 前端选地图链路 | 透传 MAPF map 到 `create_env_from_config` | B（二期） |

## 六、验证

1. **packet_factory / northeast**：配置里有 `obstacle_1`（墙，area x=6,y=1,w=1,h=7）。改完后执行仿真，AGV 应绕开这堵墙，不再穿过。
2. **grid_factory**：方案 A 不影响（它 0 zones）；等方案 B 落地后，选 `validation-random-seed-002` 执行，AGV 应绕开地图里的 `#` 障碍。
3. **回归**：机器仍可达（AGV 能到机器格上料），不因障碍物死锁。
4. **坐标方向**：首次运行观察障碍物位置是否与配置一致；若上下颠倒，反转 map 字符串的行序。

## 七、风险

- **坐标轴方向不确定**：pogema map 行序与 zone `(x,y)` 约定的对应关系需实测。建议先在 packet_factory（已知 obstacle_1 位置）验证一次。
- **机器格冲突**：若某机器格恰好落在 obstacle zone 内部，方案 A 会强制把它设为可通行（`discard`），导致 map 里出现"障碍物上开个洞"——视觉上可能突兀，但保证 AGV 可达。若不接受，需在配置层面避免机器与障碍重叠。
- **sim_server 同步**：忘记同步会导致 grouppro 后端和 finalpro 容器行为不一致。
