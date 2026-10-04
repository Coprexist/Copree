/** Token 数量格式化（语言感知，三级分档）。
 *  - 英文 (en)：<1000 原值，≥1K 用 K，≥1M 用 M
 *  - 中文 (zh)：<10000 原值，≥1万 用 万，≥1亿 用 亿
 */
export function fmtTokenNum(n: number, lang?: string): string {
  if (lang === 'en') {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
    if (n >= 1_000) return `${(n / 1_000).toFixed(2)}K`
    return (n || 0).toLocaleString()
  }
  if (n >= 100_000_000) return `${(n / 100_000_000).toFixed(2)}亿`
  if (n >= 10_000) return `${(n / 10_000).toFixed(2)}万`
  return (n || 0).toLocaleString()
}

/** prompt cache 命中率（%）。分母是 prompt_tokens：cached_tokens 是它的子集，
 *  用 total_tokens 当分母会把 completion 也算进「本该命中的量」，与后端 cache_stats 同一口径。 */
export function cacheHitRatePct(promptTokens: number, cachedTokens: number): number {
  if (!promptTokens) return 0
  return Math.round(((cachedTokens || 0) / promptTokens) * 1000) / 10
}

/** 文件大小格式化（纯函数） */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
}
