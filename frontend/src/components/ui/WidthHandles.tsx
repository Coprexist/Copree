import { HANDLE_Y_VAR } from '../../hooks/useWidthDrag'

interface WidthHandlesProps {
  /** 内容列宽的 CSS 变量名：抓取区的位置与宽度都按它算 */
  varName: string
  dragging: boolean
  /** 悬停提示（走 i18n，别在调用处硬写中文） */
  title: string
  onHandleDown: (side: 'left' | 'right') => (e: React.PointerEvent<HTMLElement>) => void
  onHandleMove: (e: React.PointerEvent<HTMLElement>) => void
  onHandleUp: (e: React.PointerEvent<HTMLElement>) => void
  onHandleCancel: (e: React.PointerEvent<HTMLElement>) => void
  /** 双击回默认（可选：对话列没有「档位」可回，就不传） */
  onReset?: () => void
}

/** 指针 Y 为原点的渐隐带：stops 是「距指针多远开始、多远收干净」，两层共用同一个原点 */
function glowAt(stops: { inner: number; outer: number }, alpha: number) {
  const y = `var(${HANDLE_Y_VAR}, 50%)`
  const rgb = `rgb(var(--tw-primary-500) / ${alpha})`
  return `linear-gradient(to bottom, transparent calc(${y} - ${stops.outer}px), ${rgb} calc(${y} - ${stops.inner}px), ${rgb} calc(${y} + ${stops.inner}px), transparent calc(${y} + ${stops.outer}px))`
}

/**
 * 居中列两侧的拖条（对话列与页面内容列共用）。
 *
 * 抓取区落在内容列外的留白里，宽度自适应：留白不够时自然收成 0，所以窄列也不会压住正文，
 * 而拖到贴边时那点留白又刚好够把手柄抓回来。
 *
 * 显形分两层、但共用同一个原点（指针的 Y）：一片跟着鼠标上下走的区域高光（大范围，告诉你可以抓）
 * 加一条窄而亮的落点细线（精确，告诉你松手会停在哪）。平时都藏着，悬停/拖动时才浮出来。
 */
export default function WidthHandles({ varName, dragging, title, onHandleDown, onHandleMove, onHandleUp, onHandleCancel, onReset }: WidthHandlesProps) {
  const shown = dragging ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
  return (
    <>
      {(['left', 'right'] as const).map((side) => (
        <div
          key={side}
          role="separator"
          aria-orientation="vertical"
          title={title}
          onPointerDown={onHandleDown(side)}
          onPointerMove={onHandleMove}
          onPointerUp={onHandleUp}
          onPointerCancel={onHandleCancel}
          onLostPointerCapture={onHandleCancel}
          onDoubleClick={onReset}
          className="group absolute top-0 bottom-0 z-overlay cursor-col-resize"
          style={{
            width: `max(0px, min(40px, calc((100% - var(${varName})) / 2 - 48px)))`,
            ...(side === 'left'
              ? { right: `calc(50% + var(${varName}) / 2 + 24px)` }
              : { left: `calc(50% + var(${varName}) / 2 + 24px)` }),
          }}
        >
          {/* 区域高光：跟着指针上下的一大片柔光 */}
          <span
            className={`pointer-events-none absolute inset-0 transition-opacity ${shown}`}
            style={{ background: glowAt({ inner: 40, outer: 120 }, 0.12) }}
          />
          {/* 落点细线：同一原点，窄而亮 */}
          <span
            className={`pointer-events-none absolute inset-y-0 left-1/2 w-[3px] -translate-x-1/2 rounded-full transition-opacity ${shown}`}
            style={{ background: glowAt({ inner: 12, outer: 52 }, 1) }}
          />
        </div>
      ))}
    </>
  )
}
