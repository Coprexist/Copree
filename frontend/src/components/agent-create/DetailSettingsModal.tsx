import { useState } from 'react'
import { ArrowLeft, Loader2, RotateCw, Ticket, X } from 'lucide-react'
import { api } from '../../api/client'
import { useT } from '../../i18n/I18nContext'
import {
  AiTypeSelector, ApiKeyGetLink, NumberField, Section, SliderField, ToggleField,
  TristateSelect, renderModelOptions,
} from './fields'
import type { AgentFormApi, ModelOption, ProviderInfo } from './types'

/** 详细设置弹窗：只收一份表单 API（值 + setter），不再逐字段传参 */
export default function DetailSettingsModal({
  form, modelOptions, providers, defaults, thinkingSupported, onClose,
}: {
  form: AgentFormApi
  modelOptions: ModelOption[]
  providers: ProviderInfo[]
  defaults: { chat_model: string; work_model: string }
  thinkingSupported: boolean
  onClose: () => void
}) {
  // 表单只有父组件一份状态：这里解出「值 + setter」，与旧签名同名，改动面最小
  const {
    name, setName,
    systemPrompt, setSystemPrompt,
    temperature, setTemperature,
    topP, setTopP,
    presencePenalty, setPresencePenalty,
    frequencyPenalty, setFrequencyPenalty,
    thinkingEnabled, setThinkingEnabled,
    hideAiIdentity, setHideAiIdentity,
    delayReplyEnabled, setDelayReplyEnabled,
    maxToolRounds, setMaxToolRounds,
    alarmMaxToolRounds, setAlarmMaxToolRounds,
    forceAlarmOnEnd, setForceAlarmOnEnd,
    maxAlarms, setMaxAlarms,
    isAiEditable, setIsAiEditable,
    allowFriendRequests, setAllowFriendRequests,
    autoRespondFriendRequest, setAutoRespondFriendRequest,
    reminderGrace, setReminderGrace,
    discoverable, setDiscoverable,
    allowOthersChat, setAllowOthersChat,
    othersChatMode, setOthersChatMode,
    othersChatQuota, setOthersChatQuota,
    othersChatUsed, setOthersChatUsed,
    disallowMode, setDisallowMode,
    chatModel, setChatModel,
    workModel, setWorkModel,
    apiCreditCost, setApiCreditCost,
    aiType, setAiType,
    apiBaseUrl, setApiBaseUrl,
    apiKey, setApiKey,
    memoryLoadMode, setMemoryLoadMode,
    memoryRecentCount, setMemoryRecentCount,
    memorySharedScope, setMemorySharedScope,
    autoDndThreshold, setAutoDndThreshold,
    autoDndDuration, setAutoDndDuration,
    conversationLogsLimit, setConversationLogsLimit,
    userCanViewLogs, setUserCanViewLogs,
    autoResetQuota, setAutoResetQuota,
    groupOwnerPays, setGroupOwnerPays,
  } = form
  const t = useT()
  // 兑换码状态（弹窗内自管理）
  const [redeemCode, setRedeemCode] = useState('')
  const [redeeming, setRedeeming] = useState(false)
  const [redeemMsg, setRedeemMsg] = useState('')
  const [redeemOk, setRedeemOk] = useState<boolean | null>(null)
  const [testingApi, setTestingApi] = useState(false)
  const [testApiMsg, setTestApiMsg] = useState('')
  const [testApiOk, setTestApiOk] = useState<boolean | null>(null)

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

  return (
    <div className="fixed inset-0 md:bg-black/70 flex items-start justify-center z-toast md:pt-8 overflow-y-auto bg-surface" onClick={onClose}>
      <div
        className="bg-elevated border border-border rounded-none md:rounded-dialog p-6 w-full max-w-full md:max-w-2xl mx-0 md:mx-4 shadow-2xl shadow-black/30 my-0 md:my-4 h-full md:h-auto flex flex-col pb-[var(--safe-bottom)] md:pb-6"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 移动端头部 */}
        <div className="flex items-center justify-between mb-5 md:hidden shrink-0">
          <button onClick={onClose} className="icon-btn-sm -ml-1 text-textSecondary">
            <ArrowLeft size={20} />
          </button>
          <h2 className="text-base font-semibold text-textPrimary">{t('modal.detailSettingsTitle')}</h2>
          <div className="w-6" />
        </div>

        {/* 桌面端头部 */}
        <div className="hidden md:flex items-center justify-between mb-5">
          <h2 className="text-base font-semibold text-textPrimary">{t('modal.detailSettingsTitle')}</h2>
          <button onClick={onClose} className="text-textMuted hover:text-textSecondary transition-colors">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-5 flex-1 overflow-y-auto md:max-h-[65vh] pr-1 pb-[var(--safe-bottom)] md:pb-0">

          {/* ── 基础信息 ── */}
          <Section title={t('modal.detailSettingsBasicInfo')} desc={t('modal.detailSettingsBasicInfoDesc')}>
            <div>
              <label className="block text-xs font-medium mb-1 text-textSecondary">{t('chat.groupName')}</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
              />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1 text-textSecondary">{t('modal.createAgentSystemPrompt')}</label>
              <textarea
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                rows={3}
                className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50 resize-none"
                placeholder={t('modal.createAgentSystemPromptPlaceholder')}
              />
            </div>
          </Section>

          {/* ── 模型参数 ── */}
          <Section title={t('modal.detailSettingsModelParams')} desc={t('modal.detailSettingsModelParamsDesc')}>
            <SliderField label="Temperature" value={temperature} setValue={setTemperature} min={0} max={2} step={0.1} desc={t('modal.detailSettingsTemperatureDesc')} />
            <SliderField label="Top P" value={topP} setValue={setTopP} min={0} max={1} step={0.05} desc={t('modal.detailSettingsTopPDesc')} />
            <SliderField label="Presence Penalty" value={presencePenalty} setValue={setPresencePenalty} min={-2} max={2} step={0.1} desc={t('modal.detailSettingsPresencePenaltyDesc')} />
            <SliderField label="Frequency Penalty" value={frequencyPenalty} setValue={setFrequencyPenalty} min={-2} max={2} step={0.1} desc={t('modal.detailSettingsFrequencyPenaltyDesc')} />
            {thinkingSupported && (
              <ToggleField label={t('modal.detailSettingsThinkingMode')} value={thinkingEnabled} setValue={setThinkingEnabled} desc={t('modal.detailSettingsThinkingModeDesc')} />
            )}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium mb-1 text-textSecondary">
                  {t('modal.detailSettingsChatModel')} <span className="text-textMuted">{t('modal.detailSettingsDefaultLabel')} {defaults.chat_model})</span>
                </label>
                <select value={chatModel} onChange={(e) => setChatModel(e.target.value)}
                  className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50">
                  <option value="">{t('modal.detailSettingsGlobalDefault')}</option>
                  {renderModelOptions(modelOptions, providers)}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium mb-1 text-textSecondary">
                  {t('modal.detailSettingsWorkModel')} <span className="text-textMuted">{t('modal.detailSettingsDefaultLabel')} {defaults.work_model})</span>
                </label>
                <select value={workModel} onChange={(e) => setWorkModel(e.target.value)}
                  className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50">
                  <option value="">{t('modal.detailSettingsGlobalDefault')}</option>
                  {renderModelOptions(modelOptions, providers)}
                </select>
              </div>
            </div>
          </Section>

          {/* ── 工具调用 ── */}
          <Section title={t('modal.detailSettingsToolCalls')} desc={t('modal.detailSettingsToolCallsDesc')}>
            <div className="grid grid-cols-2 gap-3">
              <NumberField label={t('modal.detailSettingsMaxToolRounds')} value={maxToolRounds} setValue={setMaxToolRounds} min={1} max={20} desc={t('modal.detailSettingsMaxToolRoundsDesc')} />
              <NumberField label={t('modal.detailSettingsAlarmRounds')} value={alarmMaxToolRounds} setValue={setAlarmMaxToolRounds} min={1} max={30} desc={t('modal.detailSettingsAlarmRoundsDesc')} />
            </div>
          </Section>

          {/* ── 闹钟 / 心跳 ── */}
          <Section title={t('modal.detailSettingsAlarm')} desc={t('modal.detailSettingsAlarmDesc')}>
            <ToggleField label={t('modal.detailSettingsForceAlarm')} value={forceAlarmOnEnd} setValue={setForceAlarmOnEnd} desc={t('modal.detailSettingsForceAlarmDesc')} />
            <NumberField label={t('modal.detailSettingsMaxAlarms')} value={maxAlarms} setValue={setMaxAlarms} min={1} max={50} desc={t('modal.detailSettingsMaxAlarmsDesc')} />
          </Section>

          {/* ── 文件记忆 ── */}
          <Section title={t('modal.detailSettingsFileMemory')} desc={t('modal.detailSettingsFileMemoryDesc')}>
            <div>
              <label className="block text-xs font-medium mb-1 text-textSecondary">{t('modal.detailSettingsMemoryLoadMode')}</label>
              <select
                value={memoryLoadMode}
                onChange={(e) => setMemoryLoadMode(e.target.value)}
                className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
              >
                <option value="index_only">{t('modal.detailSettingsMemoryLoadModeIndexOnly')}</option>
                <option value="index_plus_recent">{t('modal.detailSettingsMemoryLoadModeIndexRecent')}</option>
                <option value="index_plus_semantic">{t('modal.detailSettingsMemoryLoadModeIndexSemantic')}</option>
              </select>
              <p className="text-3xs text-textMuted mt-1">{t('modal.detailSettingsMemoryLoadModeDesc')}</p>
            </div>
            {memoryLoadMode === 'index_plus_recent' && (
              <NumberField label={t('modal.detailSettingsMemoryRecentCount')} value={memoryRecentCount} setValue={setMemoryRecentCount} min={0} max={50} desc={t('modal.detailSettingsMemoryRecentCountDesc')} />
            )}
            <div>
              <label className="block text-xs font-medium mb-1 text-textSecondary">{t('modal.detailSettingsMemorySharedScope')}</label>
              <select
                value={memorySharedScope}
                onChange={(e) => setMemorySharedScope(e.target.value)}
                className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
              >
                <option value="private_only">{t('modal.detailSettingsMemorySharedScopePrivate')}</option>
                <option value="private_plus_shared_by_user">{t('modal.detailSettingsMemorySharedScopeByUser')}</option>
                <option value="private_plus_shared_all">{t('modal.detailSettingsMemorySharedScopeAll')}</option>
              </select>
              <p className="text-3xs text-textMuted mt-1">{t('modal.detailSettingsMemorySharedScopeDesc')}</p>
            </div>
          </Section>

          {/* ── 自动免打扰 ── */}
          <Section title={t('modal.detailSettingsAutoDnd')} desc={t('modal.detailSettingsAutoDndDesc')}>
            <SliderField label={t('modal.detailSettingsAutoDndThreshold')} value={autoDndThreshold} setValue={setAutoDndThreshold} min={0} max={100} step={5} desc={t('modal.detailSettingsAutoDndThresholdDesc')} />
            <NumberField label={t('modal.detailSettingsAutoDndDuration')} value={autoDndDuration} setValue={setAutoDndDuration} min={1} max={1440} desc={t('modal.detailSettingsAutoDndDurationDesc')} />
          </Section>

          {/* ── 对话日志 ── */}
          <Section title={t('modal.detailSettingsConversationLogs')} desc={t('modal.detailSettingsConversationLogsDesc')}>
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
              <TristateSelect value={userCanViewLogs} onChange={setUserCanViewLogs} />
              <p className="text-3xs text-textMuted mt-0.5">{t('modal.detailSettingsUserCanViewLogsDesc')}</p>
            </div>
          </Section>

          {/* ── AI 类型 ── */}
          <Section title={t('modal.detailSettingsAiType')} desc={t('modal.detailSettingsAiTypeDesc')}>
            <AiTypeSelector value={aiType} onChange={setAiType} />
          </Section>

          {/* ── 行为开关 ── */}
          <Section title={t('modal.detailSettingsBehaviorSwitches')} desc={t('modal.detailSettingsBehaviorSwitchesDesc')}>
            <div>
              <label className="block text-xs font-medium mb-1 text-textSecondary">{t('modal.detailSettingsDelayReply')}</label>
              <TristateSelect value={delayReplyEnabled} onChange={setDelayReplyEnabled} />
            </div>
            <ToggleField label={t('modal.detailSettingsSelfEdit')} value={isAiEditable} setValue={setIsAiEditable} desc={t('modal.detailSettingsSelfEditDesc')} />
            <ToggleField label={t('modal.detailSettingsHideAiIdentity')} value={hideAiIdentity} setValue={setHideAiIdentity} desc={t('modal.detailSettingsHideAiIdentityDesc')} />
            <div>
              <label className="block text-xs font-medium mb-1 text-textSecondary">{t('modal.detailSettingsReminderGrace')}</label>
              <select
                value={reminderGrace}
                onChange={(e) => setReminderGrace(e.target.value)}
                className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
              >
                <option value="every_time">{t('modal.detailSettingsReminderGraceEvery')}</option>
                <option value="once">{t('modal.detailSettingsReminderGraceOnce')}</option>
                <option value="off">{t('modal.detailSettingsReminderGraceOff')}</option>
              </select>
            </div>
          </Section>

          {/* ── 对话与社交权限 ── */}
          <Section title={t('modal.detailSettingsChatPermissionsDetail')} desc={t('modal.detailSettingsChatPermissionsDetailDesc')}>
            <ToggleField label={t('agents.discoverable')} value={discoverable} setValue={setDiscoverable} desc={t('agents.discoverableDesc')} />
            <ToggleField label={t('modal.detailSettingsAllowFriendRequests')} value={allowFriendRequests} setValue={setAllowFriendRequests} desc={t('modal.detailSettingsAllowFriendRequestsDesc')} />
            {allowFriendRequests && (
              <ToggleField label={t('modal.detailSettingsAutoRespondFriendRequest')} value={autoRespondFriendRequest} setValue={setAutoRespondFriendRequest} desc={t('modal.detailSettingsAutoRespondFriendRequestDesc')} />
            )}

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
                        <button type="button" onClick={() => setOthersChatUsed(0)} className="text-3xs px-2 py-0.5 rounded border border-border text-textMuted hover:text-textSecondary transition-colors">{t('agents.othersChatUsedReset')}</button>
                      </div>
                      <p className="text-3xs text-textMuted leading-relaxed">{t('agents.othersChatQuotaDesc')}</p>
                    </div>
                    <ToggleField label={t('agents.autoResetQuota')} value={autoResetQuota} setValue={setAutoResetQuota} desc={t('agents.autoResetQuotaDesc')} />
                  </>
                )}
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
          </Section>

          {/* ── 额度 ── */}
          <Section title={t('modal.detailSettingsCreditCost')} desc={t('modal.detailSettingsCreditCostDesc')}>
            <NumberField label={t('modal.detailSettingsApiCreditCost')} value={apiCreditCost} setValue={setApiCreditCost} min={0} max={100000} desc={t('modal.detailSettingsApiCreditCostDesc')} />
          </Section>

          {/* ── API 提供商 ── */}
          <Section title={t('modal.detailSettingsApiProvider')} desc={t('modal.detailSettingsApiProviderDesc')}>
            <div>
              <label className="block text-xs font-medium mb-1 text-textSecondary">API Base URL</label>
              <input
                type="text"
                value={apiBaseUrl}
                onChange={(e) => setApiBaseUrl(e.target.value)}
                className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                placeholder={t('modal.detailSettingsApiBaseUrlPlaceholder')}
              />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1 text-textSecondary flex items-center gap-2">
                API Key
                <ApiKeyGetLink providers={providers} />
              </label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                autoComplete="off"
                className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                placeholder={t('modal.detailSettingsApiKeyPlaceholder')}
              />
            </div>
            <button
              onClick={handleTestApi}
              disabled={testingApi || (!apiBaseUrl.trim() && !apiKey.trim())}
              className="btn btn-xs btn-outline gap-1.5"
            >
              {testingApi ? <Loader2 size={12} className="animate-spin" /> : <RotateCw size={12} />}
              {t('settings.testConnection')}
            </button>
            {testApiMsg && (
              <p className={`text-xs ${testApiOk === true ? 'text-mint-400' : 'text-rose-400'}`}>
                {testApiMsg}
              </p>
            )}
          </Section>

          {/* ── 兑换码 ── */}
          <Section title={t('modal.detailSettingsRedeemCode')} desc={t('modal.detailSettingsRedeemCodeDesc')}>
            <div className="flex items-center gap-2">
              <div className="flex-1 relative">
                <Ticket size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-textMuted" />
                <input
                  type="text"
                  value={redeemCode}
                  onChange={(e) => setRedeemCode(e.target.value)}
                  className="w-full pl-9 pr-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                  placeholder={t('modal.detailSettingsRedeemPlaceholder')}
                />
              </div>
              <button
                onClick={handleRedeem}
                disabled={redeeming || !redeemCode.trim()}
                className="btn btn-sm btn-primary gap-1 shrink-0"
              >
                {redeeming ? <Loader2 size={14} className="animate-spin" /> : <span>{t('me.redeem')}</span>}
              </button>
            </div>
            {redeemMsg && (
              <p className={`text-xs ${redeemOk === false ? 'text-rose-400' : 'text-mint-400'}`}>
                {redeemMsg}
              </p>
            )}
          </Section>

        </div>

        <button
          onClick={onClose}
          className="btn btn-md btn-primary w-full mt-5"
        >
          {t('modal.detailSettingsSaveAndClose')}
        </button>
      </div>
    </div>
  )
}

// ── 分区容器 ──
