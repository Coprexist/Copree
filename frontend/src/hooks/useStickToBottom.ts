/**
 * 贴底跟随 —— 聊天列表「在底部就跟、不在就不跟」的**唯一实现**。
 *
 * 群视界对话（useWorldChat/WorldChatPanel）与 DSH 对话页共用本文件：
 * 阈值、rAF 节流测量、多列表支持、回到底部、用户主动翻走的同步断开，
 * 之前是两份，现在只有这一份。
 *
 * 两条容易写错的时序，写在这里当契约：
 * 1) 跟随判定只用 isAtBottomRef（与「回到底部」按钮同一个真相源），**绝不**在跟随时重新测量
 *    scrollHeight-scrollTop-clientHeight：那一刻 DOM 已撑高而 scrollTop 还没跟上，
 *    会把「正在跟随」误判成「用户翻走了」，此后越差越多、跟随永久断掉。
 * 2) 用户翻上去必须**同步**断开（wheel/touch/keydown），不能等 rAF 节流的位置判断：
 *    中间那一帧若来了流式分片，跟随会把视图拽回底部，而回弹又触发 scroll 把 ref 置回 true，
 *    用户将再也滚不上去。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

/** 距底小于它算「在底部」（测量与按钮共用同一阈值） */
export const BOTTOM_THRESHOLD = 80
/** 距顶小于它触发 onReachTop（加载更早） */
export const TOP_THRESHOLD = 30
/** 这些键表示用户想往回看 */
const SCROLL_UP_KEYS = new Set(['PageUp', 'ArrowUp', 'Home'])

export interface StickToBottomOptions {
  /** 列表顶部附近回调（如加载更早消息） */
  onReachTop?: () => void
  /** 处于底部时回调（如清未读） */
  onAtBottom?: () => void
  /** 覆盖「在底部」阈值 */
  threshold?: number
}

export interface StickToBottom {
  /** 列表元素回调 ref：移动/桌面多实例共存时逐个收集，滚动作用在全部 */
  listRef: (el: HTMLDivElement | null) => void
  /** 直接遍历当前挂载的列表元素（历史插入后保持视口等场景用） */
  eachList: (fn: (el: HTMLDivElement, i: number) => void) => void
  /** 当前第一个已挂载的列表元素（需要在提交前量位置的场景用） */
  firstList: () => HTMLDivElement | undefined
  /** 当前已挂载的列表元素数组（历史插入后按索引保持视口） */
  listEls: () => HTMLDivElement[]
  isAtBottom: boolean
  /** 同步可读的「在底部」真相（ref 与 state 同一来源）：如「不在底部才计未读」 */
  isAtBottomRef: React.RefObject<boolean>
  /** 列表内容是否真的溢出（不溢出时永远算在底部） */
  canScroll: boolean
  scrollToBottom: (smooth?: boolean) => void
  /** 滚到底并校验：双层 rAF 等布局稳定，250ms 后仍不在底部再滚一次 */
  forceScrollToBottom: () => void
  /** 内容增长后调用：在底部才跟随（首次瞬时，之后按 smooth） */
  follow: (smooth?: boolean) => void
  /** 复位「首次落底已做过」（换会话/重新加载时调） */
  resetFollow: () => void
  /** 主动重测一次滚动状态（布局变化但没产生滚动事件时用，如消息条数变化） */
  measure: () => void
}

export function useStickToBottom(options: StickToBottomOptions = {}): StickToBottom {
  const { onReachTop, onAtBottom, threshold = BOTTOM_THRESHOLD } = options
  const [isAtBottom, setIsAtBottom] = useState(true)
  const isAtBottomRef = useRef(true)
  const [canScroll, setCanScroll] = useState(false)
  // 回调放 ref：调用方每次渲染都会传新函数，进依赖会让监听反复重建
  const onReachTopRef = useRef(onReachTop)
  onReachTopRef.current = onReachTop
  const onAtBottomRef = useRef(onAtBottom)
  onAtBottomRef.current = onAtBottom

  const listElsRef = useRef<HTMLDivElement[]>([])
  const listRef = useCallback((el: HTMLDivElement | null) => {
    if (el) {
      if (!listElsRef.current.includes(el)) listElsRef.current.push(el)
    } else {
      listElsRef.current = listElsRef.current.filter((x) => x.isConnected)
    }
  }, [])
  const eachList = useCallback((fn: (el: HTMLDivElement, i: number) => void) => {
    listElsRef.current.forEach((el, i) => { if (el.isConnected) fn(el, i) })
  }, [])

  // 滚动状态统一处理（rAF 节流）：capture 监听 window scroll（scroll 不冒泡，
  // 捕获阶段才能覆盖列表/页面任何滚动）。每帧最多一次布局读取，避免滚动时反复 reflow。
  const scrollRafRef = useRef<number | null>(null)
  const updateScrollState = useCallback(() => {
    scrollRafRef.current = null
    const el = listElsRef.current.find((x) => x.isConnected)
    if (!el) return
    if (el.scrollTop < TOP_THRESHOLD) onReachTopRef.current?.()
    // 只看列表元素位置（列表铺满视口时 window.scrollY 恒 0，会误判）
    const listCanScroll = el.scrollHeight - el.clientHeight > 4
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < threshold
    isAtBottomRef.current = atBottom
    setIsAtBottom(atBottom)
    setCanScroll(listCanScroll)
    if (atBottom) onAtBottomRef.current?.()
  }, [threshold])
  const onAnyScroll = useCallback(() => {
    if (scrollRafRef.current !== null) return
    scrollRafRef.current = requestAnimationFrame(updateScrollState)
  }, [updateScrollState])
  useEffect(() => {
    window.addEventListener('scroll', onAnyScroll, true)
    return () => {
      window.removeEventListener('scroll', onAnyScroll, true)
      if (scrollRafRef.current !== null) cancelAnimationFrame(scrollRafRef.current)
    }
  }, [onAnyScroll])

  const scrollToBottom = useCallback((smooth = true) => {
    isAtBottomRef.current = true
    setIsAtBottom(true)
    eachList((el) => { el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' }) })
  }, [eachList])

  const forceScrollToBottom = useCallback(() => {
    const settle = () => {
      eachList((el) => { el.scrollTop = el.scrollHeight })
      // 兜底：内容可能还在渲染（图片/代码块），稍后仍不在底部再滚一次
      window.setTimeout(() => {
        eachList((el) => {
          if (el.scrollHeight - el.scrollTop - el.clientHeight > 10) el.scrollTop = el.scrollHeight
        })
      }, 250)
    }
    requestAnimationFrame(() => requestAnimationFrame(settle))
  }, [eachList])

  const loadedOnceRef = useRef(false)
  const follow = useCallback((smooth = true) => {
    if (!isAtBottomRef.current) return
    if (!loadedOnceRef.current) {
      loadedOnceRef.current = true
      forceScrollToBottom()
      return
    }
    eachList((el) => { el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' }) })
  }, [eachList, forceScrollToBottom])
  const resetFollow = useCallback(() => { loadedOnceRef.current = false }, [])

  // 用户主动往回看 → 同步断开跟随（不读布局）
  useEffect(() => {
    const breakFollow = () => {
      if (!isAtBottomRef.current) return
      isAtBottomRef.current = false
      setIsAtBottom(false)
    }
    const onWheel = (e: WheelEvent) => { if (e.deltaY < 0) breakFollow() }
    const onKey = (e: KeyboardEvent) => { if (SCROLL_UP_KEYS.has(e.key)) breakFollow() }
    let lastTouchY = 0
    const onTouchStart = (e: TouchEvent) => { lastTouchY = e.touches[0]?.clientY ?? 0 }
    const onTouchMove = (e: TouchEvent) => {
      const y = e.touches[0]?.clientY ?? 0
      if (y > lastTouchY) breakFollow()  // 手指下滑 = 内容上移 = 往回看
      lastTouchY = y
    }
    window.addEventListener('wheel', onWheel, { capture: true, passive: true })
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('touchstart', onTouchStart, { capture: true, passive: true })
    window.addEventListener('touchmove', onTouchMove, { capture: true, passive: true })
    return () => {
      window.removeEventListener('wheel', onWheel, true)
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('touchstart', onTouchStart, true)
      window.removeEventListener('touchmove', onTouchMove, true)
    }
  }, [])

  const firstList = useCallback(() => listElsRef.current.find((x) => x.isConnected), [])
  const listEls = useCallback(() => listElsRef.current.filter((x) => x.isConnected), [])

  return {
    listRef, eachList, firstList, listEls,
    isAtBottom, isAtBottomRef, canScroll, scrollToBottom, forceScrollToBottom, follow, resetFollow,
    measure: updateScrollState,
  }
}
