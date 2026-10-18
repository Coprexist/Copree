/**
 * 书签目录（本页目录 / 正文书签）—— 长内容右侧的锚点侧栏，一份实现，两种取条目方式：
 *
 *  · 文档页：给 targetRef，从它渲染出来的 h1–h4 读标题（id 就是正文那份锚点，不另抄清单）；
 *  · 日志正文：给 entries，条目由渲染方按分块算（正文是分片补渲染的，DOM 里未必齐）。
 * 滚动位置既可以从 targetRef 往上自己找容器，也可以由调用方直接给（日志正文的滚动在
 * [data-export-flat] 那一层）。宽屏常驻右栏、跟着滚动高亮，窄屏收成浮起的小按钮 + 底部抽屉。
 * 三件事：搜条目（标题 + 顺带给的检索文本）、回到开头 / 跳到结尾、复制这一条的锚点链接。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { Check, ChevronDown, ChevronUp, Link2, ListTree, Search, X } from 'lucide-react'
import Dialog from '../ui/Dialog'
import { useT } from '../../i18n/I18nContext'
import { scrollToAnchor } from '../../utils/scrollAnchor'

export interface OutlineEntry {
  id: string
  /** 列表里那一行 */
  text: string
  /** 第二行小字（日志正文用：这段正文的开头几个字），可省 */
  detail?: string
  /** 搜索时额外匹配的文本（正文全文），可省 */
  terms?: string
  /** 层级：文档页按 h1–h4 缩进；平铺的内容（日志分块）不给就是 0 */
  level?: number
}

interface Props {
  /** 文档模式：从这个容器里读 h1–h4 当条目 */
  targetRef?: RefObject<HTMLElement | null>
  /** 显式条目模式：条目由调用方给（与 targetRef 二选一） */
  entries?: OutlineEntry[]
  /** 显式滚动容器；不给就自己从 targetRef 往上找（本站滚动挂在 main 上） */
  scroller?: () => HTMLElement | null
  /** 跳转之前先把目标铺出来（分片渲染时它可能还没进 DOM），铺完由调用方负责滚过去 */
  onReveal?: (id: string) => void
  /** 目录标题，默认「本页目录」 */
  heading?: string
  /** 右栏的类：文档页是吸顶一窄栏，日志页是贴着面板的一列 */
  railClassName?: string
  /** 地址栏能不能把这一条当锚点（文档页能；日志正文的块锚点是内部 id，不往地址栏写、也不给复制） */
  linkable?: boolean
  /** 搜索框占位文案，默认「搜索小节…」（日志正文用「搜索正文…」） */
  searchLabel?: string
}

const HEADINGS = 'h1, h2, h3, h4'
/** 滚到某一条时上方留的空白；判定「当前条目」时也给同样的余量 */
const GAP = 16
const SPY_LINE = 96
/** 少于这么多条就不必占一栏（短内容不需要目录） */
const MIN_ENTRIES = 3
const DOC_RAIL =
  'hidden xl:flex xl:flex-col xl:self-start xl:w-56 xl:shrink-0 xl:sticky xl:top-6 xl:max-h-[calc(100dvh-3rem)]'

/**
 * 页面真正的滚动容器：本站页面滚动挂在 main 上（不是 window），
 * 所以既不能 window.scrollTo，也不能拿 window.scrollY 当进度。
 */
function scrollHost(el: Element | null): HTMLElement | null {
  let node = el?.parentElement ?? null
  while (node) {
    const { overflowY } = getComputedStyle(node)
    if ((overflowY === 'auto' || overflowY === 'scroll') && node.scrollHeight > node.clientHeight + 1) {
      return node as HTMLElement
    }
    node = node.parentElement
  }
  return null
}

export default function OutlineNav({
  targetRef, entries, scroller, onReveal, heading, railClassName = DOC_RAIL, linkable = true, searchLabel,
}: Props) {
  const t = useT()
  const [domEntries, setDomEntries] = useState<OutlineEntry[]>([])
  const [activeId, setActiveId] = useState('')
  const [progress, setProgress] = useState(0)
  const [query, setQuery] = useState('')
  const [sheetOpen, setSheetOpen] = useState(false)
  const [copied, setCopied] = useState('')

  // 回调放 ref 里：调用方每次渲染都新写一个箭头函数，不该因此重装滚动监听
  const scrollerRef = useRef(scroller)
  scrollerRef.current = scroller
  const revealRef = useRef(onReveal)
  revealRef.current = onReveal
  const targetRefRef = useRef(targetRef)
  targetRefRef.current = targetRef

  const items = entries ?? domEntries

  // 文档模式：标题跟着正文走（正文是异步取回来的、也可能整篇换掉，所以读 DOM 而不是传数组）
  useEffect(() => {
    if (entries || !targetRef) return
    const root = targetRef.current
    if (!root) return
    const read = () => {
      const found: OutlineEntry[] = Array.from(root.querySelectorAll<HTMLElement>(HEADINGS))
        .filter(node => node.id && node.textContent?.trim())
        .map(node => ({ id: node.id, text: (node.textContent || '').trim(), level: Number(node.tagName.slice(1)) }))
      // 逐项比：没变就别换引用，免得下面的滚动监听被反复拆装
      setDomEntries(prev => (prev.length === found.length && prev.every((e, i) =>
        e.id === found[i].id && e.text === found[i].text && e.level === found[i].level) ? prev : found))
    }
    read()
    const observer = new MutationObserver(read)
    observer.observe(root, { childList: true, subtree: true, characterData: true })
    return () => observer.disconnect()
  }, [entries, targetRef])

  const host = useCallback(
    () => (scrollerRef.current ? scrollerRef.current() : scrollHost(targetRefRef.current?.current ?? null)),
    [],
  )

  // 滚动位置 → 当前条目 + 已读进度
  useEffect(() => {
    if (items.length === 0) return
    const scrollerEl: HTMLElement | Window = host() ?? window
    let frame = 0
    const measure = () => {
      frame = 0
      const el = host()
      const top = el ? el.scrollTop : window.scrollY
      const span = el
        ? el.scrollHeight - el.clientHeight
        : document.documentElement.scrollHeight - window.innerHeight
      setProgress(span > 4 ? Math.min(100, Math.max(0, Math.round((top / span) * 100))) : 100)
      const base = el ? el.getBoundingClientRect().top : 0
      let current = items[0].id
      for (const entry of items) {
        const node = document.getElementById(entry.id)
        if (!node) continue
        // 条目在内容里的绝对位置（相对滚动容器顶部）
        const offset = node.getBoundingClientRect().top - base + top
        if (offset > top + SPY_LINE) break
        current = entry.id
      }
      setActiveId(current)
    }
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(measure) }
    measure()
    scrollerEl.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      if (frame) cancelAnimationFrame(frame)
      scrollerEl.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  }, [items, host])

  // 当前条目跟着目录自己走：目录比可视区高时把高亮那条挪进来（只动目录自己的滚动，不带动正文）
  useEffect(() => {
    if (!activeId) return
    for (const list of document.querySelectorAll<HTMLElement>('[data-outline-list]')) {
      const item = list.querySelector<HTMLElement>(`[data-outline-entry='${CSS.escape(activeId)}']`)
      if (!item) continue
      const box = list.getBoundingClientRect()
      if (!box.height) continue
      const row = item.getBoundingClientRect()
      if (row.top < box.top + 8) list.scrollTop += row.top - box.top - 8
      else if (row.bottom > box.bottom - 8) list.scrollTop += row.bottom - box.bottom + 8
    }
  }, [activeId])

  /** 滚到某一条：滚过去 + 滚停后校准（迟到的重排会让落点漂） */
  const jump = useCallback((id: string) => {
    const node = document.getElementById(id)
    if (!node) return
    const el = host()
    if (!el) {
      node.scrollIntoView({ behavior: 'smooth', block: 'start' })
      return
    }
    scrollToAnchor(() => ({ scroller: host(), target: document.getElementById(id) }), { gap: GAP })
  }, [host])

  /** 选一条：需要先铺出来的交给调用方，其余自己滚；地址栏记成这一条的锚点（这个地址本身就是书签） */
  const pick = (id: string) => {
    if (revealRef.current) revealRef.current(id)
    else jump(id)
    if (linkable) history.replaceState(null, '', `#${id}`)
    setSheetOpen(false)
  }

  const toEdge = (edge: 'top' | 'bottom') => {
    const el = host()
    const top = edge === 'top' ? 0 : (el ? el.scrollHeight : document.documentElement.scrollHeight)
    if (el) el.scrollTo({ top, behavior: 'smooth' })
    else window.scrollTo({ top, behavior: 'smooth' })
    setSheetOpen(false)
  }

  const copy = async (id: string) => {
    try {
      await navigator.clipboard.writeText(`${location.origin}${location.pathname}#${id}`)
      setCopied(id)
      window.setTimeout(() => setCopied(c => (c === id ? '' : c)), 1400)
    } catch {
      // 没有 https / 没授权时静默：这只是顺手的一个入口，不打断阅读
    }
  }

  const minLevel = useMemo(
    () => items.reduce((min, entry) => Math.min(min, entry.level ?? 0), 9),
    [items],
  )
  const keyword = query.trim().toLowerCase()
  const shown = useMemo(() => {
    if (!keyword) return items
    return items.filter(entry =>
      entry.text.toLowerCase().includes(keyword) ||
      (entry.detail || '').toLowerCase().includes(keyword) ||
      (entry.terms || '').toLowerCase().includes(keyword))
  }, [items, keyword])

  if (items.length < MIN_ENTRIES) return null

  const panel = (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="relative shrink-0">
        <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-textMuted pointer-events-none" />
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && shown[0]) pick(shown[0].id) }}
          placeholder={searchLabel ?? t('outline:search')}
          className="w-full pl-7 pr-6 py-1.5 rounded-control border border-border bg-canvas text-xs text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/30"
        />
        {query !== '' && (
          <button
            type="button"
            onClick={() => setQuery('')}
            className="absolute right-1.5 top-1/2 -translate-y-1/2 p-0.5 rounded text-textMuted hover:text-textPrimary transition-colors"
          >
            <X size={11} />
          </button>
        )}
      </div>

      <div data-outline-list className="flex-1 min-h-0 overflow-y-auto scroll-floor mt-2 pr-1 space-y-px">
        {shown.length === 0 ? (
          <p className="text-3xs text-textMuted px-2 py-2">{t('outline:noMatch')}</p>
        ) : shown.map(entry => {
          const on = entry.id === activeId
          return (
            <div
              key={entry.id}
              className={`group flex items-start rounded-control border-l-2 transition-colors ${
                on ? 'border-primary-500 bg-primary-500/5' : 'border-transparent'
              }`}
            >
              <button
                type="button"
                data-outline-entry={entry.id}
                onClick={() => pick(entry.id)}
                style={{ paddingLeft: 6 + ((entry.level ?? 0) - minLevel) * 12 }}
                className={`flex-1 min-w-0 py-1 pr-1 text-left text-3xs leading-snug transition-colors ${
                  on ? 'text-primary-400' : 'text-textMuted hover:text-textSecondary'
                }`}
              >
                <span className="block truncate">{entry.text}</span>
                {entry.detail && (
                  <span className={`block truncate text-[10px] ${on ? 'text-primary-400/70' : 'text-textMuted/70'}`}>
                    {entry.detail}
                  </span>
                )}
              </button>
              {linkable && (
                <button
                  type="button"
                  onClick={() => copy(entry.id)}
                  title={copied === entry.id ? t('outline:copied') : t('outline:copyLink')}
                  className="shrink-0 p-1 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 text-textMuted hover:text-primary-400 transition-opacity"
                >
                  {copied === entry.id ? <Check size={11} /> : <Link2 size={11} />}
                </button>
              )}
            </div>
          )
        })}
      </div>

      <div className="flex items-center gap-1 pt-2 mt-2 border-t border-border shrink-0">
        <button
          type="button"
          onClick={() => toEdge('top')}
          className="flex-1 flex items-center justify-center gap-1 py-1 rounded-control text-3xs text-textMuted hover:text-textPrimary hover:bg-elevated transition-colors"
        >
          <ChevronUp size={12} />{t('outline:top')}
        </button>
        <button
          type="button"
          onClick={() => toEdge('bottom')}
          className="flex-1 flex items-center justify-center gap-1 py-1 rounded-control text-3xs text-textMuted hover:text-textPrimary hover:bg-elevated transition-colors"
        >
          <ChevronDown size={12} />{t('outline:bottom')}
        </button>
      </div>
    </div>
  )

  return (
    <>
      {/* 宽屏：右栏，跟着滚动停住（日志正文里则是贴着正文那一列） */}
      <aside className={railClassName}>
        <div className="flex items-baseline justify-between px-2 pb-1 shrink-0">
          <span className="text-3xs font-medium text-textSecondary">{heading ?? t('outline:title')}</span>
          <span className="text-3xs text-textMuted tabular-nums">{progress}%</span>
        </div>
        <div className="h-0.5 mx-2 mb-2 rounded-full bg-border overflow-hidden shrink-0">
          <div className="h-full rounded-full bg-primary-500 transition-[width] duration-150" style={{ width: `${progress}%` }} />
        </div>
        {panel}
      </aside>

      {/* 窄屏：右下角一个浮起的小按钮，点开是底部抽屉（同一份列表，不另写一套） */}
      <button
        type="button"
        onClick={() => setSheetOpen(true)}
        className="xl:hidden fixed right-4 bottom-[calc(var(--safe-bottom)+0.75rem)] md:bottom-6 z-overlay flex items-center gap-1.5 pl-2.5 pr-3 py-2 rounded-full border border-border bg-surface text-3xs text-textSecondary shadow-lg"
      >
        <ListTree size={14} className="text-primary-400" />
        {t('outline:open')}
        <span className="tabular-nums text-textMuted">{progress}%</span>
      </button>
      {sheetOpen && (
        <Dialog onClose={() => setSheetOpen(false)} className="flex items-end justify-center xl:hidden">
          <div className="w-full max-h-[75vh] flex flex-col rounded-t-dialog border-t border-border bg-surface p-3 pb-[calc(var(--safe-bottom)+0.75rem)]">
            <div className="flex items-center justify-between pb-2 shrink-0">
              <span className="text-xs font-medium text-textPrimary">{heading ?? t('outline:title')}</span>
              <button
                type="button"
                onClick={() => setSheetOpen(false)}
                className="p-1 rounded-control text-textMuted hover:text-textPrimary hover:bg-elevated transition-colors"
              >
                <X size={14} />
              </button>
            </div>
            {panel}
          </div>
        </Dialog>
      )}
    </>
  )
}
