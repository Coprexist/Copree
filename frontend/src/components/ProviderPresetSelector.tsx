import { useState, useEffect } from 'react'
import { api } from '../api/client'
import { useT } from '../i18n/I18nContext'
import { Plus, Trash2, Check, Loader2, Settings, Server, Star, Globe, Download } from 'lucide-react'

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
    <section className="bg-surface border border-border rounded-card p-5 space-y-4">
      <h3 className="text-sm font-semibold text-textPrimary flex items-center gap-2">
        <Server size={16} className="text-accent-400" />
        {t('admin:llmProvider')}
      </h3>

      {/* 已配置的供应商列表 */}
      {providers.length > 0 && (
        <div className="space-y-2">
          {providers.map((p, idx) => {
            const isOpen = expanded === p.name
            return (
              <div key={p.name} className={`border rounded-control p-3 ${p.is_default ? 'border-primary-500/40 bg-primary-500/5' : 'border-border bg-canvas'}`}>
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
                  </button>
                  <div className="flex items-center gap-2">
                    {p.is_default && (
                      <span className="chip chip-accent shrink-0">
                        {t('admin:defaultProvider')}
                      </span>
                    )}
                    <button
                      onClick={() => handleDelete(p.name)}
                      className="p-1 text-textMuted hover:text-rose-400 transition-colors"
                      title={t('common:delete')}
                    >
                      <Trash2 size={14} />
                    </button>
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
        <div className="text-xs text-textMuted py-4 text-center">{t('admin:noProvidersYet')}</div>
      )}

      {/* 添加新供应商 */}
      {adding ? (
        <div className="border border-primary-500/40 rounded-control p-4 bg-primary-500/5 space-y-3">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-medium text-textPrimary">
              {t('admin:addProvider')}
            </h4>
            <button
              onClick={() => setAdding(false)}
              className="text-xs text-textMuted hover:text-textPrimary"
            >
              {t('common:cancel')}
            </button>
          </div>

          {/* 预设选择 */}
          <div className="flex flex-wrap gap-2">
            {presets.map(p => (
              <button
                key={p.key}
                disabled={newPreset === p.key}
                onClick={() => startAdd(p.key)}
                className={`px-3 py-1.5 text-xs rounded-control font-medium transition-colors border ${
                  newPreset === p.key
                    ? 'bg-primary-500/15 border-primary-500/40 text-primary-500'
                    : 'bg-canvas border-border text-textSecondary hover:text-textPrimary hover:border-primary-500/30'
                }`}
              >
                {p.label}
              </button>
            ))}
            <button
              disabled={newPreset === 'manual'}
              onClick={() => startAdd('manual')}
              className={`px-3 py-1.5 text-xs rounded-control font-medium transition-colors border ${
                newPreset === 'manual'
                  ? 'bg-accent-500/15 border-accent-500/40 text-accent-500'
                  : 'bg-canvas border-border text-textMuted hover:text-textSecondary'
              }`}
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
              saving={saving} saved={saved}
              onSave={handleSave}
              t={t}
              isNew
            />
          )}
        </div>
      ) : (
        <button
          onClick={() => setAdding(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-control border border-dashed border-border text-textMuted hover:text-primary-500 hover:border-primary-500/40 transition-colors"
        >
          <Plus size={14} />
          {t('admin:addProvider')}
        </button>
      )}
    </section>
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

  return (
    <div className="space-y-3 mt-3">
      {isNew && (
        <div>
          <label className="block text-xs text-textSecondary mb-1">{t('admin:providerName')}</label>
          <input
            type="text" value={editName}
            onChange={e => setEditName(e.target.value)}
            className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-textPrimary text-xs focus:outline-none focus:ring-2 focus:ring-primary-500/60"
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
            className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-textPrimary text-xs font-mono focus:outline-none focus:ring-2 focus:ring-primary-500/60"
          />
        </div>
        <div>
          <label className="block text-xs text-textSecondary mb-1">{t('admin:defaultChatModel')}</label>
          <input
            type="text" value={editChat}
            onChange={e => setEditChat(e.target.value)}
            className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-textPrimary text-xs focus:outline-none focus:ring-2 focus:ring-primary-500/60"
          />
        </div>
        <div>
          <label className="block text-xs text-textSecondary mb-1">{t('admin:defaultWorkModel')}</label>
          <input
            type="text" value={editWork}
            onChange={e => setEditWork(e.target.value)}
            className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-textPrimary text-xs focus:outline-none focus:ring-2 focus:ring-primary-500/60"
          />
        </div>
        <div>
          <label className="block text-xs text-textSecondary mb-1">Embedding 模型</label>
          <input
            type="text" value={editEmbed}
            onChange={e => setEditEmbed(e.target.value)}
            className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-textPrimary text-xs focus:outline-none focus:ring-2 focus:ring-primary-500/60"
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
              className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-textPrimary text-xs focus:outline-none focus:ring-2 focus:ring-primary-500/60"
              placeholder="例如：glm-5.2"
            />
          </div>
          <div>
            <label className="block text-xs text-textSecondary mb-1">全局默认工作模型</label>
            <input
              type="text" value={editGlobalWorkModel}
              onChange={e => setEditGlobalWorkModel(e.target.value)}
              className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-textPrimary text-xs focus:outline-none focus:ring-2 focus:ring-primary-500/60"
              placeholder="例如：glm-5.3-flash"
            />
          </div>
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
              className="w-56 px-2 py-1 rounded-control border border-border bg-canvas text-textPrimary text-2xs font-mono focus:outline-none focus:ring-2 focus:ring-primary-500/60"
            />
            <button
              onClick={handleFetchModels}
              disabled={fetching || !editBaseUrl.trim()}
              title={!editBaseUrl.trim() ? '请先填 API Base URL' : undefined}
              className="btn btn-xs btn-outline gap-1 bg-canvas hover:text-primary-500 hover:border-primary-500/40 disabled:hover:text-textSecondary shrink-0"
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
          className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-textPrimary text-xs font-mono focus:outline-none focus:ring-2 focus:ring-primary-500/60 resize-y"
        />
        {fetchMsg && (
          <p className={'text-2xs mt-1 break-all ' + (fetchMsg.ok ? 'text-mint-400' : 'text-rose-400')}>{fetchMsg.text}</p>
        )}
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={onSave}
          disabled={saving || !editName.trim()}
          className="btn btn-sm btn-primary gap-1.5"
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
