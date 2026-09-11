'''
@Project ：SkyEngine
@File    ：env_const.py
@IDE     ：PyCharm
@Author  ：Skyrimforest
@Date    ：2025/10/9 20:46
'''
import os
from enum import Enum

# ──────────────────── Padding Cap（批处理训练用） ────────────────────
# 空闲 AGV 最大数量上限，超出截断，不足 padding
MAX_N_IDLE = int(os.environ.get("SKY_MAX_N_IDLE", 16))
# 待分配任务最大数量上限
MAX_N_TASK = int(os.environ.get("SKY_MAX_N_TASK", 64))


class ObsType(Enum):
    DEFAULT = 'default'  # 默认的array
    MAPF = 'MAPF'  # 全局观察
    POMAPF = 'POMAPF'  # 局部观察


class OnTargetType(Enum):
    RESTART = "restart"
    NOTHING = "nothing"
    FINISH = "finish"


class CollisionSystem(Enum):
    BLOCK = "block_both"
    PRIORITY = "priority"
    SOFT = "soft"


class ActionType(Enum):
    WAIT = 0
    UP = 1
    DOWN = 2
    LEFT = 3
    RIGHT = 4
