import { ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { HelpCircle } from 'lucide-react'
import IconButton from './IconButton'
import { MenuPanel } from './MenuPanel'

/**
 * 「？」帮助：点一下弹一个悬浮小窗，点外面 / ESC 关。
 *
 * 为什么用 portal + fixed：这是**浮层**，不是内容的一部分。挂在原地的话会被父级
 * overflow / 对话框裁掉（也可能被算进排版），所以挂到 body 上、按触发点的位置算坐标；
 * 默认往上弹（下面常常就是按钮栏），上面放不下才翻到下面。
 *
 * 为什么不用 title：title 只能悬浮看，手机上出不来，长句还会被系统截成一行。
 */
interface HelpTipProps {
  /** 浮窗里的说明（长文本；传 ReactNode 也能分段） */
  content: ReactNode
  /** 无文字按钮的可访问名称，默认「帮助」 */
  label?: string
  /** 与 IconButton 同一套尺寸档 */
  size?: 'sm' | 'md'
  /** 相对触发点左对齐或右对齐（窄屏会自动收进视口） */
  align?: 'left' | 'right'
  className?: string
}

const GAP = 6
const MARGIN = 8
const MAX_WIDTH = 240

export default function HelpTip({
  content,
  label = '帮助',
  size = 'sm',
  align = 'right',
  className = '',
}: HelpTipProps) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState({ left: 0, top: 0, ready: false })
  const rootRef = useRef<HTMLSpanElement | null>(null)
  const panelRef = useRef<HTMLDivElement | null>(null)

  /** 按触发点算浮窗位置：默认在上方，放不下才翻到下方；左右也收进视口 */
  const place = useCallback(() => {
    const anchor = rootRef.current?.getBoundingClientRect()
    if (!anchor) return
    const panel = panelRef.current
    const width = panel?.offsetWidth || MAX_WIDTH
    const height = panel?.offsetHeight || 0
    let left = align === 'left' ? anchor.left : anchor.right - width
    left = Math.min(Math.max(MARGIN, left), window.innerWidth - width - MARGIN)
    let top = anchor.top - height - GAP
    if (top < MARGIN) top = anchor.bottom + GAP
    setPos({ left, top, ready: true })
  }, [align])

  // 先挂上去量一次，再定位（量之前先藏起来，避免在 0,0 闪一下）
  useLayoutEffect(() => {
    if (!open) {
      setPos({ left: 0, top: 0, ready: false })
      return
    }
    place()
    const onScroll = () => place()
    window.addEventListener('resize', onScroll)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      window.removeEventListener('resize', onScroll)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [open, place])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node
      if (rootRef.current?.contains(target) || panelRef.current?.contains(target)) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <span ref={rootRef} className={`inline-flex align-middle ${className}`}>
      <IconButton
        size={size}
        icon={<HelpCircle size={12} />}
        label={label}
        aria-expanded={open}
        aria-haspopup="dialog"
        className={open ? 'text-primary-400' : ''}
        // 常被放在 <label> 里（勾选行）：别让点击顺带把复选框切了
        onClick={(e) => {
          e.preventDefault()
          e.stopPropagation()
          setOpen((v) => !v)
        }}
      />
      {open && createPortal(
        /* 外层只负责定位与测量（MenuPanel 不接 ref），浮窗本身仍是统一配方 */
        <div
          ref={panelRef}
          style={{
            position: 'fixed',
            left: pos.left,
            top: pos.top,
            width: 'max-content',
            maxWidth: MAX_WIDTH,
            visibility: pos.ready ? 'visible' : 'hidden',
          }}
          className="z-modal"
        >
          <MenuPanel role="dialog" aria-label={label} className="whitespace-normal p-2.5 text-3xs leading-relaxed text-textSecondary">
            {content}
          </MenuPanel>
        </div>,
        document.body,
      )}
    </span>
  )
}
