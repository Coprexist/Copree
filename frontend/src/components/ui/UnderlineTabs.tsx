import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'

/**
 * 下划线页签：一条底边 + 选中项高亮，选中态的下划线是一条**共享指示器**，
 * 切换时平移到新页签（不是各自淡入淡出，那样会闪）。
 *
 * 这个画法原先在 AI 详情页、群设置、好友列表、世界设计页各手抄过一遍（颜色甚至
 * primary-400/500 两种、内边距三套），所以收进默认库当基准：新页面别再自己拼 class。
 *
 * 三档尺寸对应仓里真实存在的三种位置，别随手改：
 *   md    页面主面板（px-4 py-2 text-sm）—— AI 详情页、用量分析
 *   sm    卡片内二级页签（px-3 py-1.5 text-xs）—— 工作区 TODO/PLAN/JOURNAL
 *   panel 面板与移动端等宽条（px-2 py-2.5 text-xs）—— 群设置、好友列表；触控目标不缩水
 */
export interface UnderlineTabItem<T extends string> {
  key: T
  label: string
  icon?: ReactNode
  /** 标签右侧小角标（未读数这类）。库只负责位置，配色由调用方给 */
  badge?: ReactNode
}

const SIZING = {
  md: 'px-4 py-2 text-sm',
  sm: 'px-3 py-1.5 text-xs',
  panel: 'px-2 py-2.5 text-xs',
} as const

interface UnderlineTabsProps<T extends string> {
  items: readonly UnderlineTabItem<T>[]
  value: T
  onChange: (key: T) => void
  size?: keyof typeof SIZING
  /** 等宽铺满一行（面板 / 移动端切页）；默认按内容宽度排 */
  grow?: boolean
  /** 关掉滑动动画（给"页签会整条换掉"这类场景留个退路） */
  animated?: boolean
  className?: string
}

export default function UnderlineTabs<T extends string>({
  items,
  value,
  onChange,
  size = 'md',
  grow = false,
  animated = true,
  className = '',
}: UnderlineTabsProps<T>) {
  const rowRef = useRef<HTMLDivElement | null>(null)
  // 观察器回调与布局效应是异步的：它们必须读到"此刻"的选中项，不能闭包住上一次渲染的 value
  const valueRef = useRef(value)
  const [indicator, setIndicator] = useState<{ left: number; width: number } | null>(null)

  /** 量当前页签在行里的位置（分数像素也要留住）。量而不是写死百分比：文案宽度会变
   *  （换语言、角标出现/消失），百分比立刻就对不上了 */
  const measure = useCallback(() => {
    const row = rowRef.current
    const el = row?.querySelector<HTMLButtonElement>(`[data-tab-key="${valueRef.current}"]`)
    if (!row || !el) {
      setIndicator(null)
      return
    }
    // 用 rect 而不是 offsetLeft/offsetWidth：后者是整数，缩放成非整数倍率时会被取整
    // 同样的滚动量也要补回去：rect 是屏幕坐标，指示器排的是内容坐标
    const rect = el.getBoundingClientRect()
    const base = row.getBoundingClientRect()
    const next = {
      left: rect.left - base.left + row.scrollLeft,
      width: rect.width,
    }
    // 值没变就交回同一个对象：否则每量一次都触发一轮渲染
    setIndicator(prev => (prev && Math.abs(prev.left - next.left) < 0.5 && Math.abs(prev.width - next.width) < 0.5 ? prev : next))
  }, [])

  // 首帧量一次（元素带着终值挂载，所以不会从 1px 宽滑过去），之后每次切页签重量
  useLayoutEffect(() => {
    valueRef.current = value
    if (animated) measure()
  }, [animated, value, measure])

  // 页签文案变长（换语言）、容器变窄（收起侧栏）都会动到宽度，尺寸一变就重量
  const itemKeys = items.map(item => item.key).join('|')
  useEffect(() => {
    if (!animated || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    rowRef.current?.querySelectorAll('[data-tab-key]').forEach(el => observer.observe(el))
    return () => observer.disconnect()
  }, [animated, itemKeys, measure])

  return (
    // 指示器留在滚动层里面：页签条横滑时它跟着内容一起走，不需要另记滚动量
    // （记 scrollLeft 再补 transform 那套，一旦那份 state 没跟上滚动，就会整条错位到新页签右边）。
    // 横向滚动条用 .no-scrollbar 不画出来：它占的那 5px 会把指示器顶到离底边一整条滚动条高的地方
    <div ref={rowRef} className={`relative flex gap-1 border-b border-border overflow-x-auto no-scrollbar ${className}`}>
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          data-tab-key={item.key}
          onClick={() => onChange(item.key)}
          className={`${SIZING[size]} ${grow ? 'flex-1 justify-center' : ''} inline-flex items-center gap-1.5 font-medium border-b-2 border-transparent transition-colors whitespace-nowrap ${
            value === item.key ? 'text-primary-400' : 'text-textMuted hover:text-textSecondary'
          }`}
        >
          {item.icon}
          {item.label}
          {item.badge}
        </button>
      ))}
      {/* 位置与长度都直接写成 px，不拿 1px 的底去 scaleX：缩放成非整数倍率时那 1px 会被
          按设备像素取整，被它乘出来的长度就跟页签对不上了（真机就是这样"长度不随缩放"）。
          动画开着时页签自己的下边框一律透明——两条线并存会露出"新页签已经画好了"的破绽。
          bottom-0 = 紧贴底边那条灰线上方（不是压上去）：两条线都看得见 */}
      {animated && indicator && (
        <span
          aria-hidden
          className="pointer-events-none absolute bottom-0 h-0.5 bg-primary-500 transition-[left,width] duration-200 ease-out motion-reduce:transition-none"
          style={{ left: indicator.left, width: indicator.width }}
        />
      )}
    </div>
  )
}
