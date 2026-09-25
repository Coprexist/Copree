/**
 * DSH 对话页的图片附件 —— 「上传后的附件」与「桥接能过的那点东西」之间的唯一转换层。
 *
 * 为什么不再在前端编码 base64：一张截图就能把 /prompt 的 JSON 正文撑到几 MB，
 * 链路上任何一跳（反向代理默认 1MB 等）都会先把它掐掉 —— 实测丢图就是这么丢的。
 * 现在正文只带一行人读的标记，字节交给 DSH 插件用密钥按 fileId 回取后落盘。
 */
import { getApiBaseUrl } from '../api/client'

export interface DshImageRef {
  file_id: number
  name: string
}

/** 交给桥接的附件引用：DSH 侧按 fileId 回取字节（正文里因此没有 base64） */
export interface DshAttachmentRef {
  fileId: number
  name: string
  mime: string
}

/** 人可读、AI 可读、刷新后仍在的图片标记 */
export function imageMarker(fileId: number, name: string): string {
  return `[图片 #${fileId}: ${name}]`
}

/** 从消息正文里取回图片标记（气泡渲染缩略图用；没有标记就是没有图） */
export function imageRefsIn(text: string): DshImageRef[] {
  const out: DshImageRef[] = []
  const re = /\[图片 #(\d+)(?:: ([^\]]*))?\]/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text || '')) !== null) {
    out.push({ file_id: Number(m[1]), name: m[2] || '' })
  }
  return out
}

/**
 * 展示用：把图片标记从正文里摘掉（缩略图已经表达了它）。
 * 只摘我们自己写的那种标记，插件补的路径行照旧显示——那是"图存哪了"的凭据。
 */
export function stripImageMarkers(text: string): string {
  return (text || '')
    .split('\n')
    .filter((line) => !/^\[图片 #\d+(?:: [^\]]*)?\]$/.test(line.trim()))
    .join('\n')
    .trim()
}

/**
 * 插件是否吃附件引用。用版本号做能力开关：只送标记不会出事，参考旧插件（<0.3）
 * 也没有回取字节的能力，所以宁可退化成只发标记。
 */
export function supportsImageAttachments(version?: string): boolean {
  if (!version) return false
  const [major, minor] = version.split('.').map((n) => Number(n))
  if (!Number.isFinite(major) || !Number.isFinite(minor)) return false
  return major > 0 || minor >= 3
}

/** 附件缩略图地址：与群视界对话同一条下载路径（token 走 query，img 标签带不上 header） */
export function attachmentUrl(fileId: number): string {
  return `${getApiBaseUrl()}/fs/download/${fileId}?token=${localStorage.getItem('access_token') || ''}`
}
