import { useEffect, useRef } from 'react'
import { HANDLE_NEAR_VAR, HANDLE_Y_VAR } from '../../hooks/useWidthDrag'

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

/** 鼠标离拖条多远算「远到看不见」 */
const NEAR_FALLOFF_PX = 200

/** 指针 Y 为原点的横向渐隐带：stops 是「距指针多远开始、多远收干净」 */
function glowAt(stops: { inner: number; outer: number }, alpha: number) {
  const y = `var(${HANDLE_Y_VAR}, 50%)`
  const rgb = `rgb(var(--tw-primary-500) / ${alpha})`
  return `linear-gradient(to bottom, transparent calc(${y} - ${stops.outer}px), ${rgb} calc(${y} - ${stops.inner}px), ${rgb} calc(${y} + ${stops.inner}px), transparent calc(${y} + ${stops.outer}px))`
}

/**
 * 指针为原点的椭圆光斑：横半径 x、纵半径 y。
 * 细线用它是为了横向也有亮度梯度——正中最亮，往两边迅速收干净，
 * 否则一条等宽的实心竖条会在边缘留出硬边。
 */
function sparkAt({ x, y }: { x: number; y: number }) {
  return `radial-gradient(ellipse ${x}px ${y}px at 50% var(${HANDLE_Y_VAR}, 50%), rgb(var(--tw-primary-500)) 0%, rgb(var(--tw-primary-500) / 0) 100%)`
}

/**
 * 居中列两侧的拖条（对话列与页面内容列共用）。
 *
 * 抓取区落在内容列外的留白里，宽度自适应：留白不够时自然收成 0，所以窄列也不会压住正文，
 * 而拖到贴边时那点留白又刚好够把手柄抓回来。
 *
 * 显形分两层，共用同一个原点（指针的 Y）：
 *  · 区域高光（粉）—— 一大片柔光告诉你可以抓；浓度按鼠标到拖条的横向距离走，
 *    贴近了才最亮，离远了淡掉，所以正文里正常阅读时它不会晃眼；
 *  · 落点光斑（紫）—— 一小片椭圆，正中最亮、四周收干净，告诉你松手会停在哪；
 *    它只在悬停/拖动时出现，浓度不跟距离走。
 */
export default function WidthHandles({ varName, dragging, title, onHandleDown, onHandleMove, onHandleUp, onHandleCancel, onReset }: WidthHandlesProps) {
  const firstRef = useRef<HTMLDivElement>(null)
  const shown = dragging ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'

  /**
   * 贴近度：监听得挂在定位祖先上 —— 鼠标不在拖条上时也要算，否则「离远了淡掉」无从谈起。
   * 每帧最多读写一次（rAF 合并 pointermove），鼠标离开宿主编组直接归零。
   */
  useEffect(() => {
    const handle = firstRef.current
    const host = (handle?.offsetParent as HTMLElement | null) ?? handle?.parentElement
    if (!host) return
    let frame: number | null = null
    let pointerX = 0
    const apply = () => {
      frame = null
      const el = firstRef.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      // 拖条本身宽度内恒为最亮，往外才开始衰减
      const away = Math.max(0, Math.abs(pointerX - (rect.left + rect.width / 2)) - rect.width / 2)
      host.style.setProperty(HANDLE_NEAR_VAR, Math.max(0, 1 - away / NEAR_FALLOFF_PX).toFixed(3))
    }
    const onMove = (e: PointerEvent) => {
      pointerX = e.clientX
      frame ??= requestAnimationFrame(apply)
    }
    const onLeave = () => {
      if (frame !== null) { cancelAnimationFrame(frame); frame = null }
      host.style.setProperty(HANDLE_NEAR_VAR, '0')
    }
    host.addEventListener('pointermove', onMove)
    host.addEventListener('pointerleave', onLeave)
    return () => {
      host.removeEventListener('pointermove', onMove)
      host.removeEventListener('pointerleave', onLeave)
      if (frame !== null) cancelAnimationFrame(frame)
    }
  }, [])

  return (
    <>
      {(['left', 'right'] as const).map((side) => (
        <div
          key={side}
          ref={side === 'left' ? firstRef : undefined}
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
          {/* 区域高光：跟着指针上下的一片柔光，浓度跟鼠标的远近走 */}
          <span
            className="pointer-events-none absolute inset-0 transition-opacity duration-200"
            style={{ opacity: dragging ? 1 : `var(${HANDLE_NEAR_VAR}, 0)`, background: glowAt({ inner: 40, outer: 120 }, 0.12) }}
          />
          {/* 落点光斑：同一原点，悬停/拖动才出现 */}
          <span
            className={`pointer-events-none absolute left-1/2 top-0 h-full w-[14px] -translate-x-1/2 transition-opacity ${shown}`}
            style={{ background: sparkAt({ x: 7, y: 52 }) }}
          />
        </div>
      ))}
    </>
  )
}
