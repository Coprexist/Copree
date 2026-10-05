/**
 * 创建助手的对话列 —— 与群视界对话共用零件（ToolBubble / MarkdownContent），
 * 数据源小而不同：一个 POST + 一条 SSE 流，没有会话切换、附件、重连。
 *
 * 表单不在这个组件里：它只把 form 帧交给 CreateAgentModal，
 * 那一份 AgentForm 仍是全站唯一的表单状态。
 */
import { useEffect, useRef, useState } from 'react'
import { ClipboardList, Globe, Send, Sparkles, Square } from 'lucide-react'
import MarkdownContent from '../shared/MarkdownContent'
import { ToolBubble } from '../shared/ChatPanelAtoms'
import { useT } from '../../i18n/I18nContext'
import { getDraft, runCreatorTurn, type CreationDraft, type TurnFrame } from './creationApi'
import type { AgentForm } from './types'

type Item =
  | { kind: 'user'; text: string }
  | { kind: 'assistant'; text: string; reasoning?: string }
  | { kind: 'tool'; name: string; label: string; detail?: string; running: boolean; error?: boolean }

export default function CreationAssistantPanel({ draft, onForm, className = '' }: {
  draft: CreationDraft
  onForm: (patch: Partial<AgentForm>, preset: string | null, sub: string | null) => void
  className?: string
}) {
  const t = useT()
  const [items, setItems] = useState<Item[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    let alive = true
    getDraft(draft.id)
      .then((d) => { if (alive) setItems((d.messages || []).map((m) => ({ kind: m.role, text: m.content }) as Item)) })
      .catch(() => {})
    return () => { alive = false }
  }, [draft.id])

  useEffect(() => { bottomRef.current?.scrollIntoView({ block: 'end' }) }, [items])

  /** 往「最后一条助手消息」追加——工具气泡会插在中间，不能假设它在末尾 */
  const appendToAssistant = (fn: (last: { kind: 'assistant'; text: string; reasoning?: string }) => Item) =>
    setItems((prev) => {
      const next = [...prev]
      for (let i = next.length - 1; i >= 0; i -= 1) {
        const item = next[i]
        if (item.kind === 'assistant') { next[i] = fn(item); break }
      }
      return next
    })

  const send = async () => {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    setError('')
    setBusy(true)
    setItems((prev) => [...prev, { kind: 'user', text }, { kind: 'assistant', text: '' }])
    const controller = new AbortController()
    abortRef.current = controller
    const onFrame = (frame: TurnFrame) => {
      if (frame.type === 'text') {
        appendToAssistant((last) => ({ ...last, text: last.text + (frame.delta || '') }))
      } else if (frame.type === 'reasoning') {
        appendToAssistant((last) => ({ ...last, reasoning: (last.reasoning || '') + (frame.delta || '') }))
      } else if (frame.type === 'tool') {
        setItems((prev) => [...prev, { kind: 'tool', name: frame.name || '', label: frame.label || '', running: true }])
      } else if (frame.type === 'tool_result') {
        setItems((prev) => {
          const next = [...prev]
          for (let i = next.length - 1; i >= 0; i -= 1) {
            const item = next[i]
            if (item.kind === 'tool' && item.running) {
              next[i] = { ...item, running: false, detail: frame.summary, error: !frame.ok }
              break
            }
          }
          return next
        })
      } else if (frame.type === 'form') {
        onForm(frame.patch || {}, frame.preset ?? null, frame.sub ?? null)
      } else if (frame.type === 'notice') {
        setItems((prev) => [...prev, { kind: 'assistant', text: frame.message || '' }])
      } else if (frame.type === 'error') {
        setError(frame.message || t('modal:creatorFailed'))
      }
    }
    try {
      await runCreatorTurn(draft.id, text, onFrame, controller.signal)
    } catch (e: any) {
      if (!controller.signal.aborted) setError(String(e?.message || e))
    } finally {
      setBusy(false)
      abortRef.current = null
      if (!controller.signal.aborted) {
        // 流断在半路（服务端异常、代理掐线）时不能把「思考中…」永远挂在屏幕上：
        // 空着的助手气泡换成一句人话，用户知道该重发
        setItems((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last && last.kind === 'assistant' && !last.text.trim()) {
            next[next.length - 1] = { ...last, text: t('modal:creatorNoReply') }
          }
          return next
        })
      }
    }
  }

  const stop = () => abortRef.current?.abort()

  return (
    <div className={`flex flex-col min-h-0 ${className}`}>
      <div className="flex items-center gap-1.5 text-xs font-medium text-textSecondary mb-2 shrink-0">
        <Sparkles size={13} /> {t('modal:creatorTitle')}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto space-y-2 pr-1">
        {items.length === 0 && (
          <p className="text-xs text-textMuted leading-relaxed">{t('modal:creatorEmpty')}</p>
        )}
        {items.map((item, index) => {
          if (item.kind === 'tool') {
            return (
              <ToolBubble
                key={index}
                name={item.name}
                label={item.label || (item.name === 'web_search' ? t('modal:creatorSearching') : t('modal:creatorFilling'))}
                detail={item.detail}
                error={item.error}
                running={item.running}
                icon={item.name === 'web_search' ? <Globe size={10} /> : <ClipboardList size={10} />}
              />
            )
          }
          if (item.kind === 'user') {
            return (
              <div key={index} className="flex justify-end">
                <div className="max-w-[85%] rounded-card bg-primary-500/15 border border-primary-500/25 px-3 py-1.5 text-sm text-textPrimary whitespace-pre-wrap">
                  {item.text}
                </div>
              </div>
            )
          }
          return (
            <div key={index} className="text-sm text-textPrimary min-w-0">
              {item.text ? <MarkdownContent content={item.text} /> : <span className="text-textMuted text-xs">{t('modal:creatorThinking')}</span>}
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>

      {error && <p className="text-xs text-rose-400 mt-2 shrink-0">{error}</p>}

      <div className="shrink-0 mt-2 flex items-end gap-2">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          rows={2}
          placeholder={t('modal:creatorPlaceholder')}
          className="flex-1 min-w-0 px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50 resize-none"
        />
        {busy ? (
          <button onClick={stop} className="btn btn-sm btn-secondary shrink-0" title={t('modal:creatorStop')}>
            <Square size={13} />
          </button>
        ) : (
          <button onClick={send} disabled={!input.trim()} className="btn btn-sm btn-primary shrink-0" title={t('modal:creatorSend')}>
            <Send size={13} />
          </button>
        )}
      </div>
    </div>
  )
}
