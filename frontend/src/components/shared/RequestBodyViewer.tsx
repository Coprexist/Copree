/**
 * 请求体可视化 — AI 详情页与管理台「对话日志」详情共用（单一实现，勿各自手写）。
 *
 * 存下来的 messages 就是送给模型的那一份，分块上色只为肉眼能分区：系统提示 / 插入的通知与
 * 交接 / 人说的话 / AI 说的话 / 本轮工具 / 工具调用 / 工具返回 / 思考 / 收尾与报错。
 * 正文一律原样显示（不截断）：能当 Markdown 读的走 Markdown，JSON 走格式化，其余原样。
 */
import { Fragment, useId, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import MarkdownContent from './MarkdownContent'
import { useT } from '../../i18n/I18nContext'
import { escapeHtml, renderMentionChips, type MentionNames } from '../../utils/mentions'

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
const MONO: Kind[] = ['toolCall', 'toolResult']

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

/** 块头的小标记：从正文里拆出来的时间 / 说话人 / msg_id / 提到了谁 */
interface Mark {
  key: string
  label: string
  tone: 'muted' | 'who' | 'id' | 'at'
  /** 悬停时的逐字原文（@ 这种翻过名字的标记用得上） */
  hint?: string
}

const MARK_CLASS: Record<Mark['tone'], string> = {
  muted: 'bg-black/5 dark:bg-white/10 text-textMuted',
  who:   'bg-primary-500/10 text-primary-400',
  id:    'bg-black/5 dark:bg-white/10 text-textMuted font-mono',
  at:    'bg-accent-500/10 text-accent-500',
}

/** 会话历史行的行头：`[Shanghai 09-26 13:36] 谁（id=96）: 正文 [msg_id=1443]`（prompting.format_message 拼的） */
const LINE_HEAD_RE = /^\[([^\]]+)\]\s+([\s\S]+)$/
/** 真时间戳长这个形状；[本轮工具] / [历史消息] 这种方括号前缀不算，别把前缀搬进块头 */
const CLOCK_RE = /\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}/
const MSG_ID_RE = /\s*\[msg_id=(\d+)\]\s*$/
const SPEAKER_RE = /^(.{1,40}?):\s([\s\S]*)$/
const SPEAKER_ID_RE = /^(.*)（id=(\d+)）$/


/** 块头只放「这条消息自己的属性」：时间与 msg_id（两者一条一条对应，实测没有只带其中一个的行）。
 *  谁 / @ 属于内容，留在正文里就地渲染。 */
function headerMarks(text: string): Mark[] {
  const head = LINE_HEAD_RE.exec(text)
  if (!head) return []
  const marks: Mark[] = []
  if (CLOCK_RE.test(head[1])) marks.push({ key: 'time', label: head[1], tone: 'muted' })
  const msgId = MSG_ID_RE.exec(head[2])
  if (msgId) marks.push({ key: 'msg-id', label: `msg#${msgId[1]}`, tone: 'id' })
  return marks
}

/**
 * 本轮工具账本：`send_gm(ok)；pop_state(ok)；end_turn(ok)` → 一个工具一个标签的 HTML。
 *  前缀 [本轮工具] 就是块头的 kind 标签，这里不再重复；工具自报的备注跟着工具名，失败标红。
 *  掺了别的东西（合成消息把话接在后面）返回 null，交给调用方原样渲染——别把人话当工具拆。
 */
function renderLedger(content: string): string | null {
  const trimmed = content.trim()
  if (!trimmed.startsWith('[本轮工具]')) return null
  const body = trimmed.slice('[本轮工具]'.length).trim()
  if (!body || body.includes('\n')) return null
  return body.split('；').map(part => part.trim()).filter(Boolean).map(item => {
    const matched = /^(.+?)\(([^()]*)\)$/.exec(item)
    const name = matched ? matched[1] : item
    const note = matched ? matched[2] : ''
    const failed = note.startsWith('失败')
    return `<span class="log-tool${failed ? ' log-tool-fail' : ''}">` +
      `<span class="log-tool-name">${escapeHtml(name)}</span>` +
      (note ? `<span class="log-tool-note">(${escapeHtml(note)})</span>` : '') +
      '</span>'
  }).join(' ')
}

/**
 * 历史行就地渲染：`[时间] 谁（id=N）: 正文 [msg_id=N]`
 * → 说话人标签 + 正文（时间与 msg_id 已搬到块头，正文里不再露那两串标记）。
 * 不是那种行（系统提示、JSON、本轮工具账本…）就只把 @ 换成标签。
 */
function renderLine(content: string, names: MentionNames | undefined, unknown: string): string {
  const ledger = renderLedger(content)
  if (ledger) return ledger
  const head = LINE_HEAD_RE.exec(content)
  if (!head) return renderMentionChips(content, names || {}, unknown)
  let rest = head[2]
  const msgId = MSG_ID_RE.exec(rest)
  if (msgId) rest = rest.slice(0, msgId.index)
  // 时间已经搬到块头；不是时间的方括号前缀（[本轮工具] / [历史消息]）留在正文里
  const tag = CLOCK_RE.test(head[1]) ? '' : `<span class="log-tag">${escapeHtml(head[1])}</span> `
  const speaker = SPEAKER_RE.exec(rest)
  if (!speaker) return tag + renderMentionChips(rest, names || {}, unknown)
  const withId = SPEAKER_ID_RE.exec(speaker[1])
  const whoId = withId ? `<span class="log-who-id">#${withId[2]}</span>` : ''
  // 冒号放进框里收尾（框是 inline-flex，冒号自成一项，跟名字/id 之间留出间隔）
  const who = `<span class="log-who">${escapeHtml(withId ? withId[1] : speaker[1])}${whoId}:</span>`
  // 说话人单独一行：话在下一行起，读起来才是"谁说的 / 说了什么"
  return `${tag}${who}\n${renderMentionChips(speaker[2], names || {}, unknown)}`
}

/** 字段能拆到第几层：够看清 next_frame.tail 这种两层结构，再深就退回等宽（防病态嵌套） */
const FIELD_DEPTH = 3

/** 嵌套层的容器：封顶 + 自己滚。一层 next_frame 能带几十个字段，不封顶就把整页撑长了 */
const NESTED_BOX = 'rounded-control border border-border p-2 max-h-72 overflow-y-auto'

/** 一个值怎么渲染：字符串当文本读、容器递归拆、数字/布尔/null 就地等宽。
 *  只有容器吃层数——标量再把层数耗掉，emotion 那种一串数字会全变成小 JSON 块。 */
function ArgValue({ value, names, depth }: { value: any; names?: MentionNames; depth: number }) {
  if (typeof value === 'string') return <Body text={value} names={names} />
  if (Array.isArray(value)) {
    if (value.length === 0) return <Body text="[]" mono />
    if (depth <= 0) return <Body text={JSON.stringify(value, null, 2)} mono />
    const items = value.map((item, index) => (
      <ArgValue key={index} value={item} names={names} depth={depth - 1} />
    ))
    // 顶层（depth == FIELD_DEPTH）就在块里摊开；更深一层套容器
    return depth >= FIELD_DEPTH
      ? <div className="space-y-1">{items}</div>
      : <div className={`${NESTED_BOX} space-y-1`}>{items}</div>
  }
  if (value && typeof value === 'object') {
    if (depth <= 0) return <Body text={JSON.stringify(value, null, 2)} mono />
    const nested = <JsonObject value={value} names={names} depth={depth - 1} />
    return depth >= FIELD_DEPTH ? nested : <div className={NESTED_BOX}>{nested}</div>
  }
  return <span className="text-2xs font-mono text-textSecondary">{JSON.stringify(value)}</span>
}

/** 一层字段：键一列、值一列。用 grid 而不是每行一个 flex —— 键宽窄不一，值也要从同一条线起 */
function JsonObject({ value, names, depth }: {
  value: Record<string, unknown>; names?: MentionNames; depth: number
}) {
  return (
    <div className="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-2 gap-y-1.5">
      {Object.entries(value).map(([key, item]) => (
        <Fragment key={key}>
          <span className="text-3xs font-mono px-1.5 py-0.5 rounded bg-black/5 dark:bg-white/10 text-textMuted">
            {key}
          </span>
          <div className="min-w-0"><ArgValue value={item} names={names} depth={depth} /></div>
        </Fragment>
      ))}
    </div>
  )
}

/** JSON 对象 → 一行一个字段：键一个小标签，值按类型渲染。
 *  工具入参与工具返回都是这个形状（原本整坨 JSON，长文本里的转义符比正文还多），共用一套。 */
function JsonFields({ text, names }: { text: string; names?: MentionNames }) {
  const parsed = useMemo(() => {
    try {
      const value = JSON.parse(text)
      return value && typeof value === 'object' && !Array.isArray(value) ? value : null
    } catch { return null }
  }, [text])
  // 数组、标量、空对象仍走格式化等宽：那不是"一堆参数"，拆开反而更乱
  if (!parsed || Object.keys(parsed).length === 0) return <Body text={text} mono />
  return <JsonObject value={parsed} names={names} depth={FIELD_DEPTH} />
}

function toolName(call: any): string {
  return String(call?.function?.name || call?.name || call?.type || '?')
}

function toolArgs(call: any): string {
  const raw = call?.function?.arguments ?? call?.arguments
  return typeof raw === 'string' ? raw : textOf(raw)
}

function Block({ index, msgIndex, kind, title, source, marks = [], tone = 'normal', children }: {
  index: number; msgIndex: number; kind: Kind; title?: string; tone?: Tone; children: React.ReactNode
  /** 这一块的原始文本（那条消息本身）；给了才摆「原文 / 渲染」开关 */
  source?: string
  /** 从正文里拆出来的标记（时间 / 谁 / msg_id / @了谁），摆在块头供扫读 */
  marks?: Mark[]
}) {
  const t = useT()
  const [open, setOpen] = useState(true)
  const [showSource, setShowSource] = useState(false)
  const style = STYLES[kind]
  // 折叠状态要能带出组件：导出件里没有 React，靠这两个属性把开关关系留在 DOM 上
  const bodyId = useId()
  const viewId = useId()
  return (
    <div className={`flex gap-2 rounded-control border ${style.box} overflow-hidden ${
      tone === 'removed' ? 'opacity-70 ring-1 ring-rose-500/30' : ''
    }`}>
      <div className={`w-1 shrink-0 ${style.bar}`} />
      <div className="flex-1 min-w-0 py-2 pr-2">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setOpen(v => !v)}
            aria-expanded={open}
            aria-controls={bodyId}
            data-collapsible={bodyId}
            className="flex items-center gap-1.5 flex-1 min-w-0 text-left group flex-wrap"
          >
            <ChevronRight size={12} className="collapse-arrow shrink-0 text-textMuted" />
            <span className="text-3xs px-1.5 py-0.5 rounded-full bg-black/10 dark:bg-white/10 text-textSecondary">
              {t(style.key)}
            </span>
            <span className="text-3xs text-textMuted">#{index}</span>
            {title && <span className="text-3xs font-mono text-textSecondary truncate">{title}</span>}
            {marks.map(mark => (
              <span
                key={mark.key}
                title={mark.hint}
                className={`text-3xs px-1.5 py-0.5 rounded-full ${MARK_CLASS[mark.tone]}`}
              >
                {mark.label}
              </span>
            ))}
          </button>
          {source !== undefined && (
            <button
              type="button"
              onClick={() => setShowSource(v => !v)}
              data-switch={viewId}
              data-value={showSource ? 'source' : 'rendered'}
              title={t('logs:blockSourceHint')}
              className={`shrink-0 text-3xs px-1.5 py-0.5 rounded border transition-colors ${
                showSource
                  ? 'border-primary-500/50 bg-primary-500/10 text-primary-400'
                  : 'border-border text-textMuted hover:text-textSecondary'
              }`}
            >
              <span data-when="rendered">{t('logs:blockRaw')}</span>
              <span data-when="source">{t('logs:blockRender')}</span>
            </button>
          )}
        </div>
        <div id={bodyId} data-open={open} data-smooth-height className="collapse-body">
          <div className="pt-1.5 min-w-0">
            {/* 两份视图都在 DOM 里：导出件没有 React，切「原文 / 渲染」只能靠 data-value */}
            <div id={viewId} data-smooth-height data-value={showSource ? 'source' : 'rendered'} className="switch">
              <div data-case="rendered">{children}</div>
              {source !== undefined && <div data-case="source"><Raw text={source || ''} lazy={'msg:' + msgIndex} /></div>}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * 容器高度一变就平滑过渡过去，不逐个特判「这次是折叠、那次是切视图」——
 * 只要高度变了就当作一次高度动画，否则每加一种会改高度的交互都要再补一处。
 * 看的是高度本身（ResizeObserver），所以内容自己长高也算。
 */
function useSmoothHeights(rootRef: React.RefObject<HTMLDivElement | null>, deps: React.DependencyList) {
  useEffect(() => {
    const root = rootRef.current
    if (!root) return
    const stop: Array<() => void> = []
    root.querySelectorAll<HTMLElement>('[data-smooth-height]').forEach(el => {
      let prev = el.offsetHeight
      let timer = 0
      // 收到底的标记：网格 0 行高与「不用再渲染」都挂在它上面，动画期间不设，内容才完整可见
      const markCollapsed = () => {
        if (el.getAttribute('data-open') === 'false') el.setAttribute('data-collapsed', 'true')
        else el.removeAttribute('data-collapsed')
      }
      markCollapsed()
      const ro = new ResizeObserver(() => {
        const to = el.offsetHeight
        if (to === prev) return
        const from = prev
        prev = to
        // 动画期间先停掉观察：我们自己改高度也会惊动它，否则会自己追自己
        ro.disconnect()
        el.style.overflow = 'hidden'
        el.style.height = from + 'px'
        void el.offsetHeight
        el.style.transition = 'height 0.28s cubic-bezier(0.65, 0, 0.35, 1)'
        el.style.height = to + 'px'
        window.clearTimeout(timer)
        timer = window.setTimeout(() => {
          // 先落「收到底」的标记再交还高度：反过来的话网格会先弹回一行、再跳回 0
          markCollapsed()
          el.style.transition = ''
          el.style.height = ''
          el.style.overflow = ''
          prev = el.offsetHeight
          ro.observe(el)
        }, 320)
      })
      ro.observe(el)
      stop.push(() => { ro.disconnect(); window.clearTimeout(timer) })
    })
    return () => stop.forEach(fn => fn())
  }, deps)
}
/**
 * 等宽原文块：JSON 格式化、机器文本、逐字原文都用它，样式只写一遍。
 * lazy 是导出件用的键：那份 JSON 不在文件里存第二遍，打开时按它从紧凑数据现算。
 */
function Raw({ text, lazy }: { text: string; lazy?: string }) {
  return (
    <pre data-lazy-json={lazy} className="text-2xs font-mono text-textSecondary whitespace-pre-wrap break-words bg-black/5 dark:bg-black/20 rounded-control p-2 max-h-96 overflow-y-auto">
      {text}
    </pre>
  )
}

/** 正文：Markdown 优先，JSON 走格式化等宽块。机器文本一字不改，人话里只把 <@!id> 翻成人名 */
function Body({ text, mono = false, names }: { text: string; mono?: boolean; names?: MentionNames }) {
  const t = useT()
  const json = useMemo(() => prettyJson(text), [text])
  if (json !== null) return <Raw text={json} />
  if (mono) {
    return (
      <pre className="text-2xs font-mono text-textSecondary whitespace-pre-wrap break-words">{text}</pre>
    )
  }
  return (
    <div className="log-md text-xs text-textPrimary leading-relaxed break-words">
      <MarkdownContent content={renderLine(text, names, t('logs:mentionUnknown'))} />
    </div>
  )
}

export default function RequestBodyViewer({ messages, className = '', legend = true, tone = 'normal', mentionNames, raw, onToggleRaw, rawSwitchId }: {
  messages: any[]
  className?: string
  /** 拼在改变量里时不重复摆图例和原始 JSON 开关（一屏摆好几截，图例只该出现一次） */
  legend?: boolean
  tone?: Tone
  /** `<@!id>` → 名字（后端随日志详情给）；没有就退回 `@用户N` */
  mentionNames?: MentionNames
  /**
   * 受控的「原始 JSON」视图：给了 onToggleRaw 就说明按钮摆在调用方那一行
   * （要跟下载按钮并排），这里不再自己摆一个；rawSwitchId 是两边共用的开关组 id
   */
  raw?: boolean
  onToggleRaw?: () => void
  rawSwitchId?: string
}) {
  const t = useT()
  const list = Array.isArray(messages) ? messages : []
  const [innerRaw, setInnerRaw] = useState(false)
  const showRaw = onToggleRaw ? Boolean(raw) : innerRaw
  // 分段视图与原始 JSON 也是一组视图：关系同样留在 DOM 上，导出件才切得动；
  // 按钮被搬到调用方那一行时，开关组的 id 由调用方给，两边仍指向同一组
  const autoId = useId()
  const viewId = rawSwitchId ?? autoId
  // 导出件里「原始 JSON」与每条的「原文」都按这份紧凑数据现算，不在文件里重复存两遍；
  // 把 < 转义掉是为了内容里出现 </script> 时不会提前结束这个标签
  const logJson = useMemo(() => JSON.stringify(list).replace(/</g, '\\u003c'), [list])

  const blocks = useMemo(() => {
    const out: { kind: Kind; title?: string; source: string; marks: Mark[]; body: React.ReactNode; msgIndex: number }[] = []
    // 工具返回自己不带函数名，只能拿 tool_call_id 回上面那条工具调用里认
    const callNames = new Map<string, string>()
    for (const msg of list) {
      for (const call of (Array.isArray(msg?.tool_calls) ? msg.tool_calls : [])) {
        if (call?.id) callNames.set(String(call.id), toolName(call))
      }
    }
    list.forEach((msg, mi) => {
      const content = textOf(msg.content)
      if (msg?.reasoning_content) {
        const thinking = textOf(msg.reasoning_content)
        out.push({
          kind: 'reasoning', title: String(msg.role || ''), source: thinking, marks: [], msgIndex: mi,
          body: <Body text={thinking} names={mentionNames} />,
        })
      }
      const kind = classify(msg)
      let title: string | undefined
      let body: React.ReactNode
      if (kind === 'toolCall') {
        const calls = Array.isArray(msg.tool_calls) ? msg.tool_calls : []
        title = calls.map(toolName).join(', ')
        body = (
          <div className="space-y-1.5">
            {/* 带工具调用的消息，正文要么空、要么就是那行「本轮工具」：跟单独成块的账本
                走同一条渲染路（renderLine 认账本），同一句话不该两种长相 */}
            {content.trim() && <Body text={content} names={mentionNames} />}
            {calls.map((call: any, ci: number) => (
              <div key={ci} className="space-y-1">
                {/* 函数名已经在块头了；一次带多个调用时才在正文里点出每个是谁的入参 */}
                {calls.length > 1 && (
                  <div className="text-3xs font-mono text-textSecondary">{toolName(call)}</div>
                )}
                <JsonFields text={toolArgs(call)} names={mentionNames} />
              </div>
            ))}
          </div>
        )
      } else if (kind === 'toolResult') {
        // 标题用函数名（id 一长串没人认得出），对上上面那条工具调用；对不上才退回 id
        title = callNames.get(String(msg.tool_call_id)) || (msg.tool_call_id ? String(msg.tool_call_id) : undefined)
        body = <JsonFields text={content} names={mentionNames} />
      } else if (kind === 'state') {
        title = stateTitle(content)
        body = <Body text={content} />
      } else {
        body = <Body text={content} mono={MONO.includes(kind)} names={mentionNames} />
      }
      // 原文 = 这条消息本身（库里的字段一个不少），跟上面渲染出来的视图对得上
      out.push({
        kind, title, source: JSON.stringify(msg, null, 2), msgIndex: mi,
        marks: headerMarks(content),
        body,
      })
    })
    return out
  }, [list, mentionNames, t])

  const rootRef = useRef<HTMLDivElement>(null)
  // 折叠的块、两份视图都在这一层里：谁的高度变都给一段过渡（单一机制，不再逐个特判）
  useSmoothHeights(rootRef, [blocks])

  if (list.length === 0) {
    return <p className={`text-xs text-textMuted ${className}`}>{t('logs:empty')}</p>
  }

  return (
    <div ref={rootRef} className={className}>
      {/* 数据只存一份：导出件打开时按这份紧凑 JSON 现算「原始 JSON」与每条「原文」 */}
      <script type="application/json" data-log-json dangerouslySetInnerHTML={{ __html: logJson }} />
      {legend && <div className="flex items-center gap-2 flex-wrap pt-2 pb-2 sticky top-0 z-10 bg-surface" data-value={showRaw ? 'raw' : 'segments'} data-switch-mirror={viewId} data-export-stick-legend>
        <div className="flex items-center gap-2 flex-wrap" data-when="segments">
        {(Object.keys(STYLES) as Kind[]).map(kind => (
          <span key={kind} className="flex items-center gap-1 text-3xs text-textMuted">
            <span className={`w-2 h-2 rounded-full ${STYLES[kind].bar}`} />
            {t(STYLES[kind].key)}
          </span>
        ))}
        </div>
        {/* 受控时按钮在调用方那一行（跟下载按钮并排），这里就不重复摆了 */}
        {!onToggleRaw && (
          <button
            type="button"
            onClick={() => setInnerRaw(v => !v)}
            data-switch={viewId}
            data-value={showRaw ? 'raw' : 'segments'}
            className="ml-auto text-3xs px-1.5 py-0.5 rounded border border-border text-textMuted hover:text-textSecondary transition-colors"
          >
            <span data-when="segments">{t('logs:viewRaw')}</span>
            <span data-when="raw">{t('logs:viewSegments')}</span>
          </button>
        )}
      </div>}
      {/* 两份视图都在 DOM 里：导出件没有 React，切「原始 JSON」只能靠 data-value */}
      <div id={viewId} data-smooth-height data-value={showRaw ? 'raw' : 'segments'} className="switch">
        <div data-case="segments" className="space-y-1.5">
          {blocks.map((block, i) => (
            <Block key={i} index={i} msgIndex={block.msgIndex} kind={block.kind} title={block.title} source={block.source} marks={block.marks} tone={tone}>
              {block.body}
            </Block>
          ))}
        </div>
        {legend && (
          <div data-case="raw">
            <pre data-lazy-json="raw" className="text-2xs font-mono text-textSecondary whitespace-pre-wrap break-words bg-canvas border border-border rounded-control p-3 max-h-[60vh] overflow-y-auto">
              {JSON.stringify(list, null, 2) || EMPTY}
            </pre>
          </div>
        )}
      </div>
    </div>
  )
}
