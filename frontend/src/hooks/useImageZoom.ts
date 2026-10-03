import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

/** 缩放的上下限，也是「按钮点不动」的判据 */
export const ZOOM_MIN = 0.25
export const ZOOM_MAX = 10
/** 按钮每挡的倍率：跨 40 倍的范围用加减法得按几十次 */
const ZOOM_STEP = 1.25
/** 滑块刻度取等比：0.25~10 跨 40 倍，线性刻度会把 100% 挤在左边一小截里 */
const ZOOM_SPAN = Math.log(ZOOM_MAX / ZOOM_MIN)
/** 滚轮一格的倍率——deltaY 是像素量级，取指数缩放才等速 */
const WHEEL_RATE = 1.0015

const clampZoom = (scale: number) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, scale))

/** 倍率 → 滑块位置 0~1。滑块渲染与指针换算必须共用它，否则松手瞬间滑块会跳 */
export const zoomToPct = (scale: number) => Math.log(clampZoom(scale) / ZOOM_MIN) / ZOOM_SPAN
const pctToZoom = (pct: number) => ZOOM_MIN * Math.exp(pct * ZOOM_SPAN)

/**
 * 图片预览的缩放。
 *
 * 几何只认三个量：原图尺寸、容器尺寸、倍率。100% = 等比缩进容器（小图保持原样），
 * 放大走真实布局尺寸而不是 transform —— transform 撑不出可滚区域，
 * 只好在 1 倍处把 max-width 切成 none，图就会从「适配尺寸」跳到「原始尺寸」，
 * 看着像 99% 到 101% 突然变大。
 *
 * 倍率是唯一状态：按钮、滑块、滚轮都只改它，百分比、滑块位置、图片尺寸全由它推出。
 */
export function useImageZoom() {
  const boxRef = useRef<HTMLDivElement | null>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)
  const trackRef = useRef<HTMLDivElement | null>(null)
  const observerRef = useRef<ResizeObserver | null>(null)
  const [scale, setScale] = useState(1)
  const [fit, setFit] = useState({ w: 0, h: 0 })

  /** 量 100% 时的布局尺寸：原图等比缩进容器，小图不放大 */
  const measure = useCallback(() => {
    const box = boxRef.current
    const img = imgRef.current
    if (!box || !img || !img.naturalWidth) return
    // 用 offsetWidth 而不是 clientWidth：滚动条一出现 clientWidth 就变小，
    // 拿它当基准会在「放大 → 出滚动条 → 基准缩水」之间来回抖
    const ratio = Math.min(1, box.offsetWidth / img.naturalWidth, box.offsetHeight / img.naturalHeight)
    setFit({ w: img.naturalWidth * ratio, h: img.naturalHeight * ratio })
  }, [])

  /** 缩放前记下指针底下那个点在图片里的位置，缩放后据此把滚动位置摆回去 */
  const anchorRef = useRef<{ x: number; y: number; px: number; py: number } | null>(null)
  const rememberAnchor = useCallback((clientX: number, clientY: number) => {
    const box = boxRef.current
    const img = imgRef.current
    if (!box || !img || !img.offsetWidth || !img.offsetHeight) return
    const rect = box.getBoundingClientRect()
    anchorRef.current = {
      x: clientX - rect.left,
      y: clientY - rect.top,
      px: (clientX - rect.left + box.scrollLeft - img.offsetLeft) / img.offsetWidth,
      py: (clientY - rect.top + box.scrollTop - img.offsetTop) / img.offsetHeight,
    }
  }, [])

  // 滚轮直接缩放，不必按 Ctrl。React 的 wheel 是 passive 的、preventDefault 拦不住，
  // 只能原生绑；也正因为要等容器挂载，绑定放在 ref 回调里而不是 effect
  const onWheel = useCallback((e: WheelEvent) => {
    e.preventDefault()
    const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY
    rememberAnchor(e.clientX, e.clientY)
    setScale((s) => clampZoom(s * Math.pow(WHEEL_RATE, -dy)))
  }, [rememberAnchor])

  // ref 回调而非 RefObject：容器随 loading/图片分支条件渲染，观察者与滚轮要跟着改绑
  const containerRef = useCallback((node: HTMLDivElement | null) => {
    observerRef.current?.disconnect()
    observerRef.current = null
    boxRef.current?.removeEventListener('wheel', onWheel)
    boxRef.current = node
    if (!node) return
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    observerRef.current = observer
    node.addEventListener('wheel', onWheel, { passive: false })
    measure()
  }, [measure, onWheel])

  useEffect(() => () => {
    observerRef.current?.disconnect()
    boxRef.current?.removeEventListener('wheel', onWheel)
  }, [onWheel])

  const zoomIn = useCallback(() => setScale((s) => clampZoom(s * ZOOM_STEP)), [])
  const zoomOut = useCallback(() => setScale((s) => clampZoom(s / ZOOM_STEP)), [])
  const reset = useCallback(() => setScale(1), [])

  // 拖拽平移：只认鼠标，触摸留给容器自己的惯性滚动
  const [panning, setPanning] = useState(false)
  const panRef = useRef<{ x: number; y: number; left: number; top: number } | null>(null)
  const onPanDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const box = boxRef.current
    if (e.pointerType !== 'mouse' || e.button !== 0 || !box) return
    panRef.current = { x: e.clientX, y: e.clientY, left: box.scrollLeft, top: box.scrollTop }
    e.currentTarget.setPointerCapture(e.pointerId)
    setPanning(true)
  }, [])
  const onPanMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const p = panRef.current
    const box = boxRef.current
    if (!p || !box || !e.currentTarget.hasPointerCapture(e.pointerId)) return
    box.scrollLeft = p.left - (e.clientX - p.x)
    box.scrollTop = p.top - (e.clientY - p.y)
  }, [])
  const onPanUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    panRef.current = null
    setPanning(false)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
  }, [])

  // 尺寸落定之后再摆滚动位置：图片大小变了，能滚的范围才跟着变
  useLayoutEffect(() => {
    const a = anchorRef.current
    const box = boxRef.current
    const img = imgRef.current
    if (!a || !box || !img) return
    box.scrollLeft = img.offsetLeft + a.px * img.offsetWidth - a.x
    box.scrollTop = img.offsetTop + a.py * img.offsetHeight - a.y
    anchorRef.current = null
  }, [scale])

  /** 指针横坐标 → 倍率 */
  const zoomAt = useCallback((clientX: number) => {
    const track = trackRef.current
    if (!track) return null
    const rect = track.getBoundingClientRect()
    return pctToZoom(Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)))
  }, [])

  // 指针捕获：鼠标、手指、触控笔一条路，滑出轨道也还跟着走，松手不会漏到 document
  const sliderRef = useCallback((node: HTMLDivElement | null) => { trackRef.current = node }, [])
  const onSliderDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const next = zoomAt(e.clientX)
    if (next === null) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    setScale(next)
  }, [zoomAt])
  const onSliderMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!e.currentTarget.hasPointerCapture(e.pointerId)) return
    const next = zoomAt(e.clientX)
    if (next !== null) setScale(next)
  }, [zoomAt])
  const onSliderUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
  }, [])

  // 尺寸没量出来之前先按容器适配，量到之后换成显式尺寸。
  // max-width/height 必须显式写 none：Tailwind 给 img 预置了 max-width:100%，
  // 不解除的话放大只在垂直方向生效，横向永远贴着容器宽
  const imgStyle: React.CSSProperties = fit.w
    ? { width: `${fit.w * scale}px`, height: `${fit.h * scale}px`, maxWidth: 'none', maxHeight: 'none' }
    : { maxWidth: '100%', maxHeight: '100%' }

  return {
    scale,
    pct: zoomToPct(scale),
    zoomIn,
    zoomOut,
    reset,
    containerRef,
    imgRef,
    onImgLoad: measure,
    imgStyle,
    sliderRef,
    onSliderDown,
    onSliderMove,
    onSliderUp,
    panning,
    onPanDown,
    onPanMove,
    onPanUp,
  }
}
