import { useEffect, useRef } from 'react'
import { HANDLE_NEAR_VAR, HANDLE_Y_VAR } from '../../hooks/useWidthDrag'

type Side = 'left' | 'right'

interface WidthHandlesProps {
  /** 内容列宽的 CSS 变量名：抓取区的位置与宽度都按它算 */
  varName: string
  dragging: boolean
  /** 悬停提示（走 i18n，别在调用处硬写中文） */
  title: string
  onHandleDown: (side: Side) => (e: React.PointerEvent<HTMLElement>) => void
  onHandleMove: (e: React.PointerEvent<HTMLElement>) => void
  onHandleUp: (e: React.PointerEvent<HTMLElement>) => void
  onHandleCancel: (e: React.PointerEvent<HTMLElement>) => void
  /** 双击回默认（可选：对话列没有「档位」可回，就不传） */
  onReset?: () => void
}

/** 拖条本体宽度，也是高光铺满的宽度：能看到的那块，就是按得下去的那块 */
const HANDLE_W = 40
/** 拖条中心距内容列边缘：24 的内缩 + 半个拖条 */
const HANDLE_CENTER = 24 + HANDLE_W / 2
/** 鼠标离拖条多远算「远到看不见」：只在拖条边上一点点生效，走远就干净 */
const NEAR_FALLOFF_PX = 80
/** 浓度曲线：线性看不出差别，拉陡一点近处才够亮、稍远就明显淡下去 */
const NEAR_CURVE = 1.6
/** 高光浓度上限：它要跟正文底色共存，太浓就成了色块 */
const GLOW_ALPHA = 0.18
/**
 * 高光的上下过渡：实心 ±10px、到 ±28px 收干净。
 * 过渡再拉长（试过 ±120px）整块就糊成一团柔光，边界都看不出来；
 * 收短之后它就是一块跟着指针走的亮斑，位置一眼可辨。
 */
const GLOW_INNER_PX = 10
const GLOW_OUTER_PX = 28
/**
 * 感应区宽度（从内容列边缘起算）：24 内缩 + 40 拖条 + 一个衰减距离 80。
 * 这个宽度是算出来的而不是拍脑袋——拖条中心到区缘正好是「半个拖条 + 一个衰减距离」，
 * 所以走到区边时高光恰好淡到 0，出区归零不跳变；也因为它「刚好够用」，
 * 监听只落在内容列外的一小条留白里：正文里怎么移动都不参与计算，鼠标在离拖条很远的地方晃也不惊动它。
 */
const ZONE_PX = 144

/** 以指针 Y 为原点的上下渐隐 */
function fadeAt({ inner, outer }: { inner: number; outer: number }) {
  const y = `var(${HANDLE_Y_VAR}, 50%)`
  const rgb = `rgb(var(--tw-primary-500) / ${GLOW_ALPHA})`
  return `linear-gradient(to bottom, transparent calc(${y} - ${outer}px), ${rgb} calc(${y} - ${inner}px), ${rgb} calc(${y} + ${inner}px), transparent calc(${y} + ${outer}px))`
}

/**
 * 居中列两侧的拖条（对话列与页面内容列共用）。
 *
 * 结构是「感应区 > 拖条」两层：
 *  · 感应区落在内容列外的留白里（压不到正文），负责算鼠标的贴近度与光斑的 Y；
 *  · 拖条收按下/拖动，也是视觉所在。
 *
 * 视觉只留一块亮斑：宽度就是拖条本身（能看到的那块 = 按得下去的那块），纵向跟着指针走，
 * 浓度按鼠标到拖条的横向距离走（正文里正常阅读时不晃眼），拖动中恒亮。
 * 曾经在同一位置又叠过一根更亮的落点线，但两层同原点、同色、都跟着指针，
 * 信息完全重复，松手位置本来也由这块斑自己表达，于是删掉。
 */
export default function WidthHandles({ varName, dragging, title, onHandleDown, onHandleMove, onHandleUp, onHandleCancel, onReset }: WidthHandlesProps) {
  const frameRef = useRef<number | null>(null)
  const pointerRef = useRef({ x: 0, y: 0 })
  useEffect(() => () => { if (frameRef.current !== null) cancelAnimationFrame(frameRef.current) }, [])

  /**
   * 感应区内每帧算一次：光斑的 Y（写到两侧共用的宿主上）与浓度。
   * 覆盖面整个感应区而不只是拖条本身，鼠标在留白里上下移动时光斑也跟着走。
   */
  const onZoneMove = (side: Side) => (e: React.PointerEvent<HTMLDivElement>) => {
    const zone = e.currentTarget
    pointerRef.current = { x: e.clientX, y: e.clientY }
    if (frameRef.current !== null) return
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null
      const rect = zone.getBoundingClientRect()
      // 原点写在两侧共用的宿主上（感应区的定位祖先）：写在自己身上，另一侧就永远停在旧值
      const host = (zone.offsetParent as HTMLElement | null) ?? zone.parentElement
      const { x, y } = pointerRef.current
      if (host) host.style.setProperty(HANDLE_Y_VAR, `${y - rect.top}px`)
      // 拖条中心在感应区内的位置：左侧那条靠右缘，右侧那条靠左缘
      const centerX = side === 'right' ? rect.left + HANDLE_CENTER : rect.right - HANDLE_CENTER
      const away = Math.max(0, Math.abs(x - centerX) - HANDLE_W / 2)
      const near = Math.max(0, 1 - away / NEAR_FALLOFF_PX)
      zone.style.setProperty(HANDLE_NEAR_VAR, Math.pow(near, NEAR_CURVE).toFixed(3))
    })
  }

  const onZoneLeave = (e: React.PointerEvent<HTMLDivElement>) => {
    if (frameRef.current !== null) { cancelAnimationFrame(frameRef.current); frameRef.current = null }
    e.currentTarget.style.setProperty(HANDLE_NEAR_VAR, '0')
  }

  return (
    <>
      {(['left', 'right'] as const).map((side) => (
        <div
          key={side}
          onPointerMove={onZoneMove(side)}
          onPointerLeave={onZoneLeave}
          className="absolute top-0 bottom-0 z-overlay"
          style={{
            // 单侧留白（内容列居中），再按 ZONE_PX 封顶
            width: `min(calc((100% - var(${varName})) / 2), ${ZONE_PX}px)`,
            ...(side === 'left'
              ? { right: `calc(50% + var(${varName}) / 2)` }
              : { left: `calc(50% + var(${varName}) / 2)` }),
          }}
        >
          <div
            role="separator"
            aria-orientation="vertical"
            title={title}
            onPointerDown={onHandleDown(side)}
            onPointerMove={onHandleMove}
            onPointerUp={onHandleUp}
            onPointerCancel={onHandleCancel}
            onLostPointerCapture={onHandleCancel}
            onDoubleClick={onReset}
            className="absolute inset-y-0 cursor-col-resize"
            style={{
              // 从内容列边缘内缩 24，余下的放拖条；留白不够时自然收成 0
              width: `max(0px, min(${HANDLE_W}px, calc(100% - 48px)))`,
              ...(side === 'left' ? { right: '24px' } : { left: '24px' }),
            }}
          >
            {/* 亮斑：宽度 = 拖条（能看到的那块就是按得下去的那块），浓度跟鼠标的远近走 */}
            <span
              className="pointer-events-none absolute inset-0 transition-opacity duration-150"
              style={{
                opacity: dragging ? 1 : `var(${HANDLE_NEAR_VAR}, 0)`,
                background: fadeAt({ inner: GLOW_INNER_PX, outer: GLOW_OUTER_PX }),
              }}
            />
          </div>
        </div>
      ))}
    </>
  )
}
