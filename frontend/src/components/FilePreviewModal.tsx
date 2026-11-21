import { useState, useEffect, useCallback, useRef } from 'react'
import { Download, X, ArrowLeft, FileIcon, Loader2, AlertTriangle, ZoomIn, ZoomOut, RotateCcw, Share2, Maximize2, Minimize2, Eye, Code2, ChevronDown, BookmarkPlus, Check } from 'lucide-react'
import { useT } from '../i18n/I18nContext'
import { api, fileDownloadUrl } from '../api/client'
import { formatFileSize } from '../utils/format'
import { isTextPreviewable, getCodeLang, isMarkdownFile, resolveMimeType, EXT_LANG_MAP } from '../utils/mime'
import { PREVIEW_SANDBOX, buildPreviewDocument } from '../utils/previewSandbox'
import MarkdownContent from './shared/MarkdownContent'
import ForwardFileModal from './ForwardFileModal'
import { Button, Dialog, HelpTip, Input, MenuItem, MenuPanel, Modal, ResizeEdges } from './ui'
import { saveBlob } from '../utils/download'
import {
  readSvg, rasterizeSvg, outputSize, suggestedSide,
  EXPORT_SIDE_MIN, EXPORT_SIDE_MAX, EXPORT_SIDE_PRESETS, type SvgSource,
} from '../utils/svgRaster'
import { isSvgAttachment } from '../hooks/useAttachmentUpload'
import { useImageZoom, ZOOM_MIN, ZOOM_MAX } from '../hooks/useImageZoom'

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
  // 图片缩放：倍率、滑块位置、图片尺寸都由它一处推出
  const zoom = useImageZoom()

  const [forwardFile, setForwardFile] = useState<{file_id:number;name:string;size:number;mime_type:string}|null>(null)
  // 收进我的文件（§7.3）：kept 之后按钮就锁住，不让人反复点——后端虽然幂等，重复请求没意义
  const [keeping, setKeeping] = useState(false)
  const [kept, setKept] = useState(false)
  const [keepError, setKeepError] = useState('')
  // 富文本（md/html/代码）渲染 ↔ 原文切换：看渲染效果或源码

  const dlUrl = src ?? (fileId != null ? fileDownloadUrl(fileId) : '')

  // 换了一份文件，上一份的「已收进」状态不能带过来
  useEffect(() => {
    setKept(false)
    setKeepError('')
  }, [fileId])

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
  // SVG 的判定与聊天气泡共用一处（历史数据的 mime 可能错，名字兜底）
  const isSvg = isSvgAttachment({ mime_type: resolvedMime, name: fileName })
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
          text = text.slice(0, 2 * 1024 * 1024) + '\n\n' + t('filePreview:fileTooLarge')
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
            setError(err.message || t('common:loadFailed'))
            setLoading(false)
          }
        }
      }
    }
    tryFetch(0)

    return () => { cancelled = true }
  }, [fileId, previewable, dlUrl, fileName, onClose, t, isImage, isPDF, isDocx, codeLang, retry])


  const handleDownload = useCallback(() => {
    const a = document.createElement('a')
    a.href = dlUrl
    a.download = fileName
    a.click()
  }, [dlUrl, fileName])

  /** 收进我的文件：把同一份字节硬链接一份到自己的空间，从此占配额、不被自动清（§7.3） */
  const handleKeep = useCallback(async () => {
    if (fileId == null || keeping || kept) return
    setKeeping(true)
    setKeepError('')
    try {
      await api.post('/fs/keep', { file_id: fileId })
      setKept(true)
    } catch (e: any) {
      // 多半是配额满了（413）；后端把已用/上限/本次大小都写在 detail 里，直接显示
      setKeepError(e?.detail || t('filePreview:keepFailed'))
    } finally {
      setKeeping(false)
    }
  }, [fileId, keeping, kept, t])

  // SVG 的下载格式：原图（矢量，可再编辑）或 PNG（浏览器现画，尺寸可调）
  const [dlMenu, setDlMenu] = useState(false)
  const [pngOpen, setPngOpen] = useState(false)
  const [svgSource, setSvgSource] = useState<SvgSource | null>(null)
  const [sizeText, setSizeText] = useState('')
  const [loadingSvg, setLoadingSvg] = useState(false)
  const [exportingPng, setExportingPng] = useState(false)
  const [pngError, setPngError] = useState('')
  /** 去掉 SVG 自带的满幅背景层：默认保留——那层底常常就是这个图标的一部分，只有想导出透明图时才勾 */
  const [stripBg, setStripBg] = useState(false)
  const svgBaseName = fileName.replace(/\.svg$/i, '')

  const side = Number(sizeText)
  const sideOk = Number.isFinite(side) && side >= EXPORT_SIDE_MIN && side <= EXPORT_SIDE_MAX
  const outSize = svgSource && sideOk ? outputSize(svgSource, side) : null

  /** 打开导出弹窗：读一次文本并量出原图尺寸，之后改尺寸不再重复取文件 */
  const openPngDialog = useCallback(async () => {
    setDlMenu(false)
    setPngError('')
    setPngOpen(true)
    if (svgSource) return
    setLoadingSvg(true)
    try {
      const source = await readSvg(dlUrl)
      setSvgSource(source)
      // 每次打开都回到推荐尺寸（长边×2，夹在 512–2048，和最初那版一致）：
      // 记住上次会把人上次手调的 2048 一直带回来，而大图在 QQ 里预览会先铺黑底。
      setSizeText(String(suggestedSide(source)))
    } catch {
      setPngError(t('filePreview:exportPngFailed'))
    } finally {
      setLoadingSvg(false)
    }
  }, [dlUrl, svgSource, t])

  const exportPng = useCallback(async () => {
    if (!svgSource || !sideOk || exportingPng) return
    setExportingPng(true)
    setPngError('')
    try {
      saveBlob(await rasterizeSvg(svgSource, Math.round(side), { stripBackground: stripBg }), `${svgBaseName}.png`)
      setPngOpen(false)
    } catch {
      setPngError(t('filePreview:exportPngFailed'))
    } finally {
      setExportingPng(false)
    }
  }, [svgSource, side, sideOk, exportingPng, stripBg, svgBaseName, t])

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
    <div className="top-bar flex items-center gap-2 md:gap-3 px-4 h-14 shrink-0 rounded-t-2xl">
      <button
        onClick={onClose}
        className="icon-btn-sm -ml-1 text-textSecondary"
        title={t('common:close')}
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
          className="btn btn-xs btn-outline shrink-0"
          title={showSource ? t('filePreview:viewRenderedHint') : t('filePreview:viewSourceHint')}
        >
          {showSource ? <Eye size={14} /> : <Code2 size={14} />}
          {showSource ? t('filePreview:viewRendered') : t('filePreview:viewSource')}
        </button>
      )}

      {/* 图片缩放 — 滑块 + 按钮，倍率、滑块位置、百分比都从同一个 state 推出 */}
      {isImage && (
        <div className="flex items-center gap-1.5 sm:gap-2">
          <button onClick={zoom.zoomOut} disabled={zoom.scale <= ZOOM_MIN}
            className="icon-btn-sm text-textSecondary" title={t('common:zoomOut')}>
            <ZoomOut size={14} />
          </button>

          {/* h-10 + -my-2：命中区撑到 40px 而布局仍占 24px，手机上手指按得准；
              touch-none 把手指拖动交给指针捕获，不然浏览器会当成滚动 */}
          <div
            ref={zoom.sliderRef}
            onPointerDown={zoom.onSliderDown}
            onPointerMove={zoom.onSliderMove}
            onPointerUp={zoom.onSliderUp}
            onPointerCancel={zoom.onSliderUp}
            className="relative w-16 sm:w-24 h-10 -my-2 flex items-center cursor-pointer select-none touch-none"
          >
            {/* 轨道 */}
            <div className="w-full h-1 rounded-full bg-elevated" />
            {/* 填充进度 */}
            <div
              data-role="zoom-fill"
              className="absolute top-1/2 left-0 h-1 rounded-full bg-primary-500 -translate-y-1/2 pointer-events-none"
              style={{ width: `${zoom.pct * 100}%` }}
            />
            {/* 拖拽滑块 */}
            <div
              data-role="zoom-thumb"
              className="absolute top-1/2 w-3.5 h-3.5 rounded-full bg-primary-500 shadow-sm border-2 border-surface
                         -translate-x-1/2 -translate-y-1/2 pointer-events-none
                         transition-shadow duration-100 hover:shadow-md active:shadow-lg"
              style={{ left: `${zoom.pct * 100}%` }}
            />
          </div>

          <span className="text-2xs text-textMuted w-8 sm:w-9 text-center tabular-nums">
            {Math.round(zoom.scale * 100)}%
          </span>
          <button onClick={zoom.zoomIn} disabled={zoom.scale >= ZOOM_MAX}
            className="icon-btn-sm text-textSecondary" title={t('common:zoomIn')}>
            <ZoomIn size={14} />
          </button>
          <span className="hidden sm:inline-flex">
            <button onClick={zoom.reset}
              className="icon-btn-sm text-textSecondary" title={t('common:resetZoom')}>
              <RotateCcw size={14} />
            </button>
          </span>
        </div>
      )}

      {/* 全屏按钮（仅电脑版）；文字在窄屏收起，四个动作按钮同一套尺寸与图标大小。
          显示与否交给外层 span：.btn 自带的 display 会盖掉按钮上的 hidden */}
      <span className="hidden md:inline-flex">
        <button
          onClick={toggleFullscreen}
          className="btn btn-xs btn-outline shrink-0"
          title={isFullscreen ? t('common:exitFullscreen') : t('common:fullscreen')}
        >
          {isFullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          <span className="hidden lg:inline">{isFullscreen ? t('common:exitFullscreen') : t('common:fullscreen')}</span>
        </button>
      </span>

      {/* 收进我的文件：只在能拿到 file_id 时出现（世界文件之类只有 src，没有归属可收） */}
      {fileId != null && (
        <button
          onClick={() => void handleKeep()}
          disabled={keeping || kept}
          className="btn btn-xs btn-outline shrink-0"
          title={kept ? t('filePreview:kept') : t('filePreview:keep')}
        >
          {keeping ? (
            <Loader2 size={14} className="animate-spin" />
          ) : kept ? (
            <Check size={14} className="text-primary-400" />
          ) : (
            <BookmarkPlus size={14} />
          )}
          <span className="hidden sm:inline">{kept ? t('filePreview:kept') : t('filePreview:keep')}</span>
        </button>
      )}

      <button
        onClick={() => setForwardFile({ file_id: fileId ?? 0, name: fileName, size: fileSize, mime_type: mimeType })}
        className="btn btn-xs btn-outline shrink-0"
        title={t('forward:send')}
      >
        <Share2 size={14} />
        <span className="hidden sm:inline">{t('forward:send')}</span>
      </button>

      {isSvg ? (
        <div className="relative shrink-0">
          <button
            onClick={() => setDlMenu((v) => !v)}
            className="btn btn-xs btn-primary"
            title={t('filePreview:downloadFormat')}
          >
            <Download size={14} />
            <span className="hidden sm:inline">{t('common:download')}</span>
            <ChevronDown size={12} />
          </button>
          {dlMenu && (
            <>
              {/* 点空白处收起：模态框里没有全局点击关闭，自己铺一层透明遮罩 */}
              <div className="fixed inset-0 z-overlay" onClick={() => setDlMenu(false)} />
              <MenuPanel className="absolute right-0 top-full mt-1 w-44 z-modal">
                <MenuItem onClick={() => { setDlMenu(false); handleDownload() }}>
                  {t('filePreview:downloadAsSvg')}
                </MenuItem>
                <MenuItem onClick={() => void openPngDialog()}>
                  {t('filePreview:downloadAsPng')}
                </MenuItem>
              </MenuPanel>
            </>
          )}
        </div>
      ) : (
        <button
          onClick={handleDownload}
          className="btn btn-xs btn-primary shrink-0"
          title={t('common:download')}
        >
          <Download size={14} />
          <span className="hidden sm:inline">{t('common:download')}</span>
        </button>
      )}
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

          {/* 收不进我的文件时（最常见是配额满）把理由摊在这里，别只在按钮上闪一下 */}
          {keepError && (
            <p className="px-4 py-2 text-xs text-rose-400 bg-rose-500/10 shrink-0">{keepError}</p>
          )}

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
                  {t('common:downloadInstead')}
                </button>
              </div>
            ) : isImage ? (
              /* 缩放是真实布局尺寸，溢出交给这一层的滚动条；m-auto 让图片「放得下居中、
                 放不下贴左上」，否则溢出到左上角的那部分永远滚不回来。
                 拖拽平移只认鼠标，手指留给容器自己的惯性滚动 */
              <div
                ref={zoom.containerRef}
                onPointerDown={zoom.onPanDown}
                onPointerMove={zoom.onPanMove}
                onPointerUp={zoom.onPanUp}
                onPointerCancel={zoom.onPanUp}
                className={`w-full h-full flex overflow-auto ${isSvg ? 'bg-checker' : ''} ${zoom.panning ? 'cursor-grabbing' : zoom.scale > 1 ? 'cursor-grab' : ''}`}
              >
                <img
                  ref={zoom.imgRef}
                  src={dlUrl + `&_=${retry}`}
                  alt={fileName}
                  /* SVG 可能没有固有尺寸（只有 viewBox），给个下限免得预览塌成一条线 */
                  className={`m-auto shrink-0 select-none ${isSvg ? 'min-w-[120px] min-h-[120px]' : ''}`}
                  style={zoom.imgStyle}
                  draggable={false}
                  onLoad={zoom.onImgLoad}
                  onError={() => { if (retry < RETRY_MAX) setTimeout(() => setRetry(r => r + 1), 1000) }}
                />
              </div>
            ) : isPDF || (isHtml && content && !showSource) ? (
              <iframe
                src={isPDF ? dlUrl : undefined}
                srcDoc={isHtml ? buildPreviewDocument(content ?? '') : undefined}
                className="w-full h-full flex-1 border-0 bg-white overflow-auto"
                title={fileName}
                sandbox={isHtml ? PREVIEW_SANDBOX : undefined}
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
              {/* 外描边 + 八向缩放热区：几何在 index.css 的 .resize-edge，由 --edge-r / --edge-w 推出 */}
              <ResizeEdges onResizeStart={startResize} className="z-overlay" />
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

      {/* SVG → PNG：尺寸可调（快捷档位 + 自定义长边），导出前能看见真实输出尺寸 */}
      <Modal
        open={pngOpen}
        onClose={() => setPngOpen(false)}
        title={t('filePreview:exportPngTitle')}
        size="sm"
        footer={(
          <>
            <Button variant="outline" size="sm" onClick={() => setPngOpen(false)}>
              {t('common:cancel')}
            </Button>
            <Button size="sm" loading={exportingPng} disabled={!sideOk || loadingSvg} onClick={() => void exportPng()}>
              {t('filePreview:exportPngSave')}
            </Button>
          </>
        )}
      >
        {loadingSvg ? (
          <p className="text-sm text-textMuted">{t('common:loading')}</p>
        ) : (
          <div className="space-y-3">
            {svgSource && (
              <p className="text-xs text-textMuted">
                {t('filePreview:exportPngNatural', {
                  w: Math.round(svgSource.width), h: Math.round(svgSource.height),
                })}
              </p>
            )}
            <div className="flex flex-wrap gap-1.5">
              {EXPORT_SIDE_PRESETS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  onClick={() => setSizeText(String(preset))}
                  className={`btn btn-xs btn-outline ${Number(sizeText) === preset ? 'is-active' : ''}`}
                >
                  {preset}px
                </button>
              ))}
              {svgSource && (
                <button
                  type="button"
                  onClick={() => setSizeText(String(Math.round(Math.max(svgSource.width, svgSource.height))))}
                  className={`btn btn-xs btn-outline ${Number(sizeText) === Math.round(Math.max(svgSource.width, svgSource.height)) ? 'is-active' : ''}`}
                >
                  {t('filePreview:exportPngOriginal')}
                </button>
              )}
            </div>
            <Input
              type="number"
              label={t('filePreview:exportPngLongestSide')}
              hint={`${EXPORT_SIDE_MIN}–${EXPORT_SIDE_MAX} px`}
              min={EXPORT_SIDE_MIN}
              max={EXPORT_SIDE_MAX}
              value={sizeText}
              onChange={(e) => setSizeText(e.target.value)}
            />
            {outSize && (
              <p className="flex items-center gap-1 text-xs text-textSecondary">
                {t('filePreview:exportPngOutput', { w: outSize.width, h: outSize.height })}
                {/* 给客户端的建议收进 ? ：只在要发出去时才相关，不该天天占一行 */}
                <HelpTip content={t('filePreview:exportPngClientHint')} />
              </p>
            )}
            {/* "去掉背景"是高级且容易毁图的一步：放到最底下、默认不勾、写明不推荐——
                那层满幅底常常就是这个图标本身（深色圆角底），删了图就只剩上层的几个圆 */}
            {svgSource && svgSource.backgrounds.length > 0 && (
              <label className="flex items-start gap-2 text-xs text-textSecondary cursor-pointer pt-3 border-t border-border/60">
                <input
                  type="checkbox"
                  checked={stripBg}
                  onChange={(e) => setStripBg(e.target.checked)}
                  className="rounded mt-0.5"
                />
                <span className="flex flex-wrap items-center gap-1">
                  <span className="font-medium">{t('filePreview:exportStripBg', { fill: svgSource.backgrounds[0].fill })}</span>
                  <span className="text-3xs text-rose-400">{t('filePreview:exportStripBgWarn')}</span>
                  {/* 为什么"不推荐"收进 ? ：这一段只在真的想勾时才有用 */}
                  <HelpTip content={t('filePreview:exportStripBgHint')} />
                </span>
              </label>
            )}
            {pngError && <p className="text-xs text-rose-400">{pngError}</p>}
          </div>
        )}
      </Modal>
    </>
  )
}
