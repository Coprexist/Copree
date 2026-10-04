/**
 * 语言配置 — 单一数据源
 * 新增语言只需在此文件添加一条记录，所有组件自动适配。
 */
export type Lang = 'zh' | 'en' | 'ja'

export interface LangMeta {
  code: Lang
  nativeName: string       // 本族语名称，如「中文（简体）」
  i18nKey: string          // t() 的命名空间键，如 'settings:chinese'（无冒号＝common 命名空间）
  locale: string           // toLocaleTimeString / toLocaleDateString 等使用的 locale
  // 相对时间文本：**唯一来源**。页面别再写 `lang === 'zh' ? … : …`——
  // 那样日文会掉进英文分支（曾经就这样：列表里「3週間前」和 "2 months ago" 混着显示）
  yesterday: string
  justNow: string
  minutesAgo: (n: number) => string
  daysAgo: (n: number) => string
  weeksAgo: (n: number) => string
  monthsAgo: (n: number) => string
  yearsAgo: (n: number) => string
}

export const LANGUAGES: LangMeta[] = [
  {
    code: 'zh',
    nativeName: '中文（简体）',
    i18nKey: 'settings:chinese',
    locale: 'zh-CN',
    yesterday: '昨天',
    justNow: '刚刚',
    minutesAgo: (n) => `${n}分钟前`,
    daysAgo: (n) => `${n}天前`,
    weeksAgo: (n) => `${n}周前`,
    monthsAgo: (n) => `${n}月前`,
    yearsAgo: (n) => `${n}年前`,
  },
  {
    code: 'en',
    nativeName: 'English',
    i18nKey: 'settings:english',
    locale: 'en-US',
    yesterday: 'Yesterday',
    justNow: 'Just now',
    minutesAgo: (n) => `${n} min ago`,
    daysAgo: (n) => `${n} days ago`,
    weeksAgo: (n) => `${n} week${n > 1 ? 's' : ''} ago`,
    monthsAgo: (n) => `${n} month${n > 1 ? 's' : ''} ago`,
    yearsAgo: (n) => `${n} year${n > 1 ? 's' : ''} ago`,
  },
  {
    code: 'ja',
    nativeName: '日本語',
    i18nKey: 'settings:japanese',
    locale: 'ja-JP',
    yesterday: '昨日',
    justNow: 'たった今',
    minutesAgo: (n) => `${n}分前`,
    daysAgo: (n) => `${n}日前`,
    weeksAgo: (n) => `${n}週間前`,
    monthsAgo: (n) => `${n}ヶ月前`,
    yearsAgo: (n) => `${n}年前`,
  },
]

export const DEFAULT_LANG: Lang = 'en'

/** 运行时校验字符串是否为合法语言代码（null/undefined 安全） */
export function isValidLang(s: string | null | undefined): s is Lang {
  return !!s && LANGUAGES.some(l => l.code === s)
}

/** 根据语言代码获取元数据（未找到返回 en） */
export function getLangMeta(code: string): LangMeta {
  return LANGUAGES.find(l => l.code === code) ?? LANGUAGES[1] // en
}
