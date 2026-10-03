import type { MouseEvent } from 'react'

/** 八个缩放方向：与 FilePreviewModal 的 startResize 同名同序 */
export type ResizeSide = 'n' | 's' | 'w' | 'e' | 'nw' | 'ne' | 'sw' | 'se'

const SIDES: ResizeSide[] = ['n', 's', 'w', 'e', 'nw', 'ne', 'sw', 'se']

/**
 * 可缩放卡片的外描边 + 八向热区（仅电脑端）。
 *
 * 几何与配色都在 index.css 的 .resize-edge 里，由 --edge-r / --edge-w 推出来；
 * 这里只做一件事：把八个方向铺出来，并把按下事件交回调用方。
 */
export default function ResizeEdges({
  onResizeStart,
  className = '',
}: {
  onResizeStart: (side: ResizeSide) => (e: MouseEvent) => void
  /** 附加类（如 z-overlay） */
  className?: string
}) {
  return (
    <>
      {SIDES.map((side) => (
        <div
          key={side}
          className={`resize-edge hidden md:block ${className}`}
          data-edge={side}
          onMouseDown={onResizeStart(side)}
        />
      ))}
    </>
  )
}
