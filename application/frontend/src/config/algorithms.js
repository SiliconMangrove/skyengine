/**
 * 算法池配置
 * 全局唯一数据源，AlgorithmPoolView 和 useSimulationConfig 共同引用
 * 当前算法更新不频繁，暂不考虑后端动态加载，直接前端静态配置
 */

const algorithms = {
  fjsp: [
    {
      id: "pso",
      name: "PSO 粒子群优化",
      description: "模拟鸟群觅食行为的群体智能算法，通过粒子在解空间中的协作搜索寻找近优调度方案。收敛速度较快，适合中等规模问题。",
      environments: ["grid_factory", "grid_factory_new"],
      status: "available",
    },
    {
      id: "de",
      name: "DE 差分进化",
      description: "基于种群差异向量的进化算法，通过变异、交叉、选择操作迭代优化调度方案。全局搜索能力强，对复杂问题表现稳定。",
      environments: ["grid_factory_new"],
      status: "available",
    },
    {
      id: "drl",
      name: "DRL 深度强化学习",
      description: "利用深度神经网络近似策略/价值函数，通过与调度环境的交互学习最优决策。适合动态场景和在线调度。",
      environments: ["grid_factory_new"],
      status: "available",
    },
    {
      id: "best",
      name: "BEST 最优搜索",
      description: "集束搜索与贪心策略结合的确定性方法，在可接受时间内搜索高质量调度解。结果可复现，适合基准测试。",
      environments: ["grid_factory_new"],
      status: "available",
    },
  ],

  mapf: [
    {
      id: "astar",
      name: "A* 路由",
      description: "经典启发式搜索算法，为每个 AGV 独立计算最短路径，通过优先级机制避免冲突。计算效率高，适合密度较低的场景。",
      environments: ["grid_factory", "grid_factory_new"],
      status: "available",
    },
    {
      id: "mapf_gpt",
      name: "MAPF-GPT 路由",
      description: "基于 Transformer 的端到端多智能体路径规划模型，可处理大规模密集场景下的路径冲突。适合高密度 AGV 环境。",
      environments: ["grid_factory_new"],
      status: "available",
    },
  ],

  assigner: [
    {
      id: "nearest",
      name: "最近分配",
      description: "将任务分配给距离最近的可用机器，减少 AGV 运输距离。",
      environments: ["grid_factory", "grid_factory_new"],
      status: "available",
    },
    {
      id: "fifo",
      name: "FIFO 先来先服务",
      description: "按任务到达顺序依次分配给最先空闲的机器，实现简单公平。",
      environments: ["grid_factory_new"],
      status: "available",
    },
    {
      id: "greedy",
      name: "贪心分配",
      description: "每步选择当前最优的机器-任务匹配，局部最优决策。",
      environments: ["grid_factory", "grid_factory_new"],
      status: "available",
    },
    {
      id: "hungarian",
      name: "匈牙利算法",
      description: "基于二分图最优匹配的确定性算法，求解全局最优的机器-任务分配。",
      environments: ["grid_factory_new"],
      status: "available",
    },
    {
      id: "least_congestion",
      name: "最小拥堵",
      description: "优先分配给当前负载最低的机器，均衡各机器利用率。",
      environments: ["grid_factory", "grid_factory_new"],
      status: "available",
    },
    {
      id: "load_balance",
      name: "负载均衡",
      description: "综合考虑机器队列长度和加工时间，动态平衡产线负载。",
      environments: ["grid_factory", "grid_factory_new"],
      status: "available",
    },
    {
      id: "random",
      name: "随机分配",
      description: "随机选择可用机器，常作为基准对照。",
      environments: ["grid_factory", "grid_factory_new"],
      status: "available",
    },
    {
      id: "sjt",
      name: "SJT 最短作业",
      description: "优先分配加工时间最短的任务，降低平均等待时间。",
      environments: ["grid_factory_new"],
      status: "available",
    },
    {
      id: "urgency",
      name: "紧迫度优先",
      description: "按交期紧迫度排序，优先处理即将超期的任务，减少延迟。",
      environments: ["grid_factory_new"],
      status: "available",
    },
  ],
};

/** 转为下拉选项格式 (供 useSimulationConfig 使用) */
export function getAlgorithmOptions(type) {
  return (algorithms[type] || []).map((a) => ({
    label: a.name,
    value: a.id,
    disabled: a.status !== "available",
  }));
}

/** 获取所有算法数据 (供 AlgorithmPoolView 使用) */
export function getAllAlgorithms() {
  return algorithms;
}

export default algorithms;
