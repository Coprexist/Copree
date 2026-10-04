/**
 * 语言配置 — 单一数据源
 * 新增语言只需在此文件添加一条记录，所有组件自动适配。
 */
export type Lang = 'zh' | 'en' | 'ja'

export interface LangMeta {
  code: Lang
  nativeName: string       // 本族语名称，如「中文（简体）」
  i18nKey: string          // t() 的命名空间键，如 'settings:chinese'（无冒号＝common 命名空间）
  locale: string           // toLocaleTimeString 等使用的 locale
  // 相对时间文本（非 i18n 场景使用，如格式化工具函数）
  yesterday: string
  daysAgo: (n: number) => string
  weeksAgo: (n: number) => string
}

export const LANGUAGES: LangMeta[] = [
  {
    code: 'zh',
    nativeName: '中文（简体）',
    i18nKey: 'settings:chinese',
    locale: 'zh-CN',
    yesterday: '昨天',
    daysAgo: (n) => `${n}天前`,
    weeksAgo: (n) => `${n}周前`,
  },
  {
    code: 'en',
    nativeName: 'English',
    i18nKey: 'settings:english',
    locale: 'en-US',
    yesterday: 'Yesterday',
    daysAgo: (n) => `${n} days ago`,
    weeksAgo: (n) => `${n} week${n > 1 ? 's' : ''} ago`,
  },
  {
    code: 'ja',
    nativeName: '日本語',
    i18nKey: 'settings:japanese',
    locale: 'ja-JP',
    yesterday: '昨日',
    daysAgo: (n) => `${n}日前`,
    weeksAgo: (n) => `${n}週間前`,
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
