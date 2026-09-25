/**
 * 对话面板的共用零件 —— 群视界世界对话与 DSH 对话页共用同一份实现。
 *
 * 为什么抽出来：两边的数据源完全不同（一边是 useWorldChat 的 WS 流，一边是 DSH 桥接的
 * SSE 帧），但「内容列宽怎么算、工具条长什么样」是同一件事。留两份必然各自漂移，
 * 所以只把**纯展示**的零件收敛到这里：世界侧不因此多一份状态，DSH 侧也不用抄一遍。
 */
import { useState, useRef, useCallback, useEffect } from 'react'
import { FileText, Brain, Search, Globe, Terminal, Package, Clock, Wrench, Eraser, ChevronDown } from 'lucide-react'

// ── 对话内容列宽（学 DSH ConversationRoot / WidthHandle）──
// 内容列居中，宽度是可拖的：上下限都按"列宽"推，保证两侧永远留着放拖条的留白。
/** localStorage 键：拖动过的内容列宽（px），只存用户意图，渲染宽度每次按列宽重新收敛 */
const CONTENT_W_KEY = 'world_chat_content_width'
/** 内容列最小宽度：再窄代码块就没法看了（与 DSH 的 CONTENT_MIN 同值） */
const CONTENT_MIN = 640
/** 每侧必须留出的留白：24 内缩 + 40 拖条 + 24 安全区 —— 拖到头也还能拖回来 */
const CONTENT_EDGE_BUDGET = 176
/** 没有偏好时的自适应宽：列宽的 64%，夹在 680~920 之间（DSH 的同一套公式） */
const CONTENT_ADAPTIVE_MIN = 680
const CONTENT_ADAPTIVE_MAX = 920
/** 内容列宽经 CSS 变量下发：拖拽期间直接改变量，不走 state，省下每帧重渲整段消息 */
export const CONTENT_W_VAR = '--world-chat-content-w'

/** 读偏好：本地存储是"持久层边界"，坏值一律当没偏好 */
function readContentWidthPref(): number | null {
  try {
    const raw = localStorage.getItem(CONTENT_W_KEY)
    if (raw === null) return null
    const v = Number(raw)
    return Number.isFinite(v) && v > 0 ? v : null
  } catch { return null }
}

/** 列宽 → 内容列宽：有偏好按偏好夹，没偏好按列宽自适应；上限 = 列宽 - 留白预算 */
function resolveContentWidth(columnWidth: number, pref: number | null): number {
  const max = Math.max(CONTENT_MIN, columnWidth - CONTENT_EDGE_BUDGET)
  if (pref !== null) return Math.min(Math.max(pref, CONTENT_MIN), max)
  return Math.max(CONTENT_ADAPTIVE_MIN, Math.min(columnWidth * 0.64, CONTENT_ADAPTIVE_MAX))
}

/**
 * 内容列宽拖拽。三件事照 DSH 的做法：
 *  1) 内容列居中，拖任一条边都是"两边各让一半"，所以宽度按 **2× 指针位移** 变，条才跟手；
 *  2) 拖动期间只写 CSS 变量，不 setState —— 消息列表每帧重渲的代价太大，松手才落库 + 回写状态；
 *  3) 上限由 resolveContentWidth 兜住（列宽 - 176），拖到贴边也留得下重拖的把手。
 */
export function useContentColumnWidth(columnWidth: number, hostRef: React.RefObject<HTMLDivElement | null>) {
  const [pref, setPref] = useState<number | null>(() => readContentWidthPref())
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef<{ x: number; base: number; outward: 1 | -1; latest: number; frame: number | null } | null>(null)

  const onHandleDown = useCallback((side: 'left' | 'right') => (e: React.MouseEvent) => {
    e.preventDefault()
    dragRef.current = {
      x: e.clientX,
      base: resolveContentWidth(columnWidth, pref),
      outward: side === 'right' ? 1 : -1,
      latest: e.clientX,
      frame: null,
    }
    setDragging(true)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [columnWidth, pref])

  useEffect(() => {
    if (!dragging) return
    /** 指针位置 → 内容列宽（居中列：两边各让一半，所以按 2× 位移算） */
    const widthAt = (clientX: number, d: NonNullable<typeof dragRef.current>) =>
      resolveContentWidth(columnWidth, d.base + (d.outward === 1 ? clientX - d.x : d.x - clientX) * 2)
    const onMove = (e: MouseEvent) => {
      const d = dragRef.current
      if (!d) return
      d.latest = e.clientX
      // mousemove 一帧可能来好几次，每帧最多写一次变量
      if (d.frame !== null) return
      d.frame = requestAnimationFrame(() => {
        const cur = dragRef.current
        if (!cur) return
        cur.frame = null
        hostRef.current?.style.setProperty(CONTENT_W_VAR, `${widthAt(cur.latest, cur)}px`)
      })
    }
    const onUp = () => {
      const d = dragRef.current
      if (d) {
        if (d.frame !== null) cancelAnimationFrame(d.frame)
        if (d.latest !== d.x) {
          const final = widthAt(d.latest, d)
          try { localStorage.setItem(CONTENT_W_KEY, String(final)) } catch { /* 隐私模式等写不了就算了 */ }
          // 回写状态：变量交还给声明式，列宽变化时也按新偏好重新收敛
          hostRef.current?.style.setProperty(CONTENT_W_VAR, `${final}px`)
          setPref(final)
        }
      }
      dragRef.current = null
      setDragging(false)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
  }, [dragging, columnWidth, hostRef])

  return { contentWidth: resolveContentWidth(columnWidth, pref), dragging, onHandleDown }
}

/** 内容列宽拖条：落在两侧留白里，窄列时自然收成 0（拖到头也还留得下拖回来的把手） */
export function ColumnWidthHandles({ dragging, onHandleDown, title }: {
  dragging: boolean
  onHandleDown: (side: 'left' | 'right') => (e: React.MouseEvent) => void
  title: string
}) {
  return (
    <>
      {(['left', 'right'] as const).map((side) => (
        <div
          key={side}
          role="separator"
          aria-orientation="vertical"
          onMouseDown={onHandleDown(side)}
          title={title}
          className={`absolute top-0 bottom-0 z-overlay cursor-col-resize transition-colors ${dragging ? 'bg-primary-500/40' : 'hover:bg-primary-500/30'}`}
          style={{
            width: `max(0px, min(40px, calc((100% - var(${CONTENT_W_VAR})) / 2 - 48px)))`,
            ...(side === 'left'
              ? { right: `calc(50% + var(${CONTENT_W_VAR}) / 2 + 24px)` }
              : { left: `calc(50% + var(${CONTENT_W_VAR}) / 2 + 24px)` }),
          }}
        />
      ))}
    </>
  )
}

/** 工具气泡图标：按摘要内容关键词映射（后端文本不带 emoji，图标由前端渲染） */
export function toolIcon(content: string) {
  const s = content || ''
  if (s.includes('接口文档')) return <FileText size={12} />
  if (s.includes('记住') || s.includes('记忆') || s.includes('检索')) return <Brain size={12} />
  if (s.includes('搜索')) return <Search size={12} />
  if (s.includes('获取') || s.includes('http')) return <Globe size={12} />
  if (s.includes('世界代码')) return <Terminal size={12} />
  if (s.includes('压缩')) return <Package size={12} />
  if (s.includes('清空')) return <Eraser size={12} />
  if (s.includes('排队')) return <Clock size={12} />
  return <Wrench size={12} />
}

/** 工具状态行（DSH 式 GenericCommandCard，2026-08-16 借鉴）：
 * 单行折叠条：图标 + 工具名 + 状态点(running/ok/error) + 摘要；可展开看详情
 */
export function ToolBubble({ name, label, detail, error, icon, running }: {
  name?: string; label: string; detail?: string; error?: boolean; icon: React.ReactNode; running?: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  const state = error ? 'error' : running ? 'running' : 'ok'
  // 运行中显示 "进行中…" 摘要；完成显示结果摘要（截断）
  const summary = label.length > 60 ? label.slice(0, 60) + '…' : label
  // 展开内容只有一个来源：优先详情；没有详情时，只有多行摘要才值得展开
  const body = detail || (label.includes('\n') ? label : '')
  const expandable = !!body
  return (
    <div
      className={`world-msg max-w-[90%] mx-auto text-2xs rounded-control overflow-hidden border ${
        state === 'error' ? 'bg-rose-500/10 border-rose-500/25' :
        state === 'running' ? 'bg-mint-400/5 border-mint-400/20' :
        'bg-mint-400/10 border-mint-400/20'
      }`}
    >
      <div
        className={`flex items-center gap-1.5 px-2 py-1 ${expandable ? 'cursor-pointer' : ''}`}
        onClick={() => expandable && setExpanded((v) => !v)}
      >
        <span className="shrink-0 flex items-center justify-center w-3.5 h-3.5 rounded-full border border-current/20" style={{ color: state === 'error' ? 'rgb(var(--tw-rose-400))' : 'rgb(var(--tw-mint-400))' }}>
          {running ? <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" /> :
           error ? <span className="text-[9px] leading-none font-bold">!</span> :
           <span className="text-[8px] leading-none">✓</span>}
        </span>
        <span className="shrink-0 flex items-center gap-1 text-current" style={{ color: state === 'error' ? 'rgb(var(--tw-rose-400))' : 'rgb(var(--tw-mint-400))' }}>
          {icon}
          <span className="font-medium">{running ? '执行中' : error ? '执行失败' : '已完成'}</span>
        </span>
        {name && (
          <span className="shrink-0 px-1 rounded bg-current/10 font-medium" title={name}>{name}</span>
        )}
        <span className="shrink-0 w-px h-2.5 bg-current/20 mx-0.5" aria-hidden />
        <span className="flex-1 min-w-0 truncate" style={{ color: state === 'error' ? 'rgb(var(--tw-rose-400))' : 'rgb(var(--tw-mint-400))' }}>
          {summary}
        </span>
        {expandable && (
          <ChevronDown size={11} className={`shrink-0 text-current/60 transition-transform ${expanded ? 'rotate-180' : ''}`} />
        )}
      </div>
      {expanded && (
        <div className="px-2 pb-1.5 whitespace-pre-wrap text-current max-h-48 overflow-y-auto border-t border-current/10 pt-1.5" style={{ color: state === 'error' ? 'rgb(var(--tw-rose-400))' : 'rgb(var(--tw-mint-400))' }}>
          {body}
        </div>
      )}
    </div>
  )
}
