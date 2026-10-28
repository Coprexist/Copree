/**
 * 消息附件上传 — 主站聊天与群视界世界对话共用。
 *
 * 只负责"选文件 → 上传 → 拿到 {file_id, path, ...}"与待发送列表；
 * 图片如何进 LLM 多模态由后端 app/utils/multimodal.py 统一处理，前端不关心。
 */
import { useCallback, useRef, useState } from 'react'
import { api } from '../api/client'

export interface PendingAttachment {
  id: string
  file: File | null
  file_id?: number
  path?: string
  name: string
  size: number
  mime_type: string
  uploading: boolean
  error?: string
}

/** 已上传完成、可随消息发出的附件元数据 */
export interface ReadyAttachment {
  file_id: number
  name: string
  size: number
  mime_type: string
  path: string
}

/** 是否图片附件（决定气泡里是否渲染成图） */
export function isImageAttachment(a: { mime_type?: string }): boolean {
  return (a.mime_type || '').toLowerCase().startsWith('image/')
}

/**
 * 附件图片的尺寸类：最小宽高兜底 + 等比缩放，上限由调用方的 max-* 决定。
 *
 * 为什么需要下限：只写了 viewBox、没有 width/height 的 SVG 没有「内在尺寸」，一旦外面
 * 是「宽度由内容决定」的容器（聊天气泡就这样），max-width:100% 会循环求解成 0，图片
 * 塌成一条 1px 边框。给个下限就打破循环，图片仍按自身比例铺到上限为止。
 * SVG 多为图标，下限给大一点；光栅图按原尺寸显示，只兜一个不至于看不见的底。
 */
export function attachmentImageSizing(a: { mime_type?: string; name?: string }): string {
  return isSvgAttachment(a) ? 'min-w-[120px] min-h-[120px] object-contain' : 'min-w-[64px] min-h-[64px] object-contain'
}

/** 是否 SVG 附件（MIME 优先，历史数据里类型可能是错的，名字兜底） */
export function isSvgAttachment(a: { mime_type?: string; name?: string }): boolean {
  return (a.mime_type || '').toLowerCase() === 'image/svg+xml' || /\.svg$/i.test(a.name || '')
}

const isImageFile = (f: File) => (f.type || '').toLowerCase().startsWith('image/')

const IMAGE_ONLY_ERROR = '仅支持图片'

/** 拖拽进来的东西是不是文件（拖选中的文字也会触发 drag 事件，得挡掉） */
function dragHasFiles(e: React.DragEvent): boolean {
  return Array.from(e.dataTransfer?.types || []).includes('Files')
}

/** 从剪贴板取出文件（截图 / 复制的图片走 items，kind='file'；files 是兜底） */
function filesFromClipboard(dt: DataTransfer | null): File[] {
  if (!dt) return []
  const fromItems = Array.from(dt.items || [])
    .filter((it) => it.kind === 'file')
    .map((it) => it.getAsFile())
    .filter((f): f is File => !!f)
  if (fromItems.length) return fromItems
  return Array.from(dt.files || [])
}

const FALLBACK_MAX_MB = 32

/** 复用主站缓存过的 /user/config/upload-limits（缓存缺失/损坏则用兜底值） */
function cachedMaxMB(): number {
  try {
    const raw = sessionStorage.getItem('upload_limits')
    if (raw) return JSON.parse(raw).upload_max_size_mb || FALLBACK_MAX_MB
  } catch {
    // 缓存坏了 → 走兜底值（不值得打断用户）
  }
  return FALLBACK_MAX_MB
}

export interface AttachmentUploadOptions {
  /** 覆盖 /user/config/upload-limits 缓存里的上限 */
  maxFileMB?: number
  /** 只收图片（群视界世界对话：非图片进不了多模态，后端也读不出内容） */
  imagesOnly?: boolean
}

export function useAttachmentUpload({ maxFileMB, imagesOnly = false }: AttachmentUploadOptions = {}) {
  const [items, setItems] = useState<PendingAttachment[]>([])
  const [dragging, setDragging] = useState(false)
  const [dragZone, setDragZone] = useState<string | null>(null)
  const seqRef = useRef(0)
  // dragenter/dragleave 会随子元素冒泡反复触发，用深度计数才能判断"真的离开了"
  const dragDepthRef = useRef(0)

  const pick = useCallback(async (files: FileList | File[]) => {
    const maxMB = maxFileMB ?? cachedMaxMB()
    for (const file of Array.from(files)) {
      const id = `att_${Date.now()}_${seqRef.current++}`
      const base = { id, name: file.name, size: file.size, mime_type: file.type || 'application/octet-stream' }
      if (imagesOnly && !isImageFile(file)) {
        // 类型不符：同样以错误态入列，用户看得见、能移除（不静默丢弃）
        setItems((prev) => [...prev, { ...base, file: null, uploading: false, error: IMAGE_ONLY_ERROR }])
        continue
      }
      if (maxMB > 0 && file.size > maxMB * 1024 * 1024) {
        // 超限：以错误态入列，用户看得见、能移除（不静默丢弃）
        setItems((prev) => [...prev, { ...base, file: null, uploading: false, error: `超出 ${maxMB}MB 上限` }])
        continue
      }
      setItems((prev) => [...prev, { ...base, file, uploading: true }])
      try {
        const r = await api.upload('/fs/upload-attachment', file)
        setItems((prev) => prev.map((a) => a.id === id
          ? { ...a, file: null, file_id: r.file_id, path: r.path, uploading: false }
          : a))
      } catch (e: any) {
        setItems((prev) => prev.map((a) => a.id === id
          ? { ...a, uploading: false, error: e?.detail || e?.message || '上传失败' }
          : a))
      }
    }
  }, [maxFileMB, imagesOnly])

  // 拖拽放入：与点选走同一个 pick。每个落点摊一份 zoneProps(id)——
  // id 只用来判断"鼠标此刻压在哪个区块"（那块蒙版更深），所有落点共用同一个深度计数
  const zoneProps = useCallback((id: string) => ({
    onDragEnter: (e: React.DragEvent) => {
      if (!dragHasFiles(e)) return
      e.preventDefault()
      dragDepthRef.current += 1
      setDragging(true)
      setDragZone(id)
    },
    onDragOver: (e: React.DragEvent) => {
      if (!dragHasFiles(e)) return
      e.preventDefault()
      setDragZone(id)  // 值为同一个字符串时 React 直接 bail out，不会重渲染
      e.dataTransfer.dropEffect = 'copy'
    },
    onDragLeave: (e: React.DragEvent) => {
      if (!dragHasFiles(e)) return
      dragDepthRef.current = Math.max(0, dragDepthRef.current - 1)
      if (dragDepthRef.current === 0) {
        setDragging(false)
        setDragZone(null)
      }
    },
    onDrop: (e: React.DragEvent) => {
      if (!dragHasFiles(e)) return
      e.preventDefault()
      dragDepthRef.current = 0
      setDragging(false)
      setDragZone(null)
      if (e.dataTransfer.files?.length) pick(e.dataTransfer.files)
    },
  }), [pick])

  /** 落点蒙版状态：active=正在拖拽；strong=鼠标就在这个区块（蒙版更深、只有它显示文字） */
  const dropState = useCallback((id: string) => ({ active: dragging, strong: dragZone === id }), [dragging, dragZone])

  // 粘贴放入：截图直接 Ctrl+V。只认文件，纯文本粘贴原样放行给浏览器
  const pasteProps = {
    onPaste: (e: React.ClipboardEvent) => {
      const files = filesFromClipboard(e.clipboardData)
      if (!files.length) return
      e.preventDefault()
      pick(files)
    },
  }

  const remove = useCallback((id: string) => {
    setItems((prev) => prev.filter((a) => a.id !== id))
  }, [])
  const clear = useCallback(() => setItems([]), [])

  const ready: ReadyAttachment[] = items
    .filter((a) => !a.uploading && !a.error && a.file_id != null)
    .map((a) => ({ file_id: a.file_id!, name: a.name, size: a.size, mime_type: a.mime_type, path: a.path || '' }))

  return {
    items, pick, remove, clear, ready,
    /** 拖拽相关：zoneProps(id) 摊到落点容器上，dropState(id) 给蒙版用 */
    dragging, dragZone, zoneProps, dropState,
    /** 粘贴相关：摊到输入容器上（事件会从 textarea 冒泡上来） */
    pasteProps,
    uploading: items.some((a) => a.uploading),
    hasError: items.some((a) => a.error),
  }
}
