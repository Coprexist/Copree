import { useEffect, useReducer, useRef, useState } from 'react'
import { ArrowLeft, Pencil, Settings, Sparkles } from 'lucide-react'
import { api } from '../api/client'
import { useT } from '../i18n/I18nContext'
import { AiTypeSelector, PresetIcon, SubIcon } from './agent-create/fields'
import { Button, confirmAsync, UnderlineTabs } from './ui'
import { CARD_ICONS, PRESETS, SUB_OPTIONS } from './agent-create/presets'
import SubOptionModal from './agent-create/SubOptionModal'
import DetailSettingsModal from './agent-create/DetailSettingsModal'
import KnowledgeDocsSection from './KnowledgeDocsSection'
import CreationAssistantPanel from './agent-create/CreationAssistantPanel'
import { createDraft, patchDraft, type CreationDraft } from './agent-create/creationApi'
import type { AgentForm, AgentFormApi, ModelOption, ProviderInfo } from './agent-create/types'

// ── 表单状态：一份 reducer，setter 由字段名推导 ──
// 初值取「聊天档」：没选预设、直接进详细设置的路径就落在这些值上

const INITIAL_FORM: AgentForm = {
  name: '',
  systemPrompt: '',
  temperature: 0.8,
  topP: 0.9,
  presencePenalty: 0.5,
  frequencyPenalty: 0.5,
  thinkingEnabled: false,
  hideAiIdentity: false,
  reminderGrace: 'every_time',
  delayReplyEnabled: null,
  configProfile: 'chat',
  maxToolRounds: 6,
  alarmMaxToolRounds: 8,
  forceAlarmOnEnd: false,
  retireHandoverSelf: false,
  maxAlarms: 10,
  isAiEditable: true,
  allowFriendRequests: true,
  autoRespondFriendRequest: false,
  discoverable: true,
  allowOthersChat: true,
  othersChatMode: 'unlimited',
  othersChatQuota: 30,
  othersChatUsed: 0,
  disallowMode: 'strict',
  chatModel: '',
  workModel: '',
  apiCreditCost: 0,
  aiType: 'resonance',
  apiBaseUrl: '',
  apiKey: '',
  memoryLoadMode: 'index_only',
  memoryRecentCount: 0,
  memorySharedScope: 'private_only',
  bio: '',
  statusText: '',
  autoDndThreshold: 20,
  autoDndDuration: 5,
  autoResetQuota: false,
  groupOwnerPays: true,
  conversationLogsLimit: null,
  userCanViewLogs: null,
  knowledge: [],
}

/**
 * setter 名从字段名推导（temperature → setTemperature）：加字段只动类型与初值。
 *
 * 代价写在这里，别让下一个人踩：setTemperature 不是真实符号，是这里拼出来的。
 * 改字段名时 IDE/重命名找不到调用点，编译器也不会替你把 setter 改名——只能全仓 grep setXxx；
 * `as unknown as` 是同一个取舍的另一半，形状只在运行期成立。
 * 要恢复可跳转可重命名，就得手写全部 setter，换取"加字段要改多处"。
 */
function buildFormApi(form: AgentForm, patch: (p: Partial<AgentForm>) => void): AgentFormApi {
  const api: Record<string, unknown> = { ...form }
  for (const key of Object.keys(form) as (keyof AgentForm)[]) {
    const k = String(key)
    const setter = `set${k.charAt(0).toUpperCase()}${k.slice(1)}`
    api[setter] = (v: unknown) => patch({ [key]: v } as Partial<AgentForm>)
  }
  return api as unknown as AgentFormApi
}

/** camelCase → snake_case：提交体沿用后端字段名 */
const toSnake = (k: string) => k.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`)
/** 不交给表单循环的字段：独立 API 配置走创建后的 PUT；config_profile 由调用方决定（免得循环里再写一遍把它覆盖掉） */
const CREATE_BODY_OMIT = new Set<keyof AgentForm>(['apiBaseUrl', 'apiKey', 'configProfile'])
/** 空串要转成 null 的字段：后端用 null 表示「未设置」。只放字符串字段——判等只认空串，
 *  不用真值判断，否则以后加进来的数字字段一旦是 0 就会静默变 null */
const CREATE_BODY_NULLABLE = new Set<keyof AgentForm>(['systemPrompt', 'chatModel', 'workModel', 'bio', 'statusText'])

/** 提交体整表派生：新增 AgentForm 字段不会漏传，要漏只能靠 OMIT 显式排除 */
function buildCreateBody(form: AgentForm, configProfile: string): Record<string, unknown> {
  const body: Record<string, unknown> = { config_profile: configProfile }
  for (const [key, value] of Object.entries(form) as [keyof AgentForm, unknown][]) {
    if (CREATE_BODY_OMIT.has(key)) continue
    body[toSnake(key)] = CREATE_BODY_NULLABLE.has(key) ? (value === '' ? null : value) : value
  }
  body.name = form.name.trim()
  return body
}

export default function CreateAgentPanel({
  onClose,
  onCreated,
  draft = null,
}: {
  onClose: () => void
  onCreated: (agentName?: string) => void
  /** 创建助手的草稿：给了就是左右两栏（左对话、右表单），并把表单快照自动回写 */
  draft?: CreationDraft | null
}) {
  const t = useT()
  // 表单只有一份状态：值 + setter 一体的 API，子组件与本地都从这里读写。
  // 带草稿进来时用快照做初值——助手填过的接着填，不用重来
  const [form, patch] = useReducer(
    (state: AgentForm, p: Partial<AgentForm>) => ({ ...state, ...p }),
    { ...INITIAL_FORM, ...(draft?.form || {}) },
  )
  // 不套 memo：form 每次输入都变，memo 挡不住任何重建，40 个闭包的开销可以忽略
  const formApi = buildFormApi(form, patch)
  // 与旧签名同名解出：applyPreset / handleCreate / 子组件都不必逐处改写
  const {
    name, setName,
    systemPrompt, setSystemPrompt,
    setTemperature,
    setThinkingEnabled,
    setHideAiIdentity,
    setReminderGrace,
    setDelayReplyEnabled,
    setConfigProfile,
    setMaxToolRounds,
    setAlarmMaxToolRounds,
    setForceAlarmOnEnd,
    setRetireHandoverSelf,
    setMaxAlarms,
    setIsAiEditable,
    aiType, setAiType,
    apiBaseUrl,
    apiKey,
    setMemoryLoadMode,
    setMemoryRecentCount,
    bio, setBio,
    statusText, setStatusText,
  } = formApi

  // 预设选择（草稿里定过档位就接着用）
  const [selectedPreset, setSelectedPreset] = useState<string | null>(draft?.preset ?? null)
  const [selectedSub, setSelectedSub] = useState<string | null>(draft?.sub ?? null)
  const [showSubModal, setShowSubModal] = useState<string | null>(null) // 子选项弹窗（独立 modal）

  // 从「已建好的记录」重开时，保存等于再建一个：确认后另起一张草稿承接这次创建，
  // 原来那条记录（以及它指向的 AI）原样留着——草稿与 AI 始终是一对一。
  const [forkedDraftId, setForkedDraftId] = useState<number | null>(null)
  const draftId = forkedDraftId ?? draft?.id ?? null

  // 弹窗状态
  const [showDetailSettings, setShowDetailSettings] = useState(false)

  // 加载中的模型选项
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([])
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [defaults, setDefaults] = useState<{ chat_model: string; work_model: string }>({ chat_model: '', work_model: '' })
  const [thinkingSupported, setThinkingSupported] = useState(false)

  // 错误/加载
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  // 建号成功但收尾失败时留住的名字：窗口要停在原地把话说清楚
  const [createdAgent, setCreatedAgent] = useState<string | null>(null)
  // 窄屏两个屏：填写配置 / 创建助手（桌面端两栏并排，不靠这个）
  const [mobileTab, setMobileTab] = useState<'form' | 'chat'>('form')

  // ── sin() 浮动动画（JS 驱动，选完子项才启动）──
  // 拿节点用 ref 而不是 querySelector：不依赖全局唯一的 data 属性，卸载时也不会静默跳过复位
  const cardRefs = useRef(new Map<string, HTMLDivElement>())
  const animKeyRef = useRef<string | null>(null)

  useEffect(() => {
    const key = selectedSub ? selectedPreset : null
    const reset = (k: string | null) => {
      const el = k ? cardRefs.current.get(k) : null
      if (el) el.style.transform = 'translate3d(0, 0, 0)'
    }
    if (animKeyRef.current !== key) reset(animKeyRef.current)
    animKeyRef.current = key
    if (!key) return

    let rafId: number
    const start = performance.now()
    const animate = (now: number) => {
      const elapsed = (now - start) / 1000
      const y = Math.sin(elapsed * 2.1) * 5
      const el = cardRefs.current.get(key)
      if (el) el.style.transform = `translate3d(0, ${y}px, 0)`
      rafId = requestAnimationFrame(animate)
    }
    rafId = requestAnimationFrame(animate)
    return () => {
      cancelAnimationFrame(rafId)
      reset(key)
    }
  }, [selectedSub, selectedPreset])

  useEffect(() => {
    api.get<{ models: ModelOption[]; providers: ProviderInfo[]; defaults: { chat_model: string; work_model: string }; provider: { thinking_supported: boolean } }>('/agents/models')
      .then(data => {
        setModelOptions(data.models)
        setProviders(data.providers || [])
        setDefaults(data.defaults)
        setThinkingSupported(data.provider?.thinking_supported ?? false)
      })
      .catch(console.error)
  }, [])

  // ── 应用预设 ──
  const applyPreset = (presetKey: string, subId: string | null) => {
    const preset = PRESETS[presetKey]
    if (!preset) return

    // 基础预设值
    setTemperature(preset.temperature)
    setThinkingEnabled(preset.thinking_enabled)
    setMaxToolRounds(preset.max_tool_rounds)
    setAlarmMaxToolRounds(preset.alarm_max_tool_rounds)
    setForceAlarmOnEnd(preset.force_alarm_on_end)
    setRetireHandoverSelf(preset.retire_handover_self)
    setMaxAlarms(preset.max_alarms)
    setDelayReplyEnabled(preset.delay_reply_enabled)
    setIsAiEditable(preset.is_ai_editable)
    setHideAiIdentity(preset.hide_ai_identity)
    setReminderGrace(preset.reminder_grace || 'every_time')
    setMemoryLoadMode(preset.memory_load_mode || 'index_only')
    setMemoryRecentCount(preset.memory_recent_count ?? 0)
    setConfigProfile(presetKey)

    // 子选项覆盖
    if (subId) {
      const subOptions = SUB_OPTIONS[presetKey] || []
      const sub = subOptions.find(s => s.id === subId)
      if (sub) {
        if (sub.params.temperature !== undefined) setTemperature(sub.params.temperature)
        if (sub.params.max_tool_rounds !== undefined) setMaxToolRounds(sub.params.max_tool_rounds)
        if (sub.params.thinking_enabled !== undefined) setThinkingEnabled(sub.params.thinking_enabled)
        if (sub.params.is_ai_editable !== undefined) setIsAiEditable(sub.params.is_ai_editable)
        if (sub.params.alarm_max_tool_rounds !== undefined) setAlarmMaxToolRounds(sub.params.alarm_max_tool_rounds)
        if (sub.params.force_alarm_on_end !== undefined) setForceAlarmOnEnd(sub.params.force_alarm_on_end)
        if (sub.params.retire_handover_self !== undefined) setRetireHandoverSelf(sub.params.retire_handover_self)
        if (sub.params.max_alarms !== undefined) setMaxAlarms(sub.params.max_alarms)
        if (sub.ai_type) setAiType(sub.ai_type)
      }
    }
  }

  // ── 选择卡片 → 打开子选项弹窗 ──
  // 两个子弹窗互斥：同层同 z，叠在一起会分不清谁在上
  const handleCardClick = (key: string) => {
    setShowDetailSettings(false)
    setSelectedPreset(key)
    setShowSubModal(key)
  }

  // ── 选择子项 → 关闭弹窗，开始浮动 ──
  const handleSubSelect = (presetKey: string, subId: string) => {
    setSelectedPreset(presetKey)
    setSelectedSub(subId)
    applyPreset(presetKey, subId)
    setShowSubModal(null)
  }

  // ── 助手填表：先定档位，再落微调 ──
  // 顺序不能反：applyPreset 会写一整套档位值，patch 只覆盖助手明确决定的字段
  const applyAssistantForm = (fields: Partial<AgentForm>, preset: string | null, sub: string | null) => {
    if (preset && PRESETS[preset]) {
      setSelectedPreset(preset)
      setSelectedSub(sub)
      applyPreset(preset, sub)
    } else if (sub) {
      setSelectedSub(sub)
    }
    if (Object.keys(fields).length > 0) patch(fields)
  }

  // 草稿自动保存：打字、助手填表、换档位都回写，刷新或换设备能接着填
  useEffect(() => {
    if (!draftId) return
    const timer = setTimeout(() => {
      patchDraft(draftId, { form, preset: selectedPreset, sub: selectedSub }).catch(() => {})
    }, 600)
    return () => clearTimeout(timer)
  }, [draftId, form, selectedPreset, selectedSub])

  // ── 创建 ──
  /** 真正落库：draft_id 是幂等键，同一张草稿只会建出一个 AI */
  const doCreate = async (useDraftId: number | null) => {
    setLoading(true)
    setError('')
    try {
      const agent = await api.post<{ id: number; name: string }>(
        '/agents',
        { ...buildCreateBody(form, selectedPreset || 'chat'), draft_id: useDraftId },
      )
      // 如果填写了独立 API 配置，创建后立即设置
      if (apiBaseUrl.trim() || apiKey.trim()) {
        try {
          await api.put(`/agents/${agent.id}/config`, {
            api_base_url: apiBaseUrl.trim() || null,
            api_key: apiKey.trim() || null,
          })
        } catch (err: any) {
          // AI 已经建好，只是独立 API 配置没落库：先别关窗——关掉就没人看得见这句话了
          setCreatedAgent(agent.name)
          setError(err?.message || t('modal:createAgentApiConfigFailed'))
          return
        }
      }
      // 草稿的 created 状态与 agent_id 由后端在同一个事务里写（这次创建算数的唯一开关）
      onCreated(agent.name)
    } catch (err: any) {
      setError(err.message || t('modal:createAgentFailed'))
    } finally {
      setLoading(false)
    }
  }

  const handleCreate = async () => {
    if (!name.trim()) return
    let targetDraftId = draftId
    // 这张记录已经落过 AI：先把"会新建一个、原来那个不动"讲清楚，
    // 再另起一张草稿承接这次创建（草稿与 AI 一对一，入口页的记录不会被改写）
    if (draft?.status === 'created' && forkedDraftId === null) {
      const ok = await confirmAsync({
        title: t('modal:createAgentReuseTitle'),
        message: t('modal:createAgentReuseBody', { name: draft.title || name }),
        confirmText: t('modal:createAgentReuseConfirm'),
        cancelText: t('modal:createAgentReuseCancel'),
      })
      if (!ok) return
      try {
        const fresh = await createDraft()
        const cloned = await patchDraft(fresh.id, {
          title: draft.title, form, preset: selectedPreset, sub: selectedSub,
        })
        setForkedDraftId(cloned.id)
        targetDraftId = cloned.id
      } catch (err: any) {
        setError(err?.message || t('modal:createAgentFailed'))
        return
      }
    }
    await doCreate(targetDraftId)
  }

  // ── 关闭 ──
  // 有过配置保存失败时，关窗也要把"已创建"这件事交给父组件去刷新列表
  const handleClose = () => {
    if (createdAgent) { onCreated(createdAgent); return }
    onClose()
  }

  // ── 当前选中的子档（卡片角标与图标共用）──
  const selectedSubOption = selectedSub
    ? (SUB_OPTIONS[selectedPreset || ''] || []).find(s => s.id === selectedSub)
    : undefined

  return (
    <div className="h-full flex flex-col bg-canvas">
      {/* 顶部条：底纹通栏、内容跟下面的两栏同一个度量（不然标题贴在屏边、内容挤在中间） */}
      <div className="shrink-0 border-b border-border bg-surface">
        <div className="flex w-full items-center gap-3 px-4 py-3 lg:px-6">
          <button onClick={handleClose} className="icon-btn-sm -ml-1 text-textSecondary">
            <ArrowLeft size={18} />
          </button>
          <h2 className="text-lg font-semibold text-textPrimary">{t('modal:createAgentTitle')}</h2>
        </div>
      </div>

      {/* 已建好的记录被重开：把"保存 = 再建一个"先摆在面前，别等填完才说 */}
      {draft?.status === 'created' && forkedDraftId === null && (
        <div className="shrink-0 border-b border-mint-500/30 bg-mint-500/10 px-4 py-2 text-xs text-mint-400 lg:px-6">
          {t('modal:createAgentReuseBanner', { name: draft.title || t('modal:createStartUntitled') })}
        </div>
      )}

      {/* 窄屏两个屏之间切：填写配置 / 创建助手。桌面端是「左表单 + 右对话窗格」并排 */}
      <div className="shrink-0 px-2 lg:hidden">
        <UnderlineTabs
          grow
          items={[
            { key: 'form', label: t('modal:createAgentTabForm'), icon: <Pencil size={14} /> },
            { key: 'chat', label: t('modal:createAgentTabChat'), icon: <Sparkles size={14} /> },
          ]}
          value={mobileTab}
          onChange={(key) => setMobileTab(key as 'form' | 'chat')}
        />
      </div>

      <div className="flex min-h-0 w-full flex-1 flex-col lg:flex-row">
        {/* 左：表单（自己滚） */}
        <div className={`min-w-0 scroll-floor px-4 py-4 pb-[var(--safe-bottom)] lg:flex-1 lg:min-h-0 lg:overflow-y-auto lg:px-6 lg:pb-4 ${
          mobileTab === 'form' ? 'flex-1 flex flex-col min-h-0 overflow-y-auto' : 'hidden lg:block'
        }`}>

        {/* ── 名称输入 ── */}
        <div className="mb-4">
          <label className="block text-sm font-medium mb-1.5 text-textSecondary">{t('modal:agentNameLabel')}</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="field field-lg w-full"
            placeholder={t('modal:createAgentNamePlaceholder')}
          />
        </div>

        {/* ── 系统提示词 ── */}
        <div className="mb-5">
          <label className="block text-sm font-medium mb-1.5 text-textSecondary">{t('modal:createAgentSystemPrompt')}</label>
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={3}
            className="field w-full resize-none"
            placeholder={t('modal:createAgentSystemPromptPlaceholder')}
          />
        </div>

        {/* ── 知识库（虚拟：只是表单内容，建号时才落地） ── */}
        <KnowledgeDocsSection docs={form.knowledge} onChange={(docs) => patch({ knowledge: docs })} />

        {/* ── 三档卡片（横排，放大） ── */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 md:gap-4 mb-5">
          {Object.entries(PRESETS).map(([key, preset]) => {
            const icon = CARD_ICONS[key]
            const isSelected = selectedPreset === key
            const hasSub = selectedSub && isSelected

            return (
              // frame 只是浮动动画的定位壳（留出下沉空间、不裁剪 inner 的 transform），卡片本体在 inner
              <div key={key} className="preset-card-frame h-full pb-[7px] overflow-visible">
                <div
                  ref={(el) => { if (el) cardRefs.current.set(key, el); else cardRefs.current.delete(key) }}
                  className="preset-card-inner h-full"
                >
                  <button
                    onClick={() => handleCardClick(key)}
                    className={`w-full h-full text-left rounded-card border transition-colors duration-300 min-h-[150px] md:min-h-[160px]
                      bg-gradient-to-b ${icon.color}
                      ${isSelected
                        ? 'border-primary-400/60 shadow-lg shadow-primary-500/10'
                        : 'border-border hover:border-primary-500/30'
                      }`}
                  >
                    <div className="p-5 flex flex-col items-center text-center gap-2">
                      <span className="text-3xl"><PresetIcon name={icon.icon} /></span>
                      <span className="text-sm font-semibold text-textPrimary">{t(preset.nameKey)}</span>
                      <p className="text-xs text-textSecondary leading-snug">{t(preset.descKey)}</p>

                      {hasSub && (
                        <span className="chip chip-primary shrink-0 mt-1">
                          <SubIcon name={selectedSubOption?.icon || ''} /> {selectedSubOption ? t(selectedSubOption.nameKey) : ''}
                        </span>
                      )}
                    </div>
                  </button>
                </div>
              </div>
            )
          })}
        </div>

        {/* ── AI 类型（选细档时自动推荐，可手动改）── */}
        {selectedPreset && (
          <div className="mb-5">
            <div className="flex items-center gap-2 mb-2">
              <label className="text-xs font-medium text-textSecondary">{t('modal:detailSettingsAiType')}</label>
              {selectedSub && (
                <span className="chip chip-primary shrink-0">{t('modal:aiTypeRecommended')}</span>
              )}
            </div>
            <AiTypeSelector value={aiType} onChange={setAiType} />
          </div>
        )}

        {/* ── 个人资料（可选） ── */}
        <div className="space-y-2 mb-3">
          <textarea
            value={bio}
            onChange={(e) => setBio(e.target.value)}
            placeholder={t('agentDetail:bioPlaceholder')}
            rows={2}
            maxLength={500}
            className="field w-full resize-none"
          />
          <input
            type="text"
            value={statusText}
            onChange={(e) => setStatusText(e.target.value)}
            placeholder={t('agentDetail:statusTextPlaceholder')}
            className="field w-full"
          />
        </div>

        {/* ── 操作按钮区 ── */}
        {createdAgent ? (
          // 建号已完成，只剩收尾：不再给"再建一个"的入口
          <Button size="md" variant="primary" className="w-full" onClick={handleClose}>
            {t('common:close')}
          </Button>
        ) : (
          // 窄屏两个按钮平分整行；宽屏右对齐、定宽——通栏后两个各占一半会拉成两根长条
          <div className="flex gap-3 lg:justify-end">
            <Button
              size="md"
              variant="secondary"
              className="flex-1 lg:min-w-[148px] lg:flex-none"
              icon={<Settings size={14} />}
              onClick={() => { setShowSubModal(null); setShowDetailSettings(true) }}
            >
              {t('modal:createAgentDetailSettings')}
            </Button>
            <Button
              size="md"
              variant="primary"
              className="flex-1 lg:min-w-[148px] lg:flex-none"
              loading={loading}
              disabled={!name.trim()}
              onClick={handleCreate}
            >
              {loading ? t('modal:createAgentCreating') : t('modal:createAgentCreate')}
            </Button>
          </div>
        )}
        {/* 跳过预设放在动作按钮下面：先给主路径（选档 → 创建），再给"我不想选档"的旁路 */}
        {!selectedPreset && !createdAgent && (
          <div className="mt-3 flex justify-center">
            <Button size="sm" variant="link" onClick={() => setShowDetailSettings(true)}>
              {t('modal:createAgentSkipPreset')}
            </Button>
          </div>
        )}

        {!name.trim() && selectedPreset && (
          <p className="text-xs text-textMuted mt-2 text-center">{t('modal:createAgentConfirmHint')}</p>
        )}

        {error && <div className="text-sm text-rose-400 mt-3 text-center">{error}</div>}

        {/* ── 子选项弹窗（独立 modal，选中后关闭并开始浮动） ── */}
        {showSubModal && selectedPreset && (
          <SubOptionModal
            preset={PRESETS[showSubModal]}
            selectedSub={selectedSub}
            onSelect={(subId) => handleSubSelect(showSubModal, subId)}
            onClose={() => { setShowSubModal(null); setSelectedPreset(null) }}
          />
        )}

        {/* ── 详细设置弹窗 ── */}
        {showDetailSettings && (
          <DetailSettingsModal
            form={formApi}
            modelOptions={modelOptions}
            providers={providers}
            defaults={defaults}
            thinkingSupported={thinkingSupported}
            onClose={() => setShowDetailSettings(false)}
          />
        )}
        </div>

        {/* 右：创建助手对话窗格（桌面固定在右侧，窄屏自成一个屏） */}
        {draft && (
          <div className={`min-w-0 shrink-0 border-border px-3 py-3 lg:flex lg:w-[360px] lg:min-h-0 lg:flex-col lg:border-l lg:px-4 ${
            mobileTab === 'chat' ? 'flex-1 flex flex-col min-h-0' : 'hidden lg:flex'
          }`}>
            <CreationAssistantPanel draft={draft} onForm={applyAssistantForm} className="min-w-0 flex-1" />
          </div>
        )}
      </div>
    </div>
  )
}

