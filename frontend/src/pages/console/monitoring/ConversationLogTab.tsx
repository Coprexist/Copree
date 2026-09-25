import { useState, useEffect } from 'react'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'
import { FileText, Settings, Bot, Eye, ChevronDown, ChevronUp, Loader2, Save, Sliders, X } from 'lucide-react'
import Toggle from '../../../components/Toggle'
import { Dialog } from '../../../components/ui'

interface GlobalConfig {
  max_conversation_logs: number
  default_user_conversation_logs: number
  default_user_log_access: boolean
  default_delay_reply_enabled: boolean
  compression_threshold: number
  idle_threshold_percent: number | null
  compress_target_percent: number | null
}

interface AgentSettings {
  agent_id: number
  conversation_logs_limit: number | null
  user_can_view_logs: boolean | null
  effective_limit: number
  effective_user_access: boolean
  system_max: number
  system_default_access: boolean
}

interface LogSummary {
  id: number
  agent_id: number
  conversation_type: string
  message_count: number
  token_usage: any
  has_output: boolean
  model: string | null
  thinking_enabled: boolean
  preview: any[]
  created_at: string | null
}

interface AgentOption {
  id: number
  name: string
}

export default function ConversationLogTab() {
  const t = useT()
  const [section, setSection] = useState<'config' | 'agents' | 'viewer'>('config')
  const [config, setConfig] = useState<GlobalConfig | null>(null)
  const [configLoading, setConfigLoading] = useState(true)
  const [configSaving, setConfigSaving] = useState(false)

  // Per-agent
  const [agents, setAgents] = useState<AgentOption[]>([])
  const [agentSearch, setAgentSearch] = useState('')
  const [agentSearching, setAgentSearching] = useState(false)
  const [selectedAgentId, setSelectedAgentId] = useState<number | null>(null)
  const [agentSettings, setAgentSettings] = useState<AgentSettings | null>(null)
  const [agentLimit, setAgentLimit] = useState('')
  const [agentAccess, setAgentAccess] = useState<boolean | null>(null)
  const [agentSaving, setAgentSaving] = useState(false)

  // Log viewer
  const [viewAgentId, setViewAgentId] = useState<number | null>(null)
  const [logs, setLogs] = useState<LogSummary[]>([])
  const [logsLoading, setLogsLoading] = useState(false)
  const [selectedLog, setSelectedLog] = useState<any>(null)
  const [logDetail, setLogDetail] = useState<any>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  // ── Load global config ──
  useEffect(() => {
    api.get<GlobalConfig>('/admin/conversation-log/config')
      .then(setConfig)
      .catch(console.error)
      .finally(() => setConfigLoading(false))
  }, [])

  // ── Load agent list（支持搜索）──
  const loadAgents = async (search = '') => {
    setAgentSearching(true)
    try {
      const res = await api.get<{items: any[]}>(`/admin/agents?page_size=100${search ? `&search=${encodeURIComponent(search)}` : ''}`)
      setAgents((res.items || []).map((a: any) => ({ id: a.id, name: a.name })))
    } catch { /* ignore */ }
    finally { setAgentSearching(false) }
  }
  useEffect(() => { loadAgents() }, [])

  // ── Load per-agent settings ──
  useEffect(() => {
    if (!selectedAgentId) return
    api.get<AgentSettings>(`/admin/conversation-log/agents/${selectedAgentId}/settings`)
      .then(s => {
        setAgentSettings(s)
        setAgentLimit(s.conversation_logs_limit?.toString() || '')
        setAgentAccess(s.user_can_view_logs)
      })
      .catch(console.error)
  }, [selectedAgentId])

  // ── Save global config ──
  const saveConfig = async () => {
    if (!config) return
    setConfigSaving(true)
    try {
      const updated = await api.put<GlobalConfig>('/admin/conversation-log/config', {
        max_conversation_logs: config.max_conversation_logs,
        default_user_conversation_logs: config.default_user_conversation_logs,
        default_user_log_access: config.default_user_log_access,
        default_delay_reply_enabled: config.default_delay_reply_enabled,
        compression_threshold: config.compression_threshold,
        idle_threshold_percent: config.idle_threshold_percent,
        compress_target_percent: config.compress_target_percent,
      })
      setConfig(updated)
    } catch (err: any) { alert(err.message) }
    finally { setConfigSaving(false) }
  }

  // ── Save per-agent settings ──
  const saveAgentSettings = async () => {
    if (!selectedAgentId) return
    setAgentSaving(true)
    try {
      const body: any = {}
      const limit = parseInt(agentLimit)
      if (!isNaN(limit) && limit > 0) body.conversation_logs_limit = limit
      else if (agentLimit === '') body.conversation_logs_limit = null  // reset to default
      if (agentAccess !== null) body.user_can_view_logs = agentAccess
      const updated = await api.put<AgentSettings>(`/admin/conversation-log/agents/${selectedAgentId}/settings`, body)
      setAgentSettings(updated)
    } catch (err: any) { alert(err.message) }
    finally { setAgentSaving(false) }
  }

  // ── Load logs ──
  const loadLogs = async () => {
    if (!viewAgentId) return
    setLogsLoading(true)
    try {
      const data = await api.get<LogSummary[]>(`/admin/conversation-log/agents/${viewAgentId}/logs?limit=30`)
      setLogs(data)
    } catch (err: any) { alert(err.message) }
    finally { setLogsLoading(false) }
  }

  // ── View log detail ──
  const viewDetail = async (logId: number) => {
    setDetailLoading(true)
    setSelectedLog(logId)
    try {
      const data = await api.get(`/admin/conversation-log/agents/${viewAgentId}/logs/${logId}`)
      setLogDetail(data)
    } catch (err: any) { alert(err.message) }
    finally { setDetailLoading(false) }
  }

  const formatTime = (t: string | null) => {
    if (!t) return '-'
    return new Date(t).toLocaleString('zh-CN')
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="max-w-xl flex flex-col gap-6">
        {/* Section tabs */}
        <div className="flex gap-2 bg-canvas border border-border rounded-card p-1 w-full">
        {[
          { k: 'config', label: t('admin.convlogGlobal'), icon: Settings },
          { k: 'agents', label: t('admin.convlogPerAgent'), icon: Sliders },
          { k: 'viewer', label: t('admin.convlogViewer'), icon: Eye },
        ].map(s => (
          <button
            key={s.k}
            onClick={() => setSection(s.k as any)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-control text-xs font-medium transition-colors ${
              section === s.k ? 'bg-elevated text-textPrimary shadow-sm' : 'text-textMuted hover:text-textSecondary hover:bg-elevated'
            }`}
          >
            <s.icon size={14} /> {s.label}
          </button>
        ))}
      </div>

      {/* ── Global Config ── */}
      {section === 'config' && (
        <div className="bg-elevated border border-border rounded-card p-5 max-w-xl">
          <h3 className="text-sm font-semibold text-textPrimary mb-4 flex items-center gap-2">
            <Settings size={16} className="text-primary-400" /> {t('admin.convlogConfigTitle')}
          </h3>
          {configLoading ? (
            <div className="flex justify-center py-8"><Loader2 className="animate-spin" size={20} /></div>
          ) : config ? (
            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-textSecondary mb-1">{t('admin.convlogHardLimit')}</label>
                <input
                  type="number" min={1} max={500}
                  value={config.max_conversation_logs}
                  onChange={e => setConfig({ ...config, max_conversation_logs: parseInt(e.target.value) || 30 })}
                  className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-textSecondary mb-1">{t('admin.convlogDefaultLimit')}</label>
                <input
                  type="number" min={1} max={config.max_conversation_logs}
                  value={config.default_user_conversation_logs}
                  onChange={e => setConfig({ ...config, default_user_conversation_logs: parseInt(e.target.value) || 20 })}
                  className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                />
              </div>
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-textSecondary">{t('admin.convlogDefaultAccess')}</label>
                <Toggle checked={config.default_user_log_access} onChange={(v) => setConfig({ ...config, default_user_log_access: v })} />
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <label className="text-xs font-medium text-textSecondary">{t('admin.convlogDefaultDelay')}</label>
                  <p className="text-3xs text-textMuted mt-0.5">{t('admin.convlogDefaultDelayDesc')}</p>
                </div>
                <Toggle checked={config.default_delay_reply_enabled} onChange={(v) => setConfig({ ...config, default_delay_reply_enabled: v })} />
              </div>
              <div>
                <label className="block text-xs font-medium text-textSecondary mb-1">
                  {t('admin.convlogCompressThreshold')} ({config.compression_threshold || 60}%)
                </label>
                <input
                  type="range" min={5} max={100} step={5}
                  value={config.compression_threshold || 60}
                  onChange={e => setConfig({ ...config, compression_threshold: parseInt(e.target.value) })}
                  className="w-full accent-primary-500"
                />
                <p className="text-3xs text-textMuted mt-0.5">{t('admin.convlogCompressThresholdDesc')}</p>
              </div>
              <div>
                <label className="block text-xs font-medium text-textSecondary mb-1">
                  {t('admin.convlogIdleThreshold')} ({config.idle_threshold_percent ?? 37}%)
                </label>
                <input
                  type="range" min={1} max={99} step={1}
                  value={config.idle_threshold_percent ?? 37}
                  onChange={e => setConfig({ ...config, idle_threshold_percent: parseInt(e.target.value) })}
                  className="w-full accent-primary-500"
                />
                <p className="text-3xs text-textMuted mt-0.5">{t('admin.convlogIdleThresholdDesc')}</p>
              </div>
              <div>
                <label className="block text-xs font-medium text-textSecondary mb-1">
                  {t('admin.convlogTargetPercent')} ({config.compress_target_percent ?? 20}%)
                </label>
                <input
                  type="range" min={1} max={99} step={1}
                  value={config.compress_target_percent ?? 20}
                  onChange={e => setConfig({ ...config, compress_target_percent: parseInt(e.target.value) })}
                  className="w-full accent-primary-500"
                />
                <p className="text-3xs text-textMuted mt-0.5">{t('admin.convlogTargetPercentDesc')}</p>
              </div>
              <button
                onClick={saveConfig}
                disabled={configSaving}
                className="btn btn-sm btn-primary gap-2"
              >
                <Save size={14} /> {configSaving ? t('common.saving') : t('admin.saveConfig')}
              </button>
            </div>
          ) : null}
        </div>
      )}

      {/* ── Per-Agent Settings ── */}
      {section === 'agents' && (
        <div className="bg-elevated border border-border rounded-card p-5 max-w-xl">
          <h3 className="text-sm font-semibold text-textPrimary mb-4 flex items-center gap-2">
            <Bot size={16} className="text-primary-400" /> {t('admin.perAgentSettings')}
          </h3>
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-textSecondary mb-1">{t('admin.selectAi')}</label>
              <div className="relative">
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={agentSearch}
                    onChange={e => {
                      setAgentSearch(e.target.value)
                      loadAgents(e.target.value)
                    }}
                    placeholder={t('admin.searchAiPlaceholder')}
                    className="flex-1 px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                  />
                  <select
                    value={selectedAgentId || ''}
                    onChange={e => setSelectedAgentId(e.target.value ? parseInt(e.target.value) : null)}
                    className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50 min-w-0"
                  >
                    <option value="">{t('admin.selectAiPlaceholder')}</option>
                    {agentSearching ? (
                      <option disabled>{t('common.loading')}</option>
                    ) : (
                      agents.map(a => (
                        <option key={a.id} value={a.id}>{a.name} (ID: {a.id})</option>
                      ))
                    )}
                  </select>
                </div>
              </div>
            </div>

            {agentSettings && (
              <>
                <div
                  className="p-3 rounded-control bg-canvas text-xs text-textSecondary"
                  dangerouslySetInnerHTML={{
                    __html: t('admin.currentEffective')
                      .replace('{retention}', `<b class="text-textPrimary">${agentSettings.effective_limit}</b>`)
                      .replace('{access}', `<b class="${agentSettings.effective_user_access ? 'text-mint-400' : 'text-rose-400'}">${agentSettings.effective_user_access ? t('common.enabled') : t('common.disabled')}</b>`)
                  }}
                />
                <div>
                  <label className="block text-xs font-medium text-textSecondary mb-1">
                    {t('admin.retentionLimit').replace('{max}', String(agentSettings.system_max))}
                  </label>
                  <input
                    type="number" min={1} max={agentSettings.system_max}
                    value={agentLimit}
                    onChange={e => setAgentLimit(e.target.value)}
                    placeholder={t('admin.retentionLimit').replace('{max}', String(agentSettings.system_max))}
                    className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                  />
                </div>
                <div className="flex items-center justify-between">
                  <label className="text-xs font-medium text-textSecondary">
                    {t('admin.allowUserViewLogs')}
                    <span className="text-textMuted ml-1">（{agentSettings.system_default_access ? t('admin.defaultOn') : t('admin.defaultOff')}）</span>
                  </label>
                  <button
                    onClick={() => {
                      if (agentAccess === null) setAgentAccess(true)
                      else if (agentAccess === true) setAgentAccess(false)
                      else setAgentAccess(null)
                    }}
                    className={`relative w-12 h-6 rounded-full transition-colors flex-shrink-0 ml-3 ${
                      agentAccess === true ? 'bg-mint-400' :
                      agentAccess === false ? 'bg-rose-400' :
                      'bg-border'
                    }`}
                  >
                    <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                      agentAccess === true ? 'translate-x-6' :
                      agentAccess === false ? 'translate-x-0.5' :
                      'translate-x-[14px] opacity-50'
                    }`} />
                  </button>
                </div>
                <button
                  onClick={saveAgentSettings}
                  disabled={agentSaving}
                  className="btn btn-sm btn-primary gap-2"
                >
                  <Save size={14} /> {agentSaving ? t('common.saving') : t('admin.saveSettings')}
                </button>
              </>
            )}
          </div>
        </div>
      )}

      {/* ── Log Viewer ── */}
      {section === 'viewer' && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={agentSearch}
              onChange={e => {
                setAgentSearch(e.target.value)
                loadAgents(e.target.value)
              }}
              placeholder={t('admin.searchAiPlaceholder')}
              className="flex-1 px-3 py-2 rounded-control border border-border bg-elevated text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
            />
            <select
              value={viewAgentId || ''}
              onChange={e => {
                setViewAgentId(e.target.value ? parseInt(e.target.value) : null)
                setLogs([])
                setSelectedLog(null)
              }}
              className="px-3 py-2 rounded-control border border-border bg-elevated text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
            >
              <option value="">{t('admin.selectAiPlaceholder')}</option>
              {agentSearching ? (
                <option disabled>{t('common.loading')}</option>
              ) : (
                agents.map(a => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))
              )}
            </select>
            <button
              onClick={loadLogs}
              disabled={!viewAgentId || logsLoading}
              className="btn btn-sm btn-primary shrink-0"
            >
              {logsLoading ? <Loader2 className="animate-spin" size={14} /> : t('common.load')}
            </button>
          </div>

          {/* Log list */}
          {logs.length > 0 && (
            <div className="bg-elevated border border-border rounded-card overflow-hidden">
              <div className="divide-y divide-border">
                {logs.map(log => (
                  <div
                    key={log.id}
                    className="px-4 py-3 hover:bg-canvas cursor-pointer transition-colors"
                    onClick={() => viewDetail(log.id)}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono text-textMuted">#{log.id}</span>
                        <span className={`text-xs px-1.5 py-0.5 rounded ${
                          log.conversation_type === 'group' ? 'bg-blue-400/10 text-blue-400' : 'bg-primary-400/10 text-primary-400'
                        }`}>
                          {log.conversation_type === 'group' ? t('admin.groupChat') : log.conversation_type === 'dm' ? t('admin.directMessage') : log.conversation_type}
                        </span>
                        {log.has_output && <span className="text-xs text-mint-400">{t('admin.hasOutput')}</span>}
                        {log.thinking_enabled && <span className="text-xs text-accent-400">{t('admin.deepReasoning')}</span>}
                      </div>
                      <span className="text-xs text-textMuted">{formatTime(log.created_at)}</span>
                    </div>
                    <div className="text-xs text-textSecondary">
                      <span>{log.message_count} {t('admin.messages')}</span>
                      {log.token_usage && (
                        <span className="ml-3">
                          {t('admin.tokens')} {log.token_usage.total_tokens}
                        </span>
                      )}
                      {log.model && <span className="ml-3 text-textMuted">{log.model}</span>}
                    </div>
                    {log.preview && log.preview.length > 0 && (
                      <div className="mt-1.5 text-xs text-textMuted space-y-0.5">
                        {log.preview.slice(0, 2).map((p: any, i: number) => (
                          <div key={i} className="truncate">
                            <span className="font-medium">{p.role}:</span>{' '}
                            {p.content || (p.tool_calls ? p.tool_calls.join(', ') : '...')}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Log detail modal */}
          {selectedLog && (
            <Dialog onClose={() =>  { setSelectedLog(null); setLogDetail(null) } } className="flex items-start justify-center pt-10 overflow-y-auto">
              <div
                className="bg-elevated border border-border rounded-dialog p-5 w-full max-w-2xl mx-4 shadow-2xl shadow-black/30 max-h-[80vh] overflow-y-auto"
                onClick={e => e.stopPropagation()}
              >
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-sm font-semibold text-textPrimary">{t('admin.conversationLog').replace('{id}', String(selectedLog))}</h3>
                  <button onClick={() => { setSelectedLog(null); setLogDetail(null) }} className="text-textMuted hover:text-textSecondary"><X size={16} /></button>
                </div>
                {detailLoading ? (
                  <div className="flex justify-center py-12"><Loader2 className="animate-spin" size={24} /></div>
                ) : logDetail ? (
                  <div className="space-y-3">
                    <div className="flex gap-4 text-xs text-textSecondary">
                      <span>{t('admin.logType')} {logDetail.conversation_type}</span>
                      <span>{t('admin.logMessageCount')} {logDetail.message_count}</span>
                      <span>{t('admin.logModel')} {logDetail.model || '-'}</span>
                      <span>{formatTime(logDetail.created_at)}</span>
                    </div>
                    <div className="bg-canvas rounded-card p-3 max-h-[50vh] overflow-y-auto">
                      <pre className="text-xs text-textSecondary whitespace-pre-wrap font-mono leading-relaxed">
                        {JSON.stringify(logDetail.messages, null, 2)}
                      </pre>
                    </div>
                  </div>
                ) : null}
              </div>
            </Dialog>
          )}
        </div>
      )}
      </div>
    </div>
  )
}
