import { ReactNode, useEffect } from 'react'
import { createPortal } from 'react-dom'

/**
 * 无壳弹窗底座（只有遮罩 + 行为，不带卡片样式）
 *
 * 单一来源：全站所有「固定遮罩 + 自定义卡片」的弹窗都用它，
 * 统一拿到：ESC 关闭 · 打开时锁背景滚动 · 点遮罩关闭 · 层级令牌。
 * 需要标准卡片（标题栏 + 内容 + 底部操作区）时用 Modal（= Dialog + 卡片）。
 */
/** 当前打开的弹窗数（模块级，所有 Dialog 共用） */
let openDialogCount = 0

type Layer = 'overlay' | 'drawer' | 'modal' | 'toast' | 'max'

const LAYER_CLASS: Record<Layer, string> = {
  overlay: 'z-overlay',
  drawer: 'z-drawer',
  modal: 'z-modal',
  toast: 'z-toast',
  max: 'z-max',
}

interface DialogProps {
  /** 关闭动作（ESC 用）。不传则 ESC 不生效 */
  onClose?: () => void
  /** 点击遮罩是否关闭，默认 true */
  closeOnOverlay?: boolean
  layer?: Layer
  /** 遮罩外观，默认 bg-black/60 */
  backdrop?: string
  /** 定位与布局类（flex items-center justify-center p-4 …） */
  className?: string
  children: ReactNode
}

export default function Dialog({
  onClose,
  closeOnOverlay = true,
  layer = 'modal',
  backdrop = 'bg-black/60',
  className = 'flex items-center justify-center p-4',
  children,
}: DialogProps) {
  // 打开期间锁背景滚动（与是否能关无关：强制审批弹窗也要锁）
  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [])

  // 打开期间给 body 打个标记：页面自己的浮层（内容列拖条这类）据此收起。
  // 用计数而不是布尔值——弹窗能叠（确认框开在编辑器上），先关的那个不该把标记清掉。
  useEffect(() => {
    openDialogCount += 1
    document.body.dataset.modalOpen = '1'
    return () => {
      openDialogCount -= 1
      if (openDialogCount <= 0) {
        openDialogCount = 0
        delete document.body.dataset.modalOpen
      }
    }
  }, [])

  // ESC 关闭（只有给了 onClose 才生效：审批这类必须由用户明确选择的弹窗不给 onClose）
  useEffect(() => {
    if (!onClose) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // 必须挂到 body：弹窗写在自己页面里时，任何祖先只要建了层叠上下文
  //（overflow/transform/filter，或页面内部的 z-index），它的 z-modal 就只在那块里有效，
  // 页面自己的浮层（比如内容列的宽度拖条）反而会盖在弹窗上（2026-11-22 设置页实测）。
  // portal 之后弹窗永远在 body 下比较层级，z-modal 的含义才是真的。
  return createPortal(
    <div
      className={`fixed inset-0 ${LAYER_CLASS[layer]} ${backdrop} ${className}`}
      role="dialog"
      aria-modal="true"
      onClick={closeOnOverlay && onClose ? (e) => { if (e.target === e.currentTarget) onClose() } : undefined}
    >
      {children}
    </div>,
    document.body,
  )
}
