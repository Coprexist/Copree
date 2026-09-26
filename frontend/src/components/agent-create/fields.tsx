import { useState } from 'react'
import {
  MessageSquare, Microscope, Globe, Battery, Scale, Lock, Landmark, Theater,
  FlaskConical, Leaf, Flame, Shield, User, RefreshCw,
} from 'lucide-react'
import { useT } from '../../i18n/I18nContext'
import Toggle from '../Toggle'
import { AI_TYPES } from './presets'
import type { AiTypeValue, ModelOption, ProviderInfo } from './types'

// ── 图标名称到组件的映射（数据里只存名字，避免把组件塞进常量） ──

const ICON_MAP: Record<string, React.ComponentType<{ size?: number; className?: string }>> = {
  MessageSquare, Microscope, Globe, Battery, Scale, Lock,
  Landmark, Theater, FlaskConical, Leaf, Flame, Shield,
  User, RefreshCw,
}

export function PresetIcon({ name, size }: { name: string; size?: number }) {
  const Icon = ICON_MAP[name]
  if (!Icon) return null
  return <Icon size={size ?? 24} />
}

export function SubIcon({ name, size }: { name: string; size?: number }) {
  const Icon = ICON_MAP[name]
  if (!Icon) return null
  return <Icon size={size ?? 20} />
}

/** API Key 获取链接——从默认供应商取 api_key_url */
export function ApiKeyGetLink({ providers }: { providers: ProviderInfo[] }) {
  const defaultProvider = providers.find(p => p.is_default) || providers[0]
  if (!defaultProvider?.api_key_url) return null
  return (
    <a
      href={defaultProvider.api_key_url}
      target="_blank"
      rel="noopener noreferrer"
      className="text-3xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 underline underline-offset-2 font-normal"
    >
      获取 API Key →
    </a>
  )
}

// ── 模型选项渲染（按供应商分组）──
export function renderModelOptions(models: ModelOption[], providers: ProviderInfo[]) {
  if (providers.length > 0) {
    return providers.map(p => (
      <optgroup key={p.name} label={`${p.name}${p.is_default ? '（默认）' : ''}`}>
        {p.models.map(m => (
          <option key={m.value} value={m.value}>{m.label}</option>
        ))}
      </optgroup>
    ))
  }
  // 无供应商数据时回退到扁平列表
  return models.map(m => <option key={m.value} value={m.value}>{m.label}</option>)
}

// ── AI 类型三选一（主弹窗与详细设置共用） ──

export function AiTypeSelector({ value, onChange }: { value: AiTypeValue; onChange: (v: AiTypeValue) => void }) {
  const t = useT()
  return (
    <div className="grid grid-cols-3 gap-2">
      {AI_TYPES.map((type) => (
        <button
          key={type.value}
          type="button"
          onClick={() => onChange(type.value)}
          className={`flex flex-col items-center gap-1 p-2.5 rounded-card border text-center transition-all ${
            value === type.value
              ? 'border-primary-400 bg-primary-500/10 text-primary-600 dark:text-primary-300'
              : 'border-border bg-canvas text-textSecondary hover:bg-elevated'
          }`}
        >
          <span className="text-lg"><SubIcon name={type.icon} /></span>
          <span className="text-xs font-semibold">{t(type.labelKey)}</span>
          <span className="text-[9px] leading-tight text-textMuted">{t(type.descKey)}</span>
        </button>
      ))}
    </div>
  )
}

// ── 三态下拉：继承（null）/ 开 / 关 ──

export function TristateSelect({ value, onChange }: { value: boolean | null; onChange: (v: boolean | null) => void }) {
  const t = useT()
  return (
    <select
      value={value === null ? 'inherit' : value ? 'on' : 'off'}
      onChange={(e) => { const v = e.target.value; onChange(v === 'inherit' ? null : v === 'on') }}
      className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
    >
      <option value="inherit">{t('modal.detailSettingsInheritGlobal')}</option>
      <option value="on">{t('common.enabled')}</option>
      <option value="off">{t('common.disabled')}</option>
    </select>
  )
}

// ── 分区容器 ──

export function Section({ title, desc, children }: { title: string; desc: string; children: React.ReactNode }) {
  return (
    <div className="bg-canvas/50 rounded-card p-4 border border-border/50">
      <h3 className="text-xs font-semibold text-textPrimary mb-1">{title}</h3>
      <p className="text-3xs text-textMuted mb-3 leading-relaxed">{desc}</p>
      <div className="space-y-2.5">{children}</div>
    </div>
  )
}

// ── 滑块 ──

export function SliderField({
  label, value, setValue, min, max, step, desc,
}: {
  label: string; value: number; setValue: (v: number) => void
  min: number; max: number; step: number; desc?: string
}) {
  return (
    <div>
      <div className="flex justify-between mb-1">
        <label className="text-xs text-textSecondary">{label}</label>
        <span className="text-xs font-mono text-textPrimary">{value}</span>
      </div>
      <input
        type="range" min={min} max={max} step={step}
        value={value}
        onChange={(e) => setValue(parseFloat(e.target.value))}
        className="w-full"
      />
      {desc && <p className="text-3xs text-textMuted mt-0.5">{desc}</p>}
    </div>
  )
}

// ── 数字输入 ──

export function NumberField({
  label, value, setValue, min, max, desc,
}: {
  label: string; value: number; setValue: (v: number) => void
  min: number; max: number; desc?: string
}) {
  // 输入过程留一份草稿：清空时要显示空，而不是立刻跳回 min；0 是合法数字，不能被当成"没填"
  const [draft, setDraft] = useState<string | null>(null)
  return (
    <div>
      <label className="block text-xs text-textSecondary mb-1">{label}</label>
      <input
        type="number" min={min} max={max}
        value={draft ?? String(value)}
        onChange={(e) => {
          const raw = e.target.value
          setDraft(raw)
          const n = Number(raw)
          if (raw !== '' && Number.isFinite(n)) setValue(n)
        }}
        onBlur={() => {
          const n = Number(draft)
          if (draft !== null && draft !== '' && Number.isFinite(n)) {
            setValue(Math.min(max, Math.max(min, n)))
          }
          setDraft(null)
        }}
        className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
      />
      {desc && <p className="text-3xs text-textMuted mt-0.5">{desc}</p>}
    </div>
  )
}

// ── 开关 ──

export function ToggleField({
  label, value, setValue, desc,
}: {
  label: string; value: boolean; setValue: (v: boolean) => void; desc?: string
}) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex-1 min-w-0">
        <span className="text-xs text-textSecondary">{label}</span>
        {desc && <p className="text-3xs text-textMuted mt-0.5">{desc}</p>}
      </div>
      <Toggle checked={value} onChange={setValue} />
    </div>
  )
}
