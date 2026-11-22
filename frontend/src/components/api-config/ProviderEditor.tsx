/**
 * 编辑「一家厂商」—— 只有这一处能改地址、模型清单、默认 Key。
 *
 * 配置档里不许再放一份厂商字段（2026-11-22 定的）：两处都能改就会各自漂，
 * 以后对不齐也查不出来。配置档那边缺厂商，走这里打开同一个编辑器补。
 *
 * 打开方式有三种，都在这里收敛成"拿到一条 provider 再编辑"：
 *   已有 id          → 直接取
 *   带 preset_key    → 先接入（POST /providers/connect），再编辑
 *   什么都没有       → 空白表单（自建厂商）
 */
import { memo, useCallback, useEffect, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { api, errorText } from '../../api/client'
import { useT } from '../../i18n/I18nContext'
import { Button, IconButton, Input, Modal, SecretInput, Select } from '../ui'
import type { ApiKind, ApiProvider, BuiltinPreset, ModelOption } from './types'

const LAYOUTS = ['anywhere', 'leading', 'none']

const ROW_VALUE_PLACEHOLDER = 'model-id'
const ROW_LABEL_PLACEHOLDER = 'display name'

/**
 * 模型清单的一行。
 *
 * 单独抽出来 + memo：一家厂商的清单常有二三十条，合在一起写时每敲一个字
 * 整张清单（几十个受控 input）都要重渲染一遍，中文输入法下肉眼可见地顿。
 * 这里只比较 index / value / label，回调必须是稳定的（父层用 useCallback + index）。
 */
const OptionRow = memo(function OptionRow({
  index, value, label, valuePlaceholder, labelPlaceholder, onPatch, onRemove,
}: {
  index: number
  value: string
  label: string
  valuePlaceholder: string
  labelPlaceholder: string
  onPatch: (index: number, patch: Partial<ModelOption>) => void
  onRemove: (index: number) => void
}) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_2rem] items-center gap-2">
      <Input fieldSize="sm" placeholder={valuePlaceholder} value={value}
        onChange={(e) => onPatch(index, { value: e.target.value })} />
      <Input fieldSize="sm" placeholder={labelPlaceholder} value={label}
        onChange={(e) => onPatch(index, { label: e.target.value })} />
      <IconButton label={ROW_LABEL_PLACEHOLDER} size="sm" icon={<Trash2 size={14} />}
        onClick={() => onRemove(index)} />
    </div>
  )
})

export default function ProviderEditor({
  open,
  kind,
  providerId,
  presetKey,
  autoFocusKey,
  onClose,
  onSaved,
}: {
  open: boolean
  kind: ApiKind
  providerId?: number
  presetKey?: string
  autoFocusKey?: boolean
  onClose: () => void
  onSaved: (provider: ApiProvider) => void
}) {
  const t = useT()
  const [provider, setProvider] = useState<ApiProvider | null>(null)
  const [name, setName] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [primary, setPrimary] = useState('')
  const [secondary, setSecondary] = useState('')
  const [options, setOptions] = useState<ModelOption[]>([])
  const [thinking, setThinking] = useState(false)
  const [layout, setLayout] = useState('anywhere')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    let alive = true
    setError('')
    setLoading(true)
    const load = async () => {
      let row: ApiProvider | undefined
      if (providerId) {
        const res = await api.get<{ providers: ApiProvider[] }>('/user/api-config/providers?kind=' + kind)
        row = res.providers.find((p) => p.id === providerId)
      }
      if (!alive) return
      if (row) {
        setProvider(row)
        setName(row.name || '')
        setBaseUrl(row.base_url || '')
        setApiKey('')
        setPrimary(row.default_primary || '')
        setSecondary(row.default_secondary || '')
        setOptions(row.options || [])
        setThinking(!!row.thinking_supported)
        setLayout(row.system_layout || 'anywhere')
        return
      }
      if (presetKey) {
        // 从内置清单点进来：**只预填、不落库**。
        // 以前这里直接调 connect，用户只是点开看一眼、点了取消，厂商就已经进他的配置了。
        // 真正的接入放在「保存」里（POST /providers 带 preset_key）。
        const res = await api.get<{ presets: BuiltinPreset[] }>('/user/provider-presets?kind=' + kind)
        const preset = (res.presets || []).find((p) => p.key === presetKey)
        if (!alive || !preset) return
        setProvider(null)
        setName(preset.label || '')
        setBaseUrl(preset.base_url || '')
        setApiKey('')
        setPrimary(preset.chat_model || preset.voice_id || '')
        setSecondary(preset.work_model || '')
        setOptions((preset.models || preset.voices || []) as ModelOption[])
        setThinking(!!preset.thinking_supported)
        setLayout(preset.system_layout || 'anywhere')
      }
    }
    load()
      .catch((e) => { if (alive) setError(errorText(e, t('apiConfig:loadFailed'))) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [open, kind, providerId, presetKey])

  // 稳定引用：OptionRow 是 memo 的，回调每次新建会让所有行都重渲染
  const patchOption = useCallback((index: number, patch: Partial<ModelOption>) => {
    setOptions((prev) => prev.map((x, j) => (j === index ? { ...x, ...patch } : x)))
  }, [])
  const removeOption = useCallback((index: number) => {
    setOptions((prev) => prev.filter((_, j) => j !== index))
  }, [])

  const save = async () => {
    if (!name.trim()) {
      setError(t('apiConfig:nameRequired'))
      return
    }
    setSaving(true)
    setError('')
    const body: Record<string, unknown> = {
      name: name.trim(),
      base_url: baseUrl.trim(),
      default_primary: primary.trim() || null,
      default_secondary: secondary.trim() || null,
      options: options.filter((o) => o.value.trim()),
      thinking_supported: thinking,
      system_layout: layout,
    }
    if (apiKey !== '') body.api_key = apiKey
    try {
      const saved = provider?.id
        ? await api.put<ApiProvider>('/user/api-config/providers/' + provider.id, body)
        : await api.post<ApiProvider>('/user/api-config/providers', {
            ...body,
            kind,
            // 从内置清单点进来的：带上 preset_key，后端按预设补掉没填的字段
            ...(presetKey ? { preset_key: presetKey } : {}),
          })
      onSaved(saved)
      onClose()
    } catch (e) {
      setError(errorText(e, t('apiConfig:loadFailed')))
    } finally {
      setSaving(false)
    }
  }

  const isVoice = kind === 'voice'
  const isNew = !provider?.preset_key

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title={isNew ? t('apiConfig:connectTitle') : t('apiConfig:providerEditTitle')}
      bodyClassName="min-h-[24rem]"
      footer={
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>{t('apiConfig:cancel')}</Button>
          <Button variant="primary" size="sm" loading={saving} onClick={save}>{t('apiConfig:save')}</Button>
        </div>
      }
    >
      {loading ? (
        <p className="text-sm text-textMuted">{t('apiConfig:loading')}</p>
      ) : (
        <div className="space-y-3">
          {error && <p className="text-xs text-rose-400">{error}</p>}

          <Input label={t('apiConfig:providerName')} fieldSize="sm"
            value={name} onChange={(e) => setName(e.target.value)} />

          <Input label={t('apiConfig:providerBaseUrl')} fieldSize="sm" className="font-mono"
            value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />

          <SecretInput label={t('apiConfig:providerKey')} fieldSize="sm"
            hint={provider?.has_key ? t('apiConfig:keyEndsWith', { last4: provider.key_last4 }) : t('apiConfig:providerKeyHint')}
            autoFocus={!!autoFocusKey}
            value={apiKey} onChange={(e) => setApiKey(e.target.value)} />

          <div className="grid gap-3 sm:grid-cols-2">
            <Select
              label={isVoice ? t('apiConfig:providerVoicePrimary') : t('apiConfig:providerPrimary')}
              fieldSize="sm"
              placeholder={t('apiConfig:pickModel')}
              options={options.filter((o) => o.value).map((o) => ({ value: o.value, label: o.label || o.value }))}
              value={primary}
              onChange={(e) => setPrimary(e.target.value)}
            />
            {!isVoice && (
              <Select
                label={t('apiConfig:providerSecondary')}
                fieldSize="sm"
                placeholder={t('apiConfig:pickModel')}
                options={options.filter((o) => o.value).map((o) => ({ value: o.value, label: o.label || o.value }))}
                value={secondary}
                onChange={(e) => setSecondary(e.target.value)}
              />
            )}
          </div>

          <div className="space-y-2">
            <span className="text-3xs text-textMuted">
              {isVoice ? t('apiConfig:providerVoices') : t('apiConfig:providerModels')}
            </span>
            {/* 列头：和数据行共用同一套 grid 轨道，否则 flex 下两个输入框宽度随内容浮动，表头必然错位 */}
            <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_2rem] gap-2">
              <span className="text-3xs text-textMuted">{t('apiConfig:modelValue')}</span>
              <span className="text-3xs text-textMuted">{t('apiConfig:modelLabel')}</span>
              <span />
            </div>
            {options.map((opt, i) => (
              <OptionRow
                key={i}
                index={i}
                value={opt.value}
                label={opt.label}
                valuePlaceholder={t('apiConfig:modelValue')}
                labelPlaceholder={t('apiConfig:modelLabel')}
                onPatch={patchOption}
                onRemove={removeOption}
              />
            ))}
            <Button variant="ghost" size="sm" icon={<Plus size={14} />}
              onClick={() => setOptions((prev) => [...prev, { value: '', label: '' }])}>
              {t('apiConfig:addModel')}
            </Button>
          </div>

          {/* 两列同构：标签在上、控件在下。混用「有 label 的 Select」和「没 label 的 Button」
              会让两个控件基线错开，看起来像没对齐。语音用不上这两项，直接不出现 */}
          {!isVoice && (
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <span className="block text-xs font-medium text-textSecondary">
                  {t('apiConfig:thinkingTitle')}
                </span>
                <Button size="sm" variant="secondary" className={'mt-1.5 ' + (thinking ? 'is-active' : '')}
                  onClick={() => setThinking((v) => !v)}>
                  {thinking ? t('apiConfig:thinkingOn') : t('apiConfig:thinkingOff')}
                </Button>
              </div>
              <div>
                <span className="block text-xs font-medium text-textSecondary">
                  {t('apiConfig:providerSystemLayout')}
                </span>
                <Select fieldSize="sm" className="mt-1.5"
                  options={[
                    { value: 'anywhere', label: t('apiConfig:layoutAnywhere') },
                    { value: 'leading', label: t('apiConfig:layoutLeading') },
                    { value: 'none', label: t('apiConfig:layoutNone') },
                  ]}
                  value={layout} onChange={(e) => setLayout(e.target.value)} />
              </div>
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}