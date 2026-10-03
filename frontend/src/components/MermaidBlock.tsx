import { useEffect, useLayoutEffect, useRef, useState, useId, memo } from 'react'
import { Loader2, AlertTriangle, Maximize2, Minimize2, ZoomIn, ZoomOut, Download } from 'lucide-react'
import CodeRenderer from './shared/CodeRenderer'
import { useIsDark } from '../hooks/useIsDark'

interface MermaidBlockProps {
  code: string
  /** 是否为聊天消息中的（限制最大宽高） */
  compact?: boolean
}

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------

/**
 * Mermaid sandbox 模式下 render() 返回的是 iframe 包裹的整份 HTML，
 * 纯 SVG 藏在 data URL 里。直接按字符串取，不必等它落进 DOM 再捞。
 */
function svgFromHtml(html: string): string | null {
  let source = html
  const b64 = html.match(/;base64,([^"']+)/)
  if (b64) {
    try {
      source = decodeURIComponent(Array.from(atob(b64[1]), c => '%' + c.charCodeAt(0).toString(16).padStart(2, '0')).join(''))
    } catch { return null }
  }
  const svgs = source.match(/<svg[\s\S]*?<\/svg>/gi)
  return svgs ? svgs[svgs.length - 1] : null
}

/**
 * 去掉根 svg 上的固定尺寸（width / height / style 里的 max-width）。
 * mermaid 会按图表内容算出一小块尺寸写进属性，小图就被钉死成一丁点大，
 * 同一页里几张图因此大小不一；尺寸交给容器的 CSS 决定。
 * 顺手注入 CSS 防止 CJK 字符被 foreignObject 裁剪（见 mermaid#4950、#7359）。
 */
function normalizeSvgSize(svg: string): string {
  const cleaned = svg.replace(/<svg\b[^>]*>/i, (tag) => tag
    .replace(/\s+width="[^"]*"/i, '')
    .replace(/\s+height="[^"]*"/i, '')
    .replace(/\s+style="[^"]*"/i, ''))
  const styleTag = '<style>foreignObject{overflow:visible!important}</style>'
  return cleaned.includes(styleTag) ? cleaned : cleaned.replace('</svg>', styleTag + '</svg>')
}

/** 全屏图的缩放范围与挡位：跨 40 倍用加减法得按几十次，所以按钮走倍率 */
const ZOOM_MIN = 0.25
const ZOOM_MAX = 10
const ZOOM_STEP = 1.25
const WHEEL_RATE = 1.0015

const loadMermaid = async () => (await import('mermaid')).default
type Mermaid = Awaited<ReturnType<typeof loadMermaid>>
/** mermaid 只加载一份，所有 MermaidBlock 实例共用 */
const mermaidPromise = loadMermaid()

/**
 * 渲染前整体落一次配置。
 *
 * mermaid 的 initialize 是**整体替换**而不是合并：只传 theme 会把没提到的项
 * 一起打回默认值 —— suppressErrorRendering 变成 false（语法错误又开始画那张
 * "Syntax error" 炸弹图）、securityLevel 掉回 strict、字体栈也复位。
 * 所以这几项必须一处给全，别分两次 initialize。
 */
function setupMermaid(mermaid: Mermaid, isDark: boolean) {
  mermaid.initialize({
    startOnLoad: false,
    theme: isDark ? 'dark' : 'default',
    securityLevel: 'sandbox',
    // 页面真实的字体栈：写成 'inherit' 时 mermaid 拿去量文字的 canvas 会退回
    // 默认字体，量出的字宽偏小，节点框就比字小一截，字被框切掉
    fontFamily: getComputedStyle(document.body).fontFamily,
    // 语法错误直接 throw，由组件的 catch 统一处理，别渲染 mermaid 自带的错误图
    suppressErrorRendering: true,
  })
}



// ---------------------------------------------------------------------------
// 设置读取
// ---------------------------------------------------------------------------

function getMermaidSetting(key: string, fallback: boolean): boolean {
  try {
    return localStorage.getItem(key) === null ? fallback : localStorage.getItem(key) === 'true'
  } catch {
    return fallback
  }
}

// ---------------------------------------------------------------------------
// 主组件
// ---------------------------------------------------------------------------

function MermaidBlock({ code, compact = false }: MermaidBlockProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [svg, setSvg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)

  // expandLevel: 0=未展开, 1=已点击但正在渲染, 2=已显示结果
  // 聊天里默认折叠，等用户点；手册页那种非紧凑场景没有展开按钮，得自己开始渲染
  const [expandLevel, setExpandLevel] = useState(compact ? 0 : 1)
  const [errorRevealed, setErrorRevealed] = useState(false)
  const uniqueId = useId().replace(/:/g, '')
  // 渲染序号：渲好的 svg 会带着 mermaid 的 id 留在页面上，切主题要重渲时
  // 再拿同一个 id 去 render 会和它撞，mermaid 会认错元素
  const seqRef = useRef(0)
  // 配色跟着应用主题走：mermaid 的线色是渲染那一刻写进 svg 的
  const isDark = useIsDark()

  // 默认折叠设置（仅 compact 模式生效）
  const collapseDefault = compact && getMermaidSetting('mermaid_collapse', true)
  const collapseErrors = compact && getMermaidSetting('mermaid_collapse_errors', true)
  const isCollapsed = collapseDefault && expandLevel === 0
  const isErrorCollapsed = error && collapseErrors && !errorRevealed

  // ---- 渲染 mermaid（仅在用户点击展开后执行） ----
  useEffect(() => {
    if (expandLevel === 0) return
    let cancelled = false

    async function render() {
      try {
        const mermaid = await mermaidPromise
        setupMermaid(mermaid, isDark)
        // 字体没就绪时量出来的字宽是替补字体的，先等一等
        if (document.fonts && document.fonts.status !== 'loaded') await document.fonts.ready
        const { svg: rendered } = await mermaid.render(`mermaid-${uniqueId}-r${seqRef.current++}`, code)
        if (cancelled) return

        if (/translate\(NaN/.test(rendered)) {
          setError('渲染坐标异常（NaN），图表包含不支持的字符')
          setSvg(null)
        } else {
          const raw = svgFromHtml(rendered)
          setSvg(raw ? normalizeSvgSize(raw) : null)
          setError(raw ? null : 'Mermaid 渲染结果里没有 SVG')
        }
      } catch (err: any) {
        if (!cancelled) {
          setError(err?.message || 'Mermaid 渲染失败')
          setSvg(null)
        }
      }
    }

    render()
    return () => { cancelled = true }
  }, [code, uniqueId, expandLevel, isDark])

  // ---- 展开全屏（用 ref 存 svg，避免 StrictMode 双渲导致 useCallback 闭包过期） ----
  // 全屏缩放/拖拽 refs（与原版保持一致，不抽 hook，避免 StrictMode 下引用问题）
  const overlayRef = useRef<HTMLDivElement>(null)
  const zoomRef = useRef(1)
  const panRef = useRef({ x: 0, y: 0 })
  const dragRef = useRef<{ startX: number; startY: number; panX: number; panY: number } | null>(null)
  const svgWrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!expanded) return
    const up = () => { dragRef.current = null }
    document.addEventListener('mouseup', up)
    return () => document.removeEventListener('mouseup', up)
  }, [expanded])

  useEffect(() => {
    const el = overlayRef.current
    if (!el || !expanded) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY
      zoomRef.current = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, zoomRef.current * Math.pow(WHEEL_RATE, -dy)))
      updateTransform()
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [expanded])

  const updateTransform = (smooth = false) => {
    const el = svgWrapRef.current
    if (!el) return
    el.style.transition = smooth ? 'transform 0.12s ease-out' : 'none'
    el.style.transform = `translate3d(${panRef.current.x}px, ${panRef.current.y}px, 0) scale(${zoomRef.current})`
  }

  const resetTransform = () => {
    zoomRef.current = 1
    panRef.current = { x: 0, y: 0 }
    updateTransform(true)
  }

  const zoomIn = () => { zoomRef.current = Math.min(ZOOM_MAX, zoomRef.current * ZOOM_STEP); updateTransform(true) }
  const zoomOut = () => { zoomRef.current = Math.max(ZOOM_MIN, zoomRef.current / ZOOM_STEP); updateTransform(true) }

  const svgRef = useRef(svg)
  svgRef.current = svg

  // 构建错误报告消息
  const buildReportMsg = (err: string) => {
    const lang = document.documentElement.lang?.startsWith('zh') ? 'zh-CN' : 'en'
    const msgZh = `Mermaid 图表渲染失败：${err}\n\n原始代码：\n\`\`\`mermaid\n${code}\n\`\`\``
    const msgEn = `Mermaid diagram failed to render: ${err}\n\nOriginal code:\n\`\`\`mermaid\n${code}\n\`\`\``
    return lang === 'zh-CN' ? msgZh : msgEn
  }
  const dispatchErrorReport = (err: string) => {
    document.dispatchEvent(new CustomEvent('mermaid-error-report', { detail: { message: buildReportMsg(err) } }))
  }

  const handleExpand = () => setExpanded(true)

  const handleClose = () => {
    setExpanded(false)
    resetTransform()
  }

  // ---- 渲染分支 ----

  // 折叠占位：检测到 ```mermaid 代码块但尚未点击展开
  if (isCollapsed) {
    return (
      <div className={compact ? 'my-2' : 'my-4'}>
        <button
          onClick={() => setExpandLevel(1)}
          className="flex items-center gap-1.5 px-3 py-2 rounded-control border w-full text-xs border-border/50 bg-elevated/30 hover:bg-elevated text-textMuted hover:text-textSecondary"
        >
          <Maximize2 size={13} />
          <span>展开 Mermaid 图表</span>
        </button>
      </div>
    )
  }

  // 错误折叠：渲染完成后出错，但用户尚未点击查看
  if (isErrorCollapsed) {
    return (
      <div className={compact ? 'my-2' : 'my-4'}>
        <div className="flex items-center gap-1.5 px-3 py-2 rounded-control border w-full text-xs border-rose-400/20 bg-rose-400/5">
          <button
            onClick={() => setErrorRevealed(true)}
            className="flex items-center gap-1 text-rose-400/70 hover:text-rose-400 transition-colors shrink-0"
          >
            <AlertTriangle size={13} />
            <span>查看详情</span>
          </button>
          <span className="text-rose-400/20">·</span>
          <button
            onClick={() => error && dispatchErrorReport(error)}
            className="text-rose-400/70 hover:text-rose-400 transition-colors shrink-0"
          >
            报告错误给AI
          </button>
        </div>
      </div>
    )
  }

  // 加载态（用户已点击但尚未完成）：保持按钮可见，仅改文字
  if (!svg && !error) {
    if (compact) {
      return (
        <div className={compact ? 'my-2' : 'my-4'}>
          <div className="flex items-center gap-1.5 px-3 py-2 rounded-control border w-full text-xs border-border/50 bg-elevated/30 text-textMuted">
            <Loader2 size={12} className="animate-spin" />
            <span>图表加载中...</span>
          </div>
        </div>
      )
    }
    return (
      <div ref={containerRef} className="my-3 rounded-card border border-border bg-elevated p-4 flex items-center gap-2 text-textMuted text-sm">
        <Loader2 size={14} className="animate-spin" />
        图表加载中...
      </div>
    )
  }

  // 错误态
  if (error) {
    return (
      <div className="my-3 rounded-card border border-rose-400/20 bg-rose-400/5 overflow-hidden">
        <div className="flex items-center justify-between gap-1.5 px-3 py-1.5 bg-rose-400/10 border-b border-rose-400/10">
          <div className="flex items-center gap-1.5 text-3xs text-rose-400 font-medium">
            <AlertTriangle size={12} /> Mermaid 图表渲染失败
          </div>
          <button
            onClick={() => dispatchErrorReport(error)}
            className="text-3xs px-2 py-0.5 rounded-control bg-rose-400/15 hover:bg-rose-400/25 text-rose-400 transition-colors"
            title="将错误信息发送给AI，帮助其修正"
          >
            报告错误给AI
          </button>
        </div>
        {error && <div className="px-3 py-1 text-2xs text-rose-400/80 font-mono">{error}</div>}
        <div className="px-3 py-2 text-xs">
          <div className="text-textMuted mb-1">以下为原始代码：</div>
          <CodeRenderer className="" inline={false}>
            {code}
          </CodeRenderer>
        </div>
      </div>
    )
  }



  // 成功态
  const displaySvg = svg
  const isFullscreenClass = expanded
    ? 'w-screen h-screen flex items-center justify-center p-8 overflow-auto'
    : 'overflow-x-auto p-4' + (compact ? ' max-h-[420px] overflow-y-auto' : '')

  return (
    <>
      <div className={
        (compact ? 'my-2 max-w-full' : 'my-4')
        + ' rounded-card border border-border bg-white dark:bg-[#1e1e2e] [clip-path:inset(0_round_1rem)]' // clip-path 裁圆角但不去掉滚动能力
      }>
        {/* 标题栏 */}
        <div className="flex items-center justify-between px-3 py-1.5 bg-elevated/50 border-b border-border">
          <span className="text-3xs text-textMuted font-medium tracking-wide uppercase">
            Mermaid
          </span>
          {compact && (
            <button
              onClick={handleExpand}
              className="p-0.5 rounded hover:bg-surface text-textMuted hover:text-textPrimary transition-colors"
              title="全屏查看"
            >
              <Maximize2 size={13} />
            </button>
          )}
        </div>

        {/* SVG 内容 */}
        <div
          ref={containerRef}
          className={'mermaid-stage ' + isFullscreenClass}
          dangerouslySetInnerHTML={{ __html: displaySvg ?? '' }}
        />
      </div>

      {/* 全屏浮层：使用宽度修正后的 SVG + 缩放/拖拽 */}
      {expanded && (
        <div
          /* 遮罩跟着主题走：图是透明底，日间的深线落在黑幕上会看不见；
             一直半透明 + 模糊，别把底下的页面盖成一块实色 */
          className={'fixed inset-0 z-modal backdrop-blur-sm ' + (isDark ? 'bg-black/80' : 'bg-canvas/85')}
          ref={overlayRef}
          onMouseDown={(e) => {
            dragRef.current = { startX: e.clientX, startY: e.clientY, panX: panRef.current.x, panY: panRef.current.y }
          }}
          onMouseMove={(e) => {
            if (!dragRef.current) return
            panRef.current = { x: dragRef.current.panX + e.clientX - dragRef.current.startX, y: dragRef.current.panY + e.clientY - dragRef.current.startY }
            updateTransform()
          }}
          onMouseUp={() => { dragRef.current = null }}
          onClick={(e) => { if (e.target === e.currentTarget) handleClose() }}
        >
          <div className={isFullscreenClass}>
            {/* 工具栏 */}
            <div className="absolute top-4 right-4 flex items-center gap-2 z-10">
              <button onClick={zoomIn} className="icon-btn icon-btn-overlay" title="放大">
                <ZoomIn size={18} />
              </button>
              <button onClick={zoomOut} className="icon-btn icon-btn-overlay" title="缩小">
                <ZoomOut size={18} />
              </button>
              <button onClick={resetTransform} className="icon-btn icon-btn-overlay text-xs font-medium" title="重置">
                还原
              </button>
              <button onClick={() => {
                const a = document.createElement('a')
                const raw = svg
                a.href = 'data:image/svg+xml,' + encodeURIComponent(raw ?? '')
                a.download = 'diagram.svg'
                a.click()
              }} className="icon-btn icon-btn-overlay" title="下载 SVG">
                <Download size={18} />
              </button>
              <button onClick={handleClose} className="icon-btn icon-btn-overlay" title="关闭">
                <Minimize2 size={18} />
              </button>
            </div>
            <div
              ref={svgWrapRef}
              className="mermaid-stage-fill cursor-grab active:cursor-grabbing w-full h-full"
              style={{ transition: 'transform 0.12s ease-out' }}
              dangerouslySetInnerHTML={{ __html: displaySvg ?? '' }}
            />
          </div>
        </div>
      )}
    </>
  )
}

export default memo(MermaidBlock, (prev, next) => prev.code === next.code && prev.compact === next.compact)
