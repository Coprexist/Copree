import { useState } from 'react'
import { api } from '../api/client'
import { useT } from '../i18n/I18nContext'
import { ArrowLeft, X, Loader2, RotateCw, Ticket, ChevronRight, MessageSquare } from 'lucide-react'
import { STATUS_COLORS } from '../utils/statusColor.tsx'
import SkillBackpack from './SkillBackpack'
import ChannelModal from './channels/ChannelModal'
import Toggle from './Toggle'

interface AgentData {
  id: number
  name: string
  current_system_prompt: string | null
  current_temperature: number | null
  current_top_p: number | null
  current_presence_penalty: number | null
  current_frequency_penalty: number | null
  thinking_enabled: boolean
  hide_ai_identity: boolean
  chat_model: string | null
  work_model: string | null
  max_tool_rounds: number
  alarm_max_tool_rounds: number
  force_alarm_on_end: boolean
  max_alarms: number
  memory_load_mode: string
  memory_recent_count: number
  memory_shared_scope: string
  ai_type: string
  delay_reply_enabled: boolean | null
  is_ai_editable: boolean
  allow_friend_requests: boolean
  auto_respond_friend_request: boolean
  api_credit_cost: number
  api_base_url: string | null
  has_api_key: boolean
  config_profile?: string
  reminder_grace?: string
  discoverable?: boolean
  allow_others_chat?: boolean
  others_chat_mode?: string
  others_chat_quota?: number
  others_chat_used?: number
  disallow_mode?: string
  is_paused?: boolean
  auto_dnd_threshold?: number
  auto_dnd_duration?: number
  conversation_logs_limit?: number | null
  user_can_view_logs?: boolean | null
  bio?: string | null
  status_text?: string | null
  status_color?: string | null
  auto_reset_quota?: boolean
  group_owner_pays?: boolean
  dm_quota_config?: {
    send?: { daily?: number; weekly?: number; creator_chat?: number }
    receive?: { daily?: number; weekly?: number; creator_chat?: number }
  }
}

interface ModelOption {
  value: string
  label: string
  provider_name?: string
  provider_key?: string
}

interface ProviderItem {
  name: string
  provider: string
  base_url: string
  api_key_url?: string
  thinking_supported: boolean
  is_default: boolean
  models: ModelOption[]
}

interface Props {
  agent: AgentData
  modelOptions: ModelOption[]
  providers?: ProviderItem[]
  defaults: { chat_model: string; work_model: string }
  thinkingSupported: boolean
  isOpen: boolean
  onClose: () => void
  onSaved: () => void
}

// ── 预设参数（与 CreateAgentModal 保持一致） ──
const PRESET_DEFAULTS: Record<string, Record<string, any>> = {
  chat: { temperature: 0.8, thinking_enabled: false, max_tool_rounds: 3, alarm_max_tool_rounds: 3, force_alarm_on_end: false, max_alarms: 3, delay_reply_enabled: null, is_ai_editable: true, hide_ai_identity: false, reminder_grace: 'every_time', memory_load_mode: 'index_only', memory_recent_count: 0 },
  immersive: { temperature: 0.85, thinking_enabled: false, max_tool_rounds: 5, alarm_max_tool_rounds: 8, force_alarm_on_end: false, max_alarms: 8, delay_reply_enabled: true, is_ai_editable: true, hide_ai_identity: false, reminder_grace: 'every_time', memory_load_mode: 'index_plus_recent', memory_recent_count: 3 },
  digital_life: { temperature: 0.9, thinking_enabled: true, max_tool_rounds: 10, alarm_max_tool_rounds: 20, force_alarm_on_end: true, max_alarms: 30, delay_reply_enabled: true, is_ai_editable: true, hide_ai_identity: true, reminder_grace: 'off', memory_load_mode: 'index_plus_semantic', memory_recent_count: 10 },
}

const PROFILE_OPTIONS = [
  { value: 'chat', label: 'agentDetail.profileChat', desc: 'agentDetail.profileChatDesc', color: 'bg-blue-500/10 text-blue-400 border-blue-400/30' },
  { value: 'immersive', label: 'agentDetail.profileImmersive', desc: 'agentDetail.profileImmersiveDesc', color: 'bg-accent-500/10 text-accent-400 border-accent-400/30' },
  { value: 'digital_life', label: 'agentDetail.profileDigitalLife', desc: 'agentDetail.profileDigitalLifeDesc', color: 'bg-primary-500/10 text-primary-400 border-primary-400/30' },
]

/** API Key 获取链接 */
function ApiKeyGetLink({ providers }: { providers?: ProviderItem[] }) {
  const defaultProvider = providers?.find(p => p.is_default) || providers?.[0]
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

/** 模型选项渲染（按供应商分组）*/
function renderGroupedModels(models: ModelOption[], providers?: ProviderItem[]) {
  if (providers && providers.length > 0) {
    return providers.map(p => (
      <optgroup key={p.name} label={`${p.name}${p.is_default ? '（默认）' : ''}`}>
        {p.models.map(m => (
          <option key={m.value} value={m.value}>{m.label}</option>
        ))}
      </optgroup>
    ))
  }
  return models.map(m => <option key={m.value} value={m.value}>{m.label}</option>)
}

export default function AgentSettingsModal({
  agent, modelOptions, providers, defaults, thinkingSupported, isOpen, onClose, onSaved,
}: Props) {
  const t = useT()

  // ── 视图状态 ──
  const [view, setView] = useState<'main' | 'detailed'>('main')
  // QQ 通道配置项多，单独开弹窗（这里是入口）
  const [qqOpen, setQqOpen] = useState(false)

  // ── 主设置状态 ──
  const [name, setName] = useState(agent.name)
  const [systemPrompt, setSystemPrompt] = useState(agent.current_system_prompt || '')
  const [temperature, setTemperature] = useState(agent.current_temperature ?? 0.8)
  const [thinkingEnabled, setThinkingEnabled] = useState(agent.thinking_enabled)
  const [chatModel, setChatModel] = useState(agent.chat_model || '')
  const [workModel, setWorkModel] = useState(agent.work_model || '')
  const [maxToolRounds, setMaxToolRounds] = useState(agent.max_tool_rounds)
  const [delayReplyEnabled, setDelayReplyEnabled] = useState<boolean | null>(agent.delay_reply_enabled)
  const [allowOthersChat, setAllowOthersChat] = useState(agent.allow_others_chat ?? true)
  const [isPaused, setIsPaused] = useState(agent.is_paused ?? false)
  const [configProfile, setConfigProfile] = useState(agent.config_profile || 'chat')
  const [aiType, setAiType] = useState(agent.ai_type || 'resonance')

  // ── 详细设置状态 ──
  const [topP, setTopP] = useState(agent.current_top_p ?? 0.9)
  const [presencePenalty, setPresencePenalty] = useState(agent.current_presence_penalty ?? 0)
  const [frequencyPenalty, setFrequencyPenalty] = useState(agent.current_frequency_penalty ?? 0)
  const [hideAiIdentity, setHideAiIdentity] = useState(agent.hide_ai_identity)
  const [alarmMaxToolRounds, setAlarmMaxToolRounds] = useState(agent.alarm_max_tool_rounds)
  const [forceAlarmOnEnd, setForceAlarmOnEnd] = useState(agent.force_alarm_on_end)
  const [maxAlarms, setMaxAlarms] = useState(agent.max_alarms)
  const [autoDndThreshold, setAutoDndThreshold] = useState(agent.auto_dnd_threshold ?? 20)
  const [autoDndDuration, setAutoDndDuration] = useState(agent.auto_dnd_duration ?? 5)
  const [memoryLoadMode, setMemoryLoadMode] = useState(agent.memory_load_mode || 'index_only')
  const [memoryRecentCount, setMemoryRecentCount] = useState(agent.memory_recent_count || 0)
  const [memorySharedScope, setMemorySharedScope] = useState(agent.memory_shared_scope || 'private_only')
  const [conversationLogsLimit, setConversationLogsLimit] = useState<number | null>(agent.conversation_logs_limit ?? null)
  const [userCanViewLogs, setUserCanViewLogs] = useState<boolean | null>(agent.user_can_view_logs ?? null)
  const [isAiEditable, setIsAiEditable] = useState(agent.is_ai_editable)
  const [reminderGrace, setReminderGrace] = useState(agent.reminder_grace || 'every_time')
  const [discoverable, setDiscoverable] = useState(agent.discoverable ?? true)
  const [allowFriendRequests, setAllowFriendRequests] = useState(agent.allow_friend_requests)
  const [autoRespondFriendRequest, setAutoRespondFriendRequest] = useState(agent.auto_respond_friend_request)
  const [othersChatMode, setOthersChatMode] = useState(agent.others_chat_mode || 'unlimited')
  const [othersChatQuota, setOthersChatQuota] = useState(agent.others_chat_quota ?? 30)
  const [othersChatUsed, setOthersChatUsed] = useState(agent.others_chat_used ?? 0)
  const [disallowMode, setDisallowMode] = useState(agent.disallow_mode || 'strict')
  const [apiCreditCost, setApiCreditCost] = useState(agent.api_credit_cost || 0)
  const [apiBaseUrl, setApiBaseUrl] = useState(agent.api_base_url || '')
  const [apiKey, setApiKey] = useState('')
  const [bio, setBio] = useState(agent.bio || '')
  const [statusText, setStatusText] = useState(agent.status_text || '')
  const [statusColor, setStatusColor] = useState(agent.status_color || '')
  const [autoResetQuota, setAutoResetQuota] = useState(agent.auto_reset_quota ?? false)
  const [groupOwnerPays, setGroupOwnerPays] = useState(agent.group_owner_pays ?? true)

  // ── AI↔AI 私信限额（0 = 不启用）──
  const dmCfg = agent.dm_quota_config || {}
  const [dmSendDaily, setDmSendDaily] = useState(dmCfg.send?.daily ?? 20)
  const [dmSendWeekly, setDmSendWeekly] = useState(dmCfg.send?.weekly ?? 0)
  const [dmSendCreatorChat, setDmSendCreatorChat] = useState(dmCfg.send?.creator_chat ?? 0)
  const [dmRecvDaily, setDmRecvDaily] = useState(dmCfg.receive?.daily ?? 20)
  const [dmRecvWeekly, setDmRecvWeekly] = useState(dmCfg.receive?.weekly ?? 0)
  const [dmRecvCreatorChat, setDmRecvCreatorChat] = useState(dmCfg.receive?.creator_chat ?? 0)

  // ── UI 状态 ──
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState('')
  const [saveOk, setSaveOk] = useState<boolean | null>(null)
  const [testingApi, setTestingApi] = useState(false)
  const [testApiMsg, setTestApiMsg] = useState('')
  const [testApiOk, setTestApiOk] = useState<boolean | null>(null)
  const [redeemCode, setRedeemCode] = useState('')
  const [redeeming, setRedeeming] = useState(false)
  const [redeemMsg, setRedeemMsg] = useState('')
  const [redeemOk, setRedeemOk] = useState<boolean | null>(null)

  // ── 应用预设 ──
  const applyPreset = (profile: string) => {
    setConfigProfile(profile)
    if (profile === 'custom') return
    const p = PRESET_DEFAULTS[profile]
    if (!p) return
    if (p.temperature !== undefined) setTemperature(p.temperature)
    if (p.thinking_enabled !== undefined) setThinkingEnabled(p.thinking_enabled)
    if (p.max_tool_rounds !== undefined) setMaxToolRounds(p.max_tool_rounds)
    if (p.alarm_max_tool_rounds !== undefined) setAlarmMaxToolRounds(p.alarm_max_tool_rounds)
    if (p.force_alarm_on_end !== undefined) setForceAlarmOnEnd(p.force_alarm_on_end)
    if (p.max_alarms !== undefined) setMaxAlarms(p.max_alarms)
    if (p.delay_reply_enabled !== undefined) setDelayReplyEnabled(p.delay_reply_enabled)
    if (p.hide_ai_identity !== undefined) setHideAiIdentity(p.hide_ai_identity)
    if (p.is_ai_editable !== undefined) setIsAiEditable(p.is_ai_editable)
    if (p.reminder_grace !== undefined) setReminderGrace(p.reminder_grace)
    if (p.memory_load_mode !== undefined) setMemoryLoadMode(p.memory_load_mode)
    if (p.memory_recent_count !== undefined) setMemoryRecentCount(p.memory_recent_count)
  }

  const handleTestApi = async () => {
    setTestingApi(true)
    setTestApiMsg('')
    try {
      const data = await api.post<{ ok: boolean; message: string }>('/user/test-api-connection', {
        api_base_url: apiBaseUrl || null,
        api_key: apiKey || null,
      })
      setTestApiOk(data.ok)
      setTestApiMsg(data.message || (data.ok ? t('modal.testSuccess') : t('modal.testFailed')))
    } catch (err: any) {
      setTestApiOk(false)
      setTestApiMsg(err.message || t('error.testFailed'))
    } finally {
      setTestingApi(false)
    }
  }

  const handleRedeem = async () => {
    if (!redeemCode.trim()) return
    setRedeeming(true)
    setRedeemMsg('')
    setRedeemOk(null)
    try {
      const data = await api.post<{ message: string }>('/user/redeem', { code: redeemCode.trim() })
      setRedeemOk(true)
      setRedeemMsg(data.message || t('modal.redeemSuccess'))
      setRedeemCode('')
    } catch (err: any) {
      setRedeemOk(false)
      setRedeemMsg(err.message || t('modal.redeemFailed'))
    } finally {
      setRedeeming(false)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    setSaveMsg('')
    setSaveOk(null)
    try {
      const payload: Record<string, any> = {
        name,
        system_prompt: systemPrompt || null,
        temperature,
        top_p: topP,
        presence_penalty: presencePenalty,
        frequency_penalty: frequencyPenalty,
        thinking_enabled: thinkingEnabled,
        hide_ai_identity: hideAiIdentity,
        chat_model: chatModel || null,
        work_model: workModel || null,
        max_tool_rounds: maxToolRounds,
        alarm_max_tool_rounds: alarmMaxToolRounds,
        force_alarm_on_end: forceAlarmOnEnd,
        max_alarms: maxAlarms,
        memory_load_mode: memoryLoadMode,
        memory_recent_count: memoryRecentCount,
        memory_shared_scope: memorySharedScope,
        ai_type: aiType,
        delay_reply_enabled: delayReplyEnabled,
        is_ai_editable: isAiEditable,
        allow_friend_requests: allowFriendRequests,
        auto_respond_friend_request: autoRespondFriendRequest,
        reminder_grace: reminderGrace,
        discoverable,
        allow_others_chat: allowOthersChat,
        others_chat_mode: othersChatMode,
        others_chat_quota: othersChatQuota,
        others_chat_used: othersChatUsed,
        disallow_mode: disallowMode,
        api_credit_cost: apiCreditCost,
        api_base_url: apiBaseUrl || null,
        config_profile: configProfile,
        is_paused: isPaused,
        auto_dnd_threshold: autoDndThreshold,
        auto_dnd_duration: autoDndDuration,
        conversation_logs_limit: conversationLogsLimit,
        user_can_view_logs: userCanViewLogs,
        auto_reset_quota: autoResetQuota,
        group_owner_pays: groupOwnerPays,
        dm_quota_config: {
          send: { daily: dmSendDaily, weekly: dmSendWeekly, creator_chat: dmSendCreatorChat },
          receive: { daily: dmRecvDaily, weekly: dmRecvWeekly, creator_chat: dmRecvCreatorChat },
        },
        bio: bio || null,
        status_text: statusText || null,
        status_color: statusColor || null,
      }
      if (apiKey.trim()) {
        payload.api_key = apiKey.trim()
      }
      await api.put(`/agents/${agent.id}/config`, payload)
      setSaveOk(true)
      setSaveMsg(t('modal.detailSettingsSaveAndClose'))
      setTimeout(() => {
        onSaved()
        onClose()
      }, 600)
    } catch (err: any) {
      setSaveOk(false)
      setSaveMsg(err.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  if (!isOpen) return null

  return (
    <>
    <div className="fixed inset-0 md:bg-black/70 flex items-start justify-center z-toast md:pt-8 overflow-y-auto bg-surface" onClick={onClose}>
      <div
        className="bg-elevated border border-border rounded-none md:rounded-dialog p-6 w-full max-w-full md:max-w-4xl lg:max-w-5xl mx-0 md:mx-4 shadow-2xl shadow-black/30 my-0 md:my-4 h-full md:h-auto flex flex-col pb-0 md:pb-6"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 移动端头部 */}
        <div className="flex items-center justify-between mb-5 md:hidden shrink-0">
          {view === 'detailed' ? (
            <button onClick={() => setView('main')} className="p-1 -ml-1 rounded-control hover:bg-elevated text-textSecondary transition-colors">
              <ArrowLeft size={20} />
            </button>
          ) : (
            <button onClick={onClose} className="icon-btn-sm -ml-1 text-textSecondary">
              <ArrowLeft size={20} />
            </button>
          )}
          <h2 className="text-base font-semibold text-textPrimary">
            {view === 'main' ? t('modal.mainSettingsTitle') : t('modal.detailSettingsTitle')}
          </h2>
          <div className="w-6" />
        </div>

        {/* 桌面端头部 */}
        <div className="hidden md:flex items-center justify-between mb-5">
          <div className="flex items-center gap-3">
            {view === 'detailed' && (
              <button onClick={() => setView('main')} className="p-1 -ml-1 rounded-control hover:bg-elevated text-textSecondary transition-colors">
                <ArrowLeft size={18} />
              </button>
            )}
            <h2 className="text-base font-semibold text-textPrimary">
              {view === 'main' ? t('modal.mainSettingsTitle') : t('modal.detailSettingsTitle')}
            </h2>
          </div>
          <button onClick={onClose} className="text-textMuted hover:text-textSecondary transition-colors">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-5 flex-1 overflow-y-auto md:max-h-[78vh] pr-1 pb-[var(--safe-bottom)] md:pb-0">

          {/* ═══════════════════════════════════════════ 主设置 ═══════════════════════════════════════════ */}
          {view === 'main' && (
            <>
              <p className="text-3xs text-textMuted -mb-3">{t('modal.mainSettingsDesc')}</p>

              {/* 基础信息 */}
              <Section title={t('modal.detailSettingsBasicInfo')} desc={t('modal.detailSettingsBasicInfoDesc')}>
                <div>
                  <label className="block text-xs font-medium mb-1 text-textSecondary">{t('chat.groupName')}</label>
                  <input type="text" value={name} onChange={(e) => setName(e.target.value)}
                    className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50" />
                </div>
                <div>
                  <label className="block text-xs font-medium mb-1 text-textSecondary">{t('modal.mainSettingsProfile')}</label>
                  <div className="grid grid-cols-4 gap-2">
                    {PROFILE_OPTIONS.map((opt) => (
                      <button key={opt.value} type="button"
                        onClick={() => applyPreset(opt.value)}
                        className={`flex flex-col items-center gap-1 p-2 rounded-card border text-center transition-all ${
                          configProfile === opt.value
                            ? `border-primary-400 bg-primary-500/10 text-primary-600 dark:text-primary-300`
                            : 'border-border bg-canvas text-textSecondary hover:bg-elevated'
                        }`}
                      >
                        <span className="text-2xs font-semibold">{t(opt.label)}</span>
                        <span className="text-[8px] leading-tight text-textMuted">{t(opt.desc)}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="block text-xs font-medium mb-1 text-textSecondary">{t('modal.createAgentSystemPrompt')}</label>
                  <textarea value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)} rows={3}
                    className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50 resize-none" />
                </div>
              </Section>

              {/* 个人资料 */}
              <Section title={t('modal.detailSettingsProfileInfo')} desc={t('modal.detailSettingsProfileInfoDesc')}>
                <div>
                  <label className="block text-xs font-medium mb-1 text-textSecondary">{t('agentDetail.bioLabel')}</label>
                  <textarea value={bio} onChange={(e) => setBio(e.target.value)} rows={3} maxLength={500}
                    className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50 resize-none" />
                  <p className="text-3xs text-textMuted mt-0.5">{bio.length}/500</p>
                </div>
                <div>
                  <label className="block text-xs font-medium mb-1 text-textSecondary">{t('agentDetail.statusTextLabel')}</label>
                  <input type="text" value={statusText} onChange={(e) => setStatusText(e.target.value)} maxLength={100}
                    className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50" />
                </div>
                <div>
                  <label className="block text-xs font-medium mb-1 text-textSecondary">{t('me.statusColorLabel')}</label>
                  <div className="flex items-center gap-2 flex-wrap">
                    {STATUS_COLORS.map(c => (
                      <button key={c.value} type="button" onClick={() => setStatusColor(c.value)}
                        className={`w-6 h-6 rounded-full border-2 transition-all ${
                          statusColor === c.value ? 'border-primary-400 scale-110 shadow-md'
                          : c.value === '' ? 'border-border bg-canvas hover:border-textMuted'
                          : 'border-transparent hover:scale-105'
                        }`}
                        style={c.value ? { backgroundColor: c.value } : undefined}
                        title={c.label}
                      >
                        {c.value === '' && <X size={10} className="text-textMuted m-auto" />}
                      </button>
                    ))}
                    <div className="relative">
                      <input type="color" value={statusColor || '#000000'} onChange={e => setStatusColor(e.target.value)}
                        className="w-6 h-6 rounded-full cursor-pointer border-2 border-border hover:border-primary-400 transition-colors"
                        title={t('me.statusColorCustom')} />
                    </div>
                  </div>
                </div>
              </Section>

              {/* AI 类型 */}
              <Section title={t('modal.detailSettingsAiType')} desc={t('modal.detailSettingsAiTypeDesc')}>
                <div className="grid grid-cols-3 gap-2">
                  {([
                    { value: 'general', label: t('modal.detailSettingsAiTypeGeneral'), desc: t('modal.detailSettingsAiTypeGeneralDesc') },
                    { value: 'semi_general', label: t('modal.detailSettingsAiTypeSemiGeneral'), desc: t('modal.detailSettingsAiTypeSemiGeneralDesc') },
                    { value: 'resonance', label: t('modal.detailSettingsAiTypeResonance'), desc: t('modal.detailSettingsAiTypeResonanceDesc') },
                  ] as const).map((type) => (
                    <button key={type.value} type="button" onClick={() => setAiType(type.value)}
                      className={`flex flex-col items-center gap-1 p-2.5 rounded-card border text-center transition-all ${
                        aiType === type.value
                          ? 'border-primary-400 bg-primary-500/10 text-primary-600 dark:text-primary-300'
                          : 'border-border bg-canvas text-textSecondary hover:bg-elevated'
                      }`}
                    >
                      <span className="text-xs font-semibold">{type.label}</span>
                      <span className="text-[9px] leading-tight text-textMuted">{type.desc}</span>
                    </button>
                  ))}
                </div>
              </Section>

              {/* 模型与行为 */}
              <Section title={t('modal.mainSettingsModelBehavior')} desc={t('modal.mainSettingsModelBehaviorDesc')}>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium mb-1 text-textSecondary">
                      {t('modal.detailSettingsChatModel')} <span className="text-textMuted">({t('modal.detailSettingsDefaultLabel')} {defaults.chat_model})</span>
                    </label>
                    <select value={chatModel} onChange={(e) => setChatModel(e.target.value)}
                      className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50">
                      <option value="">{t('modal.detailSettingsGlobalDefault')}</option>
                      {renderGroupedModels(modelOptions, providers)}
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs font-medium mb-1 text-textSecondary">
                      {t('modal.detailSettingsWorkModel')} <span className="text-textMuted">({t('modal.detailSettingsDefaultLabel')} {defaults.work_model})</span>
                    </label>
                    <select value={workModel} onChange={(e) => setWorkModel(e.target.value)}
                      className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50">
                      <option value="">{t('modal.detailSettingsGlobalDefault')}</option>
                      {renderGroupedModels(modelOptions, providers)}
                    </select>
                  </div>
                </div>
                <SliderField label="Temperature" value={temperature} setValue={setTemperature} min={0} max={2} step={0.1} desc={t('modal.detailSettingsTemperatureDesc')} />
                {thinkingSupported && (
                  <ToggleField label={t('modal.detailSettingsThinkingMode')} value={thinkingEnabled} setValue={setThinkingEnabled} desc={t('modal.detailSettingsThinkingModeDesc')} />
                )}
                <NumberField label={t('modal.detailSettingsMaxToolRounds')} value={maxToolRounds} setValue={setMaxToolRounds} min={1} max={20} desc={t('modal.detailSettingsMaxToolRoundsDesc')} />
                <div>
                  <label className="block text-xs font-medium mb-1 text-textSecondary">{t('modal.detailSettingsDelayReply')}</label>
                  <select
                    value={delayReplyEnabled === null ? 'inherit' : delayReplyEnabled ? 'on' : 'off'}
                    onChange={(e) => { const v = e.target.value; setDelayReplyEnabled(v === 'inherit' ? null : v === 'on') }}
                    className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                  >
                    <option value="inherit">{t('modal.detailSettingsInheritGlobal')}</option>
                    <option value="on">{t('common.enabled')}</option>
                    <option value="off">{t('common.disabled')}</option>
                  </select>
                </div>
              </Section>

              {/* 权限 */}
              <Section title={t('modal.mainSettingsPermissions')} desc={t('modal.mainSettingsPermissionsDesc')}>
                <ToggleField label={t('agents.allowOthersChat')} value={allowOthersChat} setValue={setAllowOthersChat} desc={t('agents.allowOthersChatDesc')} />
                <ToggleField label={t('agents.discoverable')} value={discoverable} setValue={setDiscoverable} desc={t('agents.discoverableDesc')} />
                <ToggleField label={t('modal.detailSettingsAllowFriendRequests')} value={allowFriendRequests} setValue={setAllowFriendRequests} desc={t('modal.detailSettingsAllowFriendRequestsDesc')} />
                {allowFriendRequests && (
                  <ToggleField label={t('modal.detailSettingsAutoRespondFriendRequest')} value={autoRespondFriendRequest} setValue={setAutoRespondFriendRequest} desc={t('modal.detailSettingsAutoRespondFriendRequestDesc')} />
                )}
                <ToggleField label={t('modal.mainSettingsIsPaused')} value={isPaused} setValue={setIsPaused} desc={t('modal.mainSettingsIsPausedDesc')} />
              </Section>
            </>
          )}

          {/* ═══════════════════════════════════════════ 详细设置 ═══════════════════════════════════════════ */}
          {view === 'detailed' && (
            <>
              <p className="text-3xs text-textMuted -mb-3">{t('modal.detailSettingsDesc')}</p>

              {/* 高级模型参数 */}
              <Section title={t('modal.detailSettingsAdvancedModel')} desc={t('modal.detailSettingsAdvancedModelDesc')} defaultCollapsed>
                <SliderField label="Top P" value={topP} setValue={setTopP} min={0} max={1} step={0.05} desc={t('modal.detailSettingsTopPDesc')} />
                <SliderField label="Presence Penalty" value={presencePenalty} setValue={setPresencePenalty} min={-2} max={2} step={0.1} desc={t('modal.detailSettingsPresencePenaltyDesc')} />
                <SliderField label="Frequency Penalty" value={frequencyPenalty} setValue={setFrequencyPenalty} min={-2} max={2} step={0.1} desc={t('modal.detailSettingsFrequencyPenaltyDesc')} />
              </Section>

              {/* 工具调用 */}
              <Section title={t('modal.detailSettingsToolCalls')} desc={t('modal.detailSettingsToolCallsDesc')}>
                <div className="grid grid-cols-2 gap-3">
                  <NumberField label={t('modal.detailSettingsAlarmRounds')} value={alarmMaxToolRounds} setValue={setAlarmMaxToolRounds} min={1} max={30} desc={t('modal.detailSettingsAlarmRoundsDesc')} />
                </div>
              </Section>

              {/* 闹钟 / 心跳 */}
              <Section title={t('modal.detailSettingsAlarm')} desc={t('modal.detailSettingsAlarmDesc')}>
                <ToggleField label={t('modal.detailSettingsForceAlarm')} value={forceAlarmOnEnd} setValue={setForceAlarmOnEnd} desc={t('modal.detailSettingsForceAlarmDesc')} />
                <NumberField label={t('modal.detailSettingsMaxAlarms')} value={maxAlarms} setValue={setMaxAlarms} min={1} max={50} desc={t('modal.detailSettingsMaxAlarmsDesc')} />
              </Section>

              {/* 自动免打扰 (NEW) */}
              <Section title={t('modal.detailSettingsAutoDnd')} desc={t('modal.detailSettingsAutoDndDesc')}>
                <SliderField label={t('modal.detailSettingsAutoDndThreshold')} value={autoDndThreshold} setValue={setAutoDndThreshold} min={0} max={100} step={5} desc={t('modal.detailSettingsAutoDndThresholdDesc')} />
                <NumberField label={t('modal.detailSettingsAutoDndDuration')} value={autoDndDuration} setValue={setAutoDndDuration} min={1} max={1440} desc={t('modal.detailSettingsAutoDndDurationDesc')} />
              </Section>

              {/* 文件记忆 */}
              <Section title={t('modal.detailSettingsFileMemory')} desc={t('modal.detailSettingsFileMemoryDesc')}>
                <div>
                  <label className="block text-xs font-medium mb-1 text-textSecondary">{t('modal.detailSettingsMemoryLoadMode')}</label>
                  <select value={memoryLoadMode} onChange={(e) => setMemoryLoadMode(e.target.value)}
                    className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50">
                    <option value="index_only">{t('modal.detailSettingsMemoryLoadModeIndexOnly')}</option>
                    <option value="index_plus_recent">{t('modal.detailSettingsMemoryLoadModeIndexRecent')}</option>
                    <option value="index_plus_semantic">{t('modal.detailSettingsMemoryLoadModeIndexSemantic')}</option>
                  </select>
                </div>
                {memoryLoadMode === 'index_plus_recent' && (
                  <NumberField label={t('modal.detailSettingsMemoryRecentCount')} value={memoryRecentCount} setValue={setMemoryRecentCount} min={0} max={50} desc={t('modal.detailSettingsMemoryRecentCountDesc')} />
                )}
                <div>
                  <label className="block text-xs font-medium mb-1 text-textSecondary">{t('modal.detailSettingsMemorySharedScope')}</label>
                  <select value={memorySharedScope} onChange={(e) => setMemorySharedScope(e.target.value)}
                    className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50">
                    <option value="private_only">{t('modal.detailSettingsMemorySharedScopePrivate')}</option>
                    <option value="private_plus_shared_by_user">{t('modal.detailSettingsMemorySharedScopeByUser')}</option>
                    <option value="private_plus_shared_all">{t('modal.detailSettingsMemorySharedScopeAll')}</option>
                  </select>
                </div>
              </Section>

              {/* 对话日志 (NEW) */}
              <Section title={t('modal.detailSettingsConversationLogs')} desc={t('modal.detailSettingsConversationLogsDesc')} defaultCollapsed>
                <div>
                  <label className="block text-xs font-medium mb-1 text-textSecondary">{t('modal.detailSettingsConversationLogsLimit')}</label>
                  <input type="number" min={1} max={10000} value={conversationLogsLimit ?? ''}
                    onChange={(e) => setConversationLogsLimit(e.target.value ? parseInt(e.target.value) : null)}
                    placeholder={t('modal.detailSettingsConversationLogsLimitDesc')}
                    className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50" />
                  <p className="text-3xs text-textMuted mt-0.5">{t('modal.detailSettingsConversationLogsLimitDesc')}</p>
                </div>
                <div>
                  <label className="block text-xs font-medium mb-1 text-textSecondary">{t('modal.detailSettingsUserCanViewLogs')}</label>
                  <select
                    value={userCanViewLogs === null ? 'inherit' : userCanViewLogs ? 'on' : 'off'}
                    onChange={(e) => { const v = e.target.value; setUserCanViewLogs(v === 'inherit' ? null : v === 'on') }}
                    className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                  >
                    <option value="inherit">{t('modal.detailSettingsInheritGlobal')}</option>
                    <option value="on">{t('common.enabled')}</option>
                    <option value="off">{t('common.disabled')}</option>
                  </select>
                  <p className="text-3xs text-textMuted mt-0.5">{t('modal.detailSettingsUserCanViewLogsDesc')}</p>
                </div>
              </Section>

              {/* 行为开关 */}
              <Section title={t('modal.detailSettingsBehaviorSwitches')} desc={t('modal.detailSettingsBehaviorSwitchesDesc')} defaultCollapsed>
                <ToggleField label={t('modal.detailSettingsSelfEdit')} value={isAiEditable} setValue={setIsAiEditable} desc={t('modal.detailSettingsSelfEditDesc')} />
                <ToggleField label={t('modal.detailSettingsHideAiIdentity')} value={hideAiIdentity} setValue={setHideAiIdentity} desc={t('modal.detailSettingsHideAiIdentityDesc')} />
                <div>
                  <label className="block text-xs font-medium mb-1 text-textSecondary">{t('modal.detailSettingsReminderGrace')}</label>
                  <select value={reminderGrace} onChange={(e) => setReminderGrace(e.target.value)}
                    className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50">
                    <option value="every_time">{t('modal.detailSettingsReminderGraceEvery')}</option>
                    <option value="once">{t('modal.detailSettingsReminderGraceOnce')}</option>
                    <option value="off">{t('modal.detailSettingsReminderGraceOff')}</option>
                  </select>
                </div>
              </Section>

              {/* ── 合并：对话与社交权限 ── */}
              <Section title={t('modal.detailSettingsChatPermissionsDetail')} desc={t('modal.detailSettingsChatPermissionsDetailDesc')} defaultCollapsed>
                {/* 社交发现 */}
                <ToggleField label={t('agents.discoverable')} value={discoverable} setValue={setDiscoverable} desc={t('agents.discoverableDesc')} />
                <ToggleField label={t('modal.detailSettingsAllowFriendRequests')} value={allowFriendRequests} setValue={setAllowFriendRequests} desc={t('modal.detailSettingsAllowFriendRequestsDesc')} />
                {allowFriendRequests && (
                  <ToggleField label={t('modal.detailSettingsAutoRespondFriendRequest')} value={autoRespondFriendRequest} setValue={setAutoRespondFriendRequest} desc={t('modal.detailSettingsAutoRespondFriendRequestDesc')} />
                )}

                {/* 对话权限 - 允许时 */}
                <div className="mt-3 pt-3 border-t border-border/40">
                  <ToggleField label={t('agents.allowOthersChat')} value={allowOthersChat} setValue={setAllowOthersChat} desc={t('agents.allowOthersChatDesc')} />
                </div>
                {allowOthersChat ? (
                  <div className="ml-4 pl-3 border-l-2 border-primary-400/30 space-y-2 mt-1">
                    <div className="flex items-center gap-3">
                      <label className="flex items-center gap-1.5 cursor-pointer">
                        <input type="radio" name="othersChatMode" value="unlimited" checked={othersChatMode === 'unlimited'} onChange={() => setOthersChatMode('unlimited')} className="text-primary-500" />
                        <span className="text-xs text-textSecondary">{t('agents.othersChatUnlimited')}</span>
                      </label>
                      <label className="flex items-center gap-1.5 cursor-pointer">
                        <input type="radio" name="othersChatMode" value="quota" checked={othersChatMode === 'quota'} onChange={() => setOthersChatMode('quota')} className="text-primary-500" />
                        <span className="text-xs text-textSecondary">{t('agents.othersChatQuota')}</span>
                      </label>
                    </div>
                    {othersChatMode === 'quota' && (
                      <>
                        <div className="flex items-center gap-3">
                          <NumberField label={t('agents.othersChatQuotaLabel')} value={othersChatQuota} setValue={setOthersChatQuota} min={1} max={9999} />
                          <div className="flex items-center gap-2 pt-5">
                            <span className="text-2xs text-textMuted">{t('agents.othersChatUsed')}: {othersChatUsed}</span>
                            <button type="button"
                              onClick={async () => { try { await api.post(`/agents/${agent.id}/reset-others-chat-used`); setOthersChatUsed(0) } catch { /* ignore */ } }}
                              className="text-3xs px-2 py-0.5 rounded border border-border text-textMuted hover:text-textSecondary transition-colors"
                            >{t('agents.othersChatUsedReset')}</button>
                          </div>
                          <p className="text-3xs text-textMuted leading-relaxed">{t('agents.othersChatQuotaDesc')}</p>
                        </div>
                        <ToggleField label={t('agents.autoResetQuota')} value={autoResetQuota} setValue={setAutoResetQuota} desc={t('agents.autoResetQuotaDesc')} />
                      </>
                    )}
                    {/* 群聊付费 */}
                    <ToggleField label={t('agents.groupOwnerPays')} value={groupOwnerPays} setValue={setGroupOwnerPays} desc={t('agents.groupOwnerPaysDesc')} />
                  </div>
                ) : (
                  <div className="ml-4 pl-3 border-l-2 border-rose-400/30 space-y-2 mt-1">
                    <label className="text-2xs font-medium text-textMuted mb-2 block">{t('agents.disallowModeLabel')}</label>
                    <div className="flex items-center gap-3">
                      <label className="flex items-center gap-1.5 cursor-pointer">
                        <input type="radio" name="disallowMode" value="strict" checked={disallowMode === 'strict'} onChange={() => setDisallowMode('strict')} className="text-primary-500" />
                        <span className="text-xs text-textSecondary">{t('agents.disallowStrict')}</span>
                      </label>
                      <label className="flex items-center gap-1.5 cursor-pointer">
                        <input type="radio" name="disallowMode" value="own_key" checked={disallowMode === 'own_key'} onChange={() => setDisallowMode('own_key')} className="text-primary-500" />
                        <span className="text-xs text-textSecondary">{t('agents.disallowOwnKey')}</span>
                      </label>
                    </div>
                    {disallowMode === 'own_key' && (
                      <p className="text-3xs text-textMuted leading-relaxed">{t('agents.disallowOwnKeyDesc')}</p>
                    )}
                  </div>
                )}

                {/* AI↔AI 私信限额（2026-08-09） */}
                <div className="mt-4 pt-3 border-t border-border/40">
                  <label className="block text-xs font-medium mb-1 text-textSecondary">{t('agents.dmQuotaTitle')}</label>
                  <p className="text-3xs text-textMuted leading-relaxed mb-2">{t('agents.dmQuotaDesc')}</p>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-2">
                      <span className="text-2xs font-medium text-textSecondary block">{t('agents.dmQuotaSend')}</span>
                      <NumberField label={t('agents.dmQuotaDaily')} value={dmSendDaily} setValue={setDmSendDaily} min={0} max={9999} />
                      <NumberField label={t('agents.dmQuotaWeekly')} value={dmSendWeekly} setValue={setDmSendWeekly} min={0} max={9999} />
                      <NumberField label={t('agents.dmQuotaCreatorChat')} value={dmSendCreatorChat} setValue={setDmSendCreatorChat} min={0} max={9999} />
                    </div>
                    <div className="space-y-2">
                      <span className="text-2xs font-medium text-textSecondary block">{t('agents.dmQuotaReceive')}</span>
                      <NumberField label={t('agents.dmQuotaDaily')} value={dmRecvDaily} setValue={setDmRecvDaily} min={0} max={9999} />
                      <NumberField label={t('agents.dmQuotaWeekly')} value={dmRecvWeekly} setValue={setDmRecvWeekly} min={0} max={9999} />
                      <NumberField label={t('agents.dmQuotaCreatorChat')} value={dmRecvCreatorChat} setValue={setDmRecvCreatorChat} min={0} max={9999} />
                    </div>
                  </div>
                </div>
              </Section>

              {/* 额度 */}
              <Section title={t('modal.detailSettingsCreditCost')} desc={t('modal.detailSettingsCreditCostDesc')} defaultCollapsed>
                <NumberField label={t('modal.detailSettingsApiCreditCost')} value={apiCreditCost} setValue={setApiCreditCost} min={0} max={100000} desc={t('modal.detailSettingsApiCreditCostDesc')} />
              </Section>

              {/* API 提供商 */}
              <Section title={t('modal.detailSettingsApiProvider')} desc={t('modal.detailSettingsApiProviderDesc')} defaultCollapsed>
                <div>
                  <label className="block text-xs font-medium mb-1 text-textSecondary">API Base URL</label>
                  <input type="text" value={apiBaseUrl} onChange={(e) => setApiBaseUrl(e.target.value)}
                    className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                    placeholder={t('modal.detailSettingsApiBaseUrlPlaceholder')} />
                </div>
                <div>
                  <label className="block text-xs font-medium mb-1 text-textSecondary flex items-center gap-2">
                    API Key
                    <ApiKeyGetLink providers={providers} />
                  </label>
                  <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="off"
                    className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                    placeholder={agent.has_api_key ? '•••••••• (unchanged if empty)' : t('modal.detailSettingsApiKeyPlaceholder')} />
                </div>
                <button onClick={handleTestApi} disabled={testingApi || (!apiBaseUrl.trim() && !apiKey.trim())}
                  className="btn btn-xs btn-outline gap-1.5"
                >
                  {testingApi ? <Loader2 size={12} className="animate-spin" /> : <RotateCw size={12} />}
                  {t('settings.testConnection')}
                </button>
                {testApiMsg && (
                  <p className={`text-xs ${testApiOk === true ? 'text-mint-400' : 'text-rose-400'}`}>{testApiMsg}</p>
                )}
              </Section>

              {/* 技能背包 */}
              <Section title={t('backpack.title')} desc={t('backpack.desc')} defaultCollapsed>
                <SkillBackpack agentId={agent.id} />
              </Section>

              {/* QQ 通道：配置项多，单独开一个弹窗（这里是入口，不是表单本体） */}
              <Section title={t('tool:channel.title')} desc={t('tool:channel.desc')}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs text-textSecondary flex-1 min-w-[12rem]">{t('tool:channel.entryHint')}</span>
                  <button onClick={() => setQqOpen(true)} className="btn btn-sm btn-outline gap-1.5 shrink-0">
                    <MessageSquare size={13} /> {t('tool:channel.configure')}
                  </button>
                </div>
              </Section>

              {/* 兑换码 */}
              <Section title={t('modal.detailSettingsRedeemCode')} desc={t('modal.detailSettingsRedeemCodeDesc')} defaultCollapsed>
                <div className="flex items-center gap-2">
                  <div className="flex-1 relative">
                    <Ticket size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-textMuted" />
                    <input type="text" value={redeemCode} onChange={(e) => setRedeemCode(e.target.value)}
                      className="w-full pl-9 pr-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                      placeholder={t('modal.detailSettingsRedeemPlaceholder')} />
                  </div>
                  <button onClick={handleRedeem} disabled={redeeming || !redeemCode.trim()}
                    className="btn btn-sm btn-primary gap-1 shrink-0"
                  >
                    {redeeming ? <Loader2 size={14} className="animate-spin" /> : <span>{t('me.redeem')}</span>}
                  </button>
                </div>
                {redeemMsg && (
                  <p className={`text-xs ${redeemOk === false ? 'text-rose-400' : 'text-mint-400'}`}>{redeemMsg}</p>
                )}
              </Section>
            </>
          )}
        </div>

        {/* 保存 / 取消 + 视图切换 */}
        <div className="sticky bottom-0 bg-elevated pt-3 pb-[var(--safe-bottom)] md:static md:pt-5 md:pb-0">
        {saveMsg && (
          <p className={`text-xs text-center ${saveOk === false ? 'text-rose-400' : 'text-mint-400'}`}>{saveMsg}</p>
        )}
        <div className="flex gap-3">
          {view === 'main' ? (
            <button onClick={() => setView('detailed')}
              className="flex-1 py-2.5 text-sm border border-primary-400/30 text-primary-500 rounded-card hover:bg-primary-500/10 font-medium transition-colors flex items-center justify-center gap-1"
            >
              {t('modal.goToDetailedSettings')} <ChevronRight size={14} />
            </button>
          ) : (
            <button onClick={() => setView('main')}
              className="flex-1 py-2.5 text-sm border border-border text-textSecondary rounded-card hover:bg-elevated font-medium transition-colors"
            >
              {t('modal.backToMainSettings')}
            </button>
          )}
          <button onClick={onClose}
            className="btn btn-md btn-outline flex-1"
          >
            {t('common.cancel')}
          </button>
          <button onClick={handleSave} disabled={saving}
            className="btn btn-md btn-primary flex-1"
          >
            {saving ? <Loader2 size={16} className="animate-spin mx-auto" /> : t('common.save')}
          </button>
        </div>
      </div>
    </div>
  </div>

    {/* QQ 通道：配置项多，单独一间屋子，别和这张长表单挤在一起 */}
    {qqOpen && <ChannelModal agentId={agent.id} onClose={() => setQqOpen(false)} />}
    </>
  )
}

// ── 分区容器 ──
function Section({ title, desc, children, defaultCollapsed }: { title: string; desc: string; children: React.ReactNode; defaultCollapsed?: boolean }) {
  const [open, setOpen] = useState(!defaultCollapsed)
  return (
    <div className="bg-canvas/50 rounded-card border border-border/50 overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-4 py-3 text-left hover:bg-black/[0.02] dark:hover:bg-white/[0.02] transition-colors"
      >
        <span className={`shrink-0 text-textMuted transition-transform ${open ? 'rotate-90' : ''}`}>▶</span>
        <div className="flex-1 min-w-0">
          <h3 className="text-xs font-semibold text-textPrimary">{title}</h3>
          <p className="text-3xs text-textMuted leading-relaxed">{desc}</p>
        </div>
      </button>
      {open && <div className="px-4 pb-4 space-y-2.5 border-t border-border/30 pt-3">{children}</div>}
    </div>
  )
}

// ── 滑块 ──
function SliderField({ label, value, setValue, min, max, step, desc }: {
  label: string; value: number; setValue: (v: number) => void
  min: number; max: number; step: number; desc?: string
}) {
  return (
    <div>
      <div className="flex justify-between mb-1">
        <label className="text-xs text-textSecondary">{label}</label>
        <span className="text-xs font-mono text-textPrimary">{value}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => setValue(parseFloat(e.target.value))}
        className="w-full" />
      {desc && <p className="text-3xs text-textMuted mt-0.5">{desc}</p>}
    </div>
  )
}

// ── 数字输入 ──
function NumberField({ label, value, setValue, min, max, desc }: {
  label: string; value: number; setValue: (v: number) => void
  min: number; max: number; desc?: string
}) {
  return (
    <div>
      <label className="block text-xs text-textSecondary mb-1">{label}</label>
      <input type="number" min={min} max={max} value={value}
        onChange={(e) => setValue(parseInt(e.target.value) || min)}
        className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50" />
      {desc && <p className="text-3xs text-textMuted mt-0.5">{desc}</p>}
    </div>
  )
}

// ── 开关（用标准 Toggle 组件） ──
function ToggleField({ label, value, setValue, desc }: {
  label: string; value: boolean; setValue: (v: boolean) => void; desc?: string
}) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex-1 min-w-0">
        <span className="text-xs text-textSecondary">{label}</span>
        {desc && <p className="text-3xs text-textMuted">{desc}</p>}
      </div>
      <Toggle checked={value} onChange={setValue} />
    </div>
  )
}
