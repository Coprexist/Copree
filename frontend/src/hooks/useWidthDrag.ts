import { useCallback, useEffect, useRef, useState } from 'react'
import { clampWidth } from './useResizableSidebar'

/** 指示线跟着指针上下走的变量名（手柄自己写，样式里读） */
export const HANDLE_Y_VAR = '--width-handle-y'
/** 抓取区高光的「贴近度」0~1：按鼠标到拖条的水平距离算，越近越亮 */
export const HANDLE_NEAR_VAR = '--width-handle-near'

/**
 * 居中列宽拖拽的共用机关：对话列与页面内容列只此一份。
 *
 * 三件事：
 *  1) 列居中，拖任一条边都是「两边各让一半」，所以宽度按 2× 指针位移变，条才跟手；
 *  2) 拖动期间每帧最多落一次 DOM（rAF 合并同一帧里的多次 move），且不 setState ——
 *     消息列表与日志页每帧重渲的代价太大；松手才落盘 + 回写状态；
 *  3) 上限由调用方给（通常是「容器宽 - 留白预算」），拖到贴边也留得下重拖的把手。
 *
 * 指针用 capture 收在手柄自己身上（不挂 document）：拖出窗口、拖到 iframe 上都不断线，
 * 松手/取消/丢捕获三条路都会收尾。默认宽度与偏好怎么读留给调用方：
 * 页面级是宽度档位，对话列是自适应公式。
 */
interface WidthDragOptions {
  /** 偏好落盘的键 */
  storageKey: string
  /** 列的最小宽度 */
  min: number
  /** 当前列宽（拖动起点；也是没偏好时的默认值来源） */
  measure: () => number
  /** 当前可用上限（通常 = 容器宽 - 留白预算） */
  room: () => number
  /** 每帧把宽度落到 DOM */
  apply: (width: number) => void
  /** 松手后回写状态（调用方据此重新收敛） */
  commit: (width: number) => void
}

export function useWidthDrag(options: WidthDragOptions) {
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef<{ x: number; base: number; outward: 1 | -1; latest: number } | null>(null)
  const frameRef = useRef<number | null>(null)
  // 回调每次渲染都是新的，用 ref 兜住：拖动中不重建监听，也读得到最新的策略
  const optsRef = useRef(options)
  optsRef.current = options

  const cancelFrame = useCallback(() => {
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current)
      frameRef.current = null
    }
  }, [])

  const endDrag = useCallback(() => {
    cancelFrame()
    dragRef.current = null
    setDragging(false)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }, [cancelFrame])

  /** 指针位置 → 列宽 */
  const widthAt = (clientX: number, d: NonNullable<typeof dragRef.current>) => {
    const { min, room } = optsRef.current
    const raw = d.base + (d.outward === 1 ? clientX - d.x : d.x - clientX) * 2
    return clampWidth(raw, min, Math.max(min, room()))
  }

  const onHandleDown = useCallback((side: 'left' | 'right') => (e: React.PointerEvent<HTMLElement>) => {
    if (e.button !== 0) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    dragRef.current = {
      x: e.clientX,
      base: optsRef.current.measure(),
      outward: side === 'right' ? 1 : -1,
      latest: e.clientX,
    }
    setDragging(true)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [])

  /** 拖动计算；指示线位置由拖条组件按感应区坐标写（那边才看得到整个感应区） */
  const onHandleMove = useCallback((e: React.PointerEvent<HTMLElement>) => {
    const handle = e.currentTarget
    const d = dragRef.current
    if (!d || !handle.hasPointerCapture(e.pointerId)) return
    d.latest = e.clientX
    if (frameRef.current !== null) return
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null
      const cur = dragRef.current
      if (cur) optsRef.current.apply(widthAt(cur.latest, cur))
    })
  }, [])

  const onHandleUp = useCallback((e: React.PointerEvent<HTMLElement>) => {
    const handle = e.currentTarget
    if (!handle.hasPointerCapture(e.pointerId)) return
    handle.releasePointerCapture(e.pointerId)
    const d = dragRef.current
    cancelFrame()
    // 没真的拖过就别写盘（点一下手柄不该变成一次「偏好」）
    if (d && d.latest !== d.x) {
      const final = widthAt(d.latest, d)
      try { localStorage.setItem(optsRef.current.storageKey, String(final)) } catch { /* 隐私模式等写不了就算了 */ }
      optsRef.current.apply(final)
      optsRef.current.commit(final)
    }
    endDrag()
  }, [cancelFrame, endDrag, widthAt])

  useEffect(() => cancelFrame, [cancelFrame])

  return {
    dragging,
    onHandleDown,
    onHandleMove,
    onHandleUp,
    onHandleCancel: endDrag,
  }
}
