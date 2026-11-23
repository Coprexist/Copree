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
import { Loader2, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { api, errorText } from '../../api/client'
import { useT } from '../../i18n/I18nContext'
import { Button, IconButton, Input, Modal, SecretInput, Select } from '../ui'
import type { ApiKind, ApiProvider, BuiltinPreset, ModelOption } from './types'

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
  // 空串 = 「自动识别」：交给后端按 手选 > 探测 > 预设 > 地址 判一次
  const [layout, setLayout] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [refreshNote, setRefreshNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [reprobing, setReprobing] = useState(false)
  const [layoutNote, setLayoutNote] = useState('')

  useEffect(() => {
    if (!open) return
    let alive = true
    setError('')
    setRefreshNote(null)
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
        // 只有用户自己选过的才回填；探测/预设/猜出来的都显示「自动识别」——
        // 否则一保存就把自动结论固化成手选，之后再也不会重新判定
        setLayout(row.system_layout_source === 'user' ? row.system_layout : '')
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
        setLayout('')   // 从清单点进来：布局交给判定，预设本身就是它的依据之一
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

  /**
   * 从端点拉一次真实模型清单。
   *
   * 不是自动的：模型是用户自己机器的状态，什么时候变了只有他知道（`ollama pull` 之后
   * 点一下即可）。也不覆盖已有的行——手动改过的显示名、手动删掉的条目都留着，
   * 因为这个清单正在支撑已经在跑的 AI，一次刷新不该把它们弄断。
   */
  const refreshModels = async () => {
    if (!baseUrl.trim()) {
      setRefreshNote({ ok: false, text: t('apiConfig:refreshNoBaseUrl') })
      return
    }
    setRefreshing(true)
    setRefreshNote(null)
    try {
      // provider_id 让后端用「这条厂商自己的 Key」；不传就会退化成"无 Key 探测"，
      // 绝不借用用户账号级 Key（Key 不跨厂商借，探测也不例外）
      const data = await api.post<{ ok: boolean; message: string; kind?: string; models?: string[] }>(
        '/user/test-api-connection',
        { api_base_url: baseUrl.trim(), api_key: apiKey || null, provider_id: provider?.id ?? null },
      )
      if (!data.ok) {
        // 内网地址要先"登记"（保存过才允许探）——这是探针的安全策略，不是配置写错了，
        // 所以给一句能照着做的，而不是把后端的拒绝原文摊给用户
        const text = data.kind === 'blocked_private'
          ? t('apiConfig:refreshNeedSaveFirst')
          : (data.message || t('apiConfig:refreshFailed'))
        setRefreshNote({ ok: false, text })
        return
      }
      const found = data.models || []
      if (found.length === 0) {
        setRefreshNote({ ok: false, text: t('apiConfig:refreshEmpty') })
        return
      }
      const known = new Set(options.map((o) => o.value))
      const added = found.filter((id) => !known.has(id)).map((id) => ({ value: id, label: id }))
      if (added.length === 0) {
        setRefreshNote({ ok: true, text: t('apiConfig:refreshUpToDate', { count: found.length }) })
        return
      }
      setOptions((prev) => [...prev, ...added])
      setRefreshNote({ ok: true, text: t('apiConfig:refreshAdded', { count: added.length }) })
    } catch (e) {
      setRefreshNote({ ok: false, text: errorText(e, t('apiConfig:refreshFailed')) })
    } finally {
      setRefreshing(false)
    }
  }

  /**
   * 「重新识别」：让后端读一次对端 chat 模板，并把结论存下来。
   *
   * 只在已保存的厂商上可用——地址没进库时后端不允许探私网（防扫描），所以新建的那一刻
   * 只能靠「按地址猜」；保存之后点这里才是精确结论。改了地址要先保存，按钮会跟着变灰。
   */
  const reprobeLayout = async () => {
    if (!provider?.id || !baseUrl.trim()) return
    setReprobing(true)
    setLayoutNote('')
    try {
      const r = await api.post<{ layout: string; source: string; message: string }>(
        `/user/api-config/providers/${provider.id}/probe-layout`,
      )
      setLayout(r.layout)
      setLayoutNote(r.message || t('apiConfig:reprobeLayout'))
    } catch (e) {
      setLayoutNote(errorText(e, t('apiConfig:reprobeLayout')))
    } finally {
      setReprobing(false)
    }
  }

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
      system_layout: layout || null,
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

          {!isVoice && (
            <div className="space-y-1.5">
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="secondary" size="sm" loading={refreshing} icon={<RefreshCw size={14} />}
                  onClick={refreshModels}>
                  {t('apiConfig:refreshModels')}
                </Button>
                <span className="text-3xs text-textMuted">{t('apiConfig:refreshHint')}</span>
              </div>
              {refreshNote && (
                <p className={'text-3xs ' + (refreshNote.ok ? 'text-emerald-400' : 'text-rose-400')}>
                  {refreshNote.text}
                </p>
              )}
            </div>
          )}

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
                <div className="flex items-center justify-between gap-2">
                  <span className="block text-xs font-medium text-textSecondary">
                    {t('apiConfig:providerSystemLayout')}
                  </span>
                  <Button variant="ghost" size="sm"
                    icon={reprobing ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                    disabled={reprobing || !provider?.id || !baseUrl.trim()
                      || baseUrl.trim() !== provider.base_url}
                    onClick={reprobeLayout}>
                    {t('apiConfig:reprobeLayout')}
                  </Button>
                </div>
                <Select fieldSize="sm" className="mt-1.5"
                  options={[
                    { value: '', label: t('apiConfig:layoutAuto') },
                    { value: 'anywhere', label: t('apiConfig:layoutAnywhere') },
                    { value: 'leading', label: t('apiConfig:layoutLeading') },
                    { value: 'none', label: t('apiConfig:layoutNone') },
                  ]}
                  value={layout} onChange={(e) => setLayout(e.target.value)} />
                {layoutNote && <p className="mt-1 text-3xs text-textMuted">{layoutNote}</p>}
              </div>
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}