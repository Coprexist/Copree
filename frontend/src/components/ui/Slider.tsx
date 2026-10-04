import { useCallback, useRef } from 'react'

/**
 * 滑杆（range）：全站唯一一份 —— 轨道样式在 index.css 的 input[type="range"] 里，这里只管行为。
 *
 * 左侧填充：值换算成百分比写在 --slider-pct 上，轨道用一段主色画到滑钮左边。
 *
 * 相对拖拽：原生 range 是"按下即跳"——一按就把值跳到指针所在位置，手指偏一点
 * 就整段跳走（误触的来源）。这里**任何位置按下都只记下指针 x 与当前值**，之后按
 * **沿轨道方向的位移量**换值：垂直方向的位移一概不参与（手抖不影响值），竖滑还能
 * 让页面正常滚动（input[type=range] 的 touch-action: pan-y）。
 * 想直接跳到某处就**双击**那一处——跳值是明确表达出来的意图，不是手滑的结果。
 */

const TONE_VAR = {
  primary: '--tw-primary-500',
  accent: '--tw-accent-400',
  mint: '--tw-mint-400',
} as const

/** 与 index.css 里滑钮的 --slider-thumb 保持一致：换算行程要用 */
const THUMB_PX = 16

interface SliderProps {
  value: number
  onChange: (next: number) => void
  min?: number
  max?: number
  step?: number
  disabled?: boolean
  /** 填充与滑钮的颜色档（默认主色） */
  tone?: keyof typeof TONE_VAR
  className?: string
  'aria-label'?: string
}

export default function Slider({
  value, onChange, min = 0, max = 100, step = 1,
  disabled = false, tone = 'primary', className = '', ...rest
}: SliderProps) {
  const ref = useRef<HTMLInputElement>(null)
  const drag = useRef<{ x: number; value: number } | null>(null)

  const span = max - min
  const percent = span > 0 ? ((value - min) / span) * 100 : 0

  /** 贴到档位上并夹在区间里：浮点步长（0.05/0.1）不会漏出 0.30000000000000004 */
  const quantize = useCallback((raw: number) => {
    if (step <= 0) return Math.min(max, Math.max(min, raw))
    const snapped = Math.round((raw - min) / step) * step + min
    return Math.min(max, Math.max(min, Number(snapped.toFixed(6))))
  }, [min, max, step])

  /** 轨道可用行程 = 宽度 - 滑钮直径（滑钮中心从半径走到 宽-半径） */
  const travel = useCallback(() => {
    const el = ref.current
    return el ? Math.max(1, el.getBoundingClientRect().width - THUMB_PX) : 1
  }, [])

  const emit = useCallback((next: number) => {
    if (next !== value) onChange(next)
  }, [onChange, value])

  /** 按下：一律按位移走 —— 按在轨道远处同样是手滑，不再"按下即跳" */
  const handlePointerDown = (e: React.PointerEvent<HTMLInputElement>) => {
    const el = ref.current
    if (disabled || !el) return
    // 抢在原生跳值之前；顺带把焦点拿到手（preventDefault 会吃掉默认聚焦）
    e.preventDefault()
    el.focus({ preventScroll: true })
    el.setPointerCapture(e.pointerId)
    drag.current = { x: e.clientX, value }
  }

  /** 双击 ＝ "就跳到这儿"，这是唯一会跳值的表达 */
  const handleDoubleClick = (e: React.MouseEvent<HTMLInputElement>) => {
    const el = ref.current
    if (disabled || !el) return
    const rect = el.getBoundingClientRect()
    emit(quantize(min + ((e.clientX - rect.left - THUMB_PX / 2) / travel()) * span))
  }

  const handlePointerMove = (e: React.PointerEvent<HTMLInputElement>) => {
    const start = drag.current
    if (!start || disabled) return
    emit(quantize(start.value + ((e.clientX - start.x) / travel()) * span))
  }

  const endDrag = (e: React.PointerEvent<HTMLInputElement>) => {
    if (!drag.current) return
    drag.current = null
    ref.current?.releasePointerCapture?.(e.pointerId)
  }

  return (
    <input
      {...rest}
      ref={ref}
      type="range"
      className={className}
      style={{
        ['--slider-c' as string]: `var(${TONE_VAR[tone]})`,
        ['--slider-pct' as string]: `${percent}%`,
      } as React.CSSProperties}
      min={min}
      max={max}
      step={step}
      value={value}
      disabled={disabled}
      // 键盘（方向键 / Home / End / PageUp）仍走原生：它自己会算好新值派发 change
      onChange={e => emit(Number(e.target.value))}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onDoubleClick={handleDoubleClick}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    />
  )
}

/** 带标签的滑杆：标签 + 当前值一行，下面是滑杆与说明（AI 参数面板里那一族） */
export function SliderField({
  label, value, setValue, min, max, step, desc, tone,
}: {
  label: string; value: number; setValue: (v: number) => void
  min: number; max: number; step: number; desc?: string; tone?: keyof typeof TONE_VAR
}) {
  return (
    <div>
      <div className="flex justify-between mb-1">
        <label className="text-xs text-textSecondary">{label}</label>
        <span className="text-xs font-mono text-textPrimary">{value}</span>
      </div>
      <Slider min={min} max={max} step={step} value={value} onChange={setValue} tone={tone} aria-label={label} />
      {desc && <p className="text-3xs text-textMuted mt-0.5">{desc}</p>}
    </div>
  )
}
