/**
 * 硬编码文案闸的唯一判据来源：哪些位置算「用户可见文案」，哪些文本算「语言无关」。
 *
 * 判据只留一份：脚本、文档、将来的运行时兜底都从这里取，
 * 免得第二处「差不多」的实现慢慢分叉（i18n 字典解析已经分叉过一次）。
 */

/** 承载用户可见文案的 JSX 属性。白名单：只审这些，其余属性默认不审。 */
export const TEXT_ATTRS = new Set([
  'title',
  'placeholder',
  'label',
  'hint',
  'description',
  'subtitle',
  'alt',
  'tooltip',
  'emptyText',
  'aria-label',
  'aria-description',
  'aria-placeholder',
])

/** 就近声明豁免的标记，写法 `// i18n-exempt: 理由`；理由必填，且该行必须真的命中问题 */
export const EXEMPT_MARKER = 'i18n-exempt'

/** 豁免理由的最小长度：短到「不重要」这种就不算理由 */
export const MIN_REASON_LENGTH = 4

/** 品牌 / 产品名：三语都长一样，进字典只会让同一条文案维护三份 */
const NEUTRAL_WORDS = new Set([
  'Copree',
  'DSH',
  'GitHub',
  'QQ',
  'WeChat',
  'OpenAI',
  'Claude',
  'DeepSeek',
  'Gemini',
  'JavaScript',
  'TypeScript',
  'Python',
  'PostgreSQL',
  'Redis',
  'Docker',
])

const CJK = /[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]/
const WORD = /[A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]/

const PATTERNS = {
  url: /^(?:https?:\/\/|www\.)\S+$/,
  email: /^\S+@\S+\.\S+$/,
  color: /^#[0-9a-fA-F]{3,8}$/,
  version: /^v?\d+(?:\.\d+)+(?:[-+][\w.]+)?$/,
  numberUnit: /^\d+(?:\.\d+)?\s?(?:ms|s|min|h|d|px|em|rem|%|B|KB|MB|GB|TB)$/,
  unitWord: /^(?:ms|s|min|h|d|px|em|rem|vh|vw|%)$/i,
  timeFormat: /^[yMdHhmsS\s/:.+-]+$/,
  placeholder: /^(?:\{\{?\s*\w+\s*\}?\}|%[sd%])$/,
  entity: /^&[a-z]+;$/,
  upper: /^(?=.*[A-Z]{2})[A-Z0-9][A-Z0-9\s._+/-]*$/,
  pathLike: /^[A-Za-z0-9_./~@-]+$/,
}

export function normalizeText(raw) {
  return raw.replace(/\s+/g, ' ').trim()
}

/**
 * 这段文本是否「语言无关」——三语渲染出来一模一样，进字典只是三份重复维护。
 *
 * 判据顺序刻意写成「先排除有词，再逐类匹配格式」，新加规则永远插在格式段，
 * 不会误伤「含汉字就一定是文案」这条底线。
 */
export function isLanguageNeutral(raw) {
  const s = normalizeText(raw)
  if (!s) return true
  if (NEUTRAL_WORDS.has(s)) return true
  // 没有任何「词」：纯数字、标点、符号、空白，没有可翻译的内容
  if (!WORD.test(s)) return true
  // 含中日韩文字：一定是需要翻译的文案
  if (CJK.test(s)) return false
  if (PATTERNS.url.test(s)) return true
  if (PATTERNS.email.test(s)) return true
  if (PATTERNS.color.test(s)) return true
  if (PATTERNS.version.test(s)) return true
  if (PATTERNS.numberUnit.test(s)) return true
  if (PATTERNS.unitWord.test(s)) return true
  if (PATTERNS.placeholder.test(s)) return true
  if (PATTERNS.entity.test(s)) return true
  // 路径：必须以 / ./ ../ ~/ 开头，或以 / 结尾（worlds/、/api/v1、./a.ts）
  if (PATTERNS.pathLike.test(s) && (/^[./~]/.test(s) || s.endsWith('/'))) return true
  // 纯大写缩写：UTF-8、API、HTTP 404、TTS / LLM
  if (PATTERNS.upper.test(s)) return true
  // 时间 / 日期格式串：yyyy-MM-dd、HH:mm、ms
  if (PATTERNS.timeFormat.test(s) && /[yMdHhmsS]/.test(s)) return true
  return false
}
