/**
 * 创建助手的前端客户端：草稿 CRUD + 一轮对话的流式读帧。
 *
 * 一帧 = 一个 JSON（{type: text|reasoning|tool|tool_result|form|notice|done|error}）。
 * 表单 patch 交给调用方应用——表单状态全站仍只有 CreateAgentModal 里那一份 AgentForm。
 *
 * 这里不用 utils/sse.ts 的 streamSse：那条是 GET 重连流，断线重发等于重跑一轮对话。
 */
import { api, getApiBaseUrl } from '../../api/client'
import type { AgentForm } from './types'

export interface CreationDraft {
  id: number
  title: string
  form: Partial<AgentForm>
  preset: string | null
  sub: string | null
  status: 'open' | 'created' | 'abandoned'
  agent_id: number | null
  created_at: string | null
  updated_at: string | null
}

export interface TranscriptItem { role: 'user' | 'assistant'; content: string }

export interface TurnFrame {
  type: 'text' | 'reasoning' | 'tool' | 'tool_result' | 'form' | 'notice' | 'done' | 'error'
  delta?: string
  name?: string
  label?: string
  args?: Record<string, unknown>
  ok?: boolean
  summary?: string
  patch?: Partial<AgentForm>
  form?: Partial<AgentForm>
  preset?: string | null
  sub?: string | null
  message?: string
}

export const listDrafts = () =>
  api.get<{ drafts: CreationDraft[] }>('/agent-creator/drafts').then((r) => r.drafts)

export const createDraft = () => api.post<CreationDraft>('/agent-creator/drafts', {})

export const getDraft = (id: number) =>
  api.get<CreationDraft & { messages: TranscriptItem[] }>(`/agent-creator/drafts/${id}`)

export const patchDraft = (id: number, patch: Partial<CreationDraft>) =>
  api.patch<CreationDraft>(`/agent-creator/drafts/${id}`, patch)

export const deleteDraft = (id: number) => api.delete(`/agent-creator/drafts/${id}`)

/** 跑一轮：POST + 流式读帧；abort 即断开（服务端那一轮会自己跑完并存档） */
export async function runCreatorTurn(
  draftId: number, message: string, onFrame: (frame: TurnFrame) => void, signal: AbortSignal,
): Promise<void> {
  const token = localStorage.getItem('access_token')
  const res = await fetch(`${getApiBaseUrl()}/agent-creator/drafts/${draftId}/turns`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ message }),
    signal,
  })
  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`
    try { detail = (await res.json())?.detail || detail } catch { /* 非 JSON 就用状态码 */ }
    onFrame({ type: 'error', message: detail })
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const chunk = await reader.read()
    if (chunk.done) break
    buffer += decoder.decode(chunk.value, { stream: true })
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() || ''
    for (const block of blocks) {
      const line = block.split('\n').find((l) => l.startsWith('data: '))
      if (!line) continue
      try { onFrame(JSON.parse(line.slice(6))) } catch { /* 坏帧跳过 */ }
    }
  }
}
