import { useState, useEffect } from 'react'
import { useAuth } from '../context/AuthContext'
import { api } from '../api/client'
import { useT } from '../i18n/I18nContext'
import { RotateCcw, Check, Plus, Trash2, Smartphone } from 'lucide-react'
import type { PluginView } from '../utils/skin'
import {
  THEME_COLOR_KEYS, THEME_COLORS_PREF_KEY, applyUserTheme,
  getEffectiveThemeColors, themeVarName, hexToRgbTriplet,
  type ThemeColorKey,
} from '../utils/userTheme'

/**
 * 主题设计工具 — 用户自定义主色（2026-08-17 升级：选色页体验产品化）
 * - 左侧手机壳实时预览：引用 CSS 变量，点选/输入色值立即变化
 * - 每字段：候选色板 + 原生取色器 + 自由 hex 输入
 * - 预设：内置（平台默认定稿版 / 极光青碧）+ 用户自存（ui_prefs.theme_presets）
 * - 保存 → ui_prefs.theme_colors（现有机制），即时生效；皮肤生效时自动停用以让自定义色可见
 */

// 我的预设存储键（ui_prefs JSONB，后端零改动）
const THEME_PRESETS_PREF_KEY = 'theme_presets'

// 每个 key 的候选色板（与默认主题协调的色系）
const CANDIDATES: Record<ThemeColorKey, { hex: string; label: string }[]> = {
  primary_400: [
    { hex: '#8B5CF6', label: 'violet-500' },
    { hex: '#7C3AED', label: 'violet-600' },
    { hex: '#A78BFA', label: 'violet-400' },
    { hex: '#9B7BFA', label: '亮紫' },
    { hex: '#7E69EA', label: '柔和紫' },
    { hex: '#6D28D9', label: '当前' },
    { hex: '#6366F1', label: 'indigo-500' },
  ],
  primary_500: [
    { hex: '#8B5CF6', label: '当前' },
    { hex: '#7C3AED', label: 'violet-600' },
    { hex: '#A78BFA', label: '更亮' },
    { hex: '#9B7BFA', label: '明亮紫' },
    { hex: '#7E69EA', label: '柔和' },
    { hex: '#6D28D9', label: '深紫' },
    { hex: '#6366F1', label: 'indigo-500' },
  ],
  primary_600: [
    { hex: '#6D28D9', label: '当前' },
    { hex: '#7C3AED', label: 'violet-600' },
    { hex: '#8B5CF6', label: '浅一档' },
    { hex: '#5B21B6', label: 'violet-800' },
  ],
  accent_400: [
    { hex: '#F59E0B', label: 'amber-500' },
    { hex: '#FBBF24', label: '当前' },
    { hex: '#F0A020', label: '暖金' },
    { hex: '#E8972C', label: '柔和金' },
    { hex: '#D97706', label: 'amber-600' },
    { hex: '#FACC15', label: '鲜黄' },
  ],
  accent_500: [
    { hex: '#B45309', label: '当前' },
    { hex: '#D97706', label: 'amber-600' },
    { hex: '#F59E0B', label: 'amber-500' },
    { hex: '#E8972C', label: '柔和' },
    { hex: '#C2710A', label: '深金' },
  ],
  mint_400: [
    { hex: '#22C55E', label: 'green-500' },
    { hex: '#10B981', label: '当前' },
    { hex: '#34D399', label: 'emerald-400' },
    { hex: '#14B8A6', label: 'teal-500' },
    { hex: '#059669', label: 'emerald-600' },
    { hex: '#2DD4BF', label: 'teal-400' },
  ],
  mint_500: [
    { hex: '#34D399', label: '当前' },
    { hex: '#059669', label: 'emerald-600' },
    { hex: '#10B981', label: 'emerald-500' },
    { hex: '#047857', label: 'emerald-700' },
    { hex: '#0D9488', label: 'teal-600' },
  ],
  rose_400: [
    { hex: '#C9364F', label: '当前' },
    { hex: '#E84A69', label: '亮玫红' },
    { hex: '#E11D48', label: 'rose-600' },
    { hex: '#DC2626', label: 'red-600' },
    { hex: '#F26D8D', label: '亮粉' },
    { hex: '#A8283F', label: '深玫红' },
  ],
  rose_500: [
    { hex: '#E84A69', label: '当前' },
    { hex: '#E11D48', label: 'rose-600' },
    { hex: '#BE123C', label: 'rose-700' },
    { hex: '#A8283F', label: '暗玫红' },
    { hex: '#DC2626', label: 'red-600' },
    { hex: '#C9364F', label: '玫红' },
  ],
}

const KEY_LABELS: Record<ThemeColorKey, string> = {
  primary_400: '主色 · 强调文字/图标',
  primary_500: '主色 · 按钮背景',
  primary_600: '主色 · 按钮 hover',
  accent_400: '琥珀金 · 状态/徽章',
  accent_500: '琥珀金 · 实底按钮',
  mint_400: '活跃绿 · 在线/成功',
  mint_500: '活跃绿 · 实底按钮',
  rose_400: '危险玫红 · 文字',
  rose_500: '危险玫红 · 按钮',
}

// 内置预设（light 套；平台默认 = 第一轮投票定稿版）
const BUILTIN_PRESETS: { id: string; nameKey: string; colors: Record<ThemeColorKey, string> }[] = [
  {
    id: 'platform-default',
    nameKey: 'settings:themePresetPlatform',
    colors: {
      primary_400: '#6D28D9', primary_500: '#8B5CF6', primary_600: '#6D28D9',
      accent_400: '#FBBF24', accent_500: '#B45309',
      mint_400: '#10B981', mint_500: '#34D399',
      rose_400: '#C9364F', rose_500: '#E84A69',
    },
  },
  {
    id: 'aurora',
    nameKey: 'settings:themePresetAurora',
    colors: {
      primary_400: '#34D399', primary_500: '#10B981', primary_600: '#059669',
      accent_400: '#FBBF24', accent_500: '#D97706',
      mint_400: '#22C55E', mint_500: '#16A34A',
      rose_400: '#F87171', rose_500: '#EF4444',
    },
  },
]

const PREVIEW_KEYS = ['primary_500', 'accent_400', 'mint_400', 'rose_400'] as const

/** 手机壳实时预览：全部引用 CSS 变量，选色即时反映（结构色固定浅色） */
function PhonePreview() {
  const S = {
    surface: '#ffffff',
    border: 'rgb(203 213 225)',
    canvas: 'rgb(248 250 252)',
    ink: 'rgb(17 24 39)',
    muted: 'rgb(107 114 128)',
  }
  return (
    <div
      className="w-full max-w-[300px] mx-auto rounded-dialog overflow-hidden select-none"
      style={{ background: S.surface, border: `1px solid ${S.border}`, boxShadow: '0 8px 30px rgba(0,0,0,0.08)' }}
    >
      {/* 顶栏 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', borderBottom: `1px solid ${S.border}` }}>
        <div style={{ width: 28, height: 28, borderRadius: '50%', background: 'rgb(var(--tw-primary-500))', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700, flex: 'none' }}>AI</div>
        <div className="min-w-0">
          <div style={{ fontSize: 13, fontWeight: 600, color: S.ink, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>群视界 · 星陨大陆</div>
          <div style={{ fontSize: 10, color: 'rgb(var(--tw-mint-400))', display: 'flex', alignItems: 'center', gap: 3 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'rgb(var(--tw-mint-400))' }} /> 在线
          </div>
        </div>
      </div>

      {/* 消息区 */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '12px 10px', minHeight: 158 }}>
        <div style={{ maxWidth: '78%', padding: '7px 11px', borderRadius: 12, fontSize: 12, lineHeight: 1.45, alignSelf: 'flex-start', background: S.surface, border: `1px solid ${S.border}`, color: S.ink }}>
          欢迎来到星陨大陆，<span style={{ color: 'rgb(var(--tw-primary-400))', textDecoration: 'underline' }}>点这里</span> 查看规则
        </div>
        <div style={{ maxWidth: '78%', padding: '7px 11px', borderRadius: 12, fontSize: 12, lineHeight: 1.45, alignSelf: 'flex-end', background: 'rgb(var(--tw-primary-500))', color: '#fff', borderBottomRightRadius: 4 }}>
          探索 幽暗森林 <span style={{ background: 'rgba(255,255,255,0.18)', borderRadius: 4, padding: '0 4px', fontFamily: 'ui-monospace, monospace', fontSize: 11 }}>/探索</span>
        </div>
        <div style={{ maxWidth: '78%', padding: '7px 11px', borderRadius: 12, fontSize: 12, lineHeight: 1.45, alignSelf: 'flex-start', background: S.surface, border: `1px solid ${S.border}`, color: S.ink }}>
          你发现了一只<span style={{ background: 'rgba(0,0,0,0.06)', borderRadius: 4, padding: '0 4px', fontFamily: 'ui-monospace, monospace', fontSize: 11 }}>野狼</span>！
        </div>
      </div>

      {/* 徽章 */}
      <div style={{ display: 'flex', gap: 6, padding: '0 12px 8px', flexWrap: 'wrap' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', height: 20, padding: '0 8px', borderRadius: 999, fontSize: 10, fontWeight: 500, background: 'rgb(var(--tw-primary-500) / 0.12)', color: 'rgb(var(--tw-primary-500))' }}>主色标签</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', height: 20, padding: '0 8px', borderRadius: 999, fontSize: 10, fontWeight: 500, background: 'rgb(var(--tw-accent-400) / 0.15)', color: 'rgb(var(--tw-accent-500))' }}>琥珀状态</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', height: 20, padding: '0 8px', borderRadius: 999, fontSize: 10, fontWeight: 500, background: 'rgb(var(--tw-mint-400) / 0.15)', color: 'rgb(var(--tw-mint-500))' }}>在线徽章</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', height: 20, padding: '0 8px', borderRadius: 999, fontSize: 10, fontWeight: 500, background: 'rgb(var(--tw-rose-400) / 0.12)', color: 'rgb(var(--tw-rose-500))' }}>危险标记</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', height: 20, padding: '0 8px', borderRadius: 999, fontSize: 10, fontWeight: 500, color: '#fff', background: 'rgb(var(--tw-primary-500))' }}>实心主色</span>
      </div>

      {/* 按钮 */}
      <div style={{ display: 'flex', gap: 6, padding: '8px 12px', borderTop: `1px solid ${S.border}`, flexWrap: 'wrap' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', height: 26, padding: '0 12px', borderRadius: 8, fontSize: 11, fontWeight: 500, color: '#fff', background: 'rgb(var(--tw-primary-500))' }}>主按钮</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', height: 26, padding: '0 12px', borderRadius: 8, fontSize: 11, fontWeight: 500, color: S.ink, background: 'rgb(var(--tw-accent-400))' }}>琥珀</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', height: 26, padding: '0 12px', borderRadius: 8, fontSize: 11, fontWeight: 500, color: '#fff', background: 'rgb(var(--tw-mint-500))' }}>成功</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', height: 26, padding: '0 12px', borderRadius: 8, fontSize: 11, fontWeight: 500, color: '#fff', background: 'rgb(var(--tw-rose-500))' }}>删除</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', height: 26, padding: '0 12px', borderRadius: 8, fontSize: 11, fontWeight: 500, color: S.muted, background: S.canvas, border: `1px solid ${S.border}` }}>次级</span>
      </div>

      {/* 输入行 */}
      <div style={{ display: 'flex', gap: 6, padding: '8px 12px 10px', borderTop: `1px solid ${S.border}` }}>
        <div style={{ flex: 1, height: 30, borderRadius: 9, border: `1px solid ${S.border}`, background: S.canvas, padding: '0 10px', fontSize: 11, color: S.muted, display: 'flex', alignItems: 'center' }}>输入指令…</div>
        <div style={{ width: 30, height: 30, borderRadius: 9, background: 'rgb(var(--tw-primary-500))', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, flex: 'none' }}>➤</div>
      </div>
    </div>
  )
}

export default function ThemeCustomizer() {
  const t = useT()
  const { user, refreshUser } = useAuth()
  const [colors, setColors] = useState<Partial<Record<ThemeColorKey, string>>>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [myPresets, setMyPresets] = useState<Record<string, Record<ThemeColorKey, string>>>({})
  const [presetInput, setPresetInput] = useState(false)
  const [presetName, setPresetName] = useState('')
  const [presetMsg, setPresetMsg] = useState<{ ok: boolean; text: string } | null>(null)

  // 加载：默认 = 当前生效值；用户已存 = 覆盖；我的预设从 ui_prefs 读
  useEffect(() => {
    const eff = getEffectiveThemeColors()
    const savedColors = (user?.ui_prefs?.[THEME_COLORS_PREF_KEY] as Record<string, string> | undefined) || {}
    const merged = {} as Partial<Record<ThemeColorKey, string>>
    for (const key of THEME_COLOR_KEYS) {
      merged[key] = savedColors[key] || eff[key] || ''
    }
    setColors(merged)
    setMyPresets((user?.ui_prefs?.[THEME_PRESETS_PREF_KEY] as Record<string, Record<ThemeColorKey, string>> | undefined) || {})
    setDirty(false)
    setPresetMsg(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id])

  const hexValid = (hex: string) => /^#[0-9a-fA-F]{6}$/.test(hex)

  const pick = (key: ThemeColorKey, hex: string) => {
    setColors(prev => ({ ...prev, [key]: hex }))
    setDirty(true)
    // 即时预览：临时覆盖该变量（合法值才覆盖，非法仅更新文本）
    if (hexValid(hex)) {
      document.documentElement.style.setProperty(themeVarName(key), hexToRgbTriplet(hex))
    }
  }

  /** 整套应用（预设点击）：整体覆盖 + 预览 */
  const applyAll = (cols: Partial<Record<ThemeColorKey, string>>) => {
    const next = {} as Partial<Record<ThemeColorKey, string>>
    for (const key of THEME_COLOR_KEYS) {
      const hex = cols[key]
      next[key] = hex || ''
      if (hex && hexValid(hex)) {
        document.documentElement.style.setProperty(themeVarName(key), hexToRgbTriplet(hex))
      }
    }
    setColors(next)
    setDirty(true)
  }

  const save = async () => {
    setSaving(true)
    try {
      // 若当前有生效皮肤：先停用，否则皮肤覆盖自定义色导致"保存了看不到"
      const res = await api.safe.get<{ plugins: PluginView[] }>('/plugins')
      const activeSkins = res.ok ? (res.value.plugins || []).filter((p) => p.category === 'skin' && p.effective) : []
      for (const s of activeSkins) {
        await api.post(`/plugins/${s.id}/pref`, { enabled: false })
      }
      if (activeSkins.length) window.dispatchEvent(new CustomEvent('plugins-changed'))

      const themeColors = {} as Record<string, string>
      for (const key of THEME_COLOR_KEYS) {
        const hex = colors[key]
        if (hex && hexValid(hex)) themeColors[key] = hex
      }
      const ui_prefs = { ...(user?.ui_prefs || {}), [THEME_COLORS_PREF_KEY]: themeColors }
      await api.put('/user/settings', { ui_prefs })
      await refreshUser()  // user 更新 → AuthContext 统一应用链重算
      setSaved(true)
      setDirty(false)
      setPresetMsg(activeSkins.length ? { ok: true, text: t('settings:themeDesignSkinNote') } : null)
      setTimeout(() => setSaved(false), 2000)
    } catch (err: any) {
      console.error('保存主题失败', err)
      setPresetMsg({ ok: false, text: String(err?.message || err) })
    } finally {
      setSaving(false)
    }
  }

  const reset = async () => {
    setSaving(true)
    try {
      const ui_prefs = { ...(user?.ui_prefs || {}) }
      delete ui_prefs[THEME_COLORS_PREF_KEY]
      await api.put('/user/settings', { ui_prefs })
      await refreshUser()
      const eff = getEffectiveThemeColors()
      setColors(eff as Partial<Record<ThemeColorKey, string>>)
      setDirty(false)
      setPresetMsg(null)
    } catch (err: any) {
      console.error('恢复默认失败', err)
    } finally {
      setSaving(false)
    }
  }

  const savePreset = async () => {
    const name = presetName.trim()
    if (!name) return
    const colorsFull = {} as Record<ThemeColorKey, string>
    for (const key of THEME_COLOR_KEYS) {
      const hex = colors[key]
      if (hex && hexValid(hex)) colorsFull[key] = hex
    }
    const next = { ...myPresets, [name]: colorsFull }
    try {
      const ui_prefs = { ...(user?.ui_prefs || {}), [THEME_PRESETS_PREF_KEY]: next }
      await api.put('/user/settings', { ui_prefs })
      await refreshUser()
      setMyPresets(next)
      setPresetInput(false)
      setPresetName('')
      setPresetMsg({ ok: true, text: t('settings:themeDesignPresetSaved') })
    } catch (err: any) {
      setPresetMsg({ ok: false, text: String(err?.message || err) })
    }
  }

  const deletePreset = async (name: string) => {
    const next = { ...myPresets }
    delete next[name]
    try {
      const ui_prefs = { ...(user?.ui_prefs || {}), [THEME_PRESETS_PREF_KEY]: next }
      await api.put('/user/settings', { ui_prefs })
      await refreshUser()
      setMyPresets(next)
      setPresetMsg({ ok: true, text: t('settings:themeDesignPresetDeleted') })
    } catch (err: any) {
      setPresetMsg({ ok: false, text: String(err?.message || err) })
    }
  }

  const presetCards: { id: string; name: string; colors: Record<ThemeColorKey, string>; mine?: boolean }[] = [
    ...BUILTIN_PRESETS.map(p => ({ id: p.id, name: t(p.nameKey), colors: p.colors })),
    ...Object.entries(myPresets).map(([name, cols]) => ({ id: 'mine-' + name, name, colors: cols, mine: true })),
  ]

  return (
    <div className="mt-4 border-t border-border pt-4">
      <div className="flex items-center justify-between mb-1">
        <p className="text-sm font-medium text-textPrimary flex items-center gap-1.5">
          <Smartphone size={14} className="text-primary-400" />
          {t('settings:themeCustom')}
        </p>
        <div className="flex items-center gap-2">
          {dirty && <span className="text-3xs text-accent-500">{t('settings:unsaved')}</span>}
          {saved && <span className="text-3xs text-mint-400 flex items-center gap-0.5"><Check size={11} /> {t('settings:saved')}</span>}
        </div>
      </div>
      <p className="text-xs text-textMuted mb-3">{t('settings:themeCustomDesc')}</p>

      <div className="flex flex-col md:flex-row gap-5">
        {/* 左侧：实时预览 */}
        <div className="md:w-[300px] flex-none">
          <p className="text-2xs font-medium text-textSecondary mb-2 flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-mint-400" /> {t('settings:themeDesignPreview')}
          </p>
          <PhonePreview />
        </div>

        {/* 右侧：字段色板 + hex 输入 */}
        <div className="flex-1 min-w-0">
          <div className="space-y-4">
            {THEME_COLOR_KEYS.map((key) => (
              <div key={key}>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-2xs font-medium text-textSecondary">{KEY_LABELS[key]}</span>
                  <span className="text-3xs font-mono text-textMuted">{(colors[key] || '').toUpperCase()}</span>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  {CANDIDATES[key].map((c) => {
                    const selected = (colors[key] || '').toLowerCase() === c.hex.toLowerCase()
                    return (
                      <button
                        key={c.hex}
                        onClick={() => pick(key, c.hex)}
                        title={c.hex + ' · ' + c.label}
                        className={`w-8 h-8 rounded-control transition-transform hover:scale-110 ${selected ? 'ring-2 ring-textPrimary ring-offset-1' : ''}`}
                        style={{ background: c.hex }}
                        aria-label={c.hex}
                      />
                    )
                  })}
                  {/* 原生取色器 */}
                  <label
                    className="w-8 h-8 rounded-control border border-border flex items-center justify-center cursor-pointer hover:bg-elevated"
                    title={t('settings:themeDesignPicker')}
                    style={{ background: 'conic-gradient(#f00,#ff0,#0f0,#0ff,#00f,#f0f,#f00)' }}
                  >
                    <input
                      type="color"
                      className="opacity-0 w-0 h-0"
                      value={hexValid(colors[key] || '') ? (colors[key] as string) : '#000000'}
                      onChange={(e) => pick(key, e.target.value)}
                    />
                  </label>
                  {/* 自由 hex 输入 */}
                  <input
                    value={colors[key] || ''}
                    onChange={(e) => pick(key, e.target.value)}
                    placeholder="#RRGGBB"
                    spellCheck={false}
                    className="w-24 h-8 px-2 rounded-control border border-border bg-canvas text-2xs font-mono text-textPrimary outline-none focus:border-primary-400"
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 预设区 */}
      <div className="mt-5">
        <p className="text-2xs font-medium text-textSecondary mb-2">{t('settings:themeDesignPresets')}</p>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          {presetCards.map((p) => (
            <div
              key={p.id}
              className="relative rounded-card border border-border bg-surface p-3 transition-all hover:border-primary-400/40 hover:bg-elevated cursor-pointer"
              onClick={() => applyAll(p.colors)}
              title={t('settings:themeDesignPresetApply')}
            >
              <div className="flex items-center gap-1.5 mb-1.5">
                {PREVIEW_KEYS.map((k) => (
                  <span
                    key={k}
                    className="w-4 h-4 rounded-full ring-1 ring-white/20"
                    style={{ background: p.colors[k] || '#888' }}
                    title={k}
                  />
                ))}
                {p.mine && (
                  <button
                    onClick={(e) => { e.stopPropagation(); deletePreset(p.name) }}
                    title={t('settings:themeDesignDeletePreset')}
                    className="ml-auto w-5 h-5 rounded-control flex items-center justify-center text-textMuted hover:text-rose-500 hover:bg-rose-400/10"
                  >
                    <Trash2 size={11} />
                  </button>
                )}
              </div>
              <p className="text-xs font-medium text-textPrimary leading-tight truncate">{p.name}</p>
            </div>
          ))}

          {/* 存为我的预设 */}
          <div className="rounded-card border border-dashed border-border bg-canvas p-3 flex flex-col justify-center min-h-[64px]">
            {presetInput ? (
              <div className="flex gap-1.5">
                <input
                  value={presetName}
                  onChange={(e) => setPresetName(e.target.value)}
                  placeholder={t('settings:themeDesignPresetName')}
                  maxLength={20}
                  autoFocus
                  className="flex-1 min-w-0 h-8 px-2 rounded-control border border-border bg-surface text-2xs text-textPrimary outline-none focus:border-primary-400"
                />
                <button
                  onClick={savePreset}
                  disabled={!presetName.trim()}
                  className="btn btn-xs btn-primary h-8 text-2xs"
                >
                  <Check size={12} />
                </button>
              </div>
            ) : (
              <button
                onClick={() => setPresetInput(true)}
                className="flex items-center justify-center gap-1 text-2xs text-textSecondary hover:text-primary-400 py-1"
              >
                <Plus size={12} /> {t('settings:themeDesignSaveAsPreset')}
              </button>
            )}
          </div>
        </div>
        {presetMsg && (
          <p className={`text-3xs mt-2 ${presetMsg.ok ? 'text-mint-400' : 'text-rose-400'}`}>{presetMsg.text}</p>
        )}
        {!presetInput && Object.keys(myPresets).length === 0 && (
          <p className="text-3xs text-textMuted mt-2">{t('settings:themeDesignPresetEmpty')}</p>
        )}
      </div>

      <div className="flex gap-2 mt-4">
        <button onClick={save} disabled={saving || !dirty} className="btn btn-md btn-primary disabled:opacity-40">
          {saving ? '…' : t('settings:saveTheme')}
        </button>
        <button onClick={reset} disabled={saving} className="btn btn-md btn-secondary">
          <RotateCcw size={14} /> {t('settings:resetTheme')}
        </button>
      </div>
    </div>
  )
}
