/**
 * Base URL 是否指向本机 / 局域网。
 *
 * 这一栏填的是「模型服务的地址」，但很多人会把自己电脑上跑的中转（localhost:8888）填进来——
 * 服务器访问不到这个地址，AI 就整轮发不出话（2026-10-19 实例：8 条私信全部失败，用户只看到
 * AI 不说话）。这里只做提示、不拦保存：自建服务确实可能就在同一内网，拦下来反而更糟。
 */
export function looksLocalBaseUrl(raw: string): boolean {
  const value = (raw || '').trim().toLowerCase()
  if (!value) return false
  let host = ''
  try {
    host = new URL(value.includes('://') ? value : `http://${value}`).hostname
  } catch {
    return false
  }
  if (!host) return false
  return (
    host === 'localhost' ||
    host === '::1' ||
    host === '[::1]' ||
    host === 'host.docker.internal' ||
    host.endsWith('.localhost') ||
    /^127\./.test(host) ||
    /^10\./.test(host) ||
    /^192\.168\./.test(host) ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(host) ||
    /^169\.254\./.test(host)
  )
}
