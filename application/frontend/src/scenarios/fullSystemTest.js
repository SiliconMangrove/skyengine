// src/scenarios/fullSystemTest.js

/**
 * StaticFactory 前端剧本（前端模式仿真驱动器）
 *
 * 设计目标：产出与容器化 SkyEngine（DockerProxy 透传）同形的
 *           frame / metrics / event，让前端 hook 与面板零分支消费。
 *
 * Canonical shape（参见 docs/explore/0627帧与事件相关设计.md）：
 *   - frame:   { timestamp, env_timeline, grid_state, machines[list], jobs[list], active_transfers }
 *   - metrics: { status, step, metrics:{...}, metrics_reward }
 *   - event:   { timestamp, type(业务名), message, level }
 *
 * StaticFactory 是确定性剧本（无 random），55 步覆盖 0-54。
 */

// ============ 1. AGV 运动轨迹 ============
const AGV_TRAJECTORIES = [
    // --- 起始点 (5, 2) ---
    { step: 0, agvs: [{ x: 5, y: 2, active: true }] },

    // --- 移动到 (5, 11) ---
    { step: 1, agvs: [{ x: 5, y: 3, active: true }] },
    { step: 2, agvs: [{ x: 5, y: 4, active: true }] },
    { step: 3, agvs: [{ x: 5, y: 5, active: true }] },
    { step: 4, agvs: [{ x: 5, y: 6, active: true }] },
    { step: 5, agvs: [{ x: 5, y: 7, active: true }] },
    { step: 6, agvs: [{ x: 5, y: 8, active: true }] },
    { step: 7, agvs: [{ x: 5, y: 9, active: true }] },
    { step: 8, agvs: [{ x: 5, y: 10, active: true }] },
    { step: 9, agvs: [{ x: 5, y: 11, active: true }] },

    // --- 移动到 (8, 11) ---
    { step: 10, agvs: [{ x: 6, y: 11, active: true }] },
    { step: 11, agvs: [{ x: 7, y: 11, active: true }] },
    { step: 12, agvs: [{ x: 8, y: 11, active: true }] },

    // --- 移动到 (8, 9) ---
    { step: 13, agvs: [{ x: 8, y: 10, active: true }] },
    { step: 14, agvs: [{ x: 8, y: 9, active: true }] },

    // --- 移动到 (8, 6) ---
    { step: 15, agvs: [{ x: 8, y: 8, active: true }] },
    { step: 16, agvs: [{ x: 8, y: 7, active: true }] },
    { step: 17, agvs: [{ x: 8, y: 6, active: true }] },

    // --- 移动到 (8, 4) ---
    { step: 18, agvs: [{ x: 8, y: 5, active: true }] },
    { step: 19, agvs: [{ x: 8, y: 4, active: true }] },

    // --- 移动到 (10, 4) ---
    { step: 20, agvs: [{ x: 9, y: 4, active: true }] },
    { step: 21, agvs: [{ x: 10, y: 4, active: true }] },

    // --- 移动到 (14, 4) ---
    { step: 22, agvs: [{ x: 11, y: 4, active: true }] },
    { step: 23, agvs: [{ x: 12, y: 4, active: true }] },
    { step: 24, agvs: [{ x: 13, y: 4, active: true }] },
    { step: 25, agvs: [{ x: 14, y: 4, active: true }] },

    // --- 移动到 (16, 4) ---
    { step: 26, agvs: [{ x: 15, y: 4, active: true }] },
    { step: 27, agvs: [{ x: 16, y: 4, active: true }] },

    // --- 掉头返回到 (14, 4) ---
    { step: 28, agvs: [{ x: 15, y: 4, active: true }] },
    { step: 29, agvs: [{ x: 14, y: 4, active: true }] },

    // --- 移动到 (14, 6) ---
    { step: 30, agvs: [{ x: 14, y: 5, active: true }] },
    { step: 31, agvs: [{ x: 14, y: 6, active: true }] },

    // --- 移动到 (14, 9) ---
    { step: 32, agvs: [{ x: 14, y: 7, active: true }] },
    { step: 33, agvs: [{ x: 14, y: 8, active: true }] },
    { step: 34, agvs: [{ x: 14, y: 9, active: true }] },

    // --- 移动到 (14, 11) ---
    { step: 35, agvs: [{ x: 14, y: 10, active: true }] },
    { step: 36, agvs: [{ x: 14, y: 11, active: true }] },

    // --- 移动到 (8, 11) ---
    { step: 37, agvs: [{ x: 13, y: 11, active: true }] },
    { step: 38, agvs: [{ x: 12, y: 11, active: true }] },
    { step: 39, agvs: [{ x: 11, y: 11, active: true }] },
    { step: 40, agvs: [{ x: 10, y: 11, active: true }] },
    { step: 41, agvs: [{ x: 9, y: 11, active: true }] },
    { step: 42, agvs: [{ x: 8, y: 11, active: true }] },

    // --- 移动到 (5, 11) ---
    { step: 43, agvs: [{ x: 7, y: 11, active: true }] },
    { step: 44, agvs: [{ x: 6, y: 11, active: true }] },
    { step: 45, agvs: [{ x: 5, y: 11, active: true }] },

    // --- 返回起点 (5, 2) ---
    { step: 46, agvs: [{ x: 5, y: 10, active: true }] },
    { step: 47, agvs: [{ x: 5, y: 9, active: true }] },
    { step: 48, agvs: [{ x: 5, y: 8, active: true }] },
    { step: 49, agvs: [{ x: 5, y: 7, active: true }] },
    { step: 50, agvs: [{ x: 5, y: 6, active: true }] },
    { step: 51, agvs: [{ x: 5, y: 5, active: true }] },
    { step: 52, agvs: [{ x: 5, y: 4, active: true }] },
    { step: 53, agvs: [{ x: 5, y: 3, active: true }] },
    { step: 54, agvs: [{ x: 5, y: 2, active: true }] }
];

// ============ 1.5 工厂配置快照（供 analysisLog 构造 config_snapshot）============
// 0701 日志模块：finalize 时把这份数据塞进 run.config_snapshot，让 Run 自包含可复现。
export const STATIC_FACTORY_CONFIG = {
    factory_layout: {
        machines: [
            // 在 MACHINE_LOCATIONS 声明前预填（同源，保持一致）
            { id: 'M1', location: [8, 13] },
            { id: 'M2', location: [11, 13] },
            { id: 'M3', location: [14, 13] },
        ],
        agv_count: 1,
    },
    // job_spec 在 runFinalize 时从 JOB_SCRIPT 派生（见 export 段）
    job_spec: null,
    algorithm_params: null, // StaticFactory 剧本固定，无算法参数
};

// ============ 2. 机器拓扑坐标（canonical frame.machines[].location）============
// StaticFactory 固定 3 台机器，坐标与 3D 场景对齐
const MACHINE_LOCATIONS = {
    M1: [8, 13],
    M2: [11, 13],
    M3: [14, 13],
};

// ============ 3. Job-Op 静态剧本（与 AGV 折返点 / machine 阶段对齐）============
//
// 设计原则（见 docs/explore/0622更新设计.md 模块①）:
// - 3 Job × 3 Op，覆盖 0-54 步，确定性，无 random
// - arrive/start/finish 单位均为 step，与 env_timeline 对齐
// - assigned_machine 用 int (1/2/3) → 前端 `M${id}` 拼接对应 "M1"/"M2"/"M3"
// - J1 故意超期 (due < completion_time)，J2 准时，J3 无交期，覆盖三种分支
//
// 与 machine 阶段对齐:
//   step 5-15  M1 warmup   ← J1.Op0 (5-10), J2.Op0 (10-15)
//   step 16-35 M1+M2 heavy ← J1.Op1 (16-31 on M2), J2.Op1 (31-35 on M2)
//   step 36-42 M1 BROKEN/M3 WORKING ← J3.Op0 (36-39), J3.Op1 (39-42) on M3
//   step 43-54 M1 cooldown  ← J1.Op2 (43-47), J2.Op2 (47-50), J3.Op2 (50-52)
const JOB_SCRIPT = [
    {
        job_id: 1, release: 0, due: 20, // 故意超期
        ops: [
            { op_id: 0, proc_time: 5, assigned_machine: 1,
              arrive_machine_at: 5,  start_process_at: 5,  finish_process_at: 10 },
            { op_id: 1, proc_time: 15, assigned_machine: 2,
              arrive_machine_at: 10, start_process_at: 16, finish_process_at: 31 },
            { op_id: 2, proc_time: 4,  assigned_machine: 1,
              arrive_machine_at: 31, start_process_at: 43, finish_process_at: 47 },
        ],
    },
    {
        job_id: 2, release: 0, due: 60, // 准时
        ops: [
            { op_id: 0, proc_time: 5, assigned_machine: 1,
              arrive_machine_at: 10, start_process_at: 10, finish_process_at: 15 },
            { op_id: 1, proc_time: 4, assigned_machine: 2,
              arrive_machine_at: 15, start_process_at: 31, finish_process_at: 35 },
            { op_id: 2, proc_time: 3, assigned_machine: 1,
              arrive_machine_at: 35, start_process_at: 47, finish_process_at: 50 },
        ],
    },
    {
        job_id: 3, release: 0, due: null, // 无交期
        ops: [
            { op_id: 0, proc_time: 3, assigned_machine: 3,
              arrive_machine_at: 36, start_process_at: 36, finish_process_at: 39 },
            { op_id: 1, proc_time: 3, assigned_machine: 3,
              arrive_machine_at: 39, start_process_at: 39, finish_process_at: 42 },
            { op_id: 2, proc_time: 2, assigned_machine: 1,
              arrive_machine_at: 42, start_process_at: 50, finish_process_at: 52 },
        ],
    },
];

// 回填 STATIC_FACTORY_CONFIG.job_spec（工艺路线投影：去掉时序，保留可复现信息）
STATIC_FACTORY_CONFIG.job_spec = JOB_SCRIPT.map((j) => ({
    job_id: j.job_id,
    release: j.release,
    due: j.due,
    ops: j.ops.map((o) => ({
        op_id: o.op_id,
        proc_time: o.proc_time,
        assigned_machine: o.assigned_machine,
    })),
}));

// ============ 4. 派生：jobs 快照 ============
/**
 * 从 JOB_SCRIPT 派生当前 step 的 jobs 快照（canonical shape）。
 * 单向数据流：JOB_SCRIPT → generateJobs → jobs[]，generateMachineStates 反查本输出。
 *
 * op status 判定：step >= finish → FINISHED；step >= start → PROCESSING；否则 PENDING
 */
function generateJobs(step) {
    return JOB_SCRIPT.map((job) => {
        const opsDef = job.ops
        const total = opsDef.length
        let doneCount = 0
        let lastFinish = -1

        const ops = opsDef.map((op) => {
            const start = op.start_process_at
            const finish = op.finish_process_at
            let status, stepDone

            if (finish >= 0 && step >= finish) {
                status = 'FINISHED'
                stepDone = op.proc_time
                doneCount += 1
                lastFinish = Math.max(lastFinish, finish)
            } else if (start >= 0 && step >= start) {
                status = 'PROCESSING'
                stepDone = Math.max(0, Math.min(op.proc_time, step - start))
            } else {
                status = 'PENDING'
                stepDone = 0
            }

            const wait = start >= 0 ? Math.max(0, start - op.arrive_machine_at) : 0

            return {
                op_id: op.op_id,
                status,
                proc_time: op.proc_time,
                assigned_machine: op.assigned_machine,
                arrive_machine_at: op.arrive_machine_at,
                start_process_at: start,
                finish_process_at: status !== 'PENDING' ? finish : -1,
                wait_for_machine_time: wait,
                step_done: stepDone,
            }
        })

        const isCompleted = doneCount === total
        return {
            job_id: job.job_id,
            release: job.release,
            due: job.due,
            is_completed: isCompleted,
            completion_time: isCompleted ? lastFinish : -1,
            progress: { done: doneCount, total },
            ops,
        }
    })
}

// ============ 5. 派生：machines 快照（canonical list）============
/**
 * 生成当前 step 的 machines 列表（canonical shape，list + id 字段）。
 *
 * - status / load 保留 5 阶段剧本（向后兼容 UI）
 * - current_op / queue_length 由 generateJobs(step) 反查派生
 * - location 来自 MACHINE_LOCATIONS 拓扑
 * - 有 current_op 时强制 status=WORKING（避免状态/进度矛盾）
 * - id 保留字符串 "M1"（canonical 只约束 list+id，不强类型）
 */
function generateMachines(step) {
    const base = {
        M1: { id: 'M1', status: 'IDLE', load: 0 },
        M2: { id: 'M2', status: 'IDLE', load: 0 },
        M3: { id: 'M3', status: 'IDLE', load: 0 },
    }

    // 阶段1: 预热 (Step 5-15)
    if (step >= 5 && step < 16) {
        base.M1 = { id: 'M1', status: 'WORKING', load: 10 + (step - 5) * 2 }
    }
    // 阶段2: 高负荷作业 (Step 16-35)
    if (step >= 16 && step < 36) {
        base.M1 = { id: 'M1', status: 'WORKING', load: 60 + Math.floor(Math.random() * 20) }
        base.M2 = { id: 'M2', status: 'WORKING', load: 40 + Math.floor(Math.random() * 10) }
    }
    // 阶段3: 模拟故障 (Step 36-42)
    if (step >= 36 && step <= 42) {
        base.M1 = { id: 'M1', status: 'BROKEN', load: 99 }
        base.M2 = { id: 'M2', status: 'IDLE', load: 0 }
        base.M3 = { id: 'M3', status: 'WORKING', load: 20 }
    }
    // 阶段4: 恢复与收尾 (Step 43-54)
    if (step > 42) {
        const cooldown = Math.max(0, 50 - (step - 42) * 5)
        base.M1 = { id: 'M1', status: 'WORKING', load: cooldown }
        base.M2 = { id: 'M2', status: 'WORKING', load: cooldown / 2 }
    }
    // 结束时刻归零
    if (step === 54) {
        base.M1 = { id: 'M1', status: 'IDLE', load: 0 }
        base.M2 = { id: 'M2', status: 'IDLE', load: 0 }
    }

    // 补齐 canonical 字段
    const machines = Object.values(base).map((m) => ({
        ...m,
        location: MACHINE_LOCATIONS[m.id] || [0, 0],
        current_op: null,
        queue_length: 0,
    }))

    // 反查 jobs 填充 current_op / queue_length
    const byKey = { M1: machines[0], M2: machines[1], M3: machines[2] }
    for (const job of generateJobs(step)) {
        for (const op of job.ops) {
            const mid = op.assigned_machine
            if (mid == null) continue
            const key = `M${mid}`
            const machine = byKey[key]
            if (!machine) continue
            if (op.status === 'PROCESSING') {
                machine.current_op = {
                    job_id: job.job_id,
                    op_id: op.op_id,
                    index_in_job: op.op_id,
                    total_in_job: job.ops.length,
                    step_done: op.step_done,
                    proc_time: op.proc_time,
                }
                // 强制对齐：有 current_op 必为 WORKING
                machine.status = 'WORKING'
            } else if (op.status === 'PENDING' && op.arrive_machine_at <= step) {
                machine.queue_length += 1
            }
        }
    }

    return machines
}

// ============ 6. 派生：canonical metrics ============
/**
 * 生成 canonical metrics point：{ status, step, metrics:{...}, metrics_reward }。
 *
 * 字段口径对齐容器化 SkyEngine MetricsHub（见 docs/explore/0627static对齐难点.md §二）：
 *   - machine_utilization:    必填（hook machine_util 消费）
 *   - efficiency / agv_loaded_utilization / agv_busy_utilization: 可枚举指标
 *
 * StaticFactory 是 mock，数值从 load/utilVal 简单派生。
 */
function generateMetrics(step, machines) {
    const m1 = machines.find((m) => m.id === 'M1') || {}
    const m2 = machines.find((m) => m.id === 'M2') || {}
    const m3 = machines.find((m) => m.id === 'M3') || {}
    const mLoad = m1.load || 0

    // 效率：随负载升高，故障时下降
    let effVal = 0
    if (mLoad > 0 && mLoad < 80) effVal = 60 + Math.random() * 20
    else if (mLoad >= 80 && mLoad < 99) effVal = 90
    else if (mLoad === 99) effVal = 0

    // 利用率
    let utilVal = Math.min(100, step * 2)
    if (step > 40) utilVal = Math.max(0, 100 - (step - 40) * 10)

    // AGV 利用率：动作期间较高，故障期下降
    const agvMoving = step > 0 && step < 54
    const agvLoadedUtil = agvMoving ? 0.6 + Math.random() * 0.2 : 0
    const agvBusyUtil = agvMoving ? 0.8 + Math.random() * 0.15 : 0

    // 各机器负载比（用于扩展指标，0-1）
    const machineLoadVariance = [
        m1.load || 0, m2.load || 0, m3.load || 0,
    ].reduce((acc, v, _i, arr) => {
        const mean = arr.reduce((a, b) => a + b, 0) / arr.length
        const variance = arr.reduce((a, b) => a + (b - mean) ** 2, 0) / arr.length
        return variance
    }, 0)

    return {
        status: 'running',
        step,
        metrics: {
            machine_utilization: utilVal / 100,
            machine_non_processing_time_mean: (1 - utilVal / 100) * 0.5,
            machine_load_variance: machineLoadVariance,
            operation_queue_waiting_time_mean: step * 0.05,
            efficiency: effVal / 100,
            agv_loaded_utilization: agvLoadedUtil,
            agv_busy_utilization: agvBusyUtil,
            agv_travel_time_total: step,
            agv_waiting_time_total: Math.max(0, 54 - step),
            swap_conflict_count: 0,
            tasked_stationary_count: 0,
        },
        metrics_reward: 0,
    }
}

// ============ 7. 事件剧本（canonical）============
// type: 业务名（能套容器化词汇的套，套不上的用 narrative 兜底）
// level: info / success / warning / error
// 原 title 内容并入 message（canonical 无 title）
const EVENTS_LOG = [
    { step: 0,  type: 'sim_started',       level: 'info',    message: '系统就绪：AGV #1 已上线，坐标 (5, 2)，等待指令' },
    { step: 10, type: 'narrative',         level: 'info',    message: '进入作业区：AGV 到达上料缓冲区 (Y=11)，M1 开始预热' },
    { step: 27, type: 'narrative',         level: 'info',    message: '折返点装载：AGV 到达最远端 (16, 4)，执行自动装载任务' },
    { step: 36, type: 'narrative',         level: 'warning', message: '设备告警：M1 主轴过载 (Load 99%)，触发安全停机' },
    { step: 43, type: 'narrative',         level: 'success', message: '故障排除：M1 重启完成，系统恢复正常运行' },
    { step: 54, type: 'episode_completed', level: 'success', message: '任务完成：AGV 返回原点 (5, 2)，作业流程结束' },
]

// ============ 8. 驱动器 ============
/**
 * 按时间节拍将 canonical 数据推送到 Store。
 * 兼容旧调用: (store, monitor, onFinish) 和 (store, monitor, speed, onFinish)
 */
export function runFullSystemTest(factoryStore, monitorStore, speedOrOnFinish, onFinish) {
    let speed = 1500
    let callback = onFinish
    if (typeof speedOrOnFinish === 'function') {
        callback = speedOrOnFinish
    } else if (typeof speedOrOnFinish === 'number') {
        speed = speedOrOnFinish
    }

    let frameIndex = 0
    const TOTAL_STEPS = 55 // 0-54 共 55 步

    factoryStore.reset()
    monitorStore.clear()
    factoryStore.isPlaying = true

    const timer = setInterval(() => {
        if (frameIndex >= TOTAL_STEPS) {
            clearInterval(timer)
            factoryStore.isPlaying = false
            if (callback) callback()
            return
        }

        const step = frameIndex

        // ==================== A. 推送 frame（canonical state）====================
        const agvFrame = AGV_TRAJECTORIES[Math.min(step, AGV_TRAJECTORIES.length - 1)]
        if (agvFrame) {
            const machines = generateMachines(step)
            const jobs = generateJobs(step)
            const snapshot = {
                timestamp: `T+${step}s`,
                env_timeline: String(step),
                grid_state: {
                    positions_xy: agvFrame.agvs.map((a) => [a.x, a.y]),
                    is_active: agvFrame.agvs.map((a) => a.active),
                },
                machines, // list（canonical）
                jobs,     // list（canonical）
                active_transfers: [],
                // 不带 events 字段
            }
            factoryStore.pushSnapshot(snapshot)

            // ==================== B. 推送 metrics（canonical，需要 machines）====================
            const metricsPoint = generateMetrics(step, machines)
            monitorStore.pushMetrics(metricsPoint)
        }

        // ==================== C. 推送 events（canonical，独立）====================
        const eventFrame = EVENTS_LOG.find((e) => e.step === step)
        if (eventFrame) {
            monitorStore.pushEvent({
                type: eventFrame.type,
                level: eventFrame.level,
                message: eventFrame.message,
                idx: step,
                timestamp: `T+${step}s`,
            })
        }

        frameIndex++
    }, speed)

    return () => clearInterval(timer)
}
