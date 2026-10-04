import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import { useT } from '../i18n/I18nContext'
import { type PluginView } from '../utils/skin'
import { Palette, Check, CircleSlash } from 'lucide-react'

/**
 * 皮肤选择 — 设置页「皮肤」区块（统一插件系统 2026-08-17）
 * 交互：卡片式选择——点哪张卡片就用哪套，点「默认」恢复平台默认配色。
 * - 「默认」是一张固定卡片（无皮肤生效时选中）
 * - 皮肤卡片：点击即启用（POST /plugins/{id}/pref），互斥由后端保证（新的一套自动停旧的）
 * - 皮肤变量最后应用，覆盖用户自选色；选「默认」后自选色自然恢复
 */

// 预览用主色 key（light 套）
const PREVIEW_KEYS = ['primary_500', 'accent_500', 'mint_500', 'rose_500', 'bubble'] as const

// 「默认」卡片的色板（平台默认主题，与 index.css :root 一致）
const DEFAULT_SWATCHES: Record<string, string> = {
  primary_500: '#8B5CF6',
  accent_500: '#B45309',
  mint_500: '#34D399',
  rose_500: '#E84A69',
  bubble: '#8B5CF6',
}

export default function SkinPicker() {
  const t = useT()
  const [skins, setSkins] = useState<PluginView[]>([])
  const [loading, setLoading] = useState(true)
  const [applying, setApplying] = useState<string | null>(null) // 正在应用的目标 id（'default' = 默认卡片）
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const res = await api.safe.get<{ plugins: PluginView[] }>('/plugins')
      if (res.ok) {
        setSkins((res.value?.plugins || []).filter((p) => p.category === 'skin'))
        setError('')
      } else {
        setError(String(res.error?.message || res.error))
      }
    } catch (e: any) {
      setError(e?.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  /** 选择默认：停用当前所有已启用/生效的皮肤 → 恢复默认配色 */
  const selectDefault = async () => {
    setApplying('default')
    try {
      const res = await api.safe.get<{ plugins: PluginView[] }>('/plugins')
      const all = (res.ok ? res.value.plugins || [] : []).filter((p) => p.category === 'skin')
      for (const skin of all) {
        if (skin.user_enabled || skin.effective) {
          await api.post(`/plugins/${skin.id}/pref`, { enabled: false })
        }
      }
      await load()
      window.dispatchEvent(new CustomEvent('plugins-changed'))
    } catch (e: any) {
      setError(e?.message || '操作失败')
    } finally {
      setApplying(null)
    }
  }

  /** 选择某套皮肤：启用它（互斥由后端保证，其余皮肤自动停用） */
  const selectSkin = async (skin: PluginView) => {
    if (skin.effective || !skin.global_enabled) return
    setApplying(skin.id)
    try {
      await api.post(`/plugins/${skin.id}/pref`, { enabled: true })
      await load()
      window.dispatchEvent(new CustomEvent('plugins-changed'))
    } catch (e: any) {
      setError(e?.message || '操作失败')
    } finally {
      setApplying(null)
    }
  }

  const activeSkin = skins.find((s) => s.effective) || null
  const isDefaultActive = !activeSkin

  /** 渲染一张选择卡片 */
  const renderCard = (opts: {
    id: string
    name: string
    desc: string
    swatches: Record<string, string>
    selected: boolean
    disabled?: boolean
    disabledLabel?: string
    onClick: () => void
    icon?: React.ReactNode
  }) => {
    const busy = applying === opts.id
    return (
      <button
        key={opts.id}
        onClick={opts.onClick}
        disabled={busy || opts.disabled}
        className={`relative w-full text-left rounded-card border p-3 transition-all ${
          opts.selected
            ? 'border-mint-400/60 bg-mint-400/5 ring-1 ring-mint-400/30'
            : 'border-border bg-surface hover:border-primary-400/40 hover:bg-elevated'
        } ${busy ? 'opacity-60' : ''} ${opts.disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
      >
        {opts.selected && (
          <span className="absolute -top-2 left-3 px-2 py-0.5 rounded-full bg-mint-500 text-white text-3xs font-medium flex items-center gap-0.5 shadow">
            <Check size={10} /> {t('settings:skinInUse')}
          </span>
        )}
        {/* 色板预览（light 主色） */}
        <div className="flex items-center gap-1.5 mb-2">
          {PREVIEW_KEYS.map((k) => (
            <span
              key={k}
              className="w-5 h-5 rounded-full ring-1 ring-white/20"
              style={{ background: opts.swatches[k] || '#888' }}
              title={k}
            />
          ))}
          {opts.icon}
        </div>
        <p className="text-sm font-medium text-textPrimary leading-tight">{opts.name}</p>
        <p className="text-2xs text-textMuted mt-0.5 line-clamp-2 min-h-[2em]">{opts.desc}</p>
        {opts.disabled && opts.disabledLabel && (
          <p className="text-3xs text-rose-400 mt-1">{opts.disabledLabel}</p>
        )}
      </button>
    )
  }

  return (
    <div className="mt-4 border-t border-border pt-4">
      <div className="flex items-center justify-between mb-1">
        <p className="text-sm font-medium text-textPrimary flex items-center gap-1.5">
          <Palette size={14} className="text-primary-400" />
          {t('settings:skinTitle')}
        </p>
      </div>
      <p className="text-xs text-textMuted mb-1">{t('settings:skinDesc')}</p>
      <p className="text-2xs text-accent-500 mb-3">{t('settings:skinMutualExclusive')}</p>

      {loading ? (
        <p className="text-xs text-textMuted">{t('settings:loadingPlugins')}</p>
      ) : error ? (
        <p className="text-xs text-rose-400">{error}</p>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          {/* 默认卡片 */}
          {renderCard({
            id: 'default',
            name: t('settings:skinDefault'),
            desc: t('settings:skinDefaultDesc'),
            swatches: DEFAULT_SWATCHES,
            selected: isDefaultActive,
            onClick: selectDefault,
            icon: <CircleSlash size={14} className="text-textMuted ml-auto" />,
          })}
          {/* 皮肤卡片 */}
          {skins.map((skin) =>
            renderCard({
              id: skin.id,
              name: skin.name,
              desc: skin.description,
              swatches: skin.skin_vars?.light || {},
              selected: skin.effective,
              disabled: !skin.global_enabled,
              disabledLabel: !skin.global_enabled ? t('settings:skinAdminOff') : undefined,
              onClick: () => selectSkin(skin),
            }),
          )}
        </div>
      )}
    </div>
  )
}
