/**
 * 请求体可视化 — AI 详情页与管理台「对话日志」详情共用（单一实现，勿各自手写）。
 *
 * 存下来的 messages 就是送给模型的那一份，分块上色只为肉眼能分区：系统提示 / 插入的通知与
 * 交接 / 人说的话 / AI 说的话 / 本轮工具 / 工具调用 / 工具返回 / 思考 / 收尾与报错。
 * 正文一律原样显示（不截断）：能当 Markdown 读的走 Markdown，JSON 走格式化，其余原样。
 */
import { useMemo, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import MarkdownContent from './MarkdownContent'
import { useT } from '../../i18n/I18nContext'

type Kind = 'system' | 'state' | 'injected' | 'user' | 'assistant' | 'roundTools' | 'toolCall' | 'toolResult' | 'reasoning' | 'error'

/** 分块配色：左竖条定色、底色调淡；工具那两类再叠虚线框 + 等宽字体，同色系也不会混 */
const STYLES: Record<Kind, { bar: string; box: string; key: string }> = {
  system:     { bar: 'bg-textMuted/40',   box: 'bg-canvas border-border',                          key: 'logs:kindSystem' },
  state:      { bar: 'bg-primary-500',    box: 'bg-canvas border-primary-500/30',                  key: 'logs:kindState' },
  injected:   { bar: 'bg-accent-500',     box: 'bg-accent-500/10 border-accent-500/30 border-dashed', key: 'logs:kindInjected' },
  user:       { bar: 'bg-primary-500',    box: 'bg-primary-500/10 border-primary-500/30',           key: 'logs:kindUser' },
  assistant:  { bar: 'bg-mint-500',       box: 'bg-mint-500/10 border-mint-500/30',                 key: 'logs:kindAssistant' },
  roundTools: { bar: 'bg-primary-400',    box: 'bg-primary-500/5 border-primary-500/30 border-dashed', key: 'logs:kindRoundTools' },
  toolCall:   { bar: 'bg-primary-500',    box: 'bg-primary-500/10 border-primary-500/40 border-dashed', key: 'logs:kindToolCall' },
  toolResult: { bar: 'bg-accent-500',     box: 'bg-accent-500/10 border-accent-500/40 border-dashed', key: 'logs:kindToolResult' },
  reasoning:  { bar: 'bg-primary-400/60', box: 'bg-elevated border-border',                        key: 'logs:kindReasoning' },
  error:      { bar: 'bg-rose-500',       box: 'bg-rose-500/10 border-rose-500/30',                 key: 'logs:kindError' },
}

/** 等宽渲染的种类：工具入参/返回与「本轮工具」汇总行都是机器文本，等宽才看得出结构 */
const MONO: Kind[] = ['roundTools', 'toolCall', 'toolResult']

/** removed 用于改变量里"已经没了"的那几条：压暗再加一圈红，跟留下的分得开 */
type Tone = 'normal' | 'removed'

/** 状态栈摘要：这段状态的前缀（当前帧 / 交接 / 焦段）就长在这条里，是各状态请求体最大的不同 */
const STATE_MARK = '## 📋 当前状态'
/** 插入进来的上下文（不是对话双方说的话）：变更通知、上一轮交接、上下文压缩、未读提示 */
const INJECTED_MARKS = ['【能力变更通知】', '【通道变更】', '[上一轮交接]', '[上下文压缩]', '（更早还有']
/** 收尾/报错：工具轮次用尽、异常中断、模型报错都留在这里，单独上红色才不会被当成一句普通回复 */
const ERROR_MARKS = ['[本轮收尾]', 'Traceback', '异常中断', '执行失败']
/** 一个空数组的 JSON 化结果（方便判断"数组里没有东西"） */
const EMPTY = '[]'

function textOf(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return ''
  return JSON.stringify(value, null, 2)
}

/** 能当 JSON 读就格式化，读不了返回 null（读不了的不是错，正文照样原样渲染） */
function prettyJson(text: string): string | null {
  const s = text.trim()
  if (!s.startsWith('{') && !s.startsWith('[')) return null
  try {
    return JSON.stringify(JSON.parse(s), null, 2)
  } catch {
    return null
  }
}

function classify(msg: any): Kind {
  const content = textOf(msg?.content)
  if (msg?.role === 'tool') return 'toolResult'
  if (Array.isArray(msg?.tool_calls) && msg.tool_calls.length > 0) return 'toolCall'
  if (content.startsWith('[本轮工具]')) return 'roundTools'
  if (content.trimStart().startsWith(STATE_MARK)) return 'state'
  if (ERROR_MARKS.some(mark => content.includes(mark))) return 'error'
  if (INJECTED_MARKS.some(mark => content.includes(mark))) return 'injected'
  if (msg?.role === 'user') return 'user'
  if (msg?.role === 'assistant') return 'assistant'
  return 'system'
}

/** 状态块的首行就是这轮的帧身份（▸▶ [type] (label): 在干嘛），原样拿来当标题 */
function stateTitle(content: string): string | undefined {
  const line = content.split('\n').map(s => s.trim()).find(s => s.startsWith('▸') || s.startsWith('▶'))
  return line ? line.replace(/^[▸▶]+\s*/, '') : undefined
}

function toolName(call: any): string {
  return String(call?.function?.name || call?.name || call?.type || '?')
}

function toolArgs(call: any): string {
  const raw = call?.function?.arguments ?? call?.arguments
  return typeof raw === 'string' ? raw : textOf(raw)
}

function Block({ index, kind, title, tone = 'normal', children }: {
  index: number; kind: Kind; title?: string; tone?: Tone; children: React.ReactNode
}) {
  const t = useT()
  const [open, setOpen] = useState(true)
  const style = STYLES[kind]
  return (
    <div className={`flex gap-2 rounded-control border ${style.box} overflow-hidden ${
      tone === 'removed' ? 'opacity-70 ring-1 ring-rose-500/30' : ''
    }`}>
      <div className={`w-1 shrink-0 ${style.bar}`} />
      <div className="flex-1 min-w-0 py-2 pr-2">
        <button
          type="button"
          onClick={() => setOpen(v => !v)}
          className="flex items-center gap-1.5 w-full text-left group"
        >
          <ChevronRight size={12} className={`shrink-0 text-textMuted transition-transform ${open ? 'rotate-90' : ''}`} />
          <span className="text-3xs px-1.5 py-0.5 rounded-full bg-black/10 dark:bg-white/10 text-textSecondary">
            {t(style.key)}
          </span>
          <span className="text-3xs text-textMuted">#{index}</span>
          {title && <span className="text-3xs font-mono text-textSecondary truncate">{title}</span>}
        </button>
        {open && <div className="mt-1.5 min-w-0">{children}</div>}
      </div>
    </div>
  )
}

/** 正文：Markdown 优先，JSON 走格式化等宽块 —— 两者都不改一个字 */
function Body({ text, mono = false }: { text: string; mono?: boolean }) {
  const json = useMemo(() => prettyJson(text), [text])
  if (json !== null) {
    return (
      <pre className="text-2xs font-mono text-textSecondary whitespace-pre-wrap break-words bg-black/5 dark:bg-black/20 rounded-control p-2 max-h-96 overflow-y-auto">
        {json}
      </pre>
    )
  }
  if (mono) {
    return (
      <pre className="text-2xs font-mono text-textSecondary whitespace-pre-wrap break-words">{text}</pre>
    )
  }
  return (
    <div className="text-xs text-textPrimary leading-relaxed break-words">
      <MarkdownContent content={text} />
    </div>
  )
}

export default function RequestBodyViewer({ messages, className = '', legend = true, tone = 'normal' }: {
  messages: any[]
  className?: string
  /** 拼在改变量里时不重复摆图例和原始 JSON 开关（一屏摆好几截，图例只该出现一次） */
  legend?: boolean
  tone?: Tone
}) {
  const t = useT()
  const list = Array.isArray(messages) ? messages : []
  const [raw, setRaw] = useState(false)

  const blocks = useMemo(() => {
    const out: { kind: Kind; title?: string; body: React.ReactNode }[] = []
    list.forEach((msg) => {
      if (msg?.reasoning_content) {
        out.push({ kind: 'reasoning', title: String(msg.role || ''), body: <Body text={textOf(msg.reasoning_content)} /> })
      }
      const kind = classify(msg)
      let title: string | undefined
      let body: React.ReactNode
      if (kind === 'toolCall') {
        const calls = Array.isArray(msg.tool_calls) ? msg.tool_calls : []
        title = calls.map(toolName).join(', ')
        body = (
          <div className="space-y-1.5">
            {/* 带工具调用的消息，正文也是机器拼的（要么空，要么就是那行「本轮工具」）：
                跟单独成块的「本轮工具」一个长相，同一句话不该有两种字体 */}
            {textOf(msg.content).trim() && <Body text={textOf(msg.content)} mono />}
            {calls.map((call: any, ci: number) => (
              <div key={ci}>
                <div className="text-3xs font-mono text-textSecondary mb-0.5">{toolName(call)}</div>
                <Body text={toolArgs(call)} mono />
              </div>
            ))}
          </div>
        )
      } else if (kind === 'toolResult') {
        title = msg.tool_call_id ? String(msg.tool_call_id) : undefined
        body = <Body text={textOf(msg.content)} mono />
      } else if (kind === 'state') {
        title = stateTitle(textOf(msg.content))
        body = <Body text={textOf(msg.content)} />
      } else {
        body = <Body text={textOf(msg.content)} mono={MONO.includes(kind)} />
      }
      out.push({ kind, title, body })
    })
    return out
  }, [list])

  if (list.length === 0) {
    return <p className={`text-xs text-textMuted ${className}`}>{t('logs:empty')}</p>
  }

  return (
    <div className={`space-y-2 ${className}`}>
      {legend && <div className="flex items-center gap-2 flex-wrap">
        {!raw && (Object.keys(STYLES) as Kind[]).map(kind => (
          <span key={kind} className="flex items-center gap-1 text-3xs text-textMuted">
            <span className={`w-2 h-2 rounded-full ${STYLES[kind].bar}`} />
            {t(STYLES[kind].key)}
          </span>
        ))}
        <button
          type="button"
          onClick={() => setRaw(v => !v)}
          className="ml-auto text-3xs px-1.5 py-0.5 rounded border border-border text-textMuted hover:text-textSecondary transition-colors"
        >
          {raw ? t('logs:viewSegments') : t('logs:viewRaw')}
        </button>
      </div>}
      {raw ? (
        <pre className="text-2xs font-mono text-textSecondary whitespace-pre-wrap break-words bg-canvas border border-border rounded-control p-3 max-h-[60vh] overflow-y-auto">
          {JSON.stringify(list, null, 2) || EMPTY}
        </pre>
      ) : (
        <div className="space-y-1.5">
          {blocks.map((block, i) => (
            <Block key={i} index={i} kind={block.kind} title={block.title} tone={tone}>{block.body}</Block>
          ))}
        </div>
      )}
    </div>
  )
}
