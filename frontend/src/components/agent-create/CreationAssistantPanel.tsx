/**
 * 创建助手的对话列 —— 与群视界对话共用零件（ToolBubble / MarkdownContent），
 * 数据源小而不同：一个 POST + 一条 SSE 流，没有会话切换、附件、重连。
 *
 * 表单不在这个组件里：它只把 form 帧交给 CreateAgentPanel，
 * 那一份 AgentForm 仍是全站唯一的表单状态。
 */
import { useEffect, useRef, useState } from 'react'
import { ClipboardList, Globe, Sparkles } from 'lucide-react'
import MarkdownContent from '../shared/MarkdownContent'
import { ToolBubble } from '../shared/ChatPanelAtoms'
import ChatInput from '../ChatInput'
import { useT } from '../../i18n/I18nContext'
import { getDraft, runCreatorTurn, MAX_TURN_CHARS, type CreationDraft, type TurnFrame } from './creationApi'
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
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const abortRef = useRef<AbortController | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    let alive = true
    getDraft(draft.id)
      .then((d) => { if (alive) setItems((d.messages || []).map((m) => ({ kind: m.role, text: m.content }) as Item)) })
      .catch(() => {})
    return () => { alive = false }
  }, [draft.id])

  // 直接滚动列表本身：scrollIntoView 会连带滚动外层页面（窄屏整页可滚），
  // 每来一帧就把页面拽走一次，用户刚点完的输入框会跑到屏幕外
  useEffect(() => {
    const list = listRef.current
    if (list) list.scrollTop = list.scrollHeight
  }, [items])

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

  const send = async (raw: string) => {
    const text = raw.trim()
    if (!text || busy) return
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
        setError(String(frame.message || t('modal:creatorFailed')))
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
        // 空着的助手气泡不能一直挂「思考中…」，但也不能一律当成「断线」：
        //   · 只有思考没有正文 —— 模型其实答了，只是没给正文，别谎报断线
        //   · 后面已经插了工具/提示帧 —— 那帧才是解释，空壳直接删掉
        //   · 真的一帧都没有 —— 这时才说「再发一次试试」
        setItems((prev) => {
          const next = [...prev]
          let i = next.length - 1
          while (i >= 0 && next[i].kind !== 'assistant') i -= 1
          if (i < 0) return prev
          const item = next[i]
          if (item.kind !== 'assistant') return prev
          if (item.text.trim() || (item.reasoning || '').trim()) return prev
          if (i < next.length - 1) {
            next.splice(i, 1)
            return next
          }
          next[i] = { ...item, text: t('modal:creatorNoReply') }
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

      {/* 列表吃掉面板剩下的高度（自己滚）：窄屏上「创建助手」本来就是一整屏，
          以前那个 38vh 封顶是用来和下面的表单分高度的，拆屏之后只会把输入框顶到半空 */}
      <div ref={listRef} className="flex-1 min-h-0 overflow-y-auto space-y-2 pr-1">
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
      </div>

      {error && <p className="text-xs text-rose-400 mt-2 shrink-0">{error}</p>}

      {/* 与群聊/私信同一个输入框：外壳、发送键、Enter 发送、草稿保存都走它。
          创建助手没有附件与表情，关掉左下工具行；流式输出时同一位置换成「停止」*/}
      <div className="shrink-0 mt-2">
        <ChatInput
          conversationType="creator"
          conversationId={draft.id}
          t={t}
          onSend={send}
          connected
          busy={busy}
          onStop={stop}
          showTools={false}
          maxLength={MAX_TURN_CHARS}
          placeholder={t('modal:creatorPlaceholder')}
        />
      </div>
    </div>
  )
}
