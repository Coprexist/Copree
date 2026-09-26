import type { AiTypeValue, PresetData, SubOption } from './types'

// ── 预设数据 ──
// 轮次下限 6：一轮要装下「搜索 → 回复 → 进页面核实 → 有出入再补一条」四步，
// 更低会在核实与更正之间被截断（上限只是天花板，模型不多跑就不多花）。

export const PRESETS: Record<string, PresetData> = {
  chat: {
    key: 'chat',
    nameKey: 'preset.chatName',
    descKey: 'preset.chatDesc',
    temperature: 0.7,
    thinking_enabled: false,
    max_tool_rounds: 6,
    alarm_max_tool_rounds: 8,
    force_alarm_on_end: false,
    max_alarms: 3,
    delay_reply_enabled: false,
    is_ai_editable: false,
    hide_ai_identity: true,
    reminder_grace: 'every_time',
    memory_load_mode: 'index_only',
    memory_recent_count: 0,
  },
  immersive: {
    key: 'immersive',
    nameKey: 'preset.immersiveName',
    descKey: 'preset.immersiveDesc',
    temperature: 0.9,
    thinking_enabled: true,
    max_tool_rounds: 8,
    alarm_max_tool_rounds: 10,
    force_alarm_on_end: false,
    max_alarms: 5,
    delay_reply_enabled: true,
    is_ai_editable: true,
    hide_ai_identity: false,
    reminder_grace: 'every_time',
    memory_load_mode: 'index_plus_recent',
    memory_recent_count: 3,
  },
  digital_life: {
    key: 'digital_life',
    nameKey: 'preset.digital_lifeName',
    descKey: 'preset.digital_lifeDesc',
    temperature: 1.1,
    thinking_enabled: true,
    max_tool_rounds: 10,
    alarm_max_tool_rounds: 15,
    force_alarm_on_end: true,
    max_alarms: 20,
    delay_reply_enabled: true,
    is_ai_editable: true,
    hide_ai_identity: false,
    reminder_grace: 'every_time',
    memory_load_mode: 'index_plus_semantic',
    memory_recent_count: 5,
  },
}

export const SUB_OPTIONS: Record<string, SubOption[]> = {
  chat: [
    {
      id: 'chat_low_power',
      nameKey: 'preset.subLowPower',
      descKey: 'preset.subLowPowerDesc',
      icon: 'Battery',
      params: { temperature: 0.4, max_tool_rounds: 6 },
      ai_type: 'general',
    },
    {
      id: 'chat_balanced',
      nameKey: 'preset.subBalanced',
      descKey: 'preset.subBalancedDesc',
      icon: 'Scale',
      params: { temperature: 0.7, max_tool_rounds: 6 },
      ai_type: 'semi_general',
    },
    {
      id: 'chat_private',
      nameKey: 'preset.subPrivate',
      descKey: 'preset.subPrivateDesc',
      icon: 'Lock',
      params: { temperature: 0.5, max_tool_rounds: 6 },
      ai_type: 'semi_general',
    },
  ],
  immersive: [
    {
      id: 'immersive_group_admin',
      nameKey: 'preset.subGroupAdmin',
      descKey: 'preset.subGroupAdminDesc',
      icon: 'Landmark',
      params: { temperature: 0.8, max_tool_rounds: 8, thinking_enabled: false },
      ai_type: 'semi_general',
    },
    {
      id: 'immersive_roleplay',
      nameKey: 'preset.subRoleplay',
      descKey: 'preset.subRoleplayDesc',
      icon: 'Theater',
      params: { temperature: 0.9, max_tool_rounds: 8, is_ai_editable: true },
      ai_type: 'resonance',
    },
    {
      id: 'immersive_analyst',
      nameKey: 'preset.subAnalyst',
      descKey: 'preset.subAnalystDesc',
      icon: 'FlaskConical',
      params: { temperature: 0.6, max_tool_rounds: 8, thinking_enabled: true },
      ai_type: 'resonance',
    },
  ],
  digital_life: [
    {
      id: 'digital_thinker',
      nameKey: 'preset.subThinker',
      descKey: 'preset.subThinkerDesc',
      icon: 'Leaf',
      params: { temperature: 0.7, max_tool_rounds: 8 },
      ai_type: 'resonance',
    },
    {
      id: 'digital_social',
      nameKey: 'preset.subSocial',
      descKey: 'preset.subSocialDesc',
      icon: 'Flame',
      params: { temperature: 0.95, max_tool_rounds: 10 },
      ai_type: 'resonance',
    },
    {
      id: 'digital_guardian',
      nameKey: 'preset.subGuardian',
      descKey: 'preset.subGuardianDesc',
      icon: 'Shield',
      params: { temperature: 0.85, max_tool_rounds: 6, is_ai_editable: true },
      ai_type: 'resonance',
    },
  ],
}

export const CARD_ICONS: Record<string, { icon: string; color: string }> = {
  chat: { icon: 'MessageSquare', color: 'from-blue-500/20 to-blue-600/10 border-blue-500/30' },
  immersive: { icon: 'Microscope', color: 'from-primary-500/20 to-primary-600/10 border-primary-500/30' },
  digital_life: { icon: 'Globe', color: 'from-accent-500/20 to-accent-600/10 border-accent-500/30' },
}

/** AI 类型三选一：主弹窗与详细设置共用同一份，避免两处各写一遍 */
export const AI_TYPES: { value: AiTypeValue; labelKey: string; descKey: string; icon: string }[] = [
  { value: 'general', labelKey: 'modal.detailSettingsAiTypeGeneral', descKey: 'modal.detailSettingsAiTypeGeneralDesc', icon: 'User' },
  { value: 'semi_general', labelKey: 'modal.detailSettingsAiTypeSemiGeneral', descKey: 'modal.detailSettingsAiTypeSemiGeneralDesc', icon: 'RefreshCw' },
  { value: 'resonance', labelKey: 'modal.detailSettingsAiTypeResonance', descKey: 'modal.detailSettingsAiTypeResonanceDesc', icon: 'Globe' },
]
