/**
 * 用户主题色应用 — 把 ui_prefs.theme_colors 覆盖到 CSS 变量（:root）
 *
 * 设计（为个性化铺路）：
 * - 主题色存在 ui_prefs.theme_colors（JSONB，后端零改动，走 /user/settings）
 * - key 用 CSS 变量名（如 primary_500 → --tw-primary-500），值 hex
 * - 应用 = documentElement.style.setProperty，运行时覆盖默认主题，无需改 CSS
 * - 与 .dark 类共存：自定义色只在未设置的变量上覆盖；浅/深主题各自存一套？
 *   → 简化：theme_colors 统一覆盖（不分浅深），用户改的就是「主色」，日夜都生效
 */

export type ThemeColorKey =
  | 'primary_400' | 'primary_500' | 'primary_600'
  | 'accent_400' | 'accent_500'
  | 'mint_400' | 'mint_500'
  | 'rose_400' | 'rose_500'

export const THEME_COLOR_KEYS: ThemeColorKey[] = [
  'primary_400', 'primary_500', 'primary_600',
  'accent_400', 'accent_500',
  'mint_400', 'mint_500',
  'rose_400', 'rose_500',
]

/** ui_prefs 存储键 */
export const THEME_COLORS_PREF_KEY = 'theme_colors'

/** key → CSS 变量名 */
export function themeVarName(key: ThemeColorKey): string {
  return '--tw-' + key.replace(/_/g, '-')
}

/** hex (#RRGGBB) → "r g b" 三元组（CSS 变量格式） */
export function hexToRgbTriplet(hex: string): string {
  const h = hex.replace('#', '').trim()
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return ''
  return (
    parseInt(h.slice(0, 2), 16) + ' ' +
    parseInt(h.slice(2, 4), 16) + ' ' +
    parseInt(h.slice(4, 6), 16)
  )
}

/** rgb 三元组 → hex */
export function rgbTripletToHex(rgb: string): string {
  const parts = rgb.trim().split(/\s+/).map(Number)
  if (parts.length < 3 || parts.some(n => !Number.isFinite(n))) return ''
  return '#' + parts.slice(0, 3).map(n => {
    const c = Math.max(0, Math.min(255, Math.round(n))).toString(16)
    return c.length === 1 ? '0' + c : c
  }).join('').toUpperCase()
}

/** 当前生效的主题色（含默认值兜底） */
export function getEffectiveThemeColors(): Record<ThemeColorKey, string> {
  const out = {} as Record<ThemeColorKey, string>
  const root = getComputedStyle(document.documentElement)
  for (const key of THEME_COLOR_KEYS) {
    const val = root.getPropertyValue(themeVarName(key)).trim()
    out[key] = val ? rgbTripletToHex(val) || '' : ''
  }
  return out
}

/** 应用用户主题色到 :root（空值跳过 = 用默认） */
export function applyUserTheme(themeColors: Record<string, string> | null | undefined) {
  const root = document.documentElement
  // 先清掉上次应用的（否则用户「恢复默认」后旧色残留）
  for (const key of THEME_COLOR_KEYS) {
    root.style.removeProperty(themeVarName(key))
  }
  if (!themeColors) return
  for (const key of THEME_COLOR_KEYS) {
    const hex = themeColors[key]
    if (!hex) continue
    const triplet = hexToRgbTriplet(hex)
    if (!triplet) continue
    root.style.setProperty(themeVarName(key), triplet)
  }
}
