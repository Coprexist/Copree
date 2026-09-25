/**
 * 群视界机器人（世界 AI）配置弹窗 — 单独表单，不属于 agent
 * （从 WorldDesignPage 拆分；世界 AI 是世界的配置：让它改界面、加功能）
 * 2026-08-10：改为 Modal 弹窗（原内联展开），对齐 GroupManagerModal 风格
 */
import { useEffect, useState } from 'react'
import { Save, X, Settings, Brain, SlidersHorizontal, History, Pencil, ChevronDown, ChevronUp, Lock, ShieldAlert } from 'lucide-react'

// 运行模式（后端 world_ai_mode.MODES 是权威定义；这里只做展示与切换）
const AI_MODES = [
  // 标签/说明复用既有的 world.mode.*（同一概念只留一份文案）
  { key: 'auto', labelKey: 'tool:world.mode.auto', hintKey: 'tool:world.mode.hint.auto' },
  { key: 'review', labelKey: 'tool:world.mode.review', hintKey: 'tool:world.mode.hint.review' },
  { key: 'plan', labelKey: 'tool:world.mode.plan', hintKey: 'tool:world.mode.hint.plan' },
] as const
import { api } from '../../api/client'
import { Dialog } from '../ui'
import { useT } from '../../i18n/I18nContext'

export interface WorldCreator {
  id: string
  name: string
  system_prompt: string
  forced_prompt: string
  model: string | null
  temperature: number
  top_p: number
  thinking: boolean
  max_tool_rounds: number
  tools: string[]
}

/** 单轮用量（后端按 turn 聚合，正序 = 旧 → 新） */
export interface WorldUsageTurn {
  turn_id: string
  calls: number
  prompt_tokens: number
  cached_tokens: number
  hit_pct: number
  at: string | null
}

export interface WorldUsageStats {
  total_calls: number
  prompt_tokens: number
  completion_tokens: number
  cached_tokens: number
  cache_hit_rate_pct: number
  recent_turns?: WorldUsageTurn[]
}

interface WorldCreatorConfigProps {
  wid: number
  creator: WorldCreator
  usageStats: WorldUsageStats | null
  /** 世界当前运行模式（worlds.config.ai_mode，后端保证有值） */
  aiMode: string
  onModeSaved: (mode: string) => void
  onSaved: (updated: WorldCreator) => void
  onClose: () => void
  onMsg: (msg: string) => void
}

export default function WorldCreatorConfig({ wid, creator, usageStats, aiMode, onModeSaved, onSaved, onClose, onMsg }: WorldCreatorConfigProps) {
  const t = useT()
  const [form, setForm] = useState({
    name: creator.name ?? '',
    system_prompt: creator.system_prompt ?? '',
    model: creator.model ?? '',
    temperature: creator.temperature ?? 0.8,
    thinking: creator.thinking ?? false,
    max_tool_rounds: creator.max_tool_rounds ?? 50,
  })
  const [saving, setSaving] = useState(false)
  // 收起态：名字/提示词默认收起，🖊 点击展开输入框；强注入段默认收起，点开只读展示
  const [editingName, setEditingName] = useState(false)
  const [editingPrompt, setEditingPrompt] = useState(false)
  const [showForced, setShowForced] = useState(false)
  // 会话生命周期设置（/new 自动开、空闲压缩、保留天数）
  const [settings, setSettings] = useState<{ auto_new_enabled: boolean; auto_new_time: string; compact_idle_hours: number; retention_days: number } | null>(null)
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [modeSaving, setModeSaving] = useState(false)

  // 加载会话生命周期设置（失败静默，不阻塞表单）
  useEffect(() => {
    api.get<{ auto_new_enabled: boolean; auto_new_time: string; compact_idle_hours: number; retention_days: number }>(`/worlds/${wid}/chat/settings`)
      .then((r) => setSettings(r))
      .catch(() => { /* ignore */ })
  }, [wid])

  // creator 外部更新（load 重拉）后同步表单
  useEffect(() => {
    setForm({
      name: creator.name ?? '',
      system_prompt: creator.system_prompt ?? '',
      model: creator.model ?? '',
      temperature: creator.temperature ?? 0.8,
      thinking: creator.thinking ?? false,
      max_tool_rounds: creator.max_tool_rounds ?? 50,
    })
  }, [creator])

  const save = async () => {
    setSaving(true)
    try {
      const patch: Record<string, unknown> = {
        name: form.name,
        system_prompt: form.system_prompt,
        temperature: form.temperature,
        thinking: form.thinking,
        max_tool_rounds: form.max_tool_rounds,
      }
      if (form.model.trim()) patch.model = form.model.trim()
      const updated = await api.put<WorldCreator>(`/worlds/${wid}/creator`, patch)
      onSaved(updated)
      onClose()
      onMsg(t('tool:world.creatorConfig.saveSuccess'))
    } catch (e: any) {
      onMsg(t('tool:world.creatorConfig.saveFailed', { error: String(e?.message || e) }))
    } finally {
      setSaving(false)
    }
  }

  const saveSettings = async () => {
    if (!settings) return
    setSettingsSaving(true)
    try {
      const updated = await api.put<typeof settings>(`/worlds/${wid}/chat/settings`, settings)
      setSettings(updated)
      onMsg(t('tool:world.creatorConfig.settingsSaved'))
    } catch (e: any) {
      onMsg(t('tool:world.creatorConfig.saveFailed', { error: String(e?.message || e) }))
    } finally {
      setSettingsSaving(false)
    }
  }

  /** 切运行模式：改即生效（与 AI 配置表单分开保存，避免"改了模式没点保存"的误解） */
  const saveMode = async (mode: string) => {
    setModeSaving(true)
    try {
      await api.put<{ ai_mode: string }>(`/worlds/${wid}/ai-mode`, { mode })
      onModeSaved(mode)
      const labelKey = AI_MODES.find((m) => m.key === mode)?.labelKey
      onMsg(t('tool:world.creatorConfig.modeSwitched', { label: labelKey ? t(labelKey) : mode }))
    } catch (e: any) {
      onMsg(t('tool:world.creatorConfig.modeSwitchFailed', { error: String(e?.message || e) }))
    } finally {
      setModeSaving(false)
    }
  }

  const fieldLabel = 'text-3xs text-textMuted mb-1'
  const fieldInput = 'w-full bg-elevated text-sm p-2 rounded-control border border-border outline-none focus:border-primary-500/50 transition-colors'

  return (
    <Dialog onClose={onClose} className="flex items-center justify-center p-4">
      {/* 宽屏适配：里面有系统提示词编辑器与强注入段长文，max-w-lg 一条道走到黑太憋屈（用户 2026-09-19 反馈） */}
      <div
        className="w-full max-w-lg sm:max-w-2xl lg:max-w-4xl bg-surface border border-border rounded-dialog max-h-[85vh] flex flex-col shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between p-4 pb-2 shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <Settings size={16} className="text-primary-400 shrink-0" />
            <span className="text-sm font-semibold text-textPrimary truncate">{t('tool:world.creatorConfig.title')}</span>
            <span className="text-3xs text-textMuted shrink-0">{t('tool:world.creatorConfig.subtitle')}</span>
          </div>
          <button onClick={onClose} className="p-1 text-textMuted hover:text-textPrimary transition-colors shrink-0" title={t('tool:world.creatorConfig.close')}>
            <X size={16} />
          </button>
        </div>

        {/* 表单体 */}
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {/* 用量（置顶：一眼看到成本/缓存） */}
          {usageStats && (
            <div className="bg-elevated/40 rounded-card p-3">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-3xs text-textSecondary uppercase tracking-wide font-medium">{t('tool:world.creatorConfig.usage.cacheHitRate')}</div>
                  <div className="text-3xs text-textMuted mt-0.5">{t('tool:world.creatorConfig.usage.summary', { calls: usageStats.total_calls, prompt: usageStats.prompt_tokens, cached: usageStats.cached_tokens })}</div>
                </div>
                <div className="text-lg font-bold text-mint-400">{usageStats.cache_hit_rate_pct}%</div>
              </div>
              {/* 逐轮趋势：只给一个全时段数字，改完无从判断有没有用（文档 6.10） */}
              {!!usageStats.recent_turns?.length && (
                <div className="mt-2.5 pt-2 border-t border-border">
                  <div className="text-3xs text-textMuted">
                    {t('tool:world.usage.recentTurns', { n: usageStats.recent_turns.length })}
                  </div>
                  <div className="flex items-end gap-0.5 h-6 mt-1">
                    {usageStats.recent_turns.map(turn => (
                      <div
                        key={turn.turn_id}
                        className="flex-1 bg-mint-400/60 rounded-t-sm"
                        style={{ height: `${Math.max(8, turn.hit_pct)}%` }}
                        title={t('tool:world.usage.turnTip', {
                          calls: turn.calls, prompt: turn.prompt_tokens, hit: turn.hit_pct,
                        })}
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* 身份：名字默认收起，🖊 点击可改 */}
          <div className="bg-elevated/40 rounded-card p-3 space-y-2.5">
            <div className="flex items-center gap-1.5 text-3xs font-medium text-textSecondary uppercase tracking-wide">
              <Settings size={11} className="text-primary-400" /> {t('tool:world.creatorConfig.identity.section')}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-textSecondary truncate">{form.name.trim() || t('tool:world.creatorConfig.identity.unnamed')}</div>
                  <div className="text-3xs text-textMuted mt-0.5">{t('tool:world.creatorConfig.identity.nameHint')}</div>
                </div>
                <button
                  onClick={() => setEditingName((v) => !v)}
                  className="p-1.5 text-textMuted hover:text-textPrimary hover:bg-border rounded-control transition-colors shrink-0"
                  title={editingName ? t('tool:world.creatorConfig.collapse') : t('tool:world.creatorConfig.identity.editName')}
                >
                  {editingName ? <ChevronUp size={15} /> : <Pencil size={15} />}
                </button>
              </div>
              {editingName && (
                <input
                  autoFocus
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  className={`${fieldInput} mt-2`}
                  placeholder={t('tool:world.creatorConfig.identity.namePlaceholder')}
                />
              )}
            </div>
          </div>

          {/* 运行模式：AI 自主到什么程度（下载/删除/改动机制三类操作的门禁） */}
          <div className="bg-elevated/40 rounded-card p-3 space-y-2">
            <div className="flex items-center gap-1.5 text-3xs font-medium text-textSecondary uppercase tracking-wide">
              <ShieldAlert size={11} className="text-primary-400" /> {t('tool:world.mode.label')}
            </div>
            <div className="grid grid-cols-3 gap-1.5">
              {AI_MODES.map((m) => (
                <button
                  key={m.key}
                  onClick={() => saveMode(m.key)}
                  disabled={modeSaving || aiMode === m.key}
                  className={`px-2 py-1.5 text-xs rounded-control border transition-colors disabled:opacity-100 ${
                    aiMode === m.key
                      ? 'bg-primary-500/15 border-primary-500/50 text-primary-300'
                      : 'border-border text-textSecondary hover:text-textPrimary hover:border-primary-500/30'
                  }`}
                >{t(m.labelKey)}</button>
              ))}
            </div>
            <div className="text-3xs text-textMuted">
              {t((AI_MODES.find((m) => m.key === aiMode) || AI_MODES[1]).hintKey)}
            </div>
          </div>

          {/* 人设与行为：提示词默认收起；展开显示强注入（深灰只读）+ 可改输入（🖊 点击） */}
          <div className="bg-elevated/40 rounded-card p-3 space-y-2.5">
            <div className="flex items-center gap-1.5 text-3xs font-medium text-textSecondary uppercase tracking-wide">
              <Brain size={11} className="text-primary-400" /> {t('tool:world.creatorConfig.persona.section')}
            </div>

            {/* 可改的系统提示词（默认收起，🖊 点击展开输入框） */}
            <div>
              <div className="flex items-center gap-2">
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-textSecondary truncate">{t('tool:world.creatorConfig.persona.systemPrompt')}</div>
                  <div className="text-3xs text-textMuted mt-0.5 truncate">
                    {form.system_prompt.trim() ? form.system_prompt.trim().slice(0, 60) + '…' : t('tool:world.creatorConfig.persona.systemPromptUnset')}
                  </div>
                </div>
                <button
                  onClick={() => setEditingPrompt((v) => !v)}
                  className="p-1.5 text-textMuted hover:text-textPrimary hover:bg-border rounded-control transition-colors shrink-0"
                  title={editingPrompt ? t('tool:world.creatorConfig.collapse') : t('tool:world.creatorConfig.persona.editSystemPrompt')}
                >
                  {editingPrompt ? <ChevronUp size={15} /> : <Pencil size={15} />}
                </button>
              </div>
              {editingPrompt && (
                <textarea
                  autoFocus
                  value={form.system_prompt}
                  onChange={(e) => setForm((f) => ({ ...f, system_prompt: e.target.value }))}
                  rows={7}
                  className={`${fieldInput} resize-none font-mono text-xs leading-relaxed mt-2`}
                  placeholder={t('tool:world.creatorConfig.persona.systemPromptPlaceholder')}
                />
              )}
            </div>

            {/* 强注入段（平台强约束，只读深灰展示，不可改） */}
            <div>
              <div className="flex items-center gap-2">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1 text-xs text-textSecondary">
                    <Lock size={11} className="text-textMuted shrink-0" />
                    <span className="truncate">{t('tool:world.creatorConfig.persona.forcedTitle')}</span>
                  </div>
                  <div className="text-3xs text-textMuted mt-0.5">{t('tool:world.creatorConfig.persona.forcedDesc')}</div>
                </div>
                <button
                  onClick={() => setShowForced((v) => !v)}
                  className="p-1.5 text-textMuted hover:text-textPrimary hover:bg-border rounded-control transition-colors shrink-0"
                  title={showForced ? t('tool:world.creatorConfig.collapse') : t('tool:world.creatorConfig.persona.expand')}
                >
                  {showForced ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
                </button>
              </div>
              {showForced && (
                <pre className="mt-2 max-h-56 overflow-y-auto bg-elevated/60 border border-border rounded-control p-2.5 text-3xs leading-relaxed text-textMuted whitespace-pre-wrap font-mono select-text">
                  {creator.forced_prompt || t('tool:world.creatorConfig.persona.forcedEmpty')}
                </pre>
              )}
            </div>
          </div>

          {/* 对话生命周期（/new 自动开、空闲压缩、保留天数） */}
          {settings && (
            <div className="bg-elevated/40 rounded-card p-3 space-y-2.5">
              <div className="flex items-center gap-1.5 text-3xs font-medium text-textSecondary uppercase tracking-wide">
                <History size={11} className="text-primary-400" /> {t('tool:world.creatorConfig.lifecycle.section')}
              </div>
              <div className="flex items-center justify-between bg-elevated/60 rounded-control p-2.5">
                <div>
                  <div className="text-xs text-textSecondary">{t('tool:world.creatorConfig.lifecycle.autoNew')}</div>
                  <div className="text-3xs text-textMuted mt-0.5">{t('tool:world.creatorConfig.lifecycle.autoNewDesc')}</div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <input
                    type="time"
                    value={settings.auto_new_time}
                    onChange={(e) => setSettings((s) => (s ? { ...s, auto_new_time: e.target.value } : s))}
                    className="bg-elevated text-xs p-1 rounded-control border border-border outline-none focus:border-primary-500/50"
                  />
                  <input
                    type="checkbox"
                    checked={settings.auto_new_enabled}
                    onChange={(e) => setSettings((s) => (s ? { ...s, auto_new_enabled: e.target.checked } : s))}
                    className="w-4 h-4 accent-primary-500"
                  />
                </div>
              </div>
              <div className="flex items-center justify-between bg-elevated/60 rounded-control p-2.5">
                <div>
                  <div className="text-xs text-textSecondary">{t('tool:world.creatorConfig.lifecycle.compact')}</div>
                  <div className="text-3xs text-textMuted mt-0.5">{t('tool:world.creatorConfig.lifecycle.compactDesc')}</div>
                </div>
                <input
                  type="number"
                  min={0}
                  max={720}
                  value={settings.compact_idle_hours}
                  onChange={(e) => setSettings((s) => (s ? { ...s, compact_idle_hours: Number(e.target.value) || 0 } : s))}
                  className="w-20 bg-elevated text-sm p-1.5 rounded-control border border-border outline-none text-right focus:border-primary-500/50"
                  title={t('tool:world.creatorConfig.lifecycle.hours')}
                />
              </div>
              <div className="flex items-center justify-between bg-elevated/60 rounded-control p-2.5">
                <div>
                  <div className="text-xs text-textSecondary">{t('tool:world.creatorConfig.lifecycle.retention')}</div>
                  <div className="text-3xs text-textMuted mt-0.5">{t('tool:world.creatorConfig.lifecycle.retentionDesc')}</div>
                </div>
                <input
                  type="number"
                  min={0}
                  max={3650}
                  value={settings.retention_days}
                  onChange={(e) => setSettings((s) => (s ? { ...s, retention_days: Number(e.target.value) || 0 } : s))}
                  className="w-20 bg-elevated text-sm p-1.5 rounded-control border border-border outline-none text-right focus:border-primary-500/50"
                  title={t('tool:world.creatorConfig.lifecycle.days')}
                />
              </div>
              <button
                onClick={saveSettings}
                disabled={settingsSaving}
                className="w-full py-1.5 text-xs bg-elevated hover:bg-border text-textSecondary rounded-control transition-colors disabled:opacity-40"
              >
                {settingsSaving ? t('tool:world.creatorConfig.saving') : t('tool:world.creatorConfig.lifecycle.save')}
              </button>
            </div>
          )}

          {/* 模型 */}
          <div className="bg-elevated/40 rounded-card p-3 space-y-2.5">
            <div className="flex items-center gap-1.5 text-3xs font-medium text-textSecondary uppercase tracking-wide">
              <SlidersHorizontal size={11} className="text-primary-400" /> {t('tool:world.creatorConfig.model.section')}
            </div>
            <div className="flex gap-2">
              <div className="flex-1">
                <div className={fieldLabel}>{t('tool:world.creatorConfig.model.modelLabel')}</div>
                <input
                  value={form.model}
                  onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
                  placeholder={t('tool:world.creatorConfig.model.modelPlaceholder')}
                  className={fieldInput}
                />
              </div>
              <div className="w-24">
                <div className={fieldLabel}>{t('tool:world.creatorConfig.model.temperature')}</div>
                <input
                  type="number"
                  min={0}
                  max={2}
                  step={0.1}
                  value={form.temperature}
                  onChange={(e) => setForm((f) => ({ ...f, temperature: Number(e.target.value) }))}
                  className={`${fieldInput} text-right`}
                />
              </div>
            </div>
            <div className="flex items-center justify-between bg-elevated/60 rounded-control p-2.5">
              <div>
                <div className="text-xs text-textSecondary">{t('tool:world.creatorConfig.model.thinking')}</div>
                <div className="text-3xs text-accent-400/90 mt-0.5">{t('tool:world.creatorConfig.model.thinkingDesc')}</div>
              </div>
              <input
                type="checkbox"
                checked={form.thinking}
                onChange={(e) => setForm((f) => ({ ...f, thinking: e.target.checked }))}
                className="w-4 h-4 accent-primary-500"
              />
            </div>
            <div className="flex items-center justify-between bg-elevated/60 rounded-control p-2.5">
              <div>
                <div className="text-xs text-textSecondary">{t('tool:world.creatorConfig.model.maxToolRounds')}</div>
                <div className="text-3xs text-textMuted mt-0.5">{t('tool:world.creatorConfig.model.maxToolRoundsDesc')}</div>
              </div>
              <input
                type="number"
                min={1}
                max={200}
                value={form.max_tool_rounds}
                onChange={(e) => setForm((f) => ({ ...f, max_tool_rounds: Number(e.target.value) || 50 }))}
                className="w-20 bg-elevated text-sm p-1.5 rounded-control border border-border outline-none text-right focus:border-primary-500/50"
              />
            </div>
          </div>
        </div>

        {/* 底部操作 */}
        <div className="p-4 pt-3 shrink-0 border-t border-border">
          <button
            onClick={save}
            disabled={saving}
            className="btn btn-sm btn-primary w-full"
          >
            {saving ? t('tool:world.creatorConfig.saving') : (<span className="inline-flex items-center justify-center gap-1.5"><Save size={14} /> {t('tool:world.creatorConfig.save')}</span>)}
          </button>
        </div>
      </div>
    </Dialog>
  )
}
