/**
 * 复制文本到剪贴板 —— 全站唯一的实现处。
 *
 * 为什么要包一层：navigator.clipboard 只在**安全上下文**（https / localhost）里存在，
 * 用 http 打开时是 undefined，直接调会抛；这里退回老 API，并统一告诉调用方成没成
 * （界面据此显示「已复制」这类反馈，而不是静默失败）。
 */
export async function copyText(text: string): Promise<boolean> {
  const value = text ?? ''
  if (!value) return false

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value)
      return true
    }
  } catch {
    // 落到下面的兜底
  }

  // 兜底：document.execCommand('copy') 已废弃，但http 场景下它是唯一退路
  try {
    const area = document.createElement('textarea')
    area.value = value
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(area)
    return ok
  } catch {
    return false
  }
}
