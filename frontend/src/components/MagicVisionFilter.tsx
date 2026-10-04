import { useState, useEffect, useCallback } from 'react'
import Toggle from './Toggle'
import { Slider } from './ui'
import { api } from '../api/client'
import {
  WandSparkles, RotateCcw, AlertTriangle,
  Globe, Monitor, Image,
  Eye, Sun, Contrast, FlipHorizontal, FileImage,
  Palette, EyeOff, Droplet, Droplets, ImageDown,
} from 'lucide-react'
import type { MagicVisionPrefs, MagicVisionScope } from '../utils/cssFilters'
import { FILTER_DEFS, defaultPrefs, apply, saveToStorage } from '../utils/cssFilters'

const ICON_MAP: Record<string, any> = {
  blur: Eye, brightness: Sun, contrast: Contrast, 'drop-shadow': FlipHorizontal,
  grayscale: FileImage, 'hue-rotate': Palette, invert: EyeOff,
  opacity: Droplet, saturate: Droplets, sepia: ImageDown,
}

const SCOPE_OPTS: { value: MagicVisionScope; label: string; icon: any }[] = [
  { value: 'ui',     label: '仅对 UI',  icon: Monitor },
  { value: 'images', label: '仅对图片', icon: Image },
  { value: 'all',    label: '全部生效', icon: Globe },
]

interface Props {
  value: MagicVisionPrefs
  onChange: (v: MagicVisionPrefs) => void
}

export default function MagicVisionFilter({ value, onChange }: Props) {
  const [enabled, setEnabled] = useState(value.enabled)
  const [scope, setScope] = useState(value.scope)
  const [filters, setFilters] = useState(value.filters)
  const [warn, setWarn] = useState(false)
  const [saving, setSaving] = useState(false)

  // 同步外部 value 变化
  useEffect(() => { setEnabled(value.enabled); setScope(value.scope); setFilters(value.filters) }, [value])

  // 拖拽时即时注入预览
  useEffect(() => { apply({ enabled, scope, filters }) }, [enabled, scope, filters])

  const toggle = useCallback((id: string, on: boolean) => {
    setFilters(p => ({ ...p, [id]: { ...p[id], enabled: on } }))
    if (on) setWarn(true)
  }, [])

  const slide = useCallback((id: string, val: number) => {
    setFilters(p => ({ ...p, [id]: { ...p[id], value: val } }))
  }, [])

  const reset = useCallback(() => {
    setFilters(defaultPrefs().filters)
    setWarn(false)
  }, [])

  // 持久化：onChange + localStorage + 应用 + 同步后端 ui_prefs
  const persist = useCallback(async (prefs: MagicVisionPrefs) => {
    onChange(prefs)
    saveToStorage(prefs)
    apply(prefs)
    try {
      const me = await api.get('/auth/me')
      const p = (me as any)?.ui_prefs || {}
      p.magic_vision = prefs
      await api.put('/user/settings', { ui_prefs: p })
    } catch {}
  }, [onChange])

  const handleApply = useCallback(async () => {
    setSaving(true)
    const prefs: MagicVisionPrefs = { enabled, scope, filters }
    await persist(prefs)
    setSaving(false)
  }, [enabled, scope, filters, persist])

  // 关闭主开关 → 立即保存关闭状态（面板会隐藏，不能依赖“应用”按钮）
  const handleToggleEnabled = useCallback(async (v: boolean) => {
    setEnabled(v)
    if (!v) {
      setWarn(false)
      const prefs: MagicVisionPrefs = { ...value, enabled: false }
      await persist(prefs)
    }
  }, [value, persist])

  const anyActive = FILTER_DEFS.some(d => filters[d.id]?.enabled && filters[d.id].value !== d.defaultVal)

  return (
    <div className="mt-4 pt-4 border-t border-border">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <WandSparkles size={16} className="text-accent-400" />
          <span className="text-sm font-semibold text-textPrimary">魔视界</span>
          <span className="text-3xs px-1.5 py-0.5 rounded-full bg-accent-500/10 text-accent-500 border border-accent-500/20">BETA</span>
        </div>
        <Toggle checked={enabled} onChange={handleToggleEnabled} />
      </div>

      {!enabled && <p className="text-xs text-textMuted px-1">开启后可为页面叠加 CSS 滤镜效果</p>}

      {enabled && (
        <>
          {warn && (
            <div className="flex items-start gap-2 p-2.5 mb-3 rounded-control bg-accent-500/5 border border-accent-500/15 text-xs text-textSecondary">
              <AlertTriangle size={14} className="shrink-0 mt-0.5 text-accent-500" />
              <span>部分滤镜（如模糊、投影）可能影响页面性能。如遇卡顿请关闭不必要的魔棒。</span>
            </div>
          )}

          {/* 应用对象 */}
          <div className="mb-3">
            <span className="text-2xs font-medium text-textSecondary mb-1.5 block">应用对象</span>
            <div className="flex gap-1.5">
              {SCOPE_OPTS.map(o => {
                const Icon = o.icon
                return (
                  <button key={o.value} onClick={() => setScope(o.value)}
                    className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-control border transition-colors flex-1 ${
                      scope === o.value
                        ? 'border-accent-500/40 bg-accent-500/10 text-accent-500'
                        : 'border-border text-textSecondary hover:text-textPrimary hover:border-accent-500/20'
                    }`}>
                    <Icon size={12} />{o.label}
                  </button>
                )
              })}
            </div>
          </div>

          {/* 魔棒列表 */}
          <div className="space-y-1.5 max-h-[360px] overflow-y-auto pr-1">
            {FILTER_DEFS.map(def => {
              const st = filters[def.id] || { enabled: false, value: def.defaultVal }
              const Icon = ICON_MAP[def.id]
              return (
                <div key={def.id} className={`p-2.5 rounded-card border transition-colors ${
                  st.enabled ? 'border-accent-500/30 bg-accent-500/5' : 'border-border bg-canvas/30'
                }`}>
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-2 min-w-0">
                      {Icon && <Icon size={14} className={`shrink-0 ${st.enabled ? 'text-accent-400' : 'text-textMuted'}`} />}
                      <span className="text-xs font-medium text-textPrimary">{def.label}</span>
                      <code className="text-3xs text-textMuted font-mono">{def.id}</code>
                    </div>
                    <Toggle checked={st.enabled} onChange={(v) => toggle(def.id, v)} />
                  </div>
                  {st.enabled && (
                    <div className="flex items-center gap-3 pl-6">
                      <Slider tone="accent" className="flex-1"
                        min={def.min} max={def.max} step={def.step} value={st.value}
                        onChange={v => slide(def.id, v)} />
                      <span className="text-xs font-mono text-textSecondary tabular-nums w-16 text-right shrink-0">
                        {def.unit === '%' ? `${st.value}%` : def.unit === 'deg' ? `${st.value}°` : def.unit === 'px' ? `${st.value}px` : st.value}
                      </span>
                      {st.value !== def.defaultVal && (
                        <button onClick={() => slide(def.id, def.defaultVal)}
                          className="text-3xs text-textMuted hover:text-textSecondary shrink-0">重置</button>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          {/* 底部按钮 */}
          <div className="flex items-center gap-2 mt-4 pt-3 border-t border-border">
            <button onClick={reset}
              className="btn btn-xs btn-outline gap-1 hover:text-textPrimary">
              <RotateCcw size={12} />重置全部
            </button>
            <div className="flex-1" />
            <button onClick={handleApply} disabled={!anyActive || saving}
              className="flex items-center gap-1 px-4 py-1.5 text-xs rounded-control bg-accent-500 hover:bg-accent-600 text-white font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
              {saving ? '保存中…' : '应用'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
