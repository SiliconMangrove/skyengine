'''
@Project ：SkyEngine
@File    ：diff.py
@IDE     ：PyCharm
@Author  ：Claude / Skyrimforestsea
@Date    ：2026/07/02

帧间 diff 派生事件模块（`sim_server._diff_and_emit` 升级落地）。

设计文档：explore/0701diff_and_emit升级.md
契约权威：docs/explore/0701日志更新.md（§1.3 stream 信封 / §4.2 category 词表 / §6.2 Rule 规范）

----------------------------------------------------------------------------
核心抽象
----------------------------------------------------------------------------
DiffEmitter.diff(cur_frame, step)
    调度器：按注册顺序跑规则、捕获异常降级、在 finally 中滚动 _prev_frame
    （关键不变量：规则异常不污染基线）。

DiffEmitter.emit_lifecycle(etype, step, ...)
    lifecycle 事件统一出口（sim_started / episode_completed / episode_truncated）。
    这些事件不走 diff 规则（触发条件不是 frame diff），但通过此出口保证信封 shape
    与 diff 事件一致 —— lifecycle 与 diff 共用 make_envelope。

make_envelope(step, timestamp, *, etype, title, message, ...)
    构造 canonical stream 信封。供 sim_started 订阅补发等「不发只造」场景直接调用。

----------------------------------------------------------------------------
Rule 协议
----------------------------------------------------------------------------
rule(prev_frame, cur_frame, ctx) -> Iterator[dict]
    - Rule 只 yield 「半成品事件」dict（键 = make_envelope 的 kwargs：etype / title /
      message，可选 level / payload）。step / timestamp 由调度器从 ctx 注入。
    - Rule 绝不直接广播、绝不抛异常出去（异常会被调度器捕获并降级为 system 事件）。
    - Rule 可选实现 .reset()，用于 episode 边界清理自持状态（仅 R6 用到）。
    - 字段缺失必须用 .get() 容忍，不假设 frame 完整。

ctx 只读：{"step": int, "timestamp": "T+{step}s"}。规则不应写入 ctx。

----------------------------------------------------------------------------
单文件数据/代码分层
----------------------------------------------------------------------------
§A 数据层  category 常量、level 常量、type→category 词表、消息模板、标题、阈值
§B 工具层  _op_key（op 归约）、make_envelope（信封归一化）
§C 规则层  R1-R6 六个规则（R6 为唯一阈值规则，单独 stateful）
§D 调度层  default_rules（顺序装配）、DiffEmitter（调度器）

改文案 / 阈值 / 分类：动 §A。
加事件类型：在 §A 的 TYPE_REGISTRY 登记 type + §C 写规则函数 + §D 进列表。
'''

from typing import Callable, Iterator


# ============================================================
# §A 数据层：常量、词表、模板、阈值
# ============================================================

# ---- category（服从 0701日志更新 §4.2）----
CATEGORY_LIFECYCLE  = "lifecycle"
CATEGORY_MACHINE_OP = "machine_op"
CATEGORY_TRANSFER   = "transfer"
CATEGORY_JOB        = "job"
CATEGORY_AGV        = "agv"
CATEGORY_RISK       = "risk"
CATEGORY_SYSTEM     = "system"
CATEGORY_EXCEPTION  = "exception"

# ---- level ----
LEVEL_INFO    = "info"
LEVEL_SUCCESS = "success"
LEVEL_WARNING = "warning"
LEVEL_ERROR   = "error"

# ---- type → (category, default level) 词表 ----
# 所有合法事件 type 必须在此登记（约束 C7）。新增 type 同步更新此处 + 前端 eventIconMap。
TYPE_REGISTRY = {
    # lifecycle（不走 diff，走 emit_lifecycle，但信封一致）
    "sim_started":         (CATEGORY_LIFECYCLE,  LEVEL_INFO),
    "episode_completed":   (CATEGORY_LIFECYCLE,  LEVEL_SUCCESS),
    "episode_truncated":   (CATEGORY_LIFECYCLE,  LEVEL_WARNING),
    # machine_op
    "machine_start_op":    (CATEGORY_MACHINE_OP, LEVEL_INFO),
    "machine_idle":        (CATEGORY_MACHINE_OP, LEVEL_SUCCESS),
    "op_finished":         (CATEGORY_MACHINE_OP, LEVEL_SUCCESS),
    # transfer
    "transfer_started":    (CATEGORY_TRANSFER,   LEVEL_INFO),
    "transfer_phase_changed": (CATEGORY_TRANSFER, LEVEL_INFO),
    "transfer_completed":  (CATEGORY_TRANSFER,   LEVEL_SUCCESS),
    # job
    "job_completed":       (CATEGORY_JOB,        LEVEL_SUCCESS),
    "job_replan_started":  (CATEGORY_JOB,        LEVEL_INFO),
    "urgent_job_arrival":  (CATEGORY_JOB,        LEVEL_WARNING),
    "job_replan_completed": (CATEGORY_JOB,       LEVEL_SUCCESS),
    "job_replan_failed":   (CATEGORY_JOB,        LEVEL_ERROR),
    "job_insertion_phase_changed": (CATEGORY_JOB, LEVEL_INFO),
    "job_preempted":       (CATEGORY_JOB,        LEVEL_WARNING),
    "job_resumed":         (CATEGORY_JOB,        LEVEL_INFO),
    # agv
    "agv_state":           (CATEGORY_AGV,        LEVEL_INFO),
    # risk（唯一阈值类）
    "deadline_risk":       (CATEGORY_RISK,       LEVEL_WARNING),
    # system（规则异常降级）
    "diff_rule_error":     (CATEGORY_SYSTEM,     LEVEL_WARNING),
    # exception（环境动力学层主动注入的异常扰动，由 sim_server 从 infos["events"] 转译）
    "machine_breakdown":   (CATEGORY_EXCEPTION,  LEVEL_WARNING),
    "machine_recovery":    (CATEGORY_EXCEPTION,  LEVEL_INFO),
    "agv_breakdown":       (CATEGORY_EXCEPTION,  LEVEL_WARNING),
    "agv_recovery":        (CATEGORY_EXCEPTION,  LEVEL_INFO),
    "temporary_obstacle":  (CATEGORY_EXCEPTION,  LEVEL_WARNING),
    "obstacle_clear":      (CATEGORY_EXCEPTION,  LEVEL_INFO),
}

# ---- 阈值默认值（R6）----
DEFAULT_RISK_ALPHA = 0.8   # step >= due * alpha 触发交期风险预警

# ---- 消息模板（文案集中管理，便于统一改稿 / i18n）----
MSG = {
    # machine_op
    "machine_start":      "M{mid} 开始 Job{job_id}-Op{op_id}",
    "machine_switch":     "M{mid} 切换至 Job{job_id}-Op{op_id}",
    "machine_idle":       "M{mid} 完成加工，进入空闲",
    "op_finished":        "Job{job_id}-Op{op_id} 完成",
    # transfer
    "transfer_started":   "AGV 取货 Task{task_id} (Job{job_id}-Op{op_id}) {src} {dst}",
    "transfer_phase_changed": "Task{task_id} 阶段切换: {old_phase} → {new_phase}",
    "transfer_completed": "AGV 完成 Task{task_id}",
    # job
    "job_completed":      "Job{job_id} 全部 Op 完成",
    "job_preempted":      "Job{job_id}-Op{op_id} 被特急任务暂停，剩余 {remaining} step",
    "job_resumed":        "Job{job_id}-Op{op_id} 恢复加工",
    # agv
    "agv_active":         "AGV-{i} 开始执行任务",
    "agv_idle":           "AGV-{i} 进入空闲",
    # risk
    "deadline_risk":      "Job{job_id} 接近交期，剩余 {remaining} 步",
    # lifecycle
    "sim_started":        "仿真已启动 (step={step})",
    "episode_completed":  "全部 Job 完成，episode 结束",
    "episode_truncated":  "达到步数上限，episode 截断",
    # system
    "rule_error":         "{rule}: {err}",
}

# ---- 标题（≤20 字短句，前端列表用）----
TITLE = {
    "machine_start_op":   "机器开始加工",
    "machine_idle":       "机器空闲",
    "op_finished":        "工序完成",
    "transfer_started":   "开始搬运",
    "transfer_phase_changed": "搬运阶段切换",
    "transfer_completed": "搬运完成",
    "job_completed":      "Job 完成",
    "job_preempted":      "加工被特急任务暂停",
    "job_resumed":        "加工恢复",
    "agv_state":          "AGV 状态切换",
    "deadline_risk":      "交期预警",
    "sim_started":        "仿真启动",
    "episode_completed":  "回合完成",
    "episode_truncated":  "回合截断",
    "diff_rule_error":    "规则异常",
}


# ============================================================
# §B 工具层
# ============================================================

def _op_key(op):
    """current_op 归约为 (job_id, op_id)，忽略 step_done / proc_time 等渐进字段。
    设计：避免渐进字段每步变化误触发跃迁。"""
    if op is None:
        return None
    return (op.get("job_id"), op.get("op_id"))


def make_envelope(step, timestamp, *, etype, title, message,
                  level=None, payload=None):
    """构造 canonical stream 信封（服从 0701日志更新 §1.3）。
    category / 默认 level 从 TYPE_REGISTRY 词表查；level 显式传入可覆盖。
    未登记的 etype 降级为 system warning，避免事件流无声漂移。"""
    if etype in TYPE_REGISTRY:
        category, default_level = TYPE_REGISTRY[etype]
    else:
        category, default_level = CATEGORY_SYSTEM, LEVEL_WARNING
    return {
        "step":      step,
        "timestamp": timestamp,
        "category":  category,
        "type":      etype,
        "level":     level if level is not None else default_level,
        "title":     title,
        "message":   message,
        "payload":   payload if payload is not None else {},
    }


# ============================================================
# §C 规则层：R1-R6
# 签名：(prev, cur, ctx) -> Iterator[dict]
# yield 的 dict = make_envelope 的 kwargs 子集（etype / title / message，可选 level / payload）
# ============================================================

# ---- R1 机器 op 状态机（纯跃迁，迁移自现状）----
def machine_op_rule(prev, cur, ctx):
    prev_ops = {m.get("id"): _op_key(m.get("current_op"))
                for m in prev.get("machines", [])}
    for m in cur.get("machines", []):
        mid = m.get("id")
        cur_key = _op_key(m.get("current_op"))
        old_key = prev_ops.get(mid)
        if old_key is None and cur_key is not None:
            job_id, op_id = cur_key
            yield {"etype": "machine_start_op",
                   "title": TITLE["machine_start_op"],
                   "message": MSG["machine_start"].format(mid=mid, job_id=job_id, op_id=op_id),
                   "payload": {"machine_id": mid, "job_id": job_id, "op_id": op_id}}
        elif old_key is not None and cur_key is None:
            yield {"etype": "machine_idle",
                   "title": TITLE["machine_idle"],
                   "message": MSG["machine_idle"].format(mid=mid),
                   "payload": {"machine_id": mid}}
        elif (old_key is not None and cur_key is not None
              and old_key != cur_key):
            job_id, op_id = cur_key
            yield {"etype": "machine_start_op",
                   "title": TITLE["machine_start_op"],
                   "message": MSG["machine_switch"].format(mid=mid, job_id=job_id, op_id=op_id),
                   "payload": {"machine_id": mid, "job_id": job_id, "op_id": op_id,
                               "prev_job_id": old_key[0], "prev_op_id": old_key[1]}}


# ---- R2 transfer 集合差（纯跃迁，迁移自现状）----
def transfer_rule(prev, cur, ctx):
    prev_tasks = {t.get("task_id"): t for t in prev.get("active_transfers", [])}
    cur_tasks = {t.get("task_id"): t for t in cur.get("active_transfers", [])}
    for tid, t in cur_tasks.items():
        if tid not in prev_tasks:
            src = t.get("source")
            dst = t.get("destination")
            dst_str = f"→ {dst}" if dst else ""
            yield {"etype": "transfer_started",
                   "title": TITLE["transfer_started"],
                   "message": MSG["transfer_started"].format(
                       task_id=tid, job_id=t.get("job_id"),
                       op_id=t.get("op_id"), src=src, dst=dst_str),
                   "payload": {"task_id": tid, "job_id": t.get("job_id"),
                                "op_id": t.get("op_id"),
                                "source": src, "destination": dst}}
        else:
            old_phase = prev_tasks[tid].get("phase")
            new_phase = t.get("phase")
            if old_phase != new_phase:
                yield {
                    "etype": "transfer_phase_changed",
                    "title": TITLE["transfer_phase_changed"],
                    "message": MSG["transfer_phase_changed"].format(
                        task_id=tid, old_phase=old_phase, new_phase=new_phase,
                    ),
                    "payload": {
                        "task_id": tid,
                        "agent_id": t.get("agent_id"),
                        "old_phase": old_phase,
                        "new_phase": new_phase,
                        "handling_remaining": t.get("handling_remaining", 0),
                        "loaded": t.get("loaded", False),
                    },
                }
    for tid in set(prev_tasks) - set(cur_tasks):
        yield {"etype": "transfer_completed",
               "title": TITLE["transfer_completed"],
               "message": MSG["transfer_completed"].format(task_id=tid),
               "payload": {"task_id": tid}}


# ---- R3 单道 op 完成跃迁（新增，工件视角）----
def op_finish_rule(prev, cur, ctx):
    prev_status = {}
    for job in prev.get("jobs", []):
        jid = job.get("job_id")
        for op in job.get("ops", []):
            prev_status[(jid, op.get("op_id"))] = op.get("status")
    for job in cur.get("jobs", []):
        jid = job.get("job_id")
        for op in job.get("ops", []):
            key = (jid, op.get("op_id"))
            old = prev_status.get(key)
            new = op.get("status")
            if new == "FINISHED" and old != "FINISHED":
                yield {"etype": "op_finished",
                       "title": TITLE["op_finished"],
                       "message": MSG["op_finished"].format(
                           job_id=jid, op_id=op.get("op_id")),
                       "payload": {"job_id": jid, "op_id": op.get("op_id"),
                                   "machine_id": op.get("assigned_machine"),
                                   "finish_process_at": op.get("finish_process_at")}}


def job_preemption_rule(prev, cur, ctx):
    prev_ops = {}
    for job in prev.get("jobs", []):
        jid = job.get("job_id")
        for op in job.get("ops", []):
            prev_ops[(jid, op.get("op_id"))] = op
    for job in cur.get("jobs", []):
        jid = job.get("job_id")
        for op in job.get("ops", []):
            key = (jid, op.get("op_id"))
            old = (prev_ops.get(key) or {}).get("status")
            new = op.get("status")
            if old == "PROCESSING" and new == "SUSPENDED":
                remaining = op.get("remaining_proc_time")
                yield {
                    "etype": "job_preempted",
                    "title": TITLE["job_preempted"],
                    "message": MSG["job_preempted"].format(
                        job_id=jid, op_id=op.get("op_id"), remaining=remaining,
                    ),
                    "payload": {
                        "job_id": jid,
                        "op_id": op.get("op_id"),
                        "remaining_proc_time": remaining,
                        "preemption_count": op.get("preemption_count", 0),
                    },
                }
            elif old == "SUSPENDED" and new == "PROCESSING":
                yield {
                    "etype": "job_resumed",
                    "title": TITLE["job_resumed"],
                    "message": MSG["job_resumed"].format(
                        job_id=jid, op_id=op.get("op_id"),
                    ),
                    "payload": {"job_id": jid, "op_id": op.get("op_id")},
                }


# ---- R4 Job 完成跃迁（新增）----
def job_completion_rule(prev, cur, ctx):
    prev_done = {j.get("job_id"): j.get("is_completed", False)
                 for j in prev.get("jobs", [])}
    for j in cur.get("jobs", []):
        jid = j.get("job_id")
        if not prev_done.get(jid, False) and j.get("is_completed", False):
            prog = j.get("progress", {}) or {}
            yield {"etype": "job_completed",
                   "title": TITLE["job_completed"],
                   "message": MSG["job_completed"].format(job_id=jid),
                   "payload": {"job_id": jid,
                               "completion_time": j.get("completion_time"),
                               "op_count": prog.get("total")}}


# ---- R5 AGV 活动状态跃迁（新增）----
def agv_state_rule(prev, cur, ctx):
    prev_active = prev.get("grid_state", {}).get("is_active", [])
    cur_active = cur.get("grid_state", {}).get("is_active", [])
    for i, cur_a in enumerate(cur_active):
        old_a = prev_active[i] if i < len(prev_active) else None
        if old_a is None or old_a == cur_a:
            continue
        if cur_a:
            yield {"etype": "agv_state", "level": LEVEL_INFO,
                   "title": TITLE["agv_state"],
                   "message": MSG["agv_active"].format(i=i),
                   "payload": {"agv_index": i, "from_active": old_a, "to_active": True}}
        else:
            yield {"etype": "agv_state", "level": LEVEL_INFO,
                   "title": TITLE["agv_state"],
                   "message": MSG["agv_idle"].format(i=i),
                   "payload": {"agv_index": i, "from_active": old_a, "to_active": False}}


# ---- R6 交期风险预警（唯一阈值规则，stateful）----
class DeadlineRiskRule:
    """R6 交期风险预警 —— 首个非纯跃迁规则（破坏「events 流是纯语义跃迁」原则的有意识让步）。

    特殊性：
      1. 非纯跃迁：依赖 step 与 due 的阈值比较，非 prev/cur diff。
      2. 去重必需：同一 job 一个 episode 只发一次（_emitted 集合）。
      3. 降级路径：job 完成后从 _emitted 移除；reset() 清空整个集合（episode 边界）。

    用 callable class 而非纯函数，是因为去重状态天然属于规则自身。
    reset() 让 DiffEmitter.reset() 能透明地传播 episode 边界清理。
    """

    def __init__(self, alpha=DEFAULT_RISK_ALPHA):
        self.alpha = alpha
        self._emitted = set()

    def reset(self):
        self._emitted.clear()

    def __call__(self, prev, cur, ctx):
        step = ctx["step"]
        for j in cur.get("jobs", []):
            jid = j.get("job_id")
            due = j.get("due")
            if due is None:
                continue
            if j.get("is_completed", False):
                # 完成则清出，允许后续 episode 复用（保险）
                self._emitted.discard(jid)
                continue
            if jid in self._emitted:
                continue
            if step >= due * self.alpha:
                self._emitted.add(jid)
                remaining = max(0, due - step)
                yield {
                    "etype": "deadline_risk",
                    "title": TITLE["deadline_risk"],
                    "message": MSG["deadline_risk"].format(
                        job_id=jid, remaining=remaining),
                    "payload": {"job_id": jid, "due": due, "step": step,
                                "remaining": remaining,
                                "risk_ratio": round(step / due, 3) if due else 0}}


# ============================================================
# §D 调度层
# ============================================================

def default_rules(enable_deadline_risk=True, risk_alpha=DEFAULT_RISK_ALPHA):
    """默认规则列表，顺序即求值顺序（事件时序可预期）。
    顺序设计：machine_op → transfer → op_finish → job_completion → agv_state → deadline_risk
    先发细粒度（op 完成），再发粗粒度（job 完成），符合因果时序，便于前端排序/去重。"""
    rules = [
        machine_op_rule,
        transfer_rule,
        op_finish_rule,
        job_preemption_rule,
        job_completion_rule,
        agv_state_rule,
    ]
    if enable_deadline_risk:
        rules.append(DeadlineRiskRule(alpha=risk_alpha))
    return rules


class DiffEmitter:
    """帧间 diff 调度器（`_diff_and_emit` 升级落地）。

    职责：
      - 按注册顺序跑规则，捕获异常并降级为 system/diff_rule_error 事件
      - 在 finally 中滚动 _prev_frame（关键不变量：规则异常不污染基线）
      - 提供 lifecycle 事件统一出口（emit_lifecycle）

    不职责：
      - 不感知 SSE / 队列 / 广播（emit_callback 由调用方注入）
      - 不构造 frame（frame 由 _build_frame 产出，DiffEmitter 只读）
      - 不做 episode 落盘（独立议题，见 0701diff_and_emit升级.md §10.2）
    """

    def __init__(self, emit_callback: Callable[[dict], None], *,
                 rules=None, enable_deadline_risk=True,
                 risk_alpha=DEFAULT_RISK_ALPHA):
        """
        emit_callback: 接收完整 canonical 信封 dict，由调用方决定如何广播
                       （如包成 ("event", envelope) 元组进 SSE）。
        rules:         显式规则列表（用于测试或定制）；None 时用 default_rules。
        """
        self._emit = emit_callback
        self._prev_frame = None
        self._rules = (list(rules) if rules is not None
                       else default_rules(enable_deadline_risk, risk_alpha))

    def reset(self):
        """episode 边界清理：基线 + 各规则自持状态（如 R6 的 _emitted）。
        在 create / 新 episode 开始时调用。"""
        self._prev_frame = None
        for r in self._rules:
            reset_fn = getattr(r, "reset", None)
            if callable(reset_fn):
                try:
                    reset_fn()
                except Exception as e:
                    name = getattr(r, "__name__", None) or r.__class__.__name__
                    print(f"[diff] rule {name}.reset 异常: {e}", flush=True)

    def diff(self, cur_frame, step):
        """对比 _prev_frame 与 cur_frame，派生事件并回调 emit。
        首帧（_prev_frame is None）不发任何 diff 事件，只建立基线。
        无论规则是否异常，_prev_frame 必须滚动到 cur_frame（finally 保证）。"""
        prev = self._prev_frame
        timestamp = cur_frame.get("timestamp", f"T+{step}s")
        ctx = {"step": step, "timestamp": timestamp}
        try:
            if prev is not None:
                for rule in self._rules:
                    try:
                        for partial in rule(prev, cur_frame, ctx):
                            self._emit(make_envelope(step, timestamp, **partial))
                    except Exception as err:
                        self._emit_rule_error(rule, err, step, timestamp)
        finally:
            self._prev_frame = cur_frame

    def _emit_rule_error(self, rule, err, step, timestamp):
        """单条规则异常的降级路径：发 system/diff_rule_error + 打印日志。
        绝不让异常逃出 diff()，绝不污染 _prev_frame 滚动。"""
        name = getattr(rule, "__name__", None) or rule.__class__.__name__
        self._emit(make_envelope(
            step, timestamp,
            etype="diff_rule_error",
            title=TITLE["diff_rule_error"],
            message=MSG["rule_error"].format(rule=name, err=err),
        ))
        print(f"[diff] rule {name} 异常: {err}", flush=True)

    def emit_lifecycle(self, etype, step, *, message=None, title=None,
                       payload=None, level=None):
        """lifecycle 事件统一出口（sim_started / episode_completed / episode_truncated）。
        这些事件不走 diff 规则（触发条件不是 frame diff），但通过此出口保证信封
        shape 与 diff 事件完全一致 —— 调用方仍拿到 canonical 信封。
        etype 必须在 TYPE_REGISTRY 登记。"""
        if etype not in TYPE_REGISTRY:
            print(f"[diff] emit_lifecycle 收到未登记 etype: {etype}", flush=True)
        timestamp = f"T+{step}s"
        title = title if title is not None else TITLE.get(etype, etype)
        if message is None:
            tmpl = MSG.get(etype, "")
            try:
                message = tmpl.format(step=step)
            except Exception:
                message = tmpl or etype
        self._emit(make_envelope(
            step, timestamp, etype=etype, title=title,
            message=message, payload=payload, level=level,
        ))


__all__ = [
    # 数据
    "CATEGORY_LIFECYCLE", "CATEGORY_MACHINE_OP", "CATEGORY_TRANSFER",
    "CATEGORY_JOB", "CATEGORY_AGV", "CATEGORY_RISK", "CATEGORY_SYSTEM",
    "LEVEL_INFO", "LEVEL_SUCCESS", "LEVEL_WARNING", "LEVEL_ERROR",
    "TYPE_REGISTRY", "MSG", "TITLE", "DEFAULT_RISK_ALPHA",
    # 工具
    "make_envelope",
    # 规则
    "machine_op_rule", "transfer_rule", "op_finish_rule", "job_preemption_rule",
    "job_completion_rule", "agv_state_rule", "DeadlineRiskRule",
    "default_rules",
    # 调度
    "DiffEmitter",
]
