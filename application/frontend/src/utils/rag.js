/**
 * RAG 请求封装 — vLLM OpenAI 兼容 API
 *
 * URL 优先级:
 * 1. window.__RAG_BACKEND_URL__ (Docker entrypoint 注入)
 * 2. /rag 代理路径 (Vite dev server 代理到 RAG 后端，避免 CORS)
 * 3. import.meta.env.VITE_RAG_URL (构建时环境变量)
 */

// URL 解析: 运行时注入 > Vite 代理 > 环境变量
const _rawUrl = window.__RAG_BACKEND_URL__
const _hasInjected = _rawUrl && !_rawUrl.startsWith('__')
const _envUrl = import.meta.env.VITE_RAG_URL

// 优先用运行时注入，其次用 Vite 代理路径（走 /rag 避免跨域），最后用环境变量直连
const RAG_URL = _hasInjected
  ? _rawUrl
  : '/rag'  // 本地开发走 Vite proxy，无 CORS 问题

const RAG_API_KEY = import.meta.env.VITE_RAG_API_KEY || 'token-local-dev'
const RAG_MODEL = import.meta.env.VITE_RAG_MODEL || 'qwen3.6-35b'

const SYSTEM_PROMPT = `你是天工(SkyEngine)制造系统的智能分析助手。你负责分析工厂仿真运行数据，包括AGV调度、机器负载、运输任务和系统日志。

你的能力：
1. 分析 AGV 效率、利用率和空驶情况
2. 检测生产瓶颈（机器拥堵、AGV 堆叠、物流断流）
3. 分析机器负载均衡性
4. 评估运输路径和调度合理性
5. 总结日志事件并提取关键异常
6. 基于历史趋势给出优化建议

数据说明：
- grid_state.positions_xy: AGV 的 [x, y] 坐标列表
- grid_state.is_active: AGV 活跃状态列表
- machines: 机器状态字典 {id: {status, progress, ...}}
- active_transfers: 活跃运输任务列表
- metrics: 效率和利用率指标

请用中文回答，保持简洁专业。`

/**
 * 检查 RAG 是否可用（始终返回 true，依赖代理或直连）
 */
export function isRAGAvailable() {
  return true
}

/**
 * 获取 RAG 连接信息（用于 hello 测试）
 */
export function getRAGInfo() {
  return { url: RAG_URL, model: RAG_MODEL }
}

/**
 * 发送 RAG 查询
 * @param {string} question - 用户问题
 * @param {object} context - monitorStore.buildRAGContext() 生成的上下文
 * @param {function} onChunk - 流式回调 (chunk: string) => void
 * @returns {Promise<string>} 完整回复
 */
export async function queryRAG(question, context = null, onChunk = null) {
  const userMessage = context
    ? JSON.stringify({ question, context })
    : question

  const response = await fetch(`${RAG_URL}/v1/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${RAG_API_KEY}`,
    },
    body: JSON.stringify({
      model: RAG_MODEL,
      messages: [
        { role: 'system', content: SYSTEM_PROMPT },
        { role: 'user', content: userMessage },
      ],
      max_tokens: 512,
      temperature: 0.7,
      stream: !!onChunk,
    }),
  })

  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new Error(`RAG 请求失败: ${response.status} ${text}`)
  }

  // 流式响应
  if (onChunk && response.body) {
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let fullContent = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      const chunk = decoder.decode(value, { stream: true })
      for (const line of chunk.split('\n')) {
        if (!line.startsWith('data: ') || line === 'data: [DONE]') continue
        try {
          const parsed = JSON.parse(line.slice(6))
          const content = parsed.choices?.[0]?.delta?.content || ''
          if (content) {
            fullContent += content
            onChunk(content)
          }
        } catch { /* 忽略不完整的 JSON 行 */ }
      }
    }
    return fullContent
  }

  // 非流式
  const data = await response.json()
  return data.choices?.[0]?.message?.content || '无回复'
}
