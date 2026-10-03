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

/** 拖条本体宽度；抓取区再宽也还是它收按下事件 */
const HANDLE_W = 40
/** 拖条中心距内容列边缘：24 的内缩 + 半个拖条 */
const HANDLE_CENTER = 24 + HANDLE_W / 2
/** 鼠标离拖条多远算「远到看不见」：只在拖条边上一点点生效，走远就干净 */
const NEAR_FALLOFF_PX = 80
/** 浓度曲线：线性看不出差别，拉陡一点近处才够亮、稍远就明显淡下去 */
const NEAR_CURVE = 1.6
/** 高光自身的不透明度：太淡的话再乘浓度也还是看不见 */
const GLOW_ALPHA = 0.3
/**
 * 感应区宽度（从内容列边缘起算）：24 内缩 + 40 拖条 + 一个衰减距离 80。
 * 这个宽度是算出来的而不是拍脑袋——拖条中心到区缘正好是「半个拖条 + 一个衰减距离」，
 * 所以走到区边时高光恰好淡到 0，出区归零不跳变；也因为它「刚好够用」，
 * 监听只落在内容列外的一小条留白里：正文里怎么移动都不参与计算，鼠标在离拖条很远的地方晃也不惊动它。
 */
const ZONE_PX = 144

/** 指针 Y 为原点的横向渐隐带：stops 是「距指针多远开始、多远收干净」 */
function glowAt(stops: { inner: number; outer: number }) {
  const y = `var(${HANDLE_Y_VAR}, 50%)`
  const rgb = `rgb(var(--tw-primary-500) / ${GLOW_ALPHA})`
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
 * 结构是「感应区 > 拖条」两层：
 *  · 感应区落在内容列外的留白里（压不到正文），负责算鼠标的贴近度与指示线的 Y；
 *  · 拖条收按下/拖动，也是视觉所在。
 *
 * 显形分两层，共用同一个原点（指针的 Y）：
 *  · 区域高光（粉）—— 一大片柔光告诉你可以抓；浓度按鼠标到拖条的横向距离走（曲线拉陡，
 *    近处满、稍远就明显淡下去），所以正文里正常阅读时它不晃眼；
 *  · 落点光斑（紫）—— 一小片椭圆，正中最亮、四周收干净，告诉你松手会停在哪；
 *    它只在悬停/拖动时出现，浓度不跟距离走。
 */
export default function WidthHandles({ varName, dragging, title, onHandleDown, onHandleMove, onHandleUp, onHandleCancel, onReset }: WidthHandlesProps) {
  const frameRef = useRef<number | null>(null)
  const pointerRef = useRef({ x: 0, y: 0 })
  useEffect(() => () => { if (frameRef.current !== null) cancelAnimationFrame(frameRef.current) }, [])

  /**
   * 感应区内每帧算一次：指示线的 Y（写到两侧共用的宿主上）与高光浓度。
   * 覆盖面整个感应区而不只是拖条本身，鼠标在留白里上下移动时里面的光也跟着走。
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

  const shown = dragging ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'

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
            className="group absolute inset-y-0 cursor-col-resize"
            style={{
              // 从内容列边缘内缩 24，余下的放拖条；留白不够时自然收成 0
              width: `max(0px, min(${HANDLE_W}px, calc(100% - 48px)))`,
              ...(side === 'left' ? { right: '24px' } : { left: '24px' }),
            }}
          >
            {/* 区域高光：跟着指针上下的一片柔光，浓度跟鼠标的远近走 */}
            <span
              className="pointer-events-none absolute inset-0 transition-opacity duration-150"
              style={{ opacity: dragging ? 1 : `var(${HANDLE_NEAR_VAR}, 0)`, background: glowAt({ inner: 40, outer: 120 }) }}
            />
            {/* 落点光斑：同一原点，悬停/拖动才出现 */}
            <span
              className={`pointer-events-none absolute left-1/2 top-0 h-full w-[14px] -translate-x-1/2 transition-opacity ${shown}`}
              style={{ background: sparkAt({ x: 7, y: 52 }) }}
            />
          </div>
        </div>
      ))}
    </>
  )
}
