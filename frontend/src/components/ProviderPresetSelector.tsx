import { useState, useEffect } from 'react'
import { api } from '../api/client'
import { useT } from '../i18n/I18nContext'
import { Plus, Trash2, Check, Loader2, Settings, Server, Star, Globe, Download, RefreshCw } from 'lucide-react'
import { Badge, Button, Card, EmptyState, IconButton, Select } from './ui'

interface ModelOption { value: string; label: string }

interface Preset {
  key: string
  label: string
  base_url: string
  chat_model: string
  work_model: string
  embedding_model: string
  thinking_supported: boolean
  models: ModelOption[]
  system_layout?: string
  global_default_chat_model?: string
  global_default_work_model?: string

}

interface ProviderItem {
  name: string
  provider: string
  base_url: string
  chat_model: string
  work_model: string
  embedding_model: string
  model_options: ModelOption[]
  thinking_supported: boolean
  is_default: boolean
  system_layout?: string
  system_layout_source?: string
  global_default_chat_model?: string
  global_default_work_model?: string

}

export default function ProviderPresetSelector() {
  const t = useT()
  const [presets, setPresets] = useState<Preset[]>([])
  const [providers, setProviders] = useState<ProviderItem[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  // 展开的 provider 名称（编辑态）
  const [expanded, setExpanded] = useState<string | null>(null)
  // 编辑中的表单数据
  const [editName, setEditName] = useState('')
  const [editProvider, setEditProvider] = useState('')
  const [editBaseUrl, setEditBaseUrl] = useState('')
  const [editChat, setEditChat] = useState('')
  const [editWork, setEditWork] = useState('')
  const [editEmbed, setEditEmbed] = useState('')
  const [editThinking, setEditThinking] = useState(false)
  const [editModels, setEditModels] = useState('')
  const [editIsDefault, setEditIsDefault] = useState(false)
  const [editIndex, setEditIndex] = useState<number | null>(null)
  const [editGlobalChatModel, setEditGlobalChatModel] = useState('')
  const [editGlobalWorkModel, setEditGlobalWorkModel] = useState('')
  // 系统消息格式：值 + 来源（user | detected | preset）+ 一次「重新识别」的临时提示
  const [editLayout, setEditLayout] = useState('anywhere')
  const [editLayoutSource, setEditLayoutSource] = useState('preset')
  const [editLayoutTouched, setEditLayoutTouched] = useState(false)
  const [layoutNote, setLayoutNote] = useState('')
  const [reprobing, setReprobing] = useState(false)

  // 新增模式
  const [adding, setAdding] = useState(false)
  const [newPreset, setNewPreset] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      const data = await api.get<{ presets: Preset[]; providers: ProviderItem[] }>('/admin/provider-presets')
      setPresets(data.presets)
      setProviders(data.providers || [])
    } catch { /* */ }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  const startEdit = (p: ProviderItem, idx: number) => {
    setExpanded(p.name)
    setEditName(p.name)
    setEditProvider(p.provider)
    setEditBaseUrl(p.base_url)
    setEditChat(p.chat_model)
    setEditWork(p.work_model)
    setEditEmbed(p.embedding_model)
    setEditThinking(p.thinking_supported)
    setEditModels(p.model_options?.length ? JSON.stringify(p.model_options, null, 2) : '')
    setEditIsDefault(p.is_default)
    setEditGlobalChatModel(p.global_default_chat_model || '')
    setEditGlobalWorkModel(p.global_default_work_model || '')
    setEditLayout(p.system_layout || 'anywhere')
    setEditLayoutSource(p.system_layout_source || 'preset')
    setEditLayoutTouched(false)
    setLayoutNote('')
    setEditIndex(idx)
    setAdding(false)
    setNewPreset('')
    setSaved(false)
  }

  const startAdd = (presetKey: string) => {
    setAdding(true)
    setNewPreset(presetKey)
    setExpanded(null)
    setSaved(false)

    if (presetKey === 'manual') {
      setEditName('')
      setEditProvider('manual')
      setEditBaseUrl('')
      setEditChat('')
      setEditWork('')
      setEditEmbed('')
      setEditThinking(false)
      setEditModels('')
      setEditIsDefault(providers.length === 0)
      setEditIndex(null)
      setEditGlobalChatModel('')
      setEditGlobalWorkModel('')
      setEditLayout('anywhere')
      setEditLayoutSource('preset')
      setEditLayoutTouched(false)
      setLayoutNote('')
    } else {
      const p = presets.find(pr => pr.key === presetKey)
      if (p) {
        setEditName(p.key)
        setEditProvider(p.key)
        setEditBaseUrl(p.base_url)
        setEditChat(p.chat_model)
        setEditWork(p.work_model)
        setEditEmbed(p.embedding_model)
        setEditThinking(p.thinking_supported)
        setEditModels(JSON.stringify(p.models, null, 2))
        setEditIsDefault(providers.length === 0)
        setEditIndex(null)
        setEditGlobalChatModel(p.global_default_chat_model || '')
        setEditGlobalWorkModel(p.global_default_work_model || '')
        setEditLayout(p.system_layout || 'anywhere')
        setEditLayoutSource('preset')
        setEditLayoutTouched(false)
        setLayoutNote('')
      }
    }
  }

  const handleSave = async () => {
    if (!editName.trim()) return
    setSaving(true)
    setSaved(false)
    try {
      let modelOptions: ModelOption[] = []
      try { modelOptions = JSON.parse(editModels) } catch { /* */ }

      await api.put('/admin/provider-presets/save', {
        name: editName.trim(),
        provider: editProvider,
        base_url: editBaseUrl || undefined,
        chat_model: editChat || undefined,
        work_model: editWork || undefined,
        embedding_model: editEmbed || undefined,
        model_options: modelOptions.length > 0 ? modelOptions : undefined,
        thinking_supported: editThinking,
        // 只传"用户真的动过"的值：没动过就交给后端保存时探测，避免把预设值误标成手动
        system_layout: editLayoutTouched ? editLayout : undefined,
        is_default: editIsDefault,
        index: editIndex,
        global_default_chat_model: editGlobalChatModel || undefined,
        global_default_work_model: editGlobalWorkModel || undefined,
      })
      setSaved(true)
      await load()
      setAdding(false)
      setExpanded(null)
      setTimeout(() => setSaved(false), 3000)
    } catch { /* */ }
    setSaving(false)
  }

  /** 「重新识别」：只探测不保存 —— 用户改完模型或换了服务，点一下就知道新结论。 */
  const handleReprobe = async () => {
    if (!editBaseUrl.trim()) return
    setReprobing(true)
    setLayoutNote('')
    try {
      const r = await api.post<{
        ok: boolean; layout: string; provider_label: string; message: string
      }>('/admin/provider-presets/probe-template', {
        base_url: editBaseUrl.trim(),
        chat_model: editChat.trim() || undefined,
      })
      if (r.ok) {
        setEditLayout(r.layout)
        setEditLayoutSource('detected')
        setLayoutNote(r.message || t('admin:layoutSourceDetected'))
      } else {
        setLayoutNote(r.message || t('admin:reprobeLayout'))
      }
    } catch (e: any) {
      setLayoutNote(e?.detail || e?.message || '')
    }
    setReprobing(false)
  }

  const handleDelete = async (name: string) => {
    if (!confirm(t('admin:confirmDeleteProvider'))) return
    try {
      await api.delete(`/admin/provider-presets/${encodeURIComponent(name)}`)
      await load()
      if (expanded === name) setExpanded(null)
    } catch { /* */ }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 size={20} className="animate-spin text-textMuted" />
      </div>
    )
  }

  return (
    <Card title={t('admin:llmProvider')} icon={<Server size={16} className="text-accent-400" />}>

      {/* 已配置的供应商列表 */}
      {providers.length > 0 && (
        <div className="space-y-2">
          {providers.map((p, idx) => {
            const isOpen = expanded === p.name
            return (
              <div key={p.name} className={`card card-pad card-interactive ${p.is_default ? 'is-active' : ''}`}>
                <div className="flex items-center justify-between">
                  <button
                    onClick={() => isOpen ? setExpanded(null) : startEdit(p, idx)}
                    className="flex items-center gap-2 text-sm font-medium text-textPrimary hover:text-primary-500 transition-colors"
                  >
                    {p.is_default && <Star size={14} className="text-accent-400 fill-accent-400" />}
                    {p.name}
                    <span className="text-xs text-textMuted">({p.provider})</span>
                    {p.thinking_supported && (
                      <span className="chip chip-primary shrink-0">{t('admin:thinkingSupported')}</span>
                    )}
                    {p.system_layout && p.system_layout !== 'anywhere' && (
                      <Badge tone="muted" className="shrink-0">system: {p.system_layout}</Badge>
                    )}
                  </button>
                  <div className="flex items-center gap-2">
                    {p.is_default && (
                      <span className="chip chip-accent shrink-0">
                        {t('admin:defaultProvider')}
                      </span>
                    )}
                    <IconButton
                      icon={<Trash2 size={14} />}
                      label={t('common:delete')}
                      tone="danger"
                      size="sm"
                      onClick={() => handleDelete(p.name)}
                    />
                  </div>
                </div>
                {!isOpen && (
                  <div className="text-xs text-textMuted mt-1 ml-6">
                    <Globe size={10} className="inline mr-1" />
                    {p.base_url}
                  </div>
                )}

                {/* 编辑表单 */}
                {isOpen && (
                  <ProviderEditForm
                    editName={editName} setEditName={setEditName}
                    editBaseUrl={editBaseUrl} setEditBaseUrl={setEditBaseUrl}
                    editChat={editChat} setEditChat={setEditChat}
                    editWork={editWork} setEditWork={setEditWork}
                    editEmbed={editEmbed} setEditEmbed={setEditEmbed}
                    editThinking={editThinking} setEditThinking={setEditThinking}
                    editModels={editModels} setEditModels={setEditModels}
                    editIsDefault={editIsDefault} setEditIsDefault={setEditIsDefault}
                    editGlobalChatModel={editGlobalChatModel} setEditGlobalChatModel={setEditGlobalChatModel}
                    editGlobalWorkModel={editGlobalWorkModel} setEditGlobalWorkModel={setEditGlobalWorkModel}
                    editLayout={editLayout} setEditLayout={setEditLayout} setEditLayoutTouched={setEditLayoutTouched}
                    editLayoutSource={editLayoutSource} layoutNote={layoutNote}
                    reprobeing={reprobing} onReprobe={handleReprobe}
                    saving={saving} saved={saved}
                    onSave={handleSave}
                    t={t}
                  />
                )}
              </div>
            )
          })}
        </div>
      )}

      {providers.length === 0 && (
        <EmptyState icon={Server} title={t('admin:noProvidersYet')} />
      )}

      {/* 添加新供应商 */}
      {adding ? (
        <div className="card card-pad card-interactive is-active space-y-3">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-medium text-textPrimary">
              {t('admin:addProvider')}
            </h4>
            <button
              onClick={() => setAdding(false)}
              className="btn btn-xs btn-ghost"
            >
              {t('common:cancel')}
            </button>
          </div>

          {/* 预设选择 */}
          <div className="flex flex-wrap gap-2">
            {presets.map(p => (
              <Button
                key={p.key}
                variant="outline"
                size="sm"
                disabled={newPreset === p.key}
                onClick={() => startAdd(p.key)}
                className={newPreset === p.key ? 'is-active disabled:opacity-100' : ''}
              >
                {p.label}
              </Button>
            ))}
            <button
              disabled={newPreset === 'manual'}
              onClick={() => startAdd('manual')}
              className="btn btn-sm btn-outline"
            >
              <Settings size={12} className="inline mr-1" />
              {t('admin:manualConfig')}
            </button>
          </div>

          {newPreset && (
            <ProviderEditForm
              editName={editName} setEditName={setEditName}
              editBaseUrl={editBaseUrl} setEditBaseUrl={setEditBaseUrl}
              editChat={editChat} setEditChat={setEditChat}
              editWork={editWork} setEditWork={setEditWork}
              editEmbed={editEmbed} setEditEmbed={setEditEmbed}
              editThinking={editThinking} setEditThinking={setEditThinking}
              editModels={editModels} setEditModels={setEditModels}
              editIsDefault={editIsDefault} setEditIsDefault={setEditIsDefault}
              editGlobalChatModel={editGlobalChatModel} setEditGlobalChatModel={setEditGlobalChatModel}
              editGlobalWorkModel={editGlobalWorkModel} setEditGlobalWorkModel={setEditGlobalWorkModel}
              editLayout={editLayout} setEditLayout={setEditLayout} setEditLayoutTouched={setEditLayoutTouched}
              editLayoutSource={editLayoutSource} layoutNote={layoutNote}
              reprobeing={reprobing} onReprobe={handleReprobe}
              saving={saving} saved={saved}
              onSave={handleSave}
              t={t}
              isNew
            />
          )}
        </div>
      ) : (
        <Button
          variant="outline"
          size="sm"
          icon={<Plus size={14} />}
          className="border-dashed"
          onClick={() => setAdding(true)}
        >
          {t('admin:addProvider')}
        </Button>
      )}
    </Card>
  )
}

/** 供应商编辑表单（复用） */
function ProviderEditForm({
  editName, setEditName,
  editBaseUrl, setEditBaseUrl,
  editChat, setEditChat,
  editWork, setEditWork,
  editEmbed, setEditEmbed,
  editThinking, setEditThinking,
  editModels, setEditModels,
  editIsDefault, setEditIsDefault,
  editGlobalChatModel, setEditGlobalChatModel,
  editGlobalWorkModel, setEditGlobalWorkModel,
  editLayout, setEditLayout, setEditLayoutTouched,
  editLayoutSource, layoutNote, reprobeing, onReprobe,
  saving, saved, onSave, t, isNew,
}: {
  editName: string; setEditName: (v: string) => void
  editBaseUrl: string; setEditBaseUrl: (v: string) => void
  editChat: string; setEditChat: (v: string) => void
  editWork: string; setEditWork: (v: string) => void
  editEmbed: string; setEditEmbed: (v: string) => void
  editThinking: boolean; setEditThinking: (v: boolean) => void
  editModels: string; setEditModels: (v: string) => void
  editIsDefault: boolean; setEditIsDefault: (v: boolean) => void
  editGlobalChatModel: string; setEditGlobalChatModel: (v: string) => void
  editGlobalWorkModel: string; setEditGlobalWorkModel: (v: string) => void
  editLayout: string; setEditLayout: (v: string) => void
  setEditLayoutTouched: (v: boolean) => void
  editLayoutSource: string; layoutNote: string
  reprobeing: boolean; onReprobe: () => void
  saving: boolean; saved: boolean; onSave: () => void
  t: (key: string) => string
  isNew?: boolean
}) {
  // 「获取模型」是表单自己的事：它只需要 base_url 和 write-only 的 editModels，
  // 所以状态放这里，父组件一个 prop 都不用加
  const [fetchKey, setFetchKey] = useState('')
  const [fetching, setFetching] = useState(false)
  const [fetchMsg, setFetchMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const handleFetchModels = async () => {
    if (!editBaseUrl.trim()) return
    setFetching(true)
    setFetchMsg(null)
    try {
      const r = await api.post<{ ok: boolean; message: string; models: ModelOption[] }>(
        '/admin/provider-presets/fetch-models',
        { base_url: editBaseUrl.trim(), api_key: fetchKey.trim() || undefined },
      )
      if (r.models && r.models.length > 0) {
        setEditModels(JSON.stringify(r.models, null, 2))
        const okText = t('admin:fetchModelsOk')
        setFetchMsg({ ok: true, text: okText.replace('{n}', String(r.models.length)) })
      } else {
        setFetchMsg({ ok: r.ok, text: r.message || t('admin:fetchModelsEmpty') })
      }
    } catch (e: any) {
      setFetchMsg({ ok: false, text: e?.detail || e?.message || '获取失败' })
    }
    setFetching(false)
  }

  const sourceLabel = editLayoutSource === 'detected' ? t('admin:layoutSourceDetected')
    : editLayoutSource === 'user' ? t('admin:layoutSourceUser')
    : t('admin:layoutSourcePreset')

  return (
    <div className="space-y-3 mt-3">
      {isNew && (
        <div>
          <label className="block text-xs text-textSecondary mb-1">{t('admin:providerName')}</label>
          <input
            type="text" value={editName}
            onChange={e => setEditName(e.target.value)}
            className="field field-sm"
            placeholder="deepseek-主号"
          />
        </div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-textSecondary mb-1">API Base URL</label>
          <input
            type="text" value={editBaseUrl}
            onChange={e => setEditBaseUrl(e.target.value)}
            className="field field-sm font-mono"
          />
        </div>
        <div>
          <label className="block text-xs text-textSecondary mb-1">{t('admin:defaultChatModel')}</label>
          <input
            type="text" value={editChat}
            onChange={e => setEditChat(e.target.value)}
            className="field field-sm"
          />
        </div>
        <div>
          <label className="block text-xs text-textSecondary mb-1">{t('admin:defaultWorkModel')}</label>
          <input
            type="text" value={editWork}
            onChange={e => setEditWork(e.target.value)}
            className="field field-sm"
          />
        </div>
        <div>
          <label className="block text-xs text-textSecondary mb-1">Embedding 模型</label>
          <input
            type="text" value={editEmbed}
            onChange={e => setEditEmbed(e.target.value)}
            className="field field-sm"
          />
        </div>
      </div>
      <div className="border-t border-border/60 pt-3 mt-3">
        <p className="text-xs text-textMuted mb-2">全局默认模型（全站未指定模型时使用）</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-textSecondary mb-1">全局默认聊天模型</label>
            <input
              type="text" value={editGlobalChatModel}
              onChange={e => setEditGlobalChatModel(e.target.value)}
              className="field field-sm"
              placeholder="例如：glm-5.2"
            />
          </div>
          <div>
            <label className="block text-xs text-textSecondary mb-1">全局默认工作模型</label>
            <input
              type="text" value={editGlobalWorkModel}
              onChange={e => setEditGlobalWorkModel(e.target.value)}
              className="field field-sm"
              placeholder="例如：glm-5.3-flash"
            />
          </div>
        </div>
      </div>
      <div className="border-t border-border/60 pt-3 mt-3 space-y-1.5">
        <div className="flex items-end justify-between gap-2 flex-wrap">
          <div className="flex-1 min-w-[240px]">
            <Select
              label={t('admin:systemLayout')}
              fieldSize="sm"
              value={editLayout}
              onChange={e => { setEditLayout(e.target.value); setEditLayoutTouched(true) }}
              options={[
                { value: 'anywhere', label: t('admin:systemLayoutAnywhere') },
                { value: 'leading', label: t('admin:systemLayoutLeading') },
                { value: 'none', label: t('admin:systemLayoutNone') },
              ]}
            />
          </div>
          <button
            onClick={onReprobe}
            disabled={reprobeing || !editBaseUrl.trim()}
            title={!editBaseUrl.trim() ? '请先填 API Base URL' : undefined}
            className="btn btn-md btn-outline gap-1 shrink-0"
          >
            {reprobeing ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            {t('admin:reprobeLayout')}
          </button>
        </div>
        <div className="flex items-start gap-1.5 flex-wrap">
          <Badge tone="muted">{sourceLabel}</Badge>
          <span className="text-2xs text-textMuted break-all flex-1">
            {layoutNote || t('admin:systemLayoutHint')}
          </span>
        </div>
      </div>
      <div className="flex items-center gap-4">
        <label className="flex items-center gap-2 text-xs text-textSecondary cursor-pointer">
          <input
            type="checkbox" checked={editThinking}
            onChange={e => setEditThinking(e.target.checked)}
            className="rounded"
          />
          {t('admin:thinkingSupported')}
        </label>
        <label className="flex items-center gap-2 text-xs text-textSecondary cursor-pointer">
          <input
            type="checkbox" checked={editIsDefault}
            onChange={e => setEditIsDefault(e.target.checked)}
            className="rounded"
          />
          <Star size={12} className="fill-current" /> {t('admin:setAsDefault')}
        </label>
      </div>
      <div>
        <div className="flex items-center justify-between gap-2 mb-1 flex-wrap">
          <label className="block text-xs text-textSecondary">{t('admin:modelOptionsJson')}</label>
          <div className="flex items-center gap-2">
            <input
              type="text" value={fetchKey}
              onChange={e => setFetchKey(e.target.value)}
              placeholder={t('admin:fetchModelsHint')}
              className="field field-sm field-inline w-56 font-mono"
            />
            <button
              onClick={handleFetchModels}
              disabled={fetching || !editBaseUrl.trim()}
              title={!editBaseUrl.trim() ? '请先填 API Base URL' : undefined}
              className="btn btn-md btn-outline gap-1 shrink-0"
            >
              {fetching ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
              {t('admin:fetchModels')}
            </button>
          </div>
        </div>
        <textarea
          rows={3}
          value={editModels}
          onChange={e => setEditModels(e.target.value)}
          className="field field-sm font-mono resize-y"
        />
        {fetchMsg && (
          <p className={'text-2xs mt-1 break-all ' + (fetchMsg.ok ? 'text-mint-400' : 'text-rose-400')}>{fetchMsg.text}</p>
        )}
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={onSave}
          disabled={saving || !editName.trim()}
          className="btn btn-md btn-primary gap-1.5"
        >
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
          {saving ? t('common:saving') : t('common:save')}
        </button>
        {saved && (
          <span className="text-xs text-mint-400 animate-pulse">{t('common:saved')}</span>
        )}
      </div>
    </div>
  )
}
