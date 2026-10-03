import { useState, useEffect, useCallback, useRef } from 'react'
import { Download, X, ArrowLeft, FileIcon, Loader2, AlertTriangle, ZoomIn, ZoomOut, RotateCcw, Share2, Maximize2, Minimize2 } from 'lucide-react'
import { useT } from '../i18n/I18nContext'
import { fileDownloadUrl } from '../api/client'
import { formatFileSize } from '../utils/format'
import { isTextPreviewable, getCodeLang, isMarkdownFile, resolveMimeType, EXT_LANG_MAP } from '../utils/mime'
import MarkdownContent from './shared/MarkdownContent'
import ForwardFileModal from './ForwardFileModal'
import { Dialog } from './ui'

// FileCodeRenderer ——已迁移到 components/shared/CodeRenderer.tsx

interface FilePreviewModalProps {
  fileId?: number
  fileName: string
  fileSize: number
  mimeType: string
  onClose: () => void
  /** 可选：直接内容 URL（世界文件等非附件场景）；缺省用 fileId 走附件下载接口 */
  src?: string
  /** 可选：已加载的文本内容（设计页编辑器已有内容时直接复用，免二次请求） */
  initialContent?: string | null
}

export default function FilePreviewModal({ fileId, fileName, fileSize, mimeType, onClose, src, initialContent }: FilePreviewModalProps) {
  const t = useT()
  const [content, setContent] = useState<string | null>(initialContent ?? null)
  const [loading, setLoading] = useState(initialContent == null)
  const [error, setError] = useState('')
  // 图片缩放
  const [scale, setScale] = useState(1)
  const imgContainerRef = useRef<HTMLDivElement>(null)
  const [forwardFile, setForwardFile] = useState<{file_id:number;name:string;size:number;mime_type:string}|null>(null)
  // 富文本（md/html/代码）渲染 ↔ 原文切换：看渲染效果或源码

  const dlUrl = src ?? (fileId != null ? fileDownloadUrl(fileId) : '')

  // 模态框尺寸状态
  const [modalWidth, setModalWidth] = useState<number | null>(null)
  const [modalHeight, setModalHeight] = useState<number | null>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const modalRef = useRef<HTMLDivElement>(null)

  // 拖拽缩放 — 跟 sidebar 列表拖拽一个模式：直接根据鼠标实时位置算尺寸
  const resizing = useRef<'e'|'w'|'s'|'n'|'se'|'sw'|'ne'|'nw'|null>(null)
  const resizeCenter = useRef({ x: 0, y: 0 })
  const minW = 420, minH = 320
  const wasResizing = useRef(false)

  const doResize = useCallback((e: MouseEvent) => {
    const el = modalRef.current
    if (!el || !resizing.current) return
    const dir = resizing.current
    const cx = resizeCenter.current.x
    const cy = resizeCenter.current.y

    // 弹窗居中布局，用鼠标相对中轴的方向性距离算宽高
    // 左边缘：width = 2 × (centerX - mouseX)  右边缘：width = 2 × (mouseX - centerX)
    // 上边缘：height = 2 × (centerY - mouseY)  下边缘：height = 2 × (mouseY - centerY)
    // 不能用 Math.abs！左边缘拖到中心右侧时应该缩到最小而不是反弹
    if (dir.includes('w')) {
      el.style.width = Math.max(minW, 2 * (cx - e.clientX)) + 'px'
    } else if (dir.includes('e')) {
      el.style.width = Math.max(minW, 2 * (e.clientX - cx)) + 'px'
    }
    if (dir.includes('n')) {
      el.style.maxHeight = 'none'
      el.style.height = Math.max(minH, 2 * (cy - e.clientY)) + 'px'
    } else if (dir.includes('s')) {
      el.style.maxHeight = 'none'
      el.style.height = Math.max(minH, 2 * (e.clientY - cy)) + 'px'
    }
  }, [])

  const onResizeEnd = useCallback(() => {
    resizing.current = null
    document.removeEventListener('mousemove', doResize)
    document.removeEventListener('mouseup', onResizeEnd)
    wasResizing.current = true
    setTimeout(() => { wasResizing.current = false }, 200)
    const overlay = document.getElementById('resize-mouse-overlay')
    if (overlay) overlay.remove()
    setTimeout(() => {
      const rect = modalRef.current?.getBoundingClientRect()
      if (rect) {
        setModalWidth(rect.width)
        setModalHeight(rect.height)
      }
    }, 80)
  }, [doResize])

  const startResize = useCallback((dir: 'e'|'w'|'s'|'n'|'se'|'sw'|'ne'|'nw') => (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    const rect = modalRef.current?.getBoundingClientRect()
    if (!rect) return
    resizeCenter.current = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
    resizing.current = dir
    // 加遮挡层——内容区（iframe/img）会拦截 mousemove，移出弹窗才恢复
    const overlay = document.createElement('div')
    overlay.id = 'resize-mouse-overlay'
    overlay.style.cssText = 'position:absolute;inset:0;z-index:999;pointer-events:auto'
    modalRef.current?.appendChild(overlay)
    document.addEventListener('mousemove', doResize)
    document.addEventListener('mouseup', onResizeEnd)
  }, [doResize, onResizeEnd])

  // 全屏切换
  const toggleFullscreen = useCallback(() => {
    setIsFullscreen((prev) => !prev)
  }, [])

  // 优先后端 mimeType，缺失时从文件名扩展名推断
  const resolvedMime = resolveMimeType(fileName, mimeType)

  const isImage = resolvedMime.startsWith('image/')
  const isPDF = resolvedMime === 'application/pdf'
  const isDocx = resolvedMime === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    || fileName.endsWith('.docx')
  const isText = isTextPreviewable(resolvedMime)
  const isHtml = resolvedMime === 'text/html' || fileName.endsWith('.html') || fileName.endsWith('.htm')
  const previewable = isImage || isPDF || isDocx || isText || isHtml
  const isMd = isMarkdownFile(fileName, resolvedMime)
  const codeLang = isHtml ? '' : getCodeLang(fileName, resolvedMime)  // HTML 用 iframe 渲染
  const isRichText = isMd || isHtml || !!codeLang
  const [showSource, setShowSource] = useState(false)

  // 重试：新文件可能后台还没处理完，点开失败就重试
  const RETRY_MAX = 3
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    if (initialContent != null) {
      // 内容已由调用方提供（如设计页编辑器）：直接渲染，不再请求
      setLoading(false)
      return
    }

    if (!previewable) {
      // 不可预览 → 直接触发下载
      const a = document.createElement('a')
      a.href = dlUrl
      a.download = fileName
      a.click()
      onClose()
      return
    }

    // 图片 / PDF：不需要 fetch 文本内容
    if (isImage || isPDF) {
      // 但可能还没处理完，加个 retry key 让 etag/cache 失效
      setLoading(false)
      return
    }

    let cancelled = false
    const tryFetch = async (attempt: number) => {
      if (cancelled) return
      try {
        const res = await fetch(dlUrl + `&_=${retry}` + (attempt > 0 ? `&r=${attempt}` : ''))
        if (cancelled) return
        if (!res.ok) throw new Error(`HTTP ${res.status}`)

        if (isDocx) {
          const { default: mammoth } = await import('mammoth')
          const buf = await res.arrayBuffer()
          const result = await mammoth.convertToHtml({ arrayBuffer: buf })
          if (!cancelled) { setContent(result.value); setLoading(false) }
          return
        }

        let text = await res.text()
        if (text.length > 2 * 1024 * 1024) {
          text = text.slice(0, 2 * 1024 * 1024) + '\n\n' + t('filePreview.fileTooLarge')
        }
        if (codeLang) {
          text = '```' + codeLang + '\n' + text + '\n```'
        }
        if (!cancelled) { setContent(text); setLoading(false) }
      } catch (err: any) {
        if (cancelled) return
        if (attempt < RETRY_MAX - 1) {
          // 等一会儿重试：新文件可能还没处理完
          const delay = (attempt + 1) * 1000
          await new Promise(r => setTimeout(r, delay))
          if (!cancelled) tryFetch(attempt + 1)
        } else {
          if (err.name !== 'AbortError') {
            setError(err.message || t('common.loadFailed'))
            setLoading(false)
          }
        }
      }
    }
    tryFetch(0)

    return () => { cancelled = true }
  }, [fileId, previewable, dlUrl, fileName, onClose, t, isImage, isPDF, isDocx, codeLang, retry])

  // 缩放滑块常量
  const ZOOM_MIN = 0.5
  const ZOOM_MAX = 4

  // 缩放控制 — +/- 按钮
  const zoomIn = useCallback(() => setScale((s) => Math.min(s + 0.25, ZOOM_MAX)), [])
  const zoomOut = useCallback(() => setScale((s) => Math.max(s - 0.25, ZOOM_MIN)), [])
  const zoomReset = useCallback(() => setScale(1), [])

  // 缩放滑块 — ref 直写 DOM，跟 resize 一个模式
  const sliderTrackRef = useRef<HTMLDivElement>(null)
  const slidering = useRef(false)
  const sliderDisplayRef = useRef<HTMLSpanElement>(null)

  // 直接操作 img 和 slider DOM，不触发 React 重渲染
  const applyZoom = useCallback((pct: number) => {
    const s = ZOOM_MIN * Math.pow(ZOOM_MAX / ZOOM_MIN, pct)
    // 缩略图
    const thumb = sliderTrackRef.current?.querySelector<HTMLElement>('[data-role=zoom-thumb]')
    if (thumb) thumb.style.left = (pct * 100) + '%'
    // 填充条
    const fill = sliderTrackRef.current?.querySelector<HTMLElement>('[data-role=zoom-fill]')
    if (fill) fill.style.width = (pct * 100) + '%'
    // 百分比显示
    if (sliderDisplayRef.current) {
      sliderDisplayRef.current.textContent = Math.round(s * 100) + '%'
    }
    // 图片
    const img = imgContainerRef.current?.querySelector<HTMLElement>('img')
    if (img) {
      img.style.transform = `scale(${s})`
      img.style.maxWidth = s <= 1 ? '100%' : 'none'
      img.style.maxHeight = s <= 1 ? '100%' : 'none'
    }
    return s
  }, [])

  const doSliderMove = useCallback((e: MouseEvent) => {
    const track = sliderTrackRef.current
    if (!track || !slidering.current) return
    const rect = track.getBoundingClientRect()
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
    applyZoom(pct)
  }, [applyZoom])

  const doSliderEnd = useCallback(() => {
    slidering.current = false
    document.removeEventListener('mousemove', doSliderMove)
    document.removeEventListener('mouseup', doSliderEnd)
    // 松手后同步到 React state，供下次点击 +/- 或滚轮使用
    const s = ZOOM_MIN * Math.pow(ZOOM_MAX / ZOOM_MIN,
      parseFloat(sliderTrackRef.current?.querySelector<HTMLElement>('[data-role=zoom-thumb]')?.style.left || '50') / 100)
    setScale(s)
  }, [doSliderMove])

  const startSlider = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    slidering.current = true
    const track = sliderTrackRef.current
    if (!track) return
    const rect = track.getBoundingClientRect()
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
    applyZoom(pct)
    document.addEventListener('mousemove', doSliderMove)
    document.addEventListener('mouseup', doSliderEnd)
  }, [applyZoom, doSliderMove, doSliderEnd])

  // 滚轮缩放
  useEffect(() => {
    if (!isImage) return
    const el = imgContainerRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault()
        setScale((s) => Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, s - e.deltaY * 0.005)))
      }
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [isImage])

  const handleDownload = useCallback(() => {
    const a = document.createElement('a')
    a.href = dlUrl
    a.download = fileName
    a.click()
  }, [dlUrl, fileName])

  if (!previewable) return null

  const fileForForward = { file_id: fileId, name: fileName, size: fileSize, mime_type: mimeType }

  // 非全屏默认宽度/高度
  const defaultWidth = 'md:w-[800px]'
  const defaultHeight = 'md:max-h-[85vh]'
  const sizeStyle: React.CSSProperties = {}
  if (isFullscreen) {
    // 全屏模式：撑满
  } else {
    if (modalWidth !== null) sizeStyle.width = modalWidth
    if (modalHeight !== null) sizeStyle.maxHeight = modalHeight
  }

  const headerBar = (
    <div className="flex items-center gap-3 px-4 h-12 border-b border-border bg-surface shrink-0 rounded-t-2xl">
      <button
        onClick={onClose}
        className="icon-btn-sm -ml-1 text-textSecondary"
        title={t('common.close')}
      >
        <ArrowLeft size={18} className="md:hidden" />
        <X size={18} className="hidden md:block" />
      </button>

      <FileIcon size={18} className="text-textMuted shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-textPrimary truncate">{fileName}</p>
        <p className="text-3xs text-textMuted">{formatFileSize(fileSize)}</p>
      </div>

      {/* 富文本：渲染 ↔ 原文 切换（看源码用） */}
      {isRichText && content !== null && (
        <button
          onClick={() => setShowSource((v) => !v)}
          className="px-2 py-1 rounded-control text-xs border border-border bg-elevated hover:bg-border text-textSecondary transition-colors shrink-0"
          title={showSource ? '查看渲染效果' : '查看原文源码'}
        >
          {showSource ? '👁 渲染' : '📄 原文'}
        </button>
      )}

      {/* 图片缩放 — 滑块 + 按钮 */}
      {isImage && (
        <div className="flex items-center gap-2">
          <button onClick={zoomOut} disabled={scale <= ZOOM_MIN}
            className="icon-btn-sm text-textSecondary" title={t('common.zoomOut')}>
            <ZoomOut size={16} />
          </button>

          {/* 可拖拽缩放滑块 */}
          <div
            ref={sliderTrackRef}
            onMouseDown={startSlider}
            className="relative w-24 h-6 flex items-center cursor-pointer select-none"
          >
            {/* 轨道 */}
            <div className="w-full h-1 rounded-full bg-elevated" />
            {/* 填充进度 */}
            <div
              data-role="zoom-fill"
              className="absolute top-1/2 left-0 h-1 rounded-full bg-primary-500 -translate-y-1/2 pointer-events-none"
              style={{ width: `${((scale - ZOOM_MIN) / (ZOOM_MAX - ZOOM_MIN)) * 100}%` }}
            />
            {/* 拖拽滑块 */}
            <div
              data-role="zoom-thumb"
              className="absolute top-1/2 w-3.5 h-3.5 rounded-full bg-primary-500 shadow-sm border-2 border-surface
                         -translate-x-1/2 -translate-y-1/2 pointer-events-none
                         transition-shadow duration-100 hover:shadow-md active:shadow-lg"
              style={{ left: `${((scale - ZOOM_MIN) / (ZOOM_MAX - ZOOM_MIN)) * 100}%` }}
            />
          </div>

          <span ref={sliderDisplayRef} className="text-2xs text-textMuted w-9 text-center tabular-nums">
            {Math.round(scale * 100)}%
          </span>
          <button onClick={zoomIn} disabled={scale >= ZOOM_MAX}
            className="p-1 rounded hover:bg-elevated text-textSecondary disabled:opacity-30 transition-colors" title={t('common.zoomIn')}>
            <ZoomIn size={16} />
          </button>
          <button onClick={zoomReset}
            className="icon-btn-sm text-textSecondary" title={t('common.resetZoom')}>
            <RotateCcw size={14} />
          </button>
        </div>
      )}

      {/* 全屏按钮（仅电脑版） */}
      <button
        onClick={toggleFullscreen}
        className="btn btn-xs btn-outline hidden md:flex gap-1.5"
        title={isFullscreen ? t('common.exitFullscreen') : t('common.fullscreen')}
      >
        {isFullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
      </button>

      <button
        onClick={() => setForwardFile({ file_id: fileId ?? 0, name: fileName, size: fileSize, mime_type: mimeType })}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-control border border-border text-textSecondary hover:bg-elevated text-xs font-medium transition-colors"
        title={t('forward.send')}
      >
        <Share2 size={14} />
        <span className="hidden sm:inline">{t('forward.send')}</span>
      </button>

      <button
        onClick={handleDownload}
        className="btn btn-xs btn-primary gap-1.5"
        title={t('common.download')}
      >
        <Download size={14} />
        <span className="hidden sm:inline">{t('common.download')}</span>
      </button>
    </div>
  )

  return (
    <>
      <Dialog onClose={() =>  { if (!wasResizing.current) onClose() } } className="flex items-center justify-center p-0 md:p-6">
        <div
          ref={modalRef}
          className={`bg-surface border border-border md:rounded-dialog shadow-2xl shadow-black/30 flex flex-col relative
                        ${isFullscreen ? 'w-full h-full md:w-full md:h-full md:max-h-full' : 'w-full h-full ' + defaultWidth + ' ' + defaultHeight}`}
          style={sizeStyle}
          onClick={(e) => e.stopPropagation()}
        >
          {headerBar}

          {/* 内容区 —— 滚动只在这一层：文字类分支（Markdown / docx / 源码）都不自己写滚动条，
              免得像之前那样漏掉一支就整块滚不动。图片与 iframe 是例外：图片靠 scale 缩放、
              布局尺寸不变，溢出得由它自己那个容器接；iframe 则要撑满、由内部自己滚。 */}
          <div className="flex-1 min-h-0 overflow-auto bg-canvas md:rounded-b-2xl">
            {loading ? (
              <div className="flex items-center justify-center py-20 w-full h-full">
                <Loader2 size={24} className="animate-spin text-textMuted" />
              </div>
            ) : error ? (
              <div className="flex flex-col items-center justify-center py-20 gap-3 text-textMuted w-full h-full">
                <AlertTriangle size={24} className="text-rose-400" />
                <p className="text-sm">{error}</p>
                <button onClick={handleDownload} className="btn btn-sm btn-primary">
                  {t('common.downloadInstead')}
                </button>
              </div>
            ) : isImage ? (
              <div ref={imgContainerRef} className="w-full h-full flex items-center justify-center overflow-auto">
                {/* 图片自带滚动：缩放走 transform，布局尺寸不变，外层接不到它的溢出 */}
                <img
                  src={dlUrl + `&_=${retry}`}
                  alt={fileName}
                  className="object-contain select-none"
                  style={{
                    transform: `scale(${scale})`,
                    maxWidth: scale <= 1 ? '100%' : 'none',
                    maxHeight: scale <= 1 ? '100%' : 'none',
                  }}
                  draggable={false}
                  onError={() => { if (retry < RETRY_MAX) setTimeout(() => setRetry(r => r + 1), 1000) }}
                />
              </div>
            ) : isPDF || (isHtml && content && !showSource) ? (
              <iframe
                src={isPDF ? dlUrl : undefined}
                srcDoc={isHtml ? (content ?? undefined) : undefined}
                className="w-full h-full flex-1 border-0 bg-white overflow-auto"
                title={fileName}
                sandbox={isHtml ? 'allow-scripts' : undefined}
              />
            ) : isRichText && showSource ? (
              <pre className="w-full p-4 md:p-5 m-0 text-xs leading-relaxed font-mono text-textPrimary whitespace-pre-wrap break-words bg-canvas">
                {content}
              </pre>
            ) : (
              <div className="w-full p-4 md:p-5">
                {isDocx ? (
                  <div
                    className="prose prose-sm dark:prose-invert max-w-none text-textPrimary"
                    dangerouslySetInnerHTML={{ __html: content || '' }}
                  />
                ) : isMd || codeLang ? (
                  <div className="w-full max-w-none text-sm leading-relaxed break-words text-textPrimary">
                    <MarkdownContent
                      content={codeLang ? '```' + codeLang + '\n' + (content || '') + '\n```' : (content || '')}
                      isMine={false}
                    />
                  </div>
                ) : (
                  <pre className="text-xs text-textPrimary whitespace-pre-wrap break-all font-mono leading-relaxed select-text">
                    {content}
                  </pre>
                )}
              </div>
            )}
          </div>

          {/* 拖拽缩放手柄（仅电脑版且非全屏） */}
          {!isFullscreen && (
            <>
              {/* 外发光描边 —— 用 box-shadow 的 spread 而不是 outline：outline 在圆角处会斜接，
                  四角看着像被封成扇形，而 shadow 的圆角是跟着元素半径推出来的，正是一段弧 */}
              <div className="absolute inset-0 rounded-dialog pointer-events-none z-overlay"
                style={{ boxShadow: '0 0 0 3px rgba(99,102,241,0.45), 0 0 14px rgba(99,102,241,0.25)' }} />

              {/* 四边拖拽条 — 微光可见，hover 更亮 */}
              <div
                className="hidden md:block absolute inset-y-0 -left-1 w-[8px] cursor-ew-resize z-overlay
                  bg-gradient-to-r from-primary-500/25 to-transparent
                  hover:from-primary-500/45 active:from-primary-500/55 transition-all duration-150"
                onMouseDown={startResize('w')}
              />
              <div
                className="hidden md:block absolute inset-y-0 -right-1 w-[8px] cursor-ew-resize z-overlay
                  bg-gradient-to-l from-primary-500/25 to-transparent
                  hover:from-primary-500/45 active:from-primary-500/55 transition-all duration-150"
                onMouseDown={startResize('e')}
              />
              <div
                className="hidden md:block absolute inset-x-0 -bottom-1 h-[8px] cursor-ns-resize z-overlay
                  bg-gradient-to-b from-primary-500/25 to-transparent
                  hover:from-primary-500/45 active:from-primary-500/55 transition-all duration-150"
                onMouseDown={startResize('s')}
              />
              <div
                className="hidden md:block absolute inset-x-0 -top-1 h-[8px] cursor-ns-resize z-overlay
                  bg-gradient-to-t from-primary-500/25 to-transparent
                  hover:from-primary-500/45 active:from-primary-500/55 transition-all duration-150"
                onMouseDown={startResize('n')}
              />
              {/* 四角 — 圆角 2xl 完全贴合弹窗弧线，hover 加厚加亮 */}
              <div
                className="hidden md:block absolute -top-1 -left-1 w-[12px] h-[12px] cursor-nwse-resize z-overlay
                  rounded-tl-2xl border-l-[2px] border-t-[2px] border-primary-500/45
                  hover:border-[3px] hover:border-primary-500/70 hover:bg-primary-500/15 active:bg-primary-500/25 transition-all"
                onMouseDown={startResize('nw')}
              />
              <div
                className="hidden md:block absolute -top-1 -right-1 w-[12px] h-[12px] cursor-nesw-resize z-overlay
                  rounded-tr-2xl border-r-[2px] border-t-[2px] border-primary-500/45
                  hover:border-[3px] hover:border-primary-500/70 hover:bg-primary-500/15 active:bg-primary-500/25 transition-all"
                onMouseDown={startResize('ne')}
              />
              <div
                className="hidden md:block absolute -bottom-1 -left-1 w-[12px] h-[12px] cursor-nesw-resize z-overlay
                  rounded-bl-2xl border-l-[2px] border-b-[2px] border-primary-500/45
                  hover:border-[3px] hover:border-primary-500/70 hover:bg-primary-500/15 active:bg-primary-500/25 transition-all"
                onMouseDown={startResize('sw')}
              />
              <div
                className="hidden md:block absolute -bottom-1 -right-1 w-[12px] h-[12px] cursor-nwse-resize z-overlay
                  rounded-br-2xl border-r-[2px] border-b-[2px] border-primary-500/45
                  hover:border-[3px] hover:border-primary-500/70 hover:bg-primary-500/15 active:bg-primary-500/25 transition-all"
                onMouseDown={startResize('se')}
              />
            </>
          )}
        </div>
      </Dialog>

      {forwardFile && (
        <ForwardFileModal
          file={forwardFile}
          onClose={() => setForwardFile(null)}
        />
      )}
    </>
  )
}
