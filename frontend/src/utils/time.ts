import { type Lang, getLangMeta } from '../i18n/languages'

/**
 * 解析后端时间字符串（后端 DateTime 列无 timezone=True，Pydantic 序列化为 naive UTC）。
 * 对无时区标记的字符串追加 'Z'，避免 JavaScript 将其误判为本地时间。
 */
export function parseServerDate(dateStr: string): Date {
  const hasTimezone = /[+\-Zz]\d{2}:\d{2}$/.test(dateStr) || /Z$/i.test(dateStr)
  return new Date(hasTimezone ? dateStr : dateStr + 'Z')
}

/** 从原始字符串提取 HH:MM（不受时区影响），返回 [小时, 分钟] */
function parseSourceTime(dateStr: string): [number, number] {
  const m = dateStr.match(/(\d{1,2}):(\d{2})/)
  if (!m) return [0, 0]
  return [parseInt(m[1]), parseInt(m[2])]
}

/** 用"昨天/今天"前缀显示原始时间，不经过 Date.toLocaleTimeString（避免时区偏移） */
function fmtSourceHM(h: number, m: number): string {
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

/**
 * 计算两个 Date 对象之间的日历天数差（基于本地时区的年/月/日）。
 * 不受经过小时数影响——昨天 23:59 到今早 00:01 算 1 天。
 */
function calendarDayDiff(a: Date, b: Date): number {
  const da = new Date(a.getFullYear(), a.getMonth(), a.getDate())
  const db = new Date(b.getFullYear(), b.getMonth(), b.getDate())
  return Math.round((da.getTime() - db.getTime()) / (1000 * 60 * 60 * 24))
}

/**
 * 相对时间格式化（侧边栏等列表用，精简版）
 * - 今天 → HH:MM
 * - 昨天 → "昨天" / "Yesterday" + HH:MM
 * - 2-6 天前 → "X天前" / "X days ago"
 * - 1-4 周前 → "X周前" / "X weeks ago"
 * - 1-11 月前 → "X月前" / "X months ago"
 * - 1+ 年前 → "X年前" / "X years ago"
 *
 * 自动处理时区回滚：后端 naive datetime 被当作 UTC 解析后，如果原始时间
 * HH:MM 在数值上大于当前本地时间，说明 UTC 日期向前滚了一天，降级为"昨天"。
 */
export function formatRelativeTime(
  dateStr: string | null | undefined,
  lang: Lang = 'zh'
): string {
  if (!dateStr) return ''

  const date = parseServerDate(dateStr)
  if (isNaN(date.getTime())) return ''

  const now = new Date()
  const diffDays = Math.max(0, calendarDayDiff(now, date))

  const meta = getLangMeta(lang)

  // ── 时区回滚检测 ──
  // 后端 naive datetime 按 UTC 解析后，如果原始 HH:MM 数值 > 当前本地 HH:MM，
  // 说明日期被时区转换滚到了"明天"，降级为昨天。
  const [srcH, srcM] = parseSourceTime(dateStr)
  const localMin = now.getHours() * 60 + now.getMinutes()
  const srcMin = srcH * 60 + srcM
  const rollover = now.getTime() - date.getTime() < 0 && srcMin > localMin

  // 用于显示的时间：回滚时取原始字符串的 HH:MM，否则取转换后的本地时间
  const showTime = rollover
    ? fmtSourceHM(srcH, srcM)
    : date.toLocaleTimeString(meta.locale, { hour: '2-digit', minute: '2-digit', hour12: false })

  // 回滚 → "昨天 HH:MM"
  if (rollover) return `${meta.yesterday} ${showTime}`

  // 今天
  if (diffDays === 0) return showTime

  // 昨天
  if (diffDays === 1) return `${meta.yesterday} ${showTime}`

  // 2-6 天
  if (diffDays >= 2 && diffDays <= 6) return meta.daysAgo(diffDays)

  // 1-4 周
  if (diffDays >= 7 && diffDays <= 28) {
    const weeks = Math.floor(diffDays / 7)
    return meta.weeksAgo(weeks)
  }

  // 1-11 月
  const diffMonths =
    (now.getFullYear() - date.getFullYear()) * 12 +
    (now.getMonth() - date.getMonth())
  if (diffMonths >= 1 && diffMonths <= 11) {
    return lang === 'zh'
      ? `${diffMonths}月前`
      : `${diffMonths} month${diffMonths > 1 ? 's' : ''} ago`
  }

  // 1+ 年
  const diffYears = now.getFullYear() - date.getFullYear()
  if (diffYears >= 1) {
    return lang === 'zh'
      ? `${diffYears}年前`
      : `${diffYears} year${diffYears > 1 ? 's' : ''} ago`
  }

  // 兜底
  const y = date.getFullYear()
  const m = date.getMonth() + 1
  const d = date.getDate()
  return `${y}/${m}/${d}`
}

/**
 * 格式化消息气泡内的时间（完整版，含月/年）。
 * 用户可点击切换相对 ↔ 绝对时间。
 */
export function formatMessageTime(
  dateStr: string | null | undefined,
  lang: Lang = 'zh'
): string {
  if (!dateStr) return ''

  const date = parseServerDate(dateStr)
  if (isNaN(date.getTime())) return ''

  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffDays = Math.max(0, calendarDayDiff(now, date))
  const diffMins = Math.max(0, Math.floor(diffMs / (1000 * 60)))

  const timeStr = date.toLocaleTimeString(lang === 'zh' ? 'zh-CN' : 'en-US', {
    hour: '2-digit', minute: '2-digit',
  })

  // < 1 分钟
  if (diffMins < 1) return lang === 'zh' ? '刚刚' : 'Just now'

  // < 1 小时
  if (diffMins < 60) return lang === 'zh' ? `${diffMins}分钟前` : `${diffMins} min ago`

  // 今天
  if (diffDays === 0) return timeStr

  // 昨天
  if (diffDays === 1) return lang === 'zh' ? `昨天 ${timeStr}` : `Yesterday ${timeStr}`

  // 2-6 天
  if (diffDays >= 2 && diffDays <= 6) {
    return lang === 'zh' ? `${diffDays}天前 ${timeStr}` : `${diffDays} days ago ${timeStr}`
  }

  // 1-4 周
  if (diffDays >= 7 && diffDays <= 28) {
    const weeks = Math.floor(diffDays / 7)
    const w = lang === 'zh' ? `${weeks}周前` : `${weeks} week${weeks > 1 ? 's' : ''} ago`
    return `${w} ${timeStr}`
  }

  // 1-11 月
  const diffMonths =
    (now.getFullYear() - date.getFullYear()) * 12 +
    (now.getMonth() - date.getMonth())
  if (diffMonths >= 1 && diffMonths <= 11) {
    const m = lang === 'zh' ? `${diffMonths}月前` : `${diffMonths} month${diffMonths > 1 ? 's' : ''} ago`
    return `${m} ${timeStr}`
  }

  // 1+ 年
  const diffYears = now.getFullYear() - date.getFullYear()
  if (diffYears >= 1) {
    const y = lang === 'zh' ? `${diffYears}年前` : `${diffYears} year${diffYears > 1 ? 's' : ''} ago`
    return `${y} ${timeStr}`
  }

  // 兜底
  const y = date.getFullYear()
  const mo = date.getMonth() + 1
  const d = date.getDate()
  return `${y}/${mo}/${d} ${timeStr}`
}
