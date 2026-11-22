import { ReactNode, useId } from 'react'
import { X } from 'lucide-react'
import IconButton from './IconButton'
import Dialog from './Dialog'

/**
 * 统一 Modal 弹窗组件（全站唯一弹窗底座）
 *
 * 消灭各处手写的 fixed inset-0 遮罩 + 卡片。特性：
 * - 居中卡片 + 半透明遮罩 + 点击遮罩关闭
 * - ESC 键关闭 · 打开时锁定背景滚动
 * - 标题栏（可选关闭按钮）+ 内容 + 底部操作区（可选）
 * 圆角/层级走令牌：rounded-dialog + z-modal。
 */
type ModalSize = 'sm' | 'md' | 'lg' | 'xl'

// 宽屏下不要只占中间一小条：每档在 sm / lg 断点各抬一级（卡片本身是 w-full，窄屏仍回落整宽）
const SIZE_CLASS: Record<ModalSize, string> = {
  sm: 'max-w-sm sm:max-w-md',
  md: 'max-w-lg sm:max-w-xl lg:max-w-2xl',
  lg: 'max-w-2xl sm:max-w-3xl lg:max-w-4xl',
  xl: 'max-w-4xl sm:max-w-5xl lg:max-w-6xl',
}

interface ModalProps {
  open: boolean
  onClose: () => void
  title?: ReactNode
  children: ReactNode
  footer?: ReactNode
  /** 尺寸档位（优先于 width） */
  size?: ModalSize
  /** 自定义最大宽度类名，保留给历史调用 */
  width?: string
  closeOnOverlay?: boolean
  /** 遮罩样式。默认纯半透明：全屏 backdrop-blur 在长内容滚动时每帧重算，弱机直接卡死，
      而它并不提供信息价值（背景本来就被压暗了） */
  backdropClassName?: string
  /** 是否显示右上角关闭按钮（无标题时默认显示） */
  showClose?: boolean
  bodyClassName?: string
  /** 手机端铺满整屏（长内容弹窗用：聊天记录、长列表）。桌面端仍居中留白 */
  mobileFullscreen?: boolean
}

export default function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  size = 'md',
  width,
  closeOnOverlay = true,
  backdropClassName = 'bg-black/65',
  showClose,
  bodyClassName = '',
  mobileFullscreen = false,
}: ModalProps) {
  const titleId = useId()

  if (!open) return null
  const withClose = showClose ?? true

  return (
    // 手机全屏时把遮罩的留白也去掉：留 16px 就铺不满，边角会露出底色
    <Dialog
      onClose={onClose}
      closeOnOverlay={closeOnOverlay}
      backdrop={backdropClassName}
      className={`flex items-center justify-center ${mobileFullscreen ? 'p-0 md:p-4' : 'p-4'}`}
    >
      {/* 卡片 */}
      <div
        className={`relative w-full ${width || SIZE_CLASS[size]} bg-surface border border-border rounded-dialog shadow-2xl max-h-[85vh] flex flex-col ${
          mobileFullscreen ? 'max-md:h-[100dvh] max-md:max-h-none max-md:max-w-none max-md:rounded-none max-md:border-0' : ''
        }`}
      >
        {(title || withClose) && (
          <div className="top-bar flex items-center justify-between gap-2 px-5 py-4 shrink-0">
            <h3 id={title ? titleId : undefined} className="font-semibold text-textPrimary text-sm flex items-center gap-2 min-w-0">
              {title}
            </h3>
            {withClose && <IconButton icon={<X size={18} />} label="关闭" onClick={onClose} className="-mr-2" />}
          </div>
        )}
        <div className={`px-5 py-4 overflow-y-auto flex-1 ${bodyClassName}`}>{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-border shrink-0">
            {footer}
          </div>
        )}
      </div>
    </Dialog>
  )
}
