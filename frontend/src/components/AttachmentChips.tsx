import { File as FileIcon, Loader2, X } from 'lucide-react'
import type { PendingAttachment } from '../hooks/useAttachmentUpload'
import { formatFileSize } from '../utils/format'

/**
 * 待发送附件预览条（主站聊天与群视界世界对话共用）。
 * 纯展示：状态与操作全部来自 useAttachmentUpload。
 */
export function AttachmentChips({ items, onRemove, errorText = '上传失败' }: {
  items: PendingAttachment[]
  onRemove: (id: string) => void
  errorText?: string
}) {
  if (items.length === 0) return null
  return (
    <div className="mb-2 flex flex-wrap gap-2">
      {items.map((att) => (
        <div
          key={att.id}
          className={`relative group flex items-center gap-2 pl-3 pr-1 py-1.5 rounded-card text-xs border transition-colors ${
            att.error ? 'bg-rose-500/10 border-rose-500/30'
              : att.uploading ? 'bg-canvas border-border animate-pulse'
              : 'bg-canvas border-border hover:bg-elevated'
          }`}
        >
          <FileIcon size={14} className={att.error ? 'text-rose-400' : att.uploading ? 'text-textMuted' : 'text-primary-400'} />
          <span className={`max-w-[120px] truncate ${att.error ? 'text-rose-400' : 'text-textSecondary'}`}>
            {att.name}
          </span>
          {att.uploading && <Loader2 size={12} className="animate-spin text-textMuted shrink-0" />}
          {att.error && <span className="text-rose-400 text-3xs shrink-0" title={att.error}>{errorText}</span>}
          <span className="text-textMuted text-3xs shrink-0">{formatFileSize(att.size)}</span>
          <button
            onClick={() => onRemove(att.id)}
            className="shrink-0 p-0.5 rounded text-textMuted hover:text-rose-400 hover:bg-rose-500/10 transition-colors"
            title="移除"
          ><X size={12} /></button>
        </div>
      ))}
    </div>
  )
}

/**
 * 拖拽蒙版（主站聊天与群视界世界对话共用）。
 * 铺满所在的 relative 容器，半透明覆盖：所有落点都淡，鼠标所在那块更深。
 * 三块落点上下相邻 → 视觉上整个面板是一层蒙版，只有当前区块加深。
 *
 * pointer-events-none：不挡拖放事件，也不会因为自己浮出来而打断拖拽。
 * 只有 strong（鼠标就在这块）才显示文字——否则三块会同时冒字。
 */
export function DropMask({ active, strong = false, label }: {
  active: boolean
  strong?: boolean
  label?: string
}) {
  if (!active) return null
  return (
    <div
      className={`pointer-events-none absolute inset-0 z-modal flex items-center justify-center transition-colors duration-150 ${
        strong ? 'bg-primary-500/20' : 'bg-primary-500/[0.07]'
      }`}
    >
      {strong && label && <span className="text-xs font-medium text-primary-400">{label}</span>}
    </div>
  )
}
