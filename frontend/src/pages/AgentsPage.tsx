import { useState, useEffect } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { Bot, Plus, Edit3, History, Power, Download, Upload, X, RotateCcw, Eye, EyeOff, ArrowLeft, Menu } from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import Toggle from '../components/Toggle'
import { Button, Dialog, IconButton, PageHeader, Slider } from '../components/ui'
import { useT, useLang } from '../i18n/I18nContext'
import { fmtDate, fmtDateTime } from '../utils/time'

import { STATE_BADGE_COLORS, AI_TYPE_LABEL } from '../constants'

interface ModelOption {
  value: string
  label: string
  provider: string
}

interface Agent {
  id: number
  name: string
  state: string
  is_ai_editable: boolean
  original_system_prompt: string | null
  original_temperature: number
  original_top_p: number
  original_presence_penalty: number
  original_frequency_penalty: number
  current_system_prompt: string | null
  current_temperature: number
  current_top_p: number
  current_presence_penalty: number
  current_frequency_penalty: number
  chat_model: string | null
  work_model: string | null
  thinking_enabled: boolean
  hide_ai_identity: boolean
  config_profile: string
  delay_reply_enabled: boolean | null
  max_tool_rounds: number
  alarm_max_tool_rounds: number
  force_alarm_on_end: boolean
  retire_handover_self: boolean
  max_alarms: number
  api_credit_cost: number
  api_base_url: string | null
  has_api_key: boolean
  avatar_url: string | null
  ai_type: string
  created_at: string
  emotion_vectorized?: boolean

}

interface ConfigHistory {
  id: number
  agent_id: number
  system_prompt: string | null
  temperature: number
  top_p: number
  presence_penalty: number
  frequency_penalty: number
  created_at: string | null
}

const stateLabelKeys: Record<string, string> = {
  active: 'agents:stateLabelsActive',
  dnd: 'agents:stateLabelsDnd',
  offline: 'agents:stateLabelsOffline',
  blocked: 'agents:stateLabelsBlocked',
}

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([])

  const [showImport, setShowImport] = useState(false)
  const [editAgent, setEditAgent] = useState<Agent | null>(null)
  const [historyAgent, setHistoryAgent] = useState<Agent | null>(null)
  const [stateAgent, setStateAgent] = useState<Agent | null>(null)
  const { refreshUser } = useAuth()
  const t = useT()
  const lang = useLang()

  const loadAgents = async () => {
    try {
      const data = await api.get('/agents')
      setAgents(data)
    } catch (err) {
      console.error('加载 AI 列表失败:', err)
    }
  }

  useEffect(() => {
    loadAgents()
  }, [])

  const handleExport = async (agent: Agent) => {
    try {
      await api.download(`/agents/${agent.id}/export`, `soul_${agent.name}.json`)
    } catch (err: any) {
      console.error('导出失败:', err)
      alert(err.message || t('error:exportFailed'))
    }
  }

  const navigate = useNavigate()
  const { openDrawer } = useOutletContext<{ openDrawer: () => void }>()

  return (
    <div className="h-full flex flex-col bg-canvas">
      <PageHeader
        title={t('agents:title')}
        subtitle={t('agents:subtitle')}
        leading={
          <IconButton
            size="sm"
            icon={<Menu size={18} />}
            label={t('chatlist:menu')}
            onClick={openDrawer}
            className="md:hidden -ml-1"
          />
        }
      >
        <Button size="sm" variant="outline" icon={<Upload size={13} />} onClick={() => setShowImport(true)}>
          {t('agents:import')}
        </Button>
        <Button size="sm" icon={<Plus size={13} />} onClick={() => navigate('/agents/create')}>
          {t('agents:create')}
        </Button>
      </PageHeader>

      {/* 内容 */}
      <div className="flex-1 overflow-y-auto">
        <div className="p-4 md:p-6">
        <div className="max-w-4xl mx-auto">

        {/* AI 卡片列表 */}
        {agents.length === 0 ? (
          <div className="text-center py-16">
            <Bot size={48} className="mx-auto text-textMuted mb-4" />
            <p className="text-textSecondary">{t('agents:empty')}</p>
            <p className="text-sm text-textMuted mt-1">{t('agents:emptyHint')}</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {agents.map((agent) => {
              const hasModified =
                agent.current_system_prompt !== agent.original_system_prompt ||
                agent.current_temperature !== agent.original_temperature

              return (
                <div
                  key={agent.id}
                  onClick={() => navigate(`/agents/${agent.id}`)}
                  className="bg-surface border border-border rounded-card p-5 hover:border-primary-500/30 transition-all duration-200 cursor-pointer"
                >
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-full bg-primary-500/15 flex items-center justify-center overflow-hidden">
                        {agent.avatar_url ? (
                          <img src={agent.avatar_url} className="w-10 h-10 rounded-full object-cover" alt={agent.name} />
                        ) : (
                          <Bot size={20} className="text-primary-400" />
                        )}
                      </div>
                      <div>
                        <h3 className="font-medium text-textPrimary">{agent.name}</h3>
                        <p className="text-xs text-textMuted">
                          {fmtDate(agent.created_at, lang)}
                        </p>
                      </div>
                    </div>
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium border ${STATE_BADGE_COLORS[agent.state] || ''}`}>
                      {t(stateLabelKeys[agent.state]) || agent.state}
                    </span>
                  </div>

                  {agent.current_system_prompt && (
                    <p className="text-sm text-textSecondary line-clamp-2 mb-3">
                      {agent.current_system_prompt}
                    </p>
                  )}

                  <div className="flex items-center gap-2 text-xs text-textMuted mb-1 flex-wrap">
                    <span className="text-textSecondary font-medium">
                      {agent.chat_model || t('agents:defaultModel')}
                    </span>
                    <span>
                      Temp: {agent.current_temperature}
                      {hasModified && agent.original_temperature !== agent.current_temperature && (
                        <span className="text-accent-400 ml-0.5">({agent.original_temperature})</span>
                      )}
                    </span>
                    {agent.is_ai_editable && (
                      <span className="text-mint-400">{t('agents:selfEditable')}</span>
                    )}
                    {AI_TYPE_LABEL[agent.ai_type] && (
                      <span className={`text-3xs px-1.5 py-0.5 rounded-full font-medium ${AI_TYPE_LABEL[agent.ai_type].cls}`}>
                        {t(AI_TYPE_LABEL[agent.ai_type].key)}
                      </span>
                    )}
                    {agent.thinking_enabled && (
                      <span className="text-accent-400">{t('agents:thinkingMode')}</span>
                    )}
                    {hasModified && (
                      <span className="text-accent-400 font-medium">{t('agents:modified')}</span>
                    )}
                  </div>

                  <div className="flex items-center gap-2 mt-3 pt-3 border-t border-border flex-wrap">
                    <button
                      onClick={(e) => { e.stopPropagation(); navigate(`/agents/${agent.id}`) }}
                      className="flex items-center gap-1 px-2 py-1 text-xs text-textSecondary hover:text-primary-400 rounded-control hover:bg-elevated transition-colors"
                    >
                      <Edit3 size={12} /> {t('agents:edit')}
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); setHistoryAgent(agent) }}
                      className="flex items-center gap-1 px-2 py-1 text-xs text-textSecondary hover:text-primary-400 rounded-control hover:bg-elevated transition-colors"
                    >
                      <History size={12} /> {t('agents:history')}
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); setStateAgent(agent) }}
                      className="flex items-center gap-1 px-2 py-1 text-xs text-textSecondary hover:text-accent-400 rounded-control hover:bg-elevated transition-colors"
                    >
                      <Power size={12} /> {t('agents:state')}
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleExport(agent) }}
                      className="flex items-center gap-1 px-2 py-1 text-xs text-textSecondary hover:text-mint-400 rounded-control hover:bg-elevated transition-colors"
                    >
                      <Download size={12} /> {t('agents:export')}
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {/* 创建走整页流程（入口三选一 + 左助手右表单）：/agents/create */}

        {/* 编辑弹窗 */}
        {editAgent && (
          <EditAgentModal
            agent={editAgent}
            onClose={() => setEditAgent(null)}
            onUpdated={() => {
              setEditAgent(null)
              loadAgents()
            }}
          />
        )}

        {/* 历史弹窗 */}
        {historyAgent && (
          <HistoryModal
            agent={historyAgent}
            onClose={() => setHistoryAgent(null)}
            onRollback={() => {
              setHistoryAgent(null)
              loadAgents()
            }}
          />
        )}

        {/* 状态弹窗 */}
        {stateAgent && (
          <StateModal
            agent={stateAgent}
            onClose={() => setStateAgent(null)}
            onUpdated={() => {
              setStateAgent(null)
              loadAgents()
            }}
          />
        )}

        {/* 导入灵魂弹窗 */}
        {showImport && (
          <ImportSoulModal
            onClose={() => setShowImport(false)}
            onImported={() => {
              setShowImport(false)
              loadAgents()
              refreshUser()
            }}
          />
        )}
        </div>
      </div>
    </div>
    </div>
  )
}

/* ================================================================
   编辑 AI 配置弹窗
   左侧：原始设定（只读） | 右侧：当前设定（可编辑）
   ================================================================ */
function EditAgentModal({ agent, onClose, onUpdated }: {
  agent: Agent; onClose: () => void; onUpdated: () => void
}) {
  const t = useT()
  const [systemPrompt, setSystemPrompt] = useState(agent.current_system_prompt || '')
  const [temperature, setTemperature] = useState(agent.current_temperature)
  const [topP, setTopP] = useState(agent.current_top_p)
  const [presencePenalty, setPresencePenalty] = useState(agent.current_presence_penalty)
  const [frequencyPenalty, setFrequencyPenalty] = useState(agent.current_frequency_penalty)
  const [chatModel, setChatModel] = useState(agent.chat_model || '')
  const [workModel, setWorkModel] = useState(agent.work_model || '')
  const [thinkingEnabled, setThinkingEnabled] = useState(agent.thinking_enabled)
  const [emotionVectorized, setEmotionVectorized] = useState(agent.emotion_vectorized || false)
  const [hideAiIdentity, setHideAiIdentity] = useState(agent.hide_ai_identity || false)
  const [delayReplyEnabled, setDelayReplyEnabled] = useState<boolean | null>(agent.delay_reply_enabled ?? null)
  const [configProfile, setConfigProfile] = useState(agent.config_profile || 'chat')
  const [maxToolRounds, setMaxToolRounds] = useState(agent.max_tool_rounds ?? 3)
  const [alarmMaxToolRounds, setAlarmMaxToolRounds] = useState(agent.alarm_max_tool_rounds ?? 10)
  const [forceAlarmOnEnd, setForceAlarmOnEnd] = useState(agent.force_alarm_on_end ?? false)
  const [retireHandoverSelf, setRetireHandoverSelf] = useState(agent.retire_handover_self ?? false)
  const [maxAlarms, setMaxAlarms] = useState(agent.max_alarms ?? 10)
  const [isAiEditable, setIsAiEditable] = useState(agent.is_ai_editable ?? true)
  const [agentApiBaseUrl, setAgentApiBaseUrl] = useState(agent.api_base_url || '')
  const [agentApiKey, setAgentApiKey] = useState('')
  const [applyingPreset, setApplyingPreset] = useState(false)
  const [presetPreview, setPresetPreview] = useState<any>(null) // 预设预览数据
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([])
  const [defaults, setDefaults] = useState<{ chat_model: string; work_model: string }>({ chat_model: '', work_model: '' })
  const [thinkingSupported, setThinkingSupported] = useState(false)

  useEffect(() => {
    api.get<{ models: ModelOption[]; defaults: { chat_model: string; work_model: string }; provider: { thinking_supported: boolean } }>('/agents/models')
      .then(data => {
        setModelOptions(data.models)
        setDefaults(data.defaults)
        setThinkingSupported(data.provider?.thinking_supported ?? false)
      })
      .catch(console.error)
  }, [])

  const hasModified =
    agent.current_system_prompt !== agent.original_system_prompt ||
    agent.current_temperature !== agent.original_temperature

  const effectiveChatModel = chatModel || defaults.chat_model
  const effectiveWorkModel = workModel || defaults.work_model

  const handleSave = async () => {
    setLoading(true)
    setError('')
    try {
      const hideIdentity = hideAiIdentity
      const apiBaseUrl = agentApiBaseUrl || null
      const apiKey = agentApiKey || null

      await api.put(`/agents/${agent.id}/config`, {
        system_prompt: systemPrompt || null,
        temperature,
        top_p: topP,
        presence_penalty: presencePenalty,
        frequency_penalty: frequencyPenalty,
        chat_model: chatModel || null,
        work_model: workModel || null,
        thinking_enabled: thinkingEnabled,
        emotion_vectorized: emotionVectorized,
        hide_ai_identity: hideIdentity,
        delay_reply_enabled: delayReplyEnabled,
        max_tool_rounds: maxToolRounds,
        alarm_max_tool_rounds: alarmMaxToolRounds,
        force_alarm_on_end: forceAlarmOnEnd,
        retire_handover_self: retireHandoverSelf,
        max_alarms: maxAlarms,
        is_ai_editable: isAiEditable,
        api_base_url: apiBaseUrl,
        api_key: apiKey,
      })
      onUpdated()
    } catch (err: any) {
      setError(err.message || t('error:saveFailed'))
    } finally {
      setLoading(false)
    }
  }

  const handleApplyPreset = async (profile: string) => {
    // 先预览
    setApplyingPreset(true)
    setError('')
    try {
      const preview = await api.get<any>(`/agents/${agent.id}/preset-preview?profile=${profile}`)
      setPresetPreview({ profile, ...preview })
    } catch (err: any) {
      setError(err.message || t('error:operationFailed'))
      setApplyingPreset(false)
    }
  }

  const confirmApplyPreset = async () => {
    if (!presetPreview) return
    const profile = presetPreview.profile
    setApplyingPreset(true)
    setError('')
    try {
      const updated = await api.post<Agent>(`/agents/${agent.id}/apply-preset`, { profile })
      setTemperature(updated.current_temperature)
      setTopP(updated.current_top_p)
      setPresencePenalty(updated.current_presence_penalty)
      setFrequencyPenalty(updated.current_frequency_penalty)
      setThinkingEnabled(updated.thinking_enabled)
      setMaxToolRounds(updated.max_tool_rounds)
      setAlarmMaxToolRounds(updated.alarm_max_tool_rounds)
      setForceAlarmOnEnd(updated.force_alarm_on_end)
      setRetireHandoverSelf(updated.retire_handover_self)
      setMaxAlarms(updated.max_alarms)
      setIsAiEditable(updated.is_ai_editable)
      setConfigProfile(profile)
      setPresetPreview(null)
      onUpdated()
    } catch (err: any) {
      setError(err.message || t('error:operationFailed'))
    } finally {
      setApplyingPreset(false)
    }
  }

  const presets = [
    { key: 'chat', label: t('agents:presetChat'), desc: t('agents:presetChatDesc') },
    { key: 'immersive', label: t('agents:presetImmersive'), desc: t('agents:presetImmersiveDesc') },
    { key: 'digital_life', label: t('agents:presetDigitalLife'), desc: t('agents:presetDigitalLifeDesc') },
  ]

  return (
    <Dialog onClose={onClose} className="flex items-center justify-center">
      <div className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto shadow-2xl shadow-black/30 pb-[var(--safe-bottom)] md:pb-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-textPrimary">{t('agents:editTitle')} {agent.name}</h2>
          <button onClick={onClose} className="icon-btn-sm text-textMuted">
            <X size={18} />
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          {/* 原始设定（只读） */}
          <div className="bg-canvas rounded-card p-4 border border-border">
            <h3 className="text-sm font-semibold text-textSecondary mb-3 flex items-center gap-1">
              {t('agents:originalSettings')}
            </h3>
            <div className="space-y-2 text-sm">
              <div>
                <span className="text-xs text-textMuted">{t('agents:systemPromptLabel')}:</span>
                <p className="text-textSecondary mt-0.5 whitespace-pre-wrap line-clamp-6">
                  {agent.original_system_prompt || t('agents:notSet')}
                </p>
              </div>
              <div className="grid grid-cols-2 gap-1 text-xs">
                <span className="text-textMuted">{t('agents:chatModelLabel')}</span>
                <span className="text-textSecondary">{agent.chat_model || `${t('common:default')} (${defaults.chat_model})`}</span>
                <span className="text-textMuted">{t('agents:workModelLabel')}</span>
                <span className="text-textSecondary">{agent.work_model || `${t('common:default')} (${defaults.work_model})`}</span>
                <span className="text-textMuted">Temperature:</span>
                <span className="text-textSecondary">{agent.original_temperature}</span>
                <span className="text-textMuted">Top P:</span>
                <span className="text-textSecondary">{agent.original_top_p}</span>
                <span className="text-textMuted">Presence:</span>
                <span className="text-textSecondary">{agent.original_presence_penalty}</span>
                <span className="text-textMuted">Frequency:</span>
                <span className="text-textSecondary">{agent.original_frequency_penalty}</span>
              </div>
            </div>
          </div>

          {/* 当前设定（可编辑） */}
          <div className="bg-primary-500/5 rounded-card p-4 border border-primary-500/20">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-primary-400 flex items-center gap-1">
                {t('agents:currentSettings')}{hasModified ? t('agents:currentSettingsModified') : ''}
              </h3>
              {/* 档位标签 */}
              <span className="chip chip-primary shrink-0">
                {configProfile === 'chat' ? t('agents:badgeChat') : configProfile === 'immersive' ? t('agents:badgeImmersive') : t('agents:badgeDigitalLife')}
              </span>
            </div>
            {/* 三档快捷切换 */}
            <div className="flex gap-1.5 mb-3">
              {presets.map(p => (
                <button
                  key={p.key}
                  onClick={() => handleApplyPreset(p.key)}
                  disabled={applyingPreset || configProfile === p.key}
                  className={`flex-1 py-1.5 text-xs rounded-control font-medium transition-all ${
                    configProfile === p.key
                      ? 'bg-primary-500 text-white shadow-sm'
                      : 'bg-canvas border border-border text-textSecondary hover:border-primary-500/40 hover:text-primary-400'
                  } disabled:opacity-50`}
                  title={p.desc}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <div className="space-y-2">
              <div>
                <label className="text-xs text-textMuted">{t('agents:systemPromptLabel')}</label>
                <textarea
                  value={systemPrompt}
                  onChange={(e) => setSystemPrompt(e.target.value)}
                  rows={3}
                  className="w-full px-3 py-1.5 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50 resize-none mt-0.5"
                />
              </div>
              {/* 模型选择 */}
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-xs text-textMuted">{t('agents:chatModelSelect')}</label>
                  <select
                    value={chatModel}
                    onChange={(e) => setChatModel(e.target.value)}
                    className="w-full mt-0.5 px-2 py-1.5 rounded-control border border-border bg-canvas text-xs text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                  >
                    <option value="">{t('common:default')} ({defaults.chat_model})</option>
                    {modelOptions.map(m => (
                      <option key={m.value} value={m.value}>{m.label}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-textMuted">{t('agents:workModelSelect')}</label>
                  <select
                    value={workModel}
                    onChange={(e) => setWorkModel(e.target.value)}
                    className="w-full mt-0.5 px-2 py-1.5 rounded-control border border-border bg-canvas text-xs text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                  >
                    <option value="">{t('common:default')} ({defaults.work_model})</option>
                    {modelOptions.map(m => (
                      <option key={m.value} value={m.value}>{m.label}</option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="text-3xs text-textMuted">
                {t('agents:effectiveChatPrefix')} {effectiveChatModel} {t('agents:effectiveWorkPrefix')} {effectiveWorkModel}
              </div>
              <div className="space-y-1.5 text-xs">
                {[
                  ['Temperature', temperature, setTemperature, 0, 2, 0.1],
                  ['Top P', topP, setTopP, 0, 1, 0.05],
                  ['Presence', presencePenalty, setPresencePenalty, -2, 2, 0.1],
                  ['Frequency', frequencyPenalty, setFrequencyPenalty, -2, 2, 0.1],
                ].map(([label, value, setter, min, max, step]) => (
                  <div key={label as string}>
                    <label className="text-textMuted">{label as string}: {String(value)}</label>
                    <Slider
                      min={min as number}
                      max={max as number}
                      step={step as number}
                      value={value as number}
                      onChange={(v) => (setter as any)(v)}
                      className="mt-1"
                    />
                  </div>
                ))}
              </div>
              {/* 深度推理模式（仅 DeepSeek API 显示） */}
              {thinkingSupported && (
                <div className="flex items-center justify-between pt-2 border-t border-border mt-2">
                  <div>
                    <span className="text-xs text-textSecondary">{t('agents:thinkingModeLabel')}</span>
                    <p className="text-3xs text-textMuted mt-0.5">{t('agents:thinkingModeDesc')}</p>
                  </div>
                  <Toggle checked={thinkingEnabled} onChange={setThinkingEnabled} />
                </div>
              )}
              {/* AI 身份隐藏 */}
              <div className="flex items-center justify-between pt-2 border-t border-border mt-2">
                <div>
                  <span className="text-xs text-textSecondary">{t('agents:hideAiLabel')}</span>
                  <p className="text-3xs text-textMuted mt-0.5">{t('agents:hideAiDesc')}</p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <Toggle checked={hideAiIdentity} onChange={setHideAiIdentity} />
                </label>
              </div>
              {/* 向量化情感（更拟人） */}
              <div className="flex items-center justify-between pt-2 border-t border-border mt-2">
                <div>
                  <span className="text-xs text-textSecondary">向量化情感（更拟人）</span>
                  <p className="text-3xs text-textMuted mt-0.5">情感用 8 轴向量表达（开心/伤心/愤怒/恐惧/厌恶/惊讶/信任/期待），可同时多轴非零；不勾则用文字心情描述</p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <Toggle checked={emotionVectorized} onChange={setEmotionVectorized} />
                </label>
              </div>
              {/* 延迟回复 */}
              <div className="flex items-center justify-between pt-2 border-t border-border mt-2">
                <div>
                  <span className="text-xs text-textSecondary">{t('agents:delayReplyLabel')}</span>
                  <p className="text-3xs text-textMuted mt-0.5">{t('agents:delayReplyDesc')}</p>
                </div>
                <select
                  value={delayReplyEnabled === null ? 'inherit' : delayReplyEnabled ? 'on' : 'off'}
                  onChange={(e) => {
                    const v = e.target.value
                    setDelayReplyEnabled(v === 'inherit' ? null : v === 'on')
                  }}
                  className="text-xs px-2 py-1 rounded-control border border-border bg-canvas text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                >
                  <option value="inherit">{t('agents:inheritGlobal')}</option>
                  <option value="on">{t('agents:enable')}</option>
                  <option value="off">{t('agents:disable')}</option>
                </select>
              </div>
            </div>
          </div>
        </div>

        {/* ── 工具调用 & 闹钟 ── */}
        <div className="bg-canvas rounded-card p-4 border border-border mb-4">
          <h3 className="text-sm font-semibold text-textSecondary mb-3">{t('agentDetail:toolCallsAndAlarms')}</h3>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <span className="text-xs text-textMuted">{t('agentDetail:maxToolRounds')}（1-20）</span>
              <span className="inline-flex items-center gap-1 ml-1">
                <button onClick={() => setMaxToolRounds(Math.max(1, maxToolRounds - 1))}
                  className="w-5 h-5 rounded bg-canvas border border-border text-textMuted hover:text-textPrimary text-xs">−</button>
                <span className="text-textPrimary font-mono w-5 text-center text-xs">{maxToolRounds}</span>
                <button onClick={() => setMaxToolRounds(Math.min(20, maxToolRounds + 1))}
                  className="w-5 h-5 rounded bg-canvas border border-border text-textMuted hover:text-textPrimary text-xs">+</button>
              </span>
            </div>
            <div>
              <span className="text-xs text-textMuted">{t('agentDetail:alarmMaxRounds')}（1-30）</span>
              <span className="inline-flex items-center gap-1 ml-1">
                <button onClick={() => setAlarmMaxToolRounds(Math.max(1, alarmMaxToolRounds - 1))}
                  className="w-5 h-5 rounded bg-canvas border border-border text-textMuted hover:text-textPrimary text-xs">−</button>
                <span className="text-textPrimary font-mono w-5 text-center text-xs">{alarmMaxToolRounds}</span>
                <button onClick={() => setAlarmMaxToolRounds(Math.min(30, alarmMaxToolRounds + 1))}
                  className="w-5 h-5 rounded bg-canvas border border-border text-textMuted hover:text-textPrimary text-xs">+</button>
              </span>
            </div>
            <div>
              <span className="text-xs text-textMuted">{t('agentDetail:maxAlarms')}（1-50）</span>
              <span className="inline-flex items-center gap-1 ml-1">
                <button onClick={() => setMaxAlarms(Math.max(1, maxAlarms - 1))}
                  className="w-5 h-5 rounded bg-canvas border border-border text-textMuted hover:text-textPrimary text-xs">−</button>
                <span className="text-textPrimary font-mono w-5 text-center text-xs">{maxAlarms}</span>
                <button onClick={() => setMaxAlarms(Math.min(50, maxAlarms + 1))}
                  className="w-5 h-5 rounded bg-canvas border border-border text-textMuted hover:text-textPrimary text-xs">+</button>
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs text-textSecondary">{t('agentDetail:forceAlarm')}</span>
              <Toggle checked={forceAlarmOnEnd} onChange={setForceAlarmOnEnd} />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs text-textSecondary">{t('agentDetail:retireHandover')}</span>
              <Toggle checked={retireHandoverSelf} onChange={setRetireHandoverSelf} />
            </div>
          </div>
          {/* 自修改开关 */}
          <div className="flex items-center justify-between mt-3 pt-3 border-t border-border/60">
            <div>
              <span className="text-xs text-textSecondary">{t('agents:selfEditable')}</span>
              <p className="text-3xs text-textMuted mt-0.5">{t('agents:selfEditableDesc')}</p>
            </div>
            <Toggle checked={isAiEditable} onChange={setIsAiEditable} />
          </div>
        </div>

        {/* 独立 API 配置 */}
        <div className="bg-canvas rounded-card p-4 border border-border mb-4">
          <h3 className="text-sm font-semibold text-textSecondary mb-3">{t('agents:independentApi')}</h3>
          <div className="space-y-2">
            <div>
              <label className="text-xs text-textMuted">{t('agents:apiBaseUrlLabel')}</label>
              <input
                type="text"
                value={agentApiBaseUrl}
                onChange={(e) => setAgentApiBaseUrl(e.target.value)}
                className="w-full mt-0.5 px-3 py-1.5 rounded-control border border-border bg-canvas text-xs text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                placeholder="https://api.deepseek.com"
              />
            </div>
            <div>
              <label className="text-xs text-textMuted">{t('agents:apiKeyLabel')}</label>
              <input
                type="password"
                value={agentApiKey}
                onChange={(e) => setAgentApiKey(e.target.value)}
                placeholder={agent.has_api_key ? t('agents:apiKeyPlaceholderSet') : t('agents:apiKeyPlaceholder')}
                className="w-full mt-0.5 px-3 py-1.5 rounded-control border border-border bg-canvas text-xs text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
              />
              {agent.has_api_key && <p className="text-3xs text-mint-400 mt-0.5">{t('agents:apiKeySet')}</p>}
            </div>
          </div>
        </div>

        {error && <div className="text-sm text-rose-400 mb-3">{error}</div>}

        {/* ── 预设切换预览确认 ── */}
        {presetPreview && (
          <div className="mb-4 bg-accent-500/5 border border-accent-500/20 rounded-card p-4">
            <h4 className="text-sm font-semibold text-textPrimary mb-2">{t('agents:presetConfirmTitle')}</h4>
            <p className="text-xs text-textSecondary mb-3">
              {t('agents:presetFrom')} <b>{presetPreview.old_profile}</b> {t('agents:presetTo')}{' '}
              <b>{presetPreview.new_profile}</b>
              （{presetPreview.direction === 'upgrade' ? `${t('agents:presetUpgrade')}` : `${t('agents:presetDowngrade')}`}），
              {t('agents:presetItemsWillChange').replace(' field(s) will change:', ` ${Object.keys(presetPreview.changed_fields || {}).length} 项将变更：`.replace('/项/', ` ${Object.keys(presetPreview.changed_fields || {}).length} 项`))}
            </p>
            {Object.keys(presetPreview.changed_fields || {}).length > 0 ? (
              <div className="space-y-1.5 mb-3 text-xs">
                {Object.entries(presetPreview.changed_fields as Record<string, {old: any; new: any}>).map(([key, v]: [string, any]) => (
                  <div key={key} className="flex items-center justify-between bg-canvas rounded-control px-3 py-1.5">
                    <span className="text-textSecondary">{key}</span>
                    <span className="text-textMuted font-mono">
                      <span>{String(v.old)}</span>
                      <span className="mx-1.5">→</span>
                      <span className="text-mint-400 font-medium">{String(v.new)}</span>
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-textMuted mb-3">{t('agents:presetNoChange')}</p>
            )}
            {presetPreview.independent_untouched?.length > 0 && (
              <p className="text-3xs text-textMuted mb-3">
                {t('agents:presetIndependentFields')} {presetPreview.independent_untouched.join('、')}
              </p>
            )}
            <div className="flex gap-2">
              <button onClick={() => { setPresetPreview(null); setApplyingPreset(false) }}
                className="flex-1 py-1.5 text-xs border border-border rounded-control hover:bg-elevated text-textSecondary transition-colors">
                {t('agents:cancel')}
              </button>
              <button onClick={confirmApplyPreset} disabled={applyingPreset}
                className="btn btn-xs btn-primary flex-1">
                {applyingPreset ? t('agents:applying') : t('agents:confirmSwitch')}
              </button>
            </div>
          </div>
        )}

        <div className="flex gap-2">
          <button onClick={onClose} className="btn btn-md btn-outline flex-1">
            {t('agents:cancel')}
          </button>
          <button
            onClick={handleSave}
            disabled={loading}
            className="btn btn-md btn-primary flex-1"
          >
            {loading ? t('agents:saving') : t('agents:saveChanges')}
          </button>
        </div>
      </div>
    </Dialog>
  )
}

/* ================================================================
   配置历史弹窗
   ================================================================ */
function HistoryModal({ agent, onClose, onRollback }: {
  agent: Agent; onClose: () => void; onRollback: () => void
}) {
  const t = useT()
  const lang = useLang()
  const [history, setHistory] = useState<ConfigHistory[]>([])
  const [loading, setLoading] = useState(true)
  const [rollingBack, setRollingBack] = useState<number | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const load = async () => {
      try {
        const data = await api.get(`/agents/${agent.id}/history`)
        setHistory(data)
      } catch (err: any) {
        setError(err.message || t('error:operationFailed'))
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [agent.id])

  const handleRollback = async (versionId: number) => {
    setRollingBack(versionId)
    setError('')
    try {
      await api.post(`/agents/${agent.id}/rollback/${versionId}`)
      onRollback()
    } catch (err: any) {
      setError(err.message || t('error:operationFailed'))
    } finally {
      setRollingBack(null)
    }
  }

  return (
    <Dialog onClose={onClose} className="flex items-center justify-center">
      <div className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-xl mx-4 max-h-[80vh] overflow-y-auto shadow-2xl shadow-black/30 pb-[var(--safe-bottom)] md:pb-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-textPrimary">{agent.name} · {t('agents:configHistory')}</h2>
          <button onClick={onClose} className="icon-btn-sm text-textMuted">
            <X size={18} />
          </button>
        </div>

        {loading ? (
          <div className="text-center py-8 text-textMuted text-sm">{t('agents:historyLoading')}</div>
        ) : history.length === 0 ? (
          <div className="text-center py-8 text-textMuted text-sm">{t('agents:noHistory')}</div>
        ) : (
          <div className="space-y-3">
            {history.map((h, idx) => {
              const isLatest = idx === 0
              return (
                <div
                  key={h.id}
                  className={`rounded-card p-4 border ${
                    isLatest
                      ? 'border-primary-500/30 bg-primary-500/5'
                      : 'border-border bg-canvas'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs text-textMuted">
                      {h.created_at ? fmtDateTime(h.created_at, lang) : t('agents:unknownTime')}
                      {isLatest && <span className="ml-1 text-primary-400 font-medium">{t('agents:currentLabel')}</span>}
                    </span>
                    {!isLatest && (
                      <button
                        onClick={() => handleRollback(h.id)}
                        disabled={rollingBack === h.id}
                        className="flex items-center gap-1 px-2 py-0.5 text-xs text-accent-400 hover:bg-accent-400/10 rounded-control transition-colors disabled:opacity-30"
                      >
                        <RotateCcw size={11} />
                        {rollingBack === h.id ? t('agents:rollingBack') : t('agents:rollbackTo')}
                      </button>
                    )}
                  </div>
                  {h.system_prompt && (
                    <p className="text-xs text-textSecondary line-clamp-2 whitespace-pre-wrap mb-1">
                      {h.system_prompt}
                    </p>
                  )}
                  <div className="text-xs text-textMuted flex gap-3">
                    <span>T: {h.temperature}</span>
                    <span>P: {h.top_p}</span>
                    <span>Pre: {h.presence_penalty}</span>
                    <span>Freq: {h.frequency_penalty}</span>
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {error && <div className="text-sm text-rose-400 mt-3">{error}</div>}
      </div>
    </Dialog>
  )
}

/* ================================================================
   状态切换弹窗
   ================================================================ */
function StateModal({ agent, onClose, onUpdated }: {
  agent: Agent; onClose: () => void; onUpdated: () => void
}) {
  const t = useT()
  const [targetState, setTargetState] = useState(agent.state)
  const [durationHours, setDurationHours] = useState(1)
  const [reason, setReason] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSwitch = async () => {
    if (targetState === agent.state) {
      onClose()
      return
    }
    setLoading(true)
    setError('')
    try {
      await api.post(`/agents/${agent.id}/state`, {
        target_state: targetState,
        duration_hours: targetState === 'blocked' ? durationHours : undefined,
        reason: reason || undefined,
      })
      onUpdated()
    } catch (err: any) {
      setError(err.message || t('error:operationFailed'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog onClose={onClose} className="flex items-center justify-center">
      <div className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-sm mx-4 shadow-2xl shadow-black/30 pb-[var(--safe-bottom)] md:pb-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-textPrimary">{agent.name} · {t('agents:state')}</h2>
          <button onClick={onClose} className="icon-btn-sm text-textMuted">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium mb-1.5 text-textSecondary">{t('agents:currentState')} {t(stateLabelKeys[agent.state]) || agent.state}</label>
            <select
              value={targetState}
              onChange={(e) => setTargetState(e.target.value)}
              className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
            >
              {Object.entries(stateLabelKeys).map(([k, v]) => (
                <option key={k} value={k}>{t(v)}</option>
              ))}
            </select>
          </div>

          {targetState === 'blocked' && (
            <div>
              <label className="block text-xs font-medium mb-1.5 text-textSecondary">{t('agents:banDuration')}</label>
              <input
                type="number"
                min={1} max={72}
                value={durationHours}
                onChange={(e) => setDurationHours(parseInt(e.target.value) || 1)}
                className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
              />
            </div>
          )}

          <div>
            <label className="block text-xs font-medium mb-1.5 text-textSecondary">{t('agents:reasonOptional')}</label>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
              placeholder={t('agents:reasonPlaceholder')}
            />
          </div>
        </div>

        {error && <div className="text-sm text-rose-400 mt-3">{error}</div>}

        <div className="flex gap-2 mt-4">
          <button onClick={onClose} className="btn btn-md btn-outline flex-1">
            {t('agents:cancel')}
          </button>
          <button
            onClick={handleSwitch}
            disabled={loading || targetState === agent.state}
            className="btn btn-md btn-primary flex-1"
          >
            {loading ? t('agents:switchingState') : t('agents:switchState')}
          </button>
        </div>
      </div>
    </Dialog>
  )
}

/* ================================================================
   创建 AI 弹窗
   ================================================================ */
/* ================================================================
   导入 AI 灵魂档案弹窗
   ================================================================ */
function ImportSoulModal({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const t = useT()
  const [file, setFile] = useState<File | null>(null)
  const [importMemories, setImportMemories] = useState(true)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<any>(null)

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    setFile(f)
    setError('')
    try {
      const text = await f.text()
      const data = JSON.parse(text)
      setPreview(data)
    } catch {
      setError(t('agents:invalidFile'))
      setPreview(null)
    }
  }

  const handleImport = async () => {
    if (!file) return
    setLoading(true)
    setError('')
    try {
      const token = localStorage.getItem('access_token')
      const formData = new FormData()
      formData.append('file', file)
      const res = await fetch(`/api/agents/import?import_memories=${importMemories}`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` },
        body: formData,
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || t('error:operationFailed'))
      }
      onImported()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog onClose={onClose} className="flex items-center justify-center">
      <div className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-md mx-4 shadow-2xl pb-[var(--safe-bottom)] md:pb-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-textPrimary">{t('agents:importSoulTitle')}</h2>
          <button onClick={onClose} className="icon-btn-sm text-textMuted">
            <X size={18} />
          </button>
        </div>

        <input
          type="file"
          accept=".json"
          onChange={handleFileChange}
          className="block w-full text-sm text-textPrimary file:mr-3 file:py-2 file:px-4 file:rounded-card file:border-0 file:text-sm file:bg-elevated file:text-textPrimary hover:file:bg-border mb-3"
        />

        {preview && (
          <div className="bg-canvas rounded-card p-3 mb-3 text-sm space-y-1">
            <p className="font-medium text-textPrimary">{preview.agent_name || t('agents:unnamed')}</p>
            <p className="text-xs text-textSecondary">{t('agents:memoryCount')} {preview.memories?.length || 0}</p>
            <p className="text-xs text-textSecondary">{t('agents:friendCount')} {preview.friends?.length || 0}</p>
            <p className="text-xs text-textSecondary">{t('agents:configHistoryCount')} {preview.config_history?.length || 0}</p>
          </div>
        )}

        <label className="flex items-center gap-2 mb-4 text-sm text-textSecondary cursor-pointer">
          <input
            type="checkbox"
            checked={importMemories}
            onChange={(e) => setImportMemories(e.target.checked)}
            className="rounded"
          />
          {t('agents:importMemories')}
        </label>

        {error && <div className="text-sm text-rose-400 mb-3">{error}</div>}

        <div className="flex gap-2">
          <button onClick={onClose} className="btn btn-md btn-outline flex-1">
            {t('agents:cancel')}
          </button>
          <button
            onClick={handleImport}
            disabled={!file || loading}
            className="btn btn-md btn-primary flex-1"
          >
            {loading ? t('agents:importing') : t('agents:importButton')}
          </button>
        </div>
      </div>
    </Dialog>
  )
}

