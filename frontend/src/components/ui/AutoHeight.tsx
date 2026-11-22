import { ReactNode, useLayoutEffect, useRef, useState } from 'react'

/**
 * 高度随内容平滑变化 —— 平台唯一实现。
 *
 * 为什么必须是个组件、不是一个类：`height: auto` 不参与过渡，得先把内容真实高度量出来
 * 写成像素，浏览器才知道从哪儿过渡到哪儿。
 *
 * ## 用之前先看这三条（不然会抖、会卡）
 *
 * 1. **内容里不要有 `h-full` / `height: 100%` 的子元素**：容器高度会反过来影响内容高度，
 *    ResizeObserver 和 style.height 互相推，死循环、界面狂抖。
 * 2. 回调里只在数值真的变了才 setState（组件内部已做），否则每次尺寸回调都会重渲染整棵子树。
 * 3. **不要挂在长列表的每一项上**（消息列表 500 条 × 一个 observer 才是真的卡）。
 *    它适合「一屏只有几个」的切换区、展开区。
 *
 * 另外外层是 `overflow-hidden`（过渡中内容高于容器时才不溢出），所以贴着边缘的
 * focus 环、阴影会被裁掉——用的时候外面要留出内边距。
 *
 * `prefers-reduced-motion: reduce` 时整个退化成直接切：不设 height、不加过渡，
 * 既照顾晕动症，也在低端机上省掉这份开销。
 */
export default function AutoHeight({
  children,
  open = true,
  duration = 300,
  fade = false,
  fadeKey,
  className = '',
}: {
  children: ReactNode
  /** 折叠用：false = 收到 0 高（内容仍在 DOM 里，动画才能收） */
  open?: boolean
  /** 过渡时长（ms） */
  duration?: number
  /** 内容变了一下就淡入一次（配合 fadeKey；不做「淡出 → 换 → 淡入」，那要调用方延迟换 DOM） */
  fade?: boolean
  /** 变化这个值 = 换了一份内容：重新测量 + 重放淡入 */
  fadeKey?: string | number
  className?: string
}) {
  const innerRef = useRef<HTMLDivElement | null>(null)
  const [contentHeight, setContentHeight] = useState<number | undefined>(undefined)
  // 只在挂载时读一次；运行中改系统设置不重挂载，属可接受的边界
  const [reduceMotion] = useState(() =>
    typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches)

  useLayoutEffect(() => {
    const el = innerRef.current
    if (!el || reduceMotion) return
    const apply = () => {
      const next = el.offsetHeight
      setContentHeight((prev) => (prev === next ? prev : next))
    }
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
    // fadeKey 变了 = 内层节点重挂了，ref 指向新节点，必须重新观察
  }, [reduceMotion, fadeKey])

  const collapsed = open === false
  const height = reduceMotion ? undefined : collapsed ? 0 : contentHeight
  const animating = !reduceMotion && contentHeight !== undefined

  return (
    <div
      className={className + (reduceMotion ? '' : ' overflow-hidden')}
      style={height === undefined ? undefined : { height, transition: animating ? `height ${duration}ms cubic-bezier(.4,0,.2,1)` : undefined }}
      aria-hidden={collapsed || undefined}
      // 收起时内容虽然还在 DOM 里（收起动画要用），但不该被 Tab 到、也不该接点击
      inert={collapsed || undefined}
    >
      <div
        key={fadeKey}
        ref={innerRef}
        className={fade && !reduceMotion ? 'animate-fade-in' : undefined}
      >
        {children}
      </div>
    </div>
  )
}
