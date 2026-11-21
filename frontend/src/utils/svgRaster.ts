/**
 * SVG → PNG（浏览器自己渲染）与导出尺寸的计算。
 *
 * 为什么不在后端转：后端要光栅化就得引入 cairosvg / librsvg 这类重依赖，而用户看到的预览
 * 本来就是浏览器画的——同一个渲染器出图，导出的样子才和预览一致。
 *
 * 三个必须处理的边界（都是 SVG 特有的）：
 * - **没有固有尺寸**：只有 viewBox、没有 width/height 的 SVG 在 <img> 里可能拿不到尺寸，
 *   直接 drawImage 会得到一张空白画布。所以先按 width/height → viewBox → 兜底定尺寸；
 * - **放大要清晰**：矢量按目标像素重画才不糊，所以把根元素的 width/height 改写成导出尺寸，
 *   而不是把 24px 的图标按 24px 画完再拉大；
 * - **不涂底色**：PNG 保留透明，跟 SVG 的观感一致（要白底的人自己再叠）。
 *   但很多 SVG 自己就带一层满幅底（导出工具的"添加背景"）：那不是我们的行为，是文件本身，
 *   所以单独识别出来，交给用户在导出时决定去不去除。
 */

/** 一眼能认出来的"满幅背景层"：直接子节点里铺满画布的不透明矩形 */
export interface SvgBackground {
  /** 它的填充色，用于在弹窗里告诉用户"要去掉的是哪一层" */
  fill: string
}

/** 一次读进来、随手能算尺寸的 SVG（文本留着：导出时要再解析一次改写根元素） */
export interface SvgSource {
  text: string
  /** 它自己的尺寸：width/height 优先，其次 viewBox，最后兜底 */
  width: number
  height: number
  /** SVG 自带的满幅背景层（很多导出工具会加一层白/色底）：不清掉，PNG 就"没有透明" */
  backgrounds: SvgBackground[]
}

/** 导出选项：去掉满幅背景层（默认保留；弹窗里检测到才给这个开关） */
export interface RasterOptions {
  stripBackground?: boolean
}

/** 导出长边的上下限：太小是马赛克，太大会撞浏览器画布上限 */
export const EXPORT_SIDE_MIN = 16
export const EXPORT_SIDE_MAX = 4096
/** 弹窗里的快捷档位（常见表情/头像/贴图尺寸） */
export const EXPORT_SIDE_PRESETS = [256, 512, 1024, 2048]
/** 没有任何尺寸线索时的兜底画布 */
const FALLBACK_SIDE = 512

function lengthToPx(raw: string | null): number {
  const text = String(raw ?? '').trim()
  if (!text) return 0
  const value = parseFloat(text)
  if (!Number.isFinite(value) || value <= 0) return 0
  if (/%$/.test(text)) return 0          // 百分比没有参照系，当没写
  return value
}

/** 从根元素读尺寸：width/height → viewBox → 单向尺寸 → 兜底 */
function sizeFromRoot(root: Element): { width: number; height: number } {
  const w = lengthToPx(root.getAttribute('width'))
  const h = lengthToPx(root.getAttribute('height'))
  if (w && h) return { width: w, height: h }
  const box = String(root.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(Number)
  if (box.length === 4 && box.every(Number.isFinite) && box[2] > 0 && box[3] > 0) {
    return { width: box[2], height: box[3] }
  }
  if (w) return { width: w, height: w }
  if (h) return { width: h, height: h }
  return { width: FALLBACK_SIDE, height: FALLBACK_SIDE }
}

/** width/height 可能是 100%：满幅背景层常见写法，按画布尺寸算 */
function lengthOrFull(raw: string | null, full: number): number {
  const text = String(raw ?? '').trim()
  if (!text) return 0
  if (/^100%$/.test(text)) return full
  return lengthToPx(text)
}

/** 元素上的"实色填充"；没有/透明/渐变引用一律不算背景层 */
function solidFill(el: Element): string {
  const style = String(el.getAttribute('style') || '')
  const inline = /(?:^|;)\s*fill\s*:\s*([^;]+)/i.exec(style)
  const raw = String(inline ? inline[1] : el.getAttribute('fill') || '').trim()
  if (!raw) return ''
  const lower = raw.toLowerCase()
  if (lower === 'none' || lower === 'transparent' || lower.startsWith('url(')) return ''
  const fillOpacity = parseFloat(String(el.getAttribute('fill-opacity') || '1'))
  const opacity = parseFloat(String(el.getAttribute('opacity') || '1'))
  if (!(fillOpacity > 0.99) || !(opacity > 0.99)) return ''
  return raw
}

/** 用户坐标系尺寸：矩形/圆这些属性的单位是 viewBox 的，不是最终像素 */
function userSize(root: Element): { width: number; height: number } {
  const box = String(root.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(Number)
  if (box.length === 4 && box.every(Number.isFinite) && box[2] > 0 && box[3] > 0) {
    return { width: box[2], height: box[3] }
  }
  return sizeFromRoot(root)
}

/**
 * 找出"铺满画布的不透明矩形"——直接子节点、无变换/滤镜/裁剪、宽高都不小于画布的 99%。
 *
 * 为什么只认这一种：这是导出工具（Figma / Illustrator / 各种图标站）加背景层时的固定长相；
 * 再放宽就会把设计里真正的大色块当成背景删掉。判定不到时一律不动原图。
 */
function fullCanvasRects(root: Element, size: { width: number; height: number }): Element[] {
  const out: Element[] = []
  for (const child of Array.from(root.children)) {
    if (child.nodeName.toLowerCase() !== 'rect') continue
    if (child.children.length) continue
    if (['transform', 'filter', 'mask', 'clip-path'].some((a) => child.hasAttribute(a))) continue
    if (!solidFill(child)) continue
    const w = lengthOrFull(child.getAttribute('width'), size.width)
    const h = lengthOrFull(child.getAttribute('height'), size.height)
    const x = lengthToPx(child.getAttribute('x'))
    const y = lengthToPx(child.getAttribute('y'))
    if (w < size.width * 0.99 || h < size.height * 0.99) continue
    if (x > size.width * 0.01 || y > size.height * 0.01) continue
    out.push(child)
  }
  return out
}

function parseSvg(text: string): Element {
  const doc = new DOMParser().parseFromString(text, 'image/svg+xml')
  if (doc.querySelector('parsererror')) throw new Error('SVG 解析失败')
  const root = doc.documentElement
  if (!root || root.nodeName.toLowerCase() !== 'svg') throw new Error('不是 SVG 文件')
  return root
}

/** 取 SVG 文本并量出它自己的尺寸（导出弹窗打开时调一次，之后反复改尺寸不再重新取） */
export async function readSvg(src: string): Promise<SvgSource> {
  const res = await fetch(src, { credentials: 'same-origin' })
  if (!res.ok) throw new Error(`取 SVG 失败（HTTP ${res.status}）`)
  const text = await res.text()
  const root = parseSvg(text)
  const size = sizeFromRoot(root)
  return {
    text,
    ...size,
    backgrounds: fullCanvasRects(root, userSize(root)).map((el) => ({ fill: solidFill(el) })),
  }
}

/** 按"长边像素"算输出尺寸：等比缩放，长边正好是给定值 */
export function outputSize(source: SvgSource, longestSide: number): { width: number; height: number } {
  const naturalLongest = Math.max(source.width, source.height) || FALLBACK_SIDE
  const target = Math.min(EXPORT_SIDE_MAX, Math.max(EXPORT_SIDE_MIN, Math.round(longestSide)))
  const ratio = target / naturalLongest
  return {
    width: Math.max(1, Math.round(source.width * ratio)),
    height: Math.max(1, Math.round(source.height * ratio)),
  }
}

/** 默认导出长边：按 2× 出图，夹在 512–2048（小图标放大到看得清，大图不至于导出几十 MB） */
export function suggestedSide(source: SvgSource): number {
  const longest = Math.max(source.width, source.height) || FALLBACK_SIDE
  return Math.min(2048, Math.max(512, Math.round(longest * 2)))
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error('SVG 解码失败'))
    img.src = url
  })
}

/** 按给定长边把 SVG 画成 PNG（透明底）。尺寸由 outputSize 算，调用方只管要多大 */
export async function rasterizeSvg(
  source: SvgSource, longestSide: number, options: RasterOptions = {},
): Promise<Blob> {
  const { width, height } = outputSize(source, longestSide)
  // 每次重新解析：同一份文本要导出好几个尺寸，DOM 不带着上一次改写过的 width/height
  const root = parseSvg(source.text)
  if (options.stripBackground) {
    // 尺寸用源文件的：背景层按它自己的画布铺满，跟本次导出多大无关
    for (const el of fullCanvasRects(root, userSize(root))) el.remove()
  }
  root.setAttribute('width', String(width))
  root.setAttribute('height', String(height))
  const markup = new XMLSerializer().serializeToString(root)

  const svgUrl = URL.createObjectURL(new Blob([markup], { type: 'image/svg+xml' }))
  try {
    const img = await loadImage(svgUrl)
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('浏览器不支持 canvas')
    ctx.drawImage(img, 0, 0, width, height)
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'))
    if (!blob) throw new Error('PNG 编码失败')
    return blob
  } finally {
    URL.revokeObjectURL(svgUrl)
  }
}
